"""RED tests for adw/events.py — the ADW run event-log emitter.

Derived from .adw/spec.md, .adw/contract.yaml and docs/GUI-SPEC.md §4.1–§4.4.
Every assertion is over the public API (EventEmitter / NoOpEmitter / span
handle) and the on-disk content of .adw/runs/<run_id>/events.jsonl only.
"""

import json
import logging
import os
import re
import stat
import threading
from pathlib import Path

import pytest
from adw.events import EventEmitter, NoOpEmitter

RUN_ID = "run01a2"

TOP_LEVEL_FIELDS = {
    "seq", "ts", "type", "kind", "span", "parent",
    "phase", "lane", "round", "payload",
}

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _events_path(repo: Path, run_id: str = RUN_ID) -> Path:
    return repo.resolve() / ".adw" / "runs" / run_id / "events.jsonl"


def read_events(repo: Path, run_id: str = RUN_ID) -> list[dict]:
    """Parse every complete line of the run's events.jsonl (empty if absent)."""
    path = _events_path(repo, run_id)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            records.append(json.loads(line))
    return records


# --- §4.2 record schema -----------------------------------------------------

def test_record_has_exactly_the_ten_top_level_fields(target_repo):
    EventEmitter(target_repo, RUN_ID).emit("agent.message", {"role": "assistant"})
    (record,) = read_events(target_repo)
    assert set(record) == TOP_LEVEL_FIELDS


def test_ts_is_utc_with_millisecond_precision(target_repo):
    EventEmitter(target_repo, RUN_ID).emit("log", {"message": "hi"})
    (record,) = read_events(target_repo)
    assert TS_RE.match(record["ts"]), record["ts"]


def test_point_event_kind_and_unset_context_default_to_null(target_repo):
    EventEmitter(target_repo, RUN_ID).emit("agent.message", {"role": "assistant"})
    (record,) = read_events(target_repo)
    assert record["kind"] == "point"
    assert record["type"] == "agent.message"
    for field in ("span", "parent", "phase", "lane", "round"):
        assert record[field] is None, field


def test_explicitly_passed_context_is_written_unchanged(target_repo):
    EventEmitter(target_repo, RUN_ID).emit(
        "agent.tool.call", {"tool": "Bash"},
        phase="build", lane="backend", round=2, span="S1", parent="P0",
    )
    (record,) = read_events(target_repo)
    assert record["phase"] == "build"
    assert record["lane"] == "backend"
    assert record["round"] == 2
    assert record["span"] == "S1"
    assert record["parent"] == "P0"
    assert record["kind"] == "point"


def test_payload_is_serialized_raw_and_uncapped(target_repo):
    payload = {
        "text": "ä ö ü \n ✓ tab\t",
        "nested": {"a": [1, 2, {"b": None}]},
        "num": 1.5,
        "big": "x" * 5000,
    }
    EventEmitter(target_repo, RUN_ID).emit("agent.message", payload)
    (record,) = read_events(target_repo)
    assert record["payload"] == payload


# --- §4.3 seq: monotonic, gap-free, resume, concurrency ---------------------

def test_seq_is_monotonic_and_gap_free_from_one(target_repo):
    emitter = EventEmitter(target_repo, RUN_ID)
    for i in range(5):
        emitter.emit("log", {"i": i})
    seqs = [record["seq"] for record in read_events(target_repo)]
    assert seqs == [1, 2, 3, 4, 5]


def test_resume_continues_seq_on_existing_file_without_sidecar(target_repo):
    first = EventEmitter(target_repo, RUN_ID)
    first.emit("log", {"n": 1})
    first.emit("log", {"n": 2})
    # A fresh emitter (simulating a new process) must keep counting gap-free.
    EventEmitter(target_repo, RUN_ID).emit("log", {"n": 3})
    seqs = [record["seq"] for record in read_events(target_repo)]
    assert seqs == [1, 2, 3]
    run_dir = _events_path(target_repo).parent
    assert {p.name for p in run_dir.iterdir()} == {"events.jsonl"}


def test_concurrent_threads_produce_no_gaps_and_no_partial_lines(target_repo):
    emitter = EventEmitter(target_repo, RUN_ID)
    n_threads, per_thread = 8, 25

    def worker():
        for _ in range(per_thread):
            emitter.emit("log", {"tid": threading.get_ident()})

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    total = n_threads * per_thread
    raw_lines = _events_path(target_repo).read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == total
    seqs = sorted(json.loads(line)["seq"] for line in raw_lines)
    assert seqs == list(range(1, total + 1))


# --- §4.2/§4.4 span API -----------------------------------------------------

def test_span_start_and_end_share_type_and_id_with_separate_payloads(target_repo):
    emitter = EventEmitter(target_repo, RUN_ID)
    with emitter.span("phase", {"name": "build"}) as handle:
        assert isinstance(handle.id, str) and handle.id
        handle.end_payload["to_phase"] = "review"
    start, end = read_events(target_repo)
    assert start["kind"] == "start"
    assert end["kind"] == "end"
    assert start["type"] == end["type"] == "phase"
    assert start["span"] == end["span"] == handle.id
    assert start["parent"] is None and end["parent"] is None
    assert start["payload"] == {"name": "build"}
    assert end["payload"] == {"to_phase": "review"}


