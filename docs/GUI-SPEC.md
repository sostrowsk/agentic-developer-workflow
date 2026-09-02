# ADW Run Inspector — Specification

**English** | [Deutsch](GUI-SPEC.de.md)

Status: draft · Target release: 0.4.0 · Related: [`SPEC.md`](SPEC.md), [`PLAN.md`](PLAN.md)

A local web GUI that makes **every single step of every ADW run** visible — live
while the run is going, and afterwards for forensics. Purpose: debugging
(why did this run escalate, where does the loop oscillate) and optimization
(which prompt produced which behavior, where do time and money go).

---

## 1. Problem

The orchestrator is a black box today. Observability consists of:

| Source | What it gives | What is missing |
| --- | --- | --- |
| `state.json` | Current phase, counters, pending feedback | Snapshot only — **overwritten on every save**, no history |
| `spec.md`, `plan.md`, `contract.yaml`, `issue.md` | Final artifacts | Not the path that produced them |
| `escalation.md` | Reason for the abort | Only on failure, only the end state |
| `followups.md` | Deferred findings | — |
| stdout of `adw run` | Coarse progress | Not persisted, not structured |

Critically absent:

- **Agent transcripts.** `agents.py:SdkAgentRunner._collect()` consumes the SDK
  message stream in RAM and returns only `AgentResult(text, session_id)`.
  Every tool call, every intermediate message, all token/cost data is discarded.
- **Prompts.** The task strings assembled in `phases.py` (including the
  content rules from `_SPEC_CONTENT_RULES` etc. and the system-prompt append)
  exist only for the duration of the call.
- **Gate output.** `GateFailure.output` lives in the process; only a shortened
  form reaches the next fix task.
- **A time axis.** Nothing carries a timestamp. Durations, waits, parallelism
  of lanes are unrecoverable.
- **Intermediate code states.** Agents do not commit — the orchestrator commits
  once per lane at the end. What an individual agent run changed leaves no
  trace in git.

Therefore this feature is roughly 70 % **instrumentation of the orchestrator**
and 30 % viewer.

## 2. Goal and non-goals

### Goal

1. Every ADW run writes a complete, structured **event log** — down to the
   individual tool call of an individual agent.
2. `adw gui` starts a local web app that renders this log: run list, run detail
   with phase map + trace tree + detail pane, timeline, artifacts.
3. Live view of a running run, identical rendering for a finished one.
4. Four debugging questions are answerable without reading source code:
   *Where does the loop get stuck? What did the agent see? Where do time and
   money go? What did each step change in the code?*

### Non-goals (v1, deliberately deferred)

- **Control from the GUI.** No approve/resume/abort/start — v1 is read-only.
  Rationale: no write path into state or repo means the GUI cannot damage a run.
- **Cross-run statistics** (failure rates per gate, cost trends, finding
  categories). Needs a corpus of runs first.
- **A/B run comparison** for prompt optimization.
- **Redaction** of secrets in the log (explicit decision, see §9).
- **Remote/multi-user operation.** Binds to loopback, no auth, no TLS.
- **Replacing `adw status`.** The CLI stays the primary control surface.

## 3. Architecture

Three separable parts:

```
adw/
  events.py          NEW  emitter: append-only JSONL, span IDs, fail-open
  snapshots.py       NEW  per-step tree snapshots as git refs (diff basis)
  gui/               NEW  read-only web app
    __init__.py
    app.py                FastAPI app factory
    reader.py             JSONL reader (tail-capable, byte-offset based)
    model.py              events -> span tree (pydantic)
    registry.py           ~/.adw/repos.json — which repos to show
    i18n.py               de/en label dictionary
    templates/*.html      Jinja2
    static/*              vendored htmx + own CSS (NO CDN)
  phases.py          MOD  ~40-60 emit() calls
  agents.py          MOD  SDK stream is mirrored into the log
  gates.py           MOD  gate start/end incl. full output
  codex.py           MOD  review start/end incl. raw stdout
  cli.py             MOD  new commands: gui, runs prune
```

Data flow:

```
adw run ──emit()──> .adw/runs/<run_id>/events.jsonl   (append-only, 0600)
        └─snapshot─> refs/adw/<run_id>/<seq>          (git objects for diffs)

adw gui ──read────> registry ~/.adw/repos.json
        ──tail────> events.jsonl  (poll by byte offset)
        ──git─────> diff between snapshot refs
        ──SSE─────> browser
```

The orchestrator never talks to the GUI, the GUI never writes into the run.
The only interface is the file format of §4.

## 4. Event log

### 4.1 File

- Path: `.adw/runs/<run_id>/events.jsonl` in the **target repo**, next to
  `state.json`.
- Format: JSON Lines, UTF-8, one object per line, `\n`-terminated,
  **append-only** — an existing line is never modified.
- Permissions: `0600` (as `state.json` today).
- `ensure_runs_gitignored(repo)` (already exists in `worktrees.py`, writes
  `.adw/runs/.gitignore` with `*`) MUST be called before the first write.

### 4.2 Record schema

```jsonc
{
  "seq": 412,                     // monotonic per run, gap-free
  "ts": "2026-08-05T14:02:20.117Z",// UTC, millisecond precision
  "type": "agent.tool.call",      // see §4.4
  "kind": "point",                // "start" | "end" | "point"
  "span": "01J9…",                // ID of the span this event belongs to
  "parent": "01J9…",              // parent span; null for run
  "phase": "build",               // RunState.Phase at emit time, or null
  "lane": "backend",              // lane name or null
  "round": 2,                     // loop round or null
  "payload": { }                  // type-specific, see §4.4
}
```

Rules:

