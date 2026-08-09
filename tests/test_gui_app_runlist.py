"""RED tests for Aufgabe B — the run list (`GET /api/runs` and `GET /`).

Derived from .adw/spec.md (AC 9–11), .adw/contract.yaml (RunSummary / MissingRepo,
x-adw-template-behavior.run_list) and GUI-SPEC §7.2 A. Tested against fixture logs
with FastAPI's TestClient. RED until ``adw.gui.app.create_app`` exists.
"""

import shutil

from adw.gui.app import create_app
from fastapi.testclient import TestClient

from adw.gui.registry import register_repo
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    simple_run_lines,
    write_run,
)

LONG_ISSUE = "Refactor the persistence layer and migrate every legacy caller to the new API"


def _client(repos):
    return TestClient(create_app(repos=[str(p) for p in repos]))


def _by_id(entries, run_id):
    return next((e for e in entries if e.get("run_id") == run_id), None)


def test_api_runs_lists_every_field_with_running_first(home, tmp_path):  # noqa: F811
    """AC 9: each run carries run_id, repo (slug), repo_exists, issue (truncated),
    phase, status, start, duration, cost and event_count; running runs come
    first."""
    repo = tmp_path / "repoA"
    repo.mkdir()
    fin_lines = simple_run_lines(LONG_ISSUE, ended=True, cost=0.0123, duration=12.0)
    write_run(repo, "aaaa1111", fin_lines, phase="done", issue=LONG_ISSUE)
    write_run(repo, "bbbb2222", simple_run_lines("Running now", ended=False),
              phase="build", issue="Running now")

    client = _client([repo])
    data = client.get("/api/runs").json()
    assert isinstance(data, list)

    fin = _by_id(data, "aaaa1111")
    run = _by_id(data, "bbbb2222")
    assert fin is not None and run is not None

    for key in ("run_id", "repo", "repo_exists", "issue", "phase",
                "status", "start", "duration", "cost", "event_count"):
        assert key in fin, key

    assert fin["repo"] == run["repo"]  # same stable slug for both runs of the repo
    assert isinstance(fin["repo"], str) and "/" not in fin["repo"]
    assert fin["repo_exists"] is True
    assert fin["issue"][:20] == LONG_ISSUE[:20]  # truncated but a faithful prefix
    assert fin["phase"] == "done" and fin["status"] == "done"
    assert fin["start"] is not None
    assert fin["duration"] == 12.0 and fin["cost"] == 0.0123
    assert fin["event_count"] == 4

    assert run["status"] == "running"
    assert run["event_count"] == 2

    # Running runs precede completed ones.
    assert data.index(run) < data.index(fin)


def test_api_runs_absent_values_are_null_not_errors(home, tmp_path):  # noqa: F811
    """AC 11: values absent from the log (a running run has no totals) are null,
    never an error."""
    repo = tmp_path / "repoA"
    repo.mkdir()
    write_run(repo, "cccc3333", simple_run_lines("Dry run no usage", ended=False),
              phase="build", issue="Dry run no usage")
    entry = _by_id(_client([repo]).get("/api/runs").json(), "cccc3333")
    assert entry["duration"] is None
    assert entry["cost"] is None


def test_api_runs_missing_repo_becomes_placeholder_without_run_fields(home, tmp_path):  # noqa: F811
    """AC 10: a registered repo whose path is gone contributes exactly one
    placeholder (repo, repo_exists=false, hint) with no run fields, and never
    fails the response; other repos stay listed normally."""
    good = tmp_path / "good"
    good.mkdir()
    write_run(good, "dddd4444", simple_run_lines("Alive", ended=True), phase="done", issue="Alive")

    gone = tmp_path / "gone"
    gone.mkdir()
    gone_slug = register_repo(gone).slug  # in the (isolated) registry
    shutil.rmtree(gone)

    # The app resolves registry repos ∪ --repo repos; the good repo is passed ad hoc.
    client = TestClient(create_app(repos=[str(good)]))
    data = client.get("/api/runs").json()

    placeholder = next((e for e in data if e.get("repo") == gone_slug), None)
    assert placeholder is not None
    assert placeholder["repo_exists"] is False
    assert isinstance(placeholder.get("hint"), str) and placeholder["hint"]
    for forbidden in ("run_id", "issue", "phase", "status", "duration", "cost", "event_count"):
        assert forbidden not in placeholder

    # The reachable repo is still listed with its run.
    assert any(e.get("run_id") == "dddd4444" for e in data)


def test_run_list_html_mirrors_the_json_and_shows_missing_repo(home, tmp_path):  # noqa: F811
    """AC 9/10: GET / renders the same data base server-side (English), links runs
    by slug + run_id, and shows the unreachable repo as a visible placeholder
    without erroring."""
    good = tmp_path / "good"
    good.mkdir()
    write_run(good, "eeee5555", simple_run_lines("Alive", ended=True), phase="done", issue="Alive")

    gone = tmp_path / "gone"
    gone.mkdir()
    gone_slug = register_repo(gone).slug
    shutil.rmtree(gone)

    client = TestClient(create_app(repos=[str(good)]))
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "eeee5555" in html  # the run appears
    assert gone_slug in html   # the missing repo is still visible (greyed placeholder)
    assert "http://" not in html and "https://" not in html
