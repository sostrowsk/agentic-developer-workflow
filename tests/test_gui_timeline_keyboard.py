"""RED test for A2 — the timeline ROW becomes the operable unit (AC 5).

Since 0.24.0 a timeline line is `div.tl-bar-row` > label + track > `span.tl-bar`, all
carrying ``data-seq``; the click handler still reacts only to ``.tl-bar[data-seq]``
— the proportionally sized bar, as narrow as 6 px. This brief makes the whole row the
operable unit: clickable, keyboard-focusable in display order, and activatable with
Enter/Space, reaching the SAME node as the bar — including the existing ``?focus``
redirect for a node the page cannot show (an out-of-window timeline target), also from
the keyboard. The bar itself keeps working, with no double activation when its click
bubbles through the row.

Exercised through the Node harness against the served ``app.js``. Derived from
.adw/spec.md (A2, AC 5), .adw/plan.md (B5) and .adw/contract.yaml
(x-adw-template-behavior.timeline).
"""

from tests.gui_js_harness import run_scenario


def test_timeline_row_is_the_operable_unit(tmp_path):
    """AC 5: the row (not the bar alone) is a keyboard tab stop and its label is a
    click target; a click on the label selects the in-window node in place, keyboard
    activation of an out-of-window row redirects via ``?focus``, Space activates
    without scrolling, and a click on the bar inside the row selects exactly once."""
    r = run_scenario(tmp_path, "timeline-activate")

    # The whole row is keyboard-focusable (a tab stop), not just the 6px bar.
    assert r["row_tabindex"] is not None and int(r["row_tabindex"]) >= 0, (
        "the timeline row is not keyboard-focusable"
    )

    # A click on the LABEL selects the in-window node in place — no navigation.
    assert r["afterLabel"]["pane10_selected"] is True, "a label click did not select the node"
    assert r["afterLabel"]["navs"] == [], "an in-window label click navigated away"

    # Enter on the OUT-of-window row redirects to ?focus for its node (no pane/row).
    assert r["afterKeyOut"]["navs"] == ["/runs/repo/aaaa1111?focus=99"], (
        "keyboard activation of an out-of-window row did not use the ?focus redirect"
    )

    # Space on the in-window row triggers the same in-place selection without scrolling.
    assert r["afterKeySpace"]["pane10_selected"] is True, "Space did not activate the row"
    assert r["afterKeySpace"]["space_default_prevented"] is True, "Space did not prevent scrolling"

    # A click on the bar inside the row activates once, not twice.
    assert r["bar_click_measures"] == 1, "a bar click double-activated through the row"