- `seq` is assigned by the emitter under the file lock and is gap-free — a gap
  means the log is truncated/corrupt, and the GUI says so instead of silently
  rendering something wrong.
- `kind: "start"` opens a span, `kind: "end"` closes it (same `span`).
  Duration = difference of the `ts` values. `point` events have no duration.
- Unknown `type` values MUST be rendered generically by the GUI (icon +
  raw payload) instead of dropped or fatal — the log format stays
  forward-compatible.
- **Orphan spans and the containment rule.** `parent` is derived from a
  thread-local stack, so a span opened in a worker thread carries
  `parent: null` even though it is logically nested. This happens on every
  run (dual authoring writes both drafts in a `ThreadPoolExecutor`) and in
  parallel lanes. The emitter is deliberately *not* extended with an explicit
  parent argument — the tree is repaired on read instead, by exactly this
  rule, which every consumer MUST implement identically:

  > An orphan (a span with `parent: null` that is not the `run` root) belongs
  > to the **innermost** span whose interval strictly contains the orphan's
  > `[start ts, end ts]` — that is, among all containing candidates the one
  > with the latest start; ties are broken by the higher `seq`. A span still
  > running counts as containing everything after its start. An orphan that
  > nothing contains stays a child of the `run` root.

  Consequence, stated plainly: the tree is **not** derivable from `parent`
  alone, and the log is not self-describing on this point. That is the
  accepted price for leaving the emitter unchanged.

### 4.3 Writing: locking, ordering, fail-open

- Parallel lanes run as threads in one process (`phases.py` uses `threading`),
  but a `resume` starts a new process. The emitter therefore serializes with
  an **`fcntl.flock` on `events.jsonl`** (analogous to `state._repo_lock`):
  `open("a")` → `LOCK_EX` → assign `seq` → `write` → `flush` → unlock.
- `flush()` yes, `fsync()` **no** — per event fsync would dominate a run's
  runtime. Trade-off: a hard crash may lose the last unflushed lines. Accepted:
  the log is a debug artifact, `state.json` remains the resume authority.
- Readers only ever parse **complete lines** (terminated by `\n`). A trailing
  partial line is ignored and re-read on the next poll.
- **Fail-open is mandatory.** Every emitter call is wrapped so that no
  exception (disk full, permissions, encoding) can reach the orchestrator.
  On error: `logger.warning` **once per run and process**, then every emitter
  for that run goes silent for the rest of the process. The scope is
  per-process on purpose — enforcing it across processes would need
  persistent sidecar state, which §4.1 forbids, so a `resume` in a fresh
  process may warn once again. **A broken event log must never abort a run.**
  This is the single most important invariant of the whole feature.

### 4.4 Event types

Span-forming (`start`/`end`):

| `type` | Payload (start) | Payload (end) |
| --- | --- | --- |
| `run` | `issue`, `parallel`, `dry_run`, `repo`, `base_branch`, `adw_version`, `lanes[]` | `status` (`done`\|`escalated`\|`awaiting_approval`), `totals` (duration, cost, tokens) |
| `phase` | `name`, `from_phase` | `name`, `to_phase` |
| `lane` | `name`, `branch`, `worktree`, `base_sha`, `ports` | `completed`, `gate_iterations`, `fix_cycles` |
| `round` | `loop` (`authoring`\|`gates`\|`integration`\|`codex_review`\|`final_review`), `n`, `cap` | `outcome` |
| `agent.run` | `agent`, `model`, `tools[]`, `allowed_tools[]`, `cwd`, `resume_session`, **`prompt`** (full task string), `system_append` | `session_id`, **`result_text`**, `usage` (`input`, `output`, `cache_read`, `cache_creation`), `cost_usd`, `is_error` |
| `gate` | `name`, `cmd`, `timeout`, `cwd` | `passed`, `exit_code`, `timed_out`, **`output`** |
| `codex.review` | `kind`, `argv[]`, `cwd`, `custom_prompt` | `findings[]` (full `Finding` objects), `raw_stdout`, `parse_ok` |
| `codex.author` | `kind`, `argv[]`, `cwd`, `task` | `artifacts[]` (returned file names), `raw_stdout`, `parse_ok` — dual authoring is a substantial slice of a run's time and cost; without this span the timeline shows a gap where the Codex draft was written |
| `ci.wait` | `provider`, `pipeline_ref` | `status`, `polls`, `duration` |

Point events:

| `type` | Payload |
| --- | --- |
| `agent.message` | `role`, `text` (assistant text block) |
| `agent.tool.call` | `tool`, `tool_use_id`, `input` |
| `agent.tool.result` | `tool_use_id`, `is_error`, `content` |
| `snapshot` | `lane`, `tree`, `ref`, `label` (`before_agent`\|`after_agent`\|`after_gates`\|`red`) |
| `red.check` | `confirmed`, `test_paths[]`, `gates[]` |
| `commit` | `lane`, `sha`, `subject` |
| `merge` | `lane`, `target`, `conflicts[]` |
| `ci.poll` | `provider`, `status`, `job` |
| `ci.reentry` | `n`, `reason` |
| `triage.decision` | `finding_key`, `severity`, `action`, `reason` |
| `limit.hit` | `limit`, `value`, `cap` |
| `circuit_breaker` | `keys[]`, `scope` |
| `escalation` | `reason`, `phase` |
| `approval` | `gate` (`spec`\|`plan`\|`before_integration`\|`before_push`), `event` (`awaited`\|`granted`) |
| `artifact` | `name`, `path`, `bytes`, `sha256` |
| `followup` | `finding_key`, `text` |
| `state.saved` | `seq` (RunState.seq), `phase` |
| `log` | `level`, `message` (orchestrator warnings, e.g. from `logger.warning`) |

