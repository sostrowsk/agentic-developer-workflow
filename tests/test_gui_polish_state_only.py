"""RED tests for Aufgabe G — runs with state.json but no events.jsonl.

Runs that predate instrumentation (e.g. 8f8dc4ff, e680e005) have a ``state.json``
but no ``events.jsonl``. They must appear in the run list with the metadata from
their state and a clear "no trace" indication, and opening their detail must never
error.

Derived from .adw/spec.md (G1–G3), .adw/contract.yaml (x-adw-runs-without-log)
and .adw/plan.md. Tested with FastAPI's TestClient. The slug is derived the same
way the resolver does, so the detail routes are addressable regardless of listing.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    write_state_only_run,
)

RUN_ID = "8f8dc4ff"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _repo_with_state_only_run(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_state_only_run(repo, RUN_ID, phase="done", issue="Legacy run without trace")
    return repo


def test_state_only_run_is_listed_with_state_metadata(home, tmp_path):  # noqa: F811
    """G1: the run appears in /api/runs with the metadata available from its state
    (run_id, phase, issue) and carries no trace (event_count == 0)."""
    repo = _repo_with_state_only_run(tmp_path)
    client = TestClient(create_app(repos=[str(repo)]))

    data = client.get("/api/runs").json()
    entry = next((e for e in data if e.get("run_id") == RUN_ID), None)
    assert entry is not None
    assert entry["phase"] == "done"                      # from state.json
    assert entry["issue"] == "Legacy run without trace"  # from state.json
    assert entry["event_count"] == 0                     # no events log


def test_state_only_run_html_list_shows_no_trace_hint(home, tmp_path):  # noqa: F811
    """G2: the list page shows the run and a clear indication that no trace exists
    (how the hint is embedded is not contractual — the word 'trace' surfaces it)."""
    repo = _repo_with_state_only_run(tmp_path)
    client = TestClient(create_app(repos=[str(repo)]))

    html = client.get("/").text
    assert RUN_ID in html
    assert "trace" in html.lower()


def test_state_only_run_detail_never_errors(home, tmp_path):  # noqa: F811
    """G3: opening the detail of such a run never errors; the events surface
    answers controlled (empty list), not a 5xx."""
    repo = _repo_with_state_only_run(tmp_path)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)

    assert client.get(f"/runs/{slug}/{RUN_ID}").status_code == 200
    assert client.get(f"/api/runs/{slug}/{RUN_ID}").status_code == 200

    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events")
    assert events.status_code == 200
    assert events.json() == []
