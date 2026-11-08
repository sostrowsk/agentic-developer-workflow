"""adw CLI (typer): run / resume / approve / status — SPEC §5.

Exit codes: 0 = done, 2 = awaiting_approval (plan-approval pause),
1 = escalation or error.
"""

import contextlib
import fcntl
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, get_args

import typer
from pydantic import ValidationError

from adw import ci, github, ledger, preflight, retention, retro
from adw import dry_run as dry
from adw.agents import AgentRunError, RunTotals, SdkAgentRunner
from adw.codex import CodexRunner
from adw.config import AdwConfig, BreakpointName, ConfigError
from adw.events import EventEmitter
from adw.gui.registry import register_repo
from adw.phases import (
    AwaitingApproval,
    EscalationError,
    RunContext,
    WorktreeRefusedError,
    run_build_phase,
    run_ci_phase,
    run_codex_review_phase,
    run_final_review_phase,
    run_integration_phase,
    run_spec_and_plan,
)
from adw.state import RUNS_RELPATH, RunState, StateNotFoundError, run_lock
from adw.worktrees import ensure_runs_gitignored

app = typer.Typer(add_completion=False, help="Agentic Developer Workflow — 7-Phasen-Orchestrator")

EXIT_AWAITING_APPROVAL = 2

# Modul-Attribute statt Direkt-Import: Tests ersetzen die CLI-Aufrufe hier.
_run_glab = ci.run_glab
_run_gh = github.run_gh
# Injection seam like the two above: tests swap the dry-run mock runners here.
_dry_run_runners = dry.runners


def _fail(message: str) -> typer.Exit:
    typer.echo(f"Fehler: {message}", err=True)
    return typer.Exit(1)


# Der sprechende `--gates`-Schalter über die bestehende 4-Wege-Gate-Matrix.
# Jeder Modus bildet auf genau die beiden Booleans ab, die die Mechanik heute
# schon konsumiert (phases.py: skip_approval / spec_approval) — Bestandsschutz
# (AC7) bleibt damit automatisch gewahrt.
_GATES_VALUES = ("none", "spec", "plan", "both")
_GATES_TO_BOOLS = {
    # mode: (skip_approval, spec_approval)
    "none": (True, False),
    "spec": (True, True),
    "plan": (False, False),
    "both": (False, True),
}
# Die vollständige Altflag-Kombination löst zu genau einem Modus auf; keine
# Kombination löst zu `plan` auf — `plan` ist nur ohne Flags/über --gates plan
# erreichbar.
_ALTFLAGS_TO_GATES = {
    (True, False): "none",
    (False, True): "both",
    (True, True): "spec",
}


def _resolve_gate_mode(
    gates: str | None, no_approval: bool, spec_approval: bool
) -> tuple[str, bool, bool]:
    """Resolve the effective gate mode from `--gates` and the two legacy flags.

    Returns ``(mode, skip_approval, spec_approval)``. Raises (via ``_fail``) on an
    invalid ``--gates`` value (AC6) or a contradiction between ``--gates`` and a
    legacy flag (AC5) — order-independent, no precedence, before any run is
    created. ``--gates`` without any legacy flag is always contradiction-free;
    a redundant but consistent combination is accepted."""
    if gates is not None and gates not in _GATES_TO_BOOLS:
        raise _fail(
            f"--gates {gates!r} ist ungültig — zulässig sind genau: "
            f"{', '.join(_GATES_VALUES)}"
        )
    if gates is not None and (no_approval or spec_approval):
        altflag_mode = _ALTFLAGS_TO_GATES[(no_approval, spec_approval)]
        if altflag_mode != gates:
            raise _fail(
                f"--gates {gates} widerspricht den Altflags: --no-approval/"
                f"--spec-approval ergeben '{altflag_mode}'. Entweder nur --gates "
                f"oder die dazu passenden Altflags angeben."
            )
    if gates is not None:
        mode = gates
    elif no_approval or spec_approval:
        mode = _ALTFLAGS_TO_GATES[(no_approval, spec_approval)]
    else:
        mode = "plan"
    skip, spec = _GATES_TO_BOOLS[mode]
    return mode, skip, spec


def _preflight(
    repo: Path, config: AdwConfig, dry_run: bool, protect_artifacts: bool = False
) -> None:
    """Forge → working tree → models (adw/preflight.py); a refusal is exit 1."""
    try:
        preflight.run_all(repo, config, dry_run, protect_artifacts=protect_artifacts)
    except preflight.PreflightRefused as exc:
        raise _fail(str(exc)) from exc


def _adw_version() -> str:
    try:
        return version("adw")
    except PackageNotFoundError:
        return "unknown"


def _run_start_payload(state: RunState, config: AdwConfig, repo: Path) -> dict:
    return {
        "issue": state.issue,
        "parallel": state.parallel,
        "dry_run": state.dry_run,
        "repo": str(repo),
        "base_branch": config.base_branch,
        "adw_version": _adw_version(),
        "lanes": list(config.lanes),
    }


# typer.Exit codes → run-span status (E1 status_mapping): 0=done, 2=awaiting,
# everything else (1: EscalationError/AgentRunError, or an unexpected raise) = escalated.
_EXIT_STATUS = {0: "done", 1: "escalated", EXIT_AWAITING_APPROVAL: "awaiting_approval"}


@contextlib.contextmanager
def _run_span(
    emitter: EventEmitter,
    state: RunState,
    config: AdwConfig,
    repo: Path,
    run_totals: RunTotals,
):
    """The `run` span around the whole CLI lifecycle (E1), at EVERY exit. The end
    event is written before any exception propagates (span() writes end in its
    finally); the exception itself propagates unchanged. ``run_totals`` is the
    live accumulator the agent runner feeds; a dry run leaves it 0/0."""
    start = time.monotonic()

    def totals() -> dict:
        return {
            "duration": time.monotonic() - start,
            "cost": run_totals.cost_usd,
            "tokens": run_totals.tokens,
        }

    with emitter.span("run", _run_start_payload(state, config, repo)) as handle:
        handle.end_payload = {"status": "escalated", "totals": totals()}
        try:
            yield handle
        except typer.Exit as exc:
            handle.end_payload = {
                "status": _EXIT_STATUS.get(exc.exit_code, "escalated"),
                "totals": totals(),
            }
            raise
        except BaseException:
            handle.end_payload = {"status": "escalated", "totals": totals()}
            raise
        else:
            handle.end_payload = {"status": "done", "totals": totals()}


def _export_after_execution(repo: Path, run_id: str) -> None:
    """Ledger export after an execution (E6). Fail-open: never changes exit code or
    phase, never replaces an exception/typer.Exit that is already propagating.

    Re-takes the per-run lock (after the execution released it; same order repo
    lock → run lock): a concurrent manual prune must not export and delete the run
    while this export copies it."""
    try:
        with run_lock(repo, run_id):
            ledger.export_run(repo, run_id)
    except Exception as exc:  # noqa: BLE001 — export_run never raises; second guard
        typer.echo(f"Ledger-Export fehlgeschlagen: {exc}", err=True)


