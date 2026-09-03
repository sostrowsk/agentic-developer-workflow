"""The trace-tree column renders the COMPLETE tree — no paging, no entry cap.

The bounded moving window over the trace tree (``?offset``, 100 nodes per page)
was removed on request: the left ``section.trace`` shows every node of the run at
once. What stays untouched is the Tools window inside the detail panes (its own
``?tools_offset``) and the compaction of the tree itself (folded results, repeat
and group nodes, phases collapsed by default) — folding is presentation, paging
was a cut.

Also pinned here: the pane's raw payload block is pretty-printed JSON, not a
single unreadable line.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.model import build_tree
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    many_tool_entries_lines,
    tool_entry_command,
    write_run,
)
from tests.gui_js_harness import run_scenario

RUN_ID = "aaaa1111"
TREE_ENTRY_MARKER = "data-tree-entry"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _client(tmp_path, lines):
    repo = tmp_path / "repo"
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


def test_trace_column_renders_every_node_of_a_large_run(home, tmp_path):  # noqa: F811
    """A run far past the old 200-entry cap renders ONE entry marker per tree node —
    head and tail are in the same document, nothing is windowed out."""
    pairs = 600
    lines = many_tool_entries_lines(pairs)
    client, slug = _client(tmp_path, lines)

    html = _detail_html(client, slug)
    tree = _trace_section(html)

    assert html.count(TREE_ENTRY_MARKER) == _tree_node_count(lines)
    assert tool_entry_command(0) in tree                 # the head ...
    assert tool_entry_command(pairs - 1) in tree         # ... and the tail, together


def test_trace_column_has_no_paging_navigation(home, tmp_path):  # noqa: F811
    """No ``?offset`` paging link survives in the trace column — there is nothing
    left to page through."""
    client, slug = _client(tmp_path, many_tool_entries_lines(600))

    tree = _trace_section(_detail_html(client, slug))

    assert "offset=" not in tree
    assert "window-nav" not in tree


def test_trace_column_ignores_a_stale_offset_parameter(home, tmp_path):  # noqa: F811
    """A bookmarked ``?offset`` from the paged era must not hide the head any more:
    the parameter is inert for the trace column, the full tree renders either way."""
    pairs = 600
    lines = many_tool_entries_lines(pairs)
    client, slug = _client(tmp_path, lines)

    tree = _trace_section(_detail_html(client, slug, offset=100000))

    assert tool_entry_command(0) in tree
    assert tool_entry_command(pairs - 1) in tree


def test_pane_raw_payload_is_pretty_printed(home, tmp_path):  # noqa: F811
    """The pane's payload block is a readable field list over several lines — not one
    long line, and not a JSON dump (no quoted keys, no braces). See
    tests/test_gui_payload_pretty.py for the format itself."""
    client, slug = _client(tmp_path, many_tool_entries_lines(2))

    html = _detail_html(client, slug)

    open_tag = '<pre class="raw-fields">'
    i = html.find(open_tag)
    assert i != -1, "no payload field block rendered"
    block = html[i + len(open_tag) : html.find("</pre>", i)]
    assert "\n" in block, "payload is still a single line"
    assert "&#34;" not in block and '"' not in block, "payload is still a JSON dump"
    assert "{" not in block and "}" not in block, "payload is still a JSON dump"
    assert ": " in block, "payload is not a key/value listing"


# --- lazy detail panes: one shared shell instead of one pane per point node -----


def _panes_section(html: str) -> str:
    i = html.find('class="panes"')
    j = html.find('class="problems"')
    return html[i:j] if (i != -1 and j != -1 and i < j) else html[i:]


def test_point_nodes_get_no_server_rendered_pane(home, tmp_path):  # noqa: F811
    """With the column unpaged, a pane per node would put thousands of hidden
    elements in the DOM. Point nodes (tool calls/results, messages, …) therefore get
    NO pane of their own — the panes section holds only the few span panes."""
    pairs = 300
    client, slug = _client(tmp_path, many_tool_entries_lines(pairs))

    panes = _panes_section(_detail_html(client, slug))

    assert panes.count('class="pane') < 20, "a pane is still rendered per point node"
    # ... while the tree still has one entry per node (nothing was cut).
    assert _detail_html(client, slug).count(TREE_ENTRY_MARKER) >= 2 * pairs


def test_a_single_lazy_pane_shell_is_rendered_for_point_nodes(home, tmp_path):  # noqa: F811
    """Exactly one shared, server-rendered shell exists that the client fills on
    selection — no DOM is constructed in JS (GUI-SPEC §7.3)."""
    client, slug = _client(tmp_path, many_tool_entries_lines(5))

    panes = _panes_section(_detail_html(client, slug))

    assert panes.count("data-generic-pane") == 1
    assert "data-generic-label" in panes and "data-generic-type" in panes


def test_selecting_a_point_node_fills_the_shared_pane(tmp_path):
    """A5/client: clicking a POINT node that has no pane of its own selects the shared
    generic pane, points it at that node's seq, labels it from the tree row and fills
    it from the events route — no navigation, and no DOM built in JS. Selecting a span
    node again hands the selection back to that node's OWN pane."""
    r = run_scenario(tmp_path, "lazy-pane")
    after = r["after_point"]

    assert after["generic_selected"] is True
    assert after["span_selected"] is False
    assert after["load_seq"] == "42"
    assert after["label"] == "Read adw/gui/app.py"
    assert after["type"] == "agent.tool.call"
    assert "LAZYMARK" in after["body"]
    assert after["fetched_own_record"] is True   # exactly this record, not the tail
    assert after["navigations"] == []            # selected in place, no ?focus reload

    assert r["span_selected_after"] is True
    assert r["generic_selected_after"] is False


def test_a_late_response_never_clears_the_shared_pane(tmp_path):
    """Regression (P1): the shared pane is ONE element, so two point selections in a
    row reuse it. A response for the node it no longer shows must neither write into
    it nor clear it — otherwise the newer node's payload is wiped and the pane stays
    blank for the node the user actually selected."""
    r = run_scenario(tmp_path, "lazy-pane-race")

    # B is the selected node and renders first ...
    assert "FRESH_B" in r["after_fresh"]["body"]
    # ... and A's late response neither overwrites nor CLEARS it.
    assert "FRESH_B" in r["final_body"], "a stale response cleared the selected node's pane"
    assert "STALE_A" not in r["final_body"]
    assert r["final_load_seq"] == "43"
    assert r["generic_selected"] is True


def test_long_tree_labels_wrap_inside_their_column():
    """Regression: a long unbreakable label — a worktree path, a grep pattern — used
    to overflow the trace column and paint over the detail pane beside it (measured
    on run 16f39431: 59 labels past the column edge, the worst 282px into the panes
    column). The column already has `min-width: 0`; the labels must also be allowed
    to break."""
    import re

    css = TestClient(create_app(repos=[])).get("/static/app.css").text
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    wraps = False
    for sel, body in re.findall(r"([^{}]*)\{([^}]*)\}", css):
        if ".label" not in sel:
            continue
        m = re.search(r"overflow-wrap\s*:\s*([\w-]+)", body)
        if m and m.group(1) in ("anywhere", "break-word"):
            wraps = True
    assert wraps, "app.css lets long trace labels overflow their column"
