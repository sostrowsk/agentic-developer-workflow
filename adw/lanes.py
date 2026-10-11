"""Phase 3 and every fix cycle: build lanes in isolated worktrees, TDD RED stage,
gate loop, orchestrator commits, and the dispatch of findings back into lanes."""

import os
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from adw import snapshots
from adw.agents import REGISTRY, AgentSpec
from adw.config import Gate
from adw.context import (
    ARTIFACTS,
    RunContext,
    _active_lanes,
    _agent_run,
    _breakpoint_gate,
    _checkpoint_lane_session,
    _gate_failure_text,
    _git,
    _lane_span,
    _log_warning,
    _phase_span,
    _require_progress,
    escalate,
)
from adw.env import safe_env
from adw.findings import (
    Finding,
    ReviewResult,
)
from adw.gates import run_gates
from adw.git import run_git
from adw.review_policy import (
    _finding_key,
    _findings_text,
    _write_followups,
)
from adw.state import LaneState
from adw.triage import (
    MAX_GATE_ITERATIONS,
    LimitExceededError,
    check_gate_iterations,
    triage_final_review,
)
from adw.worktrees import (
    create_lane_worktree,
    lane_branch,
    lane_worktree_path,
    ports_for,
)

# --- Phase 3: Build-Lanes ---------------------------------------------------


@_phase_span("build")
def run_build_phase(ctx: RunContext) -> None:
    """Phase 3: build agent(s) per Lane in isolated Worktrees, Gate loop until
    green (max. 10 iterations, Circuit-Breaker), commit by the orchestrator."""
    lanes = _active_lanes(ctx)
    if ctx.state.parallel and len(lanes) > 1:
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            futures = {lane: pool.submit(_run_lane, ctx, lane, lanes) for lane in lanes}
            for future in futures.values():
                future.result()  # Eskalationen propagieren
    else:
        for lane in lanes:
            _run_lane(ctx, lane, lanes)
    # Grenze Build → Integration/Review: alle Build-Lanes sind grün, es hat weder
    # Integrations-/Merge- noch Review-Arbeit begonnen (AC4).
    _breakpoint_gate(ctx, "before_integration")
    ctx.state.phase = "integration" if ctx.state.parallel else "codex_review"
    ctx.save()


