"""Read-only FastAPI GUI over the ADW event log (GUI-SPEC §7, §8).

Every web import (FastAPI, Jinja2) lives in this module so the core ``adw run``
path stays free of the web stack — ``adw gui`` imports it lazily, and the tests
build the app via :func:`create_app`. The GUI is strictly read-only: it reads
only below the resolved ``.adw/runs/<run_id>/`` directory (plus the registry file
and its own packaged templates/static assets), runs no external program and
exposes no write route. The read data layer (``reader``, ``model``, ``registry``)
is only consumed here, never modified.
"""

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from adw.env import safe_env
from adw.gui.model import build_tree
from adw.gui.reader import EventReader
from adw.gui.registry import _slug, _unique_slug, load_registry
from adw.state import RUN_ID_RE, RUNS_RELPATH, RunState, StateNotFoundError

_HERE = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))

# Aufgabe A/C: the bottleneck is DOM NODE COUNT, not payload size. The server
# renders only this many entries per child list / raw page; the rest stay fully
# reachable through the read-only events route the client pages from (E8 — the
# display is windowed, the log is never truncated). The concrete windowing
# mechanism is an implementation choice and is not pinned by the contract.
_DISPLAY_WINDOW = 100
# Upper bound for the ``?limit`` paging so a crafted value cannot ask the server to
# materialise an unbounded amount at once.
_LIMIT_MAX = 200000
# Server-rendered raw rows preview only this many payload characters (the full
# payload stays reachable via the events route); keeps a page with a few huge
# early payloads from inlining megabytes into the initial HTML.
_RAW_PAYLOAD_PREVIEW = 2000
# git-diff timeout (seconds); the diff endpoint reads only — mirrors snapshots.py.
_GIT_DIFF_TIMEOUT = 30


def _parse_limit(raw) -> int:
    """The ``?limit`` window size, clamped to [1, _LIMIT_MAX]; a missing or invalid
    value falls back to the default display window."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DISPLAY_WINDOW
    return max(1, min(value, _LIMIT_MAX))


# Hard cap on the number of ENTRY NODES materialised in the DOM per collection
# (Aufgabe A): the trace tree and the Tools entries each stay at most this many,
# independent of the total number of entries. The ``?offset`` moving window slides
# this bounded slice so every entry stays reachable without ever growing a prefix.
# The per-entry markers ``data-tree-entry`` / ``data-tool-entry`` are the selectors
# the automated tests count and the guide documents.
_ENTRY_CAP = 200


def _parse_offset(raw) -> int:
    """The ``?offset`` moving-window start (>= 0); a missing/invalid value is 0."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, 100_000_000))


def _entry_window(limit) -> int:
    """The per-collection entry-node budget: at most ``_ENTRY_CAP``, and never more
    than the requested ``?limit`` — so the DOM entry count stays bounded even when a
    large ``?limit`` is requested (the bound holds throughout navigation)."""
    return max(1, min(limit, _ENTRY_CAP))


def _clamp_window(total, offset, size):
    """A moving window ``[lo, hi)`` of ``size`` entries into ``total``: ``lo`` is
    clamped so a large offset lands on the LAST window (the tail stays reachable),
    never past the end."""
    lo = max(0, min(offset, max(0, total - size)))
    return lo, min(lo + size, total)


def _flatten_tree(tree):
    """The trace tree as a flat list of ``(node, depth)`` in document (pre-order)
    order — the single global sequence the entry budget is measured over (so nesting
    can never multiply the budget per sibling group)."""
    flat = []

    def walk(nodes, depth):
        for n in nodes:
            flat.append((n, depth))
            walk(n.get("children") or [], depth + 1)

    walk(tree, 0)
    return flat


def _tree_window(tree, offset, size):
    """The bounded, MOVING slice of the trace tree: one global budget of ``size``
    entries across ALL nesting levels together. Returns the flat rows to render
    (each marked as exactly one trace entry) and the window bounds."""
    flat = _flatten_tree(tree)
    lo, hi = _clamp_window(len(flat), offset, size)
    return {
        "rows": [{"node": n, "depth": d} for n, d in flat[lo:hi]],
        "offset": lo, "shown": hi - lo, "total": len(flat),
    }


def _tool_entries(tree):
    entries = []

    def walk(nodes):
        for n in nodes:
            if n.get("type") == "agent.run":
                for c in n.get("children") or []:
                    if c.get("type") in ("agent.tool.call", "agent.tool.result"):
                        entries.append((n.get("seq"), c))
            walk(n.get("children") or [])

    walk(tree)
    return entries


def _tool_window(tree, offset, size):
    """One GLOBAL budget over every tool entry of every agent.run pane in document
    order — hidden, non-active panes included — so the tool-entry DOM count stays
    bounded across ALL panes, not per pane. Returns ``{agent_run_seq: [tool nodes]}``
    for the current window plus the window bounds for the navigation hint."""
    entries = _tool_entries(tree)
    lo, hi = _clamp_window(len(entries), offset, size)
    per = {}
    for seq, c in entries[lo:hi]:
        per.setdefault(seq, []).append(c)
    return {"per": per, "offset": lo, "shown": hi - lo, "total": len(entries)}


