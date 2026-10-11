"""The selection is exposed machine-readably."""

from tests.gui_js_harness import run_scenario


def test_selection_is_machine_readable_and_moves(tmp_path):
    """AC 10: the selected node is machine-readably marked (not only via the class),
    exactly one at a time; the marking moves on a mouse selection, a keyboard
    selection and a timeline selection, and the previous node loses it. A pure
    navigation cursor does not carry the marking."""
    r = run_scenario(tmp_path, "selection-marked")

    assert r["initial"] == ["1"], "the initial selection is not marked machine-readably"
    assert r["afterMouse"] == ["10"], "a mouse selection is not marked (or marks more than one)"
    assert r["afterNavOnly"] == ["10"], "a bare navigation cursor was mismarked as a selection"
    assert r["afterKeyboard"] == ["20"], "a keyboard selection did not move the marking"
    assert r["afterTimeline"] == ["10"], "a timeline selection did not move the marking"