@app.command()
def run(
    repo: Annotated[Path, typer.Option("--repo", help="Pfad zum Ziel-Repo")] = Path("."),
    issue: Annotated[str | None, typer.Option("--issue", help="Issue-Text direkt")] = None,
    gitlab_issue: Annotated[
        int | None, typer.Option("--gitlab-issue", help="GitLab-Issue-ID (via glab)")
    ] = None,
    github_issue: Annotated[
        int | None, typer.Option("--github-issue", help="GitHub-Issue-Nummer (via gh)")
    ] = None,
    parallel: Annotated[bool, typer.Option("--parallel", help="FE-/BE-Lanes parallel")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Mocks statt Agents, 0 Tokens")
    ] = False,
    no_approval: Annotated[
        bool,
        typer.Option(
            "--no-approval",
            help="Veraltet — bitte --gates none verwenden (kein Gate, läuft autonom bis done)",
        ),
    ] = False,
    spec_approval: Annotated[
        bool,
        typer.Option(
            "--spec-approval",
            help="Veraltet — bitte --gates both verwenden (zusätzlicher Stopp NACH der Spec)",
        ),
    ] = False,
    gates: Annotated[
        str | None,
        typer.Option(
            "--gates",
            help=(
                "Freigabe-Gates none|spec|plan|both (Default: plan). "
                "none = kein Halt (nur eine Eskalation stoppt); "
                "spec = Halt am Spec-Gate nach der Spec, vor dem Plan; "
                "plan = Halt am Plan-Gate vor dem Build (heutiges Verhalten ohne Flags); "
                "both = Halt an beiden Gates. Die Altflags bleiben äquivalent: "
                "--no-approval == none, --spec-approval == both, "
                "--no-approval --spec-approval == spec."
            ),
        ),
    ] = None,
    base_branch: Annotated[
        str | None,
        typer.Option("--base-branch", help="Base-Branch-Override gegenüber .adw/config.yaml"),
    ] = None,
) -> None:
    """Start a new run through all seven phases."""
    sources = [s for s in (issue, gitlab_issue, github_issue) if s is not None]
    if len(sources) != 1:
        raise _fail("genau EINE Issue-Quelle angeben: --issue, --gitlab-issue ODER --github-issue")
    # --gates VOR jeder State-/Netz-Mutation auflösen: ein ungültiger Wert (AC6)
    # oder ein Widerspruch zu den Altflags (AC5) wird hier abgelehnt, bevor ein
    # Run angelegt oder ein Forge-Aufruf gemacht wird.
    legacy_flags_used = no_approval or spec_approval  # before resolution overwrites them
    gate_mode, skip_approval, spec_approval = _resolve_gate_mode(gates, no_approval, spec_approval)
    if legacy_flags_used:
        typer.echo(
            f"Hinweis: --no-approval/--spec-approval sind veraltet — bitte "
            f"--gates {gate_mode} verwenden (gleiches Verhalten).",
            err=True,
        )
    repo = repo.resolve()
    # Register the resolved target repo for the later GUI (GUI-SPEC §7.4).
    # Fail-open: the registry only feeds a display, so a registry error (e.g. a
    # write failure) must never block the actual run.
    try:
        register_repo(repo)
    except Exception:  # noqa: BLE001 — fail-open by design
        pass
    # Config ZUERST validieren (fail fast) — kein Forge-Netzaufruf für ein
    # Repo, das ohnehin keine gültige .adw/config.yaml hat.
    config = _load_config(repo, base_branch)
    # Repo-Lock VOR dem Vorflug (C3): kein zweites ADW-Kommando heilt oder
    # schreibt parallel im Haupt-Checkout.
    with _repo_lock(repo):
        # Vorflug VOR jeder State-Mutation: eine Verweigerung legt keinen Run an und
        # macht keinen Netzaufruf (B1/B4). Nie Eskalation.
        _preflight(repo, config, dry_run)
        if issue is not None:
            issue_text = issue
        elif gitlab_issue is not None:
            issue_text = _fetch_gitlab_issue(repo, gitlab_issue)
        else:
            issue_text = _fetch_github_issue(repo, github_issue)
        state = RunState.new(issue=issue_text, parallel=parallel)
        state.dry_run = dry_run
        # BEIDE wirksamen Gate-Entscheide aus dem Gate-Modus beim Run-Start pinnen:
        # der Resume-/Approve-Aufruf kennt weder --gates noch die Altflags mehr.
        # skip_approval MUSS hier (vor dem ersten state.save) gesetzt werden — sonst
        # ließe ein Crash vor dem lazy-Pin in run_spec_and_plan einen resumierbaren
        # State mit skip_approval=false zurück, der den Modus still verschöbe
        # (none→plan, spec→both) und ein unbeabsichtigtes Plan-Gate einführte.
        state.skip_approval = skip_approval
        state.spec_approval = spec_approval
        # Effektiven Base-Branch pinnen (Config ODER Override): Fortsetzungen
        # bauen exakt gegen diese Basis, auch wenn die config.yaml sich ändert.
        state.pinned_base_branch = config.base_branch
        # Ebenso die Haltepunkte pinnen: der Lauf hält an genau der Menge, die beim
        # Start konfiguriert war — eine spätere Config-Änderung verschiebt sie nicht.
        state.pinned_breakpoints = list(config.breakpoints)
        # Effort ebenso (W2) — auch das leere Mapping, damit später ergänzte
        # Einträge einen laufenden Run nicht verändern.
        state.pinned_effort = dict(config.effort)
        state.pinned_codex_reasoning_effort = config.codex.reasoning_effort
        # SOFORT persistieren: crasht der allererste Agent-Lauf, muss die
        # angezeigte run_id per `adw resume` auffindbar sein.
        ensure_runs_gitignored(repo)
        # E1: der eine Emitter dieses Runs wird NACH RunState.new und VOR dem ersten
        # state.save erzeugt — das erste state.saved-Event liegt damit im run-Span.
        # trace.enabled=false schaltet das Ereignis-Log ab (D2/E6), ohne Lauf/State zu
        # berühren.
        emitter = EventEmitter(repo, state.run_id, enabled=config.trace.enabled)
        run_totals = RunTotals()
        # Denselben per-run Execution-Lock wie resume/approve halten, sobald der Run
        # persistiert (und damit per `adw resume <run_id>` erreichbar) wird — über die
        # GESAMTE Ausführung (P1): ein gleichzeitiger resume/approve darf den frisch
        # angelegten Run nicht parallel weiterführen (doppelte CI-Vorbereitung/Push,
        # sich überschreibende State-Snapshots). Die run_id ist frisch/zufällig, also
        # gibt es beim Erwerb keine Konkurrenz; gehalten schließt sie jeden zweiten
        # Executor aus, bis der Run fertig ist. state.json entsteht erst im Lock.
        executed = False
        try:
            with _run_execution_lock(repo, state.run_id):
                with _run_span(emitter, state, config, repo, run_totals) as run_handle:
                    state.save(repo, emitter=emitter, span_id=run_handle.id)
                    ctx = _build_context(
                        repo, config, state, emitter=emitter, run_totals=run_totals
                    )
                    typer.echo(
                        f"Run {state.run_id} gestartet (Phase: {state.phase}, Gates: {gate_mode})"
                    )
                    executed = True
                    try:
                        _execute(ctx)
                    finally:
                        # W14: retro of a finished run inside the run span; never
                        # raises, so a pending typer.Exit propagates unchanged.
                        retro.run_retro(ctx)
        finally:
            # E6: export after every execution (done, escalation, pause, error) —
            # after the run span and the per-run lock, before auto-prune.
            if executed:
                _export_after_execution(repo, state.run_id)
        # Nach erfolgreichem Abschluss (done): automatisches Pruning (D3). Eine
        # Eskalation/Approval-Pause hätte oben eine typer.Exit geworfen und käme hier
        # nicht an. Pruning läuft NACH dem Freigeben des Execution-Locks.
        _maybe_auto_prune(repo, config, state)


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="ID des fortzusetzenden Runs")],
    repo: Annotated[Path, typer.Option("--repo")] = Path("."),
    base_branch: Annotated[str | None, typer.Option("--base-branch")] = None,
) -> None:
    """Continue a run in the same phase after a crash or approval pause."""
    repo = repo.resolve()
    with _repo_lock(repo):  # vor Existenzprüfung, Vorflug und per-Run-Lock (C3)
        # Existence check OUTSIDE the per-run lock — no lock file for a missing run.
        _load_state(repo, run_id)
        # Same per-run execution lock as `approve` (P1): a `resume` racing an `approve`
        # (or another `resume`) on the same run must not double-execute the downstream
        # phases. Both commands take THIS lock, so continuations of one run serialize.
        executed = False
        try:
            with _run_execution_lock(repo, run_id):
                state = _load_state(repo, run_id)  # reload under the lock
                if state.phase == "escalated":
                    raise _fail(
                        f"Run {run_id} ist eskaliert — Report unter "
                        f".adw/runs/{run_id}/escalation.md lesen und einen neuen Run starten"
                    )
                if state.phase == "done":
                    # Vor Vorflug, Emitter und Auto-Prune: ein fertiger Lauf bekommt
                    # keine neue Run-Spanne — sein (ggf. komprimiertes) Log bleibt.
                    typer.echo(f"Run {run_id} ist bereits abgeschlossen")
                    return
                config, rebased = _config_for_continuation(repo, state, base_branch)
                # Vorflug VOR jeder State-Mutation (auch vor dem rebased-Save): eine
                # Verweigerung lässt den Run-State unverändert und resumierbar (B1).
                _preflight(
                    repo, config, state.dry_run,
                    protect_artifacts=state.authoring_session is not None,
                )
                emitter = EventEmitter(repo, state.run_id, enabled=config.trace.enabled)
                run_totals = RunTotals()
                with _run_span(emitter, state, config, repo, run_totals) as run_handle:
                    if rebased:
                        # First persistence of this command — inside the run span (E1), so
                        # its state.saved event is emitted within the span.
                        state.save(repo, emitter=emitter, span_id=run_handle.id)
                    ctx = _build_context(
                        repo, config, state, emitter=emitter, run_totals=run_totals
                    )
                    typer.echo(f"Run {state.run_id} wird fortgesetzt (Phase: {state.phase})")
                    executed = True
                    try:
                        _execute(ctx)
                    finally:
                        # W14: retro of a finished run inside the run span; never
                        # raises, so a pending typer.Exit propagates unchanged.
                        retro.run_retro(ctx)
        finally:
            # E6: export after every execution (done, escalation, pause, error) —
            # after the run span and the per-run lock, before auto-prune.
            if executed:
                _export_after_execution(repo, run_id)
        # Reaching done through `resume` is a normal successful completion → same
        # automatic pruning as `run` (D3). An escalation/pause raised above and skips it.
        _maybe_auto_prune(repo, config, state)