@_lane_span
def _run_lane(ctx: RunContext, lane: str, all_lanes: list[str]) -> None:
    existing = ctx.state.lanes.get(lane)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, lane, ctx.config.base_branch)
    if existing is not None and existing.completed:
        # Auch 'completed' wird revalidiert: Stimmt der Baum nicht mehr mit dem
        # persistierten Gate-Beweis überein (Manipulation im Crash-Fenster),
        # geht die Lane zurück in den Loop statt blind weitergereicht zu werden.
        if existing.gates_tree and existing.gates_tree == _worktree_tree_hash(ctx, worktree):
            return
        with ctx.state_lock:
            existing.completed = False
            existing.gates_passed = False
            existing.gates_tree = None
            ctx.save()
        existing = ctx.state.lanes.get(lane)
    if (
        existing is not None
        and existing.gates_passed
        and existing.gates_tree
        and existing.gates_tree == _worktree_tree_hash(ctx, worktree)
    ):
        # Crash nach dem persistierten Gates-grün-Beweis: nur noch abschließen —
        # aber NUR wenn der Baum noch EXAKT dem geprüften Stand entspricht
        # (Tree-Hash). Sonst zurück in den Loop: erneut Agent + Gates.
        _finalize_lane(ctx, worktree, lane, existing)
        return
    if existing is not None and existing.gates_passed:
        with ctx.state_lock:
            existing.gates_passed = False
            existing.gates_tree = None
            ctx.save()
    with ctx.state_lock:
        # Insertion unter dem Lock: parallele Lane-Threads mutieren dieselbe
        # lanes-Map, während save() sie serialisiert.
        lane_state = ctx.state.lanes.get(lane) or LaneState(
            worktree=str(worktree),
            branch=lane_branch(ctx.state.run_id, lane),
            ports=ports_for(ctx.state.run_id, lane),
        )
        ctx.state.lanes[lane] = lane_state
        if lane_state.base_sha is None:
            # Fork-Point pinnen: der Base-Branch kann während des Runs
            # weiterrücken — Restaurationen brauchen den Stand der Lane.
            lane_state.base_sha = _git(
                ctx, worktree, "merge-base", ctx.config.base_branch, "HEAD"
            ).strip()
        ctx.save()
    _seed_artifacts(ctx, worktree)
    deny = [
        str(lane_worktree_path(ctx.repo, ctx.state.run_id, other))
        for other in all_lanes
        if other != lane
    ]
    spec = REGISTRY["build_agent"]
    if _needs_red_stage(ctx, lane, lane_state):
        # Setzt red_confirmed + den Implementierungs-Task als pending_task —
        # der Loop unten startet damit im selben Agent-Kontext weiter.
        _confirm_red(ctx, lane, worktree, lane_state, spec, deny)
    # Resume: offenes Gate-Feedback und Circuit-Breaker-Basis aus dem State —
    # sonst verliert ein Crash zwischen Gate-Fail und Fix-Lauf den Kontext.
    task = lane_state.pending_task or (
        f"Implement the Workstream '{lane}' from .adw/plan.md strictly against "
        f".adw/contract.yaml (spec: .adw/spec.md). TDD: test first, confirm RED, "
        f"then implement minimally. You do not commit.\n\n"
        f"Issue:\n{ctx.state.issue}"
    )
    previous_failures: list[str] | None = lane_state.last_failures or None
    # Die Findings des Fix-Dispatches (leer beim initialen Build) entscheiden,
    # ob ein wirkungsloser Agent-Lauf eskaliert wird — siehe _handle_idle_agent_run.
    dispatch_findings = list(lane_state.pending_findings)
    while True:
        current_head = _git(ctx, worktree, "rev-parse", "HEAD").strip()
        with ctx.state_lock:
            _require_expected_head(ctx, lane, lane_state, current_head)
            # Limit VOR dem Versuch prüfen — nach Crash+Resume bei Iteration 10
            # darf kein 11. Versuch starten.
            try:
                check_gate_iterations(lane_state)
            except LimitExceededError as exc:
                ctx.emitter.emit(
                    "limit.hit",
                    {
                        "limit": "gate_iterations",
                        "value": lane_state.gate_iterations,
                        "cap": MAX_GATE_ITERATIONS,
                    },
                    span=ctx.emitter.current_span_id(),
                )
                raise escalate(ctx, f"Lane {lane}: {exc}") from exc
            lane_state.gate_iterations += 1
            lane_state.expected_head = current_head
            resume = lane_state.session_id
            ctx.save()  # Checkpoint VOR dem Agent-Lauf
        with ctx.emitter.span(
            "round",
            {"loop": "gates", "n": lane_state.gate_iterations, "cap": MAX_GATE_ITERATIONS},
        ) as _round:
            _round.end_payload = {"outcome": "aborted"}
            # Snapshot the worktree BEFORE the agent run; after_agent only on a
            # NORMAL return (an agent exception skips it — the before_agent
            # snapshot stays as the diff basis, GUI-SPEC §5, AC 10).
            snapshots.capture(ctx, worktree, "before_agent")
            result = _agent_run(
                ctx, spec, task, cwd=worktree, resume=resume, deny_read_paths=deny,
                on_session_id=lambda sid: _checkpoint_lane_session(ctx, lane_state, sid),
            )
            snapshots.capture(ctx, worktree, "after_agent")
            _require_no_agent_commit(ctx, lane, worktree, current_head)
            _require_lane_branch(ctx, worktree, lane_state)
            with ctx.state_lock:
                lane_state.session_id = result.session_id or lane_state.session_id
                ctx.save()  # Checkpoint VOR den (potenziell langen) Gates
            # VOR den Gates: approvte Artefakte restaurieren — Gates und Commit
            # müssen exakt denselben, approvten Stand sehen.
            _restore_approved_artifacts(
                ctx, worktree, lane_state.base_sha or ctx.config.base_branch
            )
            if lane_state.gate_iterations == 1 and not _worktree_dirty(ctx, worktree):
                _handle_idle_agent_run(ctx, lane, dispatch_findings)
            # Kein Save zwischen Gates und Feedback-Persistenz — jeder Checkpoint
            # nach einem Gate-Fail muss pending_task/last_failures bereits tragen.
            report = _run_lane_gates(ctx, lane, worktree, lane_state)
            # After EVERY gate iteration, regardless of the outcome (GUI-SPEC §5).
            snapshots.capture(ctx, worktree, "after_gates")
            _round.end_payload["outcome"] = "passed" if report.passed else "failed"
            if report.passed:
                _require_red_tests(ctx, lane, worktree, lane_state)
                break
            failures = [f"{f.gate}|{f.exit_code}|{f.output}" for f in report.failures]
            _require_progress(
                ctx, previous_failures, failures, f"lane:{lane}",
                f"Lane {lane}: Gate-Fix-Iteration hat nichts verändert — "
                f"Circuit-Breaker.\n\n{_gate_failure_text(report)}",
            )
            previous_failures = failures
            task = (
                f"The Gates have failed. Analyze the output and fix the root cause "
                f"(no workaround bypassing the Gates):\n\n"
                f"{_gate_failure_text(report)}"
            )
            with ctx.state_lock:
                lane_state.pending_task = task
                lane_state.last_failures = failures
                # Ab hier treibt Gate-Feedback den Lauf, nicht mehr die Findings.
                lane_state.pending_findings = []
                ctx.save()
            dispatch_findings = []
    with ctx.state_lock:
        lane_state.pending_task = None
        lane_state.last_failures = []
        lane_state.pending_findings = []
        lane_state.expected_head = None  # ab hier committet der Orchestrator selbst
        lane_state.gates_passed = True  # persistierter Beweis VOR dem Commit,
        lane_state.gates_tree = _worktree_tree_hash(ctx, worktree)  # an den Baum gebunden
        ctx.save()
    _finalize_lane(ctx, worktree, lane, lane_state)


