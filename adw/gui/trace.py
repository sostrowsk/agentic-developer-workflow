"""Trace tree for the run detail: windowing, page-local compaction (folds, repeats,
groups), node labels/status and repo-relative display paths."""

import os
from datetime import datetime

from adw.gui.fmt import (
    _as_mapping,
    _is_number,
    _mapping_payload,
)

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
# Countable but never groupable: a write is a structural boundary (it ENDS a
# read/search group, A3) yet repeating the same write is pure noise, so it collapses
# into a repeat of its own.
_WRITE_TOOLS = ("Write", "Edit")
# Calls that have a repeat target: the tool's priority main argument
# (_TOOL_ARG_PRIORITY) — file_path, or pattern for Grep/Glob.
_TARGET_TOOLS = ("Read", *_WRITE_TOOLS, "Grep", "Glob")


def _tool_use_id(node):
    p = node.get("payload")
    return p.get("tool_use_id") if isinstance(p, dict) else None


def _tool_name(node):
    p = node.get("payload")
    return p.get("tool") if isinstance(p, dict) else None


def _tool_target(node):
    """The raw comparison target of a foldable call (exact string, no normalisation):
    Read/Write/Edit → ``input.file_path``, Grep/Glob → ``input.pattern``. None (never
    compared) for anything else or when the target is absent (no target → no
    summary)."""
    p = node.get("payload")
    inp = p.get("input") if isinstance(p, dict) else None
    if not isinstance(inp, dict):
        return None
    tool = p.get("tool")
    if tool not in _TARGET_TOOLS:
        return None
    value = inp.get(_TOOL_ARG_PRIORITY[tool][0])
    return value if isinstance(value, str) and value else None


def _result_failed(payload) -> bool | None:
    """THE outcome rule of a tool result: ``is_error`` wins, else a nonzero
    ``exit_code`` is a failure and zero a success; anything else (``content`` is a
    body, not an outcome) is undetermined — None, never presented as success."""
    if not isinstance(payload, dict):
        return None
    is_error, exit_code = payload.get("is_error"), payload.get("exit_code")
    if isinstance(is_error, bool):
        return is_error
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return exit_code != 0
    return None


def _result_outcome(result_node):
    """The determinable outcome of a folded result: ``"ok"``/``"error"`` — or None
    when undetermined (spec A1). A result whose label stayed the bare type name has
    no resolvable tool identity and stays undetermined, like its label."""
    label = result_node.get("label")
    if not isinstance(label, str) or label == "agent.tool.result":
        return None
    failed = _result_failed(_mapping_payload(result_node))
    if failed is None:
        return None
    return "error" if failed else "ok"


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


def _repeat_entry(items, depth, unit="call"):
    durs = [x["duration"] for x in items if x.get("duration") is not None]
    return {"kind": "repeat", "depth": depth, "count": len(items), "unit": unit,
            "duration": (sum(durs) if durs else None),
            "children": [_node_entry(x) for x in items]}


def _repeat_key(node):
    """What makes two NEIGHBOURS the same repetition, or None when the node does not
    repeat at all. A tool call repeats per (tool, target) — the same file read or
    edited again. An ``artifact`` repeats per TYPE: consecutive artifacts are each a
    different file, so target identity would never collapse them, while the rows
    themselves carry no distinguishing text — the run of them is the noise."""
    if node.get("type") == "artifact":
        return ("artifact",)
    if node.get("type") != "agent.tool.call":
        return None
    target = _tool_target(node)
    return ("tool", _tool_name(node), target) if target is not None else None


def _repeat_unit(node) -> str:
    return "artifact" if node.get("type") == "artifact" else "call"


def _repeat_children(run):
    """Collapse maximal runs of neighbours sharing a repeat key (>= 2) into a repeat;
    everything else stays a single node entry, in original order."""
    children = []
    j = 0
    while j < len(run):
        key = _repeat_key(run[j]["node"])
        k = j + 1
        if key is not None:
            while k < len(run) and _repeat_key(run[k]["node"]) == key:
                k += 1
        if k - j >= 2:
            children.append(
                _repeat_entry(run[j:k], run[j]["depth"], _repeat_unit(run[j]["node"])))
        else:
            children.append(_node_entry(run[j]))
            k = j + 1
        j = k
    return children