def _pane_nodes(tree, tree_rows):
    """The nodes whose detail panes are materialised: every node in the current
    trace-tree window (so a visible entry is selectable) plus every agent.run (there
    are few) so its Tools tab — the tool-entry window — is available regardless of
    where the tree window currently sits. Intermediate hidden panes are NOT eagerly
    materialised, so the pane DOM stays bounded too."""
    seen = set()
    order = []

    def add(n):
        if id(n) in seen:
            return
        seen.add(id(n))
        order.append(n)

    for row in tree_rows:
        add(row["node"])

    def walk(nodes):
        for n in nodes:
            if n.get("type") == "agent.run":
                add(n)
            walk(n.get("children") or [])

    walk(tree)
    return order


# --- presentation formatting (Aufgabe E): applied in the HTML templates only; the
# JSON API keeps its raw numeric values. Missing values render empty, never 0/null.


def _fmt_duration(seconds) -> str:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return ""
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _fmt_cost(cost) -> str:
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        return ""
    return f"${cost:.2f}"


def _fmt_ts(ts) -> str:
    """ISO-UTC → ``YYYY-MM-DD HH:MM:SS`` in UTC (no ``Z``, no fractional seconds)."""
    if not isinstance(ts, str) or not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


_TEMPLATES.env.filters["fmt_duration"] = _fmt_duration
_TEMPLATES.env.filters["fmt_cost"] = _fmt_cost
_TEMPLATES.env.filters["fmt_ts"] = _fmt_ts

# The seven workflow phases in order (contract PhaseStatus enum / GUI-SPEC §7.2).
PHASES = ["spec", "plan", "build", "integration", "codex_review", "final_review", "ci"]

_POLL_SECONDS = 0.5
_ISSUE_MAX = 120


@dataclass
class RepoRef:
    slug: str
    path: str | None
    exists: bool


# --- repo resolution: registry repos ∪ --repo repos, addressed by stable slug ---


def _resolve_repos(extra_repos) -> dict[str, RepoRef]:
    refs: dict[str, RepoRef] = {}
    used: set[str] = set()
    by_path: dict[str, str] = {}
    for entry in load_registry().repos:
        refs[entry.slug] = RepoRef(slug=entry.slug, path=entry.path, exists=bool(entry.exists))
        used.add(entry.slug)
        if entry.path:
            by_path[os.path.normpath(entry.path)] = entry.slug
    for raw in extra_repos or []:
        try:
            canonical = os.path.normpath(str(Path(raw).resolve()))
        except OSError:
            canonical = os.path.normpath(str(raw))
        if canonical in by_path:
            continue  # already known via the registry — same stable slug
        slug = _slug(canonical)
        if slug in used:
            slug = _unique_slug(canonical, used)
        used.add(slug)
        by_path[canonical] = slug
        refs[slug] = RepoRef(slug=slug, path=canonical, exists=os.path.isdir(canonical))
    return refs


# --- run metadata (from the run span + state.json), tree, phases, problems ------


def _ts_epoch(ts):
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _runs_root(repo_path) -> Path | None:
    """The resolved ``.adw/runs`` directory — but ONLY if it stays within the
    resolved repository. A ``.adw/runs`` that is itself a symlink to somewhere
    outside the registered repo would otherwise become the containment root and
    expose arbitrary external runs (GUI-SPEC §8). Anchoring to the resolved repo
    (not to the resolved runs dir) closes that escape; per-run/per-file symlinks
    are still checked against this validated root by :func:`_contained`."""
    repo_root = Path(repo_path).resolve()
    runs_root = (repo_root / RUNS_RELPATH).resolve()
    if runs_root == repo_root or not runs_root.is_relative_to(repo_root):
        return None
    return runs_root


