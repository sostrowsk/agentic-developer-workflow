# Run Inspector 0.9 → 0.16 — Release Notes

**English** | [Deutsch](RELEASE-NOTES.de.md)

Eight releases that turn a run's event log into something you can operate:
visible waiting states, time travel through the run state, prompt diffs,
recovery commands, a plan skeleton, the change footprint — and breakpoints in
the orchestrator itself.

The full, machine-level history lives in [`../CHANGELOG.md`](../CHANGELOG.md);
this document frames the eight releases and gives the reasoning behind the
decisions that are not obvious.

| | |
| --- | --- |
| Window | 20–27 August 2026 |
| Releases | 8 (0.9.0 – 0.16.0) |
| Tests | 892 → 1062 (+170) |
| Lines of code | +6111 across `adw/` and `tests/` |

## Overview

| Version | Date | Change | New tests | Suite | CI |
| --- | --- | --- | ---: | ---: | --- |
| 0.16.0 | 27 Aug | Configurable breakpoints | 27 | 1062 | green |
| 0.15.0 | 26 Aug | Change footprint per lane | 19 | 1027 | green |
| 0.14.0 | 26 Aug | Plan skeleton in the trace | 21 | 1004 | green |
| 0.13.0 | 26 Aug | Recovery card | 20 | 978 | stuck (GitHub incident) |
| 0.12.0 | 26 Aug | Raw jump + prompt diff | 17 | 953 | green |
| 0.11.0 | 26 Aug | Context panel with time travel | 21 | 936 | green |
| 0.10.0 | 26 Aug | Dry-run marking, list ordering | 8 | 915 | green |
| 0.9.0 | 26 Aug | Waiting states made distinct | 15 | 907 | green |

**The common thread:** seven of the eight releases add not a single event to the
orchestrator. Every new view is a projection of the event log the detail
response already loads — additive fields on `GET /api/runs/{repo}/{run_id}`,
read-only, no new route, no persistence, no new runtime dependency.

## 0.16.0 — Breakpoints as a generalised approval

An optional `breakpoints:` list in `.adw/config.yaml` holds the run before the
expensive, hard-to-reverse steps: `before_integration` once every build lane has
finished, `before_push` after the final review — before *any* CI work, including
preparation and forge polling. The hold reuses the existing approval path: phase
`awaiting_approval`, exit code 2, continuation via `adw approve`.

Deliberately no new phase value: which breakpoint is waiting lives in the new
state field `pending_breakpoint`. That leaves the `Phase` model, the phase bar,
retention and the recovery card from 0.13.0 untouched — the hold inherits its
call to action for free. Holds are idempotent across a crash and `resume`;
`--no-approval` skips them too, keeping a single switch for "no human approval
in this run". Without the key, behaviour is exactly as before.

**Known limitation [P2]:** `_config_for_continuation()` reloads
`.adw/config.yaml` on every `resume` and `approve`, and the run state keeps no
snapshot of the enabled breakpoints. Editing the configuration while a run is in
flight can therefore add or remove a future hold — the specification forbids
runtime changes, the code does not enforce it. The fix is to pin the effective
breakpoint set when the run starts.

## 0.15.0 — A run's change footprint

The run detail now shows, side by side, which files a run actually changed —
grouped per lane, with `+/-` counts per file from comparing that lane's first
and last snapshot — and the scope the contract declares, exactly as written.

**No automatic verdict is passed.** The reason is in the data: measured across
18 contracts, `x-adw-scope` exists in only eight, in inconsistent shapes, and
not one of them names files or path patterns. Marking a file "in scope" would be
guesswork, not derivation — so the facts sit side by side and a person makes the
call. "Diff available but empty" stays distinguishable from "no diff available":
a lane with a single snapshot does not falsely claim that nothing changed.

Observable as `change_scope` with `lanes` and `declared_scope`; existing diff
logic, no new git operation.

## 0.14.0 — Plan skeleton in the trace

When `plan.md` exists, the run detail derives a read-only list of planned tasks
per `## Workstream:` section and places it beside that lane's trace — "planned"
and "done" in one view. For a running run this is the first time what is still
outstanding becomes visible, rather than only what has happened.

The parser knows exactly two rules and **no** identifier pattern: a section runs
to the next `##` heading, a task is any `###` line, text taken verbatim. That is
not sloppiness but a measurement — across runs, plans write their tasks as
`B1 — …`, `1. …`, `A.1 — …` or `Aufgabe A — …`; a pattern filter would have
produced an empty list for the majority of them. Status stays deliberately
coarse, at lane level: the event log carries no task field, so mapping
individual trace nodes to plan tasks would be guesswork.