def test_nested_spans_link_inner_parent_to_outer_id(target_repo):
    emitter = EventEmitter(target_repo, RUN_ID)
    with emitter.span("phase") as outer:
        with emitter.span("round") as inner:
            pass
    start_outer, start_inner, end_inner, end_outer = read_events(target_repo)
    assert start_outer["parent"] is None
    assert start_inner["parent"] == outer.id
    assert end_inner["parent"] == outer.id
    assert end_outer["parent"] is None
    assert start_inner["span"] == inner.id


def test_span_nesting_context_is_thread_isolated(target_repo):
    emitter = EventEmitter(target_repo, RUN_ID)
    both_outer_open = threading.Barrier(2)

    def worker(tag: str):
        with emitter.span("phase", lane=tag):
            both_outer_open.wait()
            with emitter.span("round", lane=tag):
                pass

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    events = read_events(target_repo)
    for tag in ("a", "b"):
        starts = [e for e in events if e["kind"] == "start" and e["lane"] == tag]
        outer = next(e for e in starts if e["type"] == "phase")
        inner = next(e for e in starts if e["type"] == "round")
        assert outer["parent"] is None
        assert inner["parent"] == outer["span"]


def test_span_body_exception_writes_end_and_propagates_unchanged(target_repo):
    emitter = EventEmitter(target_repo, RUN_ID)
    boom = ValueError("boom")
    with pytest.raises(ValueError) as excinfo:
        with emitter.span("phase") as handle:
            handle.end_payload["status"] = "error"
            raise boom
    assert excinfo.value is boom
    start, end = read_events(target_repo)
    assert start["kind"] == "start"
    assert end["kind"] == "end"
    assert start["span"] == end["span"]
    assert end["payload"] == {"status": "error"}


# --- §4.1 file: permissions, gitignore, flush/fsync -------------------------

def test_new_event_file_has_0600_permissions(target_repo):
    EventEmitter(target_repo, RUN_ID).emit("log", {"message": "hi"})
    mode = stat.S_IMODE(os.stat(_events_path(target_repo)).st_mode)
    assert mode == 0o600


def test_ensure_runs_gitignored_is_called_before_first_write(target_repo, monkeypatch):
    import adw.events as events_module

    events_path = _events_path(target_repo)
    original = events_module.ensure_runs_gitignored
    calls: list[bool] = []

    def spy(repo: Path):
        calls.append(events_path.exists())
        return original(repo)

    monkeypatch.setattr(events_module, "ensure_runs_gitignored", spy)
    EventEmitter(target_repo, RUN_ID).emit("log", {"message": "hi"})

    assert calls, "ensure_runs_gitignored was never called"
    assert calls[0] is False, "events.jsonl already existed at first gitignore call"
    gitignore = target_repo.resolve() / ".adw" / "runs" / ".gitignore"
    assert gitignore.exists()
    assert "*" in gitignore.read_text(encoding="utf-8").splitlines()


def test_write_flushes_but_never_fsyncs(target_repo, monkeypatch):
    fsync_calls: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: fsync_calls.append(fd) or real_fsync(fd))

    EventEmitter(target_repo, RUN_ID).emit("log", {"message": "hi"})

    assert fsync_calls == []
    # The line is visible to a separate reader after emit returns (flushed).
    assert len(read_events(target_repo)) == 1


# --- §4.3 fail-open — the central invariant ---------------------------------

def test_fail_open_warns_once_then_disables_run_across_instances(target_repo, caplog):
    other_run = "run0other"
    unserializable = {"bad": object()}

    with caplog.at_level(logging.WARNING, logger="adw.events"):
        emitter = EventEmitter(target_repo, RUN_ID)
        # An unserializable payload is an internal error — must stay fail-open.
        assert emitter.emit("log", unserializable) is None
        # Further calls on the same run stay silent no-ops (no exception).
        assert emitter.emit("log", {"ok": 1}) is None
        # A second instance of the SAME run is disabled too (per run/process).
        assert EventEmitter(target_repo, RUN_ID).emit("log", {"ok": 2}) is None

        warnings = [r for r in caplog.records
                    if r.name == "adw.events" and r.levelno == logging.WARNING]
        assert len(warnings) == 1

        # A different run in the same repo is unaffected.
        EventEmitter(target_repo, other_run).emit("log", {"ok": 3})

    assert read_events(target_repo, RUN_ID) == []
    assert len(read_events(target_repo, other_run)) == 1


# --- §4 NoOpEmitter ---------------------------------------------------------

def test_noop_emitter_writes_nothing_and_propagates_span_exceptions(target_repo, monkeypatch):
    monkeypatch.chdir(target_repo)
    noop = NoOpEmitter()

    assert noop.emit("log", {"message": "hi"}) is None

    with noop.span("phase", {"name": "build"}) as handle:
        assert isinstance(handle.id, str) and handle.id
        handle.end_payload["status"] = "done"

    boom = ValueError("boom")
    with pytest.raises(ValueError) as excinfo:
        with noop.span("phase"):
            raise boom
    assert excinfo.value is boom

    # No event file was created anywhere under the repo.
    assert list((target_repo / ".adw").glob("runs/*/events.jsonl")) == []