def _is_countable(item):
    """Whether the item may be COUNTED into a repeat although it is not groupable:
    a write to a known file, or an artifact. Both keep breaking a read/search group
    (A3 unchanged); a determinate error is never taken into a collector."""
    node = item["node"]
    if item.get("outcome") == "error":
        return False
    if node.get("type") == "artifact":
        return True
    return (node.get("type") == "agent.tool.call"
            and _tool_name(node) in _WRITE_TOOLS
            and _tool_target(node) is not None)


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
    * A2 — >= 2 neighbours sharing a repeat key collapse into a repeat: the same
      tool on the same target (reads/searches AND writes), or adjacent artifacts.
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
        elif _is_countable(items[i]):
            # Countable but NOT groupable: collapse the repetition, wrap nothing.
            depth, key = items[i]["depth"], _repeat_key(items[i]["node"])
            run = []
            while (i < len(items) and _is_countable(items[i])
                   and items[i]["depth"] == depth and _repeat_key(items[i]["node"]) == key):
                run.append(items[i])
                i += 1
            entries.extend(_repeat_children(run))
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
        return _result_failed(_mapping_payload(node)) is True
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
    if node.get("type") == "artifact":
        # A4 for an artifact: the visible text is its short name, the complete path
        # stays reachable in the title — the same deal a path argument gets. The
        # payload is guarded like everywhere else: a corrupt record may carry a
        # truthy NON-mapping value, and that must not 500 the whole page.
        p = node.get("payload")
        path = p.get("path") if isinstance(p, dict) else None
        return node.get("label"), (path if isinstance(path, str) and path else None)
    if node.get("type") != "agent.tool.call":
        return node.get("label"), None
    p = _mapping_payload(node)
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
    """The main argument for a label, truncated to ``_TOOL_ARG_MAX``."""
    value, _key = _raw_main_arg(tool, inp)
    if value is None:
        return None
    return value if len(value) <= _TOOL_ARG_MAX else value[:_TOOL_ARG_MAX] + "…"


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
    failed = _result_failed(p)
    if failed is None:
        return "agent.tool.result"  # no valid outcome signal → keep the type name
    outcome = "error" if failed else "ok"
    exit_code = p.get("exit_code")
    if exit_code is not None:
        return f"{outcome} (exit {exit_code})"
    return outcome


def _artifact_label(p) -> str:
    """``artifact <name>`` — the same shape a tool call has (``Read <path>``). Every
    artifact row used to read just ``artifact``, which made a run of them (and the
    counted collector over it) tell the reader nothing. Without a usable name the
    bare type name stays: invent nothing."""
    name = p.get("name") if isinstance(p, dict) else None
    return f"artifact {name}" if isinstance(name, str) and name else "artifact"


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
    if node.type == "artifact":
        return _artifact_label(node.payload)
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
        return "passed" if _as_mapping(node.end_payload).get("passed") else "failed"
    return "done"


def _subtree_cost(node):
    """Sum of ``cost_usd`` over every ``agent.run`` in the subtree, or None when no
    descendant carries usage (AC 11: an absent cost is null, not zero)."""
    total = None
    if _is_span(node):
        if node.type == "agent.run":
            ep = node.end_payload
            cost = ep.get("cost_usd") if isinstance(ep, dict) else None
            if _is_number(cost):
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
    ep = _as_mapping(node.end_payload)
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
            p = _mapping_payload(e)
            uid, tool = p.get("tool_use_id"), p.get("tool")
            if uid and tool:
                names[uid] = str(tool)
    return names


# The event types whose spans are pure WAITING (time passes without work): CI
# polling and gate runtime (A3).
_WAITING_TYPES = {"ci.wait", "gate"}
