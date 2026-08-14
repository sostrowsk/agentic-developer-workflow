"""Behavioural test for the live-refresh region swap preserving expand state.

After the trace tree was reworked into a flat, windowed <li> list (Aufgabe A) it has
no per-node <details>, so the open-state preservation must be scoped to the
<details> that DO still exist in the swapped region — the Tools entries, the Raw
rows and the artifact wraps. This proves the mechanism is live (not the dead,
tree-only no-op the P3 review flagged): a user-expanded Tools entry stays open across
a wholesale live region swap.

Verified through the minimal dependency-free JS harness (no browser automation).
"""

from tests.gui_js_harness import run_scenario


def test_live_swap_preserves_expanded_details(tmp_path):
    """The wholesale live region swap re-applies the user's expand choice to the
    freshly fetched markup for the <details> that exist (here a Tools entry): opening
    it, then a refresh that renders it closed by default, leaves it OPEN."""
    r = run_scenario(tmp_path, "openstate-swap")

    assert r["fresh_details_open"] is True
