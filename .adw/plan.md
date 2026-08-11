# Plan — Timeline tab, Artifacts tab, measurable response time

Authoritative on conflict: `docs/GUI-SPEC.md`, especially §7.2 (views), §7.4
(API), §8 (security), §9 (performance), §12 (open points). This plan implements
`.adw/spec.md` and builds strictly against `.adw/contract.yaml`. **Single-lane:**
there is exactly one workstream, **backend** — the GUI web layer
(`adw/gui/app.py`, the Jinja2 templates, and the packaged vanilla assets
`static/app.css`, `static/app.js`) belongs wholly to this one lane; no separate
frontend lane exists. This run adds the two remaining run-level tabs of §7.2
(`Timeline`, `Artifacts`, GUI-SPEC §11 step 10) and makes the "reaction ≤ 2 s"
promise checkable through pinned `performance` measures. It completes an existing
view; it is not a redesign. The mechanism for bounded DOM rendering (windowing,
paging, incremental loading) and the timeline's concrete drawing (CSS/SVG) remain
implementation choices and are not pinned.

**Review descope (Codex round 1, resolved in favor of the issue):** task C
instruments exactly the two interactions the issue names — node selection and
tab switch. The earlier draft's third measure (`adw:artifact`), the
latest-interaction-wins supersession protocol with its rapid-reselection check,
and the fixed 2 MB / 20,000-line fixture cardinality are removed as beyond the
issue's scope. Bounded artifact rendering (B7) is validated with a
proportionate generated large-input test instead of a prescribed size.

## Guardrails

- **Web layer only.** Changes touch exclusively `adw/gui/app.py`, the templates
  (`adw/gui/templates/*.html`), the own assets (`adw/gui/static/*`), a new short
  guide document in the repo, and the associated tests/fixtures. `adw/events.py`,
  `adw/snapshots.py`, `adw/gui/reader.py`, `adw/gui/model.py` and the orchestrator
  stay **unchanged** (issue non-goals, E1, E2). Orphan spans and the `run`-span
  boundary stay as they are; any repair is on read, in the finished model. If a
  task turns out not to be solvable without such a change, that is a **finding
  for the report**, not a silent scope widening.
- **Strictly read-only.** No GUI code path writes to `state.json`, the repo or
  the event log; no write HTTP route. Run data is read only below the resolved
  `.adw/runs/<run_id>/` directory, under the same containment / `RUN_ID_RE` /
  slug backstops as today, applied before any run is read.
- **One new HTTP route only** — the read-only artifact-content route task B
  strictly requires (GUI-SPEC §7.4). No other new route.
- **No new runtime dependency, no third-party frontend asset** (E5): vanilla JS,
  own CSS, system fonts, inline-generated SVG where needed; no CDN, no node
  toolchain, **no charting library, no Markdown library** (E10), no
  browser-automation tool (E5, C4). The web stack stays the optional extra
  `adw[gui]` (E7).
- **The UI is English throughout** until i18n (E9).
- **E8 applies to the display, not the log.** Collapsing / paging / bounded
  initial rendering in the display is explicitly permitted and is **not** a
  violation of "no payload truncation" (that concerns the log). A finding that
  reads B7 or C as such a violation is rejected with a reason. Every logged
  payload and every whitelisted artifact's full content stays reachable.
- **Missing values are treated as missing**, never reinterpreted as a displayed
  `0` or `null` (A4, GUI-SPEC §12: cost/tokens under a dry run render empty).
- **The Deferred valve binds the review loop too.** A finding that asks to
  re-introduce a deferred or pre-decided point (Markdown library, i18n, run
  management, browser automation, emitter/model change, rendering `*.FAILED`
  content) is rejected with the documented rationale, not implemented.
- Real gates (E3): `uv run ruff check .` and `uv run pytest -x -q`. `flake8`,
  `isort`, `black` appear nowhere — not in dependencies, config, scripts or
  validation commands.

## Starting point (verified in the code)

- **Run-level tabs (`run_detail.html:210-246`).** The run detail view already
  has switchable run-level tabs **Trace** (default) and **Raw** (`data-tab`
  buttons + `data-tab-panel` sections inside `.run-tabs[data-tabs]`). `Trace`
  carries the trace tree and the per-node detail panes; `Raw` carries the
  filterable event list. This run adds **Timeline** and **Artifacts** between
  them so the set becomes `Trace` (default) · `Timeline` · `Artifacts` · `Raw`;
  `Trace` stays the default and the existing `Trace`/`Raw` views are unchanged.
