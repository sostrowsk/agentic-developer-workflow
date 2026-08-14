"""RED tests for Aufgabe A — the ≤ 2 s promise, kept by BOUNDING THE NUMBER OF
ENTRY NODES in the DOM (not only their contents).

Twice a fix offloaded only the *contents* (lazy payload, collapsing) while the
sheer COUNT of DOM entry nodes kept the real view blocking (real run ``bf831719``:
2 126 tool nodes, 12 738 DOM nodes, > 40 s). This run turns the per-sibling-group /
per-pane slicing into ONE GLOBAL budget per collection that holds throughout
navigation, and it is proven here on the automatable half:

* **A1** the number of rendered *entry markers* is bounded by a hard cap of at most
  200 per collection, independent of the total — for the trace tree (deeply nested
  branches) and the Tools tab (spread over multiple tool-bearing nodes, hidden
  panes included);
* **A2** every entry stays reachable through a MOVING window (the ``?offset``
  navigation): reaching a late entry brings it into the DOM without re-materialising
  all preceding entries — the growing-prefix ``?limit`` is insufficient;
* **A3** bounded rendering changes presentation only: entries keep the underlying
  order and their call/result identity, and the full payload stays reachable.

*Counting definition (A1, pinned by these tests as the verification policy the
contract leaves to the spec/plan):* exactly ONE machine-readable marker element per
rendered entry — ``data-tree-entry`` for a trace-tree entry, ``data-tool-entry``
for a Tools entry. The concrete selector is the implementation's choice; these two
are the selectors the automated tests count and the guide documents.

Wall-clock ≤ 2 s is evidenced manually per ``docs/gui-response-time.md`` (A4); the
automated half proves the DOM bound. Derived from .adw/spec.md (A1–A3),
.adw/contract.yaml (x-adw-bounded-rendering) and .adw/plan.md §2.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.model import build_tree
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    heavy_tool_run_lines,
    home,
    many_tool_entries_lines,
    many_tool_nodes_lines,
    nested_leaf_token,
    nested_tree_lines,
    tool_entry_command,
    write_run,
)

RUN_ID = "aaaa1111"

# The hard cap pinned by the spec/contract (A1): at most this many entry markers per
# collection, at every fixture size.
CAP = 200

# The two per-entry markers the automated tests count (the counting definition).
TREE_ENTRY_MARKER = "data-tree-entry"
TOOL_ENTRY_MARKER = "data-tool-entry"

# The moving-window navigation parameters (A2): INDEPENDENT for the trace tree
# (``?offset``) and the Tools window (``?tools_offset``), so paging one does not
# move the other. Each positions a bounded window so a late entry is reachable
# WITHOUT growing a prefix.
OFFSET = "offset"
TOOLS_OFFSET = "tools_offset"


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
    """The trace-tree region only (``section.trace`` up to ``section.panes``), so a
    token check is not satisfied by the same token echoed inside a detail pane."""
    i = html.find('class="trace"')
    j = html.find('class="panes"')
    assert i != -1 and j != -1 and i < j, "trace/panes layout anchors not found"
    return html[i:j]


def _tools_sections(html: str) -> str:
    """Every ``Tools`` tab panel concatenated (there may be one per agent.run pane),
    each scoped to its own closing ``</section>`` — so a token check sees only the
    Tools entries and their window nav, never a token echoed in a later pane's
    heading or the trace tree."""
    key = 'data-tab-panel="tools"'
    close = "</section>"
    out = []
    start = 0
    while True:
        i = html.find(key, start)
        if i == -1:
            break
        end = html.find(close, i)
        out.append(html[i:end] if end != -1 else html[i:])
        start = (end + len(close)) if end != -1 else (i + len(key))
    return "".join(out)


def _tree_node_count(lines) -> int:
    dicts = [x for x in lines if isinstance(x, dict)]

    def walk(nodes):
        return sum(1 + walk(getattr(n, "children", []) or []) for n in nodes)

    return walk(build_tree(dicts))


# --- A1: bounded initial DOM, global budget per collection ----------------------


def test_trace_tree_entry_markers_bounded_across_sizes(home, tmp_path):  # noqa: F811
    """A1: for DEEPLY NESTED trace trees of 200 / 2 000 / 20 000 entries, the count
    of the trace-entry marker over the COMPLETE initial document is at least one and
    at most 200 — independent of the total. Per-sibling-group slicing would multiply
    the budget at every nesting level (the fixture's groups are each well under the
    cap, so a recursive ``children[:limit]`` would materialise the whole tree); only
    one global budget passes."""
    for total in (200, 2000, 20000):
        client, slug = _client(tmp_path, nested_tree_lines(total))
        html = _detail_html(client, slug)
        count = html.count(TREE_ENTRY_MARKER)
        assert 1 <= count <= CAP, f"total={total}: {count} trace-entry markers (cap {CAP})"
        assert count < total  # far below the total — the whole point


def test_nested_tree_bounds_initial_render_but_keeps_the_head(home, tmp_path):  # noqa: F811
    """A1/A3: the bounded initial render still shows the first entries in order (the
    head sentinel is present) while an entry far beyond the bound is NOT inlined
    initially — a bounded display, not a loss of content."""
    total = 2000
    client, slug = _client(tmp_path, nested_tree_lines(total))
    html = _detail_html(client, slug)
    tree = _trace_section(html)
    assert nested_leaf_token(0) in tree                 # the head is rendered
    assert nested_leaf_token(total - 1) not in tree     # a far entry is windowed out


def test_tool_entry_markers_bounded_across_sizes(home, tmp_path):  # noqa: F811
    """A1: for 200 / 2 000 / 20 000 Tools entries spread over MULTIPLE tool-bearing
    nodes, the count of the tool-entry marker anywhere in the initial detail-pane
    DOM (hidden, non-active panes included) is at least one and at most 200,
    independent of the total. Rendering up to ``limit`` tools in each of several
    panes (the current per-pane slice yields 8x100 = 800 > 200) defeats the cap; one
    global budget passes."""
    for total in (200, 2000, 20000):
        client, slug = _client(tmp_path, many_tool_nodes_lines(total, nodes=8))
        html = _detail_html(client, slug)
        count = html.count(TOOL_ENTRY_MARKER)
        assert 1 <= count <= CAP, f"total={total}: {count} tool-entry markers (cap {CAP})"
        assert count < total


def test_exactly_one_marker_per_rendered_entry(home, tmp_path):  # noqa: F811
    """A1 counting definition: exactly one marker element per rendered entry, on
    BOTH collections. A small run (nothing windowed) has 3 tool pairs -> 6 Tools
    entries, so the tool-entry marker appears exactly 6 times; the trace-entry marker
    appears exactly once per trace-tree node."""
    lines = many_tool_entries_lines(3)
    client, slug = _client(tmp_path, lines)
    html = _detail_html(client, slug)

    assert html.count(TOOL_ENTRY_MARKER) == 6            # 3 calls + 3 results
    assert html.count(TREE_ENTRY_MARKER) == _tree_node_count(lines)


# --- A2: reachability through a MOVING window (not a growing prefix) -------------


def test_trace_tree_moving_window_reaches_every_entry(home, tmp_path):  # noqa: F811
    """A2: with the trace tree windowed, ``?offset`` moves the bounded window so a
    late trace entry is reached WITHOUT materialising all preceding ones — reaching
    the tail drops the head from the DOM (a moving window, not a growing prefix), and
    the bound holds after every transition."""
    pairs = 600  # 600 unique call tokens (toolstep-00000 .. toolstep-00599)
    client, slug = _client(tmp_path, many_tool_entries_lines(pairs))

    head, mid, tail = tool_entry_command(0), tool_entry_command(300), tool_entry_command(pairs - 1)

    initial = _trace_section(_detail_html(client, slug))
    assert head in initial                               # the head is materialised
    assert tail not in initial                           # the tail is not, initially
    assert 1 <= _detail_html(client, slug).count(TREE_ENTRY_MARKER) <= CAP

    at_tail = _detail_html(client, slug, **{OFFSET: 100000})
    assert tail in _trace_section(at_tail)               # the tail is now reachable
    assert head not in _trace_section(at_tail)           # WITHOUT re-materialising the head
    assert 1 <= at_tail.count(TREE_ENTRY_MARKER) <= CAP  # the bound still holds

    at_mid = _detail_html(client, slug, **{OFFSET: 550})
    assert mid in _trace_section(at_mid)                 # a middle entry is reachable too
    assert head not in _trace_section(at_mid)
    assert 1 <= at_mid.count(TREE_ENTRY_MARKER) <= CAP


def test_tools_moving_window_reaches_every_entry(home, tmp_path):  # noqa: F811
    """A2: the same moving-window reachability for the Tools collection — a late tool
    entry is reached via ``?offset`` while the tool-entry marker count stays bounded
    and reaching the tail does not re-materialise the head."""
    pairs = 600
    client, slug = _client(tmp_path, many_tool_entries_lines(pairs))

    head, mid, tail = tool_entry_command(0), tool_entry_command(300), tool_entry_command(pairs - 1)

    initial = _tools_sections(_detail_html(client, slug))
    assert head in initial
    assert tail not in initial

    # The Tools window is navigated by its OWN offset (independent of the tree).
    at_tail = _detail_html(client, slug, **{TOOLS_OFFSET: 100000})
    assert tail in _tools_sections(at_tail)
    assert head not in _tools_sections(at_tail)
    assert 1 <= at_tail.count(TOOL_ENTRY_MARKER) <= CAP

    at_mid = _detail_html(client, slug, **{TOOLS_OFFSET: 550})
    assert mid in _tools_sections(at_mid)
    assert 1 <= at_mid.count(TOOL_ENTRY_MARKER) <= CAP


def test_tools_window_navigation_keeps_owning_agent_selected(home, tmp_path):  # noqa: F811
    """A2 (P1 finding): paging the Tools window must keep the OWNING agent selected
    and its trace entry visible while advancing a SEPARATE tool offset — otherwise
    the agent loses its tree entry on reload and its later tool entries become
    unreachable through the UI. The Tools 'more' link carries the agent (``focus``)
    and an independent ``tools_offset``; clicking through the windows reaches the
    tail while the agent stays selected, and the tree offset is not moved by it."""
    pairs = 600
    client, slug = _client(tmp_path, many_tool_entries_lines(pairs))
    agent_seq = 2  # run == seq 1, the single agent.run == seq 2

    tools = _tools_sections(_detail_html(client, slug))
    assert "tools_offset=" in tools               # an independent tool offset ...
    assert f"focus={agent_seq}" in tools          # ... carrying the owning agent

    # Walk several Tools windows (as clicking 'more' repeatedly would), each time
    # keeping the agent focused; the tail entry must become visible and the agent
    # must stay selected with its tree entry present.
    seen_tail = False
    for step in range(0, pairs * 2 + 200, 190):
        html = _detail_html(client, slug, focus=agent_seq, tools_offset=step)
        assert f'data-focus="{agent_seq}"' in html                 # agent stays selected
        assert f'data-seq="{agent_seq}"' in _trace_section(html)   # its tree entry stays visible
        assert html.count(TOOL_ENTRY_MARKER) <= CAP                # bound held throughout
        if tool_entry_command(pairs - 1) in _tools_sections(html):
            seen_tail = True
    assert seen_tail, "the tail tool entry never became visible while paging the Tools window"

    # Independence: moving the TOOLS offset does not move the trace-tree window.
    tree_base = _trace_section(_detail_html(client, slug))
    tree_after_tools = _trace_section(_detail_html(client, slug, **{TOOLS_OFFSET: 100000}))
    assert tree_base == tree_after_tools


# --- A3: presentation fidelity (order, identity, full payload reachable) ---------


def test_rendered_entries_keep_order_and_full_payload_stays_reachable(home, tmp_path):  # noqa: F811,E501
    """A3 (E8): displayed entries appear in the UNDERLYING ORDER and are not
    reordered relative to the served data; tool entries keep their call/result
    identity; and a windowed-out entry's complete payload stays reachable through the
    read-only events route — bounded rendering changes presentation only."""
    pairs = 600
    client, slug = _client(tmp_path, many_tool_entries_lines(pairs))

    tree = _trace_section(_detail_html(client, slug))
    shown = [int(tok.split("-")[1]) for tok in _iter_tokens(tree)]
    assert shown, "no trace entries rendered"
    assert shown == sorted(shown)                        # underlying order preserved
    assert shown == list(range(shown[0], shown[0] + len(shown)))  # contiguous, not scattered
    assert shown[0] == 0                                 # windowed from the head

    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()
    calls = {e["payload"]["tool_use_id"]: e for e in events if e.get("type") == "agent.tool.call"}
    results = {e["payload"]["tool_use_id"]: e for e in events
               if e.get("type") == "agent.tool.result"}
    assert len(calls) == pairs and len(results) == pairs  # union covers every entry

    last = f"t{pairs - 1}"                               # a windowed-out entry
    assert calls[last]["payload"]["input"]["command"] == tool_entry_command(pairs - 1)
    assert results[last]["payload"]["content"] == f"out-{pairs - 1:05d}"
    assert calls[last]["type"] != results[last]["type"]  # call/result identity kept


def _iter_tokens(text: str):
    marker = "toolstep-"
    start = 0
    while True:
        i = text.find(marker, start)
        if i == -1:
            return
        yield text[i:i + len(marker) + 5]
        start = i + len(marker)


# --- A4 fixture-shape guard (the twice-repeated wrong-size pitfall) --------------


def test_heavy_run_reference_has_the_correct_shape(home, tmp_path):  # noqa: F811
    """A4 shape guard: the manual measurement's reference run has >= 2000 TOOL NODES
    (not bytes) with SMALL contents — a node-count reference. Twice a fixture
    mirrored the wrong size (bytes instead of nodes, then too few nodes) while the
    real view blocked; an automated shape check makes that recurrence impossible."""
    client, slug = _client(tmp_path, heavy_tool_run_lines())
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()

    tool_nodes = [e for e in events if e.get("type") in ("agent.tool.call", "agent.tool.result")]
    assert len(tool_nodes) >= 2000                       # node COUNT, the real bottleneck
    results = [e for e in events if e.get("type") == "agent.tool.result"]
    assert max(len(r["payload"].get("content", "")) for r in results) < 1024  # small, not bytes
