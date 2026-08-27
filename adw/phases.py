"""The seven ADW phases as functions over a RunContext (SPEC §4).

Control flow is code: loops, limits, Dispatch, Triage and state transitions
live here — the agents only provide judgment (texts, Findings).

Trust boundary: the configuration of the TARGET repo (.git/config, e.g.
clean filters, signing programs) is considered trustworthy — it is
controlled by the user and lies outside the agents' write paths (.git
is not in the Worktree cwd). Orchestrator git runs with the env whitelist and
disabled repo hooks; configured filters/signers run as with any
manual git invocation by the user.
"""

import contextlib
import functools
import hashlib
import inspect
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from adw import ci, github, snapshots
from adw.agents import REGISTRY, AgentRunner, AgentSpec, agent_run_start_payload
from adw.codex import CodexAuthorError, CodexClient, CodexError
from adw.config import AdwConfig, Gate
from adw.env import safe_env
from adw.events import EventEmitter, NoOpEmitter
from adw.findings import (
    SCHEMA_INSTRUCTION,
    Finding,
    FindingsParseError,
    ReviewResult,
    extract_review_result,
)
from adw.forge import Forge, ForgeError, detect_forge
from adw.gates import GateReport, run_gates
from adw.state import LaneState, RunState
from adw.triage import (
    MAX_FIX_CYCLES,
    MAX_GATE_ITERATIONS,
    LimitExceededError,
    NoProgressError,
    check_fix_cycles,
    check_gate_iterations,
    check_progress,
    triage_final_review,
)
from adw.worktrees import (
    create_lane_worktree,
    ensure_runs_gitignored,
    lane_branch,
    lane_worktree_path,
    ports_for,
    remove_lane_worktree,
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

# Runden-Cap des Authoring-Loops: eine Runde = ein Agent-Lauf + ein
# Codex-Review. Nach so vielen erfolglosen Runden wird abgebrochen (P1 offen)
# bzw. das Artefakt mit dokumentierten Known Limitations akzeptiert (nur P2/P3).
AUTHORING_MAX_ROUNDS = 5

logger = logging.getLogger(__name__)


def _current_span_id(emitter) -> str | None:
    """Innermost open span on the current thread, or None.

    Point events must carry the id of their enclosing span (GUI-SPEC §4.2), but
    emit() does not read the thread-local stack itself. This reads the top of
    that stack so point-emitters (state.saved, escalation, …) attribute to the
    innermost run/phase/lane/round span active where they fire.

    KNOWN LIMITATION / FOLLOW-UP (reported, deliberately not fixed here): the
    frozen ``adw/events.py`` (AC 11) exposes no public accessor for the active
    span, so this reads the private ``emitter._stack()``. If a later run changes
    those internals, point attribution degrades to null. The proper fix is a
    separate run that adds a public ``current_span_id()`` to the emitter API and
    switches this over; until then ``test_dry_run_points_all_carry_a_non_null_span``
    is the regression guard."""
    stack = getattr(emitter, "_stack", None)
    if stack is None:  # NoOpEmitter has no stack
        return None
    try:
        current = stack()
        return current[-1] if current else None
    except Exception:
        return None


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
        kwargs = {"cwd": cwd, "resume": resume, "deny_read_paths": deny_read_paths, "span": handle}
        # Only hand the early-checkpoint callback to a runner that accepts it —
        # a runner without ``on_session_id`` (a plain test double) still works,
        # it just does not checkpoint early. Never retry the call (side effects).
        if _runner_accepts_session_callback(ctx.agents):
            kwargs["on_session_id"] = on_session_id
        return ctx.agents.run(agent, task, **kwargs)


def _runner_accepts_session_callback(runner) -> bool:
    # Inspect the ACTUAL callable (tests may override ``run`` as an instance
    # attribute), so a runner without the parameter is never handed the kwarg.
    try:
        return "on_session_id" in inspect.signature(runner.run).parameters
    except (TypeError, ValueError):
        return False


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
        span=_current_span_id(ctx.emitter),
    )


def _log_warning(ctx, message: str, span_id: str | None = None) -> None:
    """Emit the orchestrator's own warning to the log AND mirror it as a ``log``
    event — the logging behaviour itself is unchanged.

    ``span_id`` lets a caller running off the main thread (e.g. a draft-pool
    worker, whose thread-local span stack is empty) pass the enclosing span
    explicitly so the point is never attributed to null."""
    logger.warning("%s", message)
    span = span_id if span_id is not None else _current_span_id(ctx.emitter)
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
    skip_approval: bool = False
    # --spec-approval (Stopp nach Spec, vor Plan). Wie skip_approval als
    # State-Feld gepinnt, damit Resume/Approve das Gate kennen.
    spec_approval: bool = False
    # Dry-Run: Agents/Codex/glab sind Mocks; zusätzlich entfällt der echte
    # Push (externe Seiteneffekte gehören nicht in einen Dry-Run).
    dry_run: bool = False
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
        span = span_id if span_id is not None else _current_span_id(self.emitter)
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
        span = span_id if span_id is not None else _current_span_id(ctx.emitter)
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
    if ctx.skip_approval or ctx.state.skip_approval:
        return
    with ctx.state_lock:
        ctx.state.pending_breakpoint = name
        ctx.state.phase = "awaiting_approval"
        ctx.save()
    raise AwaitingApproval(ctx.state.run_id)