def _contained(path: Path, root: Path) -> Path | None:
    """The real path if it stays within ``root`` after resolving EVERY symlink,
    else None. The read-only scope (GUI-SPEC §8) must never follow a symlinked run
    directory or events/state file out of the resolved runs tree — resolving both
    sides consistently neutralises legitimate symlinks in the root itself."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(root) else None


def _read_events(run_dir: Path, runs_root: Path):
    events_file = _contained(run_dir / "events.jsonl", runs_root)
    if events_file is None or not events_file.is_file():
        return [], []
    result = EventReader(events_file).read()
    return result.events, result.problems


def _load_state(run_dir: Path, runs_root: Path, repo_path, run_id):
    state_file = _contained(run_dir / "state.json", runs_root)
    if state_file is None or not state_file.is_file():
        return None
    try:
        return RunState.load(Path(repo_path), run_id)
    except StateNotFoundError:
        return None


def _run_span(events):
    """The run's first ``run`` start (its beginning) and the end of the LAST
    ``run`` span (Aufgabe A). A gated run is several CLI commands and thus several
    ``run`` spans in one log; the reported status is the last span's, not the
    first's.

    The last span's end is matched to the last ``run`` start by **span id** and
    must occur after that start — with interleaved spans (start A, start B, end B,
    end A, e.g. from concurrent appenders) the plain last ``run`` end belongs to
    an earlier-started span, so span-id matching is required. ``end`` is None when
    the last span is still open (→ running), even if earlier spans finished."""
    first_start = None
    last_start_idx = None
    for i, e in enumerate(events):
        if e.get("type") == "run" and e.get("kind") == "start":
            if first_start is None:
                first_start = e
            last_start_idx = i
    if last_start_idx is None:
        # No run start at all: defensively fall back to the last run end, if any.
        ends = [e for e in events if e.get("type") == "run" and e.get("kind") == "end"]
        return first_start, (ends[-1] if ends else None)
    last_span = events[last_start_idx].get("span")
    for e in events[last_start_idx + 1:]:
        if e.get("type") == "run" and e.get("kind") == "end" and e.get("span") == last_span:
            return first_start, e
    return first_start, None  # the last run span remains open → running


def _summary(slug, run_id, events, state) -> dict:
    start_rec, end_rec = _run_span(events)
    start_payload = (start_rec or {}).get("payload") or {}
    end_payload = (end_rec or {}).get("payload") or {}
    totals = end_payload.get("totals") or {}
    issue = start_payload.get("issue")
    if issue is None and state is not None:
        # A run without an event log (Aufgabe G) still names its issue in state.
        issue = state.issue
    if isinstance(issue, str) and len(issue) > _ISSUE_MAX:
        issue = issue[:_ISSUE_MAX] + "…"
    if end_rec is not None:
        status = end_payload.get("status")
    elif start_rec is not None:
        status = "running"  # a run span started but has no matching end yet
    else:
        # No run span at all (a state-only run, Aufgabe G): the status is not
        # derivable from state — leave it empty, never a false 'running'.
        status = None
    return {
        "run_id": run_id,
        "repo": slug,
        "repo_exists": True,
        "issue": issue,
        "phase": state.phase if state is not None else None,
        "status": status,
        "start": (start_rec or {}).get("ts"),
        "duration": totals.get("duration"),
        "cost": totals.get("cost"),
        "event_count": len(events),
        # Aufgabe G: a clear indication whether a trace exists for this run.
        "has_trace": bool(events),
    }


def _phase_bar(events, state_phase) -> list[dict]:
    spans: dict[str, dict] = {}
    starts: dict[str, tuple] = {}
    failed: set[str] = set()  # phases the run failed/escalated from
    for e in events:
        etype = e.get("type")
        if etype == "escalation":
            # The escalation point names the phase it escalated FROM (phases.py).
            phase = (e.get("payload") or {}).get("phase")
            if phase:
                failed.add(phase)
            continue
        if etype != "phase":
            continue
        sid = e.get("span")
        payload = e.get("payload") or {}
        name = payload.get("name")
        if e.get("kind") == "start":
            starts[sid] = (name, e.get("ts"))
            if name:
                spans.setdefault(name, {}).setdefault("start", e.get("ts"))
        elif e.get("kind") == "end":
            sname = name or starts.get(sid, (None,))[0]
            if sname and payload.get("to_phase") == "escalated":
                failed.add(sname)  # ended straight into the terminal escalated state
            if sname:
                info = spans.setdefault(sname, {})
                info["end"] = e.get("ts")
                st = starts.get(sid)
                if st and "start" not in info:
                    info["start"] = st[1]
    current_idx = PHASES.index(state_phase) if state_phase in PHASES else None
    bar = []
    for i, name in enumerate(PHASES):
        info = spans.get(name)
        duration = None
        if info and info.get("start") and info.get("end"):
            a, b = _ts_epoch(info["start"]), _ts_epoch(info["end"])
            duration = (b - a) if (a is not None and b is not None) else None
        ended = bool(info and info.get("start") and info.get("end"))
        if name in failed:  # failure wins over a merely-present end record
            status = "failed"
        elif ended:
            status = "completed"
        elif name == state_phase:
            status = "active"
        elif current_idx is not None and i < current_idx:
            status = "completed"
        else:
            status = "pending"
        bar.append({"name": name, "status": status, "duration": duration})
    return bar


def _is_span(node) -> bool:
    return hasattr(node, "children")


# Per-tool priority for the main argument shown next to a tool call (Aufgabe C).
# The named cases are pinned by the spec (Read → file path, Bash → command, Grep
# → pattern); the ordered fallback keys cover other tools with an unambiguous main
# argument. Absent any of these, the tool name is shown alone (nothing invented).
_TOOL_ARG_PRIORITY = {
    "Read": ("file_path",),
    "Bash": ("command",),
    "Grep": ("pattern",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "Glob": ("pattern",),
}
_TOOL_ARG_FALLBACK = ("file_path", "command", "pattern", "path", "file", "query", "url")
_TOOL_ARG_MAX = 80


def _tool_main_arg(tool, inp) -> str | None:
    if not isinstance(inp, dict):
        return None
    keys = _TOOL_ARG_PRIORITY.get(tool, ()) + _TOOL_ARG_FALLBACK
    for key in keys:
        value = inp.get(key)
        if isinstance(value, str) and value:
            return value if len(value) <= _TOOL_ARG_MAX else value[:_TOOL_ARG_MAX] + "…"
    return None


def _tool_call_label(p) -> str:
    if not isinstance(p, dict) or not p.get("tool"):
        return "agent.tool.call"  # no tool name → keep the type name (invent nothing)
    tool = str(p["tool"])
    arg = _tool_main_arg(tool, p.get("input"))
    return f"{tool} {arg}" if arg else tool


def _tool_result_label(p, tool_names) -> str:
    if not isinstance(p, dict):
        return "agent.tool.result"
    # A result payload rarely carries the tool name directly; it references the
    # call via ``tool_use_id``. Without a resolvable tool identity, invent nothing
    # and keep the type name (spec C3 / contract fallbacks).
    tool = p.get("tool") or tool_names.get(p.get("tool_use_id"))
    if not tool:
        return "agent.tool.result"
    # Only ``is_error`` / ``exit_code`` are actual outcome fields; ``content`` is
    # the payload body, not an outcome, so a content-only result stays the type
    # name rather than being invented as success.
    is_error = p.get("is_error")
    exit_code = p.get("exit_code")
    if isinstance(is_error, bool):
        failed = is_error
    elif isinstance(exit_code, int) and not isinstance(exit_code, bool):
        # No explicit is_error: a nonzero exit code is a failure, zero a success —
        # never the opposite (a nonzero exit must not be presented as ``ok``).
        failed = exit_code != 0
    else:
        return "agent.tool.result"  # no valid outcome signal → keep the type name
    outcome = "error" if failed else "ok"
    if exit_code is not None:
        return f"{outcome} (exit {exit_code})"
    return outcome


def _node_label(node, tool_names=None) -> str:
    tool_names = tool_names or {}
    if _is_span(node):
        p = node.start_payload or node.end_payload or {}
        if isinstance(p, dict):
            if node.type in ("phase", "lane") and p.get("name"):
                return str(p["name"])
            if node.type == "agent.run" and p.get("agent"):
                return str(p["agent"])
            if node.type == "gate" and p.get("name"):
                return str(p["name"])
            if node.type == "round":
                return f"round {p.get('n')}/{p.get('cap')}"
        return node.type or "?"
    if node.type == "agent.tool.call":
        return _tool_call_label(node.payload)
    if node.type == "agent.tool.result":
        return _tool_result_label(node.payload, tool_names)
    return node.type or "?"


def _node_status(node):
    if not _is_span(node):
        return None
    if node.running:
        return "running"
    if node.type == "gate":
        return "passed" if (node.end_payload or {}).get("passed") else "failed"
    return "done"


def _subtree_cost(node):
    """Sum of ``cost_usd`` over every ``agent.run`` in the subtree, or None when no
    descendant carries usage (AC 11: an absent cost is null, not zero)."""
    total = None
    if _is_span(node):
        if node.type == "agent.run":
            cost = (node.end_payload or {}).get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                total = float(cost)
        for child in node.children:
            child_cost = _subtree_cost(child)
            if child_cost is not None:
                total = (total or 0.0) + child_cost
    return total


def _aggregate_outcome(node):
    """A per-node-type outcome for phase/lane/round aggregates: the frozen end
    payloads use different keys (round: ``outcome``, lane: ``completed``, phase:
    ``to_phase``), so none of them is a literal ``outcome`` field."""
    ep = node.end_payload or {}
    if node.type == "round":
        return ep.get("outcome")
    if node.type == "lane":
        completed = ep.get("completed")
        return "completed" if completed is True else ("failed" if completed is False else None)
    if node.type == "phase":
        return ep.get("to_phase")
    return None


def _tool_names_by_use_id(events) -> dict:
    """Map ``tool_use_id`` → tool name from the ``agent.tool.call`` records, so a
    ``agent.tool.result`` (which carries only the id) can resolve its tool name
    (Aufgabe C fallback semantics)."""
    names: dict = {}
    for e in events:
        if e.get("type") == "agent.tool.call":
            p = e.get("payload") or {}
            uid, tool = p.get("tool_use_id"), p.get("tool")
            if uid and tool:
                names[uid] = str(tool)
    return names


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
        payload = e.get("payload") or {}
        lane = payload.get("lane")
        ref = payload.get("ref")
        if not isinstance(seq, int) or not lane or not _is_snapshot_ref(ref, run_id):
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


def _serialize(node, tool_names=None, *, lane=None, own_ranges=None, snaps=None) -> dict:
    tool_names = tool_names or {}
    own_ranges = own_ranges or {}
    snaps = snaps or {}
    if _is_span(node):
        payload = node.start_payload if node.start_payload is not None else node.end_payload
        node_lane = lane
        if node.type == "lane" and isinstance(payload, dict) and payload.get("name"):
            node_lane = payload["name"]
        children = [
            _serialize(c, tool_names, lane=node_lane, own_ranges=own_ranges, snaps=snaps)
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
            "children": children,
        }
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
        "children": [],
    }


def _serialize_payload(payload) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(payload)


def _raw_view(events, limit, *, q=None, type_filter=None) -> dict:
    """Run-level Raw tab data (Aufgabe C): the distinct event types, and a bounded
    window of ``limit`` matching rows (seq, type, a payload-text preview). The
    ``type``/free-text filters are applied SERVER-SIDE over the FULL serialized
    payload — not the preview — so a match beyond the preview is still found
    (AC-C2). Every matching event, and each event's COMPLETE payload, stays
    reachable through the events route (the rows lazy-load the full payload) and
    the ``?limit`` paging (AC-C4). ``types`` lists all types of the log so the
    filter offers them even when the current window is filtered down."""
    types = sorted({e.get("type") for e in events if e.get("type")})
    q_low = q.lower() if q else None
    matches = []
    for e in events:
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
            model_by_span[e.get("span")] = (e.get("payload") or {}).get("model")
    per: dict = {}
    for e in events:
        if e.get("type") == "agent.run" and e.get("kind") == "end":
            usage = (e.get("payload") or {}).get("usage")
            if not isinstance(usage, dict):
                continue
            model = model_by_span.get(e.get("span")) or "?"
            tokens = sum(
                v for v in usage.values()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            )
            per[model] = per.get(model, 0) + int(tokens)
    return [{"model": m, "tokens": t} for m, t in per.items()]


def _events_cost(events):
    """Total ``cost_usd`` over the run's ``agent.run`` spans, or None when none
    carries a cost (a dry run → empty, never a false 0)."""
    total = None
    for e in events:
        if e.get("type") == "agent.run" and e.get("kind") == "end":
            cost = (e.get("payload") or {}).get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                total = (total or 0.0) + float(cost)
    return total


# The event types whose spans are pure WAITING (time passes without work): CI
# polling and gate runtime (A3).
_WAITING_TYPES = {"ci.wait", "gate"}


def _timeline_bar_label(node_type, payload) -> str:
    if isinstance(payload, dict):
        if node_type in ("phase", "lane") and payload.get("name"):
            return str(payload["name"])
        if node_type == "agent.run" and payload.get("agent"):
            return str(payload["agent"])
        if node_type == "gate" and payload.get("name"):
            return str(payload["name"])
    return node_type or "?"


def _timeline(events) -> dict:
    """Swimlanes + bars + header for the Timeline tab, derived from ``events``. A
    run with no event log yields ``has_trace=False`` so the tab shows a clear
    "no trace" indication rather than an error (A8)."""
    if not events:
        return {"has_trace": False, "lanes": [], "duration": None, "cost": None,
                "models": []}

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
        payload = s.get("payload") or {}
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

    start_rec, end_rec = _run_span(events)
    totals = ((end_rec or {}).get("payload") or {}).get("totals") or {}
    duration = totals.get("duration")
    if duration is None:
        a = _ts_epoch((start_rec or {}).get("ts"))
        # A finished run measures to its run-end; a live/open run measures the
        # ELAPSED time to the current timeline endpoint, so the header shows a
        # non-empty duration for a live run too (A4) — consistent with the open
        # span bars, which also extend to `t_end`.
        b = _ts_epoch(end_rec.get("ts")) if end_rec is not None else t_end
        duration = (b - a) if (a is not None and b is not None) else None
    cost = totals.get("cost")
    if cost is None:
        cost = _events_cost(events)
    return {
        "has_trace": True,
        "lanes": [lanes_map[k] for k in lane_order],
        "duration": duration,
        "cost": cost,
        "models": _tokens_per_model(events),
    }


# --- Artifacts tab + content route (Aufgabe B) ----------------------------------
# The Artifacts tab lists the whitelisted artifacts of the run and the dual-author
# drafts; content is served by the read-only route below. `{name}` is a SINGLE path
# segment, resolved through a fixed whitelist — never treated as a filesystem path.

# The eight whitelisted top-level artifact names (GUI-SPEC §7.2 / contract).
_ARTIFACT_TOP_LEVEL = [
    "issue.md", "spec.md", "plan.md", "contract.yaml",
    "escalation.md", "followups.md", "spec-summary.md", "plan-summary.md",
]
_ARTIFACT_TOP_LEVEL_SET = set(_ARTIFACT_TOP_LEVEL)
# The artifacts produced through dual authoring — each has a claude/codex draft.
_ARTIFACT_DUAL = ["spec.md", "plan.md", "contract.yaml"]
_DRAFT_AUTHORS = ("claude", "codex")


def _draft_names(top_name: str) -> list[str]:
    stem, ext = top_name.rsplit(".", 1)
    return [f"{stem}.{author}.{ext}" for author in _DRAFT_AUTHORS]


def _resolve_artifact(run_dir: Path, name: str) -> Path | None:
    """Map a single-segment artifact ``name`` to a file path inside ``run_dir`` via
    the fixed whitelist, or None if the name is unknown. The name is NEVER treated
    as a filesystem path: any separator, ``..`` or absolute form makes it unknown
    without any filesystem access (B4/B5)."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    if name in _ARTIFACT_TOP_LEVEL_SET:
        return run_dir / name
    parts = name.split(".")
    if len(parts) >= 3:
        author = parts[-2]
        stem_ext = ".".join(parts[:-2]) + "." + parts[-1]
        if author in _DRAFT_AUTHORS and stem_ext in _ARTIFACT_TOP_LEVEL_SET:
            # Drafts are addressed by their FLAT filename → drafts/<name>.
            return run_dir / "drafts" / name
    return None


