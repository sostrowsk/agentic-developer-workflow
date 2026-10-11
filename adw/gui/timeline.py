"""Timeline tab and the node-time run context."""

import bisect
from datetime import UTC, datetime

from adw.gui.fmt import (
    _is_number,
    _mapping_payload,
)
from adw.gui.runs import (
    _run_totals,
    _ts_epoch,
)
from adw.gui.trace import (
    _WAITING_TYPES,
    _is_span,
)

# --- Timeline tab (Aufgabe A) ---------------------------------------------------
# The timeline derives its swimlanes and bars from the run's ALREADY-loaded event
# log (the same events Trace uses) — no new reader, no change to model.py (A2). One
# lane per strand present: orchestrator (the run span), spec, plan, each build lane,
# codex, CI. Active vs. waiting (CI polling, gate runtime) is distinguished; a
# still-running span is drawn to the current edge; the header shows total duration,
# total cost and tokens per model. Drawn with own means only (CSS) — no library.


def _tokens_per_model(events) -> list[dict]:
    """Sum ``usage`` tokens per model over the run's ``agent.run`` spans (the model
    is on the start, the usage on the end). A dry run carries no ``usage`` → an
    empty list → the header renders no token line (never a false 0, GUI-SPEC §12)."""
    model_by_span: dict = {}
    for e in events:
        if e.get("type") == "agent.run" and e.get("kind") == "start":
            model_by_span[e.get("span")] = _mapping_payload(e).get("model")
    per: dict = {}
    for e in events:
        if e.get("type") == "agent.run" and e.get("kind") == "end":
            usage = _mapping_payload(e).get("usage")
            if not isinstance(usage, dict):
                continue
            model = model_by_span.get(e.get("span")) or "?"
            tokens = sum(
                v for v in usage.values()
                if _is_number(v)
            )
            per[model] = per.get(model, 0) + int(tokens)
    return [{"model": m, "tokens": t} for m, t in per.items()]


# --- node-time run context (GUI-SPEC §7.2) -------------------------------------
# A purely DERIVED, read-only projection of the SAME event stream the detail view
# already loaded (no new reader/route/event/persistence): the run state AT the seq
# of a node. A node's cutoff is its own seq (point) or its exposed end_seq (span,
# the subtree maximum); only events with seq <= cutoff participate, so an earlier
# node never sees a later event (time travel). Every absent datum is null, never a
# fabricated 0 (E4): counts start at None and become a number only on the first
# occurrence, and the cost sums agent.run cost_usd over the cut list.
# The API value is exact — rounding is a presentation concern only (see
# `_compact_context` / app.js).

_CONTEXT_FIELDS = (
    "phase", "round", "limit_hits", "circuit_breakers", "cost_usd", "followups",
)


def _empty_context() -> dict:
    """The six-field context with every field null — a run/section with nothing yet
    observed (a run without a trace, or a cutoff before any relevant event)."""
    return {k: None for k in _CONTEXT_FIELDS}


def _phase_observation(e):
    """The phase this event observes, or None. Exactly two existing sources count
    (C3): a non-empty ``name`` in a ``phase`` span start, and a non-empty ``phase``
    in a ``state.saved`` payload. Empty/absent values are not observations."""
    payload = e.get("payload")
    if not isinstance(payload, dict):
        return None
    name = None
    if e.get("type") == "phase" and e.get("kind") == "start":
        name = payload.get("name")
    elif e.get("type") == "state.saved":
        name = payload.get("phase")
    return name if isinstance(name, str) and name else None


def _round_subtree_ranges(roots, own_ranges):
    """For every recorded ``round`` span, its FULL subtree seq range
    ``(lo, hi, {loop, n, cap})`` — the same subtree-range semantics the serialized
    tree exposes as ``end_seq``. A round's own events (``_span_seq_ranges``) cover
    only its own span id; an OPEN round whose newest events belong to a nested
    agent/child span would otherwise end at its start event, wrongly excluding the
    cutoff. Combining descendant ranges through the built tree fixes that (one
    post-order pass, memo-free but linear)."""
    rounds = []

    def walk(node):
        if not _is_span(node):
            s = node.seq
            return (s, s) if isinstance(s, int) else (None, None)
        lo, hi = own_ranges.get(node.span_id, (None, None))
        if not isinstance(lo, int):
            lo = hi = node.seq if isinstance(node.seq, int) else None
        for child in node.children:
            clo, chi = walk(child)
            if isinstance(clo, int):
                lo = clo if not isinstance(lo, int) else min(lo, clo)
            if isinstance(chi, int):
                hi = chi if not isinstance(hi, int) else max(hi, chi)
        if node.type == "round":
            p = node.start_payload if isinstance(node.start_payload, dict) else {}
            rounds.append(
                (lo, hi, {"loop": p.get("loop"), "n": p.get("n"), "cap": p.get("cap")})
            )
        return (lo, hi)

    for root in roots:
        walk(root)
    return rounds