@app.command()
def approve(
    run_id: Annotated[str, typer.Argument(help="ID des wartenden Runs")],
    repo: Annotated[Path, typer.Option("--repo")] = Path("."),
    base_branch: Annotated[str | None, typer.Option("--base-branch")] = None,
) -> None:
    """Grant a pending approval (spec or plan) and continue the run."""
    repo = repo.resolve()
    with _repo_lock(repo):  # vor Existenzprüfung, Vorflug und per-Run-Lock (C3)
        # Existence check OUTSIDE the per-run lock — no lock file for a missing run.
        _load_state(repo, run_id)
        # The whole check/release/execute runs under the per-run execution lock (P1):
        # two concurrent approvals must not both release the same pending gate and each
        # run the downstream phases (a double push at `before_push`).
        executed = False
        try:
            with _run_execution_lock(repo, run_id):
                # Reload UNDER the lock and revalidate: a concurrent approve/resume that
                # won the lock first may already have released the gate and advanced the
                # phase — this loser then rejects cleanly, without a second execution.
                state = _load_state(repo, run_id)
                if state.phase not in ("awaiting_approval", "awaiting_spec_approval"):
                    raise _fail(
                        f"Run {run_id} wartet nicht auf Approval (Phase: {state.phase}) — "
                        f"für Crash-Fortsetzung `adw resume` benutzen"
                    )
                # A re-pinned base branch is only mutated in memory here; approve's own save
                # below (inside the run span) persists it together with the approval grant.
                config, _rebased = _config_for_continuation(repo, state, base_branch)
                # Vorflug VOR dem Setzen/Speichern der Zusage: eine Verweigerung lässt die
                # Approval-Zusage ungesetzt und den Run unverändert am Gate (B1).
                _preflight(
                    repo, config, state.dry_run,
                    protect_artifacts=state.authoring_session is not None,
                )
                if state.pending_breakpoint is not None:
                    gate = state.pending_breakpoint
                else:
                    gate = "spec" if state.phase == "awaiting_spec_approval" else "plan"
                emitter = EventEmitter(repo, state.run_id, enabled=config.trace.enabled)
                run_totals = RunTotals()
                with _run_span(emitter, state, config, repo, run_totals) as run_handle:
                    breakpoint_release = state.pending_breakpoint is not None
                    if breakpoint_release:
                        # Breakpoint freigeben: die Phase über die Grenze fortschalten UND
                        # pending_breakpoint im SELBEN atomaren Save leeren — der freigegebene
                        # Haltepunkt wird danach nie wieder erreicht (AC7/AC8). Das granted-
                        # Event NICHT hier emittieren: es entsteht log-geprüft in _execute
                        # (_catch_up_granted_breakpoints) allein aus dem awaited-Event und
                        # pending_breakpoint — so überlebt die Freigabe das Crash-Fenster
                        # zwischen diesem Save und dem Event-Append (AC12), OHNE ein
                        # zusätzliches State-Feld (Contract: pending_breakpoint ist die
                        # einzige State-Ergänzung; Plan B2). Der Plan-/Spec-Approval-Pfad
                        # bleibt unberührt (Regressions-Pin AC2).
                        state.phase = _breakpoint_resume_phase(state)
                        state.pending_breakpoint = None
                    elif state.phase == "awaiting_spec_approval":
                        # Spec approven: in die Plan-Phase übergehen. Der Lauf pausiert danach
                        # regulär beim Plan-Approval, sofern der Run nicht mit --no-approval lief.
                        state.spec_approval_granted = True
                        state.phase = "plan"
                    else:
                        state.approval_granted = True
                    state.save(repo, emitter=emitter, span_id=run_handle.id)
                    if not breakpoint_release:
                        # spec/plan-Gate: granted wie bisher direkt (der Übergang schließt
                        # innerhalb von approve ab, kein Crash-Fenster). granted: ein
                        # tatsächlich eingetretener Produktzustand (AC 9).
                        emitter.emit(
                            "approval", {"gate": gate, "event": "granted"}, span=run_handle.id
                        )
                    ctx = _build_context(
                        repo, config, state, emitter=emitter, run_totals=run_totals
                    )
                    typer.echo(f"Approval erteilt — Run {state.run_id} läuft weiter")
                    executed = True
                    try:
                        _execute(ctx)
                    finally:
                        # W14: retro of a finished run inside the run span; never
                        # raises, so a pending typer.Exit propagates unchanged.
                        retro.run_retro(ctx)
        finally:
            # E6: export after every execution (done, escalation, pause, error) —
            # after the run span and the per-run lock, before auto-prune.
            if executed:
                _export_after_execution(repo, run_id)
        # Reaching done through `approve` is a normal successful completion → same
        # automatic pruning as `run` (D3). A further pause/escalation raised above.
        # Pruning runs AFTER releasing the execution lock (it only touches other runs).
        _maybe_auto_prune(repo, config, state)


