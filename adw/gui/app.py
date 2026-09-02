"""Read-only FastAPI GUI over the ADW event log (GUI-SPEC §7, §8).

Every web import (FastAPI, Jinja2) lives in this module so the core ``adw run``
path stays free of the web stack — ``adw gui`` imports it lazily, and the tests
build the app via :func:`create_app`. The GUI is strictly read-only: it reads
only below the resolved ``.adw/runs/<run_id>/`` directory (plus the registry file
and its own packaged templates/static assets), runs no external program and
exposes no write route. The read data layer (``reader``, ``model``, ``registry``)
is only consumed here, never modified.
"""

import bisect
import difflib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from adw.env import safe_env
from adw.gui import i18n
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


# Hard cap on the number of Tools ENTRY NODES materialised in the DOM (Aufgabe A):
# the tool entries inside the detail panes stay at most this many, independent of
# the total. The ``?tools_offset`` moving window slides this bounded slice so every
# entry stays reachable without ever growing a prefix. The trace COLUMN is exempt —
# it renders every node and is kept readable by compaction, not by a window.
# ``data-tool-entry`` is the marker the automated tests count.
_ENTRY_CAP = 200


def _parse_offset(raw) -> int:
    """A moving-window start (>= 0) such as ``?tools_offset``; a missing or invalid
    value is 0."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, 100_000_000))


def _parse_seq_bound(raw):
    """An optional INCLUSIVE Raw seq bound: the integer value, or None when the
    value is missing or non-numeric — in which case that bound is inactive (a
    one-sided or absent range). This mirrors the tolerance ``_parse_limit`` /
    ``_parse_offset`` already show toward invalid Raw parameters, so a crafted or
    empty bound is a defined no-op, never a 5xx (E4 / R4)."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _entry_window(limit) -> int:
    """The Tools entry-node budget: at most ``_ENTRY_CAP``, and never more than the
    requested ``?limit`` — so the Tools DOM entry count stays bounded even when a
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


def _tree_rows(tree):
    """EVERY node of the trace tree as a flat row list — the column is not paged.
    What keeps the column readable is the compaction (folded results, repeat and
    group nodes, phases collapsed by default), not a cut: folding hides nothing,
    a window did. ``?offset`` is therefore inert for the trace tree; the Tools
    entries inside the panes keep their own window (``?tools_offset``)."""
    flat = _flatten_tree(tree)
    return {"rows": [{"node": n, "depth": d} for n, d in flat], "total": len(flat)}


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


def _focus_index(tree, seq):
    """The pre-order index of the node with ``seq``, or None. Used by ``?focus`` to
    resolve the targeted node — a result focused this way is redirected to its call,
    whose own seq then carries the selection (A5)."""
    for i, (n, _) in enumerate(_flatten_tree(tree)):
        if n.get("seq") == seq:
            return i
    return None


def _pane_nodes(tree):
    """The nodes that get their OWN server-rendered detail pane: the SPAN nodes.
    Their panes carry content only the server can build — the aggregates of a
    phase/lane/round, a gate's command and output, a review's findings table, an
    agent.run's prompt, prompt diff and Tools window, plus any Diff tab.

    POINT nodes (tool calls/results, messages, snapshots, …) are the many, and their
    pane is nothing but the event payload. Since the trace column is no longer paged,
    one pane per node would put thousands of hidden elements into the DOM — the very
    node-count bottleneck the display bounds exist for (``docs/gui-response-time.md``).
    They share ONE server-rendered pane shell instead, which the client fills from the
    read-only events route on selection (the same lazy load the tool bodies use); no
    DOM is constructed in JS (GUI-SPEC §7.3)."""
    order = []

    def walk(nodes):
        for n in nodes:
            if "end_seq" in n:  # a span — a point node has no subtree range
                order.append(n)
            walk(n.get("children") or [])

    walk(tree)
    return order


# --- trace-tree compaction (Trace-Baum verdichten): a PAGE-LOCAL presentation layer
# over the already-windowed pre-order rows. It never touches the JSON ``tree`` (the
# API stays the unverdichtete source, contract-pinned); it only shapes how the tree
# COLUMN of the loaded page renders. Fold each result into its call (A1), collapse
# repeated (A2) and group adjacent (A3) Read/Grep/Glob operations, and report the
# per-page line balance (A6). Grouping joins only DIRECT neighbours in the row list
# (E4) and never reaches past the loaded page (E3), because it sees only the window.

_FOLDABLE_TOOLS = ("Read", "Grep", "Glob")


def _tool_use_id(node):
    p = node.get("payload")
    return p.get("tool_use_id") if isinstance(p, dict) else None


def _tool_name(node):
    p = node.get("payload")
    return p.get("tool") if isinstance(p, dict) else None


def _tool_target(node):
    """The raw comparison target of a foldable call (exact string, no normalisation):
    Read → ``input.file_path``, Grep/Glob → ``input.pattern``. None (never compared)
    for anything else or when the target is absent (spec: no target → no summary)."""
    p = node.get("payload")
    inp = p.get("input") if isinstance(p, dict) else None
    if not isinstance(inp, dict):
        return None
    tool = p.get("tool")
    key = "file_path" if tool == "Read" else ("pattern" if tool in ("Grep", "Glob") else None)
    if key is None:
        return None
    value = inp.get(key)
    return value if isinstance(value, str) and value else None


def _result_outcome(result_node):
    """The determinable outcome of a folded result, read from its existing label
    (``_tool_result_label``): ``"ok"``/``"error"`` — or None when undetermined, which
    is NEVER presented as success (spec A1)."""
    label = result_node.get("label")
    if not isinstance(label, str):
        return None
    if label.startswith("error"):
        return "error"
    if label == "ok" or label.startswith("ok "):
        return "ok"
    return None


def _iso_dt(ts):
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fold_duration(call_node, result_node):
    """``ts(result) − ts(call)`` in seconds when both are parseable and the diff is
    ``>= 0``; otherwise None (undetermined, no substitute value). Subtracting a
    timezone-aware from a naive datetime raises ``TypeError`` — that mismatch is
    treated as undetermined too, never as a 500 (A1)."""
    a = _iso_dt(call_node.get("ts"))
    b = _iso_dt(result_node.get("ts"))
    if a is None or b is None:
        return None
    try:
        delta = (b - a).total_seconds()
    except TypeError:
        return None  # mixed timezone awareness -> undetermined duration
    return delta if delta >= 0 else None


def _node_entry(item):
    return {"kind": "node", "node": item["node"], "depth": item["depth"],
            "result": item.get("result"), "outcome": item.get("outcome"),
            "duration": item.get("duration")}


def _is_groupable(item):
    """Whether the item is a foldable Read/Grep/Glob call with no determinate error
    (a determinate error is never taken into a collector, spec A2/A3)."""
    node = item["node"]
    return (node.get("type") == "agent.tool.call"
            and _tool_name(node) in _FOLDABLE_TOOLS
            and item.get("outcome") != "error")


def _repeat_entry(items, depth):
    durs = [x["duration"] for x in items if x.get("duration") is not None]
    return {"kind": "repeat", "depth": depth, "count": len(items),
            "duration": (sum(durs) if durs else None),
            "children": [_node_entry(x) for x in items]}


def _repeat_children(run):
    """Collapse maximal runs of target-identical, same-tool neighbours (>= 2) into a
    repeat; everything else stays a single node entry, in original order."""
    children = []
    j = 0
    while j < len(run):
        tool = _tool_name(run[j]["node"])
        target = _tool_target(run[j]["node"])
        k = j + 1
        if target is not None:
            while (k < len(run) and _tool_name(run[k]["node"]) == tool
                   and _tool_target(run[k]["node"]) == target):
                k += 1
        if k - j >= 2:
            children.append(_repeat_entry(run[j:k], run[j]["depth"]))
        else:
            children.append(_node_entry(run[j]))
            k = j + 1
        j = k
    return children


def _group_entry(children, depth):
    count = 0
    ops = []
    for c in children:
        if c["kind"] == "repeat":
            count += c["count"]
            tool = _tool_name(c["children"][0]["node"])
        else:
            count += 1
            tool = _tool_name(c["node"])
        if tool and tool not in ops:
            ops.append(tool)
    return {"kind": "group", "depth": depth, "count": count, "ops": ops,
            "children": children}


def _compact_rows(rows):
    """Shape the windowed pre-order rows (each ``{"node", "depth"}``) into the trace
    column's display entries plus the per-page line balance. Returns
    ``{"entries": [...], "rows": int, "folded": int}``:

    * A1 — a result whose ``tool_use_id`` matches the immediately preceding call folds
      into it (outcome + determinable duration); anything else stays its own node.
    * A2 — >= 2 target-identical same-tool neighbours collapse into a repeat.
    * A3 — an uninterrupted Read/Grep/Glob run with >= 2 children (after A1/A2)
      becomes a group; below that threshold nothing is wrapped.
    * ``rows`` — display entries outside any collector (originals + collectors).
    * ``folded`` — window events minus the entries that are themselves an original
      event (so attached results and every collector member count as folded)."""
    items = []
    for row in rows:
        node = row["node"]
        if node.get("type") == "agent.tool.result" and items:
            prev = items[-1]
            uid = _tool_use_id(node)
            if (prev["node"].get("type") == "agent.tool.call"
                    and prev.get("result") is None
                    and uid is not None
                    and _tool_use_id(prev["node"]) == uid):
                prev["result"] = node
                prev["outcome"] = _result_outcome(node)
                prev["duration"] = _fold_duration(prev["node"], node)
                continue
        items.append({"node": node, "depth": row["depth"], "result": None,
                      "outcome": None, "duration": None})

    entries = []
    i = 0
    while i < len(items):
        if _is_groupable(items[i]):
            depth = items[i]["depth"]
            run = []
            while i < len(items) and _is_groupable(items[i]) and items[i]["depth"] == depth:
                run.append(items[i])
                i += 1
            children = _repeat_children(run)
            if len(children) >= 2:
                entries.append(_group_entry(children, depth))
            else:
                entries.extend(children)
        else:
            entries.append(_node_entry(items[i]))
            i += 1

    original = sum(1 for e in entries if e["kind"] == "node")
    return {"entries": entries, "rows": len(entries), "folded": len(rows) - original}


def _node_determinate_error(node) -> bool:
    """Whether a node is a determinate error by the existing outcome rules
    (``is_error: true``, else ``exit_code != 0``, else a failed span); an
    undetermined result is not an error."""
    if node.get("type") == "agent.tool.result":
        p = node.get("payload") or {}
        ie, ec = p.get("is_error"), p.get("exit_code")
        if isinstance(ie, bool):
            return ie
        if isinstance(ec, int) and not isinstance(ec, bool):
            return ec != 0
        return False
    return node.get("status") == "failed"


def _subtree_has_error(node) -> bool:
    if _node_determinate_error(node):
        return True
    return any(_subtree_has_error(c) for c in (node.get("children") or []))


def _default_open_phase(tree):
    """The ``seq`` of the phase the tree opens with (A5): the tree-order-first phase
    whose subtree carries a determinate error, else the last-started phase, else None
    when the run has no phase node. Needs the FULL tree, so it is a server decision."""
    phases = []

    def walk(nodes):
        for n in nodes:
            if n.get("type") == "phase":
                phases.append(n)
            walk(n.get("children") or [])

    walk(tree)
    if not phases:
        return None
    for ph in phases:
        if _subtree_has_error(ph):
            return ph.get("seq")
    return phases[-1].get("seq")


# The main-argument keys that hold a filesystem PATH (A4): their full path is always
# kept in the ``title``, whether or not the visible text was shortened.
_PATH_ARG_KEYS = ("file_path", "path", "file")


def _raw_main_arg(tool, inp):
    """The ``(value, key)`` of a tool call's UNtruncated main argument (the key so a
    path argument can be told from a non-path one), or ``(None, None)``."""
    if isinstance(inp, dict):
        for key in _TOOL_ARG_PRIORITY.get(tool, ()) + _TOOL_ARG_FALLBACK:
            value = inp.get(key)
            if isinstance(value, str) and value:
                return value, key
    return None, None


def _repo_relative(value, repo_root):
    """``value`` made repo-relative when it genuinely resolves INSIDE ``repo_root``,
    else ``value`` unchanged (A4: a path outside the repo stays visibly unchanged).

    Containment is decided on NORMALISED absolute paths, not a lexical prefix — so a
    traversal path that escapes the repo (``/root/../outside/x``) and a mere textual
    prefix collision (``/rootkit/x`` against ``/root``) are NOT shortened. The
    filesystem is never touched (the path may not exist); ``..`` is resolved
    lexically only. A non-absolute value is never treated as a repo-absolute path."""
    if not (isinstance(value, str) and value and isinstance(repo_root, str) and repo_root):
        return value
    if not os.path.isabs(value):
        return value
    root = os.path.normpath(repo_root)
    norm = os.path.normpath(value)
    if norm == root or norm.startswith(root + os.sep):
        return os.path.relpath(norm, root)
    return value


def _display_label(node, repo_root):
    """The (visible label, full-path title) for a tree node (A4). A tool call shows
    the repo-relative main argument in the text and keeps the full raw value in the
    title; every other node keeps its serialized label and has no title."""
    if node.get("type") != "agent.tool.call":
        return node.get("label"), None
    p = node.get("payload") or {}
    tool = p.get("tool")
    if not tool:
        return node.get("label"), None
    raw, key = _raw_main_arg(tool, p.get("input"))
    if raw is None:
        return str(tool), None
    rel = _repo_relative(raw, repo_root)
    shown = rel if len(rel) <= _TOOL_ARG_MAX else rel[:_TOOL_ARG_MAX] + "…"
    # A4: a PATH argument always keeps its complete path in the title — including an
    # outside-repo path shown unchanged — so the full path stays reachable. A
    # non-path argument (e.g. a Bash command) gets a title only when the visible text
    # was actually shortened, so a value shown in full is not duplicated redundantly.
    title = raw if (key in _PATH_ARG_KEYS or shown != raw) else None
    return f"{tool} {shown}", title


def _annotate_paths(entries, repo_root) -> None:
    """Attach the A4 display ``label``/``title`` to every node entry (recursively
    through collectors); the serialized ``label`` in the API ``tree`` stays absolute."""
    for e in entries:
        if e["kind"] == "node":
            e["label"], e["title"] = _display_label(e["node"], repo_root)
        else:
            _annotate_paths(e["children"], repo_root)


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


def _compact_context(ctx):
    """A context dict for embedding as a data attribute with every null-valued key
    DROPPED (recursively for ``round``). The client reads a missing key exactly as a
    null one (empty display), and the compact form keeps the literal ``null`` out of
    the rendered markup (the polish 'no None/null' rule). ``cost_usd`` is rounded
    HERE (presentation only — the JSON API value stays exact) so no raw float noise
    leaks into the attribute. Serialised via ``tojson`` afterwards, so
    HTML/attribute escaping is unchanged."""
    if not isinstance(ctx, dict):
        return {}
    out = {}
    for k, v in ctx.items():
        if v is None:
            continue
        if k == "round" and isinstance(v, dict):
            out[k] = {rk: rv for rk, rv in v.items() if rv is not None}
        elif k == "cost_usd" and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = round(v, 6)
        else:
            out[k] = v
    return out


_TEMPLATES.env.filters["fmt_duration"] = _fmt_duration
_TEMPLATES.env.filters["fmt_cost"] = _fmt_cost
_TEMPLATES.env.filters["fmt_ts"] = _fmt_ts
_TEMPLATES.env.filters["compact_context"] = _compact_context

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


def _events_source(run_dir: Path, runs_root: Path):
    """The contained event-log file to read: ``events.jsonl`` if present, else the
    gzipped ``events.jsonl.gz`` (a pruned but kept run). When both exist the plain
    file is authoritative (consistent with --gzip, C5/C6). Returns None when neither
    is a readable contained file."""
    plain = _contained(run_dir / "events.jsonl", runs_root)
    if plain is not None and plain.is_file():
        return plain
    gz = _contained(run_dir / "events.jsonl.gz", runs_root)
    if gz is not None and gz.is_file():
        return gz
    return None


def _read_events(run_dir: Path, runs_root: Path):
    events_file = _events_source(run_dir, runs_root)
    if events_file is None:
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


def _latest_approval_event(events):
    """The most recent ``approval`` event's ``event`` value (``awaited`` or
    ``granted``) over the log, or None. The approval events sit on the run span
    (cli.py:480/721); a later ``granted`` supersedes an earlier ``awaited``."""
    latest = None
    for e in events:
        if e.get("type") == "approval":
            ev = (e.get("payload") or {}).get("event")
            if ev in ("awaited", "granted"):
                latest = ev
    return latest


def _awaiting_gate_phase(events, state_phase):
    """The business phase (``spec``/``plan``) a run is paused at for human approval,
    or None. The event log wins: the latest ``approval`` event, if ``awaited``
    without a later ``granted``, names the gate (``gate: spec`` → ``spec``,
    ``gate: plan`` → ``plan``). Without any approval event (a run without a trace),
    the state phase is the fallback (``awaiting_spec_approval`` → ``spec``,
    ``awaiting_approval`` → ``plan``)."""
    gate = None
    seen = False
    for e in events:
        if e.get("type") != "approval":
            continue
        payload = e.get("payload") or {}
        ev = payload.get("event")
        g = payload.get("gate")
        g = g if g in ("spec", "plan") else None
        if ev == "awaited":
            seen = True
            gate = g
        elif ev == "granted":
            seen = True
            if g == gate:
                # Only the grant naming the SAME gate lifts THIS pause; a grant for
                # a different gate belongs to another approval and leaves it awaiting.
                gate = None
    if seen:
        return gate
    if state_phase == "awaiting_spec_approval":
        return "spec"
    if state_phase == "awaiting_approval":
        return "plan"
    return None


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
        # A CLOSED run span keeps its terminal end-payload status untouched
        # (done/escalated/awaiting_approval) — the open-span derivation below never
        # rewrites a finished result (R2).
        status = end_payload.get("status")
    elif start_rec is not None:
        # An open run span is `running` — unless the event log shows it paused at a
        # human-approval gate (latest `approval` is `awaited`, no later `granted`),
        # in which case it is `awaiting_approval` even though the span is still open.
        if _latest_approval_event(events) == "awaited":
            status = "awaiting_approval"
        else:
            status = "running"
    elif not events and state is not None and state.phase in (
        "awaiting_approval", "awaiting_spec_approval"
    ):
        # Fallback WITHOUT a trace (E4): a run with no event log whose state phase is
        # an approval phase reports `awaiting_approval`. With a trace the event log is
        # authoritative (handled above), so this never overrides a later `granted`.
        status = "awaiting_approval"
    else:
        # No run span at all (a state-only run, Aufgabe G): the status is not
        # derivable from state — leave it empty, never a false 'running'.
        status = None
    return {
        "run_id": run_id,
        "repo": slug,
        "repo_exists": True,
        "issue": issue,
        # A dry run is derived exclusively from the `run` start payload (E1/E4): a
        # true value there is a dry run; an explicit false, a missing field or a
        # missing `run` span are all a normal run. Missing usage/token data never
        # participates — no heuristic.
        "dry_run": start_payload.get("dry_run") is True,
        "phase": state.phase if state is not None else None,
        "status": status,
        "start": (start_rec or {}).get("ts"),
        "duration": totals.get("duration"),
        "cost": totals.get("cost"),
        "event_count": len(events),
        # Aufgabe G: a clear indication whether a trace exists for this run.
        "has_trace": bool(events),
    }


def _mapping_payload(rec) -> dict:
    """The event's payload if it is a mapping, else an empty dict. A crafted or
    corrupt event whose ``payload`` is a truthy non-mapping (string/list/number)
    must never reach ``.get`` — it would raise and turn a read into a 5xx (AC 7)."""
    payload = rec.get("payload")
    return payload if isinstance(payload, dict) else {}


def _phase_bar(events, state_phase) -> list[dict]:
    spans: dict[str, dict] = {}
    starts: dict[str, tuple] = {}
    failed: set[str] = set()  # phases the run failed/escalated from
    for e in events:
        etype = e.get("type")
        if etype == "escalation":
            # The escalation point names the phase it escalated FROM (phases.py).
            phase = _mapping_payload(e).get("phase")
            if phase:
                failed.add(phase)
            continue
        if etype != "phase":
            continue
        sid = e.get("span")
        payload = _mapping_payload(e)
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
    # The business phase (spec/plan) the run is paused at for human approval, if any
    # (R3). Its span has already ended (into awaiting_*), so `awaiting` must win over
    # the `completed` it would otherwise get; after `granted` this is None again.
    awaiting = _awaiting_gate_phase(events, state_phase)
    bar = []
    for i, name in enumerate(PHASES):
        info = spans.get(name)
        duration = None
        if info and info.get("start") and info.get("end"):
            a, b = _ts_epoch(info["start"]), _ts_epoch(info["end"])
            duration = (b - a) if (a is not None and b is not None) else None
        ended = bool(info and info.get("start") and info.get("end"))
        if name == awaiting:  # waiting for human approval — not active, not completed
            status = "awaiting"
        elif name in failed:  # failure wins over a merely-present end record
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
        # An open span that is pure WAITING (CI poll, gate runtime) is `waiting`,
        # not `running` — the same `_WAITING_TYPES` the Timeline uses (R1). Every
        # other open span (agent.run, codex, run, phase, lane, round) stays running.
        return "waiting" if node.type in _WAITING_TYPES else "running"
    if node.type == "gate":
        return "passed" if (node.end_payload or {}).get("passed") else "failed"
    return "done"


def _subtree_cost(node):
    """Sum of ``cost_usd`` over every ``agent.run`` in the subtree, or None when no
    descendant carries usage (AC 11: an absent cost is null, not zero)."""
    total = None
    if _is_span(node):
        if node.type == "agent.run":
            ep = node.end_payload
            cost = ep.get("cost_usd") if isinstance(ep, dict) else None
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
            payload = e.get("payload")
            cost = payload.get("cost_usd") if isinstance(payload, dict) else None
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                total = (total or 0.0) + float(cost)
    return total


# --- node-time run context (GUI-SPEC §7.2) -------------------------------------
# A purely DERIVED, read-only projection of the SAME event stream the detail view
# already loaded (no new reader/route/event/persistence): the run state AT the seq
# of a node. A node's cutoff is its own seq (point) or its exposed end_seq (span,
# the subtree maximum); only events with seq <= cutoff participate, so an earlier
# node never sees a later event (time travel). Every absent datum is null, never a
# fabricated 0 (E4): counts start at None and become a number only on the first
# occurrence, and the cost reuses the single _events_cost rule over the cut list.
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
            if isinstance(c, (int, float)) and not isinstance(c, bool):
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
            # The exact cumulative cost (the shared _events_cost semantics); the API
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


# --- plan skeleton: the derived "what's still planned" projection ----------------
# A pure projection of the run's `plan.md` — read ONLY through the whitelist artifact
# path (`_resolve_artifact` + `_contained`, so a boundary-escaping symlink counts as
# absent) — and of the already-built trace (for the coarse lane status). PRESENT
# exactly when `plan.md` yields at least one `## Workstream:` section with a `###`
# task, ABSENT otherwise. No new event, route, reader or persistence (E2/E4/E5).

_WORKSTREAM_PREFIX = "## Workstream: "
_TASK_PREFIX = "### "


def _read_plan_md(run_dir: Path) -> str | None:
    """The run's `plan.md` text, or None when it is missing, unreadable, not a plain
    file, or a symlink escaping the run directory — resolved ONLY through the
    existing whitelist artifact path (never from URL segments)."""
    resolved = _resolve_artifact(run_dir, "plan.md")
    if resolved is None:
        return None
    contained = _contained(resolved, run_dir)
    if contained is None or not contained.is_file():
        return None
    try:
        return contained.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _parse_plan_sections(text: str) -> list[dict]:
    """The `## Workstream:` sections of `plan.md`, each with its `###` tasks, by
    EXACTLY two rules (E3): a section runs from a `## Workstream: <name>` line to the
    next `##` heading (any line starting with `##` that is not `###`) or EOF; a task
    is every `### ` line in it, its text taken VERBATIM after the `### ` prefix (no
    identifier pattern, no further trimming). A bare `###` yields no task; only
    sections with >= 1 task are returned, in document order."""
    sections: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith(_WORKSTREAM_PREFIX):
            current = {"workstream": line[len(_WORKSTREAM_PREFIX):], "tasks": []}
            sections.append(current)
        elif line.startswith("##") and not line.startswith("###"):
            current = None                       # any other `##` heading closes the section
        elif current is not None and line.startswith(_TASK_PREFIX):
            task = line[len(_TASK_PREFIX):]
            if task:                             # a `###`/`### ` with no text is no task
                current["tasks"].append(task)
    return [s for s in sections if s["tasks"]]


def _completed_lane_names(roots) -> set[str]:
    """The names of the `lane` spans whose end carries `completed: true` — the only
    lanes whose skeleton counts as `done` (S3_status). Derived from the already-built
    trace; no per-task or per-node status."""
    done: set[str] = set()

    def walk(nodes):
        for node in nodes or []:
            if node.type == "lane":
                payload = node.start_payload if isinstance(node.start_payload, dict) \
                    else (node.end_payload if isinstance(node.end_payload, dict) else {})
                name = payload.get("name")
                if name is not None and _aggregate_outcome(node) == "completed":
                    done.add(name)
            walk(getattr(node, "children", None))

    walk(roots)
    return done


def _plan_skeleton(run_dir: Path, roots) -> list[dict]:
    """The derived skeleton: one entry per `## Workstream:` section with >= 1 task,
    each `{workstream, status, tasks}` with a coarse lane-level `pending`/`done`. An
    empty list (no `plan.md`, or none matching) means the field is omitted (B3)."""
    text = _read_plan_md(run_dir)
    if text is None:
        return []
    sections = _parse_plan_sections(text)
    if not sections:
        return []
    done = _completed_lane_names(roots)
    return [
        {
            "workstream": s["workstream"],
            "status": "done" if s["workstream"] in done else "pending",
            "tasks": s["tasks"],
        }
        for s in sections
    ]


# --- recovery card: the derived next-step projection (Aufgabe backend) ----------
# A pure projection of the already-loaded run state (`state.phase`), the existing
# run-status derivation, the event stream and the server-resolved `RepoRef.path`.
# It is PRESENT exactly when the run needs human intervention and ABSENT otherwise
# (never an empty object). No new event, route, reader or persistence (E4).

# The abort events (`limit.hit`/`circuit_breaker`) that surround an escalation.
_ABORT_TYPES = ("limit.hit", "circuit_breaker")
# The approval-gate phases (kind `approve`) and the work phases (kind `resume`).
_APPROVAL_PHASES = ("awaiting_spec_approval", "awaiting_approval")


def _recovery(ref: RepoRef, run_id: str, state, run_summary, events) -> dict | None:
    """The recovery projection, or None when the run needs no human step.

    The kind follows ONLY the selection rule, evaluated on ``state.phase`` (never
    on the escalation event's ``phase``): ``escalated`` -> ``none`` (a new run is
    required); an approval gate -> ``approve``; a work phase whose derived
    run-status is not ``running`` -> ``resume``. ``done``, a running work phase, or
    no loadable state -> None."""
    phase = state.phase if state is not None else None
    if phase is None:
        return None
    if phase == "escalated":  # strictly precedes every other branch (E5, AC 1/2)
        return _escalated_recovery(events)
    if phase in _APPROVAL_PHASES:
        return {"kind": "approve", "needs_new_run": False,
                "command": _recovery_command("approve", ref, run_id)}
    if phase in PHASES and run_summary.get("status") != "running":
        return {"kind": "resume", "needs_new_run": False,
                "command": _recovery_command("resume", ref, run_id)}
    return None


def _recovery_command(kind: str, ref: RepoRef, run_id: str) -> str:
    """The finished, copyable CLI command with the real run_id and the real,
    server-resolved registry path — both rendered POSIX-shell-safely so the path
    stays ONE ``--repo`` argument (AC 3/4). The command line is never translated."""
    return f"adw {kind} {shlex.quote(run_id)} --repo {shlex.quote(ref.path or '')}"


def _escalated_recovery(events) -> dict:
    """The ``none`` variant: no continuation command, the new-run flag, and the
    escalation context anchored at the governing (greatest-``seq``) ``escalation``
    event. Missing/untypical data is null/empty, never invented (AC 5/6/7/9)."""
    escalations = sorted(
        (e for e in events if e.get("type") == "escalation" and isinstance(e.get("seq"), int)),
        key=lambda e: e["seq"],
    )
    reason = phase = anchor_seq = None
    aborts: list[dict] = []
    if escalations:
        governing = escalations[-1]
        anchor_seq = governing["seq"]
        # An atypical (non-mapping) payload — a crafted or corrupt string/list/number
        # — yields null reason/phase, never a 5xx (AC 7). Only a real mapping carries
        # reason/phase, taken verbatim.
        payload = _mapping_payload(governing)
        reason = payload.get("reason")
        phase = payload.get("phase")
        # The aborts of THIS escalation: between the immediately prior escalation
        # (exclusive) and the governing one (exclusive), in event order.
        prior_seq = escalations[-2]["seq"] if len(escalations) >= 2 else None
        for e in events:
            seq = e.get("seq")
            if e.get("type") not in _ABORT_TYPES or not isinstance(seq, int):
                continue
            if seq < anchor_seq and (prior_seq is None or seq > prior_seq):
                # The payload is carried VERBATIM (no truthiness coercion): a present
                # null/empty/list/populated payload survives unchanged (AC 6/7). A
                # genuinely ABSENT payload is represented as `null` — the same
                # "no value" the context panel uses — never a fabricated `{}`
                # (P7_robust: missing/atypical data is never given a substitute).
                aborts.append({"type": e["type"], "seq": seq, "payload": e.get("payload")})
        aborts.sort(key=lambda a: a["seq"])
    return {
        "kind": "none",
        "needs_new_run": True,
        "command": None,
        "anchor_seq": anchor_seq,
        "reason": reason,
        "phase": phase,
        "aborts": aborts,
        "escalation_artifact": "escalation.md",
    }


def _run_detail(
    ref: RepoRef, run_id: str, run_dir: Path, runs_root: Path, *,
    limit=_DISPLAY_WINDOW, raw_q=None, raw_type=None, raw_from_seq=None, raw_to_seq=None,
) -> dict:
    events, problems = _read_events(run_dir, runs_root)
    state = _load_state(run_dir, runs_root, ref.path, run_id)
    tool_names = _tool_names_by_use_id(events)
    own_ranges = _span_seq_ranges(events)
    snaps = _snapshots_by_lane(events, run_id)
    # The node-time run context is a pure projection of THESE already-loaded events
    # (no new reader/route/event): every node carries its own context, and the
    # top-level `latest_context` is the no-selection (live) view at the greatest seq.
    # Round containment uses each round's FULL subtree seq range (so an open round
    # whose newest events live in a nested span is still recognised); the tree is
    # built once and reused for both the range map and serialization.
    roots = build_tree(events)
    round_ranges = _round_subtree_ranges(roots, own_ranges)
    context_at = _context_deriver(events, round_ranges)
    prompt_diffs = _prompt_diffs(roots)
    run_summary = _summary(ref.slug, run_id, events, state)
    detail = {
        "run": run_summary,
        "phases": _phase_bar(events, state.phase if state is not None else None),
        "tree": [
            _serialize(n, tool_names, own_ranges=own_ranges, snaps=snaps,
                       context_at=context_at, prompt_diffs=prompt_diffs)
            for n in roots
        ],
        "latest_context": context_at(_latest_cutoff(events)),
        "problems": [asdict(p) for p in problems],
        "raw": _raw_view(events, limit, q=raw_q, type_filter=raw_type,
                         from_seq=raw_from_seq, to_seq=raw_to_seq),
    }
    # The change-scope projection is ALWAYS present (unlike the conditional
    # recovery/plan_skeleton): the files each observed lane changed beside the
    # declared contract scope, both derived from the already-loaded events/snaps and
    # the whitelisted `contract.yaml` — no new git path, route, event or state.
    detail["change_scope"] = _change_scope(events, snaps, ref.path, run_dir)
    # The recovery projection is added ONLY when the run needs human intervention
    # (kind determined); otherwise the key is absent (no empty object forced).
    recovery = _recovery(ref, run_id, state, run_summary, events)
    if recovery is not None:
        detail["recovery"] = recovery
    # The plan skeleton is added ONLY when `plan.md` yields a matching section with a
    # task; otherwise the key is absent (no empty list forced) — same additive shape.
    skeleton = _plan_skeleton(run_dir, roots)
    if skeleton:
        detail["plan_skeleton"] = skeleton
    return detail


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


# --- change scope projection (Änderungsumfang eines Laufs) ----------------------
# An additive, purely DERIVED view: the files each observed lane actually changed
# (its first vs last snapshot, via the EXISTING snapshot/diff/numstat logic) placed
# beside the contract's declared `x-adw-*` scope as readable YAML text. It makes NO
# judgement (no in-/out-of-scope marker, E1) and introduces no new git path, route,
# event or state (E5/E6).

# The narrow set of expected operational diff errors a per-lane comparison may raise
# via the existing `_git_diff`: its own HTTPException(404) on a missing snapshot
# object / one-sided failure, plus the SubprocessError (incl. TimeoutExpired) and
# OSError that propagate from `subprocess.run`. A programming error is NOT swallowed.
_CHANGE_SCOPE_DIFF_ERRORS = (HTTPException, subprocess.SubprocessError, OSError)


def _observed_lanes(events, snaps: dict) -> list:
    """The observed lanes in first-observation order (AC-1/S1). A lane is observed
    via a `lane` span with a non-empty `payload.name`, OR via a structurally valid
    snapshot (already filtered into `snaps`). The order key is the smallest seq
    across both sources; each name yields exactly one entry."""
    first_seq: dict = {}

    def observe(name, seq):
        # A lane name must be a non-empty STRING (contract `lane: string`): a numeric
        # or unhashable `payload.name` is ignored, never a non-string lane and never
        # a mixed-type sort key.
        if not isinstance(name, str) or not name or not isinstance(seq, int):
            return
        prev = first_seq.get(name)
        if prev is None or seq < prev:
            first_seq[name] = seq

    for e in events:
        if e.get("type") == "lane" and e.get("kind") == "start":
            observe((e.get("payload") or {}).get("name"), e.get("seq"))
    for lane, pairs in snaps.items():
        for seq, _ref in pairs:
            observe(lane, seq)
    return sorted(first_seq, key=lambda name: (first_seq[name], name))


def _lane_change(repo_path, lane: str, snaps: dict) -> dict:
    """One lane's change entry (AC-1/AC-6/AC-7/AC-8/S2/S3). With >= 2 valid
    snapshots the diff between its lowest- and highest-seq snapshot is produced via
    the existing `_git_diff` (a produced diff with no changes is `files: []`).
    Otherwise — 0/1 snapshot, or any expected diff error despite a pair — the
    canonical unavailable shape `diff_available: false` / `files: null` (never `[]`,
    never omitted, never a false empty diff)."""
    pairs = snaps.get(lane) or []
    if len(pairs) >= 2:
        frm, to = pairs[0][1], pairs[-1][1]
        try:
            files = _git_diff(repo_path, frm, to)["files"]
        except _CHANGE_SCOPE_DIFF_ERRORS:
            return {"lane": lane, "diff_available": False, "files": None}
        return {"lane": lane, "diff_available": True, "files": files}
    return {"lane": lane, "diff_available": False, "files": None}


def _declared_scope(run_dir: Path) -> str | None:
    """The contract's declared scope as readable YAML text, or None (AC-3/AC-4/S4).
    Reads `contract.yaml` ONLY through the existing whitelist/containment path,
    selects every top-level entry whose key is a STRING with the `x-adw-` prefix in
    document order, and re-dumps them (semantic equivalence, not text fidelity). A
    missing/unreadable/non-mapping/unserializable contract, or the absence of a
    matching key, yields None — never an error, never a judgement."""
    path = _resolve_artifact(run_dir, "contract.yaml")
    if path is None:
        return None
    contained = _contained(path, run_dir)
    if contained is None or not contained.is_file():
        return None
    try:
        loaded = yaml.safe_load(contained.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(loaded, dict):
        return None
    # A legally loaded mapping may carry non-string keys (numeric, bool, null, …);
    # they are ignored, never subjected to a prefix operation.
    selected = {
        key: value for key, value in loaded.items()
        if isinstance(key, str) and key.startswith("x-adw-")
    }
    if not selected:
        return None
    try:
        return yaml.safe_dump(selected, sort_keys=False, allow_unicode=True)
    except yaml.YAMLError:
        return None


def _change_scope(events, snaps: dict, repo_path, run_dir: Path) -> dict:
    """The additive `change_scope` object (B4): the per-lane changed files beside
    the declared contract scope. Always present; `lanes` may be `[]` and
    `declared_scope` may be `null`."""
    lanes = [_lane_change(repo_path, lane, snaps) for lane in _observed_lanes(events, snaps)]
    return {"lanes": lanes, "declared_scope": _declared_scope(run_dir)}


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
                events_file = _events_source(run_dir, runs_root)
                if events_file is not None:
                    events = EventReader(events_file).read().events
                else:
                    events = []  # Aufgabe G: a run may predate instrumentation
                state = _load_state(run_dir, runs_root, ref.path, child.name)
                if not events and state is None:
                    continue  # neither a trace nor state → not a listable run
            except OSError:
                continue  # one unreadable run must not drop the rest of the repo
            entries.append(_summary(ref.slug, child.name, events, state))
    # Stable ordering: newest start first, then grouped by status priority —
    # `awaiting_approval` (needs a human) ahead of `running` ahead of the rest.
    # A stable sort keeps the newest-first ordering within each group.
    _status_rank = {"awaiting_approval": 0, "running": 1}
    entries.sort(key=lambda e: e.get("start") or "", reverse=True)
    entries.sort(key=lambda e: _status_rank.get(e.get("status"), 2))
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
        state_file = _contained(run_dir / "state.json", runs_root)
        has_events = _events_source(run_dir, runs_root) is not None
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
        events_file = _events_source(run_dir, runs_root)
        last_id = _parse_last_event_id(request.headers.get("last-event-id"))
        return StreamingResponse(
            _stream(events_file, last_id), media_type="text/event-stream"
        )

    @app.get("/", response_class=HTMLResponse)
    def run_list_page(request: Request):
        lang, t, switch_qs = _lang_context(request)
        html = _TEMPLATES.get_template("run_list.html").render({
            "entries": _list_runs(refs), "t": t, "lang": lang, "switch_qs": switch_qs,
        })
        return _apply_lang_cookie(request, HTMLResponse(html))

    @app.get("/runs/{repo}/{run_id}", response_class=HTMLResponse)
    def run_detail_page(repo: str, run_id: str, request: Request):
        ref, run_dir, runs_root = require_run(repo, run_id)
        lang, t, switch_qs = _lang_context(request)
        # Aufgabe A/C: `?limit` is how much of each long list / the raw log the
        # server materialises (a bounded DOM by default). "Load more" is a plain
        # server-rendered link that raises it — no divergent client-side rendering.
        limit = _parse_limit(request.query_params.get("limit"))
        # Aufgabe A: `?tools_offset` slides a bounded, moving window over the tool
        # entries of the detail panes, so their DOM entry count stays capped
        # (independent of the total) while every entry stays reachable. The trace
        # column has no window any more — `?offset` is accepted and ignored so a
        # bookmarked URL from the paged era still renders the full tree.
        tools_offset = _parse_offset(request.query_params.get("tools_offset"))
        # Raw-tab filters (Aufgabe C): applied server-side over the full payload so
        # a match beyond the rendered preview is still found; empty -> no filter.
        raw_q = request.query_params.get("raw_q") or None
        raw_type = request.query_params.get("raw_type") or None
        # A2/A1: the Raw tab's inclusive seq range. Each bound is tolerant like the
        # other Raw params (a non-numeric bound is inactive, R4); an active range
        # activates the Raw tab on landing (the span-node jump is a plain link).
        raw_from_seq = _parse_seq_bound(request.query_params.get("raw_from_seq"))
        raw_to_seq = _parse_seq_bound(request.query_params.get("raw_to_seq"))
        raw_range_active = raw_from_seq is not None or raw_to_seq is not None
        detail = _run_detail(
            ref, run_id, run_dir, runs_root, limit=limit, raw_q=raw_q, raw_type=raw_type,
            raw_from_seq=raw_from_seq, raw_to_seq=raw_to_seq,
        )
        # Bound the materialised entry nodes by COUNT (Aufgabe A): one global budget
        # per collection, held across nesting levels and across navigation.
        window = _entry_window(limit)
        # P2: `?focus=<seq>` navigates the bounded window to a node that a Timeline
        # bar targets even when it lies outside the current window, so its tree entry
        # AND its pane materialise and it opens selected — never a silent fall back
        # to the wrong (first visible) node.
        focus_seq = None
        try:
            focus_seq = int(request.query_params.get("focus"))
        except (TypeError, ValueError):
            focus_seq = None
        if focus_seq is not None:
            focus_at = _focus_index(detail["tree"], focus_seq)
            if focus_at is not None:
                # A5: a ?focus on an A1-foldable result is redirected to its call
                # (same tool_use_id) so the pair lands together and the result folds;
                # selection then targets the call's own seq (the result carries none).
                flat = _flatten_tree(detail["tree"])
                node = flat[focus_at][0]
                if (node.get("type") == "agent.tool.result" and focus_at > 0
                        and flat[focus_at - 1][0].get("type") == "agent.tool.call"
                        and _tool_use_id(node) is not None
                        and _tool_use_id(flat[focus_at - 1][0]) == _tool_use_id(node)):
                    focus_at -= 1
                    focus_seq = flat[focus_at][0].get("seq")
        tree_window = _tree_rows(detail["tree"])
        tool_window = _tool_window(detail["tree"], tools_offset, window)
        pane_nodes = _pane_nodes(detail["tree"])
        # The trace COLUMN renders the compaction of the COMPLETE tree (A1-A4, A6);
        # the JSON `tree` stays untouched. Paths are made repo-relative here (A4);
        # the default-open phase is decided over the full tree (A5) and handed to
        # the client as a marker.
        compact = _compact_rows(tree_window["rows"])
        _annotate_paths(compact["entries"], ref.path)
        compact["default_phase"] = _default_open_phase(detail["tree"])
        # The Timeline derives from the same events Trace uses; the Artifacts tab
        # lists the whitelisted files of the run. Both are page-render concerns and
        # stay out of the JSON detail contract.
        events, _problems = _read_events(run_dir, runs_root)
        html = _TEMPLATES.get_template("run_detail.html").render({
            "detail": detail, "limit": limit, "focus_seq": focus_seq,
            "raw_q": raw_q or "", "raw_type": raw_type or "",
            "raw_from_seq": raw_from_seq, "raw_to_seq": raw_to_seq,
            "raw_range_active": raw_range_active,
            "tree_window": tree_window, "tool_window": tool_window, "pane_nodes": pane_nodes,
            "compact": compact,
            "timeline": _timeline(events),
            "artifacts": _artifacts_listing(run_dir),
            "t": t, "lang": lang, "switch_qs": switch_qs,
        })
        return _apply_lang_cookie(request, HTMLResponse(html))

    return app


# --- i18n request plumbing (Aufgabe A) -----------------------------------------


def _lang_context(request: Request):
    """The (language code, chrome catalog, switch-link query string) for a request.
    The switch query preserves every current query parameter and only flips
    ``lang`` — so the language link keeps the Tools slice (``tools_offset``) and the
    node selection (``focus``, A5)."""
    lang = i18n.select_language(request)
    params = dict(request.query_params)
    params["lang"] = i18n.other_language(lang)
    return lang, i18n.CATALOG[lang], urlencode(params)


def _apply_lang_cookie(request: Request, response: HTMLResponse) -> HTMLResponse:
    """Set the language cookie ONLY on an explicit, valid ``?lang=`` selection (A3);
    an invalid or absent value sets nothing."""
    explicit = i18n.query_language(request)
    if explicit is not None:
        response.set_cookie(i18n.LANG_COOKIE, explicit)
    return response
