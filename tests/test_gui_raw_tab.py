"""RED tests for Aufgabe C — the run-level Raw tab.

The Raw tab shows the event log as a filterable list — the fallback that works
even for event types the GUI does not model (AC-C1). It filters at least by
``type`` and by free-text over the payload (AC-C2), surfaces the reader's reported
problems (``seq`` gaps, broken lines) here and not only in the detail pane
(AC-C3), and holds Aufgabe A's node-count criterion: a log with several thousand
events does not block the interface (AC-C4) while every matching event and its
full payload stay reachable.

Only the observable effect of the (unpinned) windowing mechanism is asserted here;
the 2-second bound itself is evidenced by the documented manual browser check.

Derived from .adw/spec.md (AC-C1..C4), .adw/contract.yaml
(x-adw-template-behavior.raw) and .adw/plan.md §6.
"""

import json
import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    DEEP_PAYLOAD_MARK,
    RAW_UNKNOWN_MARK,
    deep_payload_lines,
    home,
    large_event_log_lines,
    raw_event_mark,
    write_run,
)

RUN_ID = "aaaa1111"
COUNT = 3000


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _raw_client(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, large_event_log_lines(COUNT), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    return client, _slug_for(repo)


def _raw_panel(html):
    """Just the Raw panel of the detail HTML (everything after its marker), so an
    assertion targets the Raw tab and not the trace panes."""
    return html.split('data-tab-panel="raw"', 1)[1]


def _raw_rows(html):
    """Only the rendered ``<ol class="raw-list">`` rows of the Raw panel — excludes
    the filter form (which echoes the query) and the load-more link."""
    return _raw_panel(html).split('class="raw-list"', 1)[1].split("</ol>", 1)[0]


def test_raw_tab_present_and_shows_known_and_unknown_types(home, tmp_path):  # noqa: F811
    """AC-C1: a run-level Raw tab lists the event log, including an event of a
    ``type`` the GUI does not model (rendered generically, not dropped)."""
    client, slug = _raw_client(tmp_path)
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert "Raw" in html                 # the run-level Raw tab label
    assert "weird.unknown" in html       # unknown type surfaced generically
    assert RAW_UNKNOWN_MARK in html      # its raw payload is kept
    assert raw_event_mark(4) in html     # an early known event is in the first window


def test_raw_tab_offers_type_and_free_text_filters(home, tmp_path):  # noqa: F811
    """AC-C2: the Raw list can be filtered at least by ``type`` and by free-text
    over the payload. The controls are present and the vanilla client wires the
    filtering (mechanism/markup are not pinned — checked as observable affordances)."""
    client, slug = _raw_client(tmp_path)
    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    js = client.get("/static/app.js").text

    assert "<input" in html.lower()          # a free-text search box
    assert "filter" in js.lower()            # the client implements filtering
    assert "type" in js.lower()              # ... at least by type
    # The rendered rows carry their type and payload text so a filter can match.
    assert "note.item" in html


def test_reader_problems_are_visible_in_raw(home, tmp_path):  # noqa: F811
    """AC-C3: problems the reader reports for this log — a ``seq`` gap and a broken
    line — are surfaced (visible in the Raw tab, not only in the detail pane), and
    are also carried in the detail JSON the tab draws on."""
    client, slug = _raw_client(tmp_path)

    problems = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["problems"]
    kinds = {p["kind"] for p in problems}
    assert "seq_gap" in kinds and "bad_line" in kinds

    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    assert "seq_gap" in html and "bad_line" in html


def test_raw_initial_render_is_windowed_for_a_large_log(home, tmp_path):  # noqa: F811
    """AC-C4: for a >= 3000-event log the initial page does not materialise every
    event — a late event's unique marker is absent from the server-rendered page,
    while an early one is present (the observable effect of the windowing)."""
    client, slug = _raw_client(tmp_path)
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert raw_event_mark(4) in html          # early event materialised
    assert raw_event_mark(COUNT) not in html  # a late event is not inlined


def test_raw_free_text_search_covers_full_payload_beyond_preview(home, tmp_path):  # noqa: F811
    """AC-C2 (P2): free-text search matches text occurring BEYOND the rendered
    preview — the filter runs server-side over the full serialized payload. The
    event whose marker sits past the preview is returned by ``?raw_q=<marker>``
    (a preview-only filter would miss it), and a non-matching query returns none."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, RUN_ID, deep_payload_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    base = f"/runs/{slug}/{RUN_ID}"

    match_html = client.get(base, params={"raw_q": DEEP_PAYLOAD_MARK}).text
    rows = _raw_rows(match_html)
    # The event is returned even though the marker is past the preview — so the
    # marker itself is NOT in the rendered row (the match was over the full
    # payload, which the row only lazy-loads on demand): server-side, not preview.
    assert rows.count('class="raw-row"') == 1
    assert DEEP_PAYLOAD_MARK not in rows

    none = _raw_rows(client.get(base, params={"raw_q": "NOSUCHTEXTANYWHERE"}).text)
    assert none.count('class="raw-row"') == 0


def test_raw_row_exposes_complete_payload_on_demand(home, tmp_path):  # noqa: F811
    """AC-C4 (P2): the complete payload of every event is reachable FROM the Raw
    tab — each row carries a lazy full-payload anchor, and the events route the
    client loads it from returns the full payload (including text past the
    preview)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, RUN_ID, deep_payload_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)

    raw = _raw_panel(client.get(f"/runs/{slug}/{RUN_ID}").text)
    assert "data-load-seq" in raw  # each row can open its complete payload

    rec = client.get(
        f"/api/runs/{slug}/{RUN_ID}/events", params={"from_seq": 2, "to_seq": 2}
    ).json()
    assert DEEP_PAYLOAD_MARK in json.dumps(rec[0]["payload"])  # full payload delivered


def test_every_event_stays_reachable_with_full_payload(home, tmp_path):  # noqa: F811
    """AC-C4 (reachability, E8): the log has at least 3000 events and every one —
    including the late, windowed-out events and their full payloads — is reachable
    through the read-only events route the Raw tab draws on."""
    client, slug = _raw_client(tmp_path)
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()

    assert len(events) >= 3000
    last = next(e for e in events if e.get("seq") == COUNT)
    assert last["payload"]["rawmark"] == raw_event_mark(COUNT)  # full payload intact
    assert any(e.get("type") == "weird.unknown" for e in events)  # unknown kept