@app.command()
def status(
    run_id: Annotated[str | None, typer.Argument(help="Nur diesen Run anzeigen")] = None,
    repo: Annotated[Path, typer.Option("--repo")] = Path("."),
) -> None:
    """Show runs and their phase."""
    repo = repo.resolve()
    runs_dir = repo / RUNS_RELPATH
    states: list[RunState] = []
    if run_id is not None:
        states.append(_load_state(repo, run_id))
    elif runs_dir.is_dir():
        for path in sorted(runs_dir.glob("*/state.json")):
            try:
                states.append(RunState.load(repo, path.parent.name))
            except StateNotFoundError:
                continue
    if not states:
        typer.echo(f"Keine Runs unter {runs_dir}")
        return
    for state in sorted(states, key=lambda s: s.seq):
        issue_head = state.issue.splitlines()[0][:60] if state.issue else ""
        mode = "parallel" if state.parallel else "single"
        extra = " [dry-run]" if state.dry_run else ""
        typer.echo(f"{state.run_id}  {state.phase:<22} {mode:<8}{extra}  {issue_head}")


def _is_loopback(host: str) -> bool:
    """A loopback bind target: 127.0.0.0/8, ::1 or localhost."""
    h = host.strip().lower()
    if h in ("localhost", "::1", "0:0:0:0:0:0:0:1"):
        return True
    import ipaddress

    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@app.command()
def gui(
    repo: Annotated[
        list[Path] | None,
        typer.Option("--repo", help="Repo(s) zusätzlich zur Registry verfügbar machen"),
    ] = None,
    host: Annotated[str, typer.Option("--host", help="Bind-Adresse (nur Loopback)")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind-Port")] = 8765,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Browser auf der lokalen Adresse öffnen")
    ] = False,
    i_know: Annotated[
        bool, typer.Option("--i-know", help="Nicht-Loopback-Host explizit zulassen")
    ] = False,
) -> None:
    """Start the read-only web GUI (loopback only unless --i-know)."""
    # §8: the event log carries raw, unredacted agent output — a non-loopback bind
    # must be an explicit, informed choice. Checked BEFORE any bind/server start.
    if not _is_loopback(host) and not i_know:
        raise _fail(
            f"--host {host} ist keine Loopback-Adresse. Das Event-Log enthält rohe, "
            f"unredigierte Agent-Ausgaben — mit --i-know explizit zulassen."
        )
    # Lazy import: the web stack (adw.gui.app → FastAPI/Jinja2) is the optional
    # `gui` extra; the core `adw run` path must never import it.
    try:
        import uvicorn

        from adw.gui.app import create_app
    except ImportError:
        typer.echo(
            "adw gui benötigt das optionale Extra 'gui' (FastAPI, uvicorn, Jinja2). "
            "Installieren mit: pip install 'adw[gui]'  (oder: uv pip install 'adw[gui]').",
            err=True,
        )
        raise typer.Exit(1) from None

    # R3/E4: the GUI answers loopback hostnames only; an explicit --i-know host is
    # allowed in addition.
    extra_host = host if i_know else None  # create_app skips an already allowed host
    application = create_app(repos=[str(p) for p in (repo or [])], extra_host=extra_host)
    if open_browser:
        import webbrowser

        webbrowser.open(f"http://{host}:{port}/")
    uvicorn.run(application, host=host, port=port)


# --- `adw runs`: Retention (list / prune) -------------------------------------

runs_app = typer.Typer(add_completion=False, help="Run-Verzeichnisse verwalten (list, prune)")
app.add_typer(runs_app, name="runs")
ledger_app = typer.Typer(add_completion=False, help="Zentrales Lauf-Ledger (export)")
app.add_typer(ledger_app, name="ledger")


def _require_repo(repo: Path) -> Path:
    """A usable target repo: an existing directory that is a git worktree. Pruning
    removes worktrees and snapshot refs and listing dates from the log — a non-git
    directory cannot be operated on, so it is a clear error (exit 1), never a silent
    success (B3)."""
    repo = repo.resolve()
    if not repo.is_dir():
        raise _fail(f"--repo {repo} existiert nicht oder ist kein verwendbares Verzeichnis")
    try:
        result = preflight.git_plain(repo, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError) as exc:
        raise _fail(f"--repo {repo}: git nicht ausführbar — {exc}") from exc
    # Require --repo to be the ROOT of a git work tree: a non-git directory fails,
    # and a mere SUBDIRECTORY of a repo (whose toplevel differs) is rejected too, so
    # pruning never touches an enclosing repo's worktrees/refs by accident.
    toplevel = Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    if toplevel != repo:
        raise _fail(f"--repo {repo} ist kein verwendbares Git-Repository (Wurzel)")
    return repo


