"""The seven ADW phases as functions over a RunContext (SPEC §4) — phases 4–7
here, plus the facade the CLI imports all seven entry points from.

Control flow is code: loops, limits, Dispatch, Triage and state transitions
live in these modules — the agents only provide judgment (texts, Findings).

Trust boundary: the configuration of the TARGET repo (.git/config, e.g.
clean filters, signing programs) is considered trustworthy — it is
controlled by the user and lies outside the agents' write paths (.git
is not in the Worktree cwd). Orchestrator git runs with the env whitelist and
disabled repo hooks; configured filters/signers run as with any
manual git invocation by the user.
"""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from adw import ci, github
from adw.agents import REGISTRY
from adw.authoring import run_spec_and_plan
from adw.config import Gate
from adw.context import (
    AwaitingApproval,
    EscalationError,
    RunContext,
    WorktreeRefusedError,
    _active_lanes,
    _agent_run,
    _breakpoint_gate,
    _codex_review,
    _gate_failure_text,
    _git,
    _phase_span,
    _require_progress,
    escalate,
)
from adw.env import safe_env
from adw.findings import (
    SCHEMA_INSTRUCTION,
    FindingsParseError,
    ReviewResult,
    extract_review_result,
)
from adw.forge import Forge, ForgeError, detect_forge
from adw.gates import GateReport, run_gates
from adw.git import run_git
from adw.lanes import (
    _dispatch_lane_fixes,
    _resume_pending_lanes,
    run_build_phase,
)
from adw.review_policy import (
    _DEFERRED_DISPOSITION,
    _below_threshold_disposition,
    _circuit_breaker_keys,
    _dispatched_disposition,
    _disposition_line,
    _extend_context,
    _finding_keys,
    _findings_text,
    _severity_context,
    _split_by_severity,
    _without_deferred,
    _write_followups,
)
from adw.triage import (
    MAX_FIX_CYCLES,
    LimitExceededError,
    check_fix_cycles,
    triage_final_review,
)
from adw.worktrees import (
    create_lane_worktree,
    lane_branch,
    remove_lane_worktree,
)

# Facade: the entry points of all seven phases and the run vocabulary. Phases 1–2
# live in adw/authoring.py, phase 3 and the fix dispatch in adw/lanes.py, the
# shared core in adw/context.py, the review-loop policy in adw/review_policy.py.
__all__ = [
    "AwaitingApproval",
    "EscalationError",
    "RunContext",
    "WorktreeRefusedError",
    "escalate",
    "run_build_phase",
    "run_ci_phase",
    "run_codex_review_phase",
    "run_final_review_phase",
    "run_integration_phase",
    "run_spec_and_plan",
]

# --- Phase 4: Integration + E2E (nur --parallel) -----------------------------

MAX_E2E_ROUNDS = 10
INTEGRATION_LANE = "integration"


@_phase_span("integration")
def run_integration_phase(ctx: RunContext) -> None:
    """Phase 4: merge Lane branches onto an integration branch, run the E2E
    Gate; on red the E2E agent triages the failures back into the Lanes.
    Max. 10 rounds, Circuit-Breaker on identical E2E output."""
    lanes = _active_lanes(ctx)
    _resume_pending_lanes(ctx, lanes)
    _integration_loop(ctx, lanes)
    with ctx.state_lock:
        ctx.state.phase = "codex_review"
        ctx.save()


