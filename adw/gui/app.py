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
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from adw.gui.model import build_tree
from adw.gui.reader import EventReader
from adw.gui.registry import _slug, _unique_slug, load_registry
from adw.state import RUN_ID_RE, RUNS_RELPATH, RunState, StateNotFoundError

_HERE = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))


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
    first's. ``end`` is None when the last span has no ``end`` yet (→ running),
    even if earlier spans finished."""
    start = None
    end = None
    for e in events:
        if e.get("type") != "run":
            continue
        if e.get("kind") == "start":
            if start is None:
                start = e
            end = None  # a new span opened; its end (if any) comes later in the log
        elif e.get("kind") == "end":
            end = e
    return start, end


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
    if is_error is None and exit_code is None:
        return "agent.tool.result"
    outcome = "error" if is_error else "ok"
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


def _serialize(node, tool_names=None) -> dict:
    tool_names = tool_names or {}
    if _is_span(node):
        payload = node.start_payload if node.start_payload is not None else node.end_payload
        d = {
            "type": node.type,
            "label": _node_label(node, tool_names),
            "duration": node.duration,
            "status": _node_status(node),
            "seq": node.seq,
            "span_id": node.span_id,
            "running": node.running,
            "start_ts": node.start_ts,
            "end_ts": node.end_ts,
            "payload": payload,
            "start_payload": node.start_payload,
            "end_payload": node.end_payload,
            "children": [_serialize(c, tool_names) for c in node.children],
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
        "children": [],
    }


def _run_detail(ref: RepoRef, run_id: str, run_dir: Path, runs_root: Path) -> dict:
    events, problems = _read_events(run_dir, runs_root)
    state = _load_state(run_dir, runs_root, ref.path, run_id)
    tool_names = _tool_names_by_use_id(events)
    return {
        "run": _summary(ref.slug, run_id, events, state),
        "phases": _phase_bar(events, state.phase if state is not None else None),
        "tree": [_serialize(n, tool_names) for n in build_tree(events)],
        "problems": [asdict(p) for p in problems],
    }


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
    def api_run_events(repo: str, run_id: str, from_seq: int | None = None):
        _, run_dir, runs_root = require_run(repo, run_id)
        events, _problems = _read_events(run_dir, runs_root)
        if from_seq is not None:
            events = [e for e in events if isinstance(e.get("seq"), int) and e["seq"] >= from_seq]
        return events

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
    def run_detail_page(repo: str, run_id: str):
        ref, run_dir, runs_root = require_run(repo, run_id)
        detail = _run_detail(ref, run_id, run_dir, runs_root)
        html = _TEMPLATES.get_template("run_detail.html").render({"detail": detail})
        return HTMLResponse(html)

    return app
