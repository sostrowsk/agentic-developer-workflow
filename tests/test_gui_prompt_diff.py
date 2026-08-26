"""RED tests for A3/A4 — the per-round prompt diff on ``agent.run`` nodes.

Two additive, purely DERIVED fields appear on serialized ``agent.run`` trace nodes
of ``GET /api/runs/{repo}/{run_id}`` (contract): ``prompt_diff`` and
``previous_prompt_seq``. The predecessor is determined purely STRUCTURALLY within
THIS run — same ``agent`` string, same lane, greatest ``seq`` less than the node's
— BEFORE prompt usability matters (D1). The "no predecessor" case yields both
fields ``null``; an identical prompt yields ``prompt_diff == ""`` (NOT null) with
``previous_prompt_seq`` still set; a real difference yields the unified diff
produced byte-exactly by the fixed recipe (splitlines, ``difflib.unified_diff``
with ``n=3``/``lineterm=""``, join with ``"\n"``), where a trailing-newline-only
difference counts as identical (D2/D3, E6). The two fields appear on NO other node
type.

The Prompt tab shows exactly one distinguishable state — "no predecessor",
"identical prompt" or the visible diff (R7). Markup is not pinned by the contract;
following the established GUI test style this module fixes the observable
``data-prompt-diff-state`` (``none``/``identical``/``diff``) as the concrete
target. No production code is written here.

Derived from .adw/spec.md (AC 8-13), .adw/contract.yaml (D1..D3, R7) and
.adw/plan.md (B4-B6).
"""

import difflib

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    iter_nodes,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"

P1 = "line one\nline two\nline three\nline four"
P2 = "line one\nline TWO changed\nline three\nline four"


def _unified(prev, cur):
    """The fixed diff recipe (D3/E6): splitlines, ``unified_diff`` n=3 lineterm="",
    joined with newlines."""
    return "\n".join(
        difflib.unified_diff(prev.splitlines(), cur.splitlines(), n=3, lineterm="")
    )


def _agent_start(seq, span, agent, prompt, *, lane="backend", parent="L"):
    payload = {"system_append": ""}
    if agent is not None:
        payload["agent"] = agent
    if prompt is not None:
        payload["prompt"] = prompt
    return rec(seq, "agent.run", "start", span, parent, sec=seq, lane=lane, payload=payload)


def _agent_end(seq, span, *, lane="backend", parent="L"):
    return rec(seq, "agent.run", "end", span, parent, sec=seq, lane=lane,
               payload={"result_text": "ok", "is_error": False})


def _lane_start(seq, span, name):
    return rec(seq, "lane", "start", span, "PB", sec=seq, lane=name, payload={
        "name": name, "branch": f"adw/{name}", "worktree": "wt",
        "base_sha": None, "ports": {}})


def _wrap(inner):
    """Wrap agent/lane events in a run + build phase so the tree builds."""
    lines = [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Prompt diff")),
        rec(2, "phase", "start", "PB", "R", sec=2,
            payload={"name": "build", "from_phase": "build"}),
    ]
    last = max(e["seq"] for e in inner if isinstance(e.get("seq"), int))
    lines.extend(inner)
    lines.append(rec(last + 1, "phase", "end", "PB", "R", sec=last + 1,
                     payload={"name": "build", "to_phase": "done"}))
    lines.append(rec(last + 2, "run", "end", "R", None, sec=last + 2,
                     payload=run_end_payload("done")))
    return lines