def _require_expected_head(ctx: RunContext, lane: str, lane_state: LaneState, head: str) -> None:
    """Detect an agent commit from a crash window: expected_head is the HEAD
    persisted BEFORE the last agent run."""
    if lane_state.expected_head and lane_state.expected_head != head:
        raise escalate(
            ctx,
            f"Lane {lane}: HEAD hat sich außerhalb des Orchestrators bewegt "
            f"(Agent-Commit im Crash-Fenster?) — Commits sind Sache des "
            f"Orchestrators",
        )


def _require_no_agent_commit(ctx: RunContext, lane: str, worktree: Path, head_before: str) -> None:
    if _git(ctx, worktree, "rev-parse", "HEAD").strip() != head_before:
        raise escalate(
            ctx,
            f"Lane {lane}: der Build-Agent hat selbst committet — Commits "
            f"sind Sache des Orchestrators (Reviewer-/Gate-Invariante verletzt)",
        )


# Der rote Gate-Output geht gekürzt in den Implementierungs-Task: er soll die
# fehlschlagenden Tests benennen, nicht die Session mit einem Log fluten.
RED_OUTPUT_LINES = 40


def _needs_red_stage(ctx: RunContext, lane: str, lane_state: LaneState) -> bool:
    """RED proof only in the initial build of a Lane with tdd-marked Gates.

    Fix dispatches (pending_task) and every resume after the proof skip it —
    doubling every fix run would buy nothing."""
    return (
        bool(_tdd_gates(ctx, lane))
        and not lane_state.pending_task
        and not lane_state.red_confirmed
        and lane_state.gate_iterations == 0
    )


def _tdd_gates(ctx: RunContext, lane: str) -> list[Gate]:
    return [gate for gate in ctx.config.lanes[lane].gates if gate.tdd]


