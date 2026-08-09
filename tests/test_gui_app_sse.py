"""RED tests for Aufgabe B — the SSE live-tail stream.

Derived from .adw/spec.md (AC 18–20), .adw/contract.yaml (x-adw-sse) and
GUI-SPEC §7.3. The stream tails events.jsonl by byte offset and closes after the
run end event, so a FINISHED run yields a deterministic, terminating body that
TestClient can read in full. One live test appends bytes mid-stream to pin the
"incomplete final line is emitted exactly once when completed" behaviour. RED
until ``adw.gui.app.create_app`` exists.
"""

import json
import threading
import time

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    parse_sse,
    problems_lines,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)


def _client_for(tmp_path, run_id, lines):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def test_stream_replays_from_start_as_id_data_and_closes_after_run_end(home, tmp_path):  # noqa: F811
    """AC 18/19/20: an initial connection (no Last-Event-ID) replays from the
    beginning; each accepted event is one message with id: = seq and data: = the
    full record; the stream closes after the run end event."""
    client, slug = _client_for(tmp_path, "aaaa1111", comprehensive_lines())
    resp = client.get(f"/api/runs/{slug}/aaaa1111/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = [f for f in parse_sse(resp.text) if f["event"] is None]
    seqs = [int(f["id"]) for f in frames]
    assert seqs == list(range(1, 19))  # every accepted event, from the start, in order

    for f in frames:
        record = json.loads(f["data"])
        assert record["seq"] == int(f["id"])  # data is the full record; id is its seq
    last = json.loads(frames[-1]["data"])
    assert last["type"] == "run" and last["kind"] == "end"  # closed on the run end


def test_stream_reconnect_resumes_strictly_after_last_event_id(home, tmp_path):  # noqa: F811
    """AC 19: a reconnect with Last-Event-ID resumes strictly after that seq — no
    duplication, no gap."""
    client, slug = _client_for(tmp_path, "aaaa1111", comprehensive_lines())
    resp = client.get(
        f"/api/runs/{slug}/aaaa1111/stream", headers={"Last-Event-ID": "9"}
    )
    seqs = [int(f["id"]) for f in parse_sse(resp.text) if f["event"] is None]
    assert seqs == list(range(10, 19))  # strictly after seq 9


def test_stream_reports_problems_without_id_and_keeps_going(home, tmp_path):  # noqa: F811
    """AC 18: a broken line emits an ``event: problem`` message WITHOUT an id (so
    Last-Event-ID is unaffected), does not terminate the stream, and later valid
    events still arrive — live corruption becomes visible without a reload."""
    client, slug = _client_for(tmp_path, "aaaa1111", problems_lines())
    frames = parse_sse(client.get(f"/api/runs/{slug}/aaaa1111/stream").text)

    problems = [f for f in frames if f["event"] == "problem"]
    assert problems, "a broken line must surface as an event: problem message"
    for p in problems:
        assert p["id"] is None  # must not advance Last-Event-ID
        assert "kind" in json.loads(p["data"])

    events = [f for f in frames if f["event"] is None]
    seqs = [int(f["id"]) for f in events]
    assert seqs[0] == 1 and seqs[-1] == 5  # valid events before AND after the corruption
    assert json.loads(events[-1]["data"])["kind"] == "end"  # still closed on run end


def test_stream_incomplete_final_line_is_emitted_once_when_completed(home, tmp_path):  # noqa: F811
    """AC 18: a partially written final line is never emitted while incomplete;
    once a writer completes it (and appends the run end), it appears exactly once,
    in order, and the stream then closes.

    A background writer keeps the last line partial across at least one poll, then
    completes it plus the run end; a single blocking GET reads the whole stream
    until it closes. Emitting the once-partial seq twice would trip
    ``ids.count('3') == 1``; emitting it while still partial would corrupt the
    order."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_id = "aaaa1111"
    complete = [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Live tail")),
        rec(2, "phase", "start", "P", "R", sec=2, payload={"name": "build", "from_phase": "build"}),
    ]
    write_run(repo, run_id, complete)
    events_path = repo / ".adw" / "runs" / run_id / "events.jsonl"

    # A PARTIAL third line (no trailing newline) — must not be emitted yet.
    partial = json.dumps(rec(3, "gate", "start", "G", "P", sec=3, payload={"name": "lint"}))
    half = len(partial) // 2
    with open(events_path, "a", encoding="utf-8") as fh:
        fh.write(partial[:half])
    end_line = json.dumps(rec(4, "run", "end", "R", None, sec=4, payload=run_end_payload("done")))

    def finish_writing():
        time.sleep(0.8)  # keep the last line partial across at least one 500ms poll
        with open(events_path, "a", encoding="utf-8") as fh:
            fh.write(partial[half:] + "\n")
            fh.write(end_line + "\n")

    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]

    writer = threading.Thread(target=finish_writing)
    writer.start()
    try:
        body = client.get(f"/api/runs/{slug}/{run_id}/stream").text
    finally:
        writer.join()

    ids = [f["id"] for f in parse_sse(body) if f["event"] is None]
    assert ids.count("3") == 1  # the once-partial event appears exactly once
    assert ids == ["1", "2", "3", "4"]  # in order, no gap, closed on the run end