def _artifact_present(run_dir: Path, relpath: str) -> bool:
    # Contain against THIS run's directory (not the whole runs tree): a whitelisted
    # name symlinked to a SIBLING run's file resolves outside run_dir and must count
    # as missing, never present (B4/B5/B6).
    contained = _contained(run_dir / relpath, run_dir)
    return contained is not None and contained.is_file()


def _artifacts_listing(run_dir: Path) -> list[dict]:
    """The whitelisted artifacts of the run and, for the dual-authored ones, their
    two drafts — each marked present (a fetch anchor) or missing (B1/B2/B3). A
    missing artifact/draft is never an error."""
    items = []
    for name in _ARTIFACT_TOP_LEVEL:
        drafts = []
        if name in _ARTIFACT_DUAL:
            for dname in _draft_names(name):
                drafts.append({
                    "name": dname,
                    "present": _artifact_present(run_dir, f"drafts/{dname}"),
                })
        items.append({
            "name": name,
            "present": _artifact_present(run_dir, name),
            "drafts": drafts,
        })
    return items


def _run_detail(
    ref: RepoRef, run_id: str, run_dir: Path, runs_root: Path, *,
    limit=_DISPLAY_WINDOW, raw_q=None, raw_type=None,
) -> dict:
    events, problems = _read_events(run_dir, runs_root)
    state = _load_state(run_dir, runs_root, ref.path, run_id)
    tool_names = _tool_names_by_use_id(events)
    own_ranges = _span_seq_ranges(events)
    snaps = _snapshots_by_lane(events, run_id)
    return {
        "run": _summary(ref.slug, run_id, events, state),
        "phases": _phase_bar(events, state.phase if state is not None else None),
        "tree": [
            _serialize(n, tool_names, own_ranges=own_ranges, snaps=snaps)
            for n in build_tree(events)
        ],
        "problems": [asdict(p) for p in problems],
        "raw": _raw_view(events, limit, q=raw_q, type_filter=raw_type),
    }


