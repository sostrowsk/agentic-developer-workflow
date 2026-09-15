# Changelog

All notable changes to the ADW orchestrator are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/) (0.x: minor = features,
patch = docs/fixes).

**Release process:** every push to `main` is a release — it gets a version
bump in `pyproject.toml`, an entry here, and a git tag `vX.Y.Z`
(`git push && git push --tags`). Versions up to 0.2.1 were assigned
retroactively from the push history; their tags point to the pushed states.

Deutsche Fassung: [CHANGELOG.de.md](CHANGELOG.de.md)

## [Unreleased]

### Changed
- **Run-detail live path: send only what is shown.** During a running run the
  debounced live refresh no longer ships the whole document. `GET /runs/{repo}/{run_id}`
  now honours the `X-Requested-With: fetch` header the client already sends and
  returns **only the two live regions** (`header.run-header`, `main.detail`) as a
  bare HTML fragment — same render path, no `<html>`/`<head>`/`<body>` wrapper.
  Without the header the response is the unchanged full document.
- **Detail panes on demand.** The delivered page carries the pane **body** only for
  the `?focus`-resolved node (before: up to 62 server-rendered panes, ~45% of the
  document, of which none is visible without `?focus`). Every other span pane is an
  empty shell whose body the client loads on selection — re-requesting the same page
  with that node's `?focus` and the fetch header — reusing the proven lazy-load
  safeguards (shared in-flight request, superseded/swapped-away answers write
  nothing, a loading state, a re-loadable failure hint). Without `?focus` the page no
  longer ships those bodies, so it is substantially smaller (the excluded span-pane
  bodies were ~45% of the reference document). **AC 5 caveat:** the structural
  reduction is proven by supplementary synthetic tests; the contractual reference
  measurement — run `16f39431` ≤ 551064 uncompressed bytes, ≥35% below the 847791-byte
  2026-09-15 reference — is a manual measurement that is **not reproduced in the test
  suite** (the run falls under retention) and is recorded there as **unverified**.

### Unchanged
- All `/api` routes (fields, types, values); no new route, no new query parameter.
- The 200 ms debounce and the SSE trigger, `Last-Event-ID` reconnect, the
  stream-close at run end, the `?focus` deep links and the `?tools_offset` window.

### Docs
- `docs/GUI-SPEC.md` §7.3 now describes the actual mechanism (debounced region swap,
  the fetch-header partial, the on-demand panes) instead of the never-built
  incremental tree patching; §7.2 A marks the run-list "live-updating" promise as
  **not yet implemented** (deferred).

## [0.25.0] — 2026-09-15

### Added
- **Run-detail page: operable without a mouse.** The main interaction —
  selecting a trace node and reading it — now has a keyboard path. The trace tree
  is a single tab stop with arrow-key navigation over the visible rows (Up/Down
  move, Right/Left open/close folds, Enter/Space select, Home/End jump to the
  first/last visible row); collapsed content is skipped and Space does not scroll
  the page. The whole timeline **row** (label, track and bar) becomes the operable
  unit — clickable, keyboard-focusable and activatable, reaching the same node as
  the up-to-6 px bar, including the `?focus` redirect for an out-of-window target.
  A uniform, clearly visible focus indicator (its own `--focus` token, never
  `--signal`, ≥ 3:1 in both themes) marks every focusable element. The announced
  tab pattern is fulfilled — `role="tab"`/`aria-selected`/`aria-controls` with
  Left/Right switching and a roving tab stop, adopting the server preselection —
  and the selected node is exposed machine-readably (`aria-selected`). Purely
  operational: what the page shows and the JSON API are unchanged.

### Fixed
- **The keyboard cursor only walks real tree rows, and Right only reaches real
  children.** `isNavigableRow` accepted every `li[data-seq]`, so the recovery card's
  informational `li.recovery-abort` entries — which own no pane — became
  `role="treeitem"` rows: they swallowed an Arrow-Down on the way past, and
  activating one fell back to the first node. Rows are now `.node[data-seq]` and the
  `.trace-wrap` collector hulls. Separately, Arrow-Right implemented "to the first
  child row" as one step through the stop order, which interleaves each row with its
  own inline action links — on a phase carrying a `raw-jump` link the cursor landed
  on that link. It now resolves the child row explicitly, by DOM containment for
  collector hulls and by greater `--depth` for phases, so an open but childless
  phase no longer hands the cursor to its next sibling.

## [0.24.0] — 2026-09-14

### Fixed
- **Timeline tracks share one origin and one width.** `.tl-bar-label` was
  `flex: 0 1 auto` with a `min-width`, so the label column sized itself from its
  text and pushed its own track sideways — equal timestamps no longer sat above
  each other and equal durations were no longer equally long. The column is now a
  fixed width (`flex: 0 0 7rem`, as `.tl-lane-label` already was); a long name
  wraps inside it and stays fully readable. The defect was latent: all 31 tracks of
  run `16f39431` happened to align because the longest bar label of every existing
  run is `codex.author` (12 chars), which fits inside the `min-width` — a longer
  name moved that row's track by 163 px, and a gate named `integration-tests` in
  `.adw/config.yaml` would have been enough to trigger it.

### Changed
- **Run-detail page: the work field comes first.** The trace tree now begins
  directly under the page head. "Planned tasks" and "Change scope" moved **below**
  the three-column work field (trace tree │ detail panes │ run context) and render
  **collapsed** — native `<details>` without `open`, no JavaScript, no persistence.
  Each collapse line states, without expanding, what it holds: "Planned tasks" the
  per-lane name, task count and state; "Change scope" the number of changed files
  over all observed lanes with the sums of added/removed lines (a binary file counts
  towards the file number but not the line sums; a run without a usable diff gets an
  explanatory line instead of a `0`, distinct from an available diff with no changed
  files). The trace-tree column was widened so it is at least as wide as the panes
  column; the context column stays the narrowest.
