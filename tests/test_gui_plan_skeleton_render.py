"""RED tests for the plan-skeleton RENDERING and i18n — the observable rendering on
``GET /runs/{repo}/{run_id}`` (HTML) and the bilingual chrome labels of
``adw/gui/i18n.py``.

Derived from .adw/spec.md (AC3, AC7), .adw/contract.yaml (S5_render, S6_readonly)
and .adw/plan.md (B4, B5). When ``plan_skeleton`` is present the run detail renders,
per entry, a READ-ONLY task list beside/above the trace of the lane with the same
name, with a coarse ``pending``/``done`` marker, so "done" (trace) and "planned"
(skeleton) sit in one view. A not-yet-started lane still shows its ``pending``
skeleton without an artificial trace node. No ``plan_skeleton`` -> no list, no empty
box. Task texts are CONTENT and are not translated.

Markup/CSS are not part of the contract; these tests pin only the observable hooks
this workstream introduces (``data-plan-skeleton``, ``data-skeleton-status``) and
the rendered task/label text.

RED until the run-detail template renders the skeleton.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.i18n import CATALOG
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    rec,
    run_end_payload,
    run_start_payload,
    tab_panel,
    write_run,
)

RUN_ID = "aaaa1111"

# The chrome-label keys this feature adds to BOTH language blocks (AC3/B5).
SKELETON_LABEL_KEYS = [
    "plan_skeleton_title",
    "plan_skeleton_pending",
    "plan_skeleton_done",
]

DONE_PLAN = "## Workstream: backend\n### B1 — Parser\n### B2 — Additives Feld\n"


def _real_path(repo):
    return os.path.normpath(str(repo.resolve()))


def _plan_path(repo, run_id=RUN_ID):
    return repo / ".adw" / "runs" / run_id / "plan.md"


def _client(tmp_path, *, lines=None, phase="done", plan=None, repo_name="repo"):
    repo = tmp_path / repo_name
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, RUN_ID, lines if lines is not None else comprehensive_lines(),
              phase=phase)
    if plan is not None:
        _plan_path(repo).write_text(plan, encoding="utf-8")
    client = TestClient(create_app(repos=[str(repo)]))
    return client, _slug(_real_path(repo)), repo


def _page(client, slug, **params):
    r = client.get(f"/runs/{slug}/{RUN_ID}", params=params or None)
    assert r.status_code == 200, r.status_code
    return r.text


def _no_lane_lines():
    """A run in its ``build`` phase with NO ``backend`` lane event, so the lane has
    no trace node yet."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Not started")),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
    ]


def _running_lane_lines():
    """A build lane that has started but not ended (``pending``)."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Running lane")),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "lane", "start", "L", "PB", sec=2, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
    ]


# --- AC3 / S5_render: the skeleton sits in the Trace view ------------------------


def test_skeleton_renders_in_trace_view_with_done_marker(home, tmp_path):  # noqa: F811
    """AC3/S5: with a completed ``backend`` lane the skeleton renders inside the
    Trace tab panel — beside/above the trace — with a ``done`` marker and every task
    text verbatim. The trace tree itself is still present (unchanged)."""
    client, slug, _repo = _client(tmp_path, lines=comprehensive_lines(), plan=DONE_PLAN)
    html = _page(client, slug)

    assert html.count("data-plan-skeleton") == 1
    trace = tab_panel(html, "trace")
    assert "data-plan-skeleton" in trace                 # placed within the Trace view
    assert 'data-skeleton-status="done"' in trace
    assert "B1 — Parser" in trace and "B2 — Additives Feld" in trace
    assert "backend" in trace
    # The trace tree is unchanged — the real lane node is still rendered.
    assert "data-tree-entry" in trace


def test_skeleton_shows_pending_marker_for_running_lane(home, tmp_path):  # noqa: F811
    """AC3/AC4/S5: while the lane is still running the marker is ``pending``."""
    client, slug, _repo = _client(
        tmp_path, lines=_running_lane_lines(), phase="build", plan=DONE_PLAN)
    trace = tab_panel(_page(client, slug), "trace")

    assert 'data-skeleton-status="pending"' in trace
    assert 'data-skeleton-status="done"' not in trace


def test_not_started_lane_skeleton_visible_without_artificial_node(home, tmp_path):  # noqa: F811
    """AC3/S5 (mandatory): a valid ``backend`` workstream but NO ``backend`` lane
    event — the ``pending`` skeleton is visible, and no empty/artificial lane trace
    node is created (there is no ``lane`` node marker anywhere in the page)."""
    client, slug, _repo = _client(
        tmp_path, lines=_no_lane_lines(), phase="build", plan=DONE_PLAN)
    html = _page(client, slug)

    assert "data-plan-skeleton" in html
    trace = tab_panel(html, "trace")
    assert 'data-skeleton-status="pending"' in trace
    assert "B1 — Parser" in trace
    # No artificial lane node was invented to hang the skeleton on.
    assert "node-lane" not in html and "pane-lane" not in html


def test_no_skeleton_without_plan(home, tmp_path):  # noqa: F811
    """AC5/S5: no ``plan.md`` -> no skeleton container at all (no empty box)."""
    client, slug, _repo = _client(tmp_path, lines=comprehensive_lines(), plan=None)

    assert "data-plan-skeleton" not in _page(client, slug)


# --- AC7 / S6_readonly: pure display, content untranslated ----------------------


def test_skeleton_is_read_only_and_task_text_untranslated(home, tmp_path):  # noqa: F811
    """AC7/S6: the skeleton is pure display — no form, button or checkbox to check a
    task off — and the task texts are CONTENT, rendered identically in both GUI
    languages (only the chrome differs)."""
    client, slug, _repo = _client(tmp_path, lines=comprehensive_lines(), plan=DONE_PLAN)
    en = tab_panel(_page(client, slug, lang="en"), "trace")
    de = tab_panel(_page(client, slug, lang="de"), "trace")

    # Isolate the skeleton region roughly by its marker; assert no interactive affordance.
    start = en.find("data-plan-skeleton")
    region = en[en.rfind("<", 0, start):start + 400]
    assert "<form" not in region
    assert "<button" not in region
    assert 'type="checkbox"' not in region

    # Task text is content — verbatim and identical regardless of language.
    for task in ("B1 — Parser", "B2 — Additives Feld"):
        assert task in en and task in de


def test_skeleton_labels_are_bilingual(home, tmp_path):  # noqa: F811
    """AC3/B5: every skeleton chrome label exists in BOTH language blocks, non-empty,
    with identical key sets; the German title is not a mere copy of the English."""
    for key in SKELETON_LABEL_KEYS:
        assert key in CATALOG["en"] and CATALOG["en"][key].strip()
        assert key in CATALOG["de"] and CATALOG["de"][key].strip()
    assert CATALOG["en"]["plan_skeleton_title"] != CATALOG["de"]["plan_skeleton_title"]
