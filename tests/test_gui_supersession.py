"""RED tests for Aufgabe B — latest-interaction-wins (supersession).

This is a CORRECTNESS requirement, not a performance one: a debug tool must never
show the data of the wrong node. Click node A, then node B; A's slower lazy-load
fetch returns last. Today A's late fetch writes A's content into A's pane while B is
selected, and A's interaction still completes an ``adw:select`` measure — the
worst-possible failure for a tool whose only job is to reflect state correctly.

After the fix, a superseded interaction writes nothing into the DOM and produces no
measure; only the winning (latest) selection is applied and measured. This holds
whether B interrupts before OR after the earlier selection's fetch returns, so the
two resolution orders are exercised.

Verified BEHAVIOURALLY through the minimal dependency-free JS harness
(``tests/gui_js_harness.js``) — the served ``app.js`` run in ``node`` with stubbed
DOM / fetch / performance / rAF whose deferred responses are resolved in a chosen
order. No browser automation. A missing ``node`` runtime is a verification failure,
never a skip. Derived from .adw/spec.md (B1/B2), .adw/contract.yaml
(x-adw-supersession) and .adw/plan.md §3.
"""

import pytest

from tests.gui_js_harness import run_scenario

# "BA": B's fetch settles first, A's (superseded) settles LAST — the classic
# late-return race. "AB": A's fetch settles first while B is already selected — B
# interrupted before A's fetch returned. Both must behave identically.
ORDERS = ["BA", "AB"]


@pytest.mark.parametrize("order", ORDERS)
def test_latest_selection_wins_and_superseded_write_is_dropped(tmp_path, order):
    """B1: after selecting A then B, the detail pane ends showing node B's content;
    A's late-returning fetch does NOT write its content into the DOM while B is
    selected, and B is the active selection."""
    r = run_scenario(tmp_path, "supersession", order)

    assert r["paneB_selected"] is True
    assert r["paneA_selected"] is False
    assert "CONTENT-20" in r["paneB_text"]          # B's content is shown
    assert "CONTENT-10" not in r["paneA_text"]      # A's superseded write was dropped


@pytest.mark.parametrize("order", ORDERS)
def test_superseded_interaction_produces_no_measure(tmp_path, order):
    """B2: a superseded interaction records no end mark and produces NO measure —
    only the winning (latest) selection is measured. Both clicks set a start mark,
    but exactly one end mark and one ``adw:select`` measure exist, so A's start mark
    is never paired with B's end mark into a bogus measure."""
    r = run_scenario(tmp_path, "supersession", order)

    assert r["start_marks"] == 2                     # both selections started
    assert r["end_marks"] == 1                       # only the winner ended
    assert r["measures_select"] == 1                 # exactly one measure (B's)