def _predecessor_lines():
    """One backend lane with three ``build_agent`` runs (A1 seq4, A2 seq6 with a
    changed prompt, A3 seq8 identical to A2) and a ``review_agent`` run (seq10);
    plus a frontend lane with a ``build_agent`` run (seq14) whose prompt equals A1.
    The frontend run has NO same-lane predecessor, so a lane-ignoring derivation
    (which would pick the backend A3) is caught."""
    return _wrap([
        rec(3, "lane", "start", "L", "PB", sec=3, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
        _agent_start(4, "A1", "build_agent", P1),
        _agent_end(5, "A1"),
        _agent_start(6, "A2", "build_agent", P2),
        _agent_end(7, "A2"),
        _agent_start(8, "A3", "build_agent", P2),
        _agent_end(9, "A3"),
        _agent_start(10, "O", "review_agent", "review prompt"),
        _agent_end(11, "O"),
        rec(12, "lane", "end", "L", "PB", sec=12, lane="backend",
            payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}),
        _lane_start(13, "L2", "frontend"),
        _agent_start(14, "F1", "build_agent", P1, lane="frontend", parent="L2"),
        _agent_end(15, "F1", lane="frontend", parent="L2"),
        rec(16, "lane", "end", "L2", "PB", sec=16, lane="frontend",
            payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}),
    ])


def _client(tmp_path, lines):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def _by_seq(tree):
    return {n["seq"]: n for n in iter_nodes(tree) if n.get("type") == "agent.run"}


