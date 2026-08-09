"""Span-tree model over the event records (GUI-SPEC §4.2, normative).

``build_tree`` turns a sequence of read event records into a tree of ``SpanNode``
and ``PointNode``. ``start``/``end`` with the same span id become one node;
nesting follows ``parent`` where set. Because ``parent`` comes from a thread-local
stack, spans opened in worker threads carry ``parent: null`` even though they are
logically nested — so the tree is **not** derivable from ``parent`` alone. Such
orphans are re-homed purely by time: an orphan belongs to the innermost span
whose interval strictly contains the orphan's ``[start ts, end ts]`` (latest
start wins; ties broken by the higher ``seq``; a still-running span contains
everything after its start). The rule never uses ``phase``/``lane`` (they may be
null everywhere). Unknown ``type`` values are kept as generic nodes, never
dropped; dangling references (``end`` without ``start``, unknown ``parent``/
``span``) are handled deterministically without ever aborting.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime


def _epoch(ts):
    """Parse an ISO-8601/UTC ts to epoch seconds, or None if unparseable."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class PointNode:
    type: str
    seq: int | None
    ts: str | None
    payload: dict | None
    record: dict
    # Internal interval bounds (epoch seconds) for containment/ordering.
    _lo: float | None = None
    _hi: float | None = None


@dataclass
class SpanNode:
    span_id: str | None
    type: str | None = None
    seq: int | None = None
    start_ts: str | None = None
    end_ts: str | None = None
    duration: float | None = None
    running: bool = False
    start_payload: dict | None = None
    end_payload: dict | None = None
    start_record: dict | None = None
    end_record: dict | None = None
    children: list = field(default_factory=list)
    # Internal bookkeeping.
    _parent: str | None = None
    _has_start: bool = False
    _has_end: bool = False
    _start_epoch: float | None = None
    _end_epoch: float | None = None

    # -- interval (epoch seconds); +inf upper bound while running ------------
    @property
    def _lo(self) -> float | None:
        return self._start_epoch if self._has_start else self._end_epoch

    @property
    def _hi(self) -> float | None:
        if self._has_start and not self._has_end:
            return math.inf
        return self._end_epoch if self._has_end else self._start_epoch


def _apply_start(node: SpanNode, rec: dict) -> None:
    node._has_start = True
    node.type = rec.get("type")
    node.seq = rec.get("seq")
    node.start_ts = rec.get("ts")
    node._start_epoch = _epoch(rec.get("ts"))
    node.start_payload = rec.get("payload")
    node.start_record = rec
    node._parent = rec.get("parent")


def _apply_end(node: SpanNode, rec: dict) -> None:
    node._has_end = True
    node.end_ts = rec.get("ts")
    node._end_epoch = _epoch(rec.get("ts"))
    node.end_payload = rec.get("payload")
    node.end_record = rec
    if not node._has_start:  # end-only node (AC 19): identity comes from the end
        node.type = rec.get("type")
        node.seq = rec.get("seq")
        node._parent = rec.get("parent")


def _finalize(node: SpanNode) -> None:
    node.running = node._has_start and not node._has_end
    if node._has_start and node._has_end and node._start_epoch is not None \
            and node._end_epoch is not None:
        node.duration = node._end_epoch - node._start_epoch
    else:
        node.duration = None


def _sort_key(node):
    lo = node._lo
    return (lo if lo is not None else math.inf, node.seq if node.seq is not None else 0)


def build_tree(events) -> list:
    spans: dict[str, SpanNode] = {}
    order: list[str] = []  # span ids in first-seen (log) order
    points: list[PointNode] = []

    for rec in events:
        kind = rec.get("kind")
        sid = rec.get("span")
        if kind == "start":
            node = spans.get(sid)
            if node is None:
                node = SpanNode(span_id=sid)
                spans[sid] = node
                order.append(sid)
            _apply_start(node, rec)
        elif kind == "end":
            node = spans.get(sid)
            if node is None:
                node = SpanNode(span_id=sid)
                spans[sid] = node
                order.append(sid)
            _apply_end(node, rec)
        else:  # point (or any non-start/end kind): a generic leaf
            ep = _epoch(rec.get("ts"))
            points.append(
                PointNode(
                    type=rec.get("type"),
                    seq=rec.get("seq"),
                    ts=rec.get("ts"),
                    payload=rec.get("payload"),
                    record=rec,
                    _lo=ep,
                    _hi=ep,
                )
            )

    for node in spans.values():
        _finalize(node)

    span_nodes = [spans[sid] for sid in order]
    run_roots = [n for n in span_nodes if n.type == "run"]
    children: dict[int, list] = {}

    def add_child(parent: SpanNode, child) -> None:
        children.setdefault(id(parent), []).append(child)

    def innermost_container(lo, hi, exclude):
        if lo is None:
            return None
        best = None
        for cand in span_nodes:
            if cand is exclude:
                continue
            c_lo, c_hi = cand._lo, cand._hi
            if c_lo is None or c_hi is None:
                continue
            if c_lo < lo and c_hi > hi:  # strictly contains
                if best is None or (c_lo, cand.seq or 0) > (best._lo, best.seq or 0):
                    best = cand
        return best

    def fallback_run_root(lo):
        if not run_roots:
            return None
        eligible = [r for r in run_roots if r._lo is not None and lo is not None and r._lo <= lo]
        if eligible:
            return max(eligible, key=lambda r: (r._lo, r.seq or 0))
        return run_roots[0]

    roots: list = []
    for node in span_nodes:
        if node.type == "run":
            roots.append(node)
            continue
        parent_id = node._parent
        if parent_id is not None and parent_id in spans:
            add_child(spans[parent_id], node)  # nesting via parent
            continue
        # Orphan (parent null, or non-null but unknown): re-home by time.
        target = innermost_container(node._lo, node._hi, node)
        if target is None:
            target = fallback_run_root(node._lo)
        if target is not None:
            add_child(target, node)
        else:
            roots.append(node)  # degenerate log without any run start

    for point in points:
        span_id = point.record.get("span")
        if span_id is not None and span_id in spans:
            add_child(spans[span_id], point)
            continue
        target = innermost_container(point._lo, point._hi, None)
        if target is None:
            target = fallback_run_root(point._lo)
        if target is not None:
            add_child(target, point)
        else:
            roots.append(point)  # nothing to hang it on: keep it observable

    def attach(node: SpanNode) -> None:
        kids = sorted(children.get(id(node), []), key=_sort_key)
        node.children = kids
        for kid in kids:
            if isinstance(kid, SpanNode):
                attach(kid)

    for node in span_nodes:
        attach(node)

    return roots
