"""adw CLI (typer): run / resume / approve / status — SPEC §5.

Exit codes: 0 = done, 2 = awaiting_approval (plan-approval pause),
1 = escalation or error.
"""

import contextlib
import json
import re
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from adw import ci, github, retention
from adw.agents import AgentRunError, RunTotals, SdkAgentRunner
from adw.codex import CodexRunner
from adw.config import AdwConfig, ConfigError
from adw.env import safe_env
from adw.events import EventEmitter
from adw.findings import Finding, ReviewResult
from adw.gui.registry import register_repo
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import (
    AwaitingApproval,
    EscalationError,
    RunContext,
    WorktreeRefusedError,
    _current_span_id,
    run_build_phase,
    run_ci_phase,
    run_codex_review_phase,
    run_final_review_phase,
    run_integration_phase,
    run_spec_and_plan,
)
from adw.state import RUNS_RELPATH, RunState, StateNotFoundError
from adw.worktrees import ensure_runs_gitignored

app = typer.Typer(add_completion=False, help="Agentic Developer Workflow — 7-Phasen-Orchestrator")

EXIT_AWAITING_APPROVAL = 2

# Modul-Attribute statt Direkt-Import: Tests ersetzen die CLI-Aufrufe hier.
_run_glab = ci.run_glab
_run_gh = github.run_gh


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


# Genau die sechs ADW-eigenen Authoring-Artefakte (Konvention, nicht
# konfigurierbar, kein Glob — E2/B3): nur wenn AUSSCHLIESSLICH diese im
# Haupt-Checkout dirty sind, heilt der Vorflug sie selbst.
SELF_HEAL_ARTIFACTS = (
    ".adw/issue.md",
    ".adw/spec.md",
    ".adw/plan.md",
    ".adw/contract.yaml",
    ".adw/spec-summary.md",
    ".adw/plan-summary.md",
)


