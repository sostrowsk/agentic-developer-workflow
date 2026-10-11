"""Shared core of the orchestrator phases: RunContext, escalation, spans, the
escalating git wrapper and the agent/Codex call sites with their spans."""

import functools
import hashlib
import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from adw import ci, github
from adw.agents import AgentRunner, agent_run_start_payload
from adw.codex import CodexClient
from adw.config import AdwConfig
from adw.env import safe_env
from adw.events import EventEmitter, NoOpEmitter
from adw.gates import GateReport
from adw.git import run_git
from adw.state import LaneState, RunState
from adw.triage import (
    NoProgressError,
    check_progress,
)
from adw.worktrees import (
    lane_branch,
    lane_worktree_path,
    ports_for,
)

ARTIFACTS = ("spec.md", "plan.md", "contract.yaml")

# Der Original-Issue-Text wird als reviewbares Artefakt bereitgestellt (B3),
# damit Codex Spec/Plan gegen die tatsächliche Anforderung (Proportionalität)
# prüfen kann. NICHT in ARTIFACTS: kein Agent erzeugt es, keine Build-Lane baut
# dagegen — es ist reine Review-Referenz.
ISSUE_FILE = "issue.md"

# Zusammenfassung der Synthese je Authoring-Phase — sie liegt dem Menschen am
# Freigabe-Gate vor. NICHT in ARTIFACTS: keine Build-Lane baut dagegen, sie wird
# nie in eine Lane geseedet und ist keine Codex-Review-Referenz.
SPEC_SUMMARY = "spec-summary.md"
PLAN_SUMMARY = "plan-summary.md"
SUMMARIES = (SPEC_SUMMARY, PLAN_SUMMARY)

logger = logging.getLogger("adw.phases")  # stable name across the split


def _require_progress(ctx, previous, failures, scope: str, message: str) -> None:
    """Circuit breaker: identical failures twice in a row emit a
    ``circuit_breaker`` point and escalate — the same at every loop."""
    try:
        check_progress(previous, failures)
    except NoProgressError as exc:
        ctx.emitter.emit(
            "circuit_breaker",
            {"keys": failures, "scope": scope},
            span=ctx.emitter.current_span_id(),
        )
        raise escalate(ctx, message) from exc


def _agent_run(ctx, agent, task, cwd, resume=None, deny_read_paths=None, on_session_id=None):
    """Run an agent wrapped in its ``agent.run`` span at THIS call site (GUI-SPEC
    §6): the span lives here — mock and real runner alike — so a dry run produces
    it too. The runner fills the handle's contents (real: stream + usage/cost;
    mock: base result fields). Return value and exception semantics are unchanged
    (the exception propagates through the span's end write, as before).

    ``on_session_id`` (F5) is invoked by the runner as soon as the session id
    first appears in the stream, so a mid-run abort still leaves it checkpointed
    for a later resume."""
    with ctx.emitter.span("agent.run", agent_run_start_payload(agent, task, cwd, resume)) as handle:
        return ctx.agents.run(
            agent, task, cwd=cwd, resume=resume, deny_read_paths=deny_read_paths,
            span=handle, on_session_id=on_session_id,
        )


def _codex_review(ctx, kind, content_refs, cwd, context=None):
    """Run a Codex review wrapped in its ``codex.review`` span at THIS call site
    (GUI-SPEC §6). The full argv is built up front (shared builder) so it is
    complete in the start event and identical to what the runner executes."""
    argv = ctx.codex.effective_argv(kind, content_refs, cwd, context)
    start = {"kind": kind, "argv": argv, "cwd": str(cwd), "custom_prompt": context}
    with ctx.emitter.span("codex.review", start) as handle:
        return ctx.codex.review(kind, content_refs, cwd=cwd, context=context, span=handle)