def _phase_span(name: str):
    """Wrap a single-phase orchestrator function in a ``phase`` span — only when
    the function actually runs its phase (its own guard returns immediately for
    a skipped phase, so no span opens then). Adds no indentation to the body."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(ctx: "RunContext") -> None:
            if ctx.state.phase != name:
                return fn(ctx)
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


def run_spec_and_plan(ctx: RunContext) -> None:
    """Phases 1–2: spec and plan agent with Codex review loops + approval Gate."""
    # Ignore-Regel VOR dem ersten State-/Artefakt-Write — der Haupt-Checkout
    # bleibt auch während der Approval-Pause sauber.
    ensure_runs_gitignored(ctx.repo)
    _validate_lanes(ctx)
    if ctx.skip_approval and not ctx.state.skip_approval:
        ctx.state.skip_approval = True
        ctx.save()  # sofort persistieren — der Resume-Aufruf kennt das Flag nicht
    if ctx.spec_approval and not ctx.state.spec_approval:
        ctx.state.spec_approval = True
        ctx.save()  # sofort persistieren — der Resume-Aufruf kennt das Flag nicht
    skip = ctx.skip_approval or ctx.state.skip_approval
    spec_approval = ctx.spec_approval or ctx.state.spec_approval
    # Die Ziel-Repo-Config ist für Agents tabu — Write(.adw/**) würde sie
    # technisch erlauben, deshalb Snapshot + Restore um die Authoring-Loops.
    config_path = ctx.repo / ".adw" / "config.yaml"
    config_snapshot = config_path.read_bytes() if config_path.is_file() else None
    if ctx.state.phase == "awaiting_approval":
        if ctx.state.pending_breakpoint is not None:
            # An einem Breakpoint wartend (NICHT am Plan-Gate): approval_granted
            # ist hier bereits True (der Plan wurde früher freigegeben) — deshalb
            # NICHT die Plan-Gate-Deutung ausführen und NICHT nach build übergehen.
            # Der Lauf bleibt wartend, bis `adw approve` den Breakpoint freigibt
            # und die Phase über die Grenze fortschaltet (B6/AC9). Das erneute
            # awaited-Event entscheidet log-geprüft cli._execute (B8/AC12).
            raise AwaitingApproval(ctx.state.run_id)
        if ctx.state.approval_granted or skip:
            ctx.state.phase = "build"
            ctx.save()
            return
        ctx.save()
        raise AwaitingApproval(ctx.state.run_id)
    if ctx.state.phase == "awaiting_spec_approval":
        # Archivierung idempotent nachholen: crasht ein Run zwischen dem
        # awaiting_spec_approval-Save und dem _archive_artifacts, lägen die
        # generierten Artefakte (spec.md, issue.md) sonst die ganze Pause über
        # dirty im Checkout. Cleanup-Modus: die bereits archivierte (reviewte)
        # Spec NICHT mit einer alten getrackten Checkout-Version überschreiben.
        _archive_artifacts(ctx, overwrite_existing_archive=False)
        if not ctx.state.spec_approval_granted:
            ctx.save()
            raise AwaitingApproval(ctx.state.run_id)
        # Approval erteilt: in die Plan-Phase übergehen und normal fortfahren.
        ctx.state.phase = "plan"
        ctx.save()
    if ctx.state.phase not in ("spec", "plan"):
        return  # Resume in einer späteren Phase — hier nichts zu tun

    # protected: nach JEDEM Agent-Lauf (vor dem Review) und exception-sicher
    # am Ende restauriert — Agents dürfen diese Dateien nie effektiv ändern.
    protected: dict[Path, bytes | None] = {config_path: config_snapshot}
    paused_for_spec_approval = False
    try:
        # Fail fast (in Phase spec UND plan): uncommittete Nutzer-Edits an
        # getrackten Artefakten würde die Archivierung (git checkout --)
        # verwerfen. Ausnahme: die vom ADW selbst restaurierte Spec (siehe
        # Plan-Resume) ist kein Nutzer-Edit.
        for name in (*ARTIFACTS, *SUMMARIES):
            if ctx.state.phase == "plan" and (ctx.run_dir / name).is_file():
                continue  # gehört diesem Run, wird gleich ohnehin überschrieben
            if (
                ctx.state.phase == "plan"
                and ctx.state.authoring_session
                and name in ("plan.md", "contract.yaml", PLAN_SUMMARY)
            ):
                continue  # eigener, gecrashter Plan-Loop — kein User-Edit
            if not _git(ctx, ctx.repo, "ls-files", "--", f".adw/{name}").strip():
                continue
            if _git(ctx, ctx.repo, "status", "--porcelain", "--", f".adw/{name}").strip():
                # Letzte Instanz, falls der Checkout zwischen CLI-Vorflug und
                # Phasenlauf dirty wird: VERWEIGERN statt eskalieren (B1) — der
                # Run bleibt unverändert und resumierbar, nichts wird verworfen.
                raise WorktreeRefusedError(
                    f".adw/{name} ist getrackt und hat uncommittete Änderungen — "
                    f"bitte committen oder stashen (ADW verwirft sie nicht und "
                    f"eskaliert nicht)"
                )
        # Issue-Text als reviewbares Artefakt (B3) — idempotent, damit ein
        # Resume in der Spec- ODER Plan-Phase es wieder herstellt; protected,
        # damit ein Agent es nicht effektiv verändern kann (wie config.yaml).
        protected[ctx.repo / ".adw" / ISSUE_FILE] = _write_issue(ctx)
        if ctx.state.phase == "spec":
            with ctx.emitter.span("phase", {"name": "spec", "from_phase": "spec"}) as _spec_phase:
                _spec_phase.end_payload = {"name": "spec", "to_phase": None}
                draft_task = _spec_draft_task(ctx)
                drafts = _draft_stage(
                    ctx,
                    kind="spec",
                    agent_name="spec_agent",
                    task=draft_task,
                    artifacts=("spec.md",),
                    protected=protected,
                    summary=SPEC_SUMMARY,
                )
                _reviewed_authoring_loop(
                    ctx,
                    agent_name="spec_synthesis",
                    initial_task=_synthesis_task(
                        drafts, artifacts=("spec.md",), summary=SPEC_SUMMARY, goal=draft_task
                    ),
                    review_kind="spec",
                    artifacts=("spec.md",),
                    summary=SPEC_SUMMARY,
                    # Issue mitgeben: Codex prüft die Spec auf Proportionalität
                    # gegen die tatsächliche Anforderung.
                    review_refs=(ISSUE_FILE, "spec.md"),
                    protected=protected,
                )
                with ctx.state_lock:
                    # Reviewte Spec SOFORT ins Run-Verzeichnis sichern: markiert sie
                    # als eigenen Output (Guard-Ausnahme beim Resume) und schützt
                    # sie vor dem Crash-Fenster bis zur Archivierung. Die Summary
                    # gehört zum Gate-Ergebnis und wird genauso gesichert.
                    ctx.run_dir.mkdir(parents=True, exist_ok=True)
                    for name in ("spec.md", SPEC_SUMMARY):
                        shutil.copy2(ctx.repo / ".adw" / name, ctx.run_dir / name)
                    # Spec-Gate: Phase + geleerter Checkpoint atomar. Ein Crash im
                    # Fenster darf nie phase="plan" hinterlassen, sonst würde der
                    # Stopp übersprungen (Runden-Zähler startet für den Plan bei 0).
                    if spec_approval and not ctx.state.spec_approval_granted:
                        ctx.state.phase = "awaiting_spec_approval"
                        paused_for_spec_approval = True
                    else:
                        ctx.state.phase = "plan"
                    ctx.save()  # Phase + geleerter Authoring-Checkpoint in EINEM Save
                _spec_phase.end_payload["to_phase"] = ctx.state.phase

        if not paused_for_spec_approval:
            with ctx.emitter.span("phase", {"name": "plan", "from_phase": "plan"}) as _plan_phase:
                _plan_phase.end_payload = {"name": "plan", "to_phase": None}
                # Phase "plan" — frisch erreicht ODER Resume nach Crash in der Plan-Phase.
                # Spec UND Spec-Summary sind das Ergebnis der Vorphase: sie werden
                # wiederhergestellt und sind ab hier fix — die Plan-Phase darf sie
                # lesen, aber nicht umschreiben (Write(.adw/**) würde es erlauben).
                for name in ("spec.md", SPEC_SUMMARY):
                    path = ctx.repo / ".adw" / name
                    archived = ctx.run_dir / name
                    if archived.is_file():
                        # Die ARCHIVIERTE (reviewte) Fassung hat immer Vorrang: Ein
                        # Crash mitten in der Archivierung kann die Checkout-Datei
                        # bereits auf eine alte getrackte Version zurückgesetzt haben.
                        shutil.copy2(archived, path)
                    elif not path.is_file():
                        raise escalate(
                            ctx,
                            f"Resume in Phase 'plan', aber .adw/{name} fehlt — "
                            "Spec-Ergebnis verloren, bitte Run neu starten",
                        )
                    protected[path] = path.read_bytes()

                draft_task = _plan_draft_task(ctx)
                plan_artifacts = ("plan.md", "contract.yaml")
                drafts = _draft_stage(
                    ctx,
                    kind="plan",
                    agent_name="plan_agent",
                    task=draft_task,
                    artifacts=plan_artifacts,
                    protected=protected,
                    summary=PLAN_SUMMARY,
                )
                _reviewed_authoring_loop(
                    ctx,
                    agent_name="plan_synthesis",
                    initial_task=_synthesis_task(
                        drafts, artifacts=plan_artifacts, summary=PLAN_SUMMARY, goal=draft_task
                    ),
                    review_kind="plan",
                    artifacts=plan_artifacts,
                    summary=PLAN_SUMMARY,
                    # Issue + Spec mitgeben: Codex prüft Plan/Kontrakt GEGEN die
                    # Akzeptanzkriterien und die tatsächliche Anforderung.
                    review_refs=(ISSUE_FILE, "spec.md", "plan.md", "contract.yaml"),
                    protected=protected,
                )
                # Archival + the plan→next transition happen INSIDE the plan
                # span, so its to_phase records the phase that was ACTUALLY
                # reached and stays null if archival/persistence/approval raises.
                # _restore_all is idempotent (also runs in the finally below).
                _restore_all(protected)
                _archive_artifacts(ctx)
                # Archival already removed the ADW artifacts from the checkout;
                # drop them from the restore set so the finally's safety-net
                # restore does not recreate them (it keeps handling config.yaml).
                for _name in (*ARTIFACTS, ISSUE_FILE, *SUMMARIES):
                    protected.pop(ctx.repo / ".adw" / _name, None)
                ctx.state.phase = "awaiting_approval"
                ctx.save()  # Phase + geleerter Authoring-Checkpoint in EINEM Save
                if skip or ctx.state.approval_granted:
                    ctx.state.phase = "build"
                    ctx.save()
                # Only NOW, after the transition succeeded, is the resulting
                # phase known.
                _plan_phase.end_payload["to_phase"] = ctx.state.phase
                if not (skip or ctx.state.approval_granted):
                    raise AwaitingApproval(ctx.state.run_id)
    finally:
        _restore_all(protected)
    # Spec-gate pause path: the plan span never opened. Archive the generated
    # artifacts (issue.md, spec.md) so they do not sit dirty in the checkout
    # during the approval pause, then signal the pause.
    if paused_for_spec_approval:
        _archive_artifacts(ctx)
        raise AwaitingApproval(ctx.state.run_id)
    # Non-paused path: archival + transition already happened inside the plan
    # span (build path falls through here; awaiting_approval raised above).
    return


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


# --- Draft-Stage: zwei unabhängige Entwürfe je Authoring-Phase ---------------

DRAFTS_DIR = "drafts"


@dataclass(frozen=True)
class DraftSet:
    """Draft paths of one authoring phase, relative to the repo root.

    ``codex`` is empty when the Codex draft failed — the synthesis then works
    from a single source. The Claude draft never degrades: it escalates."""

    claude: tuple[str, ...]
    codex: tuple[str, ...]


def _draft_name(artifact: str, author: str) -> str:
    """spec.md + claude -> spec.claude.md"""
    path = Path(artifact)
    return f"{path.stem}.{author}{path.suffix}"


def _write_draft(target: Path, data: bytes) -> None:
    """Publish a draft atomically (tmp + rename).

    A crash mid-write must never leave a truncated draft behind — the resume
    would take it for a finished one and skip its author."""
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _draft_present(path: Path) -> bool:
    """A draft counts only with content — an empty file is no synthesis source."""
    return path.is_file() and bool(path.read_bytes().strip())


def _draft_stage(
    ctx: RunContext,
    kind: str,
    agent_name: str,
    task: str,
    artifacts: tuple[str, ...],
    protected: dict[Path, bytes | None] | None = None,
    summary: str | None = None,
) -> DraftSet:
    """Two independent drafts for one authoring phase, written in PARALLEL.

    Claude (agent, writes into the checkout, the orchestrator copies the result
    into the run folder) and Codex (read-only, returns the content) run at the
    same time. Idempotent over FILES, not over state: an existing draft skips
    its author, a `<kind>.codex.FAILED` marker skips the failed Codex attempt
    for good.

    ``summary`` is not drafted — it is only cleaned out of the checkout with
    the artifacts, so no stale summary can pose as the one of this run."""
    drafts = ctx.run_dir / DRAFTS_DIR
    drafts.mkdir(parents=True, exist_ok=True)
    claude_paths = [drafts / _draft_name(name, "claude") for name in artifacts]
    codex_paths = [drafts / _draft_name(name, "codex") for name in artifacts]
    marker = drafts / f"{kind}.codex.FAILED"
    # Crash-Reste dieser Phase im Hauptthread wegräumen, BEVOR ein Autor läuft:
    # ein Rest darf weder als frischer Entwurf durchgehen noch dem parallel
    # lesenden Codex als Zwischenstand erscheinen. Die Summary gehört dazu —
    # sonst ginge eine fremde/alte Zusammenfassung als die dieses Runs durch.
    # AUSNAHME: Ist der Authoring-Loop bereits gecheckpointet, stammen die
    # Dateien im Checkout von der Synthese — das ist die reviewte
    # Zwischenfassung, an der die Fix-Runde weiterarbeitet.
    if ctx.state.authoring_session is None:
        for name in (*artifacts, summary) if summary else artifacts:
            _reset_checkout_artifact(ctx, name)
    prior = {name: _artifact_bytes(ctx, name) for name in artifacts}
    # Vollständigkeit je Autor: ein Crash zwischen zwei Draft-Writes (plan.md
    # geschrieben, contract.yaml nicht) lässt den Autor erneut laufen.
    need_claude = not all(_draft_present(path) for path in claude_paths)
    need_codex = not all(_draft_present(path) for path in codex_paths) and not marker.is_file()
    # Captured on the MAIN thread (inside the phase span): the draft authors run
    # in pool workers whose span stack is empty, so any point they emit (log on
    # Codex degradation, escalation on a missing Claude draft) must be given
    # this enclosing span explicitly to stay non-null (GUI-SPEC §4.2).
    span_id = _current_span_id(ctx.emitter)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codex_future = (
            pool.submit(_codex_draft, ctx, kind, task, artifacts, codex_paths, marker, span_id)
            if need_codex
            else None
        )
        claude_future = (
            pool.submit(
                _claude_draft,
                ctx, agent_name, task, artifacts, claude_paths, prior, protected, span_id,
            )
            if need_claude
            else None
        )
        if codex_future:
            codex_future.result()  # degradiert selbst, wirft nur Unerwartetes
        if claude_future:
            claude_future.result()  # Eskalation propagieren
    # Draft artifacts individually (B4). Emitted HERE on the main thread — the
    # authors ran in pool workers whose span stack is empty, so an emit inside
    # them would carry a null span.
    for path in (*claude_paths, *codex_paths):
        if _draft_present(path):
            _emit_artifact(ctx, path)
    return DraftSet(
        claude=tuple(_repo_relative(ctx, path) for path in claude_paths),
        # Fehlender Codex-Entwurf (degradiert oder Marker aus einem Vorlauf):
        # die Synthese darf keinen Pfad genannt bekommen, den es nicht gibt.
        codex=(
            tuple(_repo_relative(ctx, path) for path in codex_paths)
            if all(_draft_present(path) for path in codex_paths)
            else ()
        ),
    )


def _claude_draft(
    ctx: RunContext,
    agent_name: str,
    task: str,
    artifacts: tuple[str, ...],
    targets: list[Path],
    prior: dict[str, bytes | None],
    protected: dict[Path, bytes | None] | None,
    span_id: str | None = None,
) -> None:
    """Claude draft: the agent writes into the checkout, the orchestrator copies
    the result into the run folder and cleans the checkout again.

    ``prior`` is the checkout snapshot taken by the caller BEFORE both authors
    started — an idle agent run must not pass off leftovers as a draft."""
    _agent_run(ctx, REGISTRY[agent_name], task, cwd=ctx.repo)
    if protected:
        _restore_all(protected)
    # Leer wie fehlend behandeln: ein weißraum-Artefakt ist keine Entwurfsquelle,
    # die Synthese hätte daraus nichts zu mergen.
    missing = [name for name in artifacts if not (_artifact_bytes(ctx, name) or b"").strip()]
    if missing:
        raise escalate(
            ctx,
            f"{agent_name} hat {', '.join(f'.adw/{m}' for m in missing)} nicht "
            "erzeugt (oder leer gelassen) — ohne Entwurf gibt es nichts zu synthetisieren",
            span_id=span_id,
        )
    # Getrackter Altbestand (gemergter Vorlauf) überlebt das Aufräumen — er darf
    # einen untätigen Agent-Lauf nicht als Entwurf adeln.
    unchanged = [
        name
        for name in artifacts
        if prior[name] is not None and _artifact_bytes(ctx, name) == prior[name]
    ]
    if unchanged:
        raise escalate(
            ctx,
            f"{agent_name} hat {', '.join(f'.adw/{u}' for u in unchanged)} nicht "
            "verändert — Altbestand eines früheren Runs würde sonst als Entwurf "
            "durchgehen",
            span_id=span_id,
        )
    for name, target in zip(artifacts, targets, strict=True):
        _write_draft(target, (ctx.repo / ".adw" / name).read_bytes())
    # Der Entwurf lebt ab jetzt im Run-Ordner; die Synthese schreibt das
    # Artefakt selbst — der Checkout darf keinen Entwurf behalten.
    for name in artifacts:
        _reset_checkout_artifact(ctx, name)


def _codex_draft(
    ctx: RunContext,
    kind: str,
    task: str,
    artifacts: tuple[str, ...],
    targets: list[Path],
    marker: Path,
    span_id: str | None = None,
) -> None:
    """Codex draft — degrades instead of escalating.

    Codex is the SECOND opinion: without it the synthesis still has the Claude
    draft. A failure is therefore logged and marked; the marker keeps a resume
    from burning another Codex run on the same broken input."""
    try:
        # codex.author span at the call site (GUI-SPEC §4.4, E4): the argv is
        # built (with the per-call marker_id) and complete BEFORE the span opens;
        # the runner fills artifacts/raw_stdout/parse_ok into the handle. Opened
        # in this pool worker, its span stack is empty, so parent stays null (the
        # documented orphan case, repaired on read by the model).
        marker_id = secrets.token_hex(8)
        author_argv = ctx.codex.effective_author_argv(kind, task, ctx.repo, marker_id)
        start_payload = {
            "kind": kind, "argv": author_argv, "cwd": str(ctx.repo), "task": task,
        }
        with ctx.emitter.span("codex.author", start_payload) as handle:
            # Deterministic defaults so a failed/empty draft still writes a
            # complete end record (the runner overwrites them on success).
            handle.end_payload = {"artifacts": [], "raw_stdout": "", "parse_ok": False}
            files = ctx.codex.author(
                kind, task, cwd=ctx.repo, marker_id=marker_id,
                emitter=ctx.emitter, span=handle,
            )
            empty = [name for name in artifacts if not files.get(name, "").strip()]
            if empty:
                raise CodexAuthorError(f"Codex-Entwurf ohne Inhalt für {', '.join(empty)}")
    except CodexError as exc:
        marker.write_text(
            f"# Codex-Entwurf ({kind}) fehlgeschlagen — Run {ctx.state.run_id}\n\n{exc}\n",
            encoding="utf-8",
        )
        _log_warning(
            ctx,
            f"Codex-Entwurf ({kind}) fehlgeschlagen — die Synthese arbeitet "
            f"einquellig: {exc}",
            span_id=span_id,
        )
        return
    for name, target in zip(artifacts, targets, strict=True):
        _write_draft(target, files[name].encode())


def _spec_draft_task(ctx: RunContext) -> str:
    return (
        f"Create the specification for this issue following the fixed template "
        f"(goal, scope, non-goals, acceptance criteria, Definition of Done) as "
        f".adw/spec.md.\n\nIssue:\n{ctx.state.issue}"
    )


def _plan_draft_task(ctx: RunContext) -> str:
    lanes = ", ".join(_active_lanes(ctx))
    return (
        f"From .adw/spec.md, create the implementation plan .adw/plan.md with "
        f"the Workstreams ({lanes}) and the interface contract .adw/contract.yaml."
    )


def _synthesis_task(drafts: DraftSet, artifacts: tuple[str, ...], summary: str, goal: str) -> str:
    """Task of the synthesis agent — the FIRST run of the authoring loop.

    It merges both drafts into the best-of artifact and writes the summary the
    human reads at the approval gate."""
    targets = ", ".join(f".adw/{name}" for name in artifacts)
    lines = [
        goal,
        f"Read .adw/{ISSUE_FILE} and the independent drafts listed below, then "
        f"write the best-of merge to {targets} and the summary .adw/{summary}.",
        f"Claude draft: {', '.join(drafts.claude)}",
    ]
    if drafts.codex:
        lines.append(f"Codex draft: {', '.join(drafts.codex)}")
    else:
        lines.append(
            "There is no Codex draft — its author failed. Work from the Claude "
            "draft alone and state that single-source basis in the summary."
        )
    return "\n\n".join(lines)


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


def _missing_loop_artifacts(ctx: RunContext, loop_artifacts: tuple[str, ...]) -> list[str]:
    """Required artifacts that are absent or empty (whitespace counts as empty)."""
    return [name for name in loop_artifacts if not (_artifact_bytes(ctx, name) or b"").strip()]


def _reviewed_authoring_loop(
    ctx: RunContext,
    agent_name: str,
    initial_task: str,
    review_kind: str,
    artifacts: tuple[str, ...],
    review_refs: tuple[str, ...] | None = None,
    protected: dict[Path, bytes | None] | None = None,
    summary: str | None = None,
) -> None:
    """Agent writes artifact(s), Codex reviews, Findings go back to the SAME
    session — until the verdict is ok. Circuit-Breaker on identical Findings.
    ``protected`` is restored after EVERY agent run — even the reviewer
    never sees a protected file rewritten by the agent.

    ``summary`` is the phase summary: it must exist like an artifact and the fix
    tasks keep it current, but it is never a Codex review reference."""
    spec = REGISTRY[agent_name]
    review_targets = review_refs or artifacts
    # Die Summary muss wie ein Artefakt entstehen; für Fix-Task und Review
    # bleibt sie getrennt (Codex hat sie nicht reviewt).
    loop_artifacts = (*artifacts, summary) if summary else artifacts
    # Resume mitten im Authoring: Session, offener Fix-Task und Findings-Basis
    # aus dem State — sonst startet der Loop kontextlos neu und der
    # Prior-Content-Check würde das vorhandene Artefakt fälschlich eskalieren.
    resuming = ctx.state.authoring_session is not None
    session: str | None = ctx.state.authoring_session
    task = ctx.state.authoring_pending_task or initial_task
    previous_failures: list[str] | None = ctx.state.authoring_last_findings or None
    # Runden-Zähler aus dem State (überlebt Crash+Resume): eine Runde =
    # ein Agent-Lauf + ein Codex-Review, das mit Verdict ≠ ok endet.
    rounds = ctx.state.authoring_rounds
    # Findings-Verlauf der Vorrunden inkl. Disposition (Review-Policy v2).
    prior_context = list(ctx.state.authoring_prior_context)
    # Prior-Snapshot: Altbestand (z. B. gemergte Artefakte früherer Runs) darf
    # einen untätigen Agenten nicht adeln — das Artefakt muss sich ÄNDERN.
    prior: dict[str, bytes | None] = {}
    for name in loop_artifacts:
        path = ctx.repo / ".adw" / name
        prior[name] = path.read_bytes() if path.is_file() else None
    first_iteration = not resuming
    # Report der Known Limitations: nur der Cap-Accept-Pfad schreibt ihn; jeder
    # andere terminale Ausgang (ok, Cap-Eskalation) entfernt einen etwaigen
    # STALE Report — z. B. ein Crash nach Cap-Accept, dessen Resume erneut die
    # letzte Runde fährt und "ok" bzw. andere Findings erhält.
    known_findings_path = ctx.run_dir / f"authoring-{review_kind}-known-findings.md"
    while True:
        with ctx.emitter.span(
            "round", {"loop": "authoring", "n": rounds + 1, "cap": AUTHORING_MAX_ROUNDS}
        ) as _round:
            _round.end_payload = {"outcome": "aborted"}
            result = _agent_run(
                ctx, spec, task, cwd=ctx.repo, resume=session,
                on_session_id=lambda sid: _checkpoint_authoring_session(ctx, sid),
            )
            session = result.session_id or session
            with ctx.state_lock:
                ctx.state.authoring_session = session
                ctx.save()
            if protected:
                _restore_all(protected)
            # Leer zählt wie fehlend: Die Summary reviewt niemand, eine weiße
            # Summary fiele sonst erst dem Menschen am Freigabe-Gate auf. Fehlt ein
            # Pflicht-Artefakt, wird GENAU EINMAL derselbe Schritt über die
            # vorhandene Session repariert (kein Review-Zyklus, keine Runde, E4).
            missing = _missing_loop_artifacts(ctx, loop_artifacts)
            if missing:
                repair_task = (
                    f"The synthesis run did not produce the required artifact(s) "
                    f"(missing or empty): {', '.join(f'.adw/{m}' for m in missing)}. "
                    f"Write exactly these files now over the same session and leave "
                    f"the already correct files unchanged."
                )
                result = _agent_run(
                    ctx, spec, repair_task, cwd=ctx.repo, resume=session,
                    on_session_id=lambda sid: _checkpoint_authoring_session(ctx, sid),
                )
                session = result.session_id or session
                with ctx.state_lock:
                    ctx.state.authoring_session = session
                    ctx.save()
                if protected:
                    _restore_all(protected)
                missing = _missing_loop_artifacts(ctx, loop_artifacts)
            if missing:
                raise escalate(
                    ctx,
                    f"{agent_name} hat {', '.join(f'.adw/{m}' for m in missing)} "
                    "nicht erzeugt (oder leer gelassen) — Lauf abgebrochen statt mit "
                    "leerem Artefakt weiterzumachen",
                )
            if first_iteration:
                # Auch die Summary muss frisch sein: Reste eines fremden oder
                # gecrashten Laufs hat die Draft-Stage vorher entfernt, ein
                # unveränderter Altbestand wäre also eine untergeschobene
                # Zusammenfassung — und die reviewt niemand.
                unchanged = [
                    name
                    for name in loop_artifacts
                    if prior[name] is not None
                    and (ctx.repo / ".adw" / name).read_bytes() == prior[name]
                ]
                if unchanged:
                    raise escalate(
                        ctx,
                        f"{agent_name} hat {', '.join(f'.adw/{u}' for u in unchanged)} "
                        "nicht verändert — Altbestand eines früheren Runs würde sonst "
                        "als neues Artefakt durchgehen",
                    )
                first_iteration = False
            round_no = rounds + 1
            try:
                review = _codex_review(
                    ctx,
                    review_kind,
                    [f".adw/{name}" for name in review_targets],
                    cwd=ctx.repo,
                    context=_severity_context(round_no, AUTHORING_MAX_ROUNDS, prior_context),
                )
            except (FindingsParseError, ValidationError) as exc:
                raise escalate(ctx, f"Codex-{review_kind}-Review unlesbar: {exc}") from exc
            _round.end_payload["outcome"] = review.verdict
            if review.verdict == "ok":
                known_findings_path.unlink(missing_ok=True)  # kein stale Report
                # Bewusst KEIN Save hier: Das Leeren des Authoring-Checkpoints
                # passiert atomar mit dem Phasenübergang beim Aufrufer — sonst
                # gäbe es ein Crash-Fenster "Phase alt, Checkpoint weg".
                _clear_authoring_checkpoint(ctx)
                return
            # Eine Runde (Agent-Lauf + Review) endete mit Verdict ≠ ok.
            rounds += 1
            if rounds >= AUTHORING_MAX_ROUNDS:
                if any(f.severity == "P1" for f in review.findings):
                    known_findings_path.unlink(missing_ok=True)  # kein stale Report
                    raise escalate(
                        ctx,
                        f"Authoring-{review_kind}: Runden-Cap ({AUTHORING_MAX_ROUNDS}) "
                        f"erreicht, P1-Findings weiterhin offen — Eskalation an den "
                        f"Menschen.\n\n{_findings_text(review)}",
                    )
                # Nur P2/P3 offen: Artefakt akzeptieren, verbleibende Findings als
                # Known Limitations dokumentieren und wie ein ok-Verdict beenden.
                known_path = _write_known_findings(
                    ctx,
                    review_kind,
                    review,
                    f"Runden-Cap ({AUTHORING_MAX_ROUNDS}) erreicht, nur P2/P3 offen — "
                    "Artefakt akzeptiert.",
                )
                _log_warning(
                    ctx,
                    f"Authoring-{review_kind}: Runden-Cap ({AUTHORING_MAX_ROUNDS}) erreicht, "
                    f"nur P2/P3 offen — Artefakt akzeptiert, Known Limitations unter {known_path}",
                )
                _clear_authoring_checkpoint(ctx)
                return
            actionable, dropped = _split_by_severity(review.findings, round_no)
            if not actionable:
                # Alle Findings liegen unter der Schwelle dieser Runde: wie am Cap
                # das Artefakt akzeptieren und die Findings dokumentieren.
                severities = "/".join(sorted(_actionable_severities(round_no)))
                known_path = _write_known_findings(
                    ctx,
                    review_kind,
                    review,
                    f"Runde {round_no}: alle Findings unter der Severity-Schwelle "
                    f"({severities}) — Artefakt akzeptiert.",
                )
                _log_warning(
                    ctx,
                    f"Authoring-{review_kind}: Runde {round_no}, alle Findings unter der "
                    f"Severity-Schwelle ({severities}) — Artefakt akzeptiert, Known "
                    f"Limitations unter {known_path}",
                )
                _clear_authoring_checkpoint(ctx)
                return
            if dropped:
                _write_followups(ctx, dropped)
            review = ReviewResult(verdict="needs_fixes", findings=actionable)
            failures = _finding_keys(review)
            try:
                check_progress(previous_failures, failures)
            except NoProgressError as exc:
                raise escalate(
                    ctx,
                    f"Codex-{review_kind}-Review meldet unverändert dieselben Findings — "
                    f"Circuit-Breaker.\n\n{_findings_text(review)}",
                ) from exc
            previous_failures = failures
            prior_context = _extend_context(
                prior_context,
                [
                    _disposition_line(item, _below_threshold_disposition(round_no))
                    for item in dropped
                ]
                + [
                    _disposition_line(item, _dispatched_disposition(round_no))
                    for item in actionable
                ],
            )
            task = (
                f"The Codex review of {', '.join(f'.adw/{a}' for a in artifacts)} has "
                f"Findings. Incorporate them and update the artifacts:\n\n"
                f"{_findings_text(review)}"
            )
            if summary:
                task += (
                    f"\n\nKeep .adw/{summary} current: when the fixes change the "
                    f"picture, update the summary accordingly."
                )
            with ctx.state_lock:
                # Checkpoint des Fix-Zyklus — überlebt einen Crash vor dem Fix-Lauf.
                # authoring_rounds und der Findings-Verlauf gehen im SELBEN Save wie
                # der Fix-Task raus.
                ctx.state.authoring_pending_task = task
                ctx.state.authoring_last_findings = failures
                ctx.state.authoring_rounds = rounds
                ctx.state.authoring_prior_context = prior_context
                ctx.save()


def _write_issue(ctx: RunContext) -> bytes:
    """Write the original issue text as .adw/issue.md so Codex can review the
    spec/plan against the actual request (proportionality). Idempotent: it is
    regenerated from the state, so a resume in any authoring phase restores it.

    The path belongs to the ADW (it is derived deterministically from the run
    state). A pre-existing file with FOREIGN content is therefore never
    overwritten — it would later be deleted/reset by the archival, silently
    losing user data — but escalated instead. Our own regenerated content
    (identical bytes, e.g. after a crash+resume) is a no-op overwrite."""
    path = ctx.repo / ".adw" / ISSUE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        f"# Issue (ADW-generiert aus dem Run-State — nicht manuell editieren)\n\n"
        f"{ctx.state.issue}\n"
    ).encode()
    if path.is_symlink():
        # Ein Symlink an diesem Pfad ist NIE unser Artefakt (wir schreiben immer
        # eine reguläre Datei) — also fremd. Nicht folgen (Referenz-Korruption)
        # und nicht still entfernen (die Archivierung würde den Ersatz löschen
        # und den Symlink verlieren), sondern eskalieren.
        raise escalate(
            ctx,
            f".adw/{ISSUE_FILE} ist ein Symlink — dieser Pfad gehört dem ADW (wird "
            f"aus dem Issue-Text generiert); bitte entfernen oder umbenennen",
        )
    elif path.is_dir():
        raise escalate(
            ctx,
            f".adw/{ISSUE_FILE} ist ein Verzeichnis — dieser Pfad gehört dem ADW "
            f"(wird aus dem Issue-Text generiert); bitte entfernen",
        )
    elif path.is_file() and path.read_bytes() != data:
        raise escalate(
            ctx,
            f".adw/{ISSUE_FILE} existiert bereits mit fremdem Inhalt — dieser Pfad "
            f"gehört dem ADW (wird aus dem Issue-Text generiert); bitte entfernen "
            f"oder umbenennen, der ADW würde ihn sonst überschreiben und verwerfen",
        )
    path.write_bytes(data)
    return data


def _clear_authoring_checkpoint(ctx: RunContext) -> None:
    """Clear in-memory only — persisting happens atomically with the phase transition."""
    ctx.state.authoring_session = None
    ctx.state.authoring_pending_task = None
    ctx.state.authoring_last_findings = []
    ctx.state.authoring_rounds = 0
    ctx.state.authoring_prior_context = []


def _write_known_findings(
    ctx: RunContext, review_kind: str, review: ReviewResult, reason: str
) -> Path:
    """Persist the remaining findings as Known Limitations when the authoring
    loop accepts the artifact anyway (round cap or severity threshold) — the
    open findings stay documented."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.run_dir / f"authoring-{review_kind}-known-findings.md"
    path.write_text(
        f"# Known Limitations — Authoring-{review_kind} (Run {ctx.state.run_id})\n\n"
        f"{reason}\n\n"
        f"{_findings_text(review)}\n",
        encoding="utf-8",
    )
    return path