def _integration_loop(ctx: RunContext, lanes: list[str]) -> Path:
    """Merge + E2E until green (core of phase 4) — returns the green
    integration Worktree. The review phases also pass through here: EVERY
    review fix must go through the E2E Gate again, not just through the Lane Gates.
    The round budget (integration_rounds) applies run-wide."""
    previous: list[str] | None = ctx.state.integration_last_failures or None
    while True:
        # Limit VOR Merge/E2E prüfen: Ein Crash zwischen Runden-Save und
        # Eskalation darf beim Resume keine weitere Runde starten.
        if ctx.state.integration_rounds >= MAX_E2E_ROUNDS:
            raise escalate(
                ctx,
                f"Integration/E2E: {ctx.state.integration_rounds} Runden erreicht "
                f"(Limit {MAX_E2E_ROUNDS}) — Eskalation",
            )
        with ctx.emitter.span(
            "round",
            {"loop": "integration", "n": ctx.state.integration_rounds + 1, "cap": MAX_E2E_ROUNDS},
        ) as _round:
            _round.end_payload = {"outcome": "aborted"}
            worktree = _fresh_integration_worktree(ctx, lanes)
            report = _run_e2e_gate(ctx, worktree, lanes)
            if report is None or report.passed:
                _round.end_payload["outcome"] = "passed"
                break
            _round.end_payload["outcome"] = "failed"
            with ctx.state_lock:
                ctx.state.integration_rounds += 1
                ctx.save()  # Runden-Limit überlebt jeden Crash ab hier
            if ctx.state.integration_rounds >= MAX_E2E_ROUNDS:
                raise escalate(
                    ctx,
                    f"Integration/E2E: {ctx.state.integration_rounds} Runden erreicht "
                    f"(Limit {MAX_E2E_ROUNDS}) — Eskalation.\n\n{_gate_failure_text(report)}",
                )
            failures = [f"{f.gate}|{f.exit_code}|{f.output}" for f in report.failures]
            _require_progress(
                ctx, previous, failures, "integration",
                f"E2E-Fix-Runde hat nichts verändert — Circuit-Breaker.\n\n"
                f"{_gate_failure_text(report)}",
            )
            review = _triage_e2e(ctx, worktree, report)
            _dispatch_lane_fixes(ctx, review.findings, lanes, source="E2E")
            with ctx.state_lock:
                # Erst NACH dem Fix-Dispatch fortschreiben: ein Crash davor darf
                # beim Resume nicht als "identische Runde" fehl-eskalieren.
                ctx.state.integration_last_failures = failures
                ctx.save()
            previous = failures
    if ctx.state.integration_last_failures:
        with ctx.state_lock:
            ctx.state.integration_last_failures = []
            ctx.save()
    return worktree


def _fresh_integration_worktree(ctx: RunContext, lanes: list[str]) -> Path:
    """Rebuild the integration branch fresh from the base branch each round and
    merge all Lane branches into it — idempotent and thus crash-safe."""
    remove_lane_worktree(ctx.repo, ctx.state.run_id, INTEGRATION_LANE)
    worktree = create_lane_worktree(
        ctx.repo, ctx.state.run_id, INTEGRATION_LANE, ctx.config.base_branch
    )
    for lane in lanes:
        branch = lane_branch(ctx.state.run_id, lane)
        try:
            result = run_git(
                worktree, "merge", "--no-edit", branch, timeout=300, env=safe_env(ctx.git_env)
            )
        except subprocess.TimeoutExpired as exc:
            _abort_merge(ctx, worktree)
            raise escalate(
                ctx,
                f"Integrations-Merge der Lane {lane} ({branch}) hat das Timeout "
                f"überschritten — Merge abgebrochen, Mensch übernimmt",
            ) from exc
        if result.returncode != 0:
            _abort_merge(ctx, worktree)
            raise escalate(
                ctx,
                f"Integrations-Merge der Lane {lane} ({branch}) fehlgeschlagen — "
                f"Konflikt braucht menschliche Auflösung:\n"
                f"{result.stdout.strip()}\n{result.stderr.strip()}",
            )
        # AFTER the lane was actually merged without conflicts (AC 9).
        ctx.emitter.emit(
            "merge",
            {"lane": lane, "target": INTEGRATION_LANE, "conflicts": []},
            span=ctx.emitter.current_span_id(),
        )
    return worktree


