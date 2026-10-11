"""Trace-tree keyboard navigation and actions."""

from tests.gui_js_harness import run_scenario

# --- from test_gui_tree_keyboard.py ---------------------------------------------


def test_keyboard_selects_a_node_without_a_mouse(tmp_path):
    """AC 1: a Down/Down/Enter sequence into the tree column selects the cursored
    node and reveals its pane — with the same result as a click, WITHOUT any (even
    synthetic) click event. Pure Up/Down movement selects nothing, and Space reaches
    the same selection as Enter while preventing the page scroll."""
    r = run_scenario(tmp_path, "tree-keyboard")

    # The initial (mouse-free) auto-selection is the first node.
    assert r["initial"] == "1"
    # Two Downs move the cursor but must NOT select (a cursor is not a selection).
    assert r["afterMotion"] == "1"

    after = r["afterEnter"]
    assert after["sel"] == "3", "Enter did not select the cursored visible node"
    assert after["pane3_selected"] is True, "the selected node's pane did not become visible"
    assert after["measures"] == 1, "keyboard selection did not run the same adw:select path"
    assert after["synth_clicks"] == 0, "selection went through a synthesised click event"

    space = r["afterSpace"]
    assert space["sel"] == "20", "Space did not select like Enter"
    assert space["space_default_prevented"] is True, "Space did not prevent the page scroll"


def test_down_arrow_skips_a_collapsed_subtree(tmp_path):
    """AC 2: with the cursor on a collapsed phase, Down moves to the next VISIBLE row,
    never into the hidden subtree; Home/End address only visible rows. The collapsed
    phase (seq 8) hides its child (seq 9); from it Down reaches seq 20 (not seq 9),
    and Space there selects seq 20 — proving the hidden row was skipped."""
    r = run_scenario(tmp_path, "tree-keyboard")

    assert r["afterSpace"]["sel"] == "20", "Down entered the collapsed subtree instead of skipping"
    assert r["afterEnd"] == "20", "End did not land on the last visible row"
    assert r["afterHome"] == "1", "Home did not land on the first visible row"


def test_right_and_left_open_and_close_a_fold_and_no_op_on_a_leaf(tmp_path):
    """AC 3: Right opens a closed fold row, Left closes an open one; on a row without a
    fold mechanism the state is unchanged and no error is raised. A Ctrl chord is not
    intercepted."""
    r = run_scenario(tmp_path, "tree-fold-keys")

    assert r["start"] == {"phase_open": False, "child_hidden": True}
    assert r["afterCtrlRight"]["phase_open"] is False, "a Ctrl chord was intercepted"
    assert r["afterRight"] == {"phase_open": True, "child_hidden": False}, "Right did not open"
    assert r["afterLeft"] == {"phase_open": False, "child_hidden": True}, "Left did not close"
    assert r["leaf_no_error"] is True, "Right/Left on a leaf row raised an error"

    # E2/AC 4: revealing a hidden row's action link on fold-open — by keyboard OR by
    # the mouse caret — must not add a sequential tab stop (before any further move).
    assert r["tab_stops_after_kbd_open"] == 1, (
        f"{r['tab_stops_after_kbd_open']} tab stops after a keyboard fold-open, not 1"
    )
    assert r["tab_stops_after_mouse_open"] == 1, (
        f"{r['tab_stops_after_mouse_open']} tab stops after a mouse fold-open, not 1"
    )


def test_tree_column_is_a_single_tab_stop(tmp_path):
    """AC 4/E2: after client initialization the tree column is exactly ONE sequential
    tab stop — not one per row and not the natively focusable fold buttons / group
    <summary> inside it (which are taken out of the sequence)."""
    r = run_scenario(tmp_path, "tree-tabstop")

    assert r["tab_stops"] == 1, f"the tree column holds {r['tab_stops']} tab stops, not 1"


def test_tree_uses_treeitem_roles_and_a_managed_cursor_scrolled_into_view(tmp_path):
    """Review findings (roles + cursor): the column is a real ARIA tree —
    ``role="tree"`` with ``role="treeitem"`` rows, so ``aria-selected`` on a row is
    supported (a bare ``<li>`` would not support it). The cursor is managed with a
    roving tabindex — real focus moves onto the current row (the single tab stop) and
    is scrolled into view when it moves, so it stays visible on a tree taller than the
    viewport."""
    r = run_scenario(tmp_path, "tree-keyboard")

    assert r["roles"]["list"] == "tree", "the tree container is not role=tree"
    assert r["roles"]["item"] == "treeitem", "the tree rows are not role=treeitem"
    # aria-selected rides on a treeitem (supported), not a plain listitem.
    assert r["afterEnter"]["selected_role"] == "treeitem", (
        "the selected row does not carry a treeitem role, so aria-selected is unsupported"
    )
    # Real focus moves to the cursor row (roving), it is the tabbable stop, and it is
    # scrolled into view on a move.
    assert r["cursor"]["focused"] is True, "focus is not managed onto the cursor row"
    assert r["cursor"]["tabindex"] == "0", "the cursor row is not the roving tab stop"
    assert r["cursor"]["scrolled"] is True, "the cursor is not scrolled into view"