# --- read-only snapshot diff (Aufgabe B, AC-B1/B2/B4) ---------------------------


def _is_snapshot_ref(value, run_id) -> bool:
    """Whether ``value`` has the EXACT snapshot-ref structure of this run:
    ``refs/adw/<run_id>/<seq>`` with a numeric seq. This rejects malformed,
    range-like (``a..b``) and option-like (``--output=…``, ``-p``) values and
    foreign-run refs BEFORE git is ever invoked — so a crafted or corrupt snapshot
    event whose ``ref`` is not a plain snapshot ref can never reach git as an
    option or revision (AC-B2/B3, strictly read-only). Membership in the event-log
    allowlist is still required in addition to this structural check."""
    return (
        isinstance(value, str)
        and re.fullmatch(rf"refs/adw/{re.escape(run_id)}/[0-9]+", value) is not None
    )


def _snapshot_refs(events) -> set:
    """The exact set of ref names appearing in ``snapshot`` events of this run —
    the allowlist the diff endpoint validates ``from``/``to`` against (AC-B2). A
    pattern match alone is never sufficient; membership in this set is required."""
    refs = set()
    for e in events:
        if e.get("type") == "snapshot":
            ref = (e.get("payload") or {}).get("ref")
            if isinstance(ref, str) and ref:
                refs.add(ref)
    return refs


