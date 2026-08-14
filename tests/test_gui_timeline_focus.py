"""RED tests for the P2 review finding — timeline navigation to an OUT-OF-WINDOW
node.

With the bounded, moving trace window (Aufgabe A) a Timeline bar can target a node
that is not in the current window: it has no rendered tree entry and no detail pane.
Clicking such a bar must NOT silently fall back to the first visible node (which
would display the WRONG node — the worst failure for a debug tool). Instead the view
navigates a bounded window to that node via ``?focus=<seq>``, so BOTH its tree entry
and its detail pane become visible, and the requested sequence is preserved.

Two halves, no browser automation:
* server (``TestClient``): ``?focus`` positions the bounded window on the requested
  node — its tree entry (``data-tree-entry``) and its ``.pane`` are materialised —
  while at offset 0 the same node is out of the window;
* client (the minimal JS harness): clicking a bar whose node has no pane navigates
  to ``?focus=<seq>``, while an in-window bar still selects in place.

Derived from the P2 finding and .adw/plan.md §2 (reachability throughout
navigation) / .adw/spec.md (A2).
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    late_timeline_strand_lines,
    tab_panel,
    write_run,
)
from tests.gui_js_harness import run_scenario

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _client(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, late_timeline_strand_lines(300), phase="done")
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo)


def _trace_section(html: str) -> str:
    i = html.find('class="trace"')
    j = html.find('class="panes"')
    return html[i:j] if (i != -1 and j != -1 and i < j) else ""


def _panes_section(html: str) -> str:
    i = html.find('class="panes"')
    j = html.find('class="problems"')
    return html[i:j] if (i != -1 and j != -1 and i < j) else html[i:]


def _ci_wait_seq(client, slug) -> int:
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()
    return next(e["seq"] for e in events
               if e.get("type") == "ci.wait" and e.get("kind") == "start")


def test_focus_brings_an_out_of_window_timeline_node_into_view(home, tmp_path):  # noqa: F811
    """P2 (server): a ``ci.wait`` strand sits beyond the initial trace window — it is
    a Timeline bar target yet has no tree entry and no pane at offset 0. ``?focus``
    positions the bounded window on it so its tree entry AND its detail pane become
    visible, without materialising the whole tree."""
    client, slug = _client(tmp_path)
    ci_seq = _ci_wait_seq(client, slug)

    base = client.get(f"/runs/{slug}/{RUN_ID}").text
    seq_attr = f'data-seq="{ci_seq}"'
    # It IS a timeline bar target ...
    assert seq_attr in tab_panel(base, "timeline")
    # ... but out of the initial trace window: no tree entry, no pane.
    assert seq_attr not in _trace_section(base)
    assert seq_attr not in _panes_section(base)

    focused = client.get(f"/runs/{slug}/{RUN_ID}", params={"focus": ci_seq}).text
    tsec = _trace_section(focused)
    assert seq_attr in tsec                       # the tree entry is now visible ...
    assert "data-tree-entry" in tsec
    # ... and its detail pane
    assert re.search(rf'class="pane[^"]*"\s+data-seq="{ci_seq}"', _panes_section(focused))


def test_bar_click_navigates_to_focus_for_out_of_window_node(tmp_path):
    """P2 (client): clicking a Timeline bar whose node has no pane navigates to
    ``?focus=<seq>`` (so the node is materialised on load); an in-window bar whose
    node has a pane still selects in place — no extra navigation, and the requested
    sequence, not the first visible one, is shown."""
    r = run_scenario(tmp_path, "timeline-focus")

    assert r["nav_out"] == ["/runs/repo/aaaa1111?focus=99"]  # out-of-window bar -> navigate
    assert r["nav_all"] == r["nav_out"]                      # in-window bar adds no navigation
    assert r["pane10_selected"] is True                      # the requested in-window node is shown
