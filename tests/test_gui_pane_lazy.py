"""RED behavioural tests for GUI-Redesign 5 (Brief 5) — the client side of A1/A2.

The served ``app.js`` is exercised in the minimal dependency-free ``node`` harness
(``tests/gui_js_harness.js`` / ``.py``; no browser automation). These prove the
externally observable contract of the span-pane LAZY LOAD and the fragment swap:

  * a selected span pane fetches the SAME detail page with ``focus=<seq>`` (existing
    query params preserved) and the ``X-Requested-With: fetch`` header, shows a loading
    state, then the fetched body — AC 6;
  * a superseded / swapped-away answer writes nothing (no half state, no error) —
    AC 7;
  * a running request is reused (double-select and A→B→A), a loaded pane is not
    re-fetched — AC 8;
  * a failed load keeps the rest of the view, shows a "could not load" state and stays
    re-loadable — AC 9;
  * keyboard selection uses the SAME path as a click (no synthetic click); pure
    navigation fetches nothing — AC 11;
  * the fragment refresh keeps the unselected context panel live (AC 3) and the open
    state of a section survives the asynchronous re-load across a region swap (AC 12).

RED until B5 adds the span-pane lazy load and B3's fragment carries the context on a
region. Derived from .adw/spec.md (AC 3, 6–9, 11–12), .adw/contract.yaml
(x-adw-template-behavior.lazy_selection, latest_context) and .adw/plan.md (B2 rows
6–13, B5).
"""

import re

from tests.gui_js_harness import run_scenario


def test_selecting_a_span_pane_fetches_with_focus_and_header_and_shows_body(tmp_path):
    """AC 6/12: selecting an unloaded span node requests the existing page with
    ``focus=<seq>`` and the fetch header — preserving ``?tools_offset`` — shows a
    loading state, then replaces it with the fetched pane body (tabs initialised, no
    empty box). The load hits the detail route, never ``/api``."""
    r = run_scenario(tmp_path, "pane-lazy-load", "?tools_offset=200&focus=1")

    url = r["request_url"]
    assert url is not None, "selecting an unloaded span pane issued no fetch"
    assert "/runs/" in url and "/api/" not in url
    assert re.search(r"focus=10(?:&|$)", url), url        # focus set to the target node
    assert "tools_offset=200" in url                       # existing window preserved
    assert r["request_header"] == "fetch"                  # X-Requested-With: fetch (A1)

    assert r["loading_text"]                               # a loading state, not an empty box
    assert r["body_text"] == "PANE_A_BODY"                 # the fetched body was inserted
    assert r["unloaded_after"] is None                     # the shell is no longer "unloaded"
    assert r["selected"] is True
    # The inserted body's tabs are (re-)initialised so they keep their roles (AC 12).
    assert r["tab_role"] == "tab"
    assert r["tab_selected"] == "true"


def test_a_superseded_pane_answer_writes_nothing(tmp_path):
    """AC 7: choosing node B while A's load is in flight makes A's late answer a no-op
    — A stays unloaded (re-loadable), B shows its own body."""
    r = run_scenario(tmp_path, "pane-lazy-stale-select")

    assert r["paneA_marker"] is None                       # A's late answer wrote nothing
    assert r["paneA_unloaded"] is not None                 # A left re-loadable, no half state
    assert r["paneB_marker"] == "FRESH_B"                  # the last-chosen node shows its body
    assert r["paneB_selected"] is True


def test_an_answer_for_a_swapped_away_pane_writes_nothing(tmp_path):
    """AC 7: an answer whose target shell was replaced by a live region swap writes
    neither body nor error into the fresh pane."""
    r = run_scenario(tmp_path, "pane-lazy-stale-swap")

    assert r["paneA_marker"] == "FRESH_MARK"               # the swapped-in body is untouched
    assert r["paneA_own_text"] == ""                       # no stale content, no error hint


def test_an_in_flight_pane_load_is_reused_not_duplicated(tmp_path):
    """AC 8: double-selecting the same node — and A→B→A while A is still loading —
    reuses the one in-flight request; a loaded pane is not fetched again."""
    r = run_scenario(tmp_path, "pane-lazy-dedup")

    assert r["after_double"] == 1                          # no second request for A
    assert r["after_aba"] == 1                             # A→B→A reuses A's in-flight load
    assert r["reload_count"] == 0                          # a loaded pane is not re-fetched


def test_a_failed_pane_load_keeps_the_view_and_stays_reloadable(tmp_path):
    """AC 9: a failed load shows a "could not load" state (not a permanent loading box,
    no body), and re-selecting the node issues a fresh request."""
    r = run_scenario(tmp_path, "pane-lazy-error")

    assert r["error_text"]                                 # the pane says it could not load
    assert r["marker_present"] is False                    # no body was inserted
    assert r["still_unloaded"] is not None                 # a failed load does not count as loaded
    assert r["retried"] is True                            # re-selection re-fetches


def test_keyboard_selection_uses_the_same_pane_load_path(tmp_path):
    """AC 11: mere keyboard navigation fetches nothing; Enter selects and loads the pane
    through the SAME path as a click (fetch header, no synthetic click)."""
    r = run_scenario(tmp_path, "pane-lazy-keyboard")

    assert r["initial_fetch"] == 0
    assert r["after_nav"] == 0                             # navigation alone loads no pane
    assert r["after_enter"] == 1                           # Enter triggers the lazy load
    assert r["synth_clicks"] == 0                          # no synthesised click
    assert r["request_header"] == "fetch"


def test_fragment_swap_keeps_the_unselected_context_panel_live(tmp_path):
    """AC 3: a live swap whose response is a bare fragment (no <body>) still updates the
    unselected context panel from the context carried on the region."""
    r = run_scenario(tmp_path, "context-fragment-swap")

    assert r["before"] == {"phase": "spec", "limit_hits": ""}
    assert r["after"] == {"phase": "build", "limit_hits": "2"}


def test_open_section_survives_the_async_reload_across_a_swap(tmp_path):
    """AC 12: a section opened inside a pane stays open after a region swap re-loads
    that pane asynchronously — the captured open state outlives the pending load."""
    r = run_scenario(tmp_path, "pane-open-survives-swap")

    assert r["section_open_after"] is True
