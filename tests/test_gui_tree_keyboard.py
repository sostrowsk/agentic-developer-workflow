"""RED tests for A1 — the trace tree becomes keyboard operable (AC 1–4).

The main interaction of the run inspector — selecting a trace node and reading it —
is today mouse-only: ``app.js`` registers no ``keydown`` handler and no ``li.node``
is focusable. This brief adds a keyboard path to the tree column with the binding
key table (spec "Bindende Tastenbelegung"): Up/Down move between VISIBLE rows
(collapsed content skipped), Right/Left drive the existing folds, Enter/Space select
the node exactly like a click, Home/End jump to the first/last visible row. The tree
column is exactly ONE tab stop, not 577 (E2).

Behaviour is exercised through the Node harness against the SERVED ``app.js`` (never a
source-text proxy); the harness only dispatches keydown and reads the resulting DOM
state (the ``.selected`` class, ``tabindex``, ``phase-open``/``fold-hidden``). Key
names and ARIA attributes are NOT contract (contract "Nicht Teil des Contracts") — the
observable RESULT is: which node the page selects, and how many tab stops the column
holds. Derived from .adw/spec.md (A1, AC 1–4), .adw/plan.md (B3/B4) and
.adw/contract.yaml (x-adw-template-behavior.tree).
"""

from tests.gui_js_harness import run_scenario


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


def test_tree_column_is_a_single_tab_stop(tmp_path):
    """AC 4/E2: after client initialization the tree column is exactly ONE sequential
    tab stop — not one per row and not the natively focusable fold buttons / group
    <summary> inside it (which are taken out of the sequence)."""
    r = run_scenario(tmp_path, "tree-tabstop")

    assert r["tab_stops"] == 1, f"the tree column holds {r['tab_stops']} tab stops, not 1"


def test_tree_uses_treeitem_roles_and_a_managed_cursor_scrolled_into_view(tmp_path):
    """Review findings (roles + cursor): the column is a real ARIA tree —
    ``role="tree"`` with ``role="treeitem"`` rows, so ``aria-selected`` on a row is
    supported (a bare ``<li>`` would not support it). The navigation cursor is exposed
    through the container's ``aria-activedescendant`` (managed focus reference) and is
    scrolled into view when it moves, so it stays visible on a tree taller than the
    viewport."""
    r = run_scenario(tmp_path, "tree-keyboard")

    assert r["roles"]["list"] == "tree", "the tree container is not role=tree"
    assert r["roles"]["item"] == "treeitem", "the tree rows are not role=treeitem"
    # aria-selected rides on a treeitem (supported), not a plain listitem.
    assert r["afterEnter"]["selected_role"] == "treeitem", (
        "the selected row does not carry a treeitem role, so aria-selected is unsupported"
    )
    # The active descendant tracks the cursor, and moving it scrolls it into view.
    assert r["activedescendant"]["matches_cursor"] is True, (
        "aria-activedescendant does not reference the current cursor row"
    )
    assert r["activedescendant"]["scrolled"] is True, "the cursor is not scrolled into view"


def test_row_action_links_stay_keyboard_reachable(tmp_path):
    """Review finding (row actions): taking the tree column down to one tab stop must
    remove the REDUNDANT fold controls from the tab order, not the row ACTION links.
    A raw-jump link (raw-range navigation) and a recovery-report link keep their
    native keyboard focus (no ``tabindex="-1"``), so those actions stay reachable
    without a mouse."""
    r = run_scenario(tmp_path, "tree-actions")

    assert r["raw_jump_tabindex"] != "-1", "the raw-jump link was removed from the tab order"
    assert r["recovery_tabindex"] != "-1", "the recovery-report link was removed from the tab order"