`agent.run` start carries the **complete prompt**, `agent.message` /
`agent.tool.*` the full stream, `agent.run` end the final text. That is the
"what did the agent see?" view in full.

### 4.5 Retention

Raw capture without truncation (decision, §9) means the log grows with the
volume of tool output. Countermeasure is retention only:

- New CLI: `adw runs prune [--repo PATH] [--keep N] [--older-than DAYS] [--gzip]`
  - default `--keep 20` per repo (oldest runs first)
  - `--gzip` compresses `events.jsonl` to `events.jsonl.gz` instead of deleting
    (reader handles both transparently)
  - pruning deletes the associated snapshot refs (`refs/adw/<run_id>/*`) too
- Optional automatic pruning after a successful run, configurable in
  `.adw/config.yaml`:

  ```yaml
  trace:
    enabled: true      # default true; false = no event log at all
    keep_runs: 20      # 0 = never prune automatically
  ```

- `adw runs list` shows run ID, phase, date, event count, log size — so it is
  obvious when pruning is due.

## 5. Snapshots and step diffs

To make "what did this step change?" answerable, the working tree state is
pinned at each step boundary:

1. `snapshots.capture(ctx, worktree, label)` builds — exactly like the existing
   `phases.py:_worktree_tree_hash()` — a temporary index
   (`read-tree HEAD` → `add -A` → `write-tree`) and gets a **tree object**.
2. `git commit-tree <tree> -p <base_sha> -m "adw snapshot <label>"` turns it
   into a commit, `git update-ref refs/adw/<run_id>/<seq> <commit>` keeps it
   alive against `git gc`.
3. The `snapshot` event stores `tree` and `ref`.

Step diff in the GUI = `git diff <ref_before> <ref_after>` — computed lazily on
request, therefore **zero patch text in the event log** and exact even months
later. Snapshot points: before and after every agent run, after the TDD RED
test-only pass, after every gate iteration.

Cost: one `write-tree` + `commit-tree` per boundary (both cheap, no working
tree writes). Failure of a snapshot is fail-open like any emit — the GUI then
shows "no diff available" for that step.

## 6. Instrumentation points

Explicit `emit()` calls (decision), fail-open, no magic:

| File | Where | Events |
| --- | --- | --- |
| `cli.py` | `run`/`resume`/`approve`: as soon as the run identity and the emitter exist, until every command exit | `run` start/end, `approval` |
| `phases.py` | every phase function entry/exit | `phase` start/end |
| `phases.py` | `_reviewed_authoring_loop` | `round`, `codex.review`, `artifact` |
| `phases.py` | every `ctx.agents.run(...)` / `ctx.codex.review(...)` call site, incl. `_draft_stage`, `_claude_draft`, `_codex_draft` | `agent.run`, `codex.review` (the **spans** live here, not in the runners — only then do mock runners produce them too and a dry run stays a usable acceptance path), `artifact` (dual authoring: both drafts + synthesis are visible individually) |
| `phases.py` | `_run_lane`, `_run_lane_gates` | `lane`, `round`, `snapshot`, `commit` |
| `phases.py` | `_confirm_red`, `_run_test_only_pass`, `_require_red_tests` | `red.check`, `snapshot` |
| `phases.py` | `escalate()`, limit and circuit-breaker checks | `escalation`, `limit.hit`, `circuit_breaker` |
| `phases.py` | integration/merge, `_record_followup` | `merge`, `followup` |
| `agents.py` | `SdkAgentRunner.run` / `_collect` | the **contents** of the `agent.run` span opened by the call site: `agent.message`, `agent.tool.call`, `agent.tool.result`, plus usage/cost into its end payload. Mock runners contribute nothing here — correctly so, they have no tool calls |
| `gates.py` | `run_gates` per gate | `gate` start/end |
| `codex.py` | review subprocess | `codex.review` start/end |
| `triage.py` | decision function | `triage.decision` |
| `ci.py` / `github.py` | poll loop | `ci.wait`, `ci.poll`, `ci.reentry` |
| `state.py` | `save`/`update` | `state.saved` |

`agents.py` is the deepest cut: `_collect()` currently only extracts `text` and
`session_id`. It gains a per-message branch that mirrors `ToolUseBlock`,
`ToolResultBlock`, `AssistantMessage.usage`, `ResultMessage.total_cost_usd` and
`model_usage` into the log. Contract: the return value of `_collect` stays
byte-identical — instrumentation must not change behavior. That is regression-
tested.

## 7. The web app

### 7.1 Start and registry

```
adw gui [--repo PATH]... [--host 127.0.0.1] [--port 8765] [--open] [--lang de|en]
```

- Binds to loopback only. `--host` accepting a non-loopback address requires an
  explicit `--i-know` flag — the log contains raw agent output (§9).
- Repos come from `~/.adw/repos.json`; **every `adw run` registers its repo
  there automatically** (path + last-seen timestamp). `--repo` adds ad hoc.
  Repos that no longer exist are shown greyed out, never crash the app.
- Stack: FastAPI + uvicorn + Jinja2 as optional extra `adw[gui]` — a plain
  `adw run` install stays free of web dependencies.
- **No CDN — and no third-party frontend asset at all.** Vanilla JS
  (`fetch`, native `EventSource` for SSE), hand-written CSS, system fonts.
  Vendoring a library would mean downloading it once from the network, and
  nothing here needs one: the whole client is a run list, a collapsible tree,
  a detail pane and an event stream. Zero supply chain, nothing to keep
  up to date.

### 7.2 Views

**A — Run list (`/`)**

