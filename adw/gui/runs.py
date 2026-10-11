"""Run metadata: repo resolution, events/state loading, run summary, phase bar and
timeline sizes, issue titles and the run list."""

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from adw.gui.fmt import (
    _as_mapping,
    _is_number,
    _mapping_payload,
)
from adw.gui.reader import EventReader
from adw.gui.registry import _slug, _unique_slug, load_registry
from adw.state import RUN_ID_RE, RUNS_RELPATH, RunState, StateNotFoundError
from adw.state import contained as _contained
from adw.state import runs_root as _runs_root

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
    events, problems, _has_trace = _load_events(run_dir, runs_root)
    return events, problems


def _load_events(run_dir: Path, runs_root: Path):
    """(events, problems, has_trace) from ONE lookup and ONE parse of the log."""
    events_file = _events_source(run_dir, runs_root)
    if events_file is None:
        return [], [], False
    result = EventReader(events_file).read()
    return result.events, result.problems, True


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
            ev = _mapping_payload(e).get("event")
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
        payload = _mapping_payload(e)
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


_RUN_TOTAL_KEYS = ("duration", "cost", "tokens")


def _run_totals(events) -> dict:
    """Sum ``totals.duration``/``cost``/``tokens`` over ALL closed ``run`` spans
    (A1). A gated run is several CLI commands and thus several ``run`` spans in one
    log; the whole-run figure is the SUM, not the last span's. Each ``run`` end
    record marks one closed span; an open (unfinished) span has no end and no
    ``totals``, so it contributes nothing. A metric stays ``None`` when no closed
    span carries a numeric value — never a fabricated ``0`` (E6) — while a genuine
    ``0`` from the payload is kept."""
    sums = {key: None for key in _RUN_TOTAL_KEYS}
    for e in events:
        if e.get("type") == "run" and e.get("kind") == "end":
            totals = _as_mapping(_mapping_payload(e).get("totals"))
            for key in _RUN_TOTAL_KEYS:
                v = totals.get(key)
                if _is_number(v):
                    sums[key] = v if sums[key] is None else sums[key] + v
    return sums


def _phase_time_sizes(phases, now_epoch):
    """The three named time sizes (A2) from the NORMATIVE definitions — independent
    of the timeline's drawing eligibility and rounded geometry, so genuine zeros and
    fractional seconds survive:

    * ``phase_seconds`` — sum of the phase spans with BOTH a parsable ``start`` and
      ``end`` (an open phase never contributes, so an active run's current phase is
      excluded); fractional seconds preserved, a real ``0`` kept. ``None`` only when
      no phase ever closed.
    * ``total_seconds`` — smallest phase start to largest phase end; an active open
      phase with no ``end`` reaches ``now_epoch`` (a calculation instant, never
      written back as a phase end). ``None`` without a determinable end.
    * ``wait_seconds`` — ``total_seconds − phase_seconds``; ``None`` when either
      operand is undetermined. The phase spans do not overlap, so the split is
      unambiguous.
    """
    starts, ends = [], []
    phase_sum = 0.0
    closed = False
    for ph in phases:
        s = _ts_epoch(ph.get("start"))
        if s is None:
            continue
        starts.append(s)
        e = _ts_epoch(ph.get("end"))
        if e is not None:
            ends.append(e)
            if e >= s:
                phase_sum += e - s
                closed = True
        elif ph.get("status") == "active":
            # An open ACTIVE phase bounds TOTAL only (to now); it is not work time and
            # never enters phase_seconds — that is the fix for the presentation-derived
            # value which counted it.
            ends.append(now_epoch)
    if not starts:
        return None, None, None
    phase_seconds = phase_sum if closed else None
    if not ends:
        return phase_seconds, None, None
    total_seconds = max(ends) - min(starts)
    wait_seconds = (total_seconds - phase_seconds) if phase_seconds is not None else None
    return phase_seconds, wait_seconds, total_seconds