def _abort_merge(ctx: RunContext, worktree: Path) -> None:
    """Best effort: do not leave a half-merged state behind."""
    try:
        run_git(worktree, "merge", "--abort", timeout=120, env=safe_env(ctx.git_env))
    except (subprocess.TimeoutExpired, OSError):
        pass  # der Worktree wird ohnehin je Runde neu aufgebaut


def _run_e2e_gate(ctx: RunContext, worktree: Path, lanes: list[str]) -> GateReport | None:
    if ctx.config.e2e is None:
        return None  # keine E2E-Config: Integration ist reiner Merge
    gate = Gate(name="e2e", cmd=ctx.config.e2e.cmd, timeout=ctx.config.e2e.timeout)
    extra_env: dict[str, str] = {}
    for lane in lanes:
        for name, port in ctx.state.lanes[lane].ports.items():
            extra_env[f"{name.upper()}_PORT"] = str(port)
    return run_gates([gate], cwd=worktree, extra_env=extra_env, emitter=ctx.emitter)


def _triage_e2e(ctx: RunContext, worktree: Path, report: GateReport) -> ReviewResult:
    spec = REGISTRY["e2e_triage"]
    task = (
        "The E2E tests on the integration branch are red. Assign each failure "
        "to a Lane (frontend/backend/unknown).\n\n"
        f"{SCHEMA_INSTRUCTION}\n\nE2E output:\n{_gate_failure_text(report)}"
    )
    result = _agent_run(ctx, spec, task, cwd=worktree, resume=None)
    try:
        return extract_review_result(result.text)
    except (FindingsParseError, ValidationError) as exc:
        raise escalate(ctx, f"E2E-Triage-Output unlesbar: {exc}") from exc


# --- Phase 5+6: Codex-Code-Review, finaler Review + Triage --------------------

# Runden-Cap der Codex-Code-Review-Phase. Zusammen mit der absteigenden
# Severity-Schwelle (_actionable_severities) konvergiert der Loop: ab Runde 3
# blockt nur noch P1, alles andere wird als Follow-up dokumentiert.
MAX_REVIEW_ROUNDS = 5


def _review_worktree(ctx: RunContext, lanes: list[str]) -> Path:
    """Worktree that reviews run against: in parallel mode the integration branch
    AFTER a green E2E Gate (every review fix must go through the complete
    integration loop again), in single-lane mode the Lane Worktree — both idempotently
    restorable, a crash resume needs no old state."""
    if ctx.state.parallel:
        return _integration_loop(ctx, lanes)
    return create_lane_worktree(ctx.repo, ctx.state.run_id, lanes[0], ctx.config.base_branch)


def _changed_files(ctx: RunContext, worktree: Path) -> list[str]:
    changed = _git(
        ctx, worktree, "diff", "--name-only", f"{ctx.config.base_branch}...HEAD"
    ).splitlines()
    return [name for name in changed if name]


def _advance_review_round(
    ctx: RunContext, round_no: int, context: list[str], failures: list[str]
) -> Callable[[list[str]], None]:
    """``mutate_staged`` callback: round counter, findings history and
    Circuit-Breaker baseline land in the SAME save as the staged fix task.

    A crash between the two would book the round as "fix dispatched" without a
    pending fix — the next round's higher severity threshold would then drop
    exactly that finding silently. The baseline is narrowed to the
    non-deferred Findings again AFTER the dispatch."""

    def advance(fix_lanes: list[str]) -> None:
        ctx.state.review_rounds = round_no
        ctx.state.review_prior_context = context
        ctx.state.review_last_failures = failures

    return advance


