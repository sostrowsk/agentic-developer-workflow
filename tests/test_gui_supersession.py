"""Tests for latest-interaction-wins (supersession)."""

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


def test_superseded_node_can_be_reselected_and_refetched(tmp_path):
    """Regression (P2): when A is superseded and its response arrives late, the stale
    response is discarded WITHOUT permanently marking the pane loaded. Selecting A
    again must issue a fresh fetch and render A's payload — the detail must never be
    stuck at "Loading…" until a page reload."""
    r = run_scenario(tmp_path, "supersession-reselect")

    assert r["refetched"] is True                    # re-selecting A re-fetched
    assert "CONTENT-10" in r["paneA_text"]           # and rendered A's payload
    assert "Loading" not in r["paneA_text"]
    assert r["paneA_selected"] is True


def test_refresh_triggered_fetch_cannot_write_the_wrong_node(tmp_path):
    """P1: the live-refresh region swap reapplies the selection and may start a
    tool-body fetch. That fetch must be tied to the current selection generation, so
    if a newer node is selected before it returns, its obsolete payload is NOT
    written into the refreshed DOM — obsolete selection work has no observable DOM
    effect."""
    r = run_scenario(tmp_path, "refresh-supersession")

    assert r["freshPaneB_selected"] is True
    assert "CONTENT-20" in r["freshPaneB_text"]       # the current node is shown
    assert "CONTENT-10" not in r["freshPaneA_text"]   # the refresh-triggered write was dropped


def test_reselecting_a_node_while_its_fetch_is_in_flight(tmp_path):
    """P2: re-selecting a node (double-click / A->B->A) BEFORE its first tool-body
    fetch returns must not break the two pinned surfaces:
    (1) the adw:select measure must NOT complete on the "Loading…" placeholder — a
        request in flight is not completion; and
    (2) when the in-flight response arrives it belongs to the STILL-selected node, so
        its payload is rendered (the pane reflects the last-chosen node), never
        cleared to empty.
    """
    r = run_scenario(tmp_path, "reselect-inflight")

    # (1) no measure while the pane still shows the loading placeholder
    assert r["paneA_before"] == "Loading…"
    assert r["measure_before_resolve"] == 0

    # (2) the last-chosen node ends up showing its real payload, measured exactly once
    assert "CONTENT-10" in r["paneA_text"]
    assert "Loading" not in r["paneA_text"] and r["paneA_text"] != ""
    assert r["paneA_selected"] is True
    assert r["measures_select"] == 1


def test_deferred_click_suppresses_the_superseded_measure_in_the_post_paint_task(tmp_path):
    """B2 race (P1): selection A settles and SCHEDULES its post-paint rAF/task while
    it is still current; only THEN is B selected; only THEN do the queued callbacks
    run. A's end mark and measure must STILL be suppressed — the currentness check
    happens inside the scheduled task, not merely before scheduling — so A never
    records an end mark and only B is measured."""
    r = run_scenario(tmp_path, "supersession-deferred")

    assert r["start_marks"] == 2
    assert r["end_marks"] == 1                       # A's task did not record an end mark
    assert r["measures_select"] == 1                 # only B produced a measure
    assert "CONTENT-20" in r["paneB_text"]
    assert r["paneB_selected"] is True
