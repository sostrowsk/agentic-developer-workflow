"""Triage-Regeln, Iterations-Limits und Circuit-Breaker — reine Funktionen, kein I/O."""

from dataclasses import dataclass, field

from adw.findings import Finding, ReviewResult
from adw.state import LaneState

MAX_GATE_ITERATIONS = 10
MAX_FIX_CYCLES = 3


class LimitExceededError(Exception):
    """Ein Loop-Limit ist erschöpft — Eskalation an den Menschen."""


class NoProgressError(Exception):
    """Circuit-Breaker: eine Fix-Iteration hat NICHTS verändert — sofort
    eskalieren statt das Limit auszureizen."""


@dataclass(frozen=True)
class TriageDecision:
    """Ergebnis der Triage nach dem finalen Review (SPEC §4 Phase 6)."""

    followups: list[Finding] = field(default_factory=list)
    fix_tasks: dict[str, list[Finding]] = field(default_factory=dict)


def triage_final_review(
    result: ReviewResult, active_lanes: list[str] | None = None
) -> TriageDecision:
    """Scope-Lücken → Follow-up-Issue; Implementierung/trivial → Fix-Zyklus je Lane.

    Findings ohne Lane-Zuordnung gehen an ALLE aktiven Lanes — lieber doppelt
    geprüft als still verloren. Kein Finding wird verworfen.
    """
    lanes = active_lanes or ["backend"]
    followups: list[Finding] = []
    fix_tasks: dict[str, list[Finding]] = {}
    for item in result.findings:
        if item.category == "scope_gap":
            followups.append(item)
            continue
        targets = lanes if item.lane == "unknown" else [item.lane]
        for lane in targets:
            fix_tasks.setdefault(lane, []).append(item)
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
    """Identische Failure-Menge wie in der Vorrunde = Null-Fortschritt."""
    if previous is None:
        return
    if sorted(previous) == sorted(current):
        raise NoProgressError(
            "Fix-Iteration hat nichts verändert — Circuit-Breaker, Eskalation statt Limit-Ausreizen"
        )