- **Detail delivery (`app.py`).** `_run_detail` builds `run`/`phases`/`tree`/
  `problems`/`raw` from the read layer; `require_run` enforces `RUN_ID_RE`, slug
  resolution and symlink containment (`_contained`, `_runs_root`) and answers 404
  on the error cases; `_read_events` loads the run's events. The Timeline derives
  its lanes and bars from these **same** already-loaded events — no new reader,
  no change to `model.py`.
- **Tab machinery (`app.js:72-91`).** Tab switching is delegated on `document`,
  toggles `active` classes only, and already scopes to the nearest `[data-tabs]`
  so nested groups (the `agent.run` tabs inside the run-level tabs) are left
  untouched. Node selection (`applySelection`) is a pure `data-seq` match between
  a tree `.node` and its `.pane`. Timeline bar-click reuses this selection path.
- **Containment discipline (`app.py`).** The diff/events routes resolve every
  path through `_contained(path, runs_root)`, which returns the real path only if
  it stays within the resolved runs tree after resolving **every** symlink, and
  reject escaping symlinks with 404. The new artifact route reuses exactly this
  discipline (B5).
- **Fixtures/tests.** `tests/gui_app_helpers.py` provides the builders (`rec`,
  `write_run`, `write_state_only_run`, `run_start_payload`, `run_end_payload`, …),
  the `home` fixture, and helpers that keep large fixtures **generated in-memory**
  rather than checked in as bulky artifacts. The app is exercised via
  `create_app(repos=…)` with FastAPI's `TestClient`. New tests build on these.

## Workstream: backend

### 1. Build the run-directory and large-artifact fixtures

- Extend the GUI test-fixture support with deterministic data for Definition of
  Done §3, keeping large fixtures **generated by the helpers**, not checked in:
  - a run directory populated with the whitelisted top-level artifacts
    (`issue.md`, `spec.md`, `plan.md`, `contract.yaml`, `followups.md`, the
    summaries `spec-summary.md`/`plan-summary.md`) plus a `drafts/` directory
    holding per-author drafts `<stem>.claude.<ext>` / `<stem>.codex.<ext>`
    (e.g. `spec.claude.md`, `spec.codex.md`, `plan.claude.md`,
    `contract.codex.yaml`) — task B1/B2;
  - a run **without** `escalation.md` and with at least one **missing draft**
    represented by a failure marker `*.FAILED` (e.g. `spec.codex.FAILED`) in
    `drafts/` instead of the draft file — tasks B2/B3/B6;
  - a **large generated artifact** in the run directory — proportionate, i.e.
    big enough that an unbounded initial render would demonstrably grow with
    input size; no fixed byte/line threshold is prescribed — with sentinels
    near its head and tail so a test can prove the route serves the FULL
    content while the initial render is bounded — task B7;
  - a symlink under the run directory whose target points **outside** it, and
    the encoded-/nested-separator and traversal inputs, for the B5 rejection
    tests;
  - an event log exercising the timeline lanes (orchestrator/spec/plan/build
    lane(s)/codex/CI), including CI-polling and gate-runtime **waiting**
    intervals, a **still-running** span (no end), a **dry-run** log whose
    `agent.run` ends carry no `usage` (so header cost/tokens are absent), and a
    run with **no event log** (state-only) — tasks A2/A3/A4/A6/A8.
- Reuse these fixtures for the endpoint tests, the template-behavior tests, the
  reachability tests and the manual timing evidence in the guide.

### 2. Add the read-only artifact-content route

- Add `GET /api/runs/{repo}/{run_id}/artifacts/{name}` in the existing FastAPI
  app (GUI-SPEC §7.4).
- Resolve repo and run through the existing slug / run-ID / containment checks
  (`require_run`) before anything else.
- Treat `{name}` as a **single path segment**, never as a filesystem path.
  Resolve it through a deterministic lookup against the fixed whitelist (B4):
  - a top-level whitelist name (`issue.md`, `spec.md`, `plan.md`,
    `contract.yaml`, `escalation.md`, `followups.md`, `spec-summary.md`,
    `plan-summary.md`) maps to that file in the run directory;
  - a draft name of the **exact** form `<stem>.<author>.<ext>` with
    `author ∈ {claude, codex}` and `<stem>.<ext>` in the top-level whitelist
    (e.g. `spec.claude.md`) maps to `drafts/<name>` in the run directory —
    drafts are addressed by their **flat filename**, never by a
    `drafts/…`-prefixed name;
  - every other name is unknown.