Table across all registered repos: run ID · repo · issue (truncated) · phase ·
status (running / awaiting approval / done / escalated) · start · duration ·
cost · event count. Sortable, filter by repo and status. Runs are grouped by
status: `awaiting_approval` first, then `running`, then the rest — the run that
needs a person to act stays at the top instead of sinking below newer finished
runs. Within each group the existing newest-first order is kept. Live-updating.

A dry run (`dry_run: true` in the `run` start payload) carries a short `Dry-Run`
label on its row so a content-thin simulation is never mistaken for a real run
with little output. The label follows the selected language; it is a marking
only — it never changes status, ordering, filtering or retention. When the field
is absent (older logs) or the `run` span is missing, the run is treated as a
normal run.

A run whose `run` span is still open but is paused at an approval gate reports
`awaiting_approval` — not `running` — in both the run list's status column and
the run-detail header (the two endpoints always agree). This is derived from the
event log: the most recent `approval` event is `awaited` with no later `granted`
(a later `granted` returns the still-open run to `running`); a closed `run` span
keeps the terminal status from its end payload untouched. For a run without a
trace, the state phase is the fallback — `awaiting_approval` / `awaiting_spec_approval`
→ `awaiting_approval`. `awaiting_approval` is the one state that needs a person to
act and is emphasised the strongest of all states. The JSON status values stay
language-neutral (`waiting`, `awaiting`, `awaiting_approval`); only their labels
are translated. A run paused at a configurable **breakpoint** (`gate`
`before_integration`/`before_push`) is shown exactly like the spec/plan gates:
the same `awaiting_approval` status and the same read-only recovery hint
`adw approve <run_id> --repo <path>` — no new write path and no new phase value;
the GUI stays read-only.

**B — Run detail (`/runs/{repo}/{run_id}`)** — the main view:

```
┌─ Run 1789dbd5 · leasing · ● running · 12:04 ─────────────┐
│ [Spec✓][Plan✓][Build◐][Integr][Codex][Final][CI]        │
├──────────────────────┬───────────────────────────────────┤
│ ▾ Build         4:12 │ Agent: builder (opus-4.8)         │
│   ▾ lane backend     │ Runde 2/10 · 1.203s · 84k tok     │
│     ▾ Runde 1        │                                   │
│       ✓ RED          │ [Prompt][Antwort][Tools][Diff]    │
│       ▸ agent   2:01 │ ───────────────────────────────── │
│       ✗ gate lint    │ 14:02:11 Read  models.py          │
│     ▸ Runde 2 ●      │ 14:02:14 Edit  models.py          │
│   ▸ lane frontend    │ 14:02:20 Bash  pytest -q          │
│                      │          → exit 1, 3 failed       │
└──────────────────────┴───────────────────────────────────┘
```

0. **Dry-run banner** (header): a dry run additionally carries a persistent
   `Dry-Run` banner in the header that stays pinned to the top of the viewport
   (a sticky header) while the trace tree scrolls, so the run cannot be mistaken
   for a real one even far down the tree. It is derived from the same `dry_run`
   field as the run list, follows the selected language, and appears only for a
   dry run; a normal run's header is unchanged.
1. **Phase map** (header): the seven phases as a status bar — done / active /
   pending / **awaiting** / failed, with duration each. Click scrolls the tree to
   that phase. A run paused at an approval gate shows its waiting business phase
   (`spec` or `plan`) as `awaiting` instead of `active`; no other phase is active
   then, and `awaiting` disappears once the gate is granted. This is the
   orientation layer; it mirrors the flowchart in
   `docs/adw-flowchart.excalidraw`.
2. **Trace tree** (left): the span tree from §4.2, collapsible, chronological.
   Each node: icon (status), label, duration, and for loops `n/cap`. Lanes are
   siblings — parallelism is visible as two open branches. Auto-scroll to the
   active node while live (toggleable). An open span that is pure **waiting** (a
   `ci.wait` poll loop or a `gate` runtime — the shared `_WAITING_TYPES`) reads
   `waiting`, not `running`, so idle polling is told apart from real work; the
   same span the Timeline draws as waiting reads `waiting` here too. A closed
   `gate`/`ci.wait` span keeps its result (`passed`/`failed`, else `done`).

   **Compacted tree column (tool-noise folding).** The tree *column* renders EVERY
   node of the run — it is not paged and has no entry cap. What keeps it readable is
   the compaction below, a presentation layer that never changes the JSON `tree`
   (see §API) and joins only direct neighbours.
   - **Results fold into their call (A1).** An `agent.tool.result` whose
     `tool_use_id` equals that of the immediately preceding `agent.tool.call` is not
     a row of its own; its outcome (from the existing result label — `ok`/`error`,
     never an invented success for an undetermined result) and its duration
     (`ts(result) − ts(call)`, shown only when both parse and the difference is
     `≥ 0`) ride at the right of the call row. A result with no matching predecessor
     stays its own node.
   - **Repetitions collapse (A2).** ≥ 2 immediately consecutive neighbours sharing a
     repeat key become one collapsible repeat node with a counter and the summed
     determinable duration; expanded it lists the single members in order with their
     folded results. The key is (tool, target) for `Read`/`Grep`/`Glob` **and**
     `Write`/`Edit` (target: `input.file_path`, resp. `input.pattern` for
     Grep/Glob — exact raw comparison), and the type alone for `artifact`: adjacent
     artifacts are each a different file, so target identity would never collapse
     them while the rows carry no distinguishing text. Writes and artifacts are
     counted but never *grouped* — they keep ending a read/search run (A3).
   - **Read/search runs group (A3).** An uninterrupted run of `Read`/`Grep`/`Glob`
     operations with ≥ 2 children (after A1/A2) becomes one collapsible group node
     with the call count and the operation kinds present. The run ends — and the
     breaking event stays its own, unfolded node — at the first message, any write
     (`Write`/`Edit`), any artefact, any determinate error, and at `Bash`/any
     unknown tool. Below two children nothing is wrapped (not even a lone repeat).
   - **Repo-relative paths (A4).** A path inside the repo shows with the repo prefix
     stripped and keeps the full path in the element's `title`; a path outside the
     repo is shown unchanged. No node shows the absolute repo path as visible text.
   - **Default fold (A5).** The tree opens with phases collapsed; open by default is
     only the phase with the tree-order-first determinate error, else the
     last-started phase (a server-decided marker; the fold itself is client state
     with no persistence). There are exactly three collapse levels — phase, group,
     repeat; `agent.run`, `round` etc. get none. A `?focus=<seq>` deep link opens
     the collapsible ancestors of its target on the loaded page; a `?focus` on a
     folded result is redirected to its call node (same `tool_use_id`) — that call's
     `seq` is selected and its pane shown, the result content stays reachable through
     the Tools tab.
   - **Line balance (A6).** Below the tree, a balance over the whole run of two
     numbers: rows (display entries outside any collector) and events folded (all
     nodes minus the entries that are themselves an original event — so attached
     results and every collector member count once).
