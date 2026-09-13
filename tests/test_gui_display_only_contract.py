"""Regression test for AC 12 — this purely presentational feature does not touch
the JSON API.

The brief re-orders the run-detail page, collapses two summaries, moves the
timeline label and states the tree size — all render-side. The JSON routes
``GET /api/runs`` and ``GET /api/runs/{repo}/{run_id}`` must stay unchanged in every
field, type and value: no new route, no new query parameter, and — the binding
architecture fact of ``.adw/plan.md`` — the two new summary aggregates (A2) and the
new timeline label chrome (A4) are RENDER-context values only, never written into
``detail`` and therefore never into the API.

The regression is fixed two ways: the detail response is IDEMPOTENT across an
intervening HTML render (which computes the summaries), and the top-level key set is
EXACTLY the known one — so a summary/timeline field accidentally hung on ``detail``
fails the test.

Derived from .adw/spec.md (AC 12), .adw/contract.yaml (/api/runs,
/api/runs/{repo}/{run_id}, x-adw-invariants) and .adw/plan.md (B1, B2.10). Green
from the start (an invariant), and it must stay green through the change.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    build_diff_run,
    home,
)

RUN_ID = "aaaa1111"

# The exact top-level keys of the run-detail JSON today (``_run_detail``): the
# always-present fields plus the additive ``change_scope``, plus the conditional
# ``plan_skeleton`` (present here — a plan.md is written) and no ``recovery`` (the
# run is done). No summary/timeline render data is allowed to join them.
EXPECTED_DETAIL_KEYS = {
    "run", "phases", "tree", "latest_context", "problems", "raw",
    "change_scope", "plan_skeleton",
}

PLAN_MD = "## Workstream: backend\n### B1 — Parser\n### B2 — Guard\n"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _fixture(tmp_path):
    """A finished run with a real diff (change_scope populated) AND a plan.md
    (plan_skeleton populated), so the render path computes both A2 summaries."""
    info = build_diff_run(tmp_path / "repo", RUN_ID)
    repo = info["repo"]
    (repo / ".adw" / "runs" / RUN_ID / "plan.md").write_text(PLAN_MD, encoding="utf-8")
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo)


def test_api_detail_is_unchanged_and_carries_no_summary_or_timeline_data(home, tmp_path):  # noqa: F811,E501
    """AC 12: the run-detail JSON has EXACTLY the known top-level keys — the A2
    summary aggregates and the A4 timeline chrome never appear in it — and an
    intervening HTML render (which computes them) leaves the JSON byte-for-byte
    identical. ``change_scope`` keeps its pinned shape."""
    client, slug = _fixture(tmp_path)
    url = f"/api/runs/{slug}/{RUN_ID}"

    before = client.get(url)
    assert before.status_code == 200
    j1 = before.json()

    assert set(j1) == EXPECTED_DETAIL_KEYS, set(j1) ^ EXPECTED_DETAIL_KEYS
    assert "timeline" not in j1
    assert set(j1["change_scope"]) == {"lanes", "declared_scope"}
    assert j1["plan_skeleton"], "the plan skeleton must be populated for this fixture"

    # An HTML render sits in between — it must not mutate any cached/derived state.
    assert client.get(f"/runs/{slug}/{RUN_ID}").status_code == 200

    j2 = client.get(url).json()
    assert j2 == j1, "the run-detail JSON changed across an HTML render"


def test_api_runs_list_is_unchanged_across_an_html_render(home, tmp_path):  # noqa: F811
    """AC 12: the run-list JSON is identical before and after a detail HTML render —
    the feature adds no field, no entry and no reordering to ``GET /api/runs``."""
    client, slug = _fixture(tmp_path)

    j1 = client.get("/api/runs").json()
    assert j1, "the fixture repo must list its run"

    assert client.get(f"/runs/{slug}/{RUN_ID}").status_code == 200

    j2 = client.get("/api/runs").json()
    assert j2 == j1, "the run-list JSON changed across an HTML render"
