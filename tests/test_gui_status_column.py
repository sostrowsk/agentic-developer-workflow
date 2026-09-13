"""RED tests for A4 — Phase and Status become ONE column (AC 9).

Today the list shows Phase and Status as two columns that carry the same word for
every finished run (``done``/``done``, ``escalated``/``escalated``). They merge
into one column that names the status and adds the phase only when it says
something new (a running or waiting run). A finished run therefore shows its status
exactly once per row.

The status token is read from the row's VISIBLE text (tags — and thus the
``status-…`` class names — stripped), so the assertion counts what a reader sees.
Derived from .adw/spec.md (AC 9), .adw/contract.yaml
(x-adw-template-behavior.status_column) and .adw/plan.md (B5).
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    simple_run_lines,
    write_run,
)


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


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


def test_finished_run_shows_status_once_running_run_adds_the_phase(home, tmp_path):  # noqa: F811
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