3. **Detail pane** (right): depends on the selected node. A **span** node (phase,
   lane, round, `agent.run`, gate, review, …) has its own server-rendered pane. A
   **point** node (tool call/result, message, snapshot, …) has none — the many of
   them would put thousands of hidden elements into the unpaged column; they share
   ONE server-rendered shell that the client re-points at the selected node and fills
   from the read-only events route (heading from the tree row). No DOM is constructed
   in JS (§7.3). Either way the payload renders as an indented **field list**, not as
   a JSON dump: a multi-line string (a run's issue, an agent's prompt) keeps its real
   line breaks under its key, scalars are `key: value`, containers nest by two spaces.
   Server and client produce the same text (`_pretty_payload` / `prettyPayload`) for
   the value ranges ADW payloads contain, so a payload reads identically wherever it
   is shown. Two divergences are known and deliberately not chased, because no event
   payload produces them: an integer beyond 2^53 or ≥ 1e21 (the client parses JSON
   numbers as IEEE-754 doubles), and an object with integer-like keys in non-ascending
   order (`Object.keys` reorders array-index keys). For `agent.run` four tabs:
   - **Prompt** — the full task string plus system append, monospace, copyable
     (the prompt-optimization lever). Additionally a **unified diff** of this
     prompt against the prompt of the *previous* `agent.run` of the **same agent
     in the same lane** within this run — so a fix round's appended findings block
     is visible without laying two Prompt tabs side by side. The predecessor is
     chosen purely structurally (same agent string, same lane, the greatest `seq`
     below this node's) *before* prompt usability matters; other runs, agents or
     lanes are never borrowed. The diff is produced server-side with the standard
     library `difflib` only (`splitlines()`, `unified_diff(prev, cur, n=3,
     lineterm="")`, joined with `\n`); a trailing-newline-only difference counts as
     identical. The tab shows exactly one distinguishable state: *no predecessor*
     (both derived fields `prompt_diff`/`previous_prompt_seq` null), *identical
     prompt* (`prompt_diff` `""` with `previous_prompt_seq` set — distinct from the
     null case) or the visible diff. The two fields are additive, purely derived
     from the already-loaded event stream, and ride only `agent.run` nodes of
     `GET /api/runs/{repo}/{run_id}`.
   - **Answer** — final text plus all intermediate assistant messages.
   - **Tools** — chronological tool-call list, each expandable to full input
     and full result.
   - **Diff** — `git diff` between the snapshot refs bracketing this step,
     syntax-highlighted, with `+/-` counts per file.

   For `gate`: command, exit code, full output. For `codex.review`: findings as
   a table (severity, key, file, message) plus raw stdout. For `phase` /
   `lane` / `round`: aggregation of the children (duration, cost, outcome).

4. **Tabs at run level**: `Trace` (default) · `Timeline` · `Artifacts` · `Raw`.
   - **Timeline**: horizontal swimlanes (orchestrator, spec, plan, per lane,
     codex, CI) as CSS bars — active vs. waiting (CI polling, gate runtime)
     rendered differently. Answers "where does the time go". Header shows
     total duration, total cost, tokens per model.
   - **Artifacts**: `issue.md`, `spec.md`, `plan.md`, `contract.yaml`,
     `escalation.md`, `followups.md`, the drafts from dual authoring — rendered
     as Markdown, with the drafts side by side against the synthesis.
   - **Raw**: the event log as a filterable JSON list — the fallback that always
     works, even for event types the GUI does not know yet. Beside the existing
     free-text (`raw_q`) and event-type (`raw_type`) filters and the `limit`
     window it accepts an **inclusive seq range** (`raw_from_seq`/`raw_to_seq`,
     each bound optional and one-sided-capable). The range is composed server-side
     (logical AND) with the other filters; `limit` windows only the fully filtered
     match set and the reported `total` stays the pre-window size; the offered
     `types` list stays the full type set of the log. A non-numeric bound is
     treated as absent (that bound inactive) and an upper bound below the lower
     yields a defined empty set — never a 5xx. Every **span node** in the trace
     tree offers a jump into this Raw tab pre-filtered to the node's already-exposed
     `[seq, end_seq]` subtree range (a pure seq-range filter — interleaved events
     of parallel spans inside the interval are *not* excluded); the jump preserves
     the current `raw_q`/`raw_type`/`limit` and activates the existing Raw tab (no
     second Raw widget). An active range is shown with its bounds and cleared in
     isolation — clearing drops only the seq range and keeps `raw_q`/`raw_type`/
     `limit`. The read-only events route (`…/events`) is unchanged: still only
     `from_seq`/`to_seq`, no `type`, no pagination.

5. **Run-context panel** (beside the detail pane): a read-only field list showing
   the run state **at the seq of the selected node** — answering *why* a node went
   the way it did without clicking up and down the tree or switching to Raw. It is
   a purely derived projection of the same event stream the detail response already
   loads (no new event, reader, route, persistence or state-schema change); every
   node carries its own six-field context in the render, so selection only
   re-projects it — no client-side re-derivation. The six fixed fields are:
   `phase`, `round` (`{loop, n, cap}` of the enclosing loop, if any), `limit_hits`,
   `circuit_breakers`, `cost_usd` (cumulative, via the existing cost logic), and
   `followups`.
   - **Cutoff / time travel**: a node's cutoff is its own `seq` (point event) or
     its exposed `end_seq` (span — the subtree maximum, so a finished/running span
     includes qualifying events *inside* it after its start). Only events with
     `seq ≤ cutoff` count, so an earlier node never reflects a later event.
     Selecting another node shows that node's historical state.
   - **No selection / live**: with no node selected the panel shows
     `latest_context`, derived through the greatest observed `seq` — the live view.
     It refreshes through the existing mechanisms; the SSE protocol is unchanged.
   - **Empty semantics**: every absent datum is **empty** — `null`, never a
     fabricated `0`. Counts and cost are `null` until their first occurrence; a
     phase with no valid observation, and a node outside any round, are `null`. A
     run without a trace has no per-node context and no top-level `context` field —
     only a `latest_context` with all six fields null — and never an error.
   - `phase` is derived from exactly two existing sources — a non-empty `name` in a
     `phase` span start and a non-empty `phase` in a `state.saved` payload — with
     the greatest-`seq` valid observation at or before the cutoff winning
     (`state.saved` is read only as its existing `{seq, phase}` payload, never
     extended). The panel is a plain field list — no chart, no history curve — and
     offers no configurable field set and no selection persistence.

6. **Recovery card** (when the run needs a human step): the run detail shows exactly
   one card that leads from the mere state display to the concrete next step. It is a
   purely derived projection of the already-loaded state (`state.phase`), the
   existing run-status derivation, the event stream and the server-resolved
   repository path (`RepoRef.path`) — no new event, reader, route, persistence or
   liveness detection. Observable as an additive `recovery` object on
   `GET /api/runs/{repo}/{run_id}`, present **exactly** when the run needs
   intervention and **absent** otherwise (never an empty object).
   - **Trigger and selection** follow *only* `state.phase` (never the `escalation`
     event's `phase`, which is always the origin phase and can never be
     `escalated`). Lifecycle basis (verified in code): `escalate()` sets
     `state.phase` to `escalated` and only *then* emits the `escalation` event, so a
     run carrying an `escalation` event is *always* finally escalated — approval
     pauses and transient aborts/crashes emit no such event. Hence:
     `escalated` → kind `none` (a NEW run is required, no continuation command);
     `awaiting_spec_approval`/`awaiting_approval` → kind `approve`; a work phase
     (`spec`, `plan`, `build`, `integration`, `codex_review`, `final_review`, `ci`)
     whose derived run-status is not `running` → kind `resume`; `done`, a running
     work phase, or no loadable state → no card. The `escalated` check strictly
     precedes all others, so an escalated run is never offered `resume`/`approve` —
     consistent with `adw resume` itself refusing an escalated run.
   - **Command** (kinds `approve`/`resume`): the finished, copyable text in the
     existing CLI signature — `adw approve <run_id> --repo <path>` resp.
     `adw resume <run_id> --repo <path>` — with the real `run_id` and the real,
     server-resolved registry path, **not** the slug. `run_id` and path are rendered
     POSIX-shell-safely per `shlex.quote` semantics, so a path with spaces, single
     quotes or shell metacharacters stays exactly one `--repo` argument and yields no
     extra command. Kind `none` carries no command but the machine-readable
     `needs_new_run` flag.
   - **Escalation context** (kind `none`): `reason` and the affected `phase` verbatim
     from the `escalation` event with the greatest `seq`; the associated
     `limit.hit`/`circuit_breaker` abort events (those between the immediately prior
     escalation and the governing one, payloads unchanged); and a reference to
     `escalation.md` in the Artifacts tab — the card links to it instead of
     duplicating its content. The card is anchored at the governing escalation node
     (`anchor_seq`); where an escalated run lacks a usable event log the context is
     `null`/empty (never invented) and the still-usable card falls back to run level.
   - **Read-only** (E1/§2): the command is displayed, never executed — rendering
     spawns no subprocess and writes nothing. The real repo path appears *only* in
     the command text, never in a URL (the slug rule of §7.4 is unchanged). All card
     labels are bilingual (`adw/gui/i18n.py`); the command line, event values,
     `run_id` and repo path are not translated.
7. **Plan skeleton** (when the run's `plan.md` yields planned tasks): the Trace view
   shows, per workstream, a read-only list of the tasks *still planned* beside/above
   that lane's trace — so "planned" (skeleton) and "done" (trace) sit in one view. It
   is a purely derived projection of the run's `plan.md` (read only through the
   existing whitelist artifact path) and of the already-loaded event stream (for the
   coarse lane status) — no new event, reader, route or persistence. Observable as an
   additive `plan_skeleton` array on `GET /api/runs/{repo}/{run_id}`, present
   **exactly** when `plan.md` yields at least one `## Workstream:` section with a
   `###` task, and **absent** otherwise (never a forced empty list).
   - **Parse rules** (exactly two, no identifier pattern, no Markdown parser, no
     dependency): a *section* starts at a line `## Workstream: <name>` and ends at the
     next `##` heading (any line starting with `##` that is not `###`) or at end of
     file; `<name>` is the text after the exact prefix `## Workstream: `. A *task* is
     every line in the section starting with the exact prefix `### ` — the task text
     is the remainder **verbatim** (the marker and exactly one separator space
     removed, no split into identifier and title, no further trimming). A bare `###`
     with no following text is no task; `###` lines outside a section or after its
     closing `##` heading do not count. The heterogeneous forms across runs
     (`### B1 — …`, `### 1. …`, `### A.1 — …`, `### Aufgabe A — …`, `### Aufgabe B1 —
     …`) are all kept, one task each. One entry per section with ≥1 task, in document
     order; a section without a task produces no entry.
   - **Status** is coarse and on the **lane level** only: `done` when the `lane` span
     whose name equals the workstream ends with `completed: true`; otherwise
     `pending` — including a `lane` end without `completed: true`, a still-running
     lane, and a not-yet-started lane. There is no per-task and no per-node status,
     and no guessed task-to-node mapping. A not-yet-started lane still shows its
     `pending` list **without** creating an empty or artificial trace node; the trace
     tree and its node structure are unchanged.
   - **Fallback** (robustness): a `plan.md` that is missing, empty, unreadable, absent
     per the artifact path (a symlink escaping the run directory), or that carries no
     matching section yields **no** skeleton — `plan_skeleton` is absent, no error, no
     empty box, and the rest of the detail response and the existing view are
     unchanged.
   - **Read-only** (E5): pure display — no checking off, no editing, no write route,
     no new persistence. Chrome labels (the list heading and the `pending`/`done`
     markers) are bilingual (`adw/gui/i18n.py`); the task texts are content and are
     not translated.

8. **Change scope** (always present): the run detail shows, side by side, which files
   the run actually changed — grouped per lane, with `+/-` counts per file — and the
   scope the contract declares, as it stands. Both facts sit **unjudged** next to each
   other; whether a change is "in scope" is decided by a human. It is a purely derived
   projection of the already-loaded events, the existing snapshots and the whitelisted
   `contract.yaml` — no new git operation, route, event or persistence. Observable as
   an additive `change_scope` object (`lanes` + `declared_scope`) on
   `GET /api/runs/{repo}/{run_id}`; the key is **always** present.
   - **File lists** (per lane): a lane is *observed* when the event log carries a
     `lane` span with a non-empty name **or** a structurally valid snapshot event (ref
     form `refs/adw/<run_id>/<seq>`) declaring it — the lanes appear in first-observation
     order (smallest seq), one entry per name. For a lane with **≥ 2** valid snapshots
     the entry carries the diff between its lowest- and highest-seq snapshot — produced
     by the **existing** snapshot/diff/numstat logic, one comparison per lane, other
     lanes never mixed in — as `diff_available: true` with `files` (each `path`,
     `additions`, `deletions`; a binary file's counts are `null`, shown as "not
     numerically available"). A produced diff with no changes is `files: []` (shown as
     "no changed files found"), distinct from unavailable.
   - **Unusable / failed diffs** (robustness): a lane with **0 or 1** valid snapshot,
     or whose diff fails despite a pair (missing snapshot object, timeout, execution
     error), is `diff_available: false` with `files: null` — canonically this shape,
     never `[]`, never an omitted field — shown as "no diff available" instead of an
     empty table. A failed lane never blocks other lanes and never turns the
     otherwise-successful detail request into a 5xx. Every observed lane keeps its own
     entry (name + per-lane state), so a span-only lane with no snapshot is never
     hidden. When **no** lane has a usable diff, only the file tables are dropped — each
     lane still shows its "no diff available" — and a clear global "no run diff
     available" statement is added, while the declared scope still renders.
   - **Declared scope**: `declared_scope` is a readable, **semantically equivalent**
     YAML serialization of all top-level `x-adw-*` blocks of `contract.yaml` (read only
     through the existing whitelist artifact path with the already-present `yaml`
     module), in document order, values and nesting unchanged — no rename, merge,
     normalization or interpretation, and textual details (comments, quoting) are not
     preserved. A missing, unreadable, non-mapping, non-safely-loadable or
     `x-adw-`-less contract (a non-string top-level key is ignored, never a crash), or
     a boundary-escaping symlink, yields `declared_scope: null`, shown clearly as "no
     declared scope" — a neutral absence, not a violation.
   - **No judgement** (E1): there is no field or marker for "in scope"/"out of
     scope"/"violation"/conformance, no file↔`x-adw-*` mapping and no derived rating —
     the `change_scope`, lane and file objects carry exactly the listed keys. A
     structured file scope and a real violation check are deliberately deferred.
   - **Read-only** (E6): pure additive projection — no new route, event, write or
     state; all existing response fields are unchanged. Chrome labels (heading, `+/-`
     column headers, the fallback texts) are bilingual (`adw/gui/i18n.py`); file paths
     and the declared scope are content and are not translated.

### 7.3 Live update

- `GET /api/runs/{repo}/{run_id}/stream` — SSE. Server tails `events.jsonl` by
  byte offset (poll interval 500 ms; no filesystem-watch dependency), sends
  each new complete line as an event.
- The client patches the tree incrementally; the GUI never re-renders the whole
  page. Reconnect via `Last-Event-ID` = last `seq`.
- A finished run closes the stream after `run` end. A GUI opened later sees no
  difference — same rendering path.

### 7.4 API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/runs` | run list (JSON) |
| `GET /api/runs/{repo}/{run_id}` | metadata + span tree |
| `GET /api/runs/{repo}/{run_id}/events?from_seq=N&to_seq=M` | raw events, seq-range only |
| `GET /api/runs/{repo}/{run_id}/stream` | SSE live tail |
| `GET /api/runs/{repo}/{run_id}/diff?from=REF&to=REF` | step diff |
| `GET /api/runs/{repo}/{run_id}/artifacts/{name}` | artifact content |

`{repo}` is a stable slug from the registry, never a raw filesystem path in the
URL. Path traversal is impossible: only registry-known repos, only run IDs
matching `RUN_ID_RE`, only a whitelist of artifact names.

### 7.5 i18n

`adw/gui/i18n.py` holds a `dict[str, dict[str, str]]` for `de` and `en`. Label
selection: `?lang=` → cookie → `Accept-Language` → `en`. Only UI chrome is
translated; content (prompts, outputs, findings) is never touched. Language
switch is a link in the header, no page state is lost.

## 8. Security and data protection

Consequence of the decision "raw capture, no redaction":

- `events.jsonl` contains **unredacted** agent output — Bash outputs, file
  contents from `Read`, environment excerpts. If a secret is ever visible to an
  agent, it is on disk afterwards.
- Mitigations (all mandatory):
  1. `0600` on `events.jsonl` — same posture as `state.json`.
  2. `ensure_runs_gitignored()` before the first write; additionally an
     assertion at run start that `.adw/runs/.gitignore` exists and contains `*`.
     Fails → warning in the log and on stdout.
  3. GUI binds to loopback; non-loopback requires an explicit opt-in flag.
  4. `docs/` and README state clearly that run directories must not be shared.
- The GUI opens **no** file outside `.adw/runs/<run_id>/` and executes exactly
  one external program: `git diff` on the target repo, with
  `core.hooksPath=/dev/null` and `safe_env()` — same guards the orchestrator
  already uses.
- Read-only: the GUI process has no code path that writes to `state.json`, the
  repo or the event log.

## 9. Performance and limits

- Emit overhead target: < 1 ms per event in the typical case (lock + append of
  a few KB). A run produces roughly 10^3–10^4 events.
- Expected log size: single-digit MB for dry runs, tens of MB for real runs
  with verbose test output. No cap by decision — retention (§4.5) is the
  countermeasure.
- The reader keeps parsed events per run in an LRU cache keyed by byte offset;
  a tail re-read parses only what is new.
- Guard rail: on a log > 200 MB the GUI renders the trace lazily (children on
  demand) instead of eagerly building the whole tree.

## 10. Definition of Done

1. `uv run adw run --dry-run` produces a `events.jsonl` in which the complete
   control flow of all seven phases is reconstructible — verified by a test
   that walks the span tree and asserts phase order, lane parallelism and
   loop rounds.
2. Every event type from §4.4 is emitted at least once by the dry-run E2E test
   or by a targeted unit test.
3. Removing the GUI extra (`pip install adw` without `[gui]`) changes nothing
   about `adw run` — no import error, no missing dependency.
4. An artificially broken event log (unwritable path, disk-full simulation,
   corrupt line) never fails a run — regression test with an emitter that
   raises.
5. `_collect()` returns bit-identical results with and without instrumentation
   — regression test with a mock SDK stream.
6. GUI: run list, run detail, all four detail tabs, timeline, artifacts, raw and
   the SSE live stream tested against a fixture log (FastAPI `TestClient`).
7. Step diff between two snapshot refs shows the correct patch — test with a
   real temp repo.
8. `adw runs prune` keeps exactly N runs, removes their refs, `--gzip`
   round-trips through the reader.
9. Language switch de/en covers all UI labels; no untranslated key (test walks
   the dictionaries against each other).
10. `flake8` + `isort` + `pytest` green, `codex review --uncommitted` without
    open P1.

## 11. Implementation order

Each step is one TDD cycle, one commit, and independently useful:

1. `adw/events.py` — emitter, schema, locking, fail-open, `seq`. No callers yet.
2. Instrumentation of `agents.py` (deepest value: prompts, tool calls, cost).
3. Instrumentation of `gates.py`, `codex.py`, `state.py`.
4. Instrumentation of `phases.py` and `cli.py` (the ~40 call sites).
5. `adw/snapshots.py` + snapshot points in the build phase.
6. `adw/gui/reader.py` + `model.py` — events to span tree, tail-capable.
7. `adw/gui/registry.py` + auto-registration in `adw run`.
8. FastAPI app: run list + run detail with trace tree and detail pane.
9. SSE live stream.
10. Timeline, artifacts, raw tab.
11. Diff endpoint and diff tab.
12. i18n de/en.
13. `adw runs list` / `adw runs prune` + `trace:` config section.

After step 4 the log alone already carries real debugging value (`jq` on
`events.jsonl`) — the GUI is then pure upside.

## 12. Open points

- **Token/cost data in dry-run mode**: mocks produce no `usage`. The timeline
  shows durations only; cost fields stay `null`. Acceptable.
- **`resume` across processes**: the event log continues in the same file, `seq`
  keeps counting (the emitter reads the highest `seq` on open). A `run` start
  event with `resumed_from_seq` marks the seam.
- **Codex CLI transcripts**: `codex.py` currently only keeps the last answer.
  Whether the full tool transcript belongs in the log too is deferred to v1.1 —
  the Claude side is the bigger lever.
- **Excalidraw flowchart**: `docs/adw-flowchart.excalidraw` could serve as the
  actual graphic for the phase map instead of a CSS status bar. Deferred; the
  status bar comes first.
