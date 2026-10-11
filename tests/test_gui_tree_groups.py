"""Keyboard opening of collapsed groups and repetitions in the trace tree."""

from tests.gui_js_harness import run_scenario


def test_groups_and_repetitions_open_by_keyboard_and_children_become_selectable(tmp_path):
    """A group and a repetition wrapper open with → (their ``aria-expanded`` flips
    true), their revealed child is then reachable and selectable with Enter, and ←
    closes the group again — all keyboard-only, with the column still one tab stop and
    no synthesised click."""
    r = run_scenario(tmp_path, "tree-groups")

    # The group starts collapsed, opens on →, and its child becomes selectable.
    assert r["groupBefore"] == {"expanded": "false", "open": False}
    assert r["groupAfterOpen"] == {"expanded": "true", "open": True}, "→ did not open the group"
    assert r["sel_child"] == "30", "the group's revealed child could not be selected by keyboard"

    # ← collapses the group again.
    assert r["groupAfterClose"] == {"expanded": "false", "open": False}, "← did not close the group"

    # The repetition wrapper works the same way.
    assert r["repeat_expanded"] == "true", "→ did not open the repetition wrapper"
    assert r["sel_repeat_child"] == "31", "the repetition's revealed child could not be selected"

    # Group navigation adds no extra tab stop and synthesises no click.
    assert r["tab_stops"] == 1, f"the tree column holds {r['tab_stops']} tab stops, not 1"
    assert r["synth_clicks"] == 0, "group interaction went through a synthesised click"
