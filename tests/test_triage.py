import pytest

from adw.findings import Finding, ReviewResult
from adw.state import LaneState
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


def finding(**overrides) -> Finding:
    defaults = dict(
        severity="P2",
        lane="backend",
        file="app/x.py",
        issue="Problem",
        remediation_plan=["fixen"],
        category="implementation",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def lane_state(**overrides) -> LaneState:
    defaults = dict(worktree="wt", branch="br")
    defaults.update(overrides)
    return LaneState(**defaults)


def test_scope_gap_becomes_followup_not_fix_task():
    result = ReviewResult(
        verdict="needs_fixes",
        findings=[finding(category="scope_gap", issue="Fehlender Scope")],
    )
    decision = triage_final_review(result)
    assert [f.issue for f in decision.followups] == ["Fehlender Scope"]
    assert decision.fix_tasks == {}


def test_implementation_findings_route_to_their_lane():
    result = ReviewResult(
        verdict="needs_fixes",
        findings=[
            finding(lane="backend", issue="BE kaputt"),
            finding(lane="frontend", issue="FE kaputt", category="trivial"),
        ],
    )
    decision = triage_final_review(result)
    assert [f.issue for f in decision.fix_tasks["backend"]] == ["BE kaputt"]
    assert [f.issue for f in decision.fix_tasks["frontend"]] == ["FE kaputt"]


def test_unknown_lane_routes_to_all_given_lanes():
    result = ReviewResult(
        verdict="needs_fixes",
        findings=[finding(lane="unknown", issue="Wo auch immer")],
    )
    decision = triage_final_review(result, active_lanes=["backend", "frontend"])
    assert [f.issue for f in decision.fix_tasks["backend"]] == ["Wo auch immer"]
    assert [f.issue for f in decision.fix_tasks["frontend"]] == ["Wo auch immer"]


def test_unknown_lane_single_lane_fallback():
    result = ReviewResult(
        verdict="needs_fixes",
        findings=[finding(lane="unknown")],
    )
    decision = triage_final_review(result, active_lanes=["backend"])
    assert list(decision.fix_tasks) == ["backend"]


def test_finding_without_category_is_treated_as_implementation():
    result = ReviewResult(verdict="needs_fixes", findings=[finding(category=None)])
    decision = triage_final_review(result)
    assert decision.followups == []
    assert list(decision.fix_tasks) == ["backend"]


def test_ok_review_yields_empty_decision():
    decision = triage_final_review(ReviewResult(verdict="ok", findings=[]))
    assert decision.followups == []
    assert decision.fix_tasks == {}


def test_gate_iterations_below_limit_pass():
    check_gate_iterations(lane_state(gate_iterations=MAX_GATE_ITERATIONS - 1))


def test_gate_iterations_at_limit_raise():
    with pytest.raises(LimitExceededError, match="10"):
        check_gate_iterations(lane_state(gate_iterations=MAX_GATE_ITERATIONS))


def test_fix_cycles_at_limit_raise():
    with pytest.raises(LimitExceededError, match="3"):
        check_fix_cycles(lane_state(fix_cycles=MAX_FIX_CYCLES))


def test_no_progress_raises_circuit_breaker():
    failures = ["pytest: FAILED test_a", "eslint: x"]
    with pytest.raises(NoProgressError):
        check_progress(previous=failures, current=list(failures))


def test_progress_with_fewer_failures_passes():
    check_progress(previous=["a", "b"], current=["a"])


def test_progress_with_different_failures_passes():
    check_progress(previous=["a"], current=["b"])


def test_first_iteration_has_no_previous_and_passes():
    check_progress(previous=None, current=["a"])
