"""Contract regression for Brief 4 — the purely operational feature does not touch
the JSON API (AC 13).

This brief adds keyboard operability, a focus indicator, the tab pattern and a
machine-readable selection — all render/behaviour side. The JSON routes ``GET
/api/runs`` and ``GET /api/runs/{repo}/{run_id}`` must stay unchanged in every field,
type and value: no new route, no new query parameter, and no keyboard/focus/selection
state written into the response. Complements the frozen pre-feature baseline of
``tests/test_gui_display_only_contract.py`` (which pins the exact values against the
cc201f1 golden): here the angle is FEATURE-SPECIFIC — for a run with both a trace tree
and a timeline (the surfaces this brief makes operable), the two responses are
byte-identical across an intervening HTML render (which is where any keyboard/tab
chrome would be computed) and carry no operability field.

Derived from .adw/spec.md (AC 13), .adw/contract.yaml (/api/runs,
/api/runs/{repo}/{run_id}, x-adw-invariants) and .adw/plan.md (B2.13).
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    timeline_lines,
    write_run,
)

RUN_ID = "aaaa1111"

# Substrings no top-level detail key may contain — this feature adds no operability
# field to the JSON (it lives only in markup/behaviour).
_FORBIDDEN_KEY_MARKERS = ("tab", "focus", "select", "keyboard", "aria", "tabindex")


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _client(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, timeline_lines(), phase="done")
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo)


def test_json_api_unchanged_by_the_operability_feature(home, tmp_path):  # noqa: F811
    """AC 13: for a run with a tree AND a timeline, ``GET /api/runs`` and ``GET
    /api/runs/{repo}/{run_id}`` are identical before and after an HTML render, and the
    detail payload carries no keyboard/focus/selection/tab field."""
    client, slug = _client(tmp_path)
    detail_url = f"/api/runs/{slug}/{RUN_ID}"

    runs_before = client.get("/api/runs").json()
    detail_before = client.get(detail_url).json()
    assert isinstance(detail_before, dict)

    # No operability state joined the detail payload (top-level keys).
    for key in detail_before:
        low = key.lower()
        assert not any(marker in low for marker in _FORBIDDEN_KEY_MARKERS), (
            f"an operability-related key leaked into the detail JSON: {key!r}"
        )
    assert "timeline" not in detail_before, "render-only timeline chrome leaked into the API"

    # An HTML render sits in between — the JSON must not move.
    assert client.get(f"/runs/{slug}/{RUN_ID}").status_code == 200

    assert client.get("/api/runs").json() == runs_before, "the run list changed across a render"
    assert client.get(detail_url).json() == detail_before, "the run detail changed across a render"
