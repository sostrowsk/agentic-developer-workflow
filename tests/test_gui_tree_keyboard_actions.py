"""Regression tests for two defects found by the review of run 0283ed75.

Both concern which elements the tree's roving cursor treats as rows:

* **Arrow-Right on an open fold row.** The binding key table says Right opens a
  collapsed fold row and, on an already open one, moves to its **first child row**.
  ``foldRight`` implemented "first child row" as ``moveCursor(1)``, but ``treeStops``
  interleaves each row with its own inline action links (``rowActions``) — so on a
  phase carrying a ``raw-jump`` link the cursor landed on that link instead of the
  child.
* **Rows that are not nodes.** ``isNavigableRow`` accepted every ``li[data-seq]``.
  The recovery card renders ``li.recovery-abort[data-seq]`` inside the trace list —
  informational entries that are not ``.node`` and have no pane. They became
  ``role="treeitem"`` rows, swallowed an Arrow-Down on the way past, and activating
  one fell back to the first node.

A third finding of the same review — "a previously hidden raw-jump link keeps a
native tab stop once its fold opens" — does **not** reproduce: ``decorateTreeItems``
walks every ``li`` regardless of visibility and sets ``tabindex="-1"`` on its action
links before they are ever revealed. The tab-stop assertions below pin that
behaviour so it stays true.

Observed through the Node harness against the SERVED ``app.js``; the harness
dispatches keydown and reads the resulting DOM state.
"""

from tests.gui_js_harness import run_scenario


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
