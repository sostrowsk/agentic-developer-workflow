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
    normalize_key_text,
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


def test_inactive_lane_finding_routes_to_all_active_lanes():
    result = ReviewResult(
        verdict="needs_fixes",
        findings=[finding(lane="frontend", issue="Swift-Quelle kaputt")],
    )
    decision = triage_final_review(result, active_lanes=["backend"])
    assert [f.issue for f in decision.fix_tasks["backend"]] == ["Swift-Quelle kaputt"]
    assert "frontend" not in decision.fix_tasks


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 1 — Worktree-/Run-Präfix weg, relativer Rest bleibt (absolut und nackt relativ)
        ("/repo/.adw/runs/1234abcd/trees/backend/src/foo.py", "src/foo.py"),
        (".adw/runs/1234abcd/trees/backend/src/foo.py", "src/foo.py"),
        # 2 — Temp-Pfade
        ("/tmp/pytest-of-user/pytest-7/data", "<tmp>"),
        ("in /private/var/folders/ab/xyz/T/f.txt", "in <tmp>"),
        # 3 — ISO-Zeitstempel als Ganzes, danach reine Uhrzeit
        ("2026-11-08T12:34:56.123Z", "<ts>"),
        ("am 2026-11-08 12:34:56+01:00 gestartet", "am <ts> gestartet"),
        ("um 12:34:56 Uhr", "um <ts> Uhr"),
        # 4 — Dauern; Bezeichner wie test_1s sind keine Dauer
        ("1 failed in 36.02s", "1 failed in <dur>"),
        ("took 250 ms", "took <dur>"),
        ("FAILED test_1s", "FAILED test_1s"),
        # 5 — Hex-Adressen
        ("at 0x7fAB12", "at <hex>"),
        # 6 — Zeile/Spalte nach Dateiname mit Endung, "line N"
        ("foo.py:12:4", "foo.py:<n>"),
        ('File "x.py", line 12', 'File "x.py", line <n>'),
        # 7 — Host-Ports
        ("localhost:8123", "<host>:<port>"),
        ("GET 127.0.0.1:8000/api", "GET <host>:<port>/api"),
        # 8 — Leerraum
        ("  Fehler\n  in\tfoo.py  ", "Fehler in foo.py"),
        # keine Kleinschreibung
        ("AssertionError in Foo", "AssertionError in Foo"),
    ],
)
def test_normalize_key_text_rules(raw, expected):
    assert normalize_key_text(raw) == expected