@runs_app.command("list")
def runs_list(
    repo: Annotated[Path, typer.Option("--repo", help="Pfad zum Ziel-Repo")] = Path("."),
) -> None:
    """Show every run with the facts that reveal when pruning is due (B1–B4)."""
    repo = _require_repo(repo)
    records = retention.scan_runs(repo)
    if not records:
        typer.echo(f"Keine Runs unter {repo / RUNS_RELPATH}")
        return

    def show(value) -> str:
        return "?" if value is None else str(value)

    # Newest first — the runs closest to being pruned are the least urgent to see.
    for rec in sorted(records, key=lambda r: (r.date, r.run_id), reverse=True):
        date = rec.date.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(
            f"{rec.run_id}  {show(rec.phase):<22}  {date}  "
            f"events={show(rec.event_count)}  log={show(rec.log_size)}"
        )


@runs_app.command("prune")
def runs_prune(
    repo: Annotated[Path, typer.Option("--repo", help="Pfad zum Ziel-Repo")] = Path("."),
    keep: Annotated[int, typer.Option("--keep", help="Neueste N Läufe schützen (N ≥ 1)")] = 20,
    older_than: Annotated[
        int | None, typer.Option("--older-than", help="Nur Läufe ab DAYS Tagen bearbeiten")
    ] = None,
    gzip: Annotated[
        bool,
        typer.Option("--gzip", help="events.jsonl komprimieren statt löschen (behält den Lauf)"),
    ] = False,
) -> None:
    """Delete old runs (dir + refs + worktrees) or, with --gzip, compress their
    event logs while keeping the run whole (§4.5, C1–C8)."""
    # Invalid N/DAYS: the usual CLI error (exit 2), BEFORE any run data changes.
    if keep < 1:
        # `trace.keep_runs: 0` means "never prune" — a manual --keep 0 that wiped
        # every run would invert it. The newest run always stays.
        typer.echo("Fehler: --keep muss mindestens 1 sein (der neueste Lauf bleibt).", err=True)
        raise typer.Exit(2)
    if older_than is not None and older_than < 0:
        raise typer.Exit(2)
    repo = _require_repo(repo)

    def echo_result(result: retention.PruneResult) -> None:
        line = f"{result.run_id}: {result.action}"
        if result.reason:
            line += f" ({result.reason})"
        typer.echo(line)

    try:
        results = retention.prune(repo, keep=keep, older_than=older_than, gzip_mode=gzip)
    except retention.RetentionError as exc:
        # Report the achieved state before failing (C7): the candidates fully
        # processed before the error, and the partial state of the failing run —
        # earlier mutations remain applied and are named, then exit 1.
        for result in exc.results:
            echo_result(result)
        if exc.failed is not None:
            echo_result(exc.failed)
        raise _fail(f"Pruning nicht sicher abgeschlossen: {exc}") from exc
    if not results:
        typer.echo("Nichts zu prunen.")
        return
    for result in results:
        echo_result(result)
    # A per-run partial failure (e.g. a worktree that turned dirty mid-removal)
    # does not abort the sweep — the remaining safe candidates are processed
    # first (C4) — but it must not end in the success exit code either.
    failed = [r for r in results if r.action == "failed"]
    if failed:
        raise _fail(
            "Pruning nicht sicher abgeschlossen fuer: "
            + ", ".join(r.run_id for r in failed)
        )


@ledger_app.command("export")
def ledger_export(
    run_id: Annotated[str | None, typer.Argument(help="Nur diesen Run exportieren")] = None,
    repo: Annotated[Path, typer.Option("--repo", help="Pfad zum Ziel-Repo")] = Path("."),
) -> None:
    """Export runs into the ledger after the fact (all runs in start order)."""
    repo = _require_repo(repo)
    # Unter dem Repo-Lock wie ein Lauf: kein gleichzeitiger run/resume/approve
    # exportiert oder verändert denselben Lauf parallel.
    with _repo_lock(repo):
        if run_id is not None:
            _load_state(repo, run_id)
            run_ids = [run_id]
        else:
            run_ids = [rec.run_id for rec in retention.scan_runs(repo)]
        outcomes = []
        for rid in run_ids:
            # Per-run lock as in prune (which holds no repo lock): a run cannot be
            # pruned while its bundle is being copied.
            with run_lock(repo, rid):
                outcome = ledger.export_run(repo, rid)
            outcomes.append(outcome)
            typer.echo(f"{rid}: {outcome}")
    if "failed" in outcomes:
        raise typer.Exit(1)


# --- `adw rate` / `adw feedback`: menschliches Urteil im Ledger (W14) ---------


class RateValue(StrEnum):
    good = "good"
    bad = "bad"


class FindingValue(StrEnum):
    valid = "valid"
    noise = "noise"


def _append_feedback(repo: Path, run_id: str, entry: dict, finding: str | None = None) -> None:
    """Resolve the run's ledger folder, pick the full finding key for ``finding``
    and append the line — under the repo lock; any refusal is exit 1, nothing
    written."""
    repo = _require_repo(repo)
    with _repo_lock(repo):
        try:
            ledger_dir, rel = ledger.ledger_run_dir(repo, run_id)
            if finding is not None:
                all_keys = ledger.finding_keys(ledger_dir, rel)
                keys = [k for k in all_keys if k.startswith(finding)]
                if len(keys) != 1:
                    # No hit: every key of the run is a candidate for a usable prefix.
                    candidates = keys or all_keys
                    shown = ", ".join(candidates[:5]) + (", …" if len(candidates) > 5 else "")
                    problem = "trifft keinen" if not keys else "ist mehrdeutig"
                    raise ledger.FeedbackError(
                        f"--finding {finding!r} {problem} finding_key"
                        + (f" (Kandidaten: {shown})" if candidates else " (Lauf ohne Findings)")
                    )
                entry["finding_key"] = keys[0]
            ledger.append_feedback(ledger_dir, rel, entry)
        except ledger.FeedbackError as exc:
            raise _fail(str(exc)) from None
    typer.echo(f"Feedback für {run_id} gespeichert ({entry['kind']} {entry['value']})")


def _feedback_entry(kind: str, value: str, note: str | None) -> dict:
    return {"ts": datetime.now(UTC).isoformat(), "kind": kind, "value": value, "note": note}


@app.command("rate")
def rate(
    run_id: Annotated[str, typer.Argument(help="Run-ID des exportierten Laufs")],
    value: Annotated[RateValue, typer.Argument(help="good | bad")],
    note: Annotated[str | None, typer.Option("--note", help="Kurze Begründung")] = None,
    repo: Annotated[Path, typer.Option("--repo", help="Pfad zum Ziel-Repo")] = Path("."),
) -> None:
    """Einen Lauf bewerten (Zeile in feedback.jsonl im Ledger)."""
    _append_feedback(repo, run_id, _feedback_entry("rate", value.value, note))


@app.command("feedback")
def feedback(
    run_id: Annotated[str, typer.Argument(help="Run-ID des exportierten Laufs")],
    value: Annotated[FindingValue, typer.Argument(help="valid | noise")],
    finding: Annotated[
        str, typer.Option("--finding", help="Präfix, der genau einen finding_key trifft")
    ],
    note: Annotated[str | None, typer.Option("--note", help="Kurze Begründung")] = None,
    repo: Annotated[Path, typer.Option("--repo", help="Pfad zum Ziel-Repo")] = Path("."),
) -> None:
    """Ein Finding eines Laufs als valid oder noise beurteilen (Zeile in feedback.jsonl)."""
    _append_feedback(repo, run_id, _feedback_entry("finding", value.value, note), finding)