def _confirm_red(
    ctx: RunContext,
    lane: str,
    worktree: Path,
    lane_state: LaneState,
    spec: AgentSpec,
    deny: list[str],
) -> None:
    """TDD RED, proven by the orchestrator: a test-only agent run, then the
    tdd-marked Gates.

    At least one red = proof; ``red_confirmed`` and the implementation task are
    persisted in ONE save, so a resume goes straight into the implementation.
    All green means the tests do not cover the required behaviour (or it already
    exists) — that is an escalation, not a fix cycle. The check runs no Gate
    iteration: the budget belongs to the implementation."""
    current_head = _git(ctx, worktree, "rev-parse", "HEAD").strip()
    with ctx.state_lock:
        _require_expected_head(ctx, lane, lane_state, current_head)
    if lane_state.session_id is None:
        _run_test_only_pass(ctx, lane, worktree, lane_state, spec, deny, current_head)
    # Crash zwischen Test-Lauf und RED-Check (session_id bereits gecheckpointet):
    # der Worktree ist unverändert, es fehlt nur der Beweis — kein zweiter Lauf.
    _restore_approved_artifacts(ctx, worktree, lane_state.base_sha or ctx.config.base_branch)
    # Beide Prüfungen laufen NACH der Restauration und in beiden Pfaden (frischer
    # Lauf wie Resume) — ein Crash darf keinen Beweis ohne Tests durchlassen.
    red_tests = _changed_paths(ctx, worktree)
    if not red_tests:
        raise escalate(
            ctx,
            f"Lane {lane}: der Test-Lauf hat keine Änderungen im Worktree "
            f"hinterlassen — ohne Tests ist RED nicht beweisbar",
        )
    _require_no_deletions(ctx, lane, worktree)
    tdd_gates = _tdd_gates(ctx, lane)
    report = _run_lane_gates(ctx, lane, worktree, lane_state, tdd_gates)
    # The RED check with its real result (confirmed = the tdd gates are red).
    ctx.emitter.emit(
        "red.check",
        {
            "confirmed": not report.passed,
            "test_paths": red_tests,
            "gates": [gate.name for gate in tdd_gates],
        },
        span=ctx.emitter.current_span_id(),
    )
    if report.passed:
        raise escalate(
            ctx,
            f"Lane {lane}: Tests nach reinem Test-Lauf grün — RED nicht bestätigt; "
            f"die Tests decken das geforderte Verhalten nicht ab oder das Verhalten "
            f"existiert bereits",
        )
    # RED proven: snapshot the test-only worktree state (GUI-SPEC §5).
    snapshots.capture(ctx, worktree, "red")
    with ctx.state_lock:
        # EIN Save: Beweis und Implementierungs-Task gehören zusammen — ein
        # Crash dazwischen dürfte weder den Test-Lauf noch den Beweis verlieren.
        lane_state.red_confirmed = True
        # Die Pfade binden den Beweis an die Tests, die ihn geliefert haben —
        # ohne sie wären grüne Gates am Ende per Löschen erreichbar.
        lane_state.red_test_paths = red_tests
        lane_state.pending_task = (
            f"RED confirmed: the tests you just wrote fail as required (Gate output "
            f"below, shortened). Now implement minimally until the Gates pass — do "
            f"not weaken, skip, move or delete the tests ({', '.join(red_tests)}). "
            f"You do not commit.\n\n"
            f"{_shorten_tail(_gate_failure_text(report), RED_OUTPUT_LINES)}"
        )
        ctx.save()


