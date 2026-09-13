"""RED tests for A6 / AC 13 — ``phases[*].start`` and ``phases[*].end`` are added
additively to ``GET /api/runs/{repo}/{run_id}``.

Each ``phases`` entry gains ``start`` and ``end`` — the ISO-8601 timestamps
``_phase_bar`` already derives internally, taken over verbatim, or ``null``. The
addition is purely additive: ``name``, ``status`` and ``duration`` stay word for
word, ``duration`` is NOT recomputed from start/end, an open phase's end stays
``null`` (the page-build instant is a presentation rule of the timeline, never an
API phase end), and the structure of ``tree`` is unchanged.

Derived from .adw/spec.md (AC 13), .adw/contract.yaml (Phase schema; x-adw-invariants)
and .adw/plan.md (B1). RED until ``_phase_bar`` emits the two fields.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"

# spec (0→2 s) and plan (3→7 s) run and end; build starts but never ends (active).
_LINES = [
    rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Phase bounds")),
    rec(2, "phase", "start", "S", "R", sec=0, payload={"name": "spec", "from_phase": "spec"}),
    rec(3, "phase", "end", "S", "R", sec=2, payload={"name": "spec", "to_phase": "plan"}),
    rec(4, "phase", "start", "PL", "R", sec=3, payload={"name": "plan", "from_phase": "plan"}),
    rec(5, "phase", "end", "PL", "R", sec=7, payload={"name": "plan", "to_phase": "build"}),
    rec(6, "phase", "start", "B", "R", sec=8, payload={"name": "build", "from_phase": "build"}),
]


def _detail(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, RUN_ID, _LINES, phase="build")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug(os.path.normpath(str(repo.resolve())))
    return client.get(f"/api/runs/{slug}/{RUN_ID}").json()


def test_each_phase_entry_carries_start_and_end_and_keeps_duration(home, tmp_path):  # noqa: F811
    """AC 13: every ``phases`` entry has ``start`` and ``end`` alongside the
    unchanged ``name``/``status``/``duration``. The started phases carry their
    verbatim event-log start; the open ``build`` phase's end stays ``null`` (no
    synthetic page-build end); a phase that never started has both ``null``. And
    ``duration`` stays the value ``_phase_bar`` already derived — it is NOT
    re-computed from start/end, and the open phase keeps a ``null`` duration."""
    detail = _detail(tmp_path)
    by_name = {p["name"]: p for p in detail["phases"]}

    for entry in detail["phases"]:
        assert set(entry) >= {"name", "status", "duration", "start", "end"}, entry

    # Verbatim event-log timestamps (the reader's ISO strings), not recomputed.
    assert by_name["spec"]["start"] == "2026-08-05T14:00:00.000Z"
    assert by_name["spec"]["end"] == "2026-08-05T14:00:02.000Z"
    assert by_name["plan"]["start"] == "2026-08-05T14:00:03.000Z"

    # The open active phase: a start, but end stays null (page-build instant is a
    # display rule of the timeline, never an API phase end).
    assert by_name["build"]["status"] == "active"
    assert by_name["build"]["start"] == "2026-08-05T14:00:08.000Z"
    assert by_name["build"]["end"] is None

    # A phase that never ran → both null, no synthetic start.
    assert by_name["integration"]["start"] is None
    assert by_name["integration"]["end"] is None

    # duration stays derived (closed-span epoch delta), never re-derived from the
    # new fields; the open phase keeps a null duration (no elapsed-to-now).
    assert by_name["spec"]["duration"] == 2.0
    assert by_name["plan"]["duration"] == 4.0
    assert by_name["build"]["duration"] is None


def test_start_end_addition_leaves_the_tree_structure_unchanged(home, tmp_path):  # noqa: F811
    """AC 13 / x-adw-invariants: the addition touches only ``phases``. ``tree`` is
    still present and structurally intact (a list of nodes each with the usual
    keys), and the seven phases keep their pinned order and count."""
    detail = _detail(tmp_path)

    assert [p["name"] for p in detail["phases"]] == [
        "spec", "plan", "build", "integration", "codex_review", "final_review", "ci"
    ]
    assert isinstance(detail["tree"], list) and detail["tree"], "tree missing/empty"
    for node in detail["tree"]:
        assert {"type", "seq", "children"} <= set(node), node
