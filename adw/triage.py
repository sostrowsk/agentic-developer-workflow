"""Triage rules, iteration limits and Circuit-Breaker — pure functions, no I/O."""

import re
from dataclasses import dataclass, field

from adw.findings import Finding, ReviewResult
from adw.gates import GateReport
from adw.state import LaneState

MAX_GATE_ITERATIONS = 10
MAX_FIX_CYCLES = 3


# Key normalization (W4): only the comparison KEY is normalized, never the text
# handed to agents. Order is fixed (spec Scope 1, rules 1–8).
_WORKTREE_PREFIX = re.compile(r"(?:\S*/)?\.adw/runs/[0-9a-f]{8}/trees/[^/\s]+/")
_KEY_RULES: list[tuple[re.Pattern[str], str]] = [
    (_WORKTREE_PREFIX, ""),
    (re.compile(r"(?:/tmp|/private/var/folders)/\S+"), "<tmp>"),
    (
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"),
        "<ts>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?"), "<ts>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|seconds|min)\b"), "<dur>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<hex>"),
    (re.compile(r"(\.[A-Za-z]\w*):\d+(?::\d+)?"), r"\1:<n>"),
    (re.compile(r"\bline \d+"), "line <n>"),
    (re.compile(r"(?:localhost|127\.0\.0\.1):\d+"), "<host>:<port>"),
    (re.compile(r"\s+"), " "),
]


def normalize_key_text(text: str) -> str:
    """Strip volatile details (paths, durations, addresses, line numbers, …)
    so the same failure yields the same Circuit-Breaker/dedupe key."""
    for pattern, replacement in _KEY_RULES:
        text = pattern.sub(replacement, text)
    return text.strip()


def finding_key(item: Finding) -> str:
    """``file`` gets only the worktree-prefix rule — line numbers there stay distinct."""
    return f"{_WORKTREE_PREFIX.sub('', item.file)}|{normalize_key_text(item.issue)}"


def gate_failure_keys(report: GateReport) -> list[str]:
    return [f"{f.gate}|{f.exit_code}|{normalize_key_text(f.output)}" for f in report.failures]


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
                    "finding_key": finding_key(item),
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