def _run_test_only_pass(
    ctx: RunContext,
    lane: str,
    worktree: Path,
    lane_state: LaneState,
    spec: AgentSpec,
    deny: list[str],
    current_head: str,
) -> None:
    """The agent run that writes ONLY tests — same invariants as every build
    run: no agent commit, Lane branch, checkpoint before and after."""
    with ctx.state_lock:
        lane_state.expected_head = current_head
        ctx.save()  # Checkpoint VOR dem Agent-Lauf
    task = (
        f"Workstream '{lane}': Write ONLY the tests for your workstream (TDD RED "
        f"step): derive them from .adw/plan.md, .adw/contract.yaml and .adw/spec.md. "
        f"Do NOT write or modify any production code. You do not commit.\n\n"
        f"Issue:\n{ctx.state.issue}"
    )
    # This is a build-lane agent run too — snapshot before and after it (§5).
    snapshots.capture(ctx, worktree, "before_agent")
    result = _agent_run(
        ctx, spec, task, cwd=worktree, resume=None, deny_read_paths=deny,
        on_session_id=lambda sid: _checkpoint_lane_session(ctx, lane_state, sid),
    )
    snapshots.capture(ctx, worktree, "after_agent")
    _require_no_agent_commit(ctx, lane, worktree, current_head)
    _require_lane_branch(ctx, worktree, lane_state)
    with ctx.state_lock:
        # Checkpoint VOR den Gates: ohne die Session-ID auf Platte würde ein
        # Resume den Test-Lauf wiederholen statt nur den RED-Check.
        lane_state.session_id = result.session_id or lane_state.session_id
        ctx.save()


def _changed_paths(ctx: RunContext, worktree: Path) -> list[str]:
    """Paths the test-only pass created or touched — the RED proof hangs on them.

    -z instead of --porcelain: git quotes umlauts and spaces in the status
    format, NUL-separated names come through verbatim."""
    listings = (
        _git(ctx, worktree, "ls-files", "--others", "--exclude-standard", "-z"),
        _git(ctx, worktree, "diff", "--name-only", "-z", "HEAD"),
    )
    return sorted({name for listing in listings for name in listing.split("\0") if name})


def _require_red_tests(ctx: RunContext, lane: str, worktree: Path, lane_state: LaneState) -> None:
    """Green Gates count only with the tests that proved RED still in place —
    deleting them would otherwise be the shortest path to green."""
    missing = [name for name in lane_state.red_test_paths if not (worktree / name).exists()]
    if missing:
        raise escalate(
            ctx,
            f"Lane {lane}: Gates grün, aber die RED-Tests fehlen "
            f"({', '.join(missing)}) — gelöschte oder verschobene Tests beweisen nichts",
        )


def _require_no_deletions(ctx: RunContext, lane: str, worktree: Path) -> None:
    """A test-only pass ADDS tests — it deletes nothing.

    Deleted files turn the marked Gates red without a single new test: the
    cheapest way to forge the RED proof. Modified files stay allowed — new
    tests often land in existing test files, and which paths are tests is
    something the orchestrator cannot know in a stack-neutral way."""
    deleted = [
        name
        for name in _git(
            ctx, worktree, "diff", "--name-only", "--diff-filter=D", "HEAD"
        ).splitlines()
        if name
    ]
    if deleted:
        raise escalate(
            ctx,
            f"Lane {lane}: der Test-Lauf hat Dateien gelöscht ({', '.join(deleted)}) — "
            f"rote Gates wären dann kein RED-Beweis, sondern ein kaputter Baum",
        )