@_phase_span("codex_review")
def run_codex_review_phase(ctx: RunContext) -> None:
    """Phase 5: Codex reviews the integrated diff; Findings are routed via the
    lane field into the build Lanes, until the verdict is ok."""
    lanes = _active_lanes(ctx)
    _resume_pending_lanes(ctx, lanes)
    previous: list[str] | None = ctx.state.review_last_failures or None
    prior_context = list(ctx.state.review_prior_context)
    while True:
        # Limit VOR dem Review: Crash zwischen Runden-Save und Eskalation darf
        # beim Resume keine weitere Runde starten.
        if ctx.state.review_rounds >= MAX_REVIEW_ROUNDS:
            raise escalate(
                ctx,
                f"Codex-Code-Review: {ctx.state.review_rounds} Runden erreicht "
                f"(Limit {MAX_REVIEW_ROUNDS}) — Eskalation",
            )
        round_no = ctx.state.review_rounds + 1
        with ctx.emitter.span(
            "round", {"loop": "codex_review", "n": round_no, "cap": MAX_REVIEW_ROUNDS}
        ) as _round:
            _round.end_payload = {"outcome": "aborted"}
            worktree = _review_worktree(ctx, lanes)
            try:
                review = _codex_review(
                    ctx,
                    "code",
                    _changed_files(ctx, worktree),
                    cwd=worktree,
                    context=_severity_context(round_no, MAX_REVIEW_ROUNDS, prior_context),
                )
            except (FindingsParseError, ValidationError) as exc:
                raise escalate(ctx, f"Codex-Code-Review unlesbar: {exc}") from exc
            _round.end_payload["outcome"] = review.verdict
            if review.verdict == "ok":
                break
            findings = _without_deferred(ctx, review.findings)
            if not findings:
                break  # nur bereits vertagte Findings — sie stehen im Follow-up-Report
            already_deferred = [item for item in review.findings if item not in findings]
            actionable, dropped = _split_by_severity(findings, round_no)
            if dropped:
                _write_followups(ctx, dropped)
            if not actionable:
                break  # nur Findings unter der Schwelle — dokumentiert statt gefixt
            review = ReviewResult(verdict="needs_fixes", findings=actionable)
            prior_context = _extend_context(
                prior_context,
                [_disposition_line(item, _DEFERRED_DISPOSITION) for item in already_deferred]
                + [
                    _disposition_line(item, _below_threshold_disposition(round_no))
                    for item in dropped
                ]
                + [
                    _disposition_line(item, _dispatched_disposition(round_no))
                    for item in actionable
                ],
            )
            if round_no >= MAX_REVIEW_ROUNDS:
                # VOR dem Dispatch eskalieren: ein Fix, den nie wieder ein Review
                # prüfen kann, ist verschwendete (teure) Arbeit.
                raise escalate(
                    ctx,
                    f"Codex-Code-Review: {round_no} Runden erreicht "
                    f"(Limit {MAX_REVIEW_ROUNDS}) — Eskalation.\n\n{_findings_text(review)}",
                )
            failures = _finding_keys(review)
            _require_progress(
                ctx, previous, failures, "codex_review",
                f"Codex-Code-Review meldet unverändert dieselben Findings — "
                f"Circuit-Breaker.\n\n{_findings_text(review)}",
            )
            _dispatch_lane_fixes(
                ctx,
                review.findings,
                lanes,
                source="Codex-Code-Review",
                mutate_staged=_advance_review_round(ctx, round_no, prior_context, failures),
            )
            with ctx.state_lock:
                # Erst NACH dem Fix-Dispatch fortschreiben — wie in Phase 4. Der
                # Dispatch kann Findings vertagt haben; die fallen aus der Basis.
                # Basis sind NUR die actionable Findings: ein neues, dauerhaft
                # gedropptes P3 würde sonst ein stehengebliebenes P1 kaschieren.
                failures = _circuit_breaker_keys(ctx, review.findings)
                ctx.state.review_last_failures = failures
                ctx.save()
            previous = failures
    with ctx.state_lock:
        ctx.state.review_last_failures = []
        ctx.state.review_prior_context = []  # Verlauf gehört zu dieser Phase
        ctx.state.phase = "final_review"
        ctx.save()


