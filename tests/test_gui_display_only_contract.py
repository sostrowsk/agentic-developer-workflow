"""Regression test for AC 12 — this purely presentational feature does not touch
the JSON API, checked against an INDEPENDENT pre-feature baseline.

The brief re-orders the run-detail page, collapses two summaries, moves the
timeline label and states the tree size — all render-side. The JSON routes
``GET /api/runs`` and ``GET /api/runs/{repo}/{run_id}`` must stay unchanged in every
field, type and value: no new route, no new query parameter, and — the binding
architecture fact of ``.adw/plan.md`` — the two new summary aggregates (A2) and the
new timeline label chrome (A4) are RENDER-context values only, never written into
``detail`` and therefore never into the API.

Independent baseline (plan B1 / Codex P2): the expected responses are captured from
the **pre-feature** revision ``cc201f1`` (release 0.23.0, the merged base this brief
builds on) into ``tests/fixtures/contract_baseline_api.json``, from a deterministic
event fixture (``contract_baseline_events.json``) at a fixed evaluation time. The
expectations are therefore NOT produced by the code under test — a *consistent*
regression (a nested field/type/value/order that changed both before and after the
HTML render) still diverges from the frozen baseline. The only normalization is the
environment-dependent repository slug, which is not a feature concern; no other
field is masked. Idempotence across an intervening HTML render (which computes the
A2/A4 chrome) is asserted on top.

To refresh the baseline after a *deliberate* API change, re-run the capture against
the then-current merged base (see the repo's build notes) — never against this
feature branch.

Derived from .adw/spec.md (AC 12), .adw/contract.yaml (/api/runs,
/api/runs/{repo}/{run_id}, x-adw-invariants) and .adw/plan.md (B1, B2.10).
"""

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import home  # noqa: F401 — home used as a fixture

_FIXTURES = Path(__file__).parent / "fixtures"
_EVENTS = json.loads((_FIXTURES / "contract_baseline_events.json").read_text())
_GOLDEN = json.loads((_FIXTURES / "contract_baseline_api.json").read_text())
RUN_ID = _EVENTS["run_id"]

# The pre-feature top-level detail keys (from the cc201f1 golden). The A2 summary
# aggregates / A4 timeline chrome must never join them — an exact golden match already
# enforces this; the explicit set makes a leak legible.
EXPECTED_DETAIL_KEYS = set(_GOLDEN["detail"])


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _build(tmp_path):
    """Rebuild the deterministic baseline run (same events + plan.md the golden was
    captured from) and serve it through the CURRENT app."""
    repo = tmp_path / "repo"
    run_dir = repo / ".adw" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    body = "".join(json.dumps(e) + "\n" for e in _EVENTS["events"])
    (run_dir / "events.jsonl").write_text(body)
    (run_dir / "plan.md").write_text(_EVENTS["plan_md"])
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo)


def _norm(obj, slug):
    """Normalize only the environment-dependent repository slug (never a feature
    field) so the golden — captured under a different temp path — compares equal."""
    return json.loads(json.dumps(obj).replace(slug, "<REPO>"))


def test_api_detail_matches_the_pre_feature_baseline_before_and_after_render(home, tmp_path):  # noqa: F811,E501
    """AC 12: the run-detail JSON equals the cc201f1 baseline in every field, type,
    value and list order — including ``change_scope``, ``plan_skeleton``, ``tree``,
    ``phases``, ``raw``, ``latest_context``, ``problems`` and the Brief-2 metrics — and
    an intervening HTML render (which computes the A2/A4 chrome) leaves it identical.
    No A2/A4 render value leaks into ``detail``."""
    client, slug = _build(tmp_path)
    url = f"/api/runs/{slug}/{RUN_ID}"

    before = client.get(url)
    assert before.status_code == 200
    got = _norm(before.json(), slug)

    assert set(got) == EXPECTED_DETAIL_KEYS, set(got) ^ EXPECTED_DETAIL_KEYS
    assert "timeline" not in got
    assert got == _GOLDEN["detail"], "run-detail JSON diverged from the cc201f1 baseline"

    # An HTML render sits in between — it must not mutate any derived/cached state.
    assert client.get(f"/runs/{slug}/{RUN_ID}").status_code == 200

    after = _norm(client.get(url).json(), slug)
    assert after == _GOLDEN["detail"], "run-detail JSON changed across an HTML render"


def test_api_runs_list_matches_the_pre_feature_baseline_before_and_after_render(home, tmp_path):  # noqa: F811,E501
    """AC 12: the run-list JSON equals the cc201f1 baseline (fields, types, values,
    entry order) and is identical before and after a detail HTML render — the feature
    adds no field, no entry and no reordering to ``GET /api/runs``."""
    client, slug = _build(tmp_path)

    before = _norm(client.get("/api/runs").json(), slug)
    assert before == _GOLDEN["runs"], "run-list JSON diverged from the cc201f1 baseline"

    assert client.get(f"/runs/{slug}/{RUN_ID}").status_code == 200

    after = _norm(client.get("/api/runs").json(), slug)
    assert after == _GOLDEN["runs"], "run-list JSON changed across an HTML render"