def _shorten_tail(text: str, max_lines: int) -> str:
    """Last lines of an output — like the Gate tail, the end carries the result."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return f"… ({len(lines) - max_lines} Zeilen gekappt)\n" + "\n".join(lines[-max_lines:])


def _handle_idle_agent_run(ctx: RunContext, lane: str, findings: list[Finding]) -> None:
    """An agent run that left the Worktree untouched — escalate or defer?

    In the initial build (no Findings) nothing to build means the run is
    pointless. After a fix dispatch it can be legitimate: pure P3 observations
    (documentation or process notes, findings the agent rejects with reason)
    demand no code change at all, and escalating there kills a finished,
    gate-green run. Those go into the follow-up report instead. As soon as a
    P1/P2 is among them, idleness is a real problem and stays an escalation."""
    if findings and all(item.severity == "P3" for item in findings):
        _write_followups(ctx, findings)
        with ctx.state_lock:
            # Vertagt merken: ein deterministischer Reviewer meldet dasselbe
            # Finding sonst erneut und der Circuit-Breaker killt den Run.
            deferred = ctx.state.deferred_findings
            deferred.extend(k for k in map(_finding_key, findings) if k not in deferred)
            ctx.save()
        _log_warning(
            ctx,
            f"Lane {lane}: Fix-Lauf ohne Worktree-Änderungen bei ausschließlich "
            f"P3-Findings — als Follow-up vermerkt statt eskaliert",
        )
        return
    raise escalate(
        ctx,
        f"Build-Agent (Lane {lane}) hat keine Änderungen im Worktree "
        f"hinterlassen — nichts zu bauen oder Agent-Lauf wirkungslos",
    )


def _require_lane_branch(ctx: RunContext, worktree: Path, lane_state: LaneState) -> None:
    """A SHA-identical branch switch (git switch --detach) would pass the
    HEAD check — the symbolic ref must remain the Lane branch."""
    result = run_git(
        worktree, "symbolic-ref", "--short", "-q", "HEAD", timeout=120, env=safe_env(ctx.git_env)
    )
    current_ref = result.stdout.strip()
    if result.returncode != 0 or current_ref != lane_state.branch:
        raise escalate(
            ctx,
            f"Worktree steht nicht mehr auf dem Lane-Branch {lane_state.branch!r} "
            f"(aktuell: {current_ref or 'detached HEAD'}) — Branch-Wechsel durch "
            f"den Agenten?",
        )


def _finalize_lane(ctx: RunContext, worktree: Path, lane: str, lane_state: LaneState) -> None:
    """After provably green Gates: commit, check the implementation, finish."""
    _require_lane_branch(ctx, worktree, lane_state)
    _commit_lane(ctx, worktree, lane, lane_state.base_sha or ctx.config.base_branch)
    if not _has_implementation_changes(ctx, worktree):
        raise escalate(
            ctx,
            f"Lane {lane}: Gates grün, aber keine Implementierungs-Änderungen "
            f"gegenüber {ctx.config.base_branch} (nur Artefakte) — Agent hat "
            f"nichts gebaut oder alles zurückgebaut",
        )
    with ctx.state_lock:
        lane_state.completed = True
        ctx.save()


def _worktree_tree_hash(ctx: RunContext, worktree: Path) -> str:
    """Content hash of the complete Worktree tree (incl. untracked, without ignored files).

    Runs over a TEMPORARY git index — the real index stays untouched.
    Binds the gates_passed proof to exactly this state."""
    tmp_index = ctx.run_dir / f".treehash-index-{os.getpid()}-{threading.get_ident()}"
    env = safe_env(ctx.git_env)
    env["GIT_INDEX_FILE"] = str(tmp_index)
    try:
        # Index aus HEAD seeden: sonst fehlen getrackte Dateien, die zugleich
        # einem Ignore-Pattern entsprechen, im Hash (add -A überspränge sie).
        for args in (["read-tree", "HEAD"], ["add", "-A"], ["write-tree"]):
            result = run_git(worktree, *args, timeout=120, env=env)
            if result.returncode != 0:
                raise escalate(
                    ctx, f"Tree-Hash fehlgeschlagen (git {args[0]}): {result.stderr.strip()}"
                )
        return result.stdout.strip()
    finally:
        tmp_index.unlink(missing_ok=True)


def _seed_artifacts(ctx: RunContext, worktree: Path) -> None:
    """Copy spec/plan/contract from the run folder into the Lane Worktree and
    commit — the build agent works against checked-in artifacts."""
    target_dir = worktree / ".adw"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        source = ctx.run_dir / name
        if not source.is_file():
            raise escalate(
                ctx,
                f"Archiviertes Artefakt {name} fehlt im Run-Ordner — keine Lane "
                f"darf gegen unvollständige/unapprovte Artefakte bauen",
            )
        shutil.copy2(source, target_dir / name)
    # Nur committen, wenn sich die ARTEFAKTE selbst geändert haben — beim
    # Resume kann der Worktree außerhalb von .adw dirty sein (Crash-Reste),
    # und ein leerer Commit würde den Lauf fälschlich eskalieren.
    if _git(ctx, worktree, "status", "--porcelain", "--", ".adw").strip():
        _git(ctx, worktree, "add", ".adw")
        _git(ctx, worktree, "commit", "-m", f"adw({ctx.state.run_id}): Spec/Plan/Kontrakt")


def _run_lane_gates(
    ctx: RunContext,
    lane: str,
    worktree: Path,
    lane_state: LaneState,
    gates: list[Gate] | None = None,
):
    """All Gates of the Lane — or the passed subset (RED check: tdd Gates only)."""
    gates = ctx.config.lanes[lane].gates if gates is None else gates
    extra_env = {f"{name.upper()}_PORT": str(port) for name, port in lane_state.ports.items()}
    return run_gates(gates, cwd=worktree, extra_env=extra_env, emitter=ctx.emitter)


def _commit_lane(ctx: RunContext, worktree: Path, lane: str, base_ref: str) -> None:
    _restore_approved_artifacts(ctx, worktree, base_ref)
    if not _worktree_dirty(ctx, worktree):
        return
    subject = f"adw({ctx.state.run_id}/{lane}): Build-Ergebnis nach grünen Gates"
    _git(ctx, worktree, "add", "-A")
    _git(ctx, worktree, "commit", "-m", subject)
    # AFTER the commit actually happened (AC 9).
    sha = _git(ctx, worktree, "rev-parse", "HEAD").strip()
    ctx.emitter.emit(
        "commit",
        {"lane": lane, "sha": sha, "subject": subject},
        span=ctx.emitter.current_span_id(),
    )


def _has_implementation_changes(ctx: RunContext, worktree: Path) -> bool:
    """Are there changes compared to the base branch BEYOND the .adw artifacts?"""
    changed = _git(
        ctx, worktree, "diff", "--name-only", f"{ctx.config.base_branch}...HEAD"
    ).splitlines()
    return any(name and not name.startswith(".adw/") for name in changed)


def _restore_approved_artifacts(ctx: RunContext, worktree: Path, base_ref: str) -> None:
    """The build agent builds AGAINST spec/plan/contract — it does not change them.

    Deviations from the approved versions in the run folder are reset before
    the commit; otherwise a Lane could silently rewrite the contract
    and parallel Lanes would build against different artifacts."""
    adw_dir = worktree / ".adw"
    if adw_dir.is_symlink():
        # .adw selbst als Symlink: NIE folgen — sonst schreibt der
        # unsandboxte Orchestrator außerhalb der Lane.
        adw_dir.unlink()
    elif adw_dir.exists() and not adw_dir.is_dir():
        adw_dir.unlink()  # .adw als Datei: ersetzen statt FileExistsError
    adw_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        approved = ctx.run_dir / name
        current = worktree / ".adw" / name
        if not approved.is_file():
            continue
        if current.is_symlink():
            # Symlink NIE folgen: is_dir/copy2 würden sonst den Referenten
            # treffen (Fremddatei-Korruption) statt das Artefakt zu ersetzen.
            current.unlink()
        elif current.is_dir():
            # Agent hat das Artefakt durch ein Verzeichnis ersetzt — wegräumen.
            shutil.rmtree(current)
        if not current.is_file() or current.read_bytes() != approved.read_bytes():
            shutil.copy2(approved, current)
    # Auch die Workflow-Config ist tabu: auf den Stand des Base-Branch
    # zurücksetzen. Schlägt das fehl (Config im Ziel-Repo untracked, also
    # nicht im Base-Branch), wird eine evtl. vom Agenten eingeschleuste
    # Config gelöscht statt still committet.
    restored = run_git(
        worktree, "checkout", base_ref, "--", ".adw/config.yaml",
        timeout=120, env=safe_env(ctx.git_env),
    )
    if restored.returncode != 0:
        # Nur der POSITIV verifizierte Nicht-im-Base-Branch-Fall rechtfertigt
        # das Löschen: ls-tree rc==0 mit LEEREM Output. rc!=0 (kaputter Ref,
        # Repo-Fehler) oder nicht-leerer Output → eskalieren, nichts löschen.
        on_base = run_git(
            worktree, "ls-tree", base_ref, "--", ".adw/config.yaml",
            timeout=120, env=safe_env(ctx.git_env),
        )
        if on_base.returncode != 0 or on_base.stdout.strip():
            raise escalate(
                ctx,
                f"Config-Restore im Worktree fehlgeschlagen: "
                f"{restored.stderr.strip()} / "
                f"{on_base.stderr.strip()}",
            )
        injected = worktree / ".adw" / "config.yaml"
        if injected.is_symlink():
            injected.unlink()  # Symlink NIE folgen (is_dir/rmtree träfen den Referenten)
        elif injected.is_dir():
            shutil.rmtree(injected)  # Agent hat ein Verzeichnis eingeschleust
        else:
            injected.unlink(missing_ok=True)


def _worktree_dirty(ctx: RunContext, worktree: Path) -> bool:
    return bool(_git(ctx, worktree, "status", "--porcelain").strip())


def _dispatch_lane_fixes(
    ctx: RunContext,
    findings: list[Finding],
    lanes: list[str],
    source: str,
    mutate_staged: Callable[[list[str]], None] | None = None,
) -> None:
    """Hand Findings per Lane as a fix task into the regular Lane loop —
    every fix takes the validated path (Gates, commit, no special route).

    ``mutate_staged`` runs under the same lock and is persisted with the same
    save as the staging — counters (e.g. fix_cycles) and the staged
    fix task are thus atomic; a crash cannot burn a cycle
    without the corresponding fix being caught up on resume."""
    decision = triage_final_review(
        ReviewResult(verdict="needs_fixes", findings=findings)
        if findings
        else ReviewResult(verdict="ok", findings=[]),
        active_lanes=lanes,
        emitter=ctx.emitter,
        span_id=ctx.emitter.current_span_id(),
    )
    with ctx.state_lock:
        # Erst ALLE Lanes validieren, dann mutieren — escalate() persistiert
        # den State und darf keinen halb gestagten Zustand mitschreiben.
        for lane in decision.fix_tasks:
            if ctx.state.lanes.get(lane) is None:
                raise escalate(
                    ctx,
                    f"{source}-Finding für unbekannte Lane {lane!r} — "
                    f"aktive Lanes: {', '.join(lanes)}",
                )
        if mutate_staged is not None:
            mutate_staged(list(decision.fix_tasks))
        for lane, items in decision.fix_tasks.items():
            lane_state = ctx.state.lanes[lane]
            text = _findings_text(ReviewResult(verdict="needs_fixes", findings=items))
            # Lane geht zurück in den Loop: neuer Fix-Task, frisches
            # Iterations-Budget (das Limit gilt pro Task, nicht pro Run).
            lane_state.completed = False
            lane_state.gates_passed = False
            lane_state.gates_tree = None
            lane_state.pending_task = (
                f"{source} Findings for your Lane. Fix the root causes (no workaround "
                f"bypassing the tests), TDD where sensible. You do not commit.\n\n{text}"
            )
            lane_state.last_failures = []
            lane_state.pending_findings = list(items)
            lane_state.gate_iterations = 0
        ctx.save()  # EIN Save: Staging aller Lanes + mutate_staged atomar
    for lane in decision.fix_tasks:
        _run_lane(ctx, lane, lanes)


def _resume_pending_lanes(ctx: RunContext, lanes: list[str]) -> None:
    """Before merge/review, send EVERY Lane through _run_lane: unfinished Lanes
    (crash after fix Dispatch) catch up on Gates + commit; finished Lanes go
    through the tree-hash revalidation and, on manipulation, return to the
    loop instead of feeding ungated changes into the review."""
    for lane in lanes:
        if ctx.state.lanes.get(lane) is not None:
            _run_lane(ctx, lane, lanes)
