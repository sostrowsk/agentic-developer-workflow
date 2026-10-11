"""Read-only FastAPI GUI over the ADW event log (GUI-SPEC §7, §8).

This module holds the routes; the projections it serves live in the sibling
modules (trace, fmt, runs, serialize, timeline, artifacts, diffs, projections).
The web stack (FastAPI, Jinja2) is imported only by this package, so the core
``adw run`` path stays free of it — ``adw gui`` imports this module lazily, and
the tests build the app via :func:`create_app`. The GUI is strictly read-only: it reads
only below the resolved ``.adw/runs/<run_id>/`` directory (plus the registry file
and its own packaged templates/static assets), runs no external program except
read-only ``git diff`` for the change-scope view, and exposes no write route.
The read data layer (``reader``, ``model``, ``registry``) is only consumed by the
GUI, never modified.
"""

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from adw.gui import i18n
from adw.gui.artifacts import (
    _artifacts_listing,
    _resolve_artifact,
)
from adw.gui.diffs import (
    _change_scope_summary,
    _git_diff,
    _is_snapshot_ref,
    _observed_lanes,
    _snapshot_refs,
)
from adw.gui.fmt import (
    _compact_context,
    _fmt_cost,
    _fmt_duration,
    _fmt_ts,
    _js_number,
    _markdown_or_text,
    _payload_blocks,
    _pretty_payload,
    _render_markdown,
)
from adw.gui.projections import (
    _run_detail,
)
from adw.gui.reader import EventReader
from adw.gui.runs import (
    _LIST_SORT_KEYS,
    _POLL_SECONDS,
    RepoRef,
    _apply_list_controls,
    _events_source,
    _list_runs,
    _load_events,
    _phase_timeline,
    _read_events,
    _resolve_repos,
)
from adw.gui.serialize import _serialize, _snapshots_by_lane
from adw.gui.timeline import (
    _timeline,
)
from adw.gui.trace import (
    _annotate_paths,
    _compact_rows,
    _default_open_phase,
    _display_label,
    _entry_window,
    _flatten_tree,
    _focus_index,
    _node_label,
    _pane_nodes,
    _parse_limit,
    _parse_offset,
    _parse_seq_bound,
    _repo_relative,
    _tool_names_by_use_id,
    _tool_use_id,
    _tool_window,
    _tree_rows,
)
from adw.state import RUN_ID_RE
from adw.state import contained as _contained
from adw.state import runs_root as _runs_root

# The route module re-exports the helpers the GUI tests address directly; the
# implementations live in the sibling modules (trace, fmt, runs, serialize,
# timeline, artifacts, diffs, projections).
__all__ = [
    "_compact_rows",
    "_default_open_phase",
    "_display_label",
    "_flatten_tree",
    "_fmt_cost",
    "_js_number",
    "_node_label",
    "_observed_lanes",
    "_payload_blocks",
    "_pretty_payload",
    "_render_markdown",
    "_repo_relative",
    "_serialize",
    "_snapshot_refs",
    "_snapshots_by_lane",
    "_timeline",
    "_tool_names_by_use_id",
    "_tree_rows",
    "create_app",
]

_HERE = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))


_TEMPLATES.env.filters["fmt_duration"] = _fmt_duration
_TEMPLATES.env.filters["fmt_cost"] = _fmt_cost
_TEMPLATES.env.filters["fmt_ts"] = _fmt_ts


_TEMPLATES.env.filters["compact_context"] = _compact_context
_TEMPLATES.env.filters["pretty_payload"] = _pretty_payload
_TEMPLATES.env.filters["payload_blocks"] = _payload_blocks
_TEMPLATES.env.filters["markdown_or_text"] = _markdown_or_text


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
        if os.fspath(events_path).endswith(".gz"):
            # A gzipped log is a pruned, finished run: nothing more will arrive,
            # and every read would decompress the whole archive again.
            return
        time.sleep(_POLL_SECONDS)