def _parse_numstat(text: str) -> list:
    """Parse ``git diff --numstat`` output into per-file counts, in git's order.
    A binary file's ``-`` count becomes JSON ``null`` (mirroring numstat)."""
    files = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_s, del_s = parts[0], parts[1]
        files.append({
            "path": "\t".join(parts[2:]),
            "additions": None if add_s == "-" else int(add_s),
            "deletions": None if del_s == "-" else int(del_s),
        })
    return files


def _git_diff(repo_path, frm: str, to: str) -> dict:
    """Run ``git diff`` between two already-allowlisted refs like the orchestrator
    (AC-B4): a list argv (no shell), ``core.hooksPath=/dev/null``, ``safe_env()``,
    a timeout. Reads only — no worktree switch, no ref created/updated/deleted.

    BOTH git invocations are checked: if either fails (a dangling/removed ref, a
    concurrent gc, a one-sided failure), the result is NOT a valid diff and must
    never be presented as an (empty) success — a controlled non-5xx error is
    raised so no diff data is silently lost or misrepresented."""
    base = ["git", "-C", str(repo_path), "-c", "core.hooksPath=/dev/null"]
    env = safe_env()
    numstat = subprocess.run(
        [*base, "diff", "--numstat", frm, to],
        capture_output=True, text=True, timeout=_GIT_DIFF_TIMEOUT, env=env,
    )
    patch = subprocess.run(
        [*base, "diff", frm, to],
        capture_output=True, text=True, timeout=_GIT_DIFF_TIMEOUT, env=env,
    )
    if numstat.returncode != 0 or patch.returncode != 0:
        # Allowlisted and well-formed, but git could not produce the diff (e.g. the
        # snapshot object is gone). Report it as unavailable, never a false empty
        # diff. Non-5xx, and never a partial/one-sided result.
        raise HTTPException(status_code=404, detail="Snapshot diff unavailable")
    return {"files": _parse_numstat(numstat.stdout), "patch": patch.stdout}


def _list_runs(refs: dict[str, RepoRef]) -> list[dict]:
    entries: list[dict] = []
    for ref in refs.values():
        if not ref.exists or not ref.path:
            # The placeholder is reserved for an UNREACHABLE repo (its registered
            # path is gone) — not for a reachable repo that simply has no runs yet.
            entries.append(
                {"repo": ref.slug, "repo_exists": False, "hint": ref.path or "(unknown path)"}
            )
            continue
        runs_dir = Path(ref.path) / RUNS_RELPATH
        runs_root = _runs_root(ref.path)
        if runs_root is None or not runs_dir.is_dir():
            # Reachable repo, but no readable/valid runs directory (none yet, or an
            # escaping runs root): contribute nothing — never a false 'unavailable'.
            continue
        try:
            children = sorted(runs_dir.iterdir())
        except OSError:
            # Unreadable runs directory (e.g. permissions): a run is only listable
            # while its directory is readable — skip THIS repo, never fail the whole
            # endpoint (contract: one repo's failure does not drop the others).
            continue
        for child in children:
            if not RUN_ID_RE.fullmatch(child.name):
                continue
            try:
                run_dir = _contained(child, runs_root)  # skip symlinks escaping the tree
                if run_dir is None or not run_dir.is_dir():
                    continue
                events_file = _contained(run_dir / "events.jsonl", runs_root)
                if events_file is not None and events_file.is_file():
                    events = EventReader(events_file).read().events
                else:
                    events = []  # Aufgabe G: a run may predate instrumentation
                state = _load_state(run_dir, runs_root, ref.path, child.name)
                if not events and state is None:
                    continue  # neither a trace nor state → not a listable run
            except OSError:
                continue  # one unreadable run must not drop the rest of the repo
            entries.append(_summary(ref.slug, child.name, events, state))
    # Stable ordering: newest start first, then running runs pulled to the front.
    entries.sort(key=lambda e: e.get("start") or "", reverse=True)
    entries.sort(key=lambda e: 0 if e.get("status") == "running" else 1)
    return entries