# --- `adw kpis`: Kennzahlen über alle Läufe im Ledger (W3) --------------------

_KPI_COLUMNS = (
    "Version", "Läufe", "done/esc/sonst", "Eskal.", "Runden med/max", "Fix med/max",
    "Gate med/max", "Dauer med", "Kosten sum/med", "CB", "Appr. med",
)


def _kpi_num(value) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def _kpi_pct(value) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _kpi_row(label: str, m: dict) -> list[str]:
    shares = m["phase_shares"]
    return [
        label,
        str(m["runs"]),
        "/".join(_kpi_pct(shares[k]) for k in ("done", "escalated", "other")),
        _kpi_pct(m["escalation_share"]),
        *(
            f"{_kpi_num(m[k]['median'])}/{_kpi_num(m[k]['max'])}"
            for k in ("review_rounds", "fix_cycles", "gate_iterations")
        ),
        _kpi_num(m["duration_s"]["median"]),
        f"{_kpi_num(m['cost_usd']['sum'])}/{_kpi_num(m['cost_usd']['median'])}",
        _kpi_num(m["circuit_breakers"]),
        _kpi_num(m["approvals"]["median"]),
    ]


def _kpi_pairs(values: dict, fmt) -> str:
    return " ".join(f"{k}={fmt(values[k])}" for k in sorted(values)) or "-"


def _format_kpis(result: dict) -> str:
    """Plain text from the compute_kpis result — formatting only, nothing recomputed."""
    labelled = [(_kpi_num(g["adw_version"]), g) for g in result["groups"]]
    labelled.append(("gesamt", result["total"]))
    rows = [list(_KPI_COLUMNS)] + [_kpi_row(label, m) for label, m in labelled]
    widths = [max(len(row[i]) for row in rows) for i in range(len(_KPI_COLUMNS))]
    lines = [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]
    total = result["total"]
    lines.append(
        f"Retros {total['retro_runs']}: {_kpi_pairs(total['retro_causes'], str)}"
        f" | Bewertungen {_kpi_pairs(total['ratings'], str)}"
        f" | Findings {_kpi_pairs(total['finding_feedback'], str)}"
    )
    lines.append("")
    for label, m in labelled:
        lines.append(
            f"{label}: Kosten-Anteile {_kpi_pairs(m['agent_cost_shares'], _kpi_pct)}"
            f" | Severity {_kpi_pairs(m['findings_by_severity'], str)}"
            f" | Action {_kpi_pairs(m['findings_by_action'], str)}"
        )
    lines.append("")
    lines.append(f"Kandidaten ({len(result['candidates'])}):")
    for c in result["candidates"]:
        lines.append(
            f"  {c['run_id']}  {_kpi_num(c['repo_slug'])}  {_kpi_num(c['adw_version'])}"
            f"  {','.join(c['rules'])}  {_kpi_num(c['issue_title'])}"
        )
    lines.append(f"{result['skipped']} übersprungen")
    return "\n".join(lines)