@_phase_span("final_review")
def run_final_review_phase(ctx: RunContext) -> None:
    """Phase 6: final reviewer (read-only) checks against the spec; Triage in
    code: scope_gap → follow-up report, rest → fix cycle (max. 3 per Lane)."""
    lanes = _active_lanes(ctx)
    _resume_pending_lanes(ctx, lanes)
    previous: list[str] | None = ctx.state.final_review_last_failures or None
    fr_round = 0
    while True:
        fr_round += 1
        with ctx.emitter.span(
            "round", {"loop": "final_review", "n": fr_round, "cap": MAX_FIX_CYCLES}
        ) as _round:
            _round.end_payload = {"outcome": "aborted"}
            worktree = _review_worktree(ctx, lanes)
            spec = REGISTRY["final_reviewer"]
            task = (
                "Check the implementation in this Worktree read-only against "
                ".adw/spec.md (acceptance criteria, Definition of Done) and "
                ".adw/contract.yaml.\n\n"
                f"{SCHEMA_INSTRUCTION}\n\n"
                'Additionally MANDATORY on every Finding: the field "category" with '
                "one of the values scope_gap | implementation | trivial "
                "(triage basis)."
            )
            result = _agent_run(ctx, spec, task, cwd=worktree, resume=None)
            try:
                review = extract_review_result(result.text)
            except (FindingsParseError, ValidationError) as exc:
                raise escalate(ctx, f"Finaler Review unlesbar: {exc}") from exc
            _round.end_payload["outcome"] = review.verdict
            if review.verdict == "ok":
                break
            uncategorized = [f.file for f in review.findings if f.category is None]
            if uncategorized:
                # Ohne category ist keine Triage möglich — ein scope_gap würde
                # sonst still als Implementierungs-Fix durchgehen.
                raise escalate(
                    ctx,
                    f"Finaler Review ohne category-Feld bei: {', '.join(uncategorized)} — "
                    f"Findings sind nicht triagierbar",
                )
            findings = _without_deferred(ctx, review.findings)
            if not findings:
                break  # nur bereits vertagte Findings — sie stehen im Follow-up-Report
            review = ReviewResult(verdict="needs_fixes", findings=findings)
            decision = triage_final_review(
                review,
                active_lanes=lanes,
                emitter=ctx.emitter,
                span_id=ctx.emitter.current_span_id(),
            )
            if decision.followups:
                _write_followups(ctx, decision.followups)
            if not decision.fix_tasks:
                break  # nur scope_gaps: Report statt Auto-Restart (SPEC §4 Phase 6)
            fixable = [f for f in review.findings if f.category != "scope_gap"]
            failures = _finding_keys(ReviewResult(verdict="needs_fixes", findings=fixable))
            _require_progress(
                ctx, previous, failures, "final_review",
                f"Finaler Review meldet unverändert dieselben Findings — "
                f"Circuit-Breaker.\n\n{_findings_text(review)}",
            )

            def _bump_fix_cycles(fix_lanes: list[str]) -> None:
                # Läuft im Dispatch unter dem State-Lock und wird mit dem Staging
                # in EINEM Save persistiert — Zähler und Fix-Task sind atomar.
                for lane in fix_lanes:
                    try:
                        check_fix_cycles(ctx.state.lanes[lane])
                    except LimitExceededError as exc:
                        ctx.emitter.emit(
                            "limit.hit",
                            {
                                "limit": "fix_cycles",
                                "value": ctx.state.lanes[lane].fix_cycles,
                                "cap": MAX_FIX_CYCLES,
                            },
                            span=ctx.emitter.current_span_id(),
                        )
                        raise escalate(ctx, f"Lane {lane}: {exc}") from exc
                for lane in fix_lanes:
                    ctx.state.lanes[lane].fix_cycles += 1

            _dispatch_lane_fixes(
                ctx,
                review.findings,
                lanes,
                source="Final-Review",
                mutate_staged=_bump_fix_cycles,
            )
            with ctx.state_lock:
                # Erst NACH dem Fix-Dispatch fortschreiben — wie in Phase 4/5. Der
                # Dispatch kann Findings vertagt haben; die fallen aus der Basis.
                failures = _circuit_breaker_keys(ctx, fixable)
                ctx.state.final_review_last_failures = failures
                ctx.save()
            previous = failures
    with ctx.state_lock:
        ctx.state.final_review_last_failures = []
        ctx.save()
    # Grenze Final-Review → CI: VOR dem Eintritt in run_ci_phase halten, damit bei
    # einem Halt noch KEINE CI-Arbeit lief (kein Push, keine Integrations-/E2E-
    # Vorbereitung, kein Polling) — Single-Lane wie Parallel (AC5).
    _breakpoint_gate(ctx, "before_push")
    with ctx.state_lock:
        ctx.state.phase = "ci"
        ctx.save()