def _context_deriver(events, round_ranges):
    """Return ``context_at(cutoff)`` deriving the six-field context at any seq. The
    prefix scans are computed ONCE (sorted by the unique seq) so per-node lookups
    stay cheap on a large tree; the round containment scans the (few) round spans
    by their FULL subtree ranges (``round_ranges``)."""
    seq_events = sorted(
        (e for e in events if isinstance(e.get("seq"), int)),
        key=lambda e: e["seq"],
    )
    seqs = [e["seq"] for e in seq_events]
    n = len(seq_events)
    pre_limit = [0] * (n + 1)
    pre_cb = [0] * (n + 1)
    pre_fu = [0] * (n + 1)
    pre_cost = [None] * (n + 1)
    pre_phase = [None] * (n + 1)
    cost = None
    phase = None
    for i, e in enumerate(seq_events, start=1):
        t = e.get("type")
        pre_limit[i] = pre_limit[i - 1] + (1 if t == "limit.hit" else 0)
        pre_cb[i] = pre_cb[i - 1] + (1 if t == "circuit_breaker" else 0)
        pre_fu[i] = pre_fu[i - 1] + (1 if t == "followup" else 0)
        if t == "agent.run" and e.get("kind") == "end":
            payload = e.get("payload")
            c = payload.get("cost_usd") if isinstance(payload, dict) else None
            if _is_number(c):
                cost = (cost or 0.0) + float(c)
        pre_cost[i] = cost
        obs = _phase_observation(e)
        if obs is not None:
            phase = obs
        pre_phase[i] = phase

    # Only rounds with a usable subtree range participate in containment.
    rounds = [
        (lo, hi, payload) for lo, hi, payload in round_ranges
        if isinstance(lo, int) and isinstance(hi, int)
    ]

    def context_at(cutoff) -> dict:
        if not isinstance(cutoff, int):
            return _empty_context()
        i = bisect.bisect_right(seqs, cutoff)
        # The innermost enclosing round is the latest-starting one still containing
        # the cutoff; its start and end events count as contained (C4).
        rnd = None
        best_lo = None
        for lo, hi, payload in rounds:
            if lo <= cutoff <= hi and (best_lo is None or lo > best_lo):
                best_lo, rnd = lo, payload
        return {
            "phase": pre_phase[i],
            "round": rnd,
            "limit_hits": pre_limit[i] or None,
            "circuit_breakers": pre_cb[i] or None,
            # The exact cumulative cost (sum of agent.run cost_usd); the API
            # value is not rounded — presentation rounds it (compact_context/app.js).
            "cost_usd": pre_cost[i],
            "followups": pre_fu[i] or None,
        }

    return context_at


def _latest_cutoff(events):
    """The greatest observed seq — the cutoff for ``latest_context`` — or None when
    the run has no events (then the context is all null)."""
    seqs = [e.get("seq") for e in events if isinstance(e.get("seq"), int)]
    return max(seqs) if seqs else None


def _timeline_bar_label(node_type, payload) -> str:
    if isinstance(payload, dict):
        if node_type in ("phase", "lane") and payload.get("name"):
            return str(payload["name"])
        if node_type == "agent.run" and payload.get("agent"):
            return str(payload["agent"])
        if node_type == "gate" and payload.get("name"):
            return str(payload["name"])
    return node_type or "?"