@app.command("kpis")
def kpis(
    since: Annotated[
        int | None, typer.Option("--since", help="Nur Läufe der letzten N Tage (started_at)")
    ] = None,
    repo_slug: Annotated[
        str | None, typer.Option("--repo-slug", help="Nur Läufe dieses Repos (z. B. acme/widget)")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Ergebnis als ein JSON-Objekt")] = False,
) -> None:
    """Kennzahlen je ADW-Version und auffällige Läufe aus dem Ledger (nur lesend)."""
    if since is not None and since < 0:
        typer.echo("Fehler: --since muss mindestens 0 sein.", err=True)
        raise typer.Exit(2)
    path = Path(os.environ.get("ADW_LEDGER", ledger._DEFAULT_LEDGER)).expanduser()
    try:
        ledger_path = ledger._ledger_path()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise _fail(f"Ledger {path} nicht lesbar: {exc}") from None
    if ledger_path is None:
        raise _fail(f"kein Ledger unter {path} (Git-Klon via ADW_LEDGER erwartet)")
    records, signals = ledger._read_ledger_runs(ledger_path)
    result = ledger.compute_kpis(records, since=since, repo_slug=repo_slug, signals=signals)
    typer.echo(json.dumps(result, ensure_ascii=False) if as_json else _format_kpis(result))


def _maybe_auto_prune(repo: Path, config: AdwConfig, state: RunState) -> None:
    """Automatic deleting-prune after a successful run (D3). Same rules as manual
    deleting pruning; disabled when keep_runs == 0. Fail-open: an error never
    changes the just-finished run's done phase or success exit code — it is only
    reported."""
    if state.dry_run or state.phase != "done" or config.trace.keep_runs <= 0:
        return  # ein Dry-Run verändert keine echten Läufe (C11)
    try:
        # Der eigene Lauf ist nie Kandidat — auch wenn er nach Startzeit nicht
        # zu den keep_runs neuesten gehört (lange am Plan-Gate gewartet).
        results = retention.prune(
            repo, keep=config.trace.keep_runs, older_than=None, exclude={state.run_id}
        )
    except Exception as exc:  # noqa: BLE001 — fail-open by design (D3)
        typer.echo(f"Auto-Pruning fehlgeschlagen (Lauf bleibt done): {exc}", err=True)
        return
    removed = [r.run_id for r in results if r.action == "deleted"]
    if removed:
        joined = ", ".join(removed)
        typer.echo(f"Auto-Pruning entfernte {len(removed)} alte(n) Lauf/Läufe: {joined}")
    # A partial failure is RETURNED, not raised (a later worktree turned dirty
    # mid-removal), so it would pass silently through the exception path above.
    # Fail-open means the finished run keeps its done phase and success exit code —
    # NOT that the damage goes unmentioned: report the achieved state visibly.
    for result in results:
        if result.action == "failed":
            typer.echo(
                f"Auto-Pruning unvollständig für {result.run_id}: {result.reason} "
                "(Lauf bleibt done)",
                err=True,
            )


# --- interne Verdrahtung ------------------------------------------------------


def _breakpoint_resume_phase(state: RunState) -> str:
    """The phase a run continues in once its waiting breakpoint is granted (B7):
    ``before_integration`` -> ``integration`` (parallel) / ``codex_review``
    (single-lane); ``before_push`` -> ``ci``."""
    if state.pending_breakpoint == "before_integration":
        return "integration" if state.parallel else "codex_review"
    return "ci"  # before_push


_BREAKPOINT_GATES: tuple[str, ...] = get_args(BreakpointName)


def _logged_approval_events(repo: Path, run_id: str) -> set[tuple[str, str]]:
    """The ``(gate, event)`` pairs already present in the run's ``approval`` event
    log — the log-checked dedup basis (B8). Empty when there is no (readable) log
    (e.g. trace disabled), which keeps every catch-up a harmless no-op."""
    path = repo / RUNS_RELPATH / run_id / "events.jsonl"
    pairs: set[tuple[str, str]] = set()
    if not path.is_file():
        return pairs
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return pairs
    for line in content.splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "approval":
            continue
        payload = record.get("payload") or {}
        gate, event = payload.get("gate"), payload.get("event")
        if isinstance(gate, str) and isinstance(event, str):
            pairs.add((gate, event))
    return pairs


def _has_approval_event(repo: Path, run_id: str, gate: str, event: str) -> bool:
    """Whether the run's event log already carries this ``approval`` ``(gate,
    event)``. A gate is unique per run because each breakpoint holds and is
    granted at most once (AC8)."""
    return (gate, event) in _logged_approval_events(repo, run_id)


def _catch_up_granted_breakpoints(ctx: RunContext) -> None:
    """Emit a missing ``approval``/``granted`` event for a breakpoint that was
    granted (the run advanced past it) but whose event a crash prevented from
    being logged — the crash-safe symmetry to the ``awaited`` catch-up (B8/AC12).

    Uses NO extra persisted state (the contract pins ``pending_breakpoint`` as the
    only state addition; plan B2 forbids a separate grant-proof field): the fact
    of a grant is derived from the event log itself. A breakpoint whose
    ``awaited`` is logged but whose ``granted`` is NOT, and at which the run is no
    longer waiting (``pending_breakpoint`` is not that gate), was released — its
    ``granted`` is appended exactly once (log-checked, so a crash-free run that
    already logged it appends nothing). This is the SINGLE emission site for
    breakpoint ``granted`` events: ``approve`` performs the release in its atomic
    phase transition but leaves the event to this catch-up, so a crash between the
    state save and the event append cannot lose it. (The spec/plan gates, which
    finish inside ``approve``, still emit directly — they have no crash window.)"""
    logged = _logged_approval_events(ctx.repo, ctx.state.run_id)
    for gate in _BREAKPOINT_GATES:
        if ctx.state.pending_breakpoint == gate:
            continue  # still waiting at this breakpoint — not granted
        if (gate, "awaited") in logged and (gate, "granted") not in logged:
            ctx.emitter.emit(
                "approval",
                {"gate": gate, "event": "granted"},
                span=ctx.emitter.current_span_id(),
            )


def _execute(ctx: RunContext) -> None:
    """All phases in order — each function checks for itself whether it is up."""
    # First close any breakpoint-grant crash window: append a granted event the
    # atomic approval save recorded but a crash prevented from being logged.
    _catch_up_granted_breakpoints(ctx)
    try:
        run_spec_and_plan(ctx)
        run_build_phase(ctx)
        run_integration_phase(ctx)
        run_codex_review_phase(ctx)
        run_final_review_phase(ctx)
        run_ci_phase(ctx)
    except AwaitingApproval:
        if ctx.state.pending_breakpoint is not None:
            # Breakpoint-Halt: `gate` ist der Haltepunktname. Log-geprüfte Emission
            # (crash-sicher, duplikatfrei — AC12): nur emittieren, wenn noch kein
            # awaited mit diesem Gate im Log steht. Nach einem Crash zwischen
            # State-Save (B3) und Log-Write holt der nächste resume es genau
            # einmal nach; ein wiederholter resume hängt nichts an.
            gate = ctx.state.pending_breakpoint
            if not _has_approval_event(ctx.repo, ctx.state.run_id, gate, "awaited"):
                ctx.emitter.emit(
                    "approval",
                    {"gate": gate, "event": "awaited"},
                    span=ctx.emitter.current_span_id(),
                )
            typer.echo(
                f"Breakpoint '{gate}' erreicht — Run {ctx.state.run_id} pausiert. "
                f"Freigeben mit `adw approve {ctx.state.run_id} --repo <pfad>`"
            )
            raise typer.Exit(EXIT_AWAITING_APPROVAL) from None
        # awaited: ein tatsächlich eingetretenes Warten am Gate (AC 9).
        gate = "spec" if ctx.state.phase == "awaiting_spec_approval" else "plan"
        ctx.emitter.emit(
            "approval", {"gate": gate, "event": "awaited"}, span=ctx.emitter.current_span_id()
        )
        # Die Zusammenfassung der Synthese ist die Entscheidungsgrundlage am
        # Gate — sie liegt im ignorierten Run-Ordner und wäre sonst unsichtbar.
        if ctx.state.phase == "awaiting_spec_approval":
            typer.echo(
                f"Spec-Approval ausstehend: .adw/runs/{ctx.state.run_id}/spec-summary.md "
                f"und .adw/runs/{ctx.state.run_id}/spec.md lesen, "
                f"dann `adw approve {ctx.state.run_id}`"
            )
        else:
            typer.echo(
                f"Plan-Approval ausstehend: .adw/runs/{ctx.state.run_id}/plan-summary.md "
                f"und .adw/runs/{ctx.state.run_id}/plan.md lesen, "
                f"dann `adw approve {ctx.state.run_id}`"
            )
        raise typer.Exit(EXIT_AWAITING_APPROVAL) from None
    except WorktreeRefusedError as exc:
        # Verweigerung, KEINE Eskalation: der In-Phasen-Guard fing einen dirty
        # Haupt-Checkout ab (Race zwischen Vorflug und Phasenlauf). Kein Save,
        # kein escalation.md — der Run bleibt unverändert und resumierbar (B1).
        typer.echo(
            f"Ausführung verweigert: {exc}\n"
            f"Der Run {ctx.state.run_id} bleibt unverändert in Phase '{ctx.state.phase}' — "
            f"nach dem Aufräumen fortsetzen mit: adw resume {ctx.state.run_id}",
            err=True,
        )
        raise typer.Exit(1) from None
    except EscalationError as exc:
        typer.echo(
            f"Eskalation: {exc}\nReport: .adw/runs/{ctx.state.run_id}/escalation.md",
            err=True,
        )
        raise typer.Exit(1) from None
    except AgentRunError as exc:
        # Bewusst KEINE Eskalation: Ein fehlgeschlagener SDK-/CLI-Aufruf
        # (typisch: Abo-Fenster erschöpft) ist ein transienter Zustand.
        # Der Run bleibt am persistierten Checkpoint stehen und ist nach
        # dem Limit-Reset per `adw resume` exakt dort fortsetzbar —
        # phase=escalated wäre endgültig und würde das verhindern.
        typer.echo(
            f"Agent-Lauf abgebrochen (z. B. Plan-Limit erschöpft): {exc}\n"
            f"Der Run {ctx.state.run_id} bleibt in Phase '{ctx.state.phase}' — "
            f"später fortsetzen mit: adw resume {ctx.state.run_id}",
            err=True,
        )
        raise typer.Exit(1) from None
    typer.echo(f"Run {ctx.state.run_id} abgeschlossen (Phase: {ctx.state.phase})")


def _load_config(repo: Path, base_branch: str | None) -> AdwConfig:
    try:
        config = AdwConfig.load(repo)
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    if base_branch is not None:
        # model_validate statt model_copy: der Override muss dieselbe
        # Validierung durchlaufen wie der Wert aus der YAML (NonBlankStr).
        try:
            config = AdwConfig.model_validate(config.model_dump() | {"base_branch": base_branch})
        except ValidationError as exc:
            raise _fail(f"--base-branch ungültig: {exc}") from exc
    return config


def _config_for_continuation(
    repo: Path, state: RunState, cli_override: str | None
) -> tuple[AdwConfig, bool]:
    """Config for resume/approve: the CLI flag wins, otherwise the run's pins.

    Run-start pins beat the file: the base branch comes from
    ``state.pinned_base_branch`` and the breakpoints from
    ``state.pinned_breakpoints`` (likewise ``pinned_effort`` and
    ``pinned_codex_reasoning_effort``), so editing ``.adw/config.yaml`` mid-run can
    neither move existing Lanes nor add/remove a future hold.

    Validate first (config loading stays OUTSIDE the run span, contract excludes),
    then mutate the pinned base branch IN MEMORY only — the persistence is the
    run's first save and belongs INSIDE the run span (E1), so the caller does it
    with the emitter/span. Returns ``(config, rebased)`` where ``rebased`` is
    True iff the pinned base branch changed and must be persisted.

    Once the Lanes have been created the base branch is fixed: the Lanes are
    forked from the old base, a switch would yield inconsistent diffs and merges."""
    candidate = cli_override if cli_override is not None else state.pinned_base_branch
    config = _load_config(repo, candidate)
    if state.pinned_breakpoints is not None:
        # Die Haltepunkte des Laufs kommen aus dem Run-Start-Schnappschuss, nicht
        # aus der (womöglich zwischenzeitlich editierten) config.yaml. None wäre
        # ein State von vor der Pinnung — der folgt weiter der Config.
        config = config.model_copy(update={"breakpoints": list(state.pinned_breakpoints)})
    if state.pinned_effort is not None:
        config = config.model_copy(update={"effort": dict(state.pinned_effort)})
    if state.pinned_codex_reasoning_effort is not None:
        codex = config.codex.model_copy(
            update={"reasoning_effort": state.pinned_codex_reasoning_effort}
        )
        config = config.model_copy(update={"codex": codex})
    if cli_override is not None and cli_override != state.pinned_base_branch:
        if state.lanes:
            raise _fail(
                "--base-branch kann nach dem Anlegen der Lanes nicht mehr geändert "
                "werden — die Lanes sind vom bisherigen Base-Branch geforkt"
            )
        state.pinned_base_branch = cli_override
        return config, True
    return config, False


def _load_state(repo: Path, run_id: str) -> RunState:
    try:
        return RunState.load(repo, run_id)
    except StateNotFoundError as exc:
        raise _fail(str(exc)) from exc


@contextlib.contextmanager
def _repo_lock(repo: Path):
    """Repo-wide, NON-blocking lock around one whole ADW command (C3): taken before
    the preflight, held until the command ends (incl. auto-prune). A second
    ``run``/``resume``/``approve`` in the same repo refuses with exit 1 — no
    preflight, no self-heal, no run dir, no state change, no escalation.

    Sibling of ``.seq`` and ``.lock-<run_id>``, never deleted (same reason as
    those). ``ensure_runs_gitignored`` instead of a bare mkdir: on the first run
    in a repo the lock file must not show up as an untracked path and make the
    worktree preflight refuse a clean checkout. Taken BEFORE the per-run lock."""
    ensure_runs_gitignored(repo)
    with open(repo / RUNS_RELPATH / ".repo.lock", "a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _fail(
                "In diesem Repo läuft bereits ein ADW-Kommando — bitte warten, "
                "bis es beendet ist, dann erneut starten."
            ) from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextlib.contextmanager
def _run_execution_lock(repo: Path, run_id: str):
    """Exclusive interprocess lock for the whole load→revalidate→execute sequence
    of ONE run.

    The repo-wide state lock only serializes individual snapshot writes; it does
    NOT make a continuation's check/release/execute atomic. Without this lock two
    concurrent ``adw approve``/``adw resume`` on the same run could both pass the
    phase check, both release the pending breakpoint and each run the downstream
    phases — at ``before_push`` that pushes twice (P1). This flock is held for the
    whole continuation, so the second caller blocks, then reloads the (now
    advanced) state under the lock and rejects cleanly — exactly one release, one
    downstream execution.

    The lock file and its implementation are ``adw.state.run_lock`` — shared with
    retention, which takes the same lock non-blocking before pruning a run (C8)."""
    with run_lock(repo, run_id):
        yield


def _fetch_github_issue(repo: Path, issue_number: int) -> str:
    try:
        raw = _run_gh(["issue", "view", str(issue_number), "--json", "number,title,body"], repo)
        data = json.loads(raw)
    except (ci.CiError, json.JSONDecodeError) as exc:
        raise _fail(f"GitHub-Issue {issue_number} nicht lesbar: {exc}") from exc
    title = data.get("title") or f"Issue {issue_number}"
    body = data.get("body") or ""
    return f"#{data.get('number', issue_number)}: {title}\n\n{body}".strip()


def _fetch_gitlab_issue(repo: Path, issue_id: int) -> str:
    try:
        raw = _run_glab(["issue", "view", str(issue_id), "--output", "json"], repo)
        data = json.loads(raw)
    except (ci.CiError, json.JSONDecodeError) as exc:
        raise _fail(f"GitLab-Issue {issue_id} nicht lesbar: {exc}") from exc
    title = data.get("title") or f"Issue {issue_id}"
    description = data.get("description") or ""
    return f"#{data.get('iid', issue_id)}: {title}\n\n{description}".strip()


def _build_context(
    repo: Path,
    config: AdwConfig,
    state: RunState,
    emitter=None,
    run_totals: RunTotals | None = None,
) -> RunContext:
    if emitter is None:
        from adw.events import NoOpEmitter

        emitter = NoOpEmitter()
    if state.dry_run:
        config = dry.config(config)
        agents, codex = _dry_run_runners()
        return RunContext(
            repo=repo,
            config=config,
            state=state,
            agents=agents,
            codex=codex,
            run_glab=dry.glab(config),
            run_gh=dry.gh(config),
            sleep=lambda seconds: None,
            emitter=emitter,
        )
    return RunContext(
        repo=repo,
        config=config,
        state=state,
        agents=SdkAgentRunner(emitter=emitter, totals=run_totals),
        codex=CodexRunner(
            emitter=emitter,
            timeout=config.codex.timeout,
            totals=run_totals,
            reasoning_effort=config.codex.reasoning_effort,
        ),
        emitter=emitter,
    )


if __name__ == "__main__":
    app()