# --- SSE live tail --------------------------------------------------------------


def _sse_event(record: dict) -> str:
    return f"id: {record['seq']}\ndata: {json.dumps(record, ensure_ascii=False)}\n\n"


def _sse_problem(data: dict) -> str:
    return f"event: problem\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_last_event_id(raw):
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _stream(events_path, last_id):
    """Tail ``events.jsonl`` by byte offset (a single reader carries the offset in
    memory — no cursor on disk). Emit each new accepted event with a valid integer
    seq as ``id:``/``data:``; surface reader problems (and records without a valid
    seq) as ``event: problem`` without an id; close after the run end event."""
    if events_path is None:
        return  # nothing safe to tail (missing or escaping events log)
    reader = EventReader(events_path)
    while True:
        result = reader.read()
        for problem in result.problems:
            yield _sse_problem(asdict(problem))
        for record in result.events:
            seq = record.get("seq")
            is_end = record.get("type") == "run" and record.get("kind") == "end"
            if not isinstance(seq, int):
                yield _sse_problem(
                    {"kind": "bad_seq", "line_no": None, "byte_offset": None,
                     "message": "record without a valid integer seq"}
                )
                continue
            if last_id is not None and seq <= last_id:
                # Already replayed — but the run end still closes the stream, so a
                # reconnect at/after the final seq does not poll the file forever.
                if is_end:
                    return
                continue
            yield _sse_event(record)
            if is_end:
                return
        time.sleep(_POLL_SECONDS)


# --- app factory ----------------------------------------------------------------