def _summary(slug, run_id, events, state, has_trace=None, now_epoch=None, phases=None) -> dict:
    start_rec, end_rec = _run_span(events)
    start_payload = _mapping_payload(start_rec or {})
    end_payload = _mapping_payload(end_rec or {})
    # Whole-run figures (A1): summed over every closed span, not the last one.
    totals = _run_totals(events)
    # The named time sizes (A2): computed from the normative definitions, NOT copied
    # from the presentation-only phase timeline (which includes open phases, rounds
    # seconds and drops a zero-length timeline).
    if phases is None:
        phases = _phase_bar(events, state.phase if state is not None else None)
    phase_seconds, wait_seconds, total_seconds = _phase_time_sizes(
        phases, time.time() if now_epoch is None else now_epoch)
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
        # BEDEUTUNGSÄNDERUNG (A1): duration/cost now sum the WHOLE run; work_seconds
        # is numerically identical to duration and named as the real work (E5),
        # distinct from phase_seconds. tokens is the summed scalar token total.
        "duration": totals["duration"],
        "cost": totals["cost"],
        "work_seconds": totals["duration"],
        "phase_seconds": phase_seconds,
        "wait_seconds": wait_seconds,
        "total_seconds": total_seconds,
        "tokens": totals["tokens"],
        "event_count": len(events),
        # Aufgabe G: a clear indication whether a trace EXISTS for this run — the
        # presence of the event log, NOT whether the reader accepted a record. An
        # existing but empty or entirely malformed log is a trace WITH reader
        # problems, and reporting it as "no trace" would hide that distinction
        # (follow-up run b6739174). `None` keeps the old event-derived answer for
        # callers that cannot see the file.
        "has_trace": bool(events) if has_trace is None else bool(has_trace),
    }


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
        # `start`/`end` are ADDITIVE (A6): the ISO-8601 timestamps already derived
        # above, taken over verbatim from the event log, or None. `duration` is NOT
        # re-derived from them, and an open phase's end stays None — the page-build
        # instant is a display rule of the timeline, never an API phase end.
        # A corrupt log can carry a non-string or unparsable `ts`; the contract
        # requires a parsable ISO-8601 string or None, so validate before exporting
        # (a valid string is kept verbatim). Status/duration derivation is untouched.
        raw_start = info.get("start") if info else None
        raw_end = info.get("end") if info else None
        start = raw_start if _ts_epoch(raw_start) is not None else None
        end = raw_end if _ts_epoch(raw_end) is not None else None
        bar.append({"name": name, "status": status, "duration": duration,
                    "start": start, "end": end})
    return bar


def _phase_timeline(phases, now_epoch):
    """Presentation-only geometry for the to-scale phase band (A5), derived at page
    build from the phases' start/end timestamps. NOT part of the JSON contract.

    Returns ``{"scaled": False}`` when the rail cannot be drawn — no phase has a
    parsable start, ``T`` is not determinable, or ``T <= 0`` — so the header falls
    back to the chip row. Otherwise it returns the per-phase segments (offset/width
    as percentages of ``T``), the waiting segments (positive gaps between an end and
    the next start), the full-phase legend (a phase that never ran stays visible,
    dampened, without a segment) and the three numbers work/waiting/total. The 2 px
    minimum width is a CSS-only floor; it never enters these seconds."""
    parsed = []
    for ph in phases:
        s = _ts_epoch(ph.get("start"))
        if s is None:
            continue
        e = _ts_epoch(ph.get("end"))
        active = ph.get("status") == "active"
        eff_end = e if e is not None else (now_epoch if active else None)
        parsed.append({"name": ph["name"], "status": ph["status"],
                       "duration": ph.get("duration"), "s": s, "e": eff_end,
                       "open": active and e is None})
    if len(parsed) < 1:
        return {"scaled": False}
    t_start = min(p["s"] for p in parsed)
    ends = [p["e"] for p in parsed if p["e"] is not None]
    if not ends:
        return {"scaled": False}
    t_total = max(ends) - t_start
    if t_total <= 0:
        return {"scaled": False}

    segments, work = [], 0.0
    for p in parsed:
        if p["e"] is None:  # a start with no usable end and not active → no segment
            continue
        work += p["e"] - p["s"]
        segments.append({
            "name": p["name"], "status": p["status"], "duration": p["duration"],
            "open": p["open"],
            "offset": round((p["s"] - t_start) / t_total * 100.0, 4),
            "width": round((p["e"] - p["s"]) / t_total * 100.0, 4),
        })

    ordered = sorted((p for p in parsed if p["e"] is not None), key=lambda p: p["s"])
    waits, wait_total = [], 0.0
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        gap = nxt["s"] - prev["e"]
        if gap > 0:
            wait_total += gap
            waits.append({
                "offset": round((prev["e"] - t_start) / t_total * 100.0, 4),
                "width": round(gap / t_total * 100.0, 4),
            })

    legend = [{"name": ph["name"], "status": ph["status"], "duration": ph.get("duration"),
               "dead": _ts_epoch(ph.get("start")) is None} for ph in phases]
    return {
        "scaled": True,
        "segments": segments,
        "waits": waits,
        "legend": legend,
        "work_seconds": int(round(work)),
        "wait_seconds": int(round(wait_total)),
        "total_seconds": int(round(t_total)),
    }


_TITLE_MAX = 90


