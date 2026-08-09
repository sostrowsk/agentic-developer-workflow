"""RED tests for Aufgabe B1 — the incremental, fault-tolerant log reader
(``adw.gui.reader``). Derived from .adw/spec.md (AC 6–11), .adw/contract.yaml
(``reader_api``) and docs/GUI-SPEC.md §4.2.

The module does not exist yet, so importing it fails — these tests are RED until
the reader is built. Everything asserted is the public surface only:
``EventReader(path)``, ``EventReader.read() -> ReadResult`` (with ``events`` and
``problems``), ``EventReader.offset`` and ``ReadProblem``.
"""

import json

from adw.gui.reader import EventReader


def rec(seq, *, type="phase", kind="point", span=None, parent=None, payload=None, **extra):
    r = {
        "seq": seq,
        "ts": f"2026-08-05T14:00:{seq % 60:02d}.000Z",
        "type": type,
        "kind": kind,
        "span": span,
        "parent": parent,
        "phase": None,
        "lane": None,
        "round": None,
        "payload": payload or {},
    }
    r.update(extra)
    return r


def line(seq, **kw):
    return json.dumps(rec(seq, **kw))


def _write(path, *lines, terminate=True):
    text = "\n".join(lines)
    if lines and terminate:
        text += "\n"
    path.write_bytes(text.encode("utf-8"))


def _append(path, *lines, terminate=True):
    text = "\n".join(lines)
    if lines and terminate:
        text += "\n"
    with open(path, "ab") as fh:
        fh.write(text.encode("utf-8"))


def test_reader_tails_only_new_and_creates_no_files(tmp_path):
    """AC 6/7: first read yields all complete records with all original fields,
    a read without growth yields nothing, an append yields only the new record —
    and the reader never rewrites the log or creates sidecar files."""
    path = tmp_path / "events.jsonl"
    _write(
        path,
        line(1, type="run", kind="start", span="R"),
        line(2, type="phase", kind="start", span="P", parent="R"),
    )
    reader = EventReader(path)
    assert reader.offset == 0

    r1 = reader.read()
    assert [e["seq"] for e in r1.events] == [1, 2]
    assert r1.problems == []
    assert {
        "seq", "ts", "type", "kind", "span", "parent", "phase", "lane", "round", "payload",
    } <= set(r1.events[0])
    assert reader.offset == path.stat().st_size

    before = path.read_bytes()
    assert reader.read().events == []  # no growth → nothing re-delivered

    _append(path, line(3, type="phase", kind="end", span="P", parent="R"))
    r3 = reader.read()
    assert [e["seq"] for e in r3.events] == [3]  # only the new one
    assert reader.offset == path.stat().st_size

    assert sorted(p.name for p in tmp_path.iterdir()) == ["events.jsonl"]
    assert path.read_bytes().startswith(before)  # earlier bytes untouched


def test_reader_ignores_unterminated_tail_until_completed(tmp_path):
    """AC 8: an unterminated last line is neither parsed nor reported, its start
    is not confirmed as offset, and once completed it is processed exactly once."""
    path = tmp_path / "events.jsonl"
    _write(
        path,
        line(1, type="run", kind="start", span="R"),
        line(2, type="phase", kind="start", span="P", parent="R"),
    )
    full3 = line(3, type="phase", kind="end", span="P", parent="R")
    half = len(full3) // 2
    _append(path, full3[:half], terminate=False)  # crash-truncated tail

    reader = EventReader(path)
    r1 = reader.read()
    assert [e["seq"] for e in r1.events] == [1, 2]
    assert r1.problems == []  # partial line not reported as broken
    assert reader.offset < path.stat().st_size  # its start is not consumed

    _append(path, full3[half:], terminate=True)  # complete the line
    r2 = reader.read()
    assert [e["seq"] for e in r2.events] == [3]  # processed exactly once
    assert r2.problems == []
    assert reader.read().events == []


def test_reader_reports_seq_gap_with_expected_and_found(tmp_path):
    """AC 9: a gap in the seq sequence is reported (expected/found), not silently
    skipped; readable events before and after the gap stay available."""
    path = tmp_path / "events.jsonl"
    _write(
        path,
        line(1, type="run", kind="start", span="R"),
        line(2, type="phase", kind="start", span="P", parent="R"),
        line(4, type="phase", kind="end", span="P", parent="R"),  # seq 3 missing
    )
    result = EventReader(path).read()
    assert [e["seq"] for e in result.events] == [1, 2, 4]

    gaps = [p for p in result.problems if p.kind == "seq_gap"]
    assert len(gaps) == 1
    assert gaps[0].expected == 3
    assert gaps[0].found == 4


def test_reader_reports_bad_line_and_keeps_following_lines(tmp_path):
    """AC 10: a complete but unparsable line is reported as bad_line for that
    line; all following complete lines are still delivered."""
    path = tmp_path / "events.jsonl"
    _write(
        path,
        line(1, type="run", kind="start", span="R"),
        "das ist kein JSON",  # complete (\n-terminated) but not parsable
        line(2, type="phase", kind="start", span="P", parent="R"),
    )
    result = EventReader(path).read()
    assert [e["seq"] for e in result.events] == [1, 2]  # following line still delivered

    bad = [p for p in result.problems if p.kind == "bad_line"]
    assert len(bad) == 1
    assert bad[0].line_no == 2 or bad[0].byte_offset is not None
    # The unparsable fragment carries no seq, so continuity 1→2 holds (no gap).
    assert [p for p in result.problems if p.kind == "seq_gap"] == []


def test_reader_passes_snapshot_and_unknown_types_through_unchanged(tmp_path):
    """AC 11: snapshot events and records of unknown type are delivered unchanged
    (ref names available); the reader computes no diffs."""
    path = tmp_path / "events.jsonl"
    _write(
        path,
        line(1, type="run", kind="start", span="R"),
        line(2, type="snapshot", kind="point", span="R",
             payload={"ref": "refs/adw/run/2", "phase": "build"}),
        line(3, type="brandnew.type", kind="point", span="R", payload={"x": 1}),
    )
    result = EventReader(path).read()
    assert result.problems == []
    assert [e["type"] for e in result.events] == ["run", "snapshot", "brandnew.type"]
    assert result.events[1]["payload"]["ref"] == "refs/adw/run/2"
    assert result.events[2]["payload"] == {"x": 1}
