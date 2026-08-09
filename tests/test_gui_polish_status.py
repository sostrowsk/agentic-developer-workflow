"""RED tests for Aufgabe A — the run status is derived from the LAST run span.

A real gated run is several CLI commands (``adw run`` + ``adw approve`` + ...),
hence several ``run`` spans in the same ``events.jsonl``. The reported status must
be that of the last span, identical in the run list and the run detail; if the
last span has no ``end`` the run is ``running``.

Derived from .adw/spec.md (A1–A4), .adw/contract.yaml (x-adw-status-semantics)
and .adw/plan.md. Tested against fixture logs with FastAPI's TestClient.
"""

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    multi_run_span_lines,
    write_run,
)


def _client_with(tmp_path, run_id, lines, phase="done"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def _list_status(client, slug, run_id):
    entry = next(e for e in client.get("/api/runs").json() if e.get("run_id") == run_id)
    return entry["status"]


def _detail_status(client, slug, run_id):
    return client.get(f"/api/runs/{slug}/{run_id}").json()["run"]["status"]


def test_last_run_span_status_wins_in_list_and_detail(home, tmp_path):  # noqa: F811
    """A1/A3: three ``run`` spans (two ``awaiting_approval``, the last ``done``)
    report ``done`` — NOT ``awaiting_approval`` — in the run list and the run
    detail alike."""
    client, slug = _client_with(
        tmp_path, "aaaa1111", multi_run_span_lines(last_ended=True, last_status="done")
    )

    assert _list_status(client, slug, "aaaa1111") == "done"
    assert _detail_status(client, slug, "aaaa1111") == "done"


def test_last_run_span_without_end_is_running(home, tmp_path):  # noqa: F811
    """A2: the last ``run`` span has no ``end`` record → the run is ``running``,
    even though the earlier spans finished (``awaiting_approval``)."""
    client, slug = _client_with(
        tmp_path, "bbbb2222", multi_run_span_lines(last_ended=False), phase="build"
    )

    assert _list_status(client, slug, "bbbb2222") == "running"
    assert _detail_status(client, slug, "bbbb2222") == "running"


def test_list_and_detail_status_are_identical(home, tmp_path):  # noqa: F811
    """A1: whatever the last span's status is, the run list and the run detail
    report the very same value (never one 'awaiting' and the other 'done')."""
    client, slug = _client_with(
        tmp_path, "cccc3333", multi_run_span_lines(last_ended=True, last_status="escalated")
    )

    assert _list_status(client, slug, "cccc3333") == _detail_status(client, slug, "cccc3333")
    assert _detail_status(client, slug, "cccc3333") == "escalated"
