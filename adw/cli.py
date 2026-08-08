"""adw CLI (typer): run / resume / approve / status — SPEC §5.

Exit codes: 0 = done, 2 = awaiting_approval (plan-approval pause),
1 = escalation or error.
"""

import contextlib
import json
import re
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from adw import ci, github
from adw.agents import AgentRunError, SdkAgentRunner
from adw.codex import CodexRunner
from adw.config import AdwConfig, ConfigError
from adw.events import EventEmitter
from adw.findings import Finding, ReviewResult
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import (
    AwaitingApproval,
    EscalationError,
    RunContext,
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
def _run_span(emitter: EventEmitter, state: RunState, config: AdwConfig, repo: Path):
    """The `run` span around the whole CLI lifecycle (E1), at EVERY exit. The end
    event is written before any exception propagates (span() writes end in its
    finally); the exception itself propagates unchanged."""
    start = time.monotonic()

    def totals() -> dict:
        return {"duration": time.monotonic() - start, "cost": 0.0, "tokens": 0}

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
        bool, typer.Option("--no-approval", help="Plan-Approval überspringen")
    ] = False,
    spec_approval: Annotated[
        bool,
        typer.Option("--spec-approval", help="Zusätzlicher Stopp NACH der Spec, VOR dem Plan"),
    ] = False,
    base_branch: Annotated[
        str | None,
        typer.Option("--base-branch", help="Base-Branch-Override gegenüber .adw/config.yaml"),
    ] = None,
) -> None:
    """Start a new run through all seven phases."""
    sources = [s for s in (issue, gitlab_issue, github_issue) if s is not None]
    if len(sources) != 1:
        raise _fail("genau EINE Issue-Quelle angeben: --issue, --gitlab-issue ODER --github-issue")
    repo = repo.resolve()
    # Config ZUERST validieren (fail fast) — kein Forge-Netzaufruf für ein
    # Repo, das ohnehin keine gültige .adw/config.yaml hat.
    config = _load_config(repo, base_branch)
    if issue is not None:
        issue_text = issue
    elif gitlab_issue is not None:
        issue_text = _fetch_gitlab_issue(repo, gitlab_issue)
    else:
        issue_text = _fetch_github_issue(repo, github_issue)
    state = RunState.new(issue=issue_text, parallel=parallel)
    state.dry_run = dry_run
    # --spec-approval beim Run-Start pinnen (wie --no-approval): der Resume-/
    # Approve-Aufruf kennt das CLI-Flag nicht mehr.
    state.spec_approval = spec_approval
    # Effektiven Base-Branch pinnen (Config ODER Override): Fortsetzungen
    # bauen exakt gegen diese Basis, auch wenn die config.yaml sich ändert.
    state.pinned_base_branch = config.base_branch
    # SOFORT persistieren: crasht der allererste Agent-Lauf, muss die
    # angezeigte run_id per `adw resume` auffindbar sein.
    ensure_runs_gitignored(repo)
    # E1: der eine Emitter dieses Runs wird NACH RunState.new und VOR dem ersten
    # state.save erzeugt — das erste state.saved-Event liegt damit im run-Span.
    emitter = EventEmitter(repo, state.run_id)
    with _run_span(emitter, state, config, repo) as run_handle:
        state.save(repo, emitter=emitter, span_id=run_handle.id)
        ctx = _build_context(
            repo, config, state, skip_approval=no_approval, spec_approval=spec_approval,
            emitter=emitter,
        )
        typer.echo(f"Run {state.run_id} gestartet (Phase: {state.phase})")
        _execute(ctx)


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
    config = _config_for_continuation(repo, state, base_branch)
    emitter = EventEmitter(repo, state.run_id)
    with _run_span(emitter, state, config, repo):
        ctx = _build_context(repo, config, state, emitter=emitter)
        typer.echo(f"Run {state.run_id} wird fortgesetzt (Phase: {state.phase})")
        _execute(ctx)


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
    config = _config_for_continuation(repo, state, base_branch)
    gate = "spec" if state.phase == "awaiting_spec_approval" else "plan"
    emitter = EventEmitter(repo, state.run_id)
    with _run_span(emitter, state, config, repo) as run_handle:
        if state.phase == "awaiting_spec_approval":
            # Spec approven: in die Plan-Phase übergehen. Der Lauf pausiert danach
            # regulär beim Plan-Approval, sofern der Run nicht mit --no-approval lief.
            state.spec_approval_granted = True
            state.phase = "plan"
        else:
            state.approval_granted = True
        state.save(repo, emitter=emitter, span_id=run_handle.id)
        # granted: ein tatsächlich eingetretener Produktzustand (AC 9).
        emitter.emit("approval", {"gate": gate, "event": "granted"}, span=run_handle.id)
        ctx = _build_context(repo, config, state, emitter=emitter)
        typer.echo(f"Approval erteilt — Run {state.run_id} läuft weiter")
        _execute(ctx)


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


# --- interne Verdrahtung ------------------------------------------------------


def _execute(ctx: RunContext) -> None:
    """All phases in order — each function checks for itself whether it is up."""
    try:
        run_spec_and_plan(ctx)
        run_build_phase(ctx)
        run_integration_phase(ctx)
        run_codex_review_phase(ctx)
        run_final_review_phase(ctx)
        run_ci_phase(ctx)
    except AwaitingApproval:
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


def _config_for_continuation(repo: Path, state: RunState, cli_override: str | None) -> AdwConfig:
    """Config for resume/approve: the CLI flag wins, otherwise the run's override.

    Validate first, THEN persist — a rejected override must not poison the
    state. Once the Lanes have been created the base branch is
    fixed: the Lanes are forked from the old base, a switch would yield
    inconsistent diffs and merges."""
    candidate = cli_override if cli_override is not None else state.pinned_base_branch
    config = _load_config(repo, candidate)
    if cli_override is not None and cli_override != state.pinned_base_branch:
        if state.lanes:
            raise _fail(
                "--base-branch kann nach dem Anlegen der Lanes nicht mehr geändert "
                "werden — die Lanes sind vom bisherigen Base-Branch geforkt"
            )
        state.pinned_base_branch = cli_override
        state.save(repo)
    return config


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
        agents=SdkAgentRunner(emitter=emitter),
        codex=CodexRunner(emitter=emitter),
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
