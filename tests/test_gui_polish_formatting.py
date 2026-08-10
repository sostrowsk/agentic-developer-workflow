"""RED tests for Aufgabe E — readable numbers and times in the run list.

Durations render as e.g. ``47m 9s`` (not ``2828.7s``), costs as ``$5.80`` (not a
raw float), timestamps as ``YYYY-MM-DD HH:MM:SS`` in UTC (no ``Z`` suffix, no
fractional seconds). Missing values stay empty — never ``0``/``null`` as text.
The machine-readable JSON API keeps its raw numeric values; formatting is a
presentation concern of the served HTML.

Derived from .adw/spec.md (E1–E5), .adw/plan.md. The formatting is not contract
surface, so it is asserted on the served HTML content, not on exact markup.
"""

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    long_duration_lines,
    simple_run_lines,
    write_run,
)


def _list_html(tmp_path, run_id, lines, phase="done"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    resp = client.get("/")
    assert resp.status_code == 200
    return resp.text, client


def _detail_html(tmp_path, run_id, lines, phase="done"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    resp = client.get(f"/runs/{slug}/{run_id}")
    assert resp.status_code == 200
    return resp.text


def test_duration_and_cost_are_formatted_readably(home, tmp_path):  # noqa: F811
    """E1/E2: 2828.7s → ``47m 9s`` and 5.795072500000001 → ``$5.80`` in the list;
    the raw ``2828.7s`` / raw float never appear."""
    lines = simple_run_lines(
        "Long run", ended=True, duration=2828.7, cost=5.795072500000001
    )
    html, _ = _list_html(tmp_path, "aaaa1111", lines)

    assert "47m 9s" in html
    assert "$5.80" in html
    assert "2828.7s" not in html
    assert "5.795072500000001" not in html


def test_timestamp_renders_as_utc_without_z_or_fractional_seconds(home, tmp_path):  # noqa: F811
    """E3: the start timestamp renders as ``2026-08-05 14:00:00`` (UTC, no ``Z``,
    no fractional seconds); the raw ISO string does not appear."""
    lines = simple_run_lines("Timed run", ended=True)  # start ts is 2026-08-05T14:00:00.000Z
    html, _ = _list_html(tmp_path, "aaaa1111", lines)

    assert "2026-08-05 14:00:00" in html
    assert "2026-08-05T14:00:00.000Z" not in html
    assert "2026-08-05T14:00:00" not in html


def test_run_list_timestamp_does_not_wrap(home, tmp_path):  # noqa: F811
    """E4: a timestamp stays on one line — the served CSS carries a no-wrap rule
    so the standard table cell does not break across two lines."""
    lines = simple_run_lines("Timed run", ended=True)
    _, client = _list_html(tmp_path, "aaaa1111", lines)

    css = client.get("/static/app.css").text
    assert "nowrap" in css.replace(" ", "").lower()


def test_detail_html_formats_durations_and_aggregate_cost(home, tmp_path):  # noqa: F811
    """E1/E2 (regression): the run DETAIL formats durations and the aggregate cost
    too — tree rows, the phase badge and the aggregate pane show ``47m 9s`` /
    ``$5.80``, never raw seconds or a raw float. The run-level Raw tab (Aufgabe C)
    is the deliberate exception: it shows the event log VERBATIM, so the negative
    assertions are scoped to the formatted Trace view (everything before the raw
    panel)."""
    html = _detail_html(tmp_path, "aaaa1111", long_duration_lines())
    trace_view = html.split('data-tab-panel="raw"')[0]  # the formatted Trace view

    assert "47m 9s" in html                         # tree row + phase badge + aggregate
    assert "$5.80" in html                          # aggregate cost, readably
    assert "2828.7s" not in trace_view              # no raw ``%.1fs`` seconds remain
    assert "5.795072500000001" not in trace_view    # no raw cost float remains


def test_missing_duration_and_cost_render_empty_not_zero(home, tmp_path):  # noqa: F811
    """E5: a running run has no totals — its duration/cost cells stay empty; never
    ``0``/``$0.00``/``null`` as text."""
    lines = simple_run_lines("Running run", ended=False)
    html, _ = _list_html(tmp_path, "bbbb2222", lines, phase="build")

    assert "$0.00" not in html
    assert "0m 0s" not in html
    assert "None" not in html
    assert "null" not in html
