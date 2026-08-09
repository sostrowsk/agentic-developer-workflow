"""RED integration test for DoD 4 — a realistic event-log fixture read by the
reader and turned into the span tree by the model.

The fixture bundles nested spans, a worker-thread orphan, point events, unknown
types, a broken line, a seq gap, an initially incomplete last line, a resume
seam (second run start) and the three dangling-reference cases (AC 19). It must
be readable without abort, yield the pinned tree with correctly re-homed
orphans, and keep every otherwise-valid record observable — and give the same
observable tree whether read at once or incrementally (AC 20).

Both modules are new, so this test is RED until they are built.
"""

import json

from adw.gui.model import build_tree
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


# ts grows with seq, so time-based containment matches the nesting we intend.
FIXTURE_LINES = [
    line(1, type="run", kind="start", span="R1", parent=None),
    line(2, type="phase", kind="start", span="P1", parent="R1"),
    line(3, type="agent.run", kind="start", span="A1", parent=None),  # worker-thread orphan
    line(4, type="agent.message", kind="point", span="A1", payload={"role": "a", "text": "hi"}),
    line(5, type="agent.run", kind="end", span="A1", parent=None),
    "GARBAGE — keine gueltige JSON-Zeile",  # bad line (a healed crash fragment)
    line(7, type="gate", kind="start", span="G1", parent="P1"),  # seq 6 is missing → gap
    line(8, type="gate", kind="end", span="G1", parent="P1"),
    line(9, type="weird.type", kind="point", span="P1", payload={"k": 1}),  # unknown point type
    line(10, type="phase", kind="end", span="P1", parent="R1"),
    line(11, type="run", kind="end", span="R1", parent=None),
    line(12, type="run", kind="start", span="R2", parent=None, payload={"resumed_from_seq": 11}),
    line(13, type="custom.span", kind="start", span="W2", parent=None),  # unknown span, orphan
    line(14, type="custom.span", kind="end", span="W2", parent=None),
    line(15, type="commit", kind="end", span="C1", parent="R2", payload={"sha": "abc"}),  # end-only
    line(16, type="log", kind="point", span="NOSUCH", payload={"m": 1}),  # unknown span on a point
    line(17, type="run", kind="end", span="R2", parent=None),
]

VALID_SEQS = {1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17}


def outline(nodes):
    result = []
    for node in nodes:
        if hasattr(node, "children"):
            result.append((node.type, getattr(node, "span_id", None), node.running,
                           outline(node.children)))
        else:
            result.append((node.type, node.seq))
    return result


def all_seqs(nodes):
    seqs = set()
    for node in nodes:
        if hasattr(node, "children"):
            if node.start_record is not None:
                seqs.add(node.start_record["seq"])
            if node.end_record is not None:
                seqs.add(node.end_record["seq"])
            seqs |= all_seqs(node.children)
        else:
            seqs.add(node.seq)
    return seqs


def test_realistic_fixture_reads_incrementally_and_builds_the_pinned_tree(tmp_path):
    path = tmp_path / "events.jsonl"

    # Write everything except the last line, then a PARTIAL last line (no newline):
    # the tail must be ignored on the first read and processed once when completed.
    body = "\n".join(FIXTURE_LINES[:-1]) + "\n"
    last = FIXTURE_LINES[-1]
    half = len(last) // 2
    path.write_bytes((body + last[:half]).encode("utf-8"))

    reader = EventReader(path)
    r1 = reader.read()
    assert all(e["seq"] != 17 for e in r1.events)  # incomplete tail not yet parsed

    kinds = {p.kind for p in r1.problems}
    assert "bad_line" in kinds  # the GARBAGE line
    assert "seq_gap" in kinds   # seq 6 missing
    gap = next(p for p in r1.problems if p.kind == "seq_gap")
    assert gap.expected == 6 and gap.found == 7

    # Complete the last line; only the new record comes through.
    with open(path, "ab") as fh:
        fh.write((last[half:] + "\n").encode("utf-8"))
    r2 = reader.read()
    assert [e["seq"] for e in r2.events] == [17]

    incremental_events = r1.events + r2.events
    full_events = EventReader(path).read().events

    # Voll == inkrementell: same observable tree either way (AC 20).
    assert outline(build_tree(incremental_events)) == outline(build_tree(full_events))

    roots = build_tree(full_events)
    assert [r.span_id for r in roots] == ["R1", "R2"]  # resume seam → two roots

    # The worker-thread orphan A1 sits under the time-containing phase P1, not R1.
    r1_root, r2_root = roots
    p1 = next(c for c in r1_root.children if getattr(c, "span_id", None) == "P1")
    assert "A1" in [getattr(c, "span_id", None) for c in p1.children if hasattr(c, "children")]

    # Dangling references land under the resume root deterministically.
    r2_span_children = [
        getattr(c, "span_id", None) for c in r2_root.children if hasattr(c, "children")
    ]
    assert "W2" in r2_span_children  # unknown-type orphan span
    c1 = next(c for c in r2_root.children if getattr(c, "span_id", None) == "C1")
    assert c1.running is False and c1.start_record is None and c1.end_ts is not None

    # Every otherwise-valid record stays observable; only seq 6 (never written)
    # and the seq-less GARBAGE fragment are absent.
    assert all_seqs(roots) == VALID_SEQS