# --- Phase 7: Push + CI + Log-Analyst -----------------------------------------

MAX_CI_REENTRIES = 1


@_phase_span("ci")
def run_ci_phase(ctx: RunContext) -> None:
    """Phase 7: push the feature branch, poll the pipeline via glab. On red the
    log analyst reads the logs, ONE re-entry via the Lane loops — then a human."""
    lanes = _active_lanes(ctx)
    _resume_pending_lanes(ctx, lanes)
    forge = _ci_forge(ctx)
    while True:
        if ctx.state.parallel:
            worktree = _integration_loop(ctx, lanes)
            branch = lane_branch(ctx.state.run_id, INTEGRATION_LANE)
        else:
            worktree = create_lane_worktree(
                ctx.repo, ctx.state.run_id, lanes[0], ctx.config.base_branch
            )
            branch = lane_branch(ctx.state.run_id, lanes[0])
        if not ctx.state.dry_run:
            _push_branch(ctx, worktree, branch)
        pushed_sha = _git(ctx, worktree, "rev-parse", "HEAD").strip()
        try:
            # An den Push binden (sha): die terminale Pipeline eines FRÜHEREN
            # Push darf das frische Ergebnis nicht bewerten.
            if forge == "github":
                result = github.poll_ci(
                    ctx.repo,
                    branch,
                    ctx.config.ci,
                    run_gh=ctx.run_gh,
                    sleep=ctx.sleep,
                    sha=pushed_sha,
                    emitter=ctx.emitter,
                )
            else:
                result = ci.poll_pipeline(
                    ctx.repo,
                    branch,
                    ctx.config.ci,
                    run_glab=ctx.run_glab,
                    sleep=ctx.sleep,
                    sha=pushed_sha,
                    emitter=ctx.emitter,
                )
        except ci.CiTimeoutError as exc:
            raise escalate(ctx, f"CI-Timeout: {exc}") from exc
        except ci.CiError as exc:
            raise escalate(ctx, f"CI-Monitoring fehlgeschlagen: {exc}") from exc
        if result.passed:
            break
        if ctx.state.ci_reentries >= MAX_CI_REENTRIES:
            raise escalate(
                ctx,
                f"Pipeline erneut rot nach {ctx.state.ci_reentries} Re-Entry — "
                f"Eskalation an den Menschen.\n\n{result.log_excerpt}",
            )
        if not result.log_excerpt.strip():
            # canceled/skipped oder YAML-Fehler: keine Job-Logs — ein Analyst
            # ohne Evidenz würde nur halluzinieren und das Re-Entry verbrennen.
            raise escalate(
                ctx,
                f"Pipeline rot ohne verwertbare Job-Logs "
                f"(Pipeline {result.pipeline_id}) — Mensch übernimmt",
            )
        review = _analyze_ci_logs(ctx, worktree, result.log_excerpt)

        no_routable = (
            f"Pipeline rot, aber der Log-Analyst lieferte keine zuordenbaren "
            f"Findings (Pipeline {result.pipeline_id}) — Mensch übernimmt.\n\n"
            f"{result.log_excerpt}"
        )

        def _bump_ci_reentries(fix_lanes: list[str], no_routable: str = no_routable) -> None:
            # Läuft im Dispatch unter dem State-Lock und wird mit dem Staging
            # in EINEM Save persistiert — ein Crash kann das Re-Entry-Budget
            # nicht verbrennen, ohne dass der Fix beim Resume nachgeholt wird.
            if not fix_lanes:
                # Nichts zuzuordnen: ein Re-Push desselben Stands wäre sinnlos und
                # die spätere Eskalation ("rot nach Re-Entry") irreführend.
                raise escalate(ctx, no_routable)
            ctx.state.ci_reentries += 1

        _dispatch_lane_fixes(
            ctx, review.findings, lanes, source="CI", mutate_staged=_bump_ci_reentries
        )
        # AFTER the fixes were actually dispatched — the run re-enters the loop.
        ctx.emitter.emit(
            "ci.reentry",
            {"n": ctx.state.ci_reentries, "reason": "pipeline red"},
            span=ctx.emitter.current_span_id(),
        )
    with ctx.state_lock:
        ctx.state.phase = "done"
        ctx.save()


