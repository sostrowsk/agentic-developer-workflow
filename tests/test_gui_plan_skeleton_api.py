"""RED tests for the plan-skeleton JSON surface — the additive, purely derived
``plan_skeleton`` array on ``GET /api/runs/{repo}/{run_id}``.

Derived from .adw/spec.md (AC1-AC7), .adw/contract.yaml
(RunDetailPlanSkeletonAddition / PlanSkeletonEntry, x-behavior S1-S6) and
.adw/plan.md (B1-B3). The array is a pure projection of the run's ``plan.md`` —
read ONLY through the existing whitelist artifact path — and of the already-loaded
event stream (for the coarse lane status). It is PRESENT exactly when ``plan.md``
yields at least one ``## Workstream:`` section with at least one ``###`` task, and
ABSENT otherwise — an empty list is never forced.

The parser follows EXACTLY two rules (S2_parse): a section is ``## Workstream:
<name>`` up to the next ``##`` heading (or EOF); a task is every ``### `` line in
it, its text taken VERBATIM after the ``### `` prefix — no identifier pattern, no
Markdown parser, no dependency.

RED until ``_run_detail`` hangs the derived ``plan_skeleton`` array on its result.
"""

import os

import pytest
from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


def _real_path(repo):
    return os.path.normpath(str(repo.resolve()))


def _plan_path(repo, run_id=RUN_ID):
    return repo / ".adw" / "runs" / run_id / "plan.md"


def _client(tmp_path, *, lines=None, phase="done", plan=None, run_id=RUN_ID,
            repo_name="repo"):
    """Build the app over one repo (optionally writing ``plan.md``) and return
    (client, slug, repo, run_id)."""
    repo = tmp_path / repo_name
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, run_id, lines if lines is not None else comprehensive_lines(),
              phase=phase)
    if plan is not None:
        _plan_path(repo, run_id).write_text(plan, encoding="utf-8")
    client = TestClient(create_app(repos=[str(repo)]))
    return client, _slug(_real_path(repo)), repo, run_id


def _detail(client, slug, run_id=RUN_ID):
    r = client.get(f"/api/runs/{slug}/{run_id}")
    assert r.status_code == 200, r.status_code
    return r.json()


def _skeleton(client, slug, run_id=RUN_ID):
    return _detail(client, slug, run_id).get("plan_skeleton")


# --- lane-status fixtures (S3_status) -------------------------------------------


def _lane_status_lines(kind, *, lane="backend"):
    """A minimal build run whose ``backend`` lane reaches ``kind``:
    ``done`` (lane end ``completed: true``), ``failed`` (``completed: false``),
    ``no_completed`` (lane end without the key), ``running`` (lane start, no end),
    or ``absent`` (no lane event at all)."""
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Skeleton run")),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
    ]
    if kind != "absent":
        lines.append(rec(3, "lane", "start", "L", "PB", sec=2, lane=lane, payload={
            "name": lane, "branch": f"adw/{lane}", "worktree": "wt",
            "base_sha": None, "ports": {}}))
        if kind in ("done", "failed", "no_completed"):
            end_payload = {"gate_iterations": 1, "fix_cycles": 0}
            if kind == "done":
                end_payload["completed"] = True
            elif kind == "failed":
                end_payload["completed"] = False
            lines.append(rec(4, "lane", "end", "L", "PB", sec=3, lane=lane,
                             payload=end_payload))
    lines.append(rec(9, "run", "end", "R", None, sec=9, payload=run_end_payload("done")))
    return lines


ONE_WS = "## Workstream: backend\n\n### B1 — Parser\n### B2 — Feld\n"


# --- AC1 / E3 / S2_parse: the two parse rules ----------------------------------


def test_heterogeneous_headings_each_yield_one_verbatim_task(home, tmp_path):  # noqa: F811
    """AC1/E3 (mandatory): the heterogeneous heading forms observed across the runs
    (``### B1 — …``, ``### 1. …``, ``### A.1 — …``, ``### Aufgabe A — …``,
    ``### Aufgabe B1 — …``) are NOT filtered — each yields exactly one task whose
    text is the full heading verbatim (only the exact ``### `` prefix removed, no
    split into identifier and title)."""
    plan = (
        "## Workstream: backend\n"
        "### B1 — Parser: Skelett aus plan.md\n"
        "### 1. Additives Feld in der Detail-Antwort\n"
        "### A.1 — Etwas anderes\n"
        "### Aufgabe A — Anzeige neben dem Trace\n"
        "### Aufgabe B1 — Noch mehr\n"
    )
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)
    skel = _skeleton(client, slug)

    assert skel is not None and len(skel) == 1
    assert skel[0]["tasks"] == [
        "B1 — Parser: Skelett aus plan.md",
        "1. Additives Feld in der Detail-Antwort",
        "A.1 — Etwas anderes",
        "Aufgabe A — Anzeige neben dem Trace",
        "Aufgabe B1 — Noch mehr",
    ]


