"""Tree serialization for the detail page: snapshot bracketing for the Diff tab,
prompt diff against the previous run, raw event view."""

import difflib
import json

from adw.gui.diffs import (
    _is_snapshot_ref,
)
from adw.gui.fmt import (
    _mapping_payload,
)
from adw.gui.timeline import (
    _empty_context,
)
from adw.gui.trace import (
    _RAW_PAYLOAD_PREVIEW,
    _aggregate_outcome,
    _is_span,
    _node_label,
    _node_status,
    _subtree_cost,
)

# --- snapshot bracketing for the Diff tab (Aufgabe B, AC-B5/B8) -----------------
# The Diff tab of a node requests the diff between the two snapshots that bracket
# the node. The pair is derived observably from THIS run's event log: same-lane
# snapshots only, by unique seq, so the pair is deterministic. Nothing here reads
# git or mutates anything — it only reads the events already loaded for the view.


def _span_seq_ranges(events) -> dict:
    """Per span id, the (min, max) event seq over every event carrying that span
    id (start, its point children, end). Subtree ranges are combined from these
    while serializing, so a node's first/last event seq is known without touching
    the model."""
    own: dict = {}
    for e in events:
        sid = e.get("span")
        seq = e.get("seq")
        if sid is None or not isinstance(seq, int):
            continue
        lo, hi = own.get(sid, (seq, seq))
        own[sid] = (min(lo, seq), max(hi, seq))
    return own


def _snapshots_by_lane(events, run_id) -> dict:
    """``snapshot`` refs of this run grouped by their declared lane (payload.lane),
    each list sorted by seq. Only refs with the exact ``refs/adw/<run_id>/<seq>``
    structure are kept — the SAME structural validation the endpoint applies — so a
    malformed or option-like recorded ref never brackets a node (and never exposes
    a Diff tab the endpoint would then reject). These events are the sole source
    both for the bracket (AC-B5) and, in the endpoint, for the allowlist (AC-B2)."""
    by_lane: dict = {}
    for e in events:
        if e.get("type") != "snapshot":
            continue
        seq = e.get("seq")
        payload = _mapping_payload(e)
        lane = payload.get("lane")
        ref = payload.get("ref")
        # The lane must be a non-empty STRING: a malformed non-string value (numeric,
        # or an unhashable list/mapping) is ignored — never a dictionary key (which
        # would raise TypeError) and never a non-string lane in the response.
        if (not isinstance(seq, int) or not isinstance(lane, str) or not lane
                or not _is_snapshot_ref(ref, run_id)):
            continue
        by_lane.setdefault(lane, []).append((seq, ref))
    for lane in by_lane:
        by_lane[lane].sort()
    return by_lane


def _bracket_pair(lo, hi, lane, snaps) -> tuple:
    """AC-B5: ``from`` is the same-lane snapshot with the highest seq at or before
    ``lo`` (the node's first event); ``to`` is the same-lane snapshot with the
    lowest seq at or after ``hi`` (the node's last event). Snapshots of other lanes
    are never considered. AC-B8: if either boundary is absent the node is not
    bracketed and (None, None) is returned — no synthetic nearest pair."""
    if not lane:
        return None, None
    cands = snaps.get(lane)
    if not cands:
        return None, None
    frm = None
    for seq, ref in cands:  # ascending by seq
        if seq <= lo:
            frm = ref
        else:
            break
    to = None
    for seq, ref in cands:
        if seq >= hi:
            to = ref
            break
    if frm is None or to is None:
        return None, None
    return frm, to


def _subtree_seq_range(node, own_ranges, children) -> tuple:
    lo, hi = own_ranges.get(node.span_id, (node.seq, node.seq))
    if lo is None:
        lo = hi = node.seq
    for c in children:
        cseq = c.get("seq")
        if isinstance(cseq, int):
            lo = min(lo, cseq)
            hi = max(hi, cseq)
        cend = c.get("end_seq")
        if isinstance(cend, int):
            hi = max(hi, cend)
    return lo, hi


# --- prompt diff against the previous run (GUI-SPEC §7.2) -----------------------
# A purely DERIVED projection of the SAME event stream the detail view already
# loaded (no new reader/route/event/persistence): each `agent.run` node's prompt
# diffed against the previous `agent.run` of THE SAME agent in THE SAME lane within
# THIS run (E3). The predecessor is chosen STRUCTURALLY — same agent string, same
# lane, greatest seq below the node's — BEFORE prompt usability matters (D1); an
# unusable immediate predecessor is never skipped for an older valid one. The
# lane is the enclosing `lane` span's name, threaded exactly as `_serialize` does.


