"""RED tests for Aufgabe B2 — the span-tree model (``adw.gui.model``).

Derived from .adw/spec.md (AC 12–20), .adw/contract.yaml (``model_api`` /
``span_tree_structure``) and docs/GUI-SPEC.md §4.2 (the normative, purely
time-based orphan-containment rule) and §12 (resume seam).

The module does not exist yet, so these tests are RED until it is built. Only
the public surface is asserted: ``build_tree(events) -> list[SpanNode]`` and the
node fields. A PointNode is distinguished from a SpanNode by the absence of a
``children`` attribute.
"""

from adw.gui.model import build_tree


def T(sec: int) -> str:
    return f"2026-08-05T14:00:{sec:02d}.000Z"


def ev(seq, sec, type, kind, span, parent=None, payload=None, **extra):
    r = {
        "seq": seq,
        "ts": T(sec),
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


def is_span(node) -> bool:
    return hasattr(node, "children")


def child_ids(node):
    return [getattr(c, "span_id", None) for c in node.children if is_span(c)]


def points(node):
    return [c for c in node.children if not is_span(c)]


def test_paired_spans_nest_via_parent_and_open_span_is_running():
    """AC 12/13: start/end with the same id become one node with a duration; a
    span without end is running (no end_ts/duration/end_payload); nesting via
    parent; the run span is the root."""
    events = [
        ev(1, 0, "run", "start", "R", None),
        ev(2, 1, "phase", "start", "P", "R"),
        ev(3, 2, "gate", "start", "G", "P", payload={"name": "lint"}),
        ev(4, 3, "gate", "end", "G", "P", payload={"passed": True}),
        ev(5, 9, "run", "end", "R", None),
        # P never ends → running
    ]
    (root,) = build_tree(events)
    assert root.span_id == "R" and root.type == "run" and root.running is False

    (phase,) = root.children
    assert phase.span_id == "P"
    assert phase.running is True
    assert phase.end_ts is None and phase.duration is None and phase.end_payload is None

    (gate,) = phase.children
    assert gate.span_id == "G" and gate.running is False
    assert gate.start_ts is not None and gate.end_ts is not None
    assert gate.duration is not None
    assert gate.start_payload == {"name": "lint"} and gate.end_payload == {"passed": True}


def test_orphan_lands_under_innermost_containing_span_time_based():
    """AC 14/15: the proven case — an agent.run span opened in a worker thread
    carries parent=null yet lands under the open phase span that contains it in
    time, not under the run root. phase/lane are null everywhere."""
    events = [
        ev(1, 0, "run", "start", "R", None),
        ev(2, 1, "phase", "start", "P", "R"),  # still open (running)
        ev(3, 2, "agent.run", "start", "A", None),  # orphan, inside [1, ...]
        ev(4, 3, "agent.run", "end", "A", None),
        ev(5, 9, "phase", "end", "P", "R"),
        ev(6, 10, "run", "end", "R", None),
    ]
    assert all(e["phase"] is None and e["lane"] is None for e in events)
    (root,) = build_tree(events)
    (phase,) = root.children
    assert child_ids(phase) == ["A"]  # orphan under the phase, not the run root
    assert child_ids(root) == ["P"]


def test_orphan_prefers_latest_start_then_higher_seq():
    """AC 14: among containing candidates the latest start wins; on a tie the
    higher seq wins. A still-running span contains everything after its start."""
    events = [
        ev(1, 0, "run", "start", "R", None),
        ev(2, 1, "phase", "start", "A", "R"),  # running, start sec 1
        ev(3, 1, "lane", "start", "B", "R"),   # running, same start sec 1, higher seq
        ev(4, 5, "codex.author", "start", "O", None),  # orphan after both starts
        ev(5, 6, "codex.author", "end", "O", None),
    ]
    root = build_tree(events)[0]
    a = next(c for c in root.children if getattr(c, "span_id", None) == "A")
    b = next(c for c in root.children if getattr(c, "span_id", None) == "B")
    assert child_ids(a) == []          # tie broken away from A
    assert child_ids(b) == ["O"]       # higher seq container wins


def test_resume_log_yields_two_roots_and_orphan_under_second():
    """AC 13/16: a resume log (second run start, seq counts through) yields two
    roots; an orphan from the resume segment hangs under the second root, never
    the first."""
    events = [
        ev(1, 0, "run", "start", "R1", None),
        ev(2, 1, "phase", "start", "P1", "R1"),
        ev(3, 2, "phase", "end", "P1", "R1"),
        ev(4, 3, "run", "end", "R1", None),
        ev(5, 4, "run", "start", "R2", None, payload={"resumed_from_seq": 4}),
        ev(6, 5, "agent.run", "start", "O", None),  # orphan of the resume segment
        ev(7, 6, "agent.run", "end", "O", None),
        ev(8, 9, "run", "end", "R2", None),
    ]
    roots = build_tree(events)
    assert [r.span_id for r in roots] == ["R1", "R2"]
    assert child_ids(roots[1]) == ["O"]
    assert "O" not in child_ids(roots[0])


def test_point_event_attaches_to_the_span_it_names():
    """AC 17: a point event hangs on the span whose id it carries in ``span``."""
    events = [
        ev(1, 0, "run", "start", "R", None),
        ev(2, 1, "phase", "start", "P", "R"),
        ev(3, 2, "log", "point", "P", payload={"level": "warning", "message": "x"}),
        ev(4, 3, "phase", "end", "P", "R"),
        ev(5, 9, "run", "end", "R", None),
    ]
    root = build_tree(events)[0]
    phase = root.children[0]
    pts = points(phase)
    assert len(pts) == 1
    point = pts[0]
    assert point.type == "log" and point.seq == 3
    assert point.ts == T(2)
    assert point.payload == {"level": "warning", "message": "x"}
    assert point.record["seq"] == 3 and point.record["span"] == "P"


def test_unknown_types_become_generic_nodes_with_full_records():
    """AC 18: unknown span/point types are taken as generic nodes (never dropped),
    take part in span formation/nesting, and keep ALL original record fields."""
    events = [
        ev(1, 0, "run", "start", "R", None),
        ev(2, 1, "weird.span", "start", "W", "R", payload={"a": 1}, custom="x"),
        ev(3, 2, "weird.point", "point", "W", payload={"b": 2}),
        ev(4, 3, "weird.span", "end", "W", "R", payload={"z": 9}),
        ev(5, 9, "run", "end", "R", None),
    ]
    root = build_tree(events)[0]
    (w,) = [c for c in root.children if is_span(c)]
    assert w.type == "weird.span"
    assert w.start_payload == {"a": 1} and w.end_payload == {"z": 9}
    assert w.start_record["custom"] == "x"  # ALL original fields preserved
    (pt,) = points(w)
    assert pt.type == "weird.point"
    assert pt.record["payload"] == {"b": 2}


def test_dangling_references_are_handled_deterministically():
    """AC 19: end without start, point with unknown span, and a span with an
    unresolvable parent never abort the build and lose no otherwise-valid record."""
    events = [
        ev(1, 0, "run", "start", "R", None),
        ev(2, 1, "gate", "end", "G", "R", payload={"passed": True}),  # end without start
        ev(3, 2, "log", "point", "NOSUCH", payload={"m": 1}),          # unknown span
        ev(4, 3, "phase", "start", "P", "GHOST"),                      # unresolvable parent
        ev(5, 4, "phase", "end", "P", "GHOST"),
        ev(6, 9, "run", "end", "R", None),
    ]
    root = build_tree(events)[0]

    g = next(c for c in root.children if getattr(c, "span_id", None) == "G")
    assert g.running is False
    assert g.start_ts is None and g.start_payload is None and g.start_record is None
    assert g.duration is None
    assert g.end_ts is not None and g.seq == 2  # seq from the end record
    assert g.end_payload == {"passed": True}

    assert any(p.seq == 3 for p in points(root))  # unknown-span point re-homed to run
    assert "P" in child_ids(root)  # unresolvable-parent span treated as orphan → run


def test_children_are_ordered_by_ts_then_seq():
    """AC 20: children of each node are chronological by ts, ties broken by seq —
    independent of the order the events arrive in."""
    events = [
        ev(1, 0, "run", "start", "R", None),
        ev(2, 5, "phase", "start", "A", "R"),  # later ts, smaller seq
        ev(3, 2, "phase", "start", "B", "R"),  # earlier ts, larger seq
        ev(4, 6, "phase", "end", "A", "R"),
        ev(5, 7, "phase", "end", "B", "R"),
        ev(6, 9, "run", "end", "R", None),
    ]
    root = build_tree(events)[0]
    assert child_ids(root) == ["B", "A"]  # ordered by ts (2 < 5), not by seq


def test_log_without_run_start_makes_parentless_spans_roots():
    """AC 13: a degenerate/truncated log without a run start does not abort;
    parentless spans become roots themselves."""
    events = [
        ev(1, 0, "phase", "start", "P", None),
        ev(2, 1, "gate", "start", "G", "P"),
        ev(3, 2, "gate", "end", "G", "P"),
        ev(4, 3, "phase", "end", "P", None),
    ]
    roots = build_tree(events)
    assert [r.span_id for r in roots] == ["P"]
    assert child_ids(roots[0]) == ["G"]