- **Timeline: geometry into the track, words beside it.** A timeline bar is now pure
  geometry — its `left`/`width` percent and its active/waiting/still-running state are
  unchanged — and each bar gets its own row with its name in a dedicated label beside
  its own track, so a short bar no longer clips its own name and one bar per row keeps
  the name-to-bar association unambiguous. The `title` attribute is preserved. Purely
  presentational — `GET /api/runs` and `GET /api/runs/{repo}/{run_id}` are unchanged
  in every field, type and value; the trace column still renders in full (one
  `data-tree-entry` per node, `?offset` inert), and the 200-marker cap continues to
  apply only to the Tools entries.

## [0.23.0] — 2026-09-13

### Fixed
- **Run metrics now cover the whole run, not just the last CLI span.** `_summary`
  sums `duration`, `cost` and `tokens` over **all** closed `run` spans. A gated run
  is several CLI invocations and thus several `run` spans in one log, so the earlier
  figures reported only the last section and were too low — e.g. a run whose last
  span cost `$0.00` was shown as `$0.00` although the whole run cost more. `start`
  still marks the first span; the status is still the last span's. This changes the
  meaning (not the name or type) of `duration`/`cost` on `GET /api/runs` and
  `GET /api/runs/{repo}/{run_id}`: an open span contributes nothing, and with no
  closed span the value stays empty — never a fabricated `0` (a genuine `0` from the
  payload is kept). The run list and the run-detail header feed from the same
  summary and therefore agree.

### Added
- **Three named time sizes, one vocabulary.** Work (the summed run-span durations),
  Phase time (the area of the coloured phase-band segments), Waiting (the gaps at
  the approval gates) and Total (first phase start to last phase end) are named
  consistently. The run-detail header now shows the real **Work** beside the
  **Phase time** — the phase-time number is no longer mislabelled "Work" (the two
  diverge 7–25× when a phase span outlives an interruption). New **additive**
  summary fields: `work_seconds`, `phase_seconds`, `wait_seconds`, `total_seconds`
  and `tokens` (`null` when undetermined; `phase_seconds + wait_seconds =
  total_seconds` up to rounding).
- **The run list answers its question.** The Issue column shows a one-line title
  derived from the raw issue text (first `#` heading in the first twelve lines,
  skipping an "Issue"-only heading; otherwise the first non-empty line; a leading
  `ADW-Issue:`/`Issue:` removed; over 90 chars cut to 89 + `…`), with the full raw
  text in the cell's `title`. Phase and Status collapse into one column — a finished
  run shows one word, a running/waiting run adds its phase. Server-side sorting
  (`?sort=start|duration|cost|events`, `?dir=asc|desc`) and filtering (`?repo=`,
  `?status=`) via query params, with `awaiting_approval` kept above every sort and a
  localized hint for an empty result. Unknown `sort`/`dir` fall back to
  `start`/`desc`; an unknown filter value yields an empty result, never the
  unfiltered list.

## [0.22.0] — 2026-09-13

### Changed
- **GUI design foundation: a named colour/typography/spacing token system.**
  `adw/gui/static/app.css` now takes every colour from custom properties defined
  in exactly two blocks — `:root` (light) and `@media (prefers-color-scheme:
  dark)`; outside them there is no hex literal. Font sizes come from a six-step
  scale and spacings from a six-step scale, radii are only 3px/6px. Machine values
  (ids, sequence numbers, paths, durations, costs, token/event counts, phase
  names) render in a system monospace stack; prose and labels keep the UI font.
- **Signal-colour discipline.** The former double role of one blue is resolved:
  "a human must act" (`--signal`), "working" (`--busy`) and "technically waiting"
  (`--wait`) are three distinct hues, and `--signal` is used for nothing else.
- **Dark mode via `prefers-color-scheme`** (no toggle, no client state), plus a
  `prefers-reduced-motion: reduce` rule that switches the (≤150 ms colour-only)
  transitions off.
- **One cost format everywhere.** The run-context panel's `cost_usd` now uses the
  shared `_fmt_cost` format (`$47.16`) in both the server render and the client
  projection, matching the run list and the Timeline header; the six-decimal form
  is gone.

### Added
- **The phase band is a to-scale timeline of the run.** In the run-detail header,
  each phase sits at its real temporal position with its real duration as width;
  the gaps at the approval gates show as their own "waiting" segments; three
  numbers — work, waiting, total — sit beneath the rail. A phase that never ran
  keeps no rail area but stays visible in the legend. When no phase has a parsable
  timestamp (or the span is not determinable), the header falls back to the
  today's chip row. `GET /api/runs/{repo}/{run_id}` gains `start` and `end` on
  each `phases` entry (additive; ISO-8601 or `null`).

## [0.21.4] — 2026-09-13

### Fixed
- **Self-heal restores ADW artifacts from HEAD, not from the index.**
  `git checkout -- <path>` restores the *staged* content, so a staged
  modification, deletion or rename of one of the six ADW artifacts survived the
  heal; the following status check still saw a dirty tree and the command
  refused instead of healing and proceeding. Now `git checkout HEAD -- <path>`
  (index and worktree, path-scoped); a path absent from HEAD has its index entry
  dropped before the file is removed. Follow-up of run `72a042ad`.