def _agent_run_index(roots) -> list:
    """Every `agent.run` span start as ``(seq, agent, lane, prompt)``, the lane
    threaded from the enclosing ``lane`` span (as in ``_serialize``). ``agent`` and
    ``prompt`` are taken verbatim from the start payload (so a missing / non-string
    value stays as-is for the usability checks in ``_prompt_diff_for``)."""
    items: list = []

    def walk(node, lane):
        if not _is_span(node):
            return
        payload = node.start_payload if node.start_payload is not None else node.end_payload
        node_lane = lane
        # A lane name is only adopted when it is a non-empty STRING: a malformed
        # non-string `payload.name` (numeric, or an unhashable list/mapping) is
        # ignored so it never reaches a lane-keyed lookup as an unhashable key.
        if (node.type == "lane" and isinstance(payload, dict)
                and isinstance(payload.get("name"), str) and payload["name"]):
            node_lane = payload["name"]
        if node.type == "agent.run":
            start = node.start_payload if isinstance(node.start_payload, dict) else {}
            items.append((node.seq, start.get("agent"), node_lane, start.get("prompt")))
        for c in node.children:
            walk(c, node_lane)

    for r in roots:
        walk(r, None)
    return items


def _prompt_diff_for(seq, agent, lane, prompt, index) -> tuple:
    """The ``(prompt_diff, previous_prompt_seq)`` pair for one `agent.run` (D1-D3).
    Returns ``(None, None)`` — the "no predecessor" case — when this node has no
    usable string ``agent``/``prompt``, when no same-agent/same-lane candidate with
    a smaller seq exists, or when the structurally chosen immediate predecessor has
    no usable string ``prompt`` (the older valid run is NEVER substituted)."""
    if not isinstance(seq, int) or not isinstance(agent, str) or not isinstance(prompt, str):
        return None, None
    prev = None  # (seq, prompt) of the greatest-seq candidate below `seq`
    for cand_seq, cand_agent, cand_lane, cand_prompt in index:
        if (
            cand_agent == agent and cand_lane == lane
            and isinstance(cand_seq, int) and cand_seq < seq
            and (prev is None or cand_seq > prev[0])
        ):
            prev = (cand_seq, cand_prompt)
    if prev is None:
        return None, None
    prev_seq, prev_prompt = prev
    if not isinstance(prev_prompt, str):
        return None, None
    diff = "\n".join(
        difflib.unified_diff(prev_prompt.splitlines(), prompt.splitlines(), n=3, lineterm="")
    )
    return diff, prev_seq


def _prompt_diffs(roots) -> dict:
    """``{agent_run_seq: (prompt_diff, previous_prompt_seq)}`` for every `agent.run`
    of the run — the derived map ``_serialize`` attaches to its `agent.run` nodes."""
    index = _agent_run_index(roots)
    return {
        seq: _prompt_diff_for(seq, agent, lane, prompt, index)
        for seq, agent, lane, prompt in index
    }


