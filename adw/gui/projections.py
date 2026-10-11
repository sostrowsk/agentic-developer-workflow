"""Derived projections of a run: artifacts listing, plan skeleton, recovery card and
the assembled run detail."""

import shlex
from dataclasses import asdict
from pathlib import Path

from adw.gui.artifacts import (
    _resolve_artifact,
)
from adw.gui.diffs import (
    _change_scope,
)
from adw.gui.fmt import (
    _mapping_payload,
)
from adw.gui.model import build_tree
from adw.gui.runs import (
    PHASES,
    RepoRef,
    _load_events,
    _load_state,
    _phase_bar,
    _summary,
)
from adw.gui.serialize import (
    _prompt_diffs,
    _raw_view,
    _serialize,
    _snapshots_by_lane,
    _span_seq_ranges,
)
from adw.gui.timeline import (
    _context_deriver,
    _latest_cutoff,
    _round_subtree_ranges,
)
from adw.gui.trace import (
    _DISPLAY_WINDOW,
    _aggregate_outcome,
    _tool_names_by_use_id,
)
from adw.state import contained as _contained

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
    loaded=None,
) -> dict:
    """The detail projection. ``loaded`` = a ``_load_events`` result the caller
    already holds (the HTML page reuses it for the timeline: one parse per request)."""
    events, problems, has_trace = loaded or _load_events(run_dir, runs_root)
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
    phases = _phase_bar(events, state.phase if state is not None else None)
    run_summary = _summary(ref.slug, run_id, events, state, has_trace=has_trace, phases=phases)
    detail = {
        "run": run_summary,
        "phases": phases,
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