def create_app(repos=None) -> FastAPI:
    app = FastAPI(title="ADW Read-only GUI")
    static_dir = _HERE / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    refs = _resolve_repos(repos)

    def resolve_repo(slug: str) -> RepoRef:
        ref = refs.get(slug)
        if ref is None:
            raise HTTPException(status_code=404, detail=f"Unknown repository slug: {slug}")
        return ref

    def require_run(slug: str, run_id: str) -> tuple[RepoRef, Path, Path]:
        ref = resolve_repo(slug)
        if not RUN_ID_RE.fullmatch(run_id):
            raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}")
        if not ref.exists or not ref.path:
            raise HTTPException(status_code=404, detail=f"Repository {slug} unreachable")
        runs_root = _runs_root(ref.path)
        if runs_root is None:  # .adw/runs symlinked outside the repository
            raise HTTPException(status_code=404, detail=f"No run {run_id}")
        run_dir = _contained(runs_root / run_id, runs_root)
        if run_dir is None:  # the run directory is a symlink escaping the tree
            raise HTTPException(status_code=404, detail=f"No run {run_id}")
        events_file = _contained(run_dir / "events.jsonl", runs_root)
        state_file = _contained(run_dir / "state.json", runs_root)
        has_events = events_file is not None and events_file.is_file()
        has_state = state_file is not None and state_file.is_file()
        if not has_events and not has_state:  # absent, or only escaping symlinks
            raise HTTPException(status_code=404, detail=f"No run {run_id}")
        return ref, run_dir, runs_root

    @app.get("/api/runs")
    def api_runs():
        return _list_runs(refs)

    @app.get("/api/runs/{repo}/{run_id}")
    def api_run_detail(repo: str, run_id: str):
        ref, run_dir, runs_root = require_run(repo, run_id)
        return _run_detail(ref, run_id, run_dir, runs_root)

    @app.get("/api/runs/{repo}/{run_id}/events")
    def api_run_events(
        repo: str, run_id: str, from_seq: int | None = None, to_seq: int | None = None
    ):
        _, run_dir, runs_root = require_run(repo, run_id)
        events, _problems = _read_events(run_dir, runs_root)
        if from_seq is not None:
            events = [e for e in events if isinstance(e.get("seq"), int) and e["seq"] >= from_seq]
        if to_seq is not None:
            # An optional upper bound (still the same read-only route) lets the
            # client fetch a single record (from_seq==to_seq) instead of the whole
            # tail from a seq to the log end.
            events = [e for e in events if isinstance(e.get("seq"), int) and e["seq"] <= to_seq]
        return events

    @app.get("/api/runs/{repo}/{run_id}/diff")
    def api_run_diff(repo: str, run_id: str, request: Request):
        # Containment first (unknown slug 404, bad run_id 400, absent run 404).
        ref, run_dir, runs_root = require_run(repo, run_id)
        # `from`/`to` are read from the raw query so a missing/empty value is a
        # controlled 400 (never a framework 422), and `from` (a keyword) is fine.
        frm = request.query_params.get("from")
        to = request.query_params.get("to")
        if not frm or not to:
            raise HTTPException(status_code=400, detail="Missing 'from'/'to' snapshot ref")
        # AC-B2/B3: a value must BOTH have the exact snapshot-ref structure of this
        # run AND appear in its snapshot events — both checks run BEFORE git, so a
        # malformed/option-like/range value (even one recorded in the log) is never
        # passed to git and can never be interpreted as a git option or revision.
        if not (_is_snapshot_ref(frm, run_id) and _is_snapshot_ref(to, run_id)):
            raise HTTPException(status_code=400, detail="Malformed snapshot ref")
        events, _problems = _read_events(run_dir, runs_root)
        allowed = _snapshot_refs(events)
        if frm not in allowed or to not in allowed:
            raise HTTPException(status_code=404, detail="Unknown snapshot ref for this run")
        return _git_diff(ref.path, frm, to)

    @app.get("/api/runs/{repo}/{run_id}/artifacts/{name}")
    def api_run_artifact(repo: str, run_id: str, name: str):
        # Containment first (unknown slug 404, bad run_id 400, absent run 404).
        _, run_dir, _ = require_run(repo, run_id)
        # Resolve the single-segment name through the whitelist BEFORE any file
        # access: an unknown/nested/encoded/traversal name is 404 without a read.
        mapped = _resolve_artifact(run_dir, name)
        if mapped is None:
            raise HTTPException(status_code=404, detail=f"Unknown artifact: {name!r}")
        # Contain against THIS run's directory (run_dir is already fully resolved),
        # so a whitelisted name that is a symlink escaping the run directory — out
        # of the runs tree OR into a SIBLING run — is 404 and its target is never
        # read (B4/B5/B6). runs_root is the wider bound the diff/events routes use;
        # for a single artifact the run directory is the correct, tighter bound.
        contained = _contained(mapped, run_dir)
        if contained is None or not contained.is_file():
            raise HTTPException(status_code=404, detail=f"No artifact {name}")
        # Serve the RAW bytes verbatim — no lossy decode — so the complete, faithful
        # content is returned (the client renders it as monospace text, E10). The
        # media type is not pinned by the contract; only the full-content and
        # faithful-bytes properties are (B4/B7).
        return Response(
            content=contained.read_bytes(), media_type="text/plain; charset=utf-8"
        )

    @app.get("/api/runs/{repo}/{run_id}/stream")
    def api_run_stream(repo: str, run_id: str, request: Request):
        _, run_dir, runs_root = require_run(repo, run_id)
        events_file = _contained(run_dir / "events.jsonl", runs_root)
        last_id = _parse_last_event_id(request.headers.get("last-event-id"))
        return StreamingResponse(
            _stream(events_file, last_id), media_type="text/event-stream"
        )

    @app.get("/", response_class=HTMLResponse)
    def run_list_page():
        html = _TEMPLATES.get_template("run_list.html").render({"entries": _list_runs(refs)})
        return HTMLResponse(html)

    @app.get("/runs/{repo}/{run_id}", response_class=HTMLResponse)
    def run_detail_page(repo: str, run_id: str, request: Request):
        ref, run_dir, runs_root = require_run(repo, run_id)
        # Aufgabe A/C: `?limit` is how much of each long list / the raw log the
        # server materialises (a bounded DOM by default). "Load more" is a plain
        # server-rendered link that raises it — no divergent client-side rendering.
        limit = _parse_limit(request.query_params.get("limit"))
        # Aufgabe A: `?offset` slides a bounded, moving window over the trace tree and
        # the tool entries, so the DOM entry count stays capped (independent of the
        # total) while every entry stays reachable — reaching a late entry never
        # re-materialises the preceding ones.
        offset = _parse_offset(request.query_params.get("offset"))
        # Raw-tab filters (Aufgabe C): applied server-side over the full payload so
        # a match beyond the rendered preview is still found; empty -> no filter.
        raw_q = request.query_params.get("raw_q") or None
        raw_type = request.query_params.get("raw_type") or None
        detail = _run_detail(
            ref, run_id, run_dir, runs_root, limit=limit, raw_q=raw_q, raw_type=raw_type
        )
        # Bound the materialised entry nodes by COUNT (Aufgabe A): one global budget
        # per collection, held across nesting levels and across navigation.
        window = _entry_window(limit)
        tree_window = _tree_window(detail["tree"], offset, window)
        tool_window = _tool_window(detail["tree"], offset, window)
        pane_nodes = _pane_nodes(detail["tree"], tree_window["rows"])
        # The Timeline derives from the same events Trace uses; the Artifacts tab
        # lists the whitelisted files of the run. Both are page-render concerns and
        # stay out of the JSON detail contract.
        events, _problems = _read_events(run_dir, runs_root)
        html = _TEMPLATES.get_template("run_detail.html").render({
            "detail": detail, "limit": limit, "offset": offset,
            "raw_q": raw_q or "", "raw_type": raw_type or "",
            "tree_window": tree_window, "tool_window": tool_window, "pane_nodes": pane_nodes,
            "timeline": _timeline(events),
            "artifacts": _artifacts_listing(run_dir),
        })
        return HTMLResponse(html)

    return app