def _ci_forge(ctx: RunContext) -> Forge:
    """GitLab or GitHub? The config override wins, otherwise origin detection.

    In dry-run an unrecognizable host falls back to 'gitlab' — there the
    monitoring is mocked anyway and a throwaway repo often has no
    origin at all. In a real run fail fast applies: no poll against the wrong API.
    The CLI preflight (_preflight_forge) already refuses an undetectable forge
    before any agent runs; this escalation is the safety net for an origin or
    config that changed while the run was underway."""
    try:
        return detect_forge(ctx.repo, ctx.config.ci.provider)
    except ForgeError as exc:
        if ctx.state.dry_run:
            return "gitlab"
        raise escalate(ctx, str(exc)) from exc


def _push_branch(ctx: RunContext, worktree: Path, branch: str) -> None:
    """Push by the orchestrator. --force-with-lease: the integration branch
    is rebuilt fresh from base each round (non-fast-forward is expected), but
    foreign remote changes are never overwritten."""
    extra = dict(ctx.git_env)
    # SSH-Agent nur dem Push durchreichen (bewusst nicht global in safe_env):
    # Gates/Agents sollen den Agenten-Socket nicht orten können.
    if "SSH_AUTH_SOCK" in os.environ:
        extra.setdefault("SSH_AUTH_SOCK", os.environ["SSH_AUTH_SOCK"])
    try:
        result = run_git(
            worktree, "push", "--force-with-lease", "-u", "origin", branch,
            timeout=600, env=safe_env(extra),
        )
    except subprocess.TimeoutExpired as exc:
        raise escalate(ctx, f"Push von {branch} hat das Timeout überschritten") from exc
    if result.returncode != 0:
        raise escalate(
            ctx,
            f"Push von {branch} nach origin fehlgeschlagen:\n{result.stderr.strip()}",
        )


def _analyze_ci_logs(ctx: RunContext, worktree: Path, log_excerpt: str) -> ReviewResult:
    spec = REGISTRY["log_analyst"]
    task = (
        "The CI pipeline is red. Analyze the logs and assign each failure "
        "to a Lane (frontend/backend/unknown).\n\n"
        f"{SCHEMA_INSTRUCTION}\n\nCI logs:\n{log_excerpt}"
    )
    # cwd = der GEPUSHTE Worktree: der Analyst liest die Quellen, die die CI
    # getestet hat — nicht den Base-Checkout des Haupt-Repos.
    result = _agent_run(ctx, spec, task, cwd=worktree, resume=None)
    try:
        return extract_review_result(result.text)
    except (FindingsParseError, ValidationError) as exc:
        raise escalate(ctx, f"Log-Analyst-Output unlesbar: {exc}") from exc