def _finding_key(item: Finding) -> str:
    return f"{item.file}|{item.issue}"


def _finding_keys(review: ReviewResult) -> list[str]:
    return sorted(_finding_key(f) for f in review.findings)


def _circuit_breaker_keys(ctx: RunContext, findings: list[Finding]) -> list[str]:
    """Baseline for the Circuit-Breaker AFTER a dispatch: deferred Findings
    triggered no fix, so their unchanged reappearance is not a standstill."""
    return sorted(_finding_key(f) for f in _without_deferred(ctx, findings))


def _without_deferred(ctx: RunContext, findings: list[Finding]) -> list[Finding]:
    """Drop Findings that a fix run has already proven unactionable.

    They are in the follow-up report; dispatching them again would burn a fix
    cycle without effect and trip the Circuit-Breaker on the next round.
    Deferred is the P3 ASSESSMENT, not the location: if a later review rates
    the same problem P1/P2, it goes through the fix cycle after all."""
    deferred = set(ctx.state.deferred_findings)
    return [
        item for item in findings if item.severity != "P3" or _finding_key(item) not in deferred
    ]


def _actionable_severities(round_no: int) -> set[str]:
    """Descending severity floor per review round (Review-Policy v2).

    Round 1 takes everything, from round 2 P3 drops out, from round 3 P2 as
    well. Without this floor the loop never converges: a deterministic
    reviewer keeps finding new edge cases at the lowest severity."""
    if round_no <= 1:
        return {"P1", "P2", "P3"}
    if round_no == 2:
        return {"P1", "P2"}
    return {"P1"}