Observable as `plan_skeleton`; if `plan.md` is missing or does not match, the
skeleton is simply absent.

## 0.13.0 — Recovery card at the offending node

When a run needs a human, the run detail names exactly one appropriate next
command as copyable, POSIX-shell-safe text — with the real repository path from
the registry and the real `run_id`, never the URL slug. A pause at an approval
gate yields `adw approve`, an aborted working phase `adw resume`, a terminally
escalated run no command at all but a clear statement that a new run is needed.

The choice follows `state.phase` — not the `phase` field of the `escalation`
event. That distinction is what makes it work at all: `escalate()` sets the
state to `escalated` *before* the event carrying the originating phase is
emitted. On escalation the card shows reason, phase and the immediately
preceding `limit.hit` and `circuit_breaker` events, and links `escalation.md`
instead of duplicating it. The GUI stays strictly read-only: the command is
displayed, never executed.

**On CI:** this release's pipeline run has been stuck in the GitHub queue since
the push (an incident reported as "Partial System Outage"). The state was
verified locally with the same suite and is fully contained in the green runs of
0.14.0 and 0.15.0.

## 0.12.0 — Jump into the raw log, and prompt diffs

Every span node jumps into the raw tab, pre-filtered to its subtree range
`[seq, end_seq]`. For that the tab gained an inclusive seq range filter, composed
server-side with the existing free-text, type and window filters; `total` remains
the match count before windowing, a non-numeric bound is inactive, and a reversed
range yields a defined empty set — never a 5xx. Clearing removes only the range.

The prompt tab of an `agent.run` additionally shows a unified diff against the
previous run of the same agent in the same lane — for a fix round, precisely the
appended findings block. The predecessor is chosen strictly structurally: if the
immediate predecessor has no usable prompt, the answer is "no predecessor"
rather than a diff against the one before it, which would quietly show the wrong
thing. Three distinguishable states: no predecessor, identical prompt, diff.

Observable as `prompt_diff` and `previous_prompt_seq` on `agent.run` nodes; the
diff comes from the standard library's `difflib`, and the `…/events` route is
unchanged.

## 0.11.0 — Context panel with time travel

Beside the detail pane, a field list shows the run state *as of the selected
node*: phase, enclosing round with `n/cap`, the number of limits and circuit
breakers hit so far, cumulative cost, and the number of follow-ups. This makes
visible *why* a node ended the way it did, without walking the tree.

A node's cutoff is its own `seq`, or for spans the subtree maximum `end_seq`;
only events up to and including the cutoff count, which makes node selection a
form of time travel. It is computed in a single pass over the seq-ordered events
with a binary search per node — not a rescan per node, which on large runs would
have broken the documented response-time promise. Every absent value stays
`null`, never a fabricated zero.

Observable as `context` per trace node and `latest_context` at the top level;
`state.saved` is unchanged.

## 0.10.0 — Dry runs unmistakable, list ordered by urgency

A dry run carries a short label on its list row and a banner in the detail
header that stays pinned to the top of the viewport while the trace scrolls —
otherwise a thin simulation looks like a real run with missing data. It is
derived solely from the long-present `dry_run` field of the start payload, never
from absent token data.

Alongside it, a correction to a side effect of 0.9.0: the run list now sorts
`awaiting_approval` before `running` before the rest. Previously the very run
waiting on a human sank below newer finished ones. Within each group, newest
first still applies.

## 0.9.0 — "working", "waiting", "waiting on a human"

Three situations that used to look identical are now distinct. An open
`ci.wait` or `gate` span reads as `waiting` rather than `running` in the trace
tree — the same distinction the timeline had been drawing all along now agrees
across both views. A run paused at an approval gate reports `awaiting_approval`
even while its run span is still open, and the phase bar shows the waiting
business phase as `awaiting` instead of `active`.

The strongest emphasis goes to the one state where a person has to act.
Everything here is derived from the existing log; a run without a trace falls
back to its state phase, and a finished run keeps its terminal status untouched.

## Where these releases came from

All eight changes were implemented by the ADW orchestrator against its own
repository — one run each through spec, plan and contract with dual authorship,
a build with test-first gates, a Codex review, a final review and CI. The
template was a transfer from cobot programming: teach pendants have always
separated "working" from "waiting", show the program tree with an execution
pointer, keep variables for the current step at hand, and offer a course of
action on a fault rather than just a message.

Not everything went smoothly, and that is part of the picture: one run escalated
on a false factual claim in its issue brief, two more on a GitHub incident
during the CI phase, and one crashed with an SDK error and was resumed from its
checkpoint. The affected states were verified locally with the same suite; the
causes are recorded in the respective release commits.
