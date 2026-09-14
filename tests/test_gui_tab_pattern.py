"""RED tests for A4 — the announced tab pattern is fulfilled (AC 8, AC 9).

``run_detail.html`` already sets ``role="tablist"`` on each tab-button container, but
the buttons carry no ``role="tab"``, no ``aria-selected``, no ``aria-controls`` and
there is no arrow-key navigation; the active state lives only in the CSS class
``active``. This brief delivers real tabs: each button gets ``role="tab"``, a
maintained ``aria-selected`` and ``aria-controls`` to its panel (``role="tabpanel"``);
Left/Right switch within a group; only the active tab is a sequential tab stop; the
existing ``active`` class and the server-side preselection stay as they are, the ARIA
markup joins them (E4). The role is NOT removed.

Exercised through the Node harness against the served ``app.js`` — the ARIA state is
maintained on the client (arrow-switching is client-only), so the harness can assert
the whole pattern regardless of whether individual attributes were rendered by the
server. The concrete ARIA names ARE binding per spec (AC 8) even though the contract
excludes attribute wording. Derived from .adw/spec.md (A4, AC 8/9), .adw/plan.md (B7)
and .adw/contract.yaml (x-adw-template-behavior.tabs).
"""

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