def test_row_actions_stay_keyboard_operable_within_one_tab_stop(tmp_path):
    """Review findings (row actions): even with inline row actions present, the tree
    column is exactly ONE sequential tab stop — the action links are managed by the
    roving tabindex (``-1``), not extra stops (AC 4 / contract). Yet each action stays
    keyboard-OPERABLE: activating the raw-jump link performs its raw-range navigation,
    and activating the recovery link opens the Artifacts tab and reveals the escalation
    report — its own behaviour, never swallowed by node selection."""
    r = run_scenario(tmp_path, "tree-actions")

    assert r["tab_stops"] == 1, f"the tree column holds {r['tab_stops']} tab stops, not 1"
    assert r["links_managed"] is True, "action links are not managed by the roving tabindex"

    assert r["raw_navs"] == ["?raw_from_seq=5&raw_to_seq=9"], (
        "keyboard activation did not perform the raw-jump's raw-range navigation"
    )
    assert r["recovery"]["artifacts_active"] is True, "recovery did not open the Artifacts tab"
    assert r["recovery"]["escalation_open"] is True, "recovery did not reveal the escalation report"
    assert r["recovery"]["trace_active"] is False, "the Trace tab stayed active after recovery"


# --- from test_gui_tree_keyboard_actions.py -------------------------------------


def test_arrow_right_on_an_open_row_reaches_the_child_not_the_action_link(tmp_path):
    """The key table's "to the first child row" must mean the child ROW. A phase that
    owns an inline ``raw-jump`` link must not capture Arrow-Right with that link."""
    r = run_scenario(tmp_path, "tree-keyboard-actions")

    cursor = r["cursorAfterRight"]
    assert cursor is not None, "Arrow-Right left the tree without a cursor"
    assert not cursor.startswith("a."), (
        f"Arrow-Right landed on a row's action link ({cursor}) instead of its first "
        "child row"
    )
    assert cursor.startswith("li.node"), f"Arrow-Right landed on {cursor}, not on a node row"
    assert cursor.endswith("[3]"), f"Arrow-Right reached {cursor}, expected the child row seq 3"


def test_a_recovery_abort_entry_is_not_a_tree_row(tmp_path):
    """``li.recovery-abort[data-seq]`` is an informational entry of the recovery card,
    not a selectable node: it has no pane, and selecting it would fall back to the
    first node. It must neither be decorated as a ``treeitem`` nor consume a cursor
    step."""
    r = run_scenario(tmp_path, "tree-keyboard-actions")

    assert not r["abortIsTreeitem"], (
        "a recovery-abort entry is decorated as role=treeitem and becomes selectable"
    )


def test_the_tree_keeps_exactly_one_tab_stop_across_a_keyboard_fold(tmp_path):
    """E2/AC 4: the tree column is ONE tab stop — before and after a fold reveals
    rows that carry their own action links. Guards the behaviour the review suspected
    to be broken; it holds because the action links are neutralised while still
    hidden."""
    r = run_scenario(tmp_path, "tree-keyboard-actions")

    assert r["cursorBeforeOpen"] is not None and r["cursorBeforeOpen"].endswith("[8]"), (
        f"the walk did not reach the collapsed phase (cursor {r['cursorBeforeOpen']})"
    )
    assert len(r["stopsInitial"]) == 1, f"initial tab stops: {r['stopsInitial']}"
    assert len(r["stopsAfterOpen"]) == 1, (
        f"opening a fold left {len(r['stopsAfterOpen'])} tab stops: {r['stopsAfterOpen']}"
    )


def test_arrow_right_on_a_childless_open_row_does_not_step_sideways(tmp_path):
    """Follow-up to the fix above (Codex, round 1): "to the first child row" means a
    CHILD — a phase that is open but has no children must not hand the cursor to the
    next sibling row. Phase-only event logs produce exactly that shape."""
    r = run_scenario(tmp_path, "tree-keyboard-actions")

    assert r["cursorOnChildless"] is not None and r["cursorOnChildless"].endswith("[30]"), (
        f"the walk did not reach the childless phase (cursor {r['cursorOnChildless']})"
    )
    # Compare the ROW the cursor sits on, not the class list — opening the fold adds
    # a `phase-open` class to the very same row.
    assert r["cursorAfterRightChildless"].endswith("[30]"), (
        f"Arrow-Right moved from the childless phase to {r['cursorAfterRightChildless']} "
        "— a sibling, not a child"
    )