def _timeline(events, has_trace=None) -> dict:
    """Swimlanes + bars + header for the Timeline tab, derived from ``events``. A
    run with no event log yields ``has_trace=False`` so the tab shows a clear
    "no trace" indication rather than an error (A8).

    ``has_trace`` answers the SAME question as the summary's field — does the log
    EXIST — so an existing but empty/unreadable log is not announced as a trace in
    the summary and as "no event log" in this tab. ``None`` keeps the old
    event-derived answer for callers that cannot see the file."""
    if not events:
        return {"has_trace": bool(has_trace), "lanes": [], "duration": None,
                "cost": None, "models": []}

    starts: dict = {}
    ends: dict = {}
    order: list = []
    for e in events:
        sid = e.get("span")
        if e.get("kind") == "start":
            if sid not in starts:
                starts[sid] = e
                order.append(sid)
        elif e.get("kind") == "end":
            ends[sid] = e

    epochs = [x for x in (_ts_epoch(e.get("ts")) for e in events) if x is not None]
    t0 = min(epochs) if epochs else 0.0
    max_epoch = max(epochs) if epochs else 0.0
    # A6: a still-running span extends to the CURRENT edge, not to the newest logged
    # event. For a live run (any unended span) the timeline endpoint — used for the
    # total scale and for every open span — is the current time; a finished run
    # keeps the last event time so its bars stay stable and deterministic.
    has_open = any(sid not in ends for sid in starts)
    if has_open:
        t_end = max(datetime.now(UTC).timestamp(), max_epoch)
    else:
        t_end = max_epoch
    total = (t_end - t0) or 1.0

    lanes_map: dict = {}
    lane_order: list = []

    def lane(key, label):
        if key not in lanes_map:
            lanes_map[key] = {"key": key, "label": label, "bars": []}
            lane_order.append(key)
        return lanes_map[key]

    for sid in order:
        s = starts[sid]
        e = ends.get(sid)
        typ = s.get("type")
        payload = _mapping_payload(s)
        if typ == "run":
            key, label = "orchestrator", "orchestrator"
        elif typ == "phase":
            name = payload.get("name")
            if name not in ("spec", "plan"):
                continue  # build/integration/… are shown via their lane/strand
            key, label = name, name
        elif typ == "lane":
            label = str(payload.get("name") or "lane")
            key = "lane:" + label
        elif typ == "agent.run":
            lane_name = s.get("lane")
            if not lane_name:
                continue  # an agent.run bar only lands on a build lane it belongs to
            label = str(lane_name)
            key = "lane:" + label
        elif typ == "gate":
            lane_name = s.get("lane")
            if lane_name:
                label = str(lane_name)
                key = "lane:" + label
            else:
                # A gate outside any lane (e.g. the integration E2E gate,
                # phases._run_e2e_gate) carries lane=None: its runtime is still
                # WAITING and must be drawn — on the orchestrator lane — so the
                # active/waiting distinction is not lost (A3).
                key, label = "orchestrator", "orchestrator"
        elif typ in ("codex.review", "codex.author"):
            key, label = "codex", "codex"
        elif typ == "ci.wait":
            key, label = "ci", "CI"
        else:
            continue
        s0 = _ts_epoch(s.get("ts"))
        s0 = t0 if s0 is None else s0
        s1 = _ts_epoch(e.get("ts")) if e is not None else t_end
        s1 = t_end if s1 is None else s1
        left = max((s0 - t0) / total * 100.0, 0.0)
        width = max((s1 - s0) / total * 100.0, 0.5)
        lane(key, label)["bars"].append({
            "seq": s.get("seq"),
            "label": _timeline_bar_label(typ, payload),
            "state": "waiting" if typ in _WAITING_TYPES else "active",
            "running": e is None,  # an unended span is drawn to the current edge
            "left": round(left, 3),
            "width": round(width, 3),
        })

    # A1/B3: the timeline header's run figures come from the corrected whole-run
    # summary (summed over ALL closed spans) — the same source the list and the
    # run-detail head use. There is NO elapsed-time or event-cost fallback: an
    # unknown metric stays empty (E6), so a live run with no completed span shows an
    # empty Work/cost, consistent with the summary. The bar geometry above keeps its
    # own elapsed `t_end` for drawing open spans; that is separate and unaffected.
    totals = _run_totals(events)
    duration = totals["duration"]
    cost = totals["cost"]
    return {
        "has_trace": True,
        "lanes": [lanes_map[k] for k in lane_order],
        "duration": duration,
        "cost": cost,
        "models": _tokens_per_model(events),
    }