def test_predecessor_by_agent_lane_and_greatest_smaller_seq(home, tmp_path):  # noqa: F811
    """D1: the predecessor is the same-agent, same-lane run with the greatest seq
    below the node's. Other agents and other lanes are never used as predecessor."""
    client, slug = _client(tmp_path, _predecessor_lines())
    runs = _by_seq(client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"])

    # A2 diffs against A1 (its immediate same-agent, same-lane predecessor).
    assert runs[6]["previous_prompt_seq"] == 4
    assert runs[6]["prompt_diff"] == _unified(P1, P2)
    # A1 has no earlier candidate -> no predecessor.
    assert runs[4]["prompt_diff"] is None and runs[4]["previous_prompt_seq"] is None
    # A different agent (review_agent) has no same-agent predecessor.
    assert runs[10]["prompt_diff"] is None and runs[10]["previous_prompt_seq"] is None
    # The frontend build_agent run must NOT borrow the backend runs (lane isolation).
    assert runs[14]["prompt_diff"] is None and runs[14]["previous_prompt_seq"] is None


def test_identical_prompt_yields_empty_diff_not_null(home, tmp_path):  # noqa: F811
    """D3: A3's predecessor is A2 (greatest smaller seq); the prompts are identical,
    so ``prompt_diff`` is ``""`` (distinct from the null 'no predecessor' case) with
    ``previous_prompt_seq`` still pointing at A2."""
    client, slug = _client(tmp_path, _predecessor_lines())
    runs = _by_seq(client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"])

    assert runs[8]["prompt_diff"] == ""
    assert runs[8]["previous_prompt_seq"] == 6


def test_unusable_immediate_predecessor_yields_null_not_an_older_run(home, tmp_path):  # noqa: F811
    """D1/D2 (targeted): the predecessor is chosen structurally BEFORE prompt
    usability. B3's immediate predecessor B2 has a non-string prompt, so the result
    is the null 'no predecessor' case — the older valid B1 is NEVER substituted."""
    base = "base line\nsecond line"
    current = "current line\nsecond line"
    lines = _wrap([
        rec(3, "lane", "start", "L", "PB", sec=3, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
        _agent_start(4, "B1", "build_agent", base),
        _agent_end(5, "B1"),
        _agent_start(6, "B2", "build_agent", None),      # missing/non-string prompt
        _agent_end(7, "B2"),
        _agent_start(8, "B3", "build_agent", current),
        _agent_end(9, "B3"),
        rec(10, "lane", "end", "L", "PB", sec=10, lane="backend",
            payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}),
    ])
    client, slug = _client(tmp_path, lines)
    runs = _by_seq(client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"])

    assert runs[8]["prompt_diff"] is None
    assert runs[8]["previous_prompt_seq"] is None
    assert runs[8]["prompt_diff"] != _unified(base, current)   # not diffed against B1


def test_no_predecessor_when_own_agent_or_prompt_is_unusable(home, tmp_path):  # noqa: F811
    """D2: a considered node with no usable string ``agent`` or ``prompt`` is the
    null case even when an earlier same-agent candidate exists."""
    lines = _wrap([
        rec(3, "lane", "start", "L", "PB", sec=3, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
        _agent_start(4, "C1", "build_agent", "seed prompt"),
        _agent_end(5, "C1"),
        _agent_start(6, "C2", "build_agent", None),      # own prompt unusable
        _agent_end(7, "C2"),
        _agent_start(8, "C3", None, "some prompt"),      # own agent unusable
        _agent_end(9, "C3"),
        rec(10, "lane", "end", "L", "PB", sec=10, lane="backend",
            payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}),
    ])
    client, slug = _client(tmp_path, lines)
    runs = _by_seq(client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"])

    assert runs[6]["prompt_diff"] is None and runs[6]["previous_prompt_seq"] is None
    assert runs[8]["prompt_diff"] is None and runs[8]["previous_prompt_seq"] is None


def test_diff_is_byte_exact_and_trailing_newline_counts_as_identical(home, tmp_path):  # noqa: F811
    """D3/E6: a real difference is the byte-exact unified diff; a difference only in
    the trailing newline splits to equal lines and yields the empty diff."""
    prev = "alpha\nbeta\ngamma\ndelta\nepsilon"
    cur = "alpha\nbeta\nGAMMA\ndelta\nepsilon"
    lines = _wrap([
        rec(3, "lane", "start", "L", "PB", sec=3, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
        _agent_start(4, "D1", "build_agent", prev),
        _agent_end(5, "D1"),
        _agent_start(6, "D2", "build_agent", cur),
        _agent_end(7, "D2"),
        _agent_start(8, "E1", "build_agent", "x\ny"),
        _agent_end(9, "E1"),
        _agent_start(10, "E2", "build_agent", "x\ny\n"),   # trailing newline only vs E1
        _agent_end(11, "E2"),
        rec(12, "lane", "end", "L", "PB", sec=12, lane="backend",
            payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}),
    ])
    client, slug = _client(tmp_path, lines)
    runs = _by_seq(client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"])

    assert runs[6]["prompt_diff"] == _unified(prev, cur)
    assert runs[6]["previous_prompt_seq"] == 4
    assert runs[10]["prompt_diff"] == ""              # trailing-newline-only -> identical
    assert runs[10]["previous_prompt_seq"] == 8


def test_fields_are_present_only_on_agent_run_nodes(home, tmp_path):  # noqa: F811
    """AC 13: the derived fields ride only ``agent.run`` nodes; no other node type
    carries them."""
    client, slug = _client(tmp_path, _predecessor_lines())
    tree = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"]

    for node in iter_nodes(tree):
        if node.get("type") == "agent.run":
            assert "prompt_diff" in node and "previous_prompt_seq" in node
        else:
            assert "prompt_diff" not in node
            assert "previous_prompt_seq" not in node


def _pane(html, seq):
    """The detail pane of the ``agent.run`` with ``data-seq`` == ``seq``."""
    marker = f'data-seq="{seq}"'
    i = html.find('class="pane pane-agent-run"')
    while i != -1:
        end = html.find('class="pane ', i + 10)
        chunk = html[i:] if end == -1 else html[i:end]
        if marker in chunk:
            return chunk
        i = html.find('class="pane pane-agent-run"', i + 10)
    raise AssertionError(f"no agent.run pane for seq {seq}")


def test_prompt_tab_shows_three_distinguishable_states(home, tmp_path):  # noqa: F811
    """R7: the Prompt tab additionally shows exactly one distinguishable state —
    'no predecessor' (A1), 'identical prompt' (A3) or the visible diff (A2, whose
    changed line is rendered). The full prompt itself stays present."""
    client, slug = _client(tmp_path, _predecessor_lines())
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert 'data-prompt-diff-state="none"' in _pane(html, 4)
    assert 'data-prompt-diff-state="identical"' in _pane(html, 8)

    diff_pane = _pane(html, 6)
    assert 'data-prompt-diff-state="diff"' in diff_pane
    assert "line TWO changed" in diff_pane          # the visible diff content
