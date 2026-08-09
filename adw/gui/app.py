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
from datetime import datetime
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


def _runs_root(repo_path) -> Path:
    return (Path(repo_path) / RUNS_RELPATH).resolve()


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
    start = next((e for e in events if e.get("type") == "run" and e.get("kind") == "start"), None)
    end = next((e for e in events if e.get("type") == "run" and e.get("kind") == "end"), None)
    return start, end


def _summary(slug, run_id, events, state) -> dict:
    start_rec, end_rec = _run_span(events)
    start_payload = (start_rec or {}).get("payload") or {}
    end_payload = (end_rec or {}).get("payload") or {}
    totals = end_payload.get("totals") or {}
    issue = start_payload.get("issue")
    if isinstance(issue, str) and len(issue) > _ISSUE_MAX:
        issue = issue[:_ISSUE_MAX] + "…"
    return {
        "run_id": run_id,
        "repo": slug,
        "repo_exists": True,
        "issue": issue,
        "phase": state.phase if state is not None else None,
        "status": end_payload.get("status") if end_rec is not None else "running",
        "start": (start_rec or {}).get("ts"),
        "duration": totals.get("duration"),
        "cost": totals.get("cost"),
        "event_count": len(events),
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


def _node_label(node) -> str:
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


def _node_status(node):
    if not _is_span(node):
        return None
    if node.running:
        return "running"
    if node.type == "gate":
        return "passed" if (node.end_payload or {}).get("passed") else "failed"
    return "done"


def _serialize(node) -> dict:
    if _is_span(node):
        payload = node.start_payload if node.start_payload is not None else node.end_payload
        d = {
            "type": node.type,
            "label": _node_label(node),
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
            "children": [_serialize(c) for c in node.children],
        }
        if node.type == "round" and isinstance(node.start_payload, dict):
            d["n"] = node.start_payload.get("n")
            d["cap"] = node.start_payload.get("cap")
        return d
    return {
        "type": node.type,
        "label": _node_label(node),
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
    return {
        "run": _summary(ref.slug, run_id, events, state),
        "phases": _phase_bar(events, state.phase if state is not None else None),
        "tree": [_serialize(n) for n in build_tree(events)],
        "problems": [asdict(p) for p in problems],
    }


def _list_runs(refs: dict[str, RepoRef]) -> list[dict]:
    entries: list[dict] = []
    for ref in refs.values():
        runs_dir = Path(ref.path) / RUNS_RELPATH if ref.path else None
        if not ref.exists or runs_dir is None or not runs_dir.is_dir():
            entries.append(
                {"repo": ref.slug, "repo_exists": False, "hint": ref.path or "(unknown path)"}
            )
            continue
        runs_root = _runs_root(ref.path)
        for child in sorted(runs_dir.iterdir()):
            if not RUN_ID_RE.fullmatch(child.name):
                continue
            run_dir = _contained(child, runs_root)  # skip symlinks escaping the tree
            if run_dir is None or not run_dir.is_dir():
                continue
            events_file = _contained(run_dir / "events.jsonl", runs_root)
            if events_file is None or not events_file.is_file():
                continue
            events = EventReader(events_file).read().events
            state = _load_state(run_dir, runs_root, ref.path, child.name)
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