- **`has_trace` answers whether an event log exists**, not whether the reader
  accepted a record. An existing but empty or entirely malformed `events.jsonl`
  now reports `has_trace: true` with `event_count: 0` — a trace *with* reader
  problems, which the old `bool(events)` collapsed into "no trace at all".
  Follow-up of run `b6739174`. The same question is now answered the same way
  everywhere: the Timeline tab takes the presence bit too (it used to say "no
  event log" for a file the summary had just called a trace), and listability
  follows the log's existence rather than the parsed count — a crash that leaves
  a freshly created, still-empty log and no readable state no longer makes the
  run vanish from `/api/runs`. Both found by `codex review`.

### Added
- **`docs/FOLLOWUPS.md` — a consolidated follow-up register.** Every `[P*]`
  finding the ADW runs left behind in `.adw/runs/*/followups.md`, verified
  against the code as it stands and given a disposition. Of eleven findings two
  were already fixed, four were obsolete, four are fixed here or in 0.21.2/0.21.3,
  and three stay open with a stated reason. The run artifacts are historical
  records and were deliberately left untouched.
- **Two DoD tests that were owed.** The auto-prune fail-open path (a real prune
  error now proves exit 0, phase `done` and a visible message) and the exact
  `--older-than` day boundary (equality counts as old enough). Both were green on
  first run — the code was right, the proof was missing — and both were
  mutation-checked to confirm they bite.

### Changed
- **Per-task attribution in the trace is deliberately not built** and is now
  recorded as such in `docs/SPEC.md` §6 with its reasoning. The build phase
  dispatches a whole `## Workstream:` section to one agent, so the orchestrator
  never knows which individual task is running — `task_id` exists nowhere in
  `adw/`. The three possible implementations are dispatch-per-task (a build-phase
  redesign), agent self-reporting (agent-falsifiable) and post-hoc text matching
  (guesswork); all three are rejected, and the decision is written down so it is
  not re-litigated each session.

## [0.21.3] — 2026-09-13

### Fixed
- **A corrupt event payload no longer 5xx's the reads — everywhere, not just in
  the change scope.** 0.21.2 guarded two helpers; a sweep of the whole read
  surface found ten more unguarded payload accesses. Measured against a run
  whose every event carries a truthy non-mapping payload, three of the four read
  endpoints returned 500: the run **list** (`_summary` on `payload.totals`), the
  run **detail**, and the server-rendered detail **page**. The list was the worst
  of them — one corrupt run took every healthy run beside it off the home page.
  All ten now read through `_mapping_payload()`, including `_snapshot_refs`,
  whose failure turned the diff endpoint's *rejection* path into a 500.
- **Nested non-mapping values are covered too.** A payload that *is* a mapping
  but whose `totals` is not (`{"totals": ["nope"]}`) broke `_summary` and the
  timeline just the same. The new `_as_mapping()` is the single place that makes
  an arbitrary JSON value safe to `.get` on; `_mapping_payload()` is now defined
  in terms of it.
- **Span nodes were the blind spot.** `_node_status` and `_aggregate_outcome`
  read `node.end_payload` — a tree node's own field, not a raw event — and were
  missed by the first sweep. Found by `codex review`, which also showed why: the
  first version of the new fixture reused one span id for every event, so
  `build_tree()` collapsed them into a single node and no per-span end payload
  was ever exercised. The fixture now gives each span its own id and pairs
  start/end, plus an open span and a point event per type.

### Added
- `tests/test_gui_payload_robustness.py` pins the guarantee at endpoint level:
  every event type crossed with every kind and every non-mapping shape, in one
  run, against list, detail, page and events. Plus three tests in
  `tests/test_gui_diff_endpoint.py` for the allowlist.

## [0.21.2] — 2026-09-13

### Fixed
- **Run detail stays 200 when an event carries a non-mapping payload.**
  `_snapshots_by_lane` and `_observed_lanes` read the event payload with
  `(e.get("payload") or {}).get(...)`, bypassing the established
  `_mapping_payload()` guard. An event whose *entire* payload is a truthy
  non-mapping (list, string, number) raised `AttributeError` and turned an
  otherwise successful `GET /api/runs/{repo}/{run_id}` into a 5xx — against the
  robustness guarantee in `docs/GUI-SPEC.md`. Both helpers now use the guard;
  such events are silently ignored and healthy lanes, snapshots and diffs are
  unaffected. Built by ADW run `e4e70373`; four tests added to
  `tests/test_gui_change_scope.py`, among them a direct RED proof.

### Changed
- **The `before_push` breakpoint is switched off again.** It was enabled in
  0.21.1 to exercise the feature in a real run; that experiment is complete
  (see below), so `.adw/config.yaml` carries no `breakpoints:` key again and
  runs of this repo no longer hold before the push.

### Notes
- **First practical validation of the breakpoints from 0.16.0.** Run `e4e70373`
  held exactly once at `before_push`: the event log shows `awaited` at seq 301
  and `granted` at seq 305, and the phase spans place the hold between the end
  of `final_review` and the start of `ci` — precisely where the specification
  puts it. The run-start pinning from 0.16.3 was active throughout
  (`pinned_breakpoints: ["before_push"]` in the state from the very first save).

## [0.21.1] — 2026-09-07

### Changed
- **ADW-on-itself now holds before the push.** `.adw/config.yaml` activates the
  `before_push` breakpoint, so a run of this repo stops after the final review
  and before *any* CI work, and continues only on `adw approve <run_id>`. This
  is the first practical use of the breakpoints shipped in 0.16.0 — until now
  the feature was only covered by tests — and it exercises the run-start pinning
  from 0.16.3 against real data. Config only, no code change.

## [0.21.0] — 2026-09-04

### Added
- **Artifact rows name their file.** An `artifact` node rendered as the bare word
  `artifact`, so a run of them told the reader nothing — and the counted collector
  added in 0.19.0 made that worse, not better: `5× artifacts` collapsed correctly but
  expanded into five identical rows. The label is now `artifact <name>`, the same
  shape a tool call has, with the complete path in the `title`. Without a usable name
  the bare type name stays. The `label` in the API `tree` carries the same text — it
  is built once.

## [0.20.2] — 2026-09-03

### Fixed
- **The GUI's own assets are always revalidated.** `/static` sent `etag` and
  `last-modified` but no `Cache-Control`, so a browser applied heuristic freshness and
  reused its cached copy without asking the server: after 0.20.1 the served `app.css`
  carried the label-wrap fix, yet the page's stylesheet arrived with `transferSize: 0`
  and the fix stayed invisible — a restarted server did not help, and a hard reload in
  one tab did not help a newly opened one. Assets now send `Cache-Control: no-cache`,
  which keeps the cached copy but requires revalidation; with the existing ETag that
  costs one 304.

## [0.20.1] — 2026-09-03

### Fixed
- **Long tree labels no longer overflow the trace column.** A worktree path or a grep
  pattern has few break opportunities, and without an explicit one the label ran past
  the column edge and painted over the detail pane beside it (measured on run
  `16f39431`: 59 labels past the edge, the worst 282px into the panes column). The
  column already had `min-width: 0`; the labels now break like the `pre` blocks do.

## [0.20.0] — 2026-09-03

### Added
- **Markdown payload fields render as documents.** In a server-rendered detail pane a
  multi-line string — a run's issue, an agent's prompt, its answer, an assistant
  message — is passed through `markdown-it-py`: headings, lists, fenced code and
  tables instead of source text. Raw HTML is escaped and links render as literal text,
  so agent-generated text can neither inject markup nor become a click target; a
  unified prompt diff stays verbatim. The field-list scaffolding around a payload
  stays literal (CommonMark would fold the scalar lines into one paragraph); the
  shared lazy pane keeps the plain field list.

## [0.19.0] — 2026-09-02

### Changed
- **The pane's payload reads as text, not as a JSON dump.** It renders as an indented
  field list in which a multi-line string — a run's issue, an agent's prompt — keeps
  its real line breaks instead of arriving as one endless line of `\n` escapes.
  Server and client produce the same text, so a payload reads identically in a
  server-rendered pane and in the shared lazy one.
- **The repeat counter now covers writes and artifacts.** ≥ 2 adjacent
  target-identical `Write`/`Edit` calls collapse into one counted row, as do adjacent
  `artifact` events (which are each a different file, so they collapse by type rather
  than by target). Both are still *grouping* boundaries — they keep ending a
  read/search run. On run `7fe9d702` this takes the trace column from 702 to 642 rows
  and raises the folded events from 554 to 649.

## [0.18.0] — 2026-09-02

### Changed
- **The Trace tree column is no longer paged.** The left `section.trace` renders
  every node of a run at once — the bounded moving window (`?offset`, 100 nodes per
  page) and its navigation are gone. Compaction now spans the whole run instead of
  stopping at a page boundary, so an uninterrupted read/search run collapses into a
  single group. `?offset` is accepted and ignored, so a bookmarked URL from the paged
  era still renders the full tree. The Tools entries inside the detail panes keep
  their own window (`?tools_offset`), and the JSON `tree` is unchanged.
- **Point nodes no longer get a detail pane each.** Tool calls/results, messages and
  snapshots share one server-rendered pane shell that the client re-points and fills
  from the events route on selection; only span nodes (phase, lane, round,
  `agent.run`, gate, review) keep their own pane. Without this the unpaged column
  reinstated the DOM-node-count bottleneck the display bounds exist for: on run
  `7fe9d702` (1 296 events) ≈ 11 600 elements, now ≈ 6 400.
- **The pane's raw payload block is pretty-printed** — indented JSON over several
  lines instead of one unreadable line.

## [0.17.0] — 2026-09-02

### Added
- **Compacted trace-tree column — tool noise folded, not paginated.** In the run
  detail's Trace tab, the tree column now folds an `agent.tool.result` into its
  matching call (outcome + duration on the call row), collapses immediately
  repeated target-identical `Read`/`Grep`/`Glob` calls into a counted repeat node,
  and groups uninterrupted read/search runs into a collapsible group node (broken by
  any message, write, artefact, error or `Bash`/unknown tool). Paths render
  repo-relative with the full path in a `title`, the tree opens with phases
  collapsed (open: the first-error phase, else the last-started), and a per-page line
  balance shows rows vs. events folded. Pure page-local presentation: the
  `GET /api/runs/{repo}/{run_id}` `tree` and the paging window are unchanged.

## [0.16.3] — 2026-09-02

### Fixed
- **The breakpoint set is pinned when a run starts** (follow-up [P2] of run
  `f4942ef3`). `_config_for_continuation()` reloaded `.adw/config.yaml` on every
  `resume`/`approve` while the state kept no snapshot of the enabled
  breakpoints, so editing the file mid-run added or removed a *future* hold —
  which the specification forbids. The effective set is now pinned in the new
  state field `pinned_breakpoints` (like `pinned_base_branch`) and every
  continuation holds at exactly those points. An empty pin means "no
  breakpoints", not "unpinned"; a state persisted before the field existed
  reads as `null` and keeps following the config.

## [0.16.2] — 2026-09-01

### Added
- **Handout on the ADW flow, walked through a real run**
  ([`docs/adw-flow-handout.de.md`](docs/adw-flow-handout.de.md), German only).
  Ten chapters from the overall flow through the phases of run `b65f5d75` to the
  telemetry model, worktree isolation and crash safety, with figures taken from
  that run's event log (wall clock, cost, tokens, event distribution).

## [0.16.1] — 2026-08-29

### Added
- **Release notes for the Run Inspector series 0.9.0 – 0.16.0**
  ([`docs/RELEASE-NOTES.md`](docs/RELEASE-NOTES.md), German:
  [`docs/RELEASE-NOTES.de.md`](docs/RELEASE-NOTES.de.md)). The changelog records
  what changed; the release notes frame the eight releases and give the
  reasoning behind the non-obvious decisions — why the plan skeleton parses no
  identifier pattern, why the change footprint passes no scope verdict, why the
  recovery card keys off `state.phase` rather than the escalation event, and why
  the context derivation is a single prefix pass. Includes the known limitation
  of 0.16.0 and the CI state of each release.

## [0.16.0] — 2026-08-27

### Added
- **Configurable breakpoints as a generalized approval.** A new optional
  `breakpoints:` list in `.adw/config.yaml` activates up to two holds before the
  expensive, hard-to-reverse steps: `before_integration` (after all build lanes
  are green, before integration/merge or review work) and `before_push` (after
  the final review, before any push/CI work). A run pauses at an active
  breakpoint exactly as at the existing spec/plan approval gates — persisted
  phase `awaiting_approval`, process exit code 2, continued with
  `adw approve <run_id> --repo <path>`. Which breakpoint waits is recorded in a
  new state field `pending_breakpoint`, not a new phase value; the `Phase` set,
  the phase bar and retention are unchanged. Each breakpoint is logged as an
  `approval` event (`gate` = breakpoint name, `event` = awaited/granted) so the
  GUI and timeline render it with no special case, and holds are idempotent
  across crash + `resume`. `--no-approval` (also via `--gates none`) skips the
  breakpoints too — one switch for "no human approval in this run". Default (no
  key or empty list): today's behavior, unchanged.

## [0.15.0] — 2026-08-26

### Added
- **Change scope of a run in the Run Inspector.** The run detail now shows, side by
  side, which files a run actually changed — grouped per lane, with `+/-` counts per
  file — and the scope the contract declares, as it stands. The file lists come from
  the existing snapshot/diff logic: per observed lane exactly one comparison between
  its first and last snapshot (`refs/adw/<run_id>/<seq>`), with a binary file shown
  as "not numerically available"; no new git operation and the diff route is
  unchanged. The declared scope is a readable, semantically equivalent YAML rendering
  of the contract's top-level `x-adw-*` blocks, read through the existing whitelist
  artifact path with the already-present `yaml` module; a missing, unreadable,
  non-mapping or `x-adw-`-less `contract.yaml` shows a clear "no declared scope"
  instead. **No automatic judgement is made** — no file is marked "in scope" or "out
  of scope"; the facts sit side by side and a human evaluates them. Robust against
  missing data: a lane with no usable snapshot pair shows "no diff available", and a
  run with no usable diff at all drops the table with a clear statement — never a 5xx,
  never an empty table without explanation. Observable as an additive, derived
  `change_scope` object (`lanes` + `declared_scope`) on
  `GET /api/runs/{repo}/{run_id}`; all existing response fields are unchanged
  (additive, read-only).

## [0.14.0] — 2026-08-26

### Added
- **Plan skeleton in the trace view of the Run Inspector.** When a run's `plan.md`
  is present, the run detail now derives, per `## Workstream:` section, a read-only
  list of its planned tasks (every `###` heading, text taken verbatim) and shows it
  beside/above that lane's trace — so "planned" (skeleton) and "done" (trace) sit in
  one view. The parser follows exactly two rules (a section is `## Workstream:
  <name>` up to the next `##` heading; a task is every `### ` line), with no
  identifier pattern and no Markdown dependency, so the heterogeneous heading forms
  across runs are all kept. Each list carries a coarse lane-level status: `done` once
  the matching lane ends with `completed: true`, otherwise `pending` (including a
  not-yet-started lane, shown without inventing a trace node). `plan.md` is read only
  through the existing whitelist artifact path; a missing, empty, unreadable or
  unmatching plan yields no skeleton (no empty box, no change to existing behavior).
  Observable as an additive, derived `plan_skeleton` array on
  `GET /api/runs/{repo}/{run_id}`; chrome labels are bilingual (`adw/gui/i18n.py`),
  the task texts are content and are not translated (GUI-SPEC §7.2).

## [0.13.0] — 2026-08-26

### Added
- **Recovery card at the causing node in the Run Inspector.** When a run needs a
  human step, the run detail now derives one recovery card that names the single
  next command as copyable, POSIX-shell-safe text — with the real repository path
  from the registry and the real `run_id` (never the URL slug). The command is
  chosen strictly from `state.phase`: an approval-gate pause → `adw approve`, an
  aborted/crashed work phase → `adw resume`, a finally escalated run → no
  continuation command but the clear hint that a NEW run is required. In the
  escalation case the card is anchored at the governing `escalation` node and shows
  the reason, the affected phase and the immediately preceding
  `limit.hit`/`circuit_breaker` events, and it links to `escalation.md` in the
  Artifacts tab rather than duplicating its content. The GUI stays strictly
  read-only: the command is displayed, never executed. Card labels are bilingual
  (`adw/gui/i18n.py`); the command line, event values, `run_id` and repo path are
  not translated. Observable as an additive, derived `recovery` object on
  `GET /api/runs/{repo}/{run_id}` (GUI-SPEC §7.2).

## [0.12.0] — 2026-08-26

### Added
- **Node → Raw-log jump and prompt diff in the Run Inspector.** Every span node in
  the trace tree now offers a jump into the existing Raw tab pre-filtered to the
  node's exposed `[seq, end_seq]` subtree range, so the raw events of one subtree
  are found without hand-searching for seq bounds. The Raw tab gained an inclusive
  seq-range filter (`raw_from_seq`/`raw_to_seq`, each optional/one-sided) composed
  server-side with the existing `raw_q`/`raw_type`/`limit`; `total` stays the
  pre-window match count and `types` stays the full log type set. A non-numeric
  bound is inactive and an inverted range is a defined empty set — never a 5xx. An
  active range is shown with its bounds and cleared in isolation (keeping
  `raw_q`/`raw_type`/`limit`). The `agent.run` **Prompt** tab additionally shows a
  unified diff of its prompt against the previous run of the same agent in the same
  lane within this run (predecessor chosen structurally by agent + lane + greatest
  smaller `seq`); `GET /api/runs/{repo}/{run_id}` carries additive derived
  `prompt_diff`/`previous_prompt_seq` fields on `agent.run` nodes, distinguishing
  "no predecessor" (both null) from "identical prompt" (`""` with the seq set). The
  diff is produced with the standard-library `difflib` only. The read-only
  `…/events` route is unchanged (still only `from_seq`/`to_seq`).

## [0.11.0] — 2026-08-26

### Added
- **Run-context panel in the Run Inspector.** Beside the run-detail pane a
  read-only field list shows the run state **at the seq of the selected node** —
  `phase`, the enclosing `round` (`{loop, n, cap}`), the number of `limit.hit` and
  `circuit_breaker` events so far, the cumulative `cost_usd` and the number of
  `followup` entries — so you can see *why* a node went the way it did without
  clicking up and down the tree or switching to Raw. It is a purely derived
  projection of the events the detail response already loads: `GET /api/runs/{repo}/{run_id}`
  now carries a six-field `context` on every trace node and a top-level
  `latest_context`. A node's cutoff is its own `seq` (point) or its `end_seq`
  (span, the subtree maximum), and only events at or before the cutoff count, so
  selecting a node is time travel; with nothing selected the panel shows
  `latest_context` (the live view). Every absent datum is empty — `null`, never a
  fabricated `0` — and a run without a trace yields only a `latest_context` with
  all six fields null, never an error. No new event, reader, route, persistence,
  runtime dependency or SSE change; `state.saved` is unchanged.

## [0.10.0] — 2026-08-26

### Added
- **Dry runs are unmistakable in the Run Inspector.** A dry run (derived purely
  from the existing `dry_run` field in the `run` start payload — no new event,
  route or persistence) carries a short `Dry-Run` label on its run-list row and a
  persistent `Dry-Run` banner in the run-detail header that stays pinned to the
  top of the viewport (sticky header) while the trace tree scrolls, so a
  content-thin simulation is never mistaken for a real run. The `dry_run` boolean
  now also appears on the run record of `GET /api/runs` and
  `GET /api/runs/{repo}/{run_id}`; a missing field or missing `run` span reads as
  `false`.

### Changed
- **The run list groups by status priority.** Runs are ordered
  `awaiting_approval` first, then `running`, then the rest (previously only
  `running` was pulled to the front, so a run awaiting a human sank below newer
  finished runs). Within each group the newest-first order is unchanged.

## [0.9.0] — 2026-08-26

### Added
- **The Run Inspector tells "working" from "waiting" from "waiting on a human".**
  Three situations that used to look identical are now distinct, derived purely
  from the existing event log (no new events, routes or persistence):
  - The trace tree gives an open `ci.wait` / `gate` span the status `waiting`
    (idle CI polling or gate runtime) instead of `running`; the same span the
    Timeline already draws as waiting now agrees in the tree. A closed
    `gate`/`ci.wait` span keeps its result (`passed`/`failed`, else `done`).
  - A run paused at an approval gate reports `awaiting_approval` — not
    `running` — in `GET /api/runs`, `GET /api/runs/{repo}/{run_id}`, the run list
    and the run-detail header, even while its `run` span is still open. It is
    derived from the latest `approval` event (`awaited` without a later
    `granted`); a run without a trace falls back to its state phase. A closed
    `run` span keeps its terminal end-payload status untouched.
  - The phase bar shows the waiting business phase (`spec` or `plan`) as
    `awaiting` instead of `active`.
  - `awaiting_approval` — the only state that needs a person to act — is
    emphasised the strongest; new CSS and EN/DE labels are additive, the JSON
    status values stay language-neutral.

## [0.8.0] — 2026-08-20

### Added
- **`adw runs list` and `adw runs prune`** make run retention operable. `list`
  shows run id, phase, date, event count and log size, so it is visible when
  pruning is due. `prune [--keep N] [--older-than DAYS] [--gzip]` keeps the 20
  newest runs by default and works oldest-first.
  - Deleting a run removes its directory, its snapshot refs
    (`refs/adw/<run_id>/*`) **and its registered git worktrees** — the latter
    through git's worktree management rather than a plain `rmtree`, so no orphaned
    registration is left behind and the lane branch survives. This matters: 96 %
    of this repo's 595 MB of run data lives in those worktrees, so a prune that
    skipped them would reclaim about 3 %.
  - **Nothing is ever force-removed.** A run that is not `done` or `escalated` is
    never pruned — its state is what makes it resumable. A run with uncommitted
    changes in *any* of its worktrees is skipped whole, and every skipped run is
    named rather than silently passed over.
  - `--gzip` is the *keeping* form: it compresses `events.jsonl` and leaves the
    run directory, state, worktrees and snapshot refs intact, so the run stays
    fully browsable including its Diff tab. Compression is atomic (temp file plus
    rename) and the `.gz` inherits the log's 0600 permissions — the event log
    holds unredacted prompts and tool output.
  - Deletion runs in a fixed, resumable order (worktrees, then refs, then
    directory). A refused worktree removal keeps what it achieved, names it,
    lets the sweep carry on with the remaining safe candidates, and ends the
    command nonzero; a failure while removing refs or the directory stops the
    sweep and reports the achieved state, so a later prune can continue.
- **`trace:` config block**: `enabled` (default `true`) switches the event log
  off entirely, `keep_runs` (default `20`, `0` disables) drives automatic pruning
  after a successful run. Auto-pruning is fail-open — it never changes the
  finished run's phase or exit code — but reports what it did or failed to do.
- **The reader handles `events.jsonl.gz` transparently**, so a compressed run
  yields the same events, order and `seq` values as its uncompressed original.
- **GUI in German and English.** Language is chosen per request as `?lang=` →
  cookie → `Accept-Language` → `en`; the default stays English. Only chrome is
  translated — prompts, agent output, findings, artifact bodies and gate output
  are byte-identical in both languages. The header carries a switch that keeps
  the page state carried in the URL — the paged window and a focused node. Hints the
  client injects (loading, empty diff, load failures) are delivered with the page,
  so a German page shows no English leftovers.

## [0.7.0] — 2026-08-17

### Added
- **`adw run --gates none|spec|plan|both`** — one speaking switch over the
  approval gates. `none` runs fully autonomously: nothing halts the run except
  an escalation. `spec` halts after the spec, before the plan; `plan` halts
  before the build; `both` halts at both gates. The effective mode is printed at
  start, so an unattended run is never a guess.
  - The 4-way matrix was already reachable through the two legacy booleans, but
    not discoverable: `--no-approval` reads like "needs no approval" rather than
    "runs autonomously", and "halt only at the spec gate" required the
    counter-intuitive pair `--no-approval --spec-approval`, which nobody had ever
    used in 19 runs.
  - The legacy flags stay valid and equivalent — `--no-approval` == `--gates
    none`, `--spec-approval` == `--gates both`, both together == `--gates spec` —
    so existing scripts and habits keep working unchanged.
  - A contradiction between `--gates` and a legacy flag is rejected before the
    run is created, order-independently and with no silent precedence; a
    redundant but consistent combination is accepted. An invalid value is
    rejected naming the four permitted ones.
  - The default is unchanged: `adw run` without flags still halts at the plan
    gate only. Each mode maps onto the two state fields the mechanism already
    consumes, so run states written by earlier versions stay resumable.

### Fixed
- CI is deterministic about coloured output (`NO_COLOR`), and the help-text test
  strips ANSI escapes before asserting. Rich colours option names and splits
  them across style segments — the first `-` becomes its own segment — so a raw
  `--gates` does not survive as a substring once colouring is on. Rich colours
  under `GITHUB_ACTIONS` but not for non-terminal output locally, which made a
  test pass locally and fail in CI.

## [0.6.0] — 2026-08-17

### Added
- **Bounded entry-node budget in the run inspector.** The measured bottleneck
  behind the "reaction ≤ 2 s" promise was the *number* of DOM entry nodes, not
  their contents (run `bf831719` stalled past 40 s). Both entry collections —
  the Trace tree and the Tools tab — now render through a global budget of at
  most **200 entries per collection**, independent of the run's total size, and
  the bound holds throughout navigation rather than only on the initial render.
  Each rendered entry carries a machine-readable marker (`data-tree-entry`,
  `data-tool-entry`) so the count can be asserted rather than eyeballed.
- **A moving window keeps every entry reachable.** The `offset` (tree) and
  `tools_offset` + `focus` (tools) query parameters slide the bounded slice via
  `← previous` / `more →`, so reaching a late entry never re-materialises the
  ones before it. The two windows page independently, and the window in effect
  survives the live region swap — the refresh re-fetches the page the user is
  actually looking at, not the server's default first window.
- **Latest-interaction-wins (supersession).** A superseded interaction writes
  nothing into the DOM, records no end mark and creates no measure; marks
  belonging to different selections are never paired. Two fast clicks leave the
  detail pane on the node clicked last.
- **Third response-time measure `adw:artifact`**, built the same way as
  `adw:select` and `adw:tab` (start mark at the opening input event, end mark
  in a task scheduled from within a `requestAnimationFrame` callback so it runs
  after the paint). Opening a large artifact inserts only a bounded initial
  slice; the full content stays reachable through the artifacts route.
- **Timeline bar-click navigation**: clicking a bar switches to the Trace tab
  and selects the corresponding node, which carries that node's `data-seq`.
- **A dependency-free JS test harness** (`tests/gui_js_harness.js` / `.py`) that
  drives the *served* `app.js` in a plain `node` process with stubbed DOM,
  `fetch`, `performance` and task scheduling. It is a test-time tool only, never
  a runtime dependency, and it is not a browser — no Playwright, no Selenium. A
  missing `node` runtime fails the tests rather than skipping them.
- `docs/gui-response-time.md` documents the marker selectors, the moving window
  and the manual measurement procedure.

## [0.5.1] — 2026-08-14

### Fixed
- `ONBOARDING.md` is git-ignored. The file is the per-session handover written
  by the `offboarding` skill and has never been committed; since the working-tree
  check from 0.5.0 went live it counted as a foreign uncommitted file and
  refused every `adw run`, `adw resume` and `adw approve`.
- Corrected the release date of 0.5.0 in this file (it was released on
  2026-08-14, not 2026-08-12).

## [0.5.0] — 2026-08-14

Catch-up release: the GUI work (runs 1–5b) reached `main` over several pushes
without its own version bumps. This entry covers everything since 0.4.0.

### Added
- **ADW Run Inspector (`adw gui`)** — a read-only web view of a run. Binds to
  loopback only unless `--i-know` is passed; `--repo` adds repos beyond the
  registry, `--port` (default 8765) and `--open` control the local address.
  The web stack is an optional extra (`pip install adw[gui]`) and stays out of
  the core dependencies: a plain `adw run` install never imports it.
  - Run list and run detail with the tabs **Trace**, **Timeline**,
    **Artifacts** and **Raw**; the detail pane shows Prompt, Answer, Tools and
    Diff for the selected node.
  - Diff endpoint backed by an explicit ref allowlist.
  - Live tail over Server-Sent Events while a run is in progress.
- **Event log** (`adw/events.py`): the orchestrator appends its run events as
  JSON Lines to `.adw/runs/<id>/events.jsonl`. The emitter is **fail-open** —
  no emitter-internal error (disk full, permissions, unserializable payload)
  ever reaches the caller or aborts a run; `state.json` remains the resume
  authority.
- **Git snapshots** (`adw/snapshots.py`): the tree before and after every agent
  run is captured under `refs/adw/<run>/<seq>`, which is what makes the GUI's
  per-node diff possible without keeping working copies around.
- **Orchestrator instrumentation**: spans at the call sites for run, phase,
  round, agent run, tool use, gate and codex steps — mock and real runner
  alike, so a dry run produces the same trace shape.
- **`codex.timeout`** as an optional key in `.adw/config.yaml` (integer
  seconds, > 0, default 900). It applies to the `codex exec` subprocesses;
  without the key the effective limit is unchanged. Invalid values are
  rejected as a `ConfigError` before the run starts.
- **Working-tree check before `adw run`, `adw resume` and `adw approve`**: if
  the only uncommitted changes are ADW's own six authoring artifacts
  (`.adw/issue.md`, `spec.md`, `plan.md`, `contract.yaml`, `spec-summary.md`,
  `plan-summary.md`), ADW resets them itself and continues. Any foreign file —
  or a mix of foreign file and ADW artifact — refuses the command instead,
  discarding nothing. Documented in the user handbook (EN + DE).
- Specification and measurement docs: `docs/GUI-SPEC.md` (+ `.de.md`) and
  `docs/gui-response-time.md`.

### Fixed
- A failing **Codex author** in dual authoring no longer aborts the run. The
  `FAILED` marker is written as before and the phase continues single-source
  with the remaining Claude draft — no traceback, no exit 1, no manual
  recovery. A Codex timeout previously crashed the orchestrator and required
  cleaning the working tree by hand.
- The working-tree check **never escalates a run** any more — neither on `run`
  nor on `resume`. It refuses at most, leaving the run state unchanged and
  resumable. Previously a dirty `.adw/spec.md` left behind by ADW's own crash
  escalated the run permanently on resume and lost it.
- A **partial synthesis failure** (one required artifact missing or empty) is
  now repaired by exactly one retry of the same step over the existing
  session, naming the missing artifact. Only if the retry fails as well does
  the run escalate. Previously a written `spec.md` plus a missing
  `spec-summary.md` killed the whole phase.
- The **agent session id is persisted as soon as it appears** in the message
  stream instead of after the run completes, so an abort mid-run leaves it in
  the state and `adw resume` reconnects to the started session instead of
  restarting it and losing the tokens already spent.
- `test_measurement_guide_document_is_present_and_complete` checked only the
  first candidate document, so any unrelated Markdown file mentioning the two
  measure names could fail it; it now requires that *some* document is
  complete.

## [0.4.0] — 2026-08-07

### Added
- **RED gate in the build phase**: a Gate can be marked `tdd: true` in
  `.adw/config.yaml`. A Lane with at least one marked Gate runs its initial
  build in two stages — an agent pass instructed to write only tests ("write
  ONLY the tests, no production code"), then the orchestrator itself runs
  exactly the marked Gates. At least one red is the RED proof (`red_confirmed` plus the test
  paths persisted in the Lane state); the implementation continues in the
  **same session** with the shortened red Gate output and flows into the
  existing Gate loop. All marked Gates green after the test-only pass
  escalates instead of looping: the tests do not cover the required
  behavior, or it already exists.
- Forgery protection around the proof: a test-only pass that deletes files
  or leaves the Worktree untouched escalates, and green Gates count only
  while the tests that proved RED are still in place.
- Dry run covers both paths at 0 tokens — the default config (no `tdd`
  Gate) stays single-stage, a `tdd` Gate walks the full RED path through
  the CLI.

### Changed
- The RED check consumes no Gate iteration; all limits and the circuit
  breaker are unchanged. Fix dispatches from the review/E2E phases
  (`pending_task` set) and Lanes without a marked Gate behave exactly as
  before. `red_confirmed` survives crash + resume: once the test pass is
  checkpointed, a crash before the RED check repeats only the check.
- Docs (SPEC, user handbook, control-flow handbook, technical spec, EN+DE
  incl. HTML/DOCX exports) describe the RED stage.

## [0.3.0] — 2026-08-03

### Added
- **Dual authoring with best-of synthesis** for the spec and plan phases:
  Claude Opus (`spec_agent`/`plan_agent`) and Codex (`CodexRunner.author()`,
  read-only sandbox, marker-block output with per-call nonce) write two
  independent drafts **in parallel** to `.adw/runs/<id>/drafts/`; a Fable
  synthesis agent (`spec_synthesis`/`plan_synthesis`) merges them into the
  best-of artifact and additionally writes a gate summary
  (`spec-summary.md`/`plan-summary.md`) that is archived and shown at the
  approval gates. The synthesis is the first run of the existing Codex
  review loop — policy v2, round cap, circuit breaker and crash resume are
  unchanged.
- Codex draft failures **degrade** instead of escalating: warning +
  `<kind>.codex.FAILED` marker, the synthesis proceeds single-source; a
  missing Claude draft still escalates. The draft stage is idempotent over
  files (a resume never re-runs a finished author).
- Dry run covers the new control flow completely (distinct draft fixtures
  per author, drafts + summaries in the run folder, 0 tokens).
- This changelog, including retroactive versions for all pushed states.

### Changed
- Draft authors moved from Fable to Opus; the shared authoring content
  rules now live in one place (`adw/agents.py`) and are imported by the
  Codex author prompts — no drift between the two authors' standards.
- `CodexReviewer` protocol renamed to `CodexClient` (review + author).
- Docs (SPEC, user handbook, control-flow handbook, technical spec, EN+DE
  incl. HTML/DOCX exports) updated to the dual-authoring flow.

## [0.2.1] — 2026-07-30

### Changed
- HTML and DOCX exports of the handbooks/spec updated to review-loop
  policy v2.

## [0.2.0] — 2026-07-30

### Added
- **Review-loop policy v2**: descending severity floor per round (R1 all,
  R2 P1+P2, R3+ P1 only), findings memory with dispositions passed back to
  Codex from round 2 on, hard cap of 5 rounds, remaining findings recorded
  as known limitations.
- Authoring hardening: proportionality counterweight in the authoring
  prompts (A1–A3), round cap in the authoring loop, `--spec-approval` gate
  (stop after spec, before plan), issue text as review reference
  `.adw/issue.md` (B1–B3).
- Process requirements (commit messages, branch topology, git history) are
  banned from specs; pure-P3 idle fix runs are deferred to the follow-up
  report instead of escalating (A4).

## [0.1.8] — 2026-07-21

### Changed
- Prompts and docstrings consistently English (comments stay German).

## [0.1.7] — 2026-07-18

### Changed
- Bilingual documentation, part 3: remaining docs split into EN + DE
  editions.

## [0.1.6] — 2026-07-18

### Changed
- Bilingual documentation, part 2 (handbooks, technical spec).

## [0.1.5] — 2026-07-18

### Changed
- Bilingual documentation, part 1 (README, SPEC).

## [0.1.4] — 2026-07-18

### Fixed
- Triage no longer loses findings: lane labels are treated tolerantly.

## [0.1.3] — 2026-07-18

### Added
- Control-flow handbook; DOCX/MD exports of the documentation.

## [0.1.2] — 2026-07-15

### Added
- MIT license.

## [0.1.1] — 2026-07-15

### Changed
- README points to the Claude skill (separate repo
  `agentic-developer-workflow-skill`).

## [0.1.0] — 2026-07-15

Initial release.

### Added
- 7-phase orchestrator: spec → plan+contract → build lanes → integration/E2E
  → Codex code review → final review → push/CI. Control flow is
  deterministic code; agents provide judgment only.
- `adw` CLI with `run`/`resume`/`approve`/`status`, plan-approval gate,
  resumable state (atomic persistence, repo lock, crash checkpoints) and a
  token-free `--dry-run` mode.
- Hardened Claude Agent SDK runner (env whitelist, secret-store denies,
  sandboxed bash, artifact-exact write rules) and Codex reviewer as an
  isolated read-only subprocess with strict findings parsing.
- Lane worktrees with deterministic ports, gate runner with timeouts and
  process-group cleanup, triage rules, iteration limits, circuit breakers.
- GitLab (glab) and GitHub (gh) support for issues and CI monitoring.
- README, user handbook, technical spec (HTML handouts), example config;
  ADW packaged as a Claude skill (extracted to its own repo).

[0.25.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.21.4...v0.22.0
[0.21.4]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.21.3...v0.21.4
[0.21.3]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.21.2...v0.21.3
[0.21.2]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.21.1...v0.21.2
[0.21.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.21.0...v0.21.1
[0.21.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.20.2...v0.21.0
[0.20.2]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.20.1...v0.20.2
[0.20.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.20.0...v0.20.1
[0.20.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.3...v0.17.0
[0.16.3]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.2...v0.16.3
[0.16.2]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.1...v0.16.2
[0.16.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.8...v0.2.0
[0.1.8]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sostrowsk/agentic-developer-workflow/releases/tag/v0.1.0
