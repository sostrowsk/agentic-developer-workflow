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