def _raw_issue(events, state):
    """The FULL, untruncated raw issue text of a run: the ``run`` start payload's
    ``issue`` (or the state's ``issue`` for a run without a trace), or None. Unlike
    the summary's ``issue`` field (truncated to ``_ISSUE_MAX``) this is the complete
    text — the source of the list's ``title`` attribute (A3)."""
    raw = _mapping_payload(_run_span(events)[0] or {}).get("issue")
    if raw is None and state is not None:
        raw = state.issue
    return raw


def _issue_title(raw) -> str:
    """The one-line display title derived from the raw issue text (A3). Pure text
    processing, never markdown/HTML rendering (E7):

    1. the first ``#`` heading among the first twelve lines (its text without the
       ``#``); a heading that only names "Issue" (``^issue\\b``, case-insensitive)
       is skipped so the next heading wins; a heading past line twelve does not win;
    2. otherwise the first non-empty line;
    3. a leading ``ADW-Issue:`` / ``Issue:`` is removed;
    4. longer than 90 chars → cut to 89 + ``…``;
    5. empty / missing → an empty string (the caller renders an empty cell).
    """
    if not isinstance(raw, str):
        return ""
    lines = raw.splitlines()
    title = None
    for line in lines[:12]:
        stripped = line.strip()
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if re.match(r"(?i)^issue\b", text):
                continue  # an "Issue"-only heading names nothing — try the next one
            if text:
                title = text
                break
    if title is None:
        for line in lines:
            if line.strip():
                title = line.strip()
                break
    if title is None:
        return ""
    title = re.sub(r"(?i)^(adw-issue|issue)\s*:\s*", "", title).strip()
    if len(title) > _TITLE_MAX:
        title = title[:89] + "…"
    return title


_LIST_SORT_KEYS = {"start": "start", "duration": "duration",
                   "cost": "cost", "events": "event_count"}
_LIST_STATUS_RANK = {"awaiting_approval": 0, "running": 1}


def _apply_list_controls(entries, sort, direction, repo_f, status_f):
    """Server-side filtering and sorting of the run list (A5). Filters intersect; an
    unknown value simply matches nothing (an empty result — the caller shows a
    hint). Unknown/absent ``sort``/``dir`` fall back to ``start``/``desc`` — never an
    error, never a spuriously empty list. Missing metrics (null) sort to the end
    without raising. The existing status grouping (``awaiting_approval`` first, then
    ``running``, then the rest; newest first within a group) is applied AFTER the
    chosen sort and keeps priority. Returns ``(ordered_entries, filter_active)``."""
    filter_active = repo_f is not None or status_f is not None
    result = list(entries)
    if repo_f is not None:
        result = [e for e in result if e.get("repo") == repo_f]
    if status_f is not None:
        result = [e for e in result if e.get("status") == status_f]
    # sort and dir default INDEPENDENTLY (contract): a bad/absent sort falls back to
    # `start` without forcing `dir` back to `desc`, so `?dir=asc` alone still sorts
    # ascending and matches the direction the control displays.
    sort = sort if sort in _LIST_SORT_KEYS else "start"
    direction = direction if direction in ("asc", "desc") else "desc"
    key = _LIST_SORT_KEYS[sort]
    reverse = direction == "desc"
    present = [e for e in result if e.get(key) is not None]
    missing = [e for e in result if e.get(key) is None]
    present.sort(key=lambda e: e.get(key), reverse=reverse)
    ordered = present + missing  # null metrics rank last, in either direction
    ordered.sort(key=lambda e: _LIST_STATUS_RANK.get(e.get("status"), 2))
    return ordered, filter_active


def _list_runs(refs: dict[str, RepoRef], for_html: bool = False) -> list[dict]:
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
                if events_file is None and state is None:
                    continue  # neither a trace NOR state → not a listable run
                # Listability follows the log's EXISTENCE, not the parsed count: a
                # crash can leave a freshly created, still-empty events.jsonl and no
                # readable state, and that run must not vanish from the listing.
            except OSError:
                continue  # one unreadable run must not drop the rest of the repo
            summary = _summary(ref.slug, child.name, events, state,
                               has_trace=events_file is not None)
            if for_html:
                # Display-only enrichment for the list template (A3): the derived
                # one-line title and the FULL raw text for the `title` attribute.
                # These are NOT API fields — the JSON list stays unchanged.
                raw = _raw_issue(events, state)
                summary["issue_title"] = _issue_title(raw)
                summary["issue_full"] = raw
            entries.append(summary)
    # Stable ordering: newest start first, then grouped by status priority —
    # `awaiting_approval` (needs a human) ahead of `running` ahead of the rest.
    # A stable sort keeps the newest-first ordering within each group.
    _status_rank = {"awaiting_approval": 0, "running": 1}
    entries.sort(key=lambda e: e.get("start") or "", reverse=True)
    entries.sort(key=lambda e: _status_rank.get(e.get("status"), 2))
    return entries