def _git_plain(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Git in the main checkout WITHOUT the escalating orchestrator wrapper — the
    worktree preflight must NEVER escalate a run (B1), only refuse or self-heal."""
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=safe_env(),
    )


def _worktree_dirty_paths(repo: Path) -> list[str]:
    """Full set of uncommitted paths in the main checkout (tracked + untracked).

    Determined up front from ``git status --porcelain -z`` so a decision is made
    over the COMPLETE set before anything is mutated. A failing ``git status``
    (nonzero exit, timeout, or launch failure) is NEVER treated as a clean tree —
    the preflight cannot then establish cleanliness, so it REFUSES (non-escalating)
    rather than silently proceeding to mutate state."""
    try:
        result = _git_plain(repo, "status", "--porcelain", "-z")
    except (OSError, subprocess.SubprocessError) as exc:
        raise _fail(
            "Arbeitsbaum-Prüfung fehlgeschlagen: `git status` im Haupt-Checkout "
            f"nicht ausführbar — {exc}"
        ) from exc
    if result.returncode != 0:
        raise _fail(
            "Arbeitsbaum-Prüfung fehlgeschlagen: `git status` im Haupt-Checkout "
            f"endete mit Exit {result.returncode} — {result.stderr.strip()[:500]}"
        )
    tokens = result.stdout.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        if not entry:
            i += 1
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        # Rename/Copy: "XY <neu>\0<orig>" — der Ursprungspfad ist ein eigenes Token.
        if "R" in status or "C" in status:
            i += 1
            if i < len(tokens) and tokens[i]:
                paths.append(tokens[i])
        i += 1
    return paths


def _heal_adw_artifact(repo: Path, relpath: str) -> None:
    """Reset ONE ADW-owned artifact: tracked → git checkout, untracked → delete.

    A failing heal REFUSES (non-escalating) instead of running on with a
    half-healed tree — the caller must not proceed to mutate state."""
    try:
        tracked = _git_plain(repo, "ls-files", "--", relpath)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _fail(f"Selbstheilung von {relpath} fehlgeschlagen: git ls-files — {exc}") from exc
    if tracked.returncode != 0:
        raise _fail(
            f"Selbstheilung von {relpath} fehlgeschlagen: git ls-files Exit "
            f"{tracked.returncode} — {tracked.stderr.strip()[:300]}"
        )
    if tracked.stdout.strip():
        try:
            result = _git_plain(repo, "checkout", "--", relpath)
        except (OSError, subprocess.SubprocessError) as exc:
            raise _fail(
                f"Selbstheilung von {relpath} fehlgeschlagen: git checkout — {exc}"
            ) from exc
        if result.returncode != 0:
            raise _fail(
                f"Selbstheilung von {relpath} fehlgeschlagen: git checkout Exit "
                f"{result.returncode} — {result.stderr.strip()[:300]}"
            )
    else:
        try:
            (repo / relpath).unlink(missing_ok=True)
        except OSError as exc:
            raise _fail(f"Selbstheilung von {relpath} fehlgeschlagen: {exc}") from exc


def _preflight_worktree(repo: Path) -> None:
    """Guard the main checkout before run/resume/approve (B1–B4). It NEVER
    escalates: a clean tree proceeds; a dirty set consisting EXCLUSIVELY of the
    six ADW-owned authoring artifacts self-heals and proceeds; anything else
    (foreign or mixed) refuses with a clear message, discarding nothing. A git
    failure (status/heal) is also a refusal, never a silent proceed."""
    dirty = _worktree_dirty_paths(repo)
    if not dirty:
        return
    if all(path in SELF_HEAL_ARTIFACTS for path in dirty):
        for path in dirty:
            _heal_adw_artifact(repo, path)
        # Re-Check: die Heilung MUSS den Checkout sauber hinterlassen haben —
        # sonst liefe der Lauf mit einem halb geheilten Baum weiter.
        remaining = _worktree_dirty_paths(repo)
        if remaining:
            raise _fail(
                "Selbstheilung ließ den Haupt-Checkout nicht sauber:\n  "
                + "\n  ".join(sorted(remaining))
            )
        return
    listing = "\n  ".join(sorted(dirty))
    raise _fail(
        "Der Haupt-Checkout hat uncommittete Änderungen außerhalb der ADW-eigenen "
        f"Authoring-Artefakte:\n  {listing}\n"
        "Bitte committen oder stashen, dann den Lauf erneut starten bzw. fortsetzen "
        "(der ADW verwirft fremde Änderungen nie)."
    )


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
            help="Altflag, weiterhin gültig: kein Gate, läuft autonom bis done (== --gates none)",
        ),
    ] = False,
    spec_approval: Annotated[
        bool,
        typer.Option(
            "--spec-approval",
            help="Altflag, weiterhin gültig: zusätzlicher Stopp NACH der Spec (== --gates both)",
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
    gate_mode, skip_approval, spec_approval = _resolve_gate_mode(gates, no_approval, spec_approval)
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
    # Arbeitsbaum-Vorflug VOR jeder State-Mutation: eine Verweigerung legt keinen
    # Run an und macht keinen Netzaufruf (B1/B4). Nie Eskalation.
    _preflight_worktree(repo)
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
    # SOFORT persistieren: crasht der allererste Agent-Lauf, muss die
    # angezeigte run_id per `adw resume` auffindbar sein.
    ensure_runs_gitignored(repo)
    # E1: der eine Emitter dieses Runs wird NACH RunState.new und VOR dem ersten
    # state.save erzeugt — das erste state.saved-Event liegt damit im run-Span.
    # trace.enabled=false schaltet das Ereignis-Log ab (D2/E6), ohne Lauf/State zu
    # berühren.
    emitter = EventEmitter(repo, state.run_id, enabled=config.trace.enabled)
    run_totals = RunTotals()
    with _run_span(emitter, state, config, repo, run_totals) as run_handle:
        state.save(repo, emitter=emitter, span_id=run_handle.id)
        ctx = _build_context(
            repo, config, state, skip_approval=skip_approval, spec_approval=spec_approval,
            emitter=emitter, run_totals=run_totals,
        )
        typer.echo(f"Run {state.run_id} gestartet (Phase: {state.phase}, Gates: {gate_mode})")
        _execute(ctx)
    # Nach erfolgreichem Abschluss (done): automatisches Pruning (D3). Eine
    # Eskalation/Approval-Pause hätte oben eine typer.Exit geworfen und käme hier
    # nicht an.
    _maybe_auto_prune(repo, config, state)


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="ID des fortzusetzenden Runs")],
    repo: Annotated[Path, typer.Option("--repo")] = Path("."),
    base_branch: Annotated[str | None, typer.Option("--base-branch")] = None,
) -> None:
    """Continue a run in the same phase after a crash or approval pause."""
    repo = repo.resolve()
    state = _load_state(repo, run_id)
    if state.phase == "escalated":
        raise _fail(
            f"Run {run_id} ist eskaliert — Report unter .adw/runs/{run_id}/escalation.md "
            f"lesen und einen neuen Run starten"
        )
    config, rebased = _config_for_continuation(repo, state, base_branch)
    # Arbeitsbaum-Vorflug VOR jeder State-Mutation (auch vor dem rebased-Save):
    # eine Verweigerung lässt den Run-State unverändert und resumierbar (B1).
    _preflight_worktree(repo)
    emitter = EventEmitter(repo, state.run_id, enabled=config.trace.enabled)
    run_totals = RunTotals()
    with _run_span(emitter, state, config, repo, run_totals) as run_handle:
        if rebased:
            # First persistence of this command — inside the run span (E1), so
            # its state.saved event is emitted within the span.
            state.save(repo, emitter=emitter, span_id=run_handle.id)
        ctx = _build_context(repo, config, state, emitter=emitter, run_totals=run_totals)
        typer.echo(f"Run {state.run_id} wird fortgesetzt (Phase: {state.phase})")
        _execute(ctx)
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
    state = _load_state(repo, run_id)
    if state.phase not in ("awaiting_approval", "awaiting_spec_approval"):
        raise _fail(
            f"Run {run_id} wartet nicht auf Approval (Phase: {state.phase}) — "
            f"für Crash-Fortsetzung `adw resume` benutzen"
        )
    # A re-pinned base branch is only mutated in memory here; approve's own save
    # below (inside the run span) persists it together with the approval grant.
    config, _rebased = _config_for_continuation(repo, state, base_branch)
    # Arbeitsbaum-Vorflug VOR dem Setzen/Speichern der Zusage: eine Verweigerung
    # lässt die Approval-Zusage ungesetzt und den Run unverändert am Gate (B1).
    _preflight_worktree(repo)
    if state.pending_breakpoint is not None:
        gate = state.pending_breakpoint
    else:
        gate = "spec" if state.phase == "awaiting_spec_approval" else "plan"
    emitter = EventEmitter(repo, state.run_id, enabled=config.trace.enabled)
    run_totals = RunTotals()
    with _run_span(emitter, state, config, repo, run_totals) as run_handle:
        breakpoint_release = state.pending_breakpoint is not None
        if breakpoint_release:
            # Breakpoint freigeben: die Freigabe crash-sicher im State vermerken
            # (granted_breakpoints), die Phase über die Grenze fortschalten UND
            # pending_breakpoint im SELBEN atomaren Save leeren — der freigegebene
            # Haltepunkt wird danach nie wieder erreicht (AC7/AC8). Das granted-
            # Event NICHT hier emittieren: es entsteht log-geprüft in _execute
            # (_catch_up_granted_breakpoints), damit es auch dann genau einmal im
            # Log landet, wenn der Prozess zwischen diesem Save und dem Event-
            # Append abstürzt (AC12). Der Plan-/Spec-Approval-Pfad bleibt unberührt
            # (Regressions-Pin AC2).
            if gate not in state.granted_breakpoints:
                state.granted_breakpoints.append(gate)
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
            emitter.emit("approval", {"gate": gate, "event": "granted"}, span=run_handle.id)
        ctx = _build_context(repo, config, state, emitter=emitter, run_totals=run_totals)
        typer.echo(f"Approval erteilt — Run {state.run_id} läuft weiter")
        _execute(ctx)
    # Reaching done through `approve` is a normal successful completion → same
    # automatic pruning as `run` (D3). A further pause/escalation raised above.
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

    application = create_app(repos=[str(p) for p in (repo or [])])
    if open_browser:
        import webbrowser

        webbrowser.open(f"http://{host}:{port}/")
    uvicorn.run(application, host=host, port=port)


# --- `adw runs`: Retention (list / prune) -------------------------------------

runs_app = typer.Typer(add_completion=False, help="Run-Verzeichnisse verwalten (list, prune)")
app.add_typer(runs_app, name="runs")


def _require_repo(repo: Path) -> Path:
    """A usable target repo: an existing directory that is a git worktree. Pruning
    removes worktrees and snapshot refs and listing dates from the log — a non-git
    directory cannot be operated on, so it is a clear error (exit 1), never a silent
    success (B3)."""
    repo = repo.resolve()
    if not repo.is_dir():
        raise _fail(f"--repo {repo} existiert nicht oder ist kein verwendbares Verzeichnis")
    try:
        result = _git_plain(repo, "rev-parse", "--show-toplevel")
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
    keep: Annotated[int, typer.Option("--keep", help="Neueste N Läufe schützen")] = 20,
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
    if keep < 0 or (older_than is not None and older_than < 0):
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


def _maybe_auto_prune(repo: Path, config: AdwConfig, state: RunState) -> None:
    """Automatic deleting-prune after a successful run (D3). Same rules as manual
    deleting pruning; disabled when keep_runs == 0. Fail-open: an error never
    changes the just-finished run's done phase or success exit code — it is only
    reported."""
    if state.phase != "done" or config.trace.keep_runs <= 0:
        return
    try:
        results = retention.prune(repo, keep=config.trace.keep_runs, older_than=None)
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


def _has_approval_event(repo: Path, run_id: str, gate: str, event: str) -> bool:
    """Whether the run's event log already carries an ``approval`` event with this
    ``gate`` and ``event`` — the log-checked dedup key (B8). A gate is unique per
    run because each breakpoint holds and is granted at most once (AC8)."""
    path = repo / RUNS_RELPATH / run_id / "events.jsonl"
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
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
        if payload.get("gate") == gate and payload.get("event") == event:
            return True
    return False


def _catch_up_granted_breakpoints(ctx: RunContext) -> None:
    """Emit any ``approval``/``granted`` event for an already-granted breakpoint
    that is missing from the log — the crash-safe symmetry to the ``awaited``
    catch-up (B8/AC12).

    ``approve`` records the grant in ``state.granted_breakpoints`` and advances
    the phase over the boundary in ONE atomic save, but the event append is a
    separate write. If the process crashes between them, the next resume/approve
    lands here and appends the missing ``granted`` exactly once (log-checked
    dedup); the normal, crash-free path finds it already logged and appends
    nothing. This is the SINGLE emission site for breakpoint ``granted`` events —
    ``approve`` itself no longer emits them, so a grant survives the crash window
    (whereas the spec/plan gates, which finish inside ``approve``, still emit
    directly)."""
    for gate in ctx.state.granted_breakpoints:
        if not _has_approval_event(ctx.repo, ctx.state.run_id, gate, "granted"):
            ctx.emitter.emit(
                "approval",
                {"gate": gate, "event": "granted"},
                span=_current_span_id(ctx.emitter),
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
                    span=_current_span_id(ctx.emitter),
                )
            typer.echo(
                f"Breakpoint '{gate}' erreicht — Run {ctx.state.run_id} pausiert. "
                f"Freigeben mit `adw approve {ctx.state.run_id} --repo <pfad>`"
            )
            raise typer.Exit(EXIT_AWAITING_APPROVAL) from None
        # awaited: ein tatsächlich eingetretenes Warten am Gate (AC 9).
        gate = "spec" if ctx.state.phase == "awaiting_spec_approval" else "plan"
        ctx.emitter.emit(
            "approval", {"gate": gate, "event": "awaited"}, span=_current_span_id(ctx.emitter)
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
    """Config for resume/approve: the CLI flag wins, otherwise the run's override.

    Validate first (config loading stays OUTSIDE the run span, contract excludes),
    then mutate the pinned base branch IN MEMORY only — the persistence is the
    run's first save and belongs INSIDE the run span (E1), so the caller does it
    with the emitter/span. Returns ``(config, rebased)`` where ``rebased`` is
    True iff the pinned base branch changed and must be persisted.

    Once the Lanes have been created the base branch is fixed: the Lanes are
    forked from the old base, a switch would yield inconsistent diffs and merges."""
    candidate = cli_override if cli_override is not None else state.pinned_base_branch
    config = _load_config(repo, candidate)
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
    skip_approval: bool = False,
    spec_approval: bool = False,
    emitter=None,
    run_totals: RunTotals | None = None,
) -> RunContext:
    if emitter is None:
        from adw.events import NoOpEmitter

        emitter = NoOpEmitter()
    if state.dry_run:
        config = _dry_run_config(config)
        agents, codex = _dry_run_runners()
        return RunContext(
            repo=repo,
            config=config,
            state=state,
            agents=agents,
            codex=codex,
            run_glab=_dry_run_glab(config),
            run_gh=_dry_run_gh(config),
            sleep=lambda seconds: None,
            emitter=emitter,
            skip_approval=skip_approval,
            spec_approval=spec_approval,
            dry_run=True,
        )
    return RunContext(
        repo=repo,
        config=config,
        state=state,
        agents=SdkAgentRunner(emitter=emitter, totals=run_totals),
        codex=CodexRunner(emitter=emitter, timeout=config.codex.timeout),
        emitter=emitter,
        skip_approval=skip_approval,
        spec_approval=spec_approval,
    )


# --- Dry-Run: kanonische Fixtures (0 Tokens, kein Netz) -----------------------

_DRY_SPEC = "# Spec (Dry-Run)\n\nZiel: Demo-Durchlauf durch alle sieben Phasen.\n"
_DRY_PLAN = "# Plan (Dry-Run)\n\n- Workstream backend\n- Workstream frontend (optional)\n"
_DRY_CONTRACT = "openapi: 3.1.0\ninfo:\n  title: Dry-Run-Kontrakt\n  version: 0.0.1\n"
# Je Autor eigene Entwurfstexte: identische Fixtures würden verdecken, wenn
# beide Draft-Dateien aus derselben Quelle stammten — der Dry-Run soll die zwei
# unabhängigen Entwürfe zeigen, aus denen die Synthese mergt.
_DRY_SPEC_DRAFT = "# Spec-Entwurf Claude (Dry-Run)\n\nZiel: Demo-Durchlauf.\n"
_DRY_PLAN_DRAFT = "# Plan-Entwurf Claude (Dry-Run)\n\n- Workstream backend\n"
_DRY_SPEC_CODEX_DRAFT = "# Spec-Entwurf Codex (Dry-Run)\n\nZiel: Demo-Durchlauf, zweite Sicht.\n"
_DRY_PLAN_CODEX_DRAFT = "# Plan-Entwurf Codex (Dry-Run)\n\n- Workstream backend (zweite Sicht)\n"
_DRY_CONTRACT_CODEX = "openapi: 3.1.0\ninfo:\n  title: Dry-Run-Kontrakt (Codex)\n  version: 0.0.1\n"
_DRY_SPEC_SUMMARY = "# Zusammenfassung der Spec (Dry-Run)\n\nBest-of beider Entwuerfe.\n"
_DRY_PLAN_SUMMARY = "# Zusammenfassung des Plans (Dry-Run)\n\nBest-of beider Entwuerfe.\n"
_OK = ReviewResult(verdict="ok", findings=[])


def _dry_run_config(config: AdwConfig) -> AdwConfig:
    """Canonical fixtures (PLAN task 11 / SPEC DoD 1): every Lane gets a
    synthetic Gate that only the second build run turns green (simulated
    Gate fail → fix task to the same session); the E2E Gate analogously turns
    green only through a triaged Lane fix (E2E triage path)."""
    data = config.model_dump()
    for lane in data["lanes"].values():
        lane["gates"] = list(lane["gates"]) + [
            {"name": "adw-dry-run-gate", "cmd": "test -f .adw-dry-run-fixed", "timeout": 10}
        ]
    if data.get("e2e"):
        data["e2e"]["cmd"] = "test -f .adw-dry-run-e2e-fixed"
    return AdwConfig.model_validate(data)


def _dry_run_runners() -> tuple[MockAgentRunner, MockCodexRunner]:
    agents = MockAgentRunner()
    # Entwurfs-Autoren der Draft-Stage …
    agents.script_files("spec_agent", {".adw/spec.md": _DRY_SPEC_DRAFT})
    agents.script_files(
        "plan_agent", {".adw/plan.md": _DRY_PLAN_DRAFT, ".adw/contract.yaml": _DRY_CONTRACT}
    )
    # … und die Synthese, die daraus Best-of-Artefakt UND Summary baut.
    agents.script_files(
        "spec_synthesis", {".adw/spec.md": _DRY_SPEC, ".adw/spec-summary.md": _DRY_SPEC_SUMMARY}
    )
    agents.script_files(
        "plan_synthesis",
        {
            ".adw/plan.md": _DRY_PLAN,
            ".adw/contract.yaml": _DRY_CONTRACT,
            ".adw/plan-summary.md": _DRY_PLAN_SUMMARY,
        },
    )

    def build_writes(cwd: Path) -> dict[str, str]:
        # Simulations-Stufe aus dem WORKTREE ableiten, nicht aus Prozess-
        # lokalen Call-Zählern: ein Resume mit frischem Mock muss den
        # checkpointeten Fix fortsetzen statt ihn zu wiederholen.
        demo = cwd / f"adw_dry_run_{cwd.name}.md"
        if not demo.exists():
            stage = 1  # Erstlauf: Demo-Datei, das synthetische Gate bleibt rot
        elif not (cwd / ".adw-dry-run-fixed").exists():
            stage = 2  # Fix des simulierten Gate-Fails
        else:
            stage = 3  # Fix des simulierten E2E-Fails (nur --parallel)
        files = {demo.name: f"Dry-Run-Artefakt der Lane {cwd.name} (Stufe {stage})\n"}
        if stage >= 2:
            files[".adw-dry-run-fixed"] = "ok\n"
        if stage >= 3:
            files[".adw-dry-run-e2e-fixed"] = "ok\n"
        return files

    agents.file_writes["build_agent"] = build_writes
    # Großzügig scripten: Restantworten in den Queues sind unschädlich,
    # eine LEERE Queue würde einen legitimen Folge-Aufruf abbrechen.
    agents.script("spec_agent", *["Spec-Entwurf geschrieben"] * 3)
    agents.script("plan_agent", *["Plan-Entwurf geschrieben"] * 3)
    agents.script("spec_synthesis", *["Spec geschrieben"] * 3)
    agents.script("plan_synthesis", *["Plan geschrieben"] * 3)
    agents.script("build_agent", *["gebaut"] * 12)
    triage = json.dumps(
        ReviewResult(
            verdict="needs_fixes",
            findings=[
                Finding(
                    severity="P2",
                    lane="backend",
                    file="adw_dry_run_backend.md",
                    issue="Simuliertes E2E-Finding",
                    remediation_plan=["Demo-Fix"],
                )
            ],
        ).model_dump()
    )
    agents.script("e2e_triage", *[triage] * 2)
    agents.script("log_analyst", *[triage] * 2)
    agents.script("final_reviewer", *[json.dumps(_OK.model_dump())] * 3)
    codex = MockCodexRunner()
    codex.script(*[_OK] * 8)  # Spec-, Plan-, Code-Reviews — alle grün
    codex.script_artifacts("spec", *[{"spec.md": _DRY_SPEC_CODEX_DRAFT} for _ in range(3)])
    codex.script_artifacts(
        "plan",
        *[
            {"plan.md": _DRY_PLAN_CODEX_DRAFT, "contract.yaml": _DRY_CONTRACT_CODEX}
            for _ in range(3)
        ],
    )
    return agents, codex


def _dry_run_gh(config: AdwConfig) -> github.RunGh:
    """Simulated green GitHub Actions incl. staging job — no network."""
    staging = config.ci.staging_job or "deploy-staging"

    def fake(argv: list[str], cwd: Path) -> str:
        if argv[0] == "api" and "/actions/runs?" in argv[1]:
            return json.dumps(
                {
                    "total_count": 1,
                    "workflow_runs": [{"id": 1, "status": "completed", "conclusion": "success"}],
                }
            )
        if argv[0] == "api" and "/jobs" in argv[1]:
            return json.dumps({"jobs": [{"id": 2, "name": staging, "conclusion": "success"}]})
        if argv[:2] == ["run", "view"]:
            return ""
        raise ci.CiError(f"Dry-Run: unerwarteter gh-Aufruf {argv}")

    return fake


def _dry_run_glab(config: AdwConfig) -> ci.RunGlab:
    """Simulated green pipeline incl. staging job — no network."""
    staging = config.ci.staging_job or "deploy-staging"

    def fake(argv: list[str], cwd: Path) -> str:
        if argv[0] == "api" and "/pipelines?" in argv[1]:
            match = re.search(r"sha=([0-9a-f]+)", argv[1])
            sha = match.group(1) if match else ""
            return json.dumps([{"id": 1, "status": "success", "sha": sha}])
        if argv[0] == "api":  # Jobs der Pipeline
            return json.dumps([{"id": 2, "name": staging, "status": "success"}])
        if argv[:2] == ["ci", "trace"]:
            return ""
        raise ci.CiError(f"Dry-Run: unerwarteter glab-Aufruf {argv}")

    return fake


if __name__ == "__main__":
    app()