def _split_by_severity(
    findings: list[Finding], round_no: int
) -> tuple[list[Finding], list[Finding]]:
    """(actionable, dropped) for this round's severity floor."""
    allowed = _actionable_severities(round_no)
    actionable = [item for item in findings if item.severity in allowed]
    dropped = [item for item in findings if item.severity not in allowed]
    return actionable, dropped


def _disposition_line(item: Finding, disposition: str) -> str:
    return f"- [{item.severity}] {item.file}: {item.issue} — disposition: {disposition}"


def _dispatched_disposition(round_no: int) -> str:
    return f"fix dispatched (round {round_no})"


def _below_threshold_disposition(round_no: int) -> str:
    return f"not adopted, below severity threshold (round {round_no}); recorded as follow-up"


_DEFERRED_DISPOSITION = "rejected by a fix run as unactionable; recorded as follow-up"


def _severity_context(round_no: int, cap: int, prior: list[str]) -> str | None:
    """Review context from round 2 on: severity floor of THIS round plus the
    findings of previous rounds with their dispositions.

    None without prior findings — round 1 acts on everything, so the prompt
    stays exactly as it was before the policy."""
    if not prior:
        return None
    severities = "/".join(sorted(_actionable_severities(round_no)))
    lines = "\n".join(prior)
    return (
        f"This is review round {round_no} of max {cap}. Only findings of severity "
        f"{severities} will be acted on in this round; anything below is recorded "
        f"as a follow-up, not fixed.\n"
        f"Findings from previous rounds and their dispositions:\n{lines}\n"
        f"Do NOT report these findings again unless the problem demonstrably "
        f"persists in the current version."
    )