class _RevalidatingStatic(StaticFiles):
    """``/static`` with ``Cache-Control: no-cache``.

    ``StaticFiles`` sends ``etag`` and ``last-modified`` but no ``Cache-Control``, and
    without one a browser applies HEURISTIC freshness: it reuses its cached copy
    without asking the server. For a local inspection tool whose assets change
    whenever the orchestrator is worked on, that is the wrong default — a stale
    stylesheet survives a server restart and even a hard reload in another tab.

    ``no-cache`` does not forbid caching; it forbids using a cached copy WITHOUT
    revalidating. The ETag is already there, so revalidation costs one 304.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["cache-control"] = "no-cache"
        return response


# --- app factory ----------------------------------------------------------------


def create_app(repos=None) -> FastAPI:
    app = FastAPI(title="ADW Read-only GUI")
    static_dir = _HERE / "static"
    if static_dir.is_dir():
        app.mount("/static", _RevalidatingStatic(directory=str(static_dir)), name="static")
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
        # A5: the list takes four OPTIONAL query params (sort, dir, repo, status),
        # evaluated server-side like the Raw tab — no client state, no persistence.
        entries = _list_runs(refs, for_html=True)
        # Filter dropdowns are populated from the UNFILTERED set so every choice
        # stays reachable regardless of the current filter.
        repo_options = sorted({e["repo"] for e in entries if e.get("repo_exists")})
        status_options = sorted({e["status"] for e in entries
                                 if e.get("repo_exists") and e.get("status")})
        sort = request.query_params.get("sort")
        direction = request.query_params.get("dir")
        repo_f = request.query_params.get("repo") or None
        status_f = request.query_params.get("status") or None
        shown, filter_active = _apply_list_controls(entries, sort, direction, repo_f, status_f)
        html = _TEMPLATES.get_template("run_list.html").render({
            "entries": shown, "t": t, "lang": lang, "switch_qs": switch_qs,
            "repo_options": repo_options, "status_options": status_options,
            "cur_sort": sort if sort in _LIST_SORT_KEYS else "start",
            "cur_dir": direction if direction in ("asc", "desc") else "desc",
            "cur_repo": repo_f or "", "cur_status": status_f or "",
            "filter_active": filter_active,
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
        loaded = _load_events(run_dir, runs_root)
        detail = _run_detail(
            ref, run_id, run_dir, runs_root, limit=limit, raw_q=raw_q, raw_type=raw_type,
            raw_from_seq=raw_from_seq, raw_to_seq=raw_to_seq, loaded=loaded,
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
        events, _problems, has_trace = loaded
        # A1: the client announces a live-refresh / pane-load with ``X-Requested-With:
        # fetch``; only then does the route emit the two live regions as a bare fragment
        # (same render path, same markup, no document wrapper). Without the header the
        # response is the unchanged full document (E3). The header alone decides.
        fragment = request.headers.get("x-requested-with", "").lower() == "fetch"
        html = _TEMPLATES.get_template("run_detail.html").render({
            "fragment": fragment,
            "detail": detail, "limit": limit, "focus_seq": focus_seq,
            "phase_timeline": _phase_timeline(detail["phases"], time.time()),
            "raw_q": raw_q or "", "raw_type": raw_type or "",
            "raw_from_seq": raw_from_seq, "raw_to_seq": raw_to_seq,
            "raw_range_active": raw_range_active,
            "tree_window": tree_window, "tool_window": tool_window, "pane_nodes": pane_nodes,
            "compact": compact,
            # A2: the collapsed change-scope summary is a pure RENDER-context value
            # (never written into `detail` / the JSON API, AC 12); the planned-tasks
            # summary is rendered inline from the existing `detail.plan_skeleton`.
            "change_summary": _change_scope_summary(detail["change_scope"]),
            "timeline": _timeline(events, has_trace=has_trace),
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
