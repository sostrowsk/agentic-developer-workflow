"""A corrupt event payload must never turn a read into a 5xx.

`adw/gui/app.py` established `_mapping_payload()` for exactly this: an event whose
`payload` is a truthy non-mapping (list, string, number) must never reach `.get`.
0.21.2 put the guard into the change-scope helpers, but the projections behind the
run LIST, the run DETAIL and the server-rendered detail page still read payloads
unguarded — so a single corrupt run took down all of them, the list including every
healthy run beside it.

These tests pin the whole read surface at once: every event type, every kind, every
non-mapping payload shape, in one run. They are endpoint-level on purpose — the
guarantee is "no 5xx", not a particular helper's signature.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_start_payload,
    simple_run_lines,
    write_run,
)

RUN_ID = "aaaa9999"
HEALTHY_ID = "bbbb8888"

# The three truthy non-mapping shapes JSON can produce. `None` and `{}` already
# fall through the old `or {}` idiom — these are the ones that raised.
BAD_PAYLOADS = (["nope"], "text", 42)

# Every event type the projections branch on.
EVENT_TYPES = (
    "run", "phase", "lane", "agent.run", "snapshot", "approval", "state.saved",
    "agent.tool.call", "agent.tool.result", "agent.message", "artifact", "gate",
    "escalation", "ci.wait",
)

# Span types the tree aggregates over — their `end_payload` feeds `_node_status`,
# `_aggregate_outcome` and `_subtree_cost`, which the flat list above never reached.
SPAN_ONLY_TYPES = ("round", "codex")


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _corrupt_run_lines():
    """A run whose every event after the opening `run`/`start` carries a
    non-mapping payload — each type crossed with each kind and each bad shape.

    Every span gets its OWN id. Reusing one id would make `build_tree()` collapse
    the lot into a single node whose type is whatever came last, and the per-span
    `end_payload` consumers (`_node_status`, `_aggregate_outcome`, `_subtree_cost`)
    would never see a corrupt payload at all — a fixture that passes while the bug
    is still there.
    """
    lines = [rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("corrupt"))]
    seq = 2
    for type_ in EVENT_TYPES + SPAN_ONLY_TYPES:
        for bad in BAD_PAYLOADS:
            span = f"s{seq}"
            # A properly PAIRED span: start and end both corrupt, so the node is
            # closed and every end_payload consumer runs against it.
            lines.append(
                rec(seq, type_, "start", span, "R", sec=seq, lane="backend", payload=bad)
            )
            seq += 1
            lines.append(
                rec(seq, type_, "end", span, "R", sec=seq, lane="backend", payload=bad)
            )
            seq += 1
            # An OPEN span (start without end) — exercises the `running` branch.
            lines.append(
                rec(seq, type_, "start", f"o{seq}", "R", sec=seq, lane="backend", payload=bad)
            )
            seq += 1
            # A point event of the same type.
            lines.append(
                rec(seq, type_, "point", f"p{seq}", "R", sec=seq, lane="backend", payload=bad)
            )
            seq += 1
    return lines


def _client(repo):
    return TestClient(create_app(repos=[str(repo)]))


def test_run_list_survives_a_corrupt_run(home, tmp_path):  # noqa: F811
    """The list is the worst case: one corrupt run must not hide the healthy ones.
    Before the fix `_summary` raised on `payload.totals` and the whole endpoint 5xx'd."""
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _corrupt_run_lines(), phase="done")
    write_run(repo, HEALTHY_ID, simple_run_lines("healthy run"), phase="done")

    resp = _client(repo).get("/api/runs")
    assert resp.status_code == 200, resp.text

    ids = [e["run_id"] for e in resp.json()]
    assert HEALTHY_ID in ids, "the healthy run must still be listed"
    assert RUN_ID in ids, "the corrupt run is degraded, not dropped"


def test_run_detail_survives_a_corrupt_run(home, tmp_path):  # noqa: F811
    """The detail projections (phase bar, approval state, tool names, token totals,
    timeline) each read payloads; none may raise."""
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _corrupt_run_lines(), phase="done")

    resp = _client(repo).get(f"/api/runs/{_slug_for(repo)}/{RUN_ID}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["run_id"] == RUN_ID


def test_detail_page_renders_for_a_corrupt_run(home, tmp_path):  # noqa: F811
    """The server-rendered page builds the trace tree and its panes from the same
    events — it must render, not 500."""
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _corrupt_run_lines(), phase="done")

    resp = _client(repo).get(f"/runs/{_slug_for(repo)}/{RUN_ID}")
    assert resp.status_code == 200, resp.text


def test_events_endpoint_survives_a_corrupt_run(home, tmp_path):  # noqa: F811
    """The raw events route already tolerated this; pinned so it stays that way."""
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _corrupt_run_lines(), phase="done")

    resp = _client(repo).get(f"/api/runs/{_slug_for(repo)}/{RUN_ID}/events")
    assert resp.status_code == 200, resp.text


def test_non_mapping_totals_do_not_break_the_summary(home, tmp_path):  # noqa: F811
    """The nested case: `payload` IS a mapping, but `payload.totals` is not. Both
    `_summary` and the timeline derive `duration`/`cost` from it with `.get`."""
    repo = tmp_path / "repo"
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("bad totals")),
        rec(2, "run", "end", "R", None, sec=9, payload={"status": "done", "totals": ["nope"]}),
    ]
    write_run(repo, RUN_ID, lines, phase="done")

    assert _client(repo).get("/api/runs").status_code == 200
    detail = _client(repo).get(f"/api/runs/{_slug_for(repo)}/{RUN_ID}")
    assert detail.status_code == 200, detail.text
