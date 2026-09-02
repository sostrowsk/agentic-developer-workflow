"""RED page-level tests for the compacted trace-tree column (Trace-Baum verdichten).

These exercise the externally observable HTML/JSON surface, which .adw/contract.yaml
pins normatively (the markup WORDING is not pinned; the behaviour of A1/A4/A5 and
the structural invariance of the JSON ``tree`` are):

* A4 — file paths render repo-relative in the visible tree text while the full path
  survives in a ``title`` attribute; a path OUTSIDE the repo is left untouched; no
  tree node shows the absolute repo path as visible text.
* A5 — ``?focus`` on an A1-folded ``agent.tool.result`` is redirected to its call
  node (same ``tool_use_id``): the CALL's ``data-seq`` tree entry and its pane are
  brought into the loaded page, and the result no longer exists as its own
  selectable tree entry.
* Contract — ``GET /api/runs/{repo}/{run_id}`` still serves under ``tree`` the
  UNVERDICHTETE structure: every ``agent.tool.call``/``agent.tool.result`` a
  separate node, no synthetic nodes, no new fields.

RED until the compaction layer is built (today paths are absolute, results are
separate tree entries, and ``?focus`` targets the result's own seq).
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    late_timeline_strand_lines,
    rec,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


def _client(tmp_path, lines, run_id=RUN_ID):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug, repo


def _trace_section(html: str) -> str:
    i = html.find('class="trace"')
    j = html.find('class="panes"')
    return html[i:j] if (i != -1 and j != -1 and i < j) else ""


def _panes_section(html: str) -> str:
    i = html.find('class="panes"')
    j = html.find('class="problems"')
    return html[i:j] if (i != -1 and j != -1 and i < j) else html[i:]


def _visible_text(section: str) -> str:
    """The section with every ``title="..."`` attribute value removed, so a
    substring assertion tests only what a reader SEES, not the title tooltip."""
    return re.sub(r'title="[^"]*"', 'title=""', section)


# --- A4: repo-relative visible paths, full path in the title --------------------


def _repo_path_lines(repo_abs: str):
    inside = f"{repo_abs}/adw/gui/app.py"
    outside = "/etc/hosts"
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Paths")),
        rec(2, "agent.run", "start", "A", "R", sec=2,
            payload={"agent": "spec_agent", "prompt": "p", "system_append": ""}),
        rec(3, "agent.tool.call", "point", "A", sec=3, payload={
            "tool": "Read", "tool_use_id": "u1", "input": {"file_path": inside}}),
        rec(4, "agent.tool.result", "point", "A", sec=4,
            payload={"tool_use_id": "u1", "is_error": False}),
        rec(5, "agent.tool.call", "point", "A", sec=5, payload={
            "tool": "Read", "tool_use_id": "u2", "input": {"file_path": outside}}),
        rec(6, "agent.tool.result", "point", "A", sec=6,
            payload={"tool_use_id": "u2", "is_error": False}),
        rec(7, "agent.run", "end", "A", "R", sec=7,
            payload={"result_text": "done", "is_error": False}),
        rec(8, "run", "end", "R", None, sec=8,
            payload={"status": "done", "totals": {"duration": 1.0}}),
    ]


def test_a4_paths_are_repo_relative_in_text_and_full_in_title(home, tmp_path):  # noqa: F811
    repo_abs = os.path.normpath(str((tmp_path / "repo").resolve()))
    client, slug, _repo = _client(tmp_path, _repo_path_lines(repo_abs))
    inside_abs = f"{repo_abs}/adw/gui/app.py"

    trace = _trace_section(client.get(f"/runs/{slug}/{RUN_ID}").text)
    assert trace, "trace section not found"
    visible = _visible_text(trace)

    # The repo-relative form is visible; the absolute repo path is NOT visible text.
    assert "adw/gui/app.py" in visible
    assert inside_abs not in visible
    # The full path survives in a title attribute (the tooltip keeps it reachable).
    assert f'title="{inside_abs}"' in trace or f"title='{inside_abs}'" in trace
    # A path OUTSIDE the repo is left exactly as it is.
    assert "/etc/hosts" in visible


# --- A5: ?focus on a folded result redirects to its call node -------------------


def test_focus_on_folded_result_redirects_to_the_call_node(home, tmp_path):  # noqa: F811
    """The late tool pairs sit far beyond the initial trace window. ``?focus`` on a
    late RESULT must position the window on its CALL (so the pair folds together)
    and materialise the call's tree entry + pane; the result is not a tree entry."""
    client, slug, _repo = _client(tmp_path, late_timeline_strand_lines(300))
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()
    results = [e for e in events if e.get("type") == "agent.tool.result"]
    # A result in the MIDDLE of the run: focusing it clamps to no tail window, so a
    # naive offset==result-index would push the call just before the window edge.
    # Only the A5 redirect (offset the CALL) keeps the pair together on the page.
    result_seq = results[len(results) // 2]["seq"]
    call_seq = result_seq - 1                 # its call immediately precedes it

    base = _trace_section(client.get(f"/runs/{slug}/{RUN_ID}").text)
    assert f'data-seq="{call_seq}"' not in base   # out of the initial window

    focused = client.get(f"/runs/{slug}/{RUN_ID}", params={"focus": result_seq}).text
    trace = _trace_section(focused)
    # The CALL is now a visible, selectable tree entry ...
    assert f'data-seq="{call_seq}"' in trace
    assert "data-tree-entry" in trace
    # ... its pane is materialised ...
    assert re.search(rf'class="pane[^"]*"\s+data-seq="{call_seq}"', _panes_section(focused))
    # ... and the folded result is NOT its own selectable tree entry.
    assert f'data-seq="{result_seq}"' not in trace


# --- Contract: the JSON ``tree`` is structurally unchanged ----------------------


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children") or [])


def test_api_tree_keeps_the_unverdichtete_structure(home, tmp_path):  # noqa: F811
    client, slug, _repo = _client(tmp_path, comprehensive_lines())
    tree = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"]
    nodes = list(_walk(tree))
    by_type = {}
    for n in nodes:
        by_type.setdefault(n["type"], []).append(n)

    # Call and result are still SEPARATE nodes (t1 in comprehensive_lines).
    assert len(by_type.get("agent.tool.call", [])) == 1
    assert len(by_type.get("agent.tool.result", [])) == 1

    # No synthetic compaction nodes and no compaction-only fields have leaked into the
    # API (``outcome`` already legitimately exists on phase/lane/round aggregates, so
    # only the genuinely new compaction keys are treated as sentinels).
    assert "group" not in by_type and "repeat" not in by_type
    forbidden = {"kind", "ops", "folded", "entries"}
    for n in nodes:
        assert not (forbidden & set(n)), f"compaction field leaked into API node: {n['type']}"
