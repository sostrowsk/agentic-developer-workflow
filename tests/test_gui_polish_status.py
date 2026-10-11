"""Run status presentation: polish rules and the status column."""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (
    interleaved_run_span_lines,
    multi_run_span_lines,
    rec,
    run_end_payload,
    run_start_payload,
    simple_run_lines,
    write_run,
)

# --- from test_gui_polish_status.py ---------------------------------------------


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


def test_last_run_span_status_wins_in_list_and_detail(home, tmp_path):
    """A1/A3: three ``run`` spans (two ``awaiting_approval``, the last ``done``)
    report ``done`` — NOT ``awaiting_approval`` — in the run list and the run
    detail alike."""
    client, slug = _client_with(
        tmp_path, "aaaa1111", multi_run_span_lines(last_ended=True, last_status="done")
    )

    assert _list_status(client, slug, "aaaa1111") == "done"
    assert _detail_status(client, slug, "aaaa1111") == "done"


def test_last_run_span_without_end_is_running(home, tmp_path):
    """A2: the last ``run`` span has no ``end`` record → the run is ``running``,
    even though the earlier spans finished (``awaiting_approval``)."""
    client, slug = _client_with(
        tmp_path, "bbbb2222", multi_run_span_lines(last_ended=False), phase="build"
    )

    assert _list_status(client, slug, "bbbb2222") == "running"
    assert _detail_status(client, slug, "bbbb2222") == "running"


def test_list_and_detail_status_are_identical(home, tmp_path):
    """A1: whatever the last span's status is, the run list and the run detail
    report the very same value (never one 'awaiting' and the other 'done')."""
    client, slug = _client_with(
        tmp_path, "cccc3333", multi_run_span_lines(last_ended=True, last_status="escalated")
    )

    assert _list_status(client, slug, "cccc3333") == _detail_status(client, slug, "cccc3333")
    assert _detail_status(client, slug, "cccc3333") == "escalated"


def test_interleaved_run_spans_report_the_last_started_span(home, tmp_path):
    """A1 (regression): with interleaved spans (start A, start B, end B, end A) the
    reported status is the last-STARTED span B's — not that of the plainly-last
    run end record (A's ``awaiting_approval``)."""
    client, slug = _client_with(
        tmp_path, "dddd4444", interleaved_run_span_lines(last_ended=True, last_status="done")
    )

    assert _list_status(client, slug, "dddd4444") == "done"
    assert _detail_status(client, slug, "dddd4444") == "done"


def test_interleaved_run_spans_last_open_is_running(home, tmp_path):
    """A2 (regression): if the last-STARTED span B stays open, the run is
    ``running`` even though the earlier-started span A already ended."""
    client, slug = _client_with(
        tmp_path, "eeee5555", interleaved_run_span_lines(last_ended=False), phase="build"
    )

    assert _list_status(client, slug, "eeee5555") == "running"
    assert _detail_status(client, slug, "eeee5555") == "running"


# --- from test_gui_status_column.py ---------------------------------------------


def _row_text(html, run_id):
    for chunk in html.split("<tr"):
        if run_id in chunk:
            row = chunk.split("</tr>")[0]
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row)).strip()
    return ""


def escalated_run(issue="Neutral title"):
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "B", "R", sec=1, payload={"name": "build", "from_phase": "build"}),
        rec(3, "escalation", "point", "B", sec=2, payload={"reason": "hopeless", "phase": "build"}),
        rec(4, "phase", "end", "B", "R", sec=3, payload={"name": "build", "to_phase": None}),
        rec(5, "run", "end", "R", None, sec=4, payload=run_end_payload("escalated")),
    ]


def test_finished_run_shows_status_once_running_run_adds_the_phase(home, tmp_path):
    """AC 9: a finished (escalated) run shows its status word exactly once per row —
    the old Phase/Status duplication is gone — while a running run additionally
    surfaces its phase (which then adds information)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Finished, terminal run: state phase and status are both "escalated" today.
    write_run(repo, "aaaa1111", escalated_run(), phase="escalated", issue="Neutral title")
    # Running run: status "running", phase "build".
    write_run(repo, "bbbb2222", simple_run_lines("Live one", ended=False),
              phase="build", issue="Live one")

    html = TestClient(create_app(repos=[str(repo)])).get("/").text

    finished = _row_text(html, "aaaa1111")
    assert finished.count("escalated") == 1, finished  # exactly once, no done/done-style pair

    running = _row_text(html, "bbbb2222")
    assert "running" in running          # the status is named
    assert "build" in running            # ... and the phase adds information here