def test_task_text_removes_exactly_one_separator_space(home, tmp_path):  # noqa: F811
    """S2_parse: only the ``### `` marker plus ONE separator space is removed —
    further leading whitespace survives verbatim (no extra trimming)."""
    plan = "## Workstream: backend\n###   two extra leading spaces\n"
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)

    assert _skeleton(client, slug)[0]["tasks"] == ["  two extra leading spaces"]


def test_bare_hashhashhash_yields_no_task(home, tmp_path):  # noqa: F811
    """S2_parse: a line that is exactly ``###`` with no following text is neither a
    section end (it starts with ``###``) nor a task (no ``### `` prefix) — it is
    ignored, and only the real task remains."""
    plan = "## Workstream: backend\n###\n### Real task\n"
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)

    assert _skeleton(client, slug)[0]["tasks"] == ["Real task"]


def test_section_ends_at_next_heading_and_ignores_trailing_tasks(home, tmp_path):  # noqa: F811
    """AC1/S2_parse: the section ends at the next ``##`` heading; a ``###`` line
    AFTER that closing heading is not attributed to the workstream."""
    plan = (
        "## Workstream: backend\n"
        "### inside backend\n"
        "## Notes\n"
        "### after the closing heading\n"
    )
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)
    skel = _skeleton(client, slug)

    assert len(skel) == 1
    assert skel[0]["workstream"] == "backend"
    assert skel[0]["tasks"] == ["inside backend"]


def test_headings_outside_any_section_do_not_count(home, tmp_path):  # noqa: F811
    """AC1/S2_parse: ``###`` lines BEFORE the first ``## Workstream:`` section are
    ignored — only tasks inside a section contribute."""
    plan = (
        "### orphan before any section\n"
        "## Workstream: backend\n"
        "### real task\n"
    )
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)
    skel = _skeleton(client, slug)

    assert len(skel) == 1
    assert skel[0]["tasks"] == ["real task"]


def test_multiple_workstreams_in_document_order(home, tmp_path):  # noqa: F811
    """AC2/S1: one entry per ``## Workstream:`` section with >= 1 task, in
    ``plan.md`` document order; ``<name>`` is the verbatim text after the prefix."""
    plan = (
        "## Workstream: backend\n"
        "### B task\n"
        "## Workstream: frontend\n"
        "### F task\n"
    )
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)
    skel = _skeleton(client, slug)

    assert [e["workstream"] for e in skel] == ["backend", "frontend"]
    assert skel[0]["tasks"] == ["B task"]
    assert skel[1]["tasks"] == ["F task"]


def test_section_without_task_produces_no_entry(home, tmp_path):  # noqa: F811
    """AC2/S1: a ``## Workstream:`` section with no ``###`` task contributes no
    entry at all (never an empty ``tasks`` list)."""
    plan = (
        "## Workstream: empty\n"
        "some prose, no task headings\n"
        "## Workstream: backend\n"
        "### the only task\n"
    )
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)
    skel = _skeleton(client, slug)

    assert [e["workstream"] for e in skel] == ["backend"]
    assert skel[0]["tasks"] == ["the only task"]


# --- AC4 / S3_status: coarse lane-level status ---------------------------------


def test_status_done_when_lane_completed(home, tmp_path):  # noqa: F811
    """AC4/S3: ``done`` exactly when the ``lane`` span named like the workstream has
    a ``lane`` end carrying ``completed: true``."""
    client, slug, _repo, _rid = _client(
        tmp_path, lines=_lane_status_lines("done"), plan=ONE_WS)

    assert _skeleton(client, slug)[0]["status"] == "done"


@pytest.mark.parametrize("kind", ["failed", "no_completed", "running", "absent"])
def test_status_pending_unless_completed(home, tmp_path, kind):  # noqa: F811
    """AC4/S3: every non-completed lane state is ``pending`` — a lane end without
    ``completed: true`` (``false`` or missing), a still-running lane, and a
    not-yet-started lane (no lane node)."""
    client, slug, _repo, _rid = _client(
        tmp_path, lines=_lane_status_lines(kind), phase="build", plan=ONE_WS)

    assert _skeleton(client, slug)[0]["status"] == "pending"


