"""RED tests for A1 / Contract R1 — ``waiting`` in the trace tree.

An OPEN (still-running) span whose event type is in ``_WAITING_TYPES``
(``ci.wait``, ``gate``) reports the new node status ``waiting`` instead of
``running``. Any other open span (``agent.run`` and friends) still reports
``running``; a CLOSED ``ci.wait``/``gate`` span keeps its prior result
(``gate`` → ``passed``/``failed``, otherwise ``done``). ``_WAITING_TYPES`` stays
the one shared source for the Timeline and the tree, so the same open span the
Timeline draws as waiting reports ``waiting`` in the tree — the two never
disagree.

Derived from .adw/spec.md (AC1), .adw/contract.yaml (x-behavior.R1_tree_waiting)
and .adw/plan.md (B1). The JSON status field of the tree is the pinned surface;
addressed through FastAPI's TestClient. RED until ``_node_status`` learns the
fifth value.
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    find_node,
    find_node_by_label,
    home,
    rec,
    run_end_payload,
    run_start_payload,
    tab_panel,
    timeline_lines,
    write_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _detail(tmp_path, lines, *, run_id=RUN_ID, phase="build"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    return client, slug, run_id


def open_waiting_lines(issue="Open waiting spans"):
    """A live run with an OPEN ``ci.wait`` AND an OPEN ``gate`` AND an OPEN
    ``agent.run`` (none of them ended), so the serialized tree exercises waiting
    vs. running for open spans side by side."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "agent.run", "start", "A", "PB", sec=2,
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        rec(4, "gate", "start", "G", "PB", sec=3,
            payload={"name": "lint", "cmd": "ruff check .", "timeout": 30, "cwd": "wt"}),
        rec(5, "ci.wait", "start", "CI", "R", sec=4,
            payload={"provider": "gitlab", "pipeline_ref": "adw/backend"}),
        # No end records at all: gate, ci.wait, agent.run, phase and run stay OPEN.
    ]


def closed_waiting_lines(issue="Closed waiting spans"):
    """A finished run whose ``gate`` and ``ci.wait`` spans are CLOSED: a passing
    gate (``lint``), a failing gate (``test``) and an ended ``ci.wait``."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "gate", "start", "GP", "PB", sec=2,
            payload={"name": "lint", "cmd": "ruff check .", "timeout": 30, "cwd": "wt"}),
        rec(4, "gate", "end", "GP", "PB", sec=3,
            payload={"passed": True, "exit_code": 0, "timed_out": False, "output": "ok"}),
        rec(5, "gate", "start", "GF", "PB", sec=4,
            payload={"name": "test", "cmd": "pytest -x -q", "timeout": 30, "cwd": "wt"}),
        rec(6, "gate", "end", "GF", "PB", sec=5,
            payload={"passed": False, "exit_code": 1, "timed_out": False, "output": "boom"}),
        rec(7, "ci.wait", "start", "CI", "R", sec=6,
            payload={"provider": "gitlab", "pipeline_ref": "adw/backend"}),
        rec(8, "ci.wait", "end", "CI", "R", sec=7,
            payload={"status": "success", "polls": 1, "duration": 3}),
        rec(9, "phase", "end", "PB", "R", sec=8, payload={"name": "build", "to_phase": "done"}),
        rec(10, "run", "end", "R", None, sec=9, payload=run_end_payload("done")),
    ]


def test_open_ci_wait_and_gate_report_waiting_while_agent_run_stays_running(
    home, tmp_path  # noqa: F811
):
    """R1: an OPEN ``ci.wait`` and an OPEN ``gate`` report ``waiting``; every
    other open span (here ``agent.run``) still reports ``running``."""
    client, slug, run_id = _detail(tmp_path, open_waiting_lines())
    tree = client.get(f"/api/runs/{slug}/{run_id}").json()["tree"]

    ci_wait = find_node(tree, "ci.wait")
    gate = find_node_by_label(tree, "lint")
    agent = find_node(tree, "agent.run")
    assert ci_wait is not None and gate is not None and agent is not None
    # Both are open and running, but the waiting types report `waiting`, not `running`.
    assert ci_wait["running"] is True and ci_wait["status"] == "waiting"
    assert gate["running"] is True and gate["status"] == "waiting"
    # A working (non-waiting) open span is unaffected.
    assert agent["running"] is True and agent["status"] == "running"


def test_closed_waiting_spans_keep_their_prior_result(home, tmp_path):  # noqa: F811
    """R1: a CLOSED ``gate`` keeps ``passed``/``failed`` and a CLOSED ``ci.wait``
    keeps ``done`` — ``waiting`` is only for the open case."""
    client, slug, run_id = _detail(tmp_path, closed_waiting_lines(), phase="done")
    tree = client.get(f"/api/runs/{slug}/{run_id}").json()["tree"]

    passed_gate = find_node_by_label(tree, "lint")
    failed_gate = find_node_by_label(tree, "test")
    ci_wait = find_node(tree, "ci.wait")
    assert passed_gate["status"] == "passed"
    assert failed_gate["status"] == "failed"
    assert ci_wait["status"] == "done"
    # None of the closed spans is (wrongly) reported as waiting.
    for node in (passed_gate, failed_gate, ci_wait):
        assert node["running"] is False
        assert node["status"] != "waiting"


def test_tree_and_timeline_never_disagree_for_the_same_open_ci_wait(
    home, tmp_path  # noqa: F811
):
    """R1: the same open ``ci.wait`` span the Timeline marks ``waiting`` reports
    ``waiting`` in the tree — the shared ``_WAITING_TYPES`` keeps both surfaces in
    agreement (no second, divergent list of waiting types)."""
    client, slug, run_id = _detail(tmp_path, timeline_lines(running=True))

    tree = client.get(f"/api/runs/{slug}/{run_id}").json()["tree"]
    ci_wait = find_node(tree, "ci.wait")
    assert ci_wait is not None and ci_wait["running"] is True
    assert ci_wait["status"] == "waiting"
    seq = ci_wait["seq"]

    # The Timeline draws the very same span (matched by data-seq) as waiting.
    panel = tab_panel(client.get(f"/runs/{slug}/{run_id}").text, "timeline")
    assert re.search(rf'bar-waiting[^>]*data-seq="{seq}"', panel), panel[:1200]