- **Security (B5, non-negotiable):** an unknown name yields **404**, never 5xx,
  and never a filesystem read outside the run directory. Resolve the mapped file
  through the same `_contained(path, runs_root)` discipline the diff/events
  routes use, so a whitelisted name whose file is a symlink pointing out of the
  run directory yields 404 and the target is never read. Any raw or encoded `/`
  or `\`, absolute path, or `..` sequence in the name fails the single-segment /
  whitelist check and yields 404 without touching the filesystem.
- Only files reachable through the B4 mapping are served (B6). Any other
  `drafts/` entry — including failure markers like `spec.codex.FAILED` — has no
  external name and is never served; the corresponding draft counts as missing.
- On a resolvable-but-absent file (e.g. `escalation.md` on a run that did not
  escalate, or a mapped draft whose file is missing) or a mapped name that
  resolves to a **non-file** (e.g. a directory) return 404 — a missing artifact
  is never a 5xx (B3).
- For a resolvable, present, contained file: serve its content as faithful text
  (E10), streamed/read so the route can return the full content of the large
  fixture (B7). The concrete content-type/streaming mechanics are an
  implementation choice and are not pinned.

Tests (FastAPI `TestClient`, fixture run directory):
- `GET …/artifacts/spec.claude.md` → 200, serving `drafts/spec.claude.md`.
- `GET …/artifacts/spec.md` → 200, serving the top-level file; content matches.
- `GET …/artifacts/drafts/spec.claude.md` and the encoded-separator form
  `…/artifacts/drafts%2Fspec.claude.md` → 404.
- Traversal attempts (`..%2F…`, absolute path, any raw or encoded `/` or `\` in
  the name) → 404 **without touching the filesystem** (a read spy proves it).
- A whitelisted name whose file is a symlink pointing out of the run directory
  → 404; the target is never read.
- An unknown flat name (`spec.gpt.md`, `state.json`) → 404.
- A `*.FAILED` marker has no external name → 404; the draft counts as missing.
- `…/artifacts/<large fixture name>` → 200 serving the FULL large-fixture
  content (head and tail sentinels both present).
- Invalid repo slugs, run IDs and absent runs keep the existing controlled
  containment behavior.

### 3. Add the run-level Artifacts tab

- Add an **Artifacts** tab at run level listing the whitelisted artifacts of the
  run: `issue.md`, `spec.md`, `plan.md`, `contract.yaml`, `escalation.md`,
  `followups.md`, the summaries, and the dual-authoring drafts (B1).
- For a dual-authored artifact, present the two drafts (`<stem>.claude.<ext>`,
  `<stem>.codex.<ext>`) against the synthesis so a reader can compare what each
  author contributed (B2). A missing draft — including one whose author failed
  and left only a `*.FAILED` marker — is shown as **missing**, not an error.
- An absent artifact (e.g. `escalation.md` on a non-escalating run, or a missing
  draft) is marked as missing; never an error, never a 5xx (B3).
- Artifact content is fetched from the step-2 route and rendered as faithful
  **monospace** text (E10, no Markdown library), escaped, never interpreted as
  markup. Opening an artifact inserts only a **bounded initial portion** into
  the DOM — bounded meaning its size does not grow with the artifact size —
  while the full content stays reachable through explicit further loading or
  bounded incremental steps (B7, E8). The concrete paging/collapsing mechanism
  is an implementation choice and is not pinned; no content is dropped.

Tests:
- The tab lists the whitelisted artifacts and the drafts; present vs. missing
  artifacts render as such (B1/B3), including a `*.FAILED`-only draft shown as
  missing (B2/B6).
- Dual-authored artifacts present both drafts against the synthesis (B2).
- Against the large generated fixture: the initial render is **bounded** (does
  not grow with artifact size) and the full content stays reachable through
  the mechanism's further-loading path (B7). This proportionate large-input
  test is the evidence that a large artifact does not block the interface; no
  separate artifact performance measure exists (review descope).

### 4. Derive and render the Timeline tab

- Add a **Timeline** tab at run level (A1). Selecting it shows horizontal
  swimlanes with time running left to right; `Trace` remains the default tab.
- Derive lanes and bars from the run's **already-loaded** event log (the same
  events `Trace` uses); introduce no new reader and no change to `model.py`
  (A2). Pair span starts and ends by span identity; an unended span becomes an
  open-ended interval. One lane per strand present in the run: orchestrator,
  spec, plan, each build lane, codex, CI. A strand absent from a run
  contributes no lane and is never an error.
- Distinguish **active** sections from **waiting** sections visually; waiting
  covers at least CI polling and gate runtime — the intervals where time passes
  without anyone working (A3).
- Show, in the timeline header, total duration, total cost, and tokens per model
  for the run (A4). A value the log does not carry (cost/tokens under a dry run,
  where mocks produce no `usage`) renders **empty**, never a false `0`
  (GUI-SPEC §12).
- Clicking a bar navigates to the corresponding node in the `Trace` tab —
  switches to `Trace` and reveals/selects that node (A5), reusing the existing
  `data-seq` selection path: each bar carries its target node's identity.
- Draw a still-running span's bar to the current right edge rather than omitting
  it; the timeline renders for a live run and a finished run through the **same**
  path (A6).
- Draw with own means only — CSS and, where needed, inline-generated SVG; no
  charting library and no third-party asset (A7, E5). The concrete drawing
  mechanism is an implementation choice and is not pinned.
- A run with no event log shows the timeline empty with a clear "no trace"
  indication, never a 5xx — parallel to the existing no-trace handling (A8).

Tests:
- The run-level tab set is `Trace` (default) · `Timeline` · `Artifacts` · `Raw`;
  `Trace` is the default (A1).
- One lane per present strand; an absent strand contributes no lane and is no
  error (A2).
- Active and waiting sections (CI polling, gate runtime) are distinguishable
  (A3, distinction attested at source/markup level; the visual check is manual).
- The header shows duration, and shows cost/tokens when present but **empty**
  (never `0`) for the dry-run fixture that carries no `usage` (A4).
- A bar carries the target node's `data-seq` so a click can select it in Trace
  (A5, wiring attested at source level; the navigation is a manual check).
- A running-span fixture and a finished-span fixture both render through the
  same path; the running bar reaches the current edge (A6).
- A no-event-log run renders the empty timeline with the no-trace indication,
  not a 5xx (A8).

### 5. Add the performance instrumentation (C)

- In the served client script, instrument the **two** interactions the issue
  names — node selection and tab switch — with the vanilla `performance` API
  only; no new dependency, no browser-automation tool (C4). For each
  interaction set a start `performance.mark()` at the triggering input event
  and an end mark **only once the interaction is observably complete**: the
  resulting content has been inserted into the DOM **and the browser has
  painted it** (C1). Because a `requestAnimationFrame` callback runs *before*
  the associated paint, record the end mark in a task scheduled *from within* a
  `requestAnimationFrame` callback (e.g. rAF → `setTimeout(0)`), which runs
  after that paint — the same post-paint sequence for both interactions.
  Asynchronously loaded content counts toward the measure: the sequence starts
  after the fetched content is rendered, not when the request is issued; a
  loading indicator is not completion. Between the marks create a named
  `performance.measure()`, readable via `performance.getEntriesByName(...)` and
  in the browser's Performance panel.
- Pin the mark/measure names and their completion conditions **exactly** (C2):
  - node selection: marks `adw:select:start` / `adw:select:end`, measure
    `adw:select` — complete when the detail pane for the selected node is
    rendered;
  - tab switch: marks `adw:tab:start` / `adw:tab:end`, measure `adw:tab` —
    complete when the target tab's content is rendered.
- No third measure and no supersession/generation protocol is built (review
  descope): the issue requires marks and measures for node selection and tab
  switch only.

Tests (assert the served client script text — wiring and stable names only, not
presented as proof of runtime behavior):
- The script contains the pinned mark and measure names for both interactions
  (C2).
- Both interactions are instrumented (start at the input event, end after the
  fetched/rendered content) (C1).
- The end mark is recorded in a task scheduled from a `requestAnimationFrame`
  callback (the post-paint sequence), not directly in the rAF callback (C1).

### 6. Write the measurement guide document (C3)

- Add a short guide document in the repo describing the procedure so a person can
  verify the ≤ 2 s promise in about a minute rather than inferring from a
  screenshot timeout:
  - how to start the existing GUI against a suitable run (the deterministic
    fixture run or a comparable real run);
  - which run and which node to pick — an `agent.run` node with a large payload
    (the class of node that blocked for 30 s before);
  - which measure name to read (`adw:select`, `adw:tab`) and how
    (`performance.getEntriesByName(...)` / the Performance panel);
  - the exact post-paint completion sequence from C1;
  - the passing value: measure duration ≤ 2000 ms.
- Include the **manual checklist** for the checks that browser automation is
  excluded from (Definition of Done §1, documented-manual): the visual
  active/waiting distinction (A3), bar-click navigation to the trace node (A5),
  and the ≤ 2 s readings of `adw:select` and `adw:tab` against the named
  fixtures (C).

Tests:
- The guide document is present in the repo, names the same two measures, the
  fixtures, the post-paint sequence and the passing value, and contains the
  manual checklist (DoD §4).

### 7. Verification and handoff

- Run `uv run ruff check .` and `uv run pytest -x -q` (E3).
- Perform and document the reproducible **manual browser check** from the guide:
  the ≤ 2 s readings of `adw:select` and `adw:tab` against the named fixtures,
  the active/waiting distinction (A3), and bar-click navigation (A5).
- Confirm the large-fixture proportionality (an unbounded render would grow
  with input size), full-content reachability, the read-only property, and
  that no forbidden file, dependency, third-party asset (charting/Markdown
  library) or validation command changed.
- Keep the new automated suite roughly within the guideline of **18–25 tests**
  across A, B and C; add no tests unrelated to the acceptance criteria.

## Verification split (browser automation excluded)

Verification is split explicitly, since browser automation is out of scope:

- **Automated** (server/static, via `pytest`): route behavior, the B4 name
  mapping and B5 rejection cases, tab presence in the rendered view, bounded
  serving/rendering for B7 (the proportionate large-input test), the pinned
  mark/measure names and the post-paint completion sequence (C1) in the served
  client script. Source-text assertions attest wiring and stable names only;
  they are **not** presented as proof of runtime behavior.
- **Documented manual checks** (the C3 guide's checklist): the visual
  active/waiting distinction (A3), bar-click navigation to the trace node (A5),
  and the ≤ 2 s readings of `adw:select` and `adw:tab` against the named
  fixtures (C).

## Test-scope guardrail

Guideline (not a hard gate): roughly **18–25 new tests** across A, B and C.
Coverage of the acceptance criteria is what matters, not the exact count;
markedly more is a signal of scope drift.

## Definition of Done

1. All acceptance criteria A1–A8, B1–B7 and C1–C4 are met — C as descoped per
   the Codex review: two instrumented interactions (node selection, tab
   switch), no supersession protocol, no fixed fixture cardinality — verified
   per the split above (automated vs. documented-manual).
2. `Timeline` and `Artifacts` are reachable run-level tabs in the run detail
   view; `Trace` stays the default and the existing `Trace`/`Raw` views are
   unchanged in behavior.
3. The artifact route serves only names resolvable through the B4 mapping;
   unknown name, nested/encoded-separator, traversal and escaping-symlink
   attempts return 404 and never read outside the run directory, verified against
   the FastAPI `TestClient` with a fixture run directory (including the large B7
   artifact, drafts and a `*.FAILED` marker).
4. The performance instrumentation carries the documented names and the C1
   post-paint completion semantics (end mark in a task scheduled from a
   `requestAnimationFrame` callback, after the paint); tests assert the served
   client script contains the pinned names and instruments both interactions;
   the guide document is present, names the same measures, the fixtures, the
   post-paint sequence and the passing value, and contains the manual
   checklist.
5. No new runtime dependency, no third-party frontend asset, no charting and no
   Markdown library is added; the web stack stays the optional `adw[gui]` extra
   (E5, E7, E10). The UI is uniformly English (E9).
6. No change to `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
   `adw/gui/model.py` or the orchestrator; the GUI remains read-only.
