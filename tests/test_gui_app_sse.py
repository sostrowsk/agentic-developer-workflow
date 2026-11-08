"""Tests for the SSE live-tail stream."""

import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (
    _slug_for,
    comprehensive_lines,
    parse_sse,
    problems_lines,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
    write_state_only_run,
)


def _client_for(tmp_path, run_id, lines):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def test_stream_replays_from_start_as_id_data_and_closes_after_run_end(home, tmp_path):
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


def test_stream_reconnect_resumes_strictly_after_last_event_id(home, tmp_path):
    """AC 19: a reconnect with Last-Event-ID resumes strictly after that seq — no
    duplication, no gap."""
    client, slug = _client_for(tmp_path, "aaaa1111", comprehensive_lines())
    resp = client.get(
        f"/api/runs/{slug}/aaaa1111/stream", headers={"Last-Event-ID": "9"}
    )
    seqs = [int(f["id"]) for f in parse_sse(resp.text) if f["event"] is None]
    assert seqs == list(range(10, 19))  # strictly after seq 9


def test_stream_reports_problems_without_id_and_keeps_going(home, tmp_path):
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


def test_stream_incomplete_final_line_is_emitted_once_when_completed(home, tmp_path):
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


# --- R3: live stream across run spans, keep-alive, terminal ``closed`` -------------

_CLOSED = "event: closed\ndata: {}\n\n"


def _span_ab_lines(*, b_ended):
    """Span A (start/end into awaiting_approval) then span B (start, phase, gate
    events, optionally its end) — one log of a gated run."""
    lines = [
        rec(1, "run", "start", "A", None, sec=1, payload=run_start_payload("Gated")),
        rec(2, "run", "end", "A", None, sec=2, payload=run_end_payload("awaiting_approval")),
        rec(3, "run", "start", "B", None, sec=3, payload=run_start_payload("Gated")),
        rec(4, "phase", "start", "P", "B", sec=4, payload={"name": "build", "from_phase": "build"}),
        rec(5, "gate", "start", "G", "P", sec=5, payload={"name": "lint"}),
    ]
    if b_ended:
        lines.append(rec(6, "run", "end", "B", None, sec=6, payload=run_end_payload("done")))
    return lines


def _setup(tmp_path, lines, *, phase=None, run_id="aaaa1111"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    return create_app(repos=[str(repo)]), _slug_for(repo), repo / ".adw" / "runs" / run_id


def _drive(app, path, *, headers=(), on_chunk=None, timeout=5.0):
    """Run one GET through the ASGI app on the event loop (no TestClient thread).

    ``on_chunk(text_so_far)`` may append to the log and returns True to make the
    client disconnect. Returns (status, body, ended_by_server). A stream that
    neither ends nor reacts to the disconnect within ``timeout`` fails the test."""

    async def main():
        chunks, status = [], {}
        disconnect = asyncio.Event()
        state = {"requested": False, "server_ended": False}

        async def receive():
            if not state["requested"]:
                state["requested"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b"").decode("utf-8"))
                if not message.get("more_body", False):
                    state["server_ended"] = not disconnect.is_set()
                elif on_chunk is not None and on_chunk("".join(chunks)):
                    disconnect.set()

        raw_path, _, query = path.partition("?")
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": raw_path, "raw_path": raw_path.encode(), "root_path": "",
            "query_string": query.encode(),
            "headers": [(b"host", b"127.0.0.1")]
            + [(k.lower().encode(), v.encode()) for k, v in headers],
            "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 80),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout)
        return status.get("code"), "".join(chunks), state["server_ended"]

    return asyncio.run(main())


def _seqs(body):
    return [int(f["id"]) for f in parse_sse(body) if f["event"] is None]