def _serialize(node, tool_names=None, *, lane=None, own_ranges=None, snaps=None,
               context_at=None, prompt_diffs=None) -> dict:
    tool_names = tool_names or {}
    own_ranges = own_ranges or {}
    snaps = snaps or {}
    prompt_diffs = prompt_diffs or {}
    context_at = context_at or (lambda _cutoff: _empty_context())
    if _is_span(node):
        payload = node.start_payload if node.start_payload is not None else node.end_payload
        node_lane = lane
        # A lane name is only adopted when it is a non-empty STRING: a malformed
        # non-string `payload.name` (numeric, or an unhashable list/mapping) is
        # ignored so it never reaches a lane-keyed lookup as an unhashable key.
        if (node.type == "lane" and isinstance(payload, dict)
                and isinstance(payload.get("name"), str) and payload["name"]):
            node_lane = payload["name"]
        children = [
            _serialize(c, tool_names, lane=node_lane, own_ranges=own_ranges, snaps=snaps,
                       context_at=context_at, prompt_diffs=prompt_diffs)
            for c in node.children
        ]
        lo, hi = _subtree_seq_range(node, own_ranges, children)
        diff_from, diff_to = _bracket_pair(lo, hi, node_lane, snaps)
        d = {
            "type": node.type,
            "label": _node_label(node, tool_names),
            "duration": node.duration,
            "status": _node_status(node),
            "seq": node.seq,
            "end_seq": hi,
            "span_id": node.span_id,
            "running": node.running,
            "start_ts": node.start_ts,
            "end_ts": node.end_ts,
            "payload": payload,
            "start_payload": node.start_payload,
            "end_payload": node.end_payload,
            # The bracketing pair the node's Diff tab requests; both None when the
            # node is not bracketed (AC-B6: no Diff tab is offered then).
            "diff_from": diff_from,
            "diff_to": diff_to,
            # Run state at this span's cutoff — its end_seq (subtree maximum), so a
            # finished/running span includes qualifying events after its start (C1).
            "context": context_at(hi),
            "children": children,
        }
        if node.type == "agent.run":
            # Additive, purely derived fields (D1-D3): the prompt diff against the
            # previous run of the same agent in the same lane. Present ONLY on
            # `agent.run` nodes; `previous_prompt_seq` is set even for an empty
            # (identical) diff, distinguishing it from the null "no predecessor".
            diff, prev_seq = prompt_diffs.get(node.seq, (None, None))
            d["prompt_diff"] = diff
            d["previous_prompt_seq"] = prev_seq
        if node.type == "round" and isinstance(node.start_payload, dict):
            d["n"] = node.start_payload.get("n")
            d["cap"] = node.start_payload.get("cap")
        if node.type in ("phase", "lane", "round"):
            # Aggregate the children (contract detail_pane.aggregates): duration is
            # the span's own; cost is summed from descendant agent.run usage;
            # outcome is derived per node type.
            d["cost"] = _subtree_cost(node)
            d["outcome"] = _aggregate_outcome(node)
        return d
    return {
        "type": node.type,
        "label": _node_label(node, tool_names),
        "duration": None,
        "status": None,
        "seq": node.seq,
        "ts": node.ts,
        "payload": node.payload,
        "diff_from": None,
        "diff_to": None,
        # A point event's cutoff is its own seq (C1).
        "context": context_at(node.seq),
        "children": [],
    }


def _serialize_payload(payload) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(payload)


def _in_seq_range(seq, from_seq, to_seq) -> bool:
    """Whether ``seq`` satisfies the (optionally one-sided) inclusive range. When
    a bound is active an event whose ``seq`` is not an integer never satisfies it,
    and each active bound is inclusive; an upper bound below the lower yields a
    predicate that no ``seq`` satisfies (a defined empty set, R1/R4)."""
    if from_seq is None and to_seq is None:
        return True
    if not isinstance(seq, int) or isinstance(seq, bool):
        return False
    if from_seq is not None and seq < from_seq:
        return False
    if to_seq is not None and seq > to_seq:
        return False
    return True


def _raw_view(events, limit, *, q=None, type_filter=None, from_seq=None, to_seq=None) -> dict:
    """Run-level Raw tab data (Aufgabe C): the distinct event types, and a bounded
    window of ``limit`` matching rows (seq, type, a payload-text preview). The
    ``type``/free-text filters are applied SERVER-SIDE over the FULL serialized
    payload — not the preview — so a match beyond the preview is still found
    (AC-C2). Every matching event, and each event's COMPLETE payload, stays
    reachable through the events route (the rows lazy-load the full payload) and
    the ``?limit`` paging (AC-C4). ``types`` lists all types of the log so the
    filter offers them even when the current window is filtered down.

    The optional inclusive ``from_seq``/``to_seq`` seq range is composed with the
    existing filters by logical AND (A1/R2); it never narrows ``types`` (R3), and
    the reported ``total`` stays the size of the full match set before the
    ``limit`` window."""
    types = sorted({e.get("type") for e in events if e.get("type")})
    q_low = q.lower() if q else None
    matches = []
    for e in events:
        if not _in_seq_range(e.get("seq"), from_seq, to_seq):
            continue
        if type_filter and e.get("type") != type_filter:
            continue
        full = _serialize_payload(e.get("payload"))
        if q_low and q_low not in full.lower() and q_low not in str(e.get("type") or "").lower():
            continue
        matches.append((e, full))
    window = [
        {"seq": e.get("seq"), "type": e.get("type"), "payload_text": full[:_RAW_PAYLOAD_PREVIEW]}
        for e, full in matches[:limit]
    ]
    return {"total": len(matches), "types": types, "window": window}
