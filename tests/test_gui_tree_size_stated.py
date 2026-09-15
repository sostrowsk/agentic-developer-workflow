"""RED/invariant tests for A5 — the trace-tree size is STATED and checked, not
windowed.

The trace column renders the COMPLETE serialized tree: exactly one
``data-tree-entry`` per tree node, no doubling, at any fixture size — a result
folded onto its call keeps its own marker, a compaction wrapper (repeat/group)
adds none. This is the invariant A5 pins as a policy (spec/plan leave the markup
to the implementation): the column is kept readable by the compaction, not by a
cut. ``?offset`` is inert for the column and it renders no page navigation (E2),
while the SEPARATE 200-marker cap keeps applying to the Tools entries
(``data-tool-entry``) and never to the tree (E6).

These fix invariants the code already holds today, so they may be green from the
start (``.adw/plan.md`` B2: the counting definition / ``?offset`` inert /
Tools-window tests are allowed to be green immediately).

Derived from .adw/spec.md (AC 9-11), .adw/contract.yaml (x-adw-template-behavior
.tree / .tools) and .adw/plan.md (B2.5-B2.7, B7).
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.model import build_tree
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    many_tool_entries_lines,
    many_tool_nodes_lines,
    nested_tree_lines,
    write_run,
)

RUN_ID = "aaaa1111"
TREE_ENTRY_MARKER = "data-tree-entry"
TOOL_ENTRY_MARKER = "data-tool-entry"
CAP = 200  # the 200-marker cap — for the Tools entries only (E6), never for the tree.


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _client(tmp_path, lines, *, name="repo"):
    repo = tmp_path / name
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, lines, phase="done")
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo)


def _detail_html(client, slug, **params):
    resp = client.get(f"/runs/{slug}/{RUN_ID}", params=params or None)
    assert resp.status_code == 200
    return resp.text


def _trace_section(html: str) -> str:
    i = html.find('class="trace"')
    j = html.find('class="panes"')
    assert i != -1 and j != -1 and i < j, "trace/panes layout anchors not found"
    return html[i:j]


def _tree_node_count(lines) -> int:
    dicts = [x for x in lines if isinstance(x, dict)]

    def walk(nodes):
        return sum(1 + walk(getattr(n, "children", []) or []) for n in nodes)

    return walk(build_tree(dicts))


# --- AC 9: one data-tree-entry per serialized node, at several sizes -------------


def test_tree_entry_marker_equals_node_count_across_sizes(home, tmp_path):  # noqa: F811
    """AC 9: for several fixture sizes — including a tree well past 200 nodes with
    appended tool results, and a deeply nested one — the number of
    ``data-tree-entry`` markers equals the number of serialized tree nodes: no node
    missing, none counted twice. A folded result keeps its own marker; a compaction
    wrapper adds none."""
    cases = [
        ("tiny", many_tool_entries_lines(3)),      # small, nothing near the old cap
        ("wide", many_tool_entries_lines(150)),    # > 200 nodes, results folded onto calls
        ("huge", many_tool_entries_lines(320)),    # far past 200
        ("nested", nested_tree_lines(250)),        # > 200 nodes, deeply nested + groups
    ]
    for name, lines in cases:
        client, slug = _client(tmp_path, lines, name=name)
        html = _detail_html(client, slug)
        expected = _tree_node_count(lines)
        assert html.count(TREE_ENTRY_MARKER) == expected, name

    # At least two of the cases genuinely exceed 200 nodes (an unwindowed column).
    big = [_tree_node_count(lines) for _n, lines in cases]
    assert sum(1 for n in big if n > CAP) >= 2


# --- AC 10: the column has no window — ?offset is inert, no page navigation ------


def test_offset_is_inert_for_the_tree_and_no_window_nav(home, tmp_path):  # noqa: F811
    """AC 10 (E2): a bookmarked ``?offset`` from the paged era does not change the
    trace column — the same markers with and without it, the same rendered column —
    and the column carries no page navigation (``offset=`` / ``window-nav``)."""
    lines = many_tool_entries_lines(300)
    client, slug = _client(tmp_path, lines)

    base = _trace_section(_detail_html(client, slug))
    offset = _trace_section(_detail_html(client, slug, offset=100000))

    assert base == offset, "?offset must be inert for the trace column"
    assert base.count(TREE_ENTRY_MARKER) == _tree_node_count(lines)
    assert "offset=" not in base and "window-nav" not in base


# --- AC 11: the Tools window keeps its 200 cap; the tree is uncapped -------------


def test_tools_window_is_capped_while_the_tree_is_uncapped(home, tmp_path):  # noqa: F811
    """AC 11 (E6): the Tools entries stay bounded by the 200-marker cap (spread over
    several panes) with their own ``?tools_offset`` page navigation, while the SAME
    page renders the trace tree in full — its marker count equals the node count and
    exceeds 200. The cap applies to ``data-tool-entry``, never to
    ``data-tree-entry``."""
    lines = many_tool_nodes_lines(2000, nodes=8)
    client, slug = _client(tmp_path, lines)
    # A2: the Tools entries render in the focused pane's Tools tab (bounded by the same
    # 200 cap); the trace tree stays fully rendered regardless of focus.
    html = _detail_html(client, slug, focus=2)

    tool_markers = html.count(TOOL_ENTRY_MARKER)
    assert 1 <= tool_markers <= CAP, tool_markers
    assert "tools_offset=" in html, "the Tools window keeps its own page navigation"

    tree_markers = html.count(TREE_ENTRY_MARKER)
    assert tree_markers == _tree_node_count(lines)
    assert tree_markers > CAP, "the trace column must be uncapped"