def test_not_started_lane_yields_pending_entry_without_a_lane_node(home, tmp_path):  # noqa: F811
    """AC3/S3: a valid ``backend`` workstream in ``plan.md`` but NO ``backend`` lane
    event still yields its ``pending`` entry — and the derived trace carries no lane
    node (the skeleton is not bound to a trace node, and none is invented)."""
    client, slug, _repo, _rid = _client(
        tmp_path, lines=_lane_status_lines("absent"), phase="build", plan=ONE_WS)
    detail = _detail(client, slug)

    assert detail["plan_skeleton"][0] == {
        "workstream": "backend", "status": "pending",
        "tasks": ["B1 — Parser", "B2 — Feld"],
    }

    def _has_lane(nodes):
        return any(n.get("type") == "lane" or _has_lane(n.get("children") or [])
                   for n in nodes)

    assert not _has_lane(detail["tree"])


# --- AC2 / AC6 / S1: purely additive -------------------------------------------


def test_plan_skeleton_is_purely_additive(home, tmp_path):  # noqa: F811
    """AC2/AC6/S1: the field is a pure addition — with ``plan.md`` removed the WHOLE
    rest of the response is byte-for-byte identical, and ``plan_skeleton`` is the
    only difference. The trace tree and all other fields keep their shape."""
    client, slug, repo, run_id = _client(tmp_path, plan=ONE_WS)
    with_plan = _detail(client, slug, run_id)
    assert "plan_skeleton" in with_plan

    _plan_path(repo, run_id).unlink()                    # plan.md now absent
    without_plan = _detail(client, slug, run_id)

    assert "plan_skeleton" not in without_plan
    stripped = dict(with_plan)
    stripped.pop("plan_skeleton")
    assert stripped == without_plan


# --- AC5 / S4_fallback: robustness ---------------------------------------------


def test_missing_plan_yields_no_skeleton(home, tmp_path):  # noqa: F811
    """AC5/S4: no ``plan.md`` at all -> ``plan_skeleton`` absent, no error."""
    client, slug, _repo, _rid = _client(tmp_path, plan=None)

    assert "plan_skeleton" not in _detail(client, slug)


@pytest.mark.parametrize("plan", [
    "",                                               # empty file
    "# Title\n## Not a workstream\n### task\n",        # no `## Workstream:` section
    "## Workstream: backend\nno task headings here\n",  # section, but no `###` task
], ids=["empty", "no_workstream", "no_task"])
def test_unmatching_plan_yields_no_skeleton(home, tmp_path, plan):  # noqa: F811
    """AC5/S4: an empty ``plan.md``, or one with no ``## Workstream:`` section
    carrying a ``###`` task, yields NO skeleton — the key is absent (never a forced
    empty list), no 5xx, and the rest of the response is intact."""
    client, slug, _repo, _rid = _client(tmp_path, plan=plan)
    detail = _detail(client, slug)                       # 200, not 5xx

    assert "plan_skeleton" not in detail
    assert "tree" in detail and "run" in detail          # rest of the response intact


def test_unreadable_plan_is_treated_as_absent(home, tmp_path):  # noqa: F811
    """AC5/S4: a ``plan.md`` that is not a readable file (here a directory in its
    place) counts as absent — no skeleton, no 5xx."""
    client, slug, repo, run_id = _client(tmp_path, plan=None)
    _plan_path(repo, run_id).mkdir(parents=True, exist_ok=True)   # not a file

    assert "plan_skeleton" not in _detail(client, slug)


def test_boundary_escaping_symlink_plan_is_absent(home, tmp_path):  # noqa: F811
    """AC5/AC7/S4/E4: a ``plan.md`` that is a symlink escaping the run-directory
    boundary is absent per the artifact path — its target is never read, and no
    skeleton appears."""
    outside = tmp_path / "outside_plan.md"
    outside.write_text("## Workstream: backend\n### leaked task\n", encoding="utf-8")
    client, slug, repo, run_id = _client(tmp_path, plan=None)
    link = _plan_path(repo, run_id)
    link.symlink_to(outside)

    detail = _detail(client, slug)
    assert "plan_skeleton" not in detail
    # The target's content never reaches the response.
    assert "leaked task" not in str(detail)