def _emit_artifact(ctx, path: Path) -> None:
    """Record a successfully written artifact (draft, synthesis, spec/plan/…)."""
    data = Path(path).read_bytes()
    ctx.emitter.emit(
        "artifact",
        {
            "name": Path(path).name,
            "path": str(path),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        span=ctx.emitter.current_span_id(),
    )


def _log_warning(ctx, message: str, span_id: str | None = None) -> None:
    """Emit the orchestrator's own warning to the log AND mirror it as a ``log``
    event — the logging behaviour itself is unchanged.

    ``span_id`` lets a caller running off the main thread (e.g. a draft-pool
    worker, whose thread-local span stack is empty) pass the enclosing span
    explicitly so the point is never attributed to null."""
    logger.warning("%s", message)
    span = span_id if span_id is not None else ctx.emitter.current_span_id()
    ctx.emitter.emit("log", {"level": "warning", "message": message}, span=span)


class EscalationError(Exception):
    """The run cannot continue autonomously — a human takes over (exit ≠ 0)."""


class AwaitingApproval(Exception):
    """Plan-approval Gate: run paused, continuation via `adw approve`."""


class WorktreeRefusedError(Exception):
    """The main checkout carries uncommitted changes ADW must not touch — the
    run is REFUSED, never escalated (B1): no escalation report, no phase change,
    the run state stays unchanged and resumable. This is the last-instance guard
    that fires if the checkout turns dirty between the CLI preflight and the
    phase run; the CLI maps it to a clear non-zero exit."""


@dataclass
class RunContext:
    repo: Path
    config: AdwConfig
    state: RunState
    agents: AgentRunner
    codex: CodexClient
    run_glab: ci.RunGlab = ci.run_glab
    run_gh: github.RunGh = github.run_gh
    sleep: Callable[[float], None] = time.sleep
    # The ONE emitter of this run/process (GUI-SPEC §4.3). Defaults to the
    # no-op so existing RunContext(...) constructions stay behaviour-identical.
    emitter: "EventEmitter | NoOpEmitter" = field(default_factory=NoOpEmitter)
    # Gate mode (skip_approval/spec_approval) and dry_run live ONLY in the
    # persisted state — `adw run` pins them before the first save, so
    # resume/approve see exactly what the run started with.
    git_env: dict[str, str] = field(default_factory=dict)
    # Serialisiert State-Mutationen + Snapshot-Saves über parallele Lane-Threads —
    # sonst kann ein Save einen halb mutierten Zustand persistieren. RLock:
    # escalate() läuft teils unter bereits gehaltenem Lock.
    state_lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def run_dir(self) -> Path:
        return self.state.run_dir(self.repo)

    def save(self, span_id: str | None = None) -> None:
        # Persist exactly as before (existing tests spy on the old save
        # signature), then mirror state.saved via THIS run's emitter with the
        # enclosing span — only after the write succeeded (AC 9). ``span_id`` lets
        # an off-main-thread caller (draft-pool worker) pass the enclosing span
        # explicitly so state.saved never carries null.
        self.state.save(self.repo)
        span = span_id if span_id is not None else self.emitter.current_span_id()
        self.emitter.emit(
            "state.saved",
            {"seq": self.state.seq, "phase": self.state.phase},
            span=span,
        )


def escalate(ctx: RunContext, reason: str, span_id: str | None = None) -> EscalationError:
    """Write the escalation report, mark the state, return the error to be raised.

    ``span_id`` lets an off-main-thread caller (draft-pool worker) pass its
    enclosing span so the escalation point is never attributed to null."""
    with ctx.state_lock:  # RLock: auch unter bereits gehaltenem Lock aufrufbar
        origin_phase = ctx.state.phase  # capture BEFORE marking escalated
        ctx.run_dir.mkdir(parents=True, exist_ok=True)
        report = ctx.run_dir / "escalation.md"
        report.write_text(
            f"# Eskalation — Run {ctx.state.run_id}\n\n"
            f"- Issue: {ctx.state.issue}\n"
            f"- Phase: {ctx.state.phase}\n"
            f"- Lanes: {', '.join(ctx.state.lanes) or '—'}\n\n"
            f"## Grund\n\n{reason}\n",
            encoding="utf-8",
        )
        # Resolve the enclosing span ONCE so both state.saved (inside ctx.save)
        # and the escalation point carry the same non-null span — critical for a
        # draft-pool worker whose thread-local stack is empty.
        span = span_id if span_id is not None else ctx.emitter.current_span_id()
        ctx.state.phase = "escalated"
        ctx.save(span_id=span)
        # AFTER the escalation actually happened (AC 9); the point carries the
        # phase the run escalated FROM.
        ctx.emitter.emit(
            "escalation", {"reason": reason, "phase": origin_phase}, span=span
        )
    return EscalationError(reason)


def _breakpoint_gate(ctx: RunContext, name: str) -> None:
    """Pause at a configured breakpoint via the EXISTING approval path (E3).

    No-op unless ``name`` is an active breakpoint and human approval is not
    skipped (AC14). Otherwise it records the waiting breakpoint, switches the
    phase to the existing ``awaiting_approval`` (NO new Phase literal — E3b) and
    raises :class:`AwaitingApproval`. It deliberately emits NO event itself:
    state save and event append are separate writes, so the ``awaited`` event is
    emitted log-checked at the single choke point in ``cli._execute`` (B8),
    which keeps it crash-safe and duplicate-free (AC12)."""
    if name not in ctx.config.breakpoints:
        return
    if ctx.state.skip_approval:
        return
    with ctx.state_lock:
        ctx.state.pending_breakpoint = name
        ctx.state.phase = "awaiting_approval"
        ctx.save()
    raise AwaitingApproval(ctx.state.run_id)


def _phase_span(name: str):
    """Wrap a single-phase orchestrator function in a ``phase`` span and run it
    only when the state is in that phase — a skipped phase is a no-op (no span,
    no body). Adds no indentation to the body."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(ctx: "RunContext") -> None:
            if ctx.state.phase != name:
                return None
            with ctx.emitter.span("phase", {"name": name, "from_phase": name}) as handle:
                handle.end_payload = {"name": name, "to_phase": None}
                result = fn(ctx)
                handle.end_payload["to_phase"] = ctx.state.phase
                return result

        return wrapper

    return deco


def _lane_span(fn):
    """Wrap ``_run_lane`` in a ``lane`` span. Under --parallel this runs in a
    ThreadPool worker whose thread-local span stack is empty, so the span
    carries parent: null (E2); the phase/lane fields keep the attribution. Adds
    no indentation to the body."""

    @functools.wraps(fn)
    def wrapper(ctx: "RunContext", lane: str, all_lanes: list[str]) -> None:
        existing = ctx.state.lanes.get(lane)
        start_payload = {
            "name": lane,
            "branch": lane_branch(ctx.state.run_id, lane),
            "worktree": str(lane_worktree_path(ctx.repo, ctx.state.run_id, lane)),
            "base_sha": existing.base_sha if existing is not None else None,
            "ports": ports_for(ctx.state.run_id, lane),
        }
        with ctx.emitter.span("lane", start_payload, phase=ctx.state.phase, lane=lane) as handle:
            handle.end_payload = {"completed": False, "gate_iterations": 0, "fix_cycles": 0}
            try:
                return fn(ctx, lane, all_lanes)
            finally:
                # Advance the end payload from the PERSISTED counters on EVERY
                # exit (contract payload_end_on_exception): an escalation after N
                # gate iterations / fix cycles must not report 0/0. No except —
                # the exception propagates unchanged (fail-open untouched).
                ls = ctx.state.lanes.get(lane)
                if ls is not None:
                    handle.end_payload = {
                        "completed": ls.completed,
                        "gate_iterations": ls.gate_iterations,
                        "fix_cycles": ls.fix_cycles,
                    }

    return wrapper


def _restore_snapshot(path: Path, snapshot: bytes | None) -> None:
    """Reset the file exactly to the snapshot (None = did not exist)."""
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    if not path.is_file() or path.read_bytes() != snapshot:
        path.write_bytes(snapshot)


def _restore_all(protected: dict[Path, bytes | None]) -> None:
    for path, snapshot in protected.items():
        _restore_snapshot(path, snapshot)


def _validate_lanes(ctx: RunContext) -> None:
    if ctx.state.parallel and not ctx.config.is_parallel_capable:
        raise escalate(
            ctx,
            "--parallel verlangt eine frontend- UND backend-Lane in .adw/config.yaml "
            f"— konfiguriert: {', '.join(ctx.config.lanes)}",
        )


def _active_lanes(ctx: RunContext) -> list[str]:
    if ctx.state.parallel:
        return list(ctx.config.lanes)
    return ["backend"] if "backend" in ctx.config.lanes else list(ctx.config.lanes)[:1]


def _artifact_bytes(ctx: RunContext, name: str) -> bytes | None:
    path = ctx.repo / ".adw" / name
    return path.read_bytes() if path.is_file() else None


def _repo_relative(ctx: RunContext, path: Path) -> str:
    return path.relative_to(ctx.repo).as_posix()


def _reset_checkout_artifact(ctx: RunContext, name: str) -> None:
    """Remove the artifact from the checkout — tracked ones are reset to the
    checked-in state instead (a tracked deletion would stay in the checkout)."""
    path = ctx.repo / ".adw" / name
    if _git(ctx, ctx.repo, "ls-files", "--", f".adw/{name}").strip():
        _git(ctx, ctx.repo, "checkout", "--", f".adw/{name}")
    elif path.is_symlink() or path.is_file():
        # Symlink NIE folgen: unlink trifft den Link, nicht den Referenten.
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _checkpoint_authoring_session(ctx: RunContext, session_id: str) -> None:
    """Persist the authoring session id the moment the runner first reports it
    (F5) — a mid-run abort then leaves it in the state for a later resume.
    Idempotent: repeated reports of the same id write nothing."""
    with ctx.state_lock:
        if ctx.state.authoring_session != session_id:
            ctx.state.authoring_session = session_id
            ctx.save()


def _checkpoint_lane_session(ctx: RunContext, lane_state: LaneState, session_id: str) -> None:
    """Persist a lane agent's session id the moment the runner first reports it
    (F5). Idempotent; uses the same whole-state save as the post-run assignment."""
    with ctx.state_lock:
        if lane_state.session_id != session_id:
            lane_state.session_id = session_id
            ctx.save()


def _git(ctx: RunContext, cwd: Path, *args: str) -> str:
    """Git through the orchestrator — env whitelist, repo hooks disabled."""
    result = run_git(cwd, *args, timeout=120, env=safe_env(ctx.git_env))
    if result.returncode != 0:
        raise escalate(
            ctx, f"git {' '.join(args)} in {cwd} fehlgeschlagen: {result.stderr.strip()}"
        )
    return result.stdout


def _gate_failure_text(report: GateReport) -> str:
    lines = []
    for failure in report.failures:
        status = "Timeout" if failure.timed_out else f"Exit {failure.exit_code}"
        lines.append(f"### Gate {failure.gate} ({status})\n{failure.output}")
    return "\n\n".join(lines)