def _append(events_dir, *records):
    with open(events_dir / "events.jsonl", "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_stream_spans_runs_and_closes_after_last_span_end(home, tmp_path):
    """AK1: both spans' events arrive in order — A's end does not end the stream —
    and after B's end (run done) exactly one ``event: closed`` is the last frame."""
    app, slug, _ = _setup(tmp_path, _span_ab_lines(b_ended=True), phase="done")
    status, body, ended = _drive(app, f"/api/runs/{slug}/aaaa1111/stream")
    assert status == 200 and ended
    assert _seqs(body) == [1, 2, 3, 4, 5, 6]
    assert body.endswith(_CLOSED) and body.count("event: closed") == 1


def test_stream_stays_open_after_earlier_span_end(home, tmp_path):
    """AK1/AK2: with B still open the stream keeps running past A's end (pings, no
    ``closed``), and an event appended later arrives over the same connection;
    a ``last_id`` behind A's end filters exactly like ``Last-Event-ID``."""
    for cursor in ({"headers": [("Last-Event-ID", "2")]}, {"query": "?last_id=2"}):
        app, slug, run_dir = _setup(tmp_path, _span_ab_lines(b_ended=False), phase="build")
        appended = {"done": False}

        def on_chunk(text, run_dir=run_dir, appended=appended):
            if not appended["done"] and ": ping" in text:
                appended["done"] = True
                _append(run_dir, rec(6, "gate", "end", "G", "P", sec=6, payload={"name": "lint"}))
                return False
            return 6 in _seqs(text)

        status, body, ended = _drive(
            app, f"/api/runs/{slug}/aaaa1111/stream{cursor.get('query', '')}",
            headers=cursor.get("headers", ()), on_chunk=on_chunk,
        )
        assert status == 200 and not ended, cursor
        assert _seqs(body) == [3, 4, 5, 6], cursor  # no replay of A, B arrives live
        assert "event: closed" not in body, cursor


def test_stream_cursor_at_final_end_closes_without_replay(home, tmp_path):
    """AK2b: a cursor on/after the last span's end (run done) yields no records and
    an immediate ``closed``; a valid header wins over an older ``last_id``."""
    app, slug, _ = _setup(tmp_path, _span_ab_lines(b_ended=True), phase="done")
    status, body, ended = _drive(
        app, f"/api/runs/{slug}/aaaa1111/stream?last_id=1",
        headers=[("Last-Event-ID", "6")],
    )
    assert status == 200 and ended
    assert _seqs(body) == []
    assert body.endswith(_CLOSED) and body.count("event: closed") == 1


def test_stream_awaiting_approval_stays_open(home, tmp_path):
    """AK3: after A's end the run waits for approval — no ``closed``; a later
    approve (new span in the same log) arrives over the same connection."""
    lines = _span_ab_lines(b_ended=False)[:2]
    app, slug, run_dir = _setup(tmp_path, lines, phase="awaiting_approval")
    appended = {"done": False}

    def on_chunk(text):
        if not appended["done"] and ": ping" in text:
            appended["done"] = True
            _append(run_dir, rec(3, "run", "start", "B", None, sec=3,
                                 payload=run_start_payload("Gated")))
            return False
        return 3 in _seqs(text)

    status, body, ended = _drive(app, f"/api/runs/{slug}/aaaa1111/stream", on_chunk=on_chunk)
    assert status == 200 and not ended
    assert _seqs(body) == [1, 2, 3]
    assert "event: closed" not in body


def test_idle_stream_pings_async_and_ends_on_disconnect(home, tmp_path, monkeypatch):
    """AK4: an open log without an end yields ``: ping`` comments, waits without a
    blocking sleep or a worker thread, and a client disconnect ends it."""
    import starlette.responses

    def no_thread(*a, **k):
        raise AssertionError("the stream must be an async generator (no threadpool)")

    def no_sleep(seconds):
        raise AssertionError("the stream must not block in time.sleep")

    monkeypatch.setattr(starlette.responses, "iterate_in_threadpool", no_thread)
    monkeypatch.setattr(time, "sleep", no_sleep)
    lines = _span_ab_lines(b_ended=False)[:1]
    app, slug, _ = _setup(tmp_path, lines, phase="build")
    status, body, ended = _drive(
        app, f"/api/runs/{slug}/aaaa1111/stream",
        on_chunk=lambda text: text.count(": ping") >= 2,
    )
    assert status == 200 and not ended  # finished because the client went away
    assert body.count(": ping\n\n") >= 2
    assert "event: closed" not in body


def test_stream_without_trace_sends_closed(home, tmp_path):
    """AK5a: a run without an event log (state only) ends with ``closed``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_state_only_run(repo, "aaaa1111", phase="done")
    app = create_app(repos=[str(repo)])
    status, body, ended = _drive(app, f"/api/runs/{_slug_for(repo)}/aaaa1111/stream")
    assert status == 200 and ended
    assert body == _CLOSED


def test_stream_over_a_gzipped_archive_is_one_pass(home, tmp_path):
    """AK5b: a pruned run kept as events.jsonl.gz without a run end replays its
    events once, then ``closed`` (no polling of the archive)."""
    import gzip

    repo = tmp_path / "repo"
    run_dir = repo / ".adw" / "runs" / "aaaa1111"
    run_dir.mkdir(parents=True)
    with gzip.open(run_dir / "events.jsonl.gz", "wt") as fh:
        fh.write(json.dumps(rec(1, "run", "start", "R", payload=run_start_payload())) + "\n")
        fh.write(json.dumps(rec(2, "phase", "start", "P", payload={"name": "spec"})) + "\n")
    app = create_app(repos=[str(repo)])
    status, body, ended = _drive(app, f"/api/runs/{_slug_for(repo)}/aaaa1111/stream")
    assert status == 200 and ended
    assert _seqs(body) == [1, 2]
    assert ": ping" not in body
    assert body.endswith(_CLOSED) and body.count("event: closed") == 1


def test_stream_rereads_log_before_closing_on_a_state_change(home, tmp_path, monkeypatch):
    """Review P2: approve appends span B and leaves approval between the stream's
    read of A's batch and its completion check. The stream must deliver B and stay
    open — never close on the stale batch."""
    import adw.gui.app as app_mod
    from adw.state import RunState

    lines = _span_ab_lines(b_ended=False)[:2]
    app, slug, run_dir = _setup(tmp_path, lines, phase="awaiting_approval")
    repo = tmp_path / "repo"
    real_load_state = app_mod._load_state
    approved = {"done": False}

    def approve_then_load(*args, **kwargs):
        if not approved["done"]:
            approved["done"] = True
            _append(run_dir, rec(3, "run", "start", "B", None, sec=3,
                                 payload=run_start_payload("Gated")))
            RunState(run_id="aaaa1111", issue="Issue text", phase="build",
                     parallel=False).save(repo)
        return real_load_state(*args, **kwargs)

    monkeypatch.setattr(app_mod, "_load_state", approve_then_load)
    status, body, ended = _drive(
        app, f"/api/runs/{slug}/aaaa1111/stream", on_chunk=lambda text: 3 in _seqs(text),
    )
    assert status == 200 and not ended
    assert _seqs(body) == [1, 2, 3]
    assert "event: closed" not in body


def test_stream_with_run_end_but_no_start_closes(home, tmp_path):
    """Final review P3: like ``_run_span``, a log with a run end but no run start
    treats that end as the run's end — the stream closes instead of polling."""
    lines = [rec(1, "run", "end", "R", None, sec=1, payload=run_end_payload("done"))]
    app, slug, _ = _setup(tmp_path, lines, phase="done")
    status, body, ended = _drive(app, f"/api/runs/{slug}/aaaa1111/stream")
    assert status == 200 and ended
    assert _seqs(body) == [1]
    assert body.endswith(_CLOSED) and body.count("event: closed") == 1
