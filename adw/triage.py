"""Triage rules, iteration limits and Circuit-Breaker — pure functions, no I/O."""

from dataclasses import dataclass, field

from adw.findings import Finding, ReviewResult
from adw.state import LaneState

MAX_GATE_ITERATIONS = 10
MAX_FIX_CYCLES = 3


class LimitExceededError(Exception):
    """A loop limit is exhausted — escalation to the human."""


class NoProgressError(Exception):
    """Circuit-Breaker: a fix iteration changed NOTHING — escalate
    immediately instead of exhausting the limit."""


@dataclass(frozen=True)
class TriageDecision:
    """Result of the triage after the final review (SPEC §4 phase 6)."""

    followups: list[Finding] = field(default_factory=list)
    fix_tasks: dict[str, list[Finding]] = field(default_factory=dict)


def triage_final_review(
    result: ReviewResult,
    active_lanes: list[str] | None = None,
    emitter=None,
    span_id: str | None = None,
) -> TriageDecision:
    """Scope gaps → follow-up issue; implementation/trivial → fix cycle per Lane.

    Findings without a Lane assignment go to ALL active Lanes — better checked
    twice than silently lost. No Finding is discarded.

    ``emitter``/``span_id`` are additive and optional: one ``triage.decision``
    point is emitted per actually made decision (never speculatively), carrying
    the enclosing span id the caller passes in.
    """
    lanes = active_lanes or ["backend"]
    followups: list[Finding] = []
    fix_tasks: dict[str, list[Finding]] = {}
    for item in result.findings:
        if item.category == "scope_gap":
            followups.append(item)
            action, reason = "followup", "scope_gap → follow-up report"
        else:
            # Inaktive Lane-Zuordnung (z. B. "frontend" bei Single-Lane-Run) wird
            # wie "unknown" behandelt — kein Finding wird verworfen.
            known = item.lane != "unknown" and (active_lanes is None or item.lane in lanes)
            targets = [item.lane] if known else lanes
            for lane in targets:
                fix_tasks.setdefault(lane, []).append(item)
            action, reason = "fix", f"fix dispatched to {', '.join(targets)}"
        if emitter is not None:
            emitter.emit(
                "triage.decision",
                {
                    "finding_key": f"{item.file}|{item.issue}",
                    "severity": item.severity,
                    "action": action,
                    "reason": reason,
                },
                span=span_id,
            )
    return TriageDecision(followups=followups, fix_tasks=fix_tasks)


def check_gate_iterations(lane: LaneState) -> None:
    if lane.gate_iterations >= MAX_GATE_ITERATIONS:
        raise LimitExceededError(
            f"Lane-Gates: {lane.gate_iterations} Iterationen erreicht "
            f"(Limit {MAX_GATE_ITERATIONS}) — Eskalation"
        )


def check_fix_cycles(lane: LaneState) -> None:
    if lane.fix_cycles >= MAX_FIX_CYCLES:
        raise LimitExceededError(
            f"Fix-Zyklen: {lane.fix_cycles} erreicht (Limit {MAX_FIX_CYCLES}) — Eskalation"
        )


def check_progress(previous: list[str] | None, current: list[str]) -> None:
    """Identical failure set as in the previous round = zero progress."""
    if previous is None:
        return
    if sorted(previous) == sorted(current):
        raise NoProgressError(
            "Fix-Iteration hat nichts verändert — Circuit-Breaker, Eskalation statt Limit-Ausreizen"
        )
