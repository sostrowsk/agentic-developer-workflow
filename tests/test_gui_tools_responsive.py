"""RED tests for Aufgabe A — the Tools tab and trace tree respond by NODE COUNT.

The polish run made selecting an ``agent.run`` node fast by loading contents
lazily, but switching to the Tools tab still blocks: the bottleneck is the number
of DOM nodes, not the payload size. The corrected criterion is an ``agent.run``
with at least 1500 tool entries (calls and results combined, small contents) that
stays responsive; the same holds for the trace tree that lists the same entries.

Only the OBSERVABLE EFFECT of the (unpinned) mechanism is asserted: the initial
server-rendered page does not materialise every entry (a late entry's unique
marker is absent), yet every entry — call and result, with full content — stays
reachable through the read-only events route (AC-A1/A2/A3, E8). The 2-second
bound itself is evidenced by the documented manual browser check, not automated.

Derived from .adw/spec.md (AC-A1..A3), .adw/contract.yaml
(x-adw-template-behavior.tools / .trace_tree) and .adw/plan.md §5.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    many_tool_entries_lines,
    tool_entry_command,
    write_run,
)

RUN_ID = "aaaa1111"
PAIRS = 800  # -> 1600 tool entries (>= 1500)


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _tools_client(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, many_tool_entries_lines(PAIRS), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    return client, _slug_for(repo)


def test_fixture_has_at_least_1500_tool_entries(home, tmp_path):  # noqa: F811
    """The fixture is a faithful node-count reference: at least 1500 tool entries
    (calls and results combined), each with a small content."""
    client, slug = _tools_client(tmp_path)
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()

    calls = [e for e in events if e.get("type") == "agent.tool.call"]
    results = [e for e in events if e.get("type") == "agent.tool.result"]
    assert len(calls) + len(results) >= 1500
    # Small contents: this is a node-count, not a payload-size, reference case.
    assert max(len(r["payload"].get("content", "")) for r in results) < 1024


def test_initial_page_does_not_materialise_every_tool_entry(home, tmp_path):  # noqa: F811
    """AC-A1/A3: the initial server-rendered detail page materialises a BOUNDED
    slice — the first call's marker may appear, but a late call's marker does not.
    This covers both the Tools pane and the trace tree, which both otherwise inline
    every entry's label (the ~35 s / node-count freeze)."""
    client, slug = _tools_client(tmp_path)
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert tool_entry_command(PAIRS - 1) not in html  # a late entry is not inlined
    # And the whole page stays far below "one summary line per entry".
    assert html.count("toolstep-") < PAIRS


def test_every_tool_entry_stays_reachable_with_full_content(home, tmp_path):  # noqa: F811
    """AC-A2 (E8): no entry is dropped — every call and result of the big node is
    reachable through the events route, calls and results remain distinguishable,
    and each entry's full content is intact."""
    client, slug = _tools_client(tmp_path)
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()

    calls = {e["payload"]["tool_use_id"]: e for e in events
             if e.get("type") == "agent.tool.call"}
    results = {e["payload"]["tool_use_id"]: e for e in events
               if e.get("type") == "agent.tool.result"}
    assert len(calls) == PAIRS and len(results) == PAIRS  # union covers every entry

    # The very last pair (windowed out of the initial page) is fully reachable.
    last = f"t{PAIRS - 1}"
    assert calls[last]["payload"]["input"]["command"] == tool_entry_command(PAIRS - 1)
    assert results[last]["payload"]["content"] == f"out-{PAIRS - 1:05d}"
    # Calls and results are distinguishable (never collapsed into one kind).
    assert calls[last]["type"] != results[last]["type"]