7. Roughly 18–25 new tests across A, B and C (issue guideline).
8. Real gates green (E3): `uv run ruff check .` and `uv run pytest -x -q`.
   `flake8`, `isort` and `black` are not added to dependencies, configuration,
   scripts or validation commands.

## Deferred (deliberately not built)

Ideas that are defensible but out of proportion or explicitly ceilinged for this
run go here, not into acceptance criteria. A review finding that asks for one of
these is rejected with this rationale documented, not implemented.

- **Real Markdown rendering** of artifacts (a Markdown library). Faithful
  monospace text meets §7.2 for spec/plan/contract files this run; a library
  would be a new dependency of low value here (E10).
- **Retention / run management**: `adw runs list`, `adw runs prune`, gzip,
  the `trace:` config key — next run (issue ceiling).
- **i18n / language switch** — until the dedicated i18n step (E9).
- **Automated browser timing** (Playwright/Selenium) or any in-app latency
  budget/alerting. The proof is a documented manual read of a `performance`
  measure; automation is explicitly out (issue non-goal).
- **Surfacing draft-failure details** (e.g. rendering the content of
  `*.FAILED` marker files). The Artifacts tab marks the draft as missing;
  diagnosing the failure belongs to the trace, not the artifact view.
- **Cross-run or A/B timeline comparison** and cost/latency trends — needs a
  corpus first (GUI-SPEC §2 non-goals).
- **Emitter/model changes** to make the tree self-describing or to carry patch
  text for the timeline — the boundary and orphan handling stay as they are
  (E1, E2).
