"""The announced tab pattern is fulfilled."""

from tests.gui_js_harness import run_scenario


def test_tabs_expose_roles_selected_state_and_panel_link(tmp_path):
    """AC 8: every button of a tablist group carries ``role="tab"``; exactly one is
    ``aria-selected="true"`` — the server-preselected one (the RAW tab of a
    raw_from_seq landing), NOT a reset to the first tab — and it points via
    ``aria-controls`` at its panel with ``role="tabpanel"``."""
    r = run_scenario(tmp_path, "tab-pattern")
    init = r["outerInit"]

    assert init["roles"] == ["tab"] * 4, f"not every button is role=tab: {init['roles']!r}"
    assert init["selected_count"] == 1, "not exactly one tab is aria-selected"
    assert init["selected"] == "raw", "the client reset the server preselection instead of adopting"
    assert init["controls_panel"] == "raw", "aria-controls does not point at the selected panel"
    assert init["controls_role"] == "tabpanel", "the controlled panel is not role=tabpanel"


def test_arrow_keys_switch_the_active_tab_within_its_group(tmp_path):
    """AC 9: Left/Right switch the active tab inside the nearest group, moving
    ``aria-selected``, the ``active`` class, the visible panel and the single roving
    tab stop together — a click switch updates the ARIA state too, and a nested
    group's arrow key does not disturb the outer group."""
    r = run_scenario(tmp_path, "tab-pattern")

    a = r["afterArrow"]
    assert a["selected"] == "artifacts", "ArrowLeft did not move aria-selected"
    assert a["active_button"] == "artifacts", "the active class did not travel with the arrow key"
    assert a["visible_panel"] == "artifacts", "the visible panel did not travel with the arrow key"
    assert a["tab_stops"] == 1, "more than one tab is a sequential tab stop (roving broken)"
    assert a["roving_at"] == "artifacts", "the single tab stop did not move to the new active tab"

    assert r["afterClick"]["selected"] == "timeline", "a click switch did not update aria-selected"

    nested = r["nested"]
    assert nested["inner_before"] == "prompt" and nested["inner_after"] == "answer", (
        "the arrow key did not switch the nested group"
    )
    assert nested["outer_after"] == nested["outer_before"], (
        "a nested-group arrow key disturbed the outer group"
    )