def _extend_context(prior: list[str], lines: list[str]) -> list[str]:
    """Append new disposition lines without duplicates — a deterministic
    reviewer reports the same finding again in every round."""
    merged = list(prior)
    merged.extend(line for line in lines if line not in merged)
    return merged


def _findings_text(review: ReviewResult) -> str:
    lines = []
    for item in review.findings:
        plan = "; ".join(item.remediation_plan)
        lines.append(f"- [{item.severity}] {item.file}: {item.issue} (Recommendation: {plan})")
    return "\n".join(lines)


def _archive_artifacts(ctx: RunContext, overwrite_existing_archive: bool = True) -> None:
    """Archive spec/plan/contract into the run folder; the main repo stays clean.

    Tracked artifacts (merged earlier ADW run) are reset via git to the
    checked-in state instead of deleted — otherwise a
    tracked deletion would remain in the checkout. Untracked artifacts are removed.

    ``overwrite_existing_archive=False`` is the idempotent CLEANUP mode (spec-gate
    resume): an already-archived artifact is NOT re-copied from the checkout —
    the checkout copy of a tracked artifact was reset to the OLD merged version
    by an earlier archival and would clobber the reviewed archive. The stray
    checkout file is still cleaned up."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    # issue.md und die Summaries werden mitarchiviert — nicht in ARTIFACTS, weil
    # keine Build-Lane dagegen baut; hier aber wie die anderen aufgeräumt.
    for name in (*ARTIFACTS, ISSUE_FILE, *SUMMARIES):
        source = ctx.repo / ".adw" / name
        if not source.is_file():
            continue
        if overwrite_existing_archive or not (ctx.run_dir / name).is_file():
            shutil.copy2(source, ctx.run_dir / name)
            # AFTER the artifact was successfully written into the run folder.
            _emit_artifact(ctx, ctx.run_dir / name)
        _reset_checkout_artifact(ctx, name)


# --- Phase 3: Build-Lanes ---------------------------------------------------


@_phase_span("build")
def run_build_phase(ctx: RunContext) -> None:
    """Phase 3: build agent(s) per Lane in isolated Worktrees, Gate loop until
    green (max. 10 iterations, Circuit-Breaker), commit by the orchestrator."""
    if ctx.state.phase != "build":
        return
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
            branch=f"adw/{ctx.state.run_id}/{lane}",
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
                    span=_current_span_id(ctx.emitter),
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
            try:
                check_progress(previous_failures, failures)
            except NoProgressError as exc:
                ctx.emitter.emit(
                    "circuit_breaker",
                    {"keys": failures, "scope": f"lane:{lane}"},
                    span=_current_span_id(ctx.emitter),
                )
                raise escalate(
                    ctx,
                    f"Lane {lane}: Gate-Fix-Iteration hat nichts verändert — "
                    f"Circuit-Breaker.\n\n{_gate_failure_text(report)}",
                ) from exc
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
        span=_current_span_id(ctx.emitter),
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
    result = subprocess.run(
        ["git", "-C", str(worktree), "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        timeout=120,
        env=safe_env(ctx.git_env),
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
            result = subprocess.run(
                ["git", "-C", str(worktree), "-c", "core.hooksPath=/dev/null", *args],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
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
        span=_current_span_id(ctx.emitter),
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
    restored = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            base_ref,
            "--",
            ".adw/config.yaml",
        ],
        capture_output=True,
        timeout=120,
        env=safe_env(ctx.git_env),
    )
    if restored.returncode != 0:
        # Nur der POSITIV verifizierte Nicht-im-Base-Branch-Fall rechtfertigt
        # das Löschen: ls-tree rc==0 mit LEEREM Output. rc!=0 (kaputter Ref,
        # Repo-Fehler) oder nicht-leerer Output → eskalieren, nichts löschen.
        on_base = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "core.hooksPath=/dev/null",
                "ls-tree",
                base_ref,
                "--",
                ".adw/config.yaml",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=safe_env(ctx.git_env),
        )
        if on_base.returncode != 0 or on_base.stdout.strip():
            raise escalate(
                ctx,
                f"Config-Restore im Worktree fehlgeschlagen: "
                f"{restored.stderr.decode(errors='replace').strip()} / "
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


def _git(ctx: RunContext, cwd: Path, *args: str) -> str:
    """Git through the orchestrator — env whitelist, repo hooks disabled."""
    result = subprocess.run(
        ["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=safe_env(ctx.git_env),
    )
    if result.returncode != 0:
        raise escalate(
            ctx, f"git {' '.join(args)} in {cwd} fehlgeschlagen: {result.stderr.strip()}"
        )
    return result.stdout


# --- Phase 4: Integration + E2E (nur --parallel) -----------------------------

MAX_E2E_ROUNDS = 10
INTEGRATION_LANE = "integration"


@_phase_span("integration")
def run_integration_phase(ctx: RunContext) -> None:
    """Phase 4: merge Lane branches onto an integration branch, run the E2E
    Gate; on red the E2E agent triages the failures back into the Lanes.
    Max. 10 rounds, Circuit-Breaker on identical E2E output."""
    if ctx.state.phase != "integration":
        return
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
            try:
                check_progress(previous, failures)
            except NoProgressError as exc:
                ctx.emitter.emit(
                    "circuit_breaker",
                    {"keys": failures, "scope": "integration"},
                    span=_current_span_id(ctx.emitter),
                )
                raise escalate(
                    ctx,
                    f"E2E-Fix-Runde hat nichts verändert — Circuit-Breaker.\n\n"
                    f"{_gate_failure_text(report)}",
                ) from exc
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
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(worktree),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "merge",
                    "--no-edit",
                    branch,
                ],
                capture_output=True,
                text=True,
                timeout=300,
                env=safe_env(ctx.git_env),
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
            span=_current_span_id(ctx.emitter),
        )
    return worktree


def _abort_merge(ctx: RunContext, worktree: Path) -> None:
    """Best effort: do not leave a half-merged state behind."""
    try:
        subprocess.run(
            ["git", "-C", str(worktree), "-c", "core.hooksPath=/dev/null", "merge", "--abort"],
            capture_output=True,
            timeout=120,
            env=safe_env(ctx.git_env),
        )
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
        span_id=_current_span_id(ctx.emitter),
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


# --- Phase 5+6: Codex-Code-Review, finaler Review + Triage --------------------

# Runden-Cap der Codex-Code-Review-Phase. Zusammen mit der absteigenden
# Severity-Schwelle (_actionable_severities) konvergiert der Loop: ab Runde 3
# blockt nur noch P1, alles andere wird als Follow-up dokumentiert.
MAX_REVIEW_ROUNDS = 5


def _resume_pending_lanes(ctx: RunContext, lanes: list[str]) -> None:
    """Before merge/review, send EVERY Lane through _run_lane: unfinished Lanes
    (crash after fix Dispatch) catch up on Gates + commit; finished Lanes go
    through the tree-hash revalidation and, on manipulation, return to the
    loop instead of feeding ungated changes into the review."""
    for lane in lanes:
        if ctx.state.lanes.get(lane) is not None:
            _run_lane(ctx, lane, lanes)


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
    if ctx.state.phase != "codex_review":
        return
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
            try:
                check_progress(previous, failures)
            except NoProgressError as exc:
                ctx.emitter.emit(
                    "circuit_breaker",
                    {"keys": failures, "scope": "codex_review"},
                    span=_current_span_id(ctx.emitter),
                )
                raise escalate(
                    ctx,
                    f"Codex-Code-Review meldet unverändert dieselben Findings — "
                    f"Circuit-Breaker.\n\n{_findings_text(review)}",
                ) from exc
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
    if ctx.state.phase != "final_review":
        return
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
                span_id=_current_span_id(ctx.emitter),
            )
            if decision.followups:
                _write_followups(ctx, decision.followups)
            if not decision.fix_tasks:
                break  # nur scope_gaps: Report statt Auto-Restart (SPEC §4 Phase 6)
            fixable = [f for f in review.findings if f.category != "scope_gap"]
            failures = _finding_keys(ReviewResult(verdict="needs_fixes", findings=fixable))
            try:
                check_progress(previous, failures)
            except NoProgressError as exc:
                ctx.emitter.emit(
                    "circuit_breaker",
                    {"keys": failures, "scope": "final_review"},
                    span=_current_span_id(ctx.emitter),
                )
                raise escalate(
                    ctx,
                    f"Finaler Review meldet unverändert dieselben Findings — "
                    f"Circuit-Breaker.\n\n{_findings_text(review)}",
                ) from exc

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
                            span=_current_span_id(ctx.emitter),
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
    if ctx.state.phase != "ci":
        return
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
        if not ctx.dry_run:
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

        def _bump_ci_reentries(fix_lanes: list[str]) -> None:
            # Läuft im Dispatch unter dem State-Lock und wird mit dem Staging
            # in EINEM Save persistiert — ein Crash kann das Re-Entry-Budget
            # nicht verbrennen, ohne dass der Fix beim Resume nachgeholt wird.
            ctx.state.ci_reentries += 1

        _dispatch_lane_fixes(
            ctx, review.findings, lanes, source="CI", mutate_staged=_bump_ci_reentries
        )
        # AFTER the fixes were actually dispatched — the run re-enters the loop.
        ctx.emitter.emit(
            "ci.reentry",
            {"n": ctx.state.ci_reentries, "reason": "pipeline red"},
            span=_current_span_id(ctx.emitter),
        )
    with ctx.state_lock:
        ctx.state.phase = "done"
        ctx.save()


def _ci_forge(ctx: RunContext) -> Forge:
    """GitLab or GitHub? The config override wins, otherwise origin detection.

    In dry-run an unrecognizable host falls back to 'gitlab' — there the
    monitoring is mocked anyway and a throwaway repo often has no
    origin at all. In a real run fail fast applies: no poll against the wrong API."""
    try:
        return detect_forge(ctx.repo, ctx.config.ci.provider)
    except ForgeError as exc:
        if ctx.dry_run:
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
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "core.hooksPath=/dev/null",
                "push",
                "--force-with-lease",
                "-u",
                "origin",
                branch,
            ],
            capture_output=True,
            text=True,
            timeout=600,
            env=safe_env(extra),
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


def _write_followups(ctx: RunContext, followups: list[Finding]) -> None:
    """Collect deferred Findings as a follow-up report (no auto-restart):
    scope gaps and P3s that a fix run could not act on.

    Dedupe via file+issue: a deliberately unfixed scope_gap shows up again in
    every further review round and must not flood the report."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.run_dir / "followups.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    entries = []
    written: list[Finding] = []
    for item in followups:
        if f"{item.file}: {item.issue}" in existing:
            continue
        plan = "; ".join(item.remediation_plan)
        entries.append(f"- [{item.severity}] {item.file}: {item.issue}\n  - Plan: {plan}")
        written.append(item)
    if not entries:
        return
    header = "" if existing else f"# Follow-ups — Run {ctx.state.run_id}\n\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(header + "\n".join(entries) + "\n")
    # AFTER the follow-up was actually recorded (AC 9), one point per new entry.
    span_id = _current_span_id(ctx.emitter)
    for item in written:
        ctx.emitter.emit(
            "followup",
            {"finding_key": f"{item.file}|{item.issue}", "text": item.issue},
            span=span_id,
        )


def _gate_failure_text(report: GateReport) -> str:
    lines = []
    for failure in report.failures:
        status = "Timeout" if failure.timed_out else f"Exit {failure.exit_code}"
        lines.append(f"### Gate {failure.gate} ({status})\n{failure.output}")
    return "\n\n".join(lines)
