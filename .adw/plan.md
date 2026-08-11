# Plan — GUI responsiveness: keep the ≤ 2 s promise, supersession, `adw:artifact`

Authoritative on conflict: `docs/GUI-SPEC.md`, especially §9 (performance and
limits) and §7.2 (views) — **except** where the issue's pre-decided points
(E1–E10) and scope ceilings override older GUI-SPEC text for this run (notably
E10: artifacts are shown as **monospace text, no Markdown library**, superseding
§7.2's "rendered as Markdown"). This plan implements `.adw/spec.md` and builds
strictly against `.adw/contract.yaml`.

**Single-lane.** There is exactly one workstream, **backend** — the GUI web
layer belongs wholly to it; no frontend lane exists. The three tasks are (A)
make the "reaction ≤ 2 s" promise actually hold on a heavy run by bounding the
**number** of entry nodes materialised in the DOM independently of the total,
(B) restore latest-interaction-wins so a superseded selection never writes the
wrong node into the detail pane and produces no measure, and (C) add the third
measure `adw:artifact`. This completes/repairs an existing view; it is not a
redesign. The mechanism for bounding the DOM (windowing, paging,
expand-on-demand) and the concrete template/CSS/JS structure remain
implementation choices and are not pinned.

## Guardrails

- **Web layer only.** Changes touch exclusively `adw/gui/app.py`, the templates
  (`adw/gui/templates/run_detail.html`), the own assets (`adw/gui/static/app.js`,
  `adw/gui/static/app.css`), the guide `docs/gui-response-time.md`, and the
  associated tests/fixtures. **Off limits and unchanged** (issue non-goals, E1,
  E2): `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
  `adw/gui/model.py`, and every orchestrator file — `phases.py`, `cli.py`,
  `gates.py`, `codex.py`, `worktrees.py`, `state.py`, `triage.py`, `ci.py`,
  `github.py`. A bug found in any of these is a **finding in the report, not a
  diff — even a correct fix** (in the prior run `worktrees.py` was changed
  against this boundary; that must not recur). Orphan spans and the `run`-span
  boundary stay as they are; any repair is on read, in the finished model.
- **Strictly read-only.** No GUI code path writes to `state.json`, the repo or
  the event log; no new write route. Run data is read only below the resolved
  `.adw/runs/<run_id>/` directory, under the existing containment / `RUN_ID_RE`
  / slug backstops, applied before any run is read.
- **No new HTTP route required.** The read-only artifact-content route
  (`GET /api/runs/{repo}/{run_id}/artifacts/{name}`) already exists and is
  reused unchanged; task C adds only client-side instrumentation over it.
- **No new runtime dependency, no third-party frontend asset** (E5): vanilla
  JS, own CSS, system fonts. No virtualisation/windowing library — windowing, if
  chosen, is written by hand. No charting library, **no Markdown library**
  (E10), no browser-automation tool (no Playwright, no Selenium). The web stack
  stays the optional extra `adw[gui]` (E7).
- **The UI is English throughout** until i18n (E9).
- **E8 governs the display, not the log.** Bounded initial rendering
  (windowing / paging / expand-on-demand) is explicitly wanted as long as the
  full content of every entry stays reachable. A finding that reads task A as a
  violation of "no payload capping" is **rejected** with this rationale (E8).
- **The Deferred valve binds the review loop too.** A finding that asks to
  re-introduce a deferred or pre-decided point (wall-clock assertions in
  automated tests, i18n, run management, gzip, browser automation, a Markdown
  library, an emitter/model change, an off-limits file edit) is rejected with
  the documented rationale, not implemented.
- Real gates (E3): `uv run ruff check .` and `uv run pytest -x -q`. `flake8`,
  `isort`, `black` appear nowhere — not in dependencies, config, scripts or
  validation commands.

## Starting point (verified in the code)

- **Server-side node windowing already exists.** `run_detail_page`
  (`app.py:1207`) reads `?limit` (`_parse_limit`, default `_DISPLAY_WINDOW = 100`,
  clamped to `[1, _LIMIT_MAX]`) and passes it to `_run_detail` and the template.
  `run_detail.html` windows by node **count** with a `load_more` macro: the
  trace tree (`tree_item`, `node.children[:limit]`), the per-node panes
  (`all_panes`, `node.children[:limit]`), the **Tools** list (`tools[:limit]`)
  and the Raw list. This slicing is the starting point but is **not yet a
  global cap**: `[:limit]` applies per **sibling group** (recursion can multiply
  the budget at every nesting level) and per **pane** (up to `limit` panes each
  holding up to `limit` tools). The `load_more` mechanism (raising `?limit`) is
  likewise insufficient as the reachability mechanism: it grows a **prefix**
  monotonically, so reaching a late entry re-materialises every preceding
  entry and the DOM grows without bound. This run turns the slicing into one
  **global** budget per collection that holds **throughout navigation**,
  provable and pinned — a stable per-entry DOM marker counted over the
  **complete document**, the ≤ 200 cap held across fixture sizes and after
  every transition, and reachability asserted.
- **Entry markup today.** Trace-tree entries are `li.node[data-seq]` (one
  `data-seq` per tree entry). Tool entries are `li.tool-*` carrying an inner
  `pre.tool-body[data-load-seq]`; they do **not** yet carry a single stable
  entry-level marker of their own. Task A must ensure exactly **one**
  machine-readable marker element per rendered entry on **both** collections
  (a `data-` attribute or ARIA role), document that selector in the guide, and
  count it in tests.
- **Measures adw:select / adw:tab already exist** (`app.js`): `perfMark` sets
  the start mark at the input event; `perfEndAfterPaint` records the end mark in
  a task scheduled from a `requestAnimationFrame` callback (rAF → `setTimeout 0`,
  after the paint); `perfEndAfterContent` waits for the async load promise to
  settle before scheduling the post-paint end mark. Task C reuses this exact
  construction for `adw:artifact`.
- **No supersession guard today.** `applySelection` returns `loadToolBody`'s
  promise, but a late fetch from a superseded selection still writes into its
  pane and its `adw:select` measure still completes. Task B adds the
  latest-interaction-wins guard.
- **Artifact open is not instrumented today.** `loadArtifact` fetches the
  content and inserts a bounded initial slice (`ARTIFACT_CHUNK = 20000`, "Show
  more" for the rest) via `textContent` (E10). Task C wraps opening an artifact
  with the `adw:artifact` measure completing after the slice is painted.
- **Fixtures/tests.** `tests/gui_app_helpers.py` provides the builders and the
  in-memory generators `many_tool_entries_lines` and `big_agent_run_lines`; the
  app is exercised via `create_app(repos=…)` with FastAPI's `TestClient`. The
  existing client-behaviour test approach (`tests/test_gui_perf.py`) asserts on
  the **served client-script source text** — pinned mark/measure names and the
  rAF → task construction; there is **no JS runtime harness yet**. Source-text
  assertions cannot demonstrate B1/B2/C1 (asynchronous, observable behaviour),
  so this run adds a **minimal dependency-free JS harness** (see step 1) and
  keeps source assertions only for the pinned names. Large fixtures stay **generated in memory**, not checked in, and must have the
  correct **shape** — node **count**, not byte size (the A4 pitfall: twice a
  fixture mirrored the wrong size).

## Workstream: backend

### 1. Fixtures with the correct shape

- Extend the GUI fixture support so the automated tests can assert the node-count
  bound and reachability, and so the manual A4/C2 measurements have a target:
  - **Entry-count fixtures** for A1: otherwise-equivalent collections of **200,
    2 000 and 20 000** entries, for **both** collections that list entries — the
    **trace tree** (many tree entries) and the **Tools** tab of a selected node
    (many tool entries). The fixtures must have the correct shape: node **count**
    (enough nodes), not byte size — and the correct **structure**: the trace-tree
    fixtures include deeply **nested branches** (not only flat sibling lists) and
    the runs include **multiple tool-bearing nodes**, so that per-sibling-group
    or per-pane slicing cannot pass the tests while the complete document stays
    unbounded. Reuse/extend `many_tool_entries_lines` and
    `big_agent_run_lines`. Place **stable sentinels** near the beginning, just
    beyond the initial bound, in the **middle**, and at the very end of each
    collection, so tests can distinguish bounded display from loss of content
    and can verify rendering **and** the DOM bound at each navigation target.
  - A **heavy run** with **≥ 2000 tool nodes** for the manual A4 measurement
    (the `spec_agent`-class node that blocked for 40 s).
  - A **≥ 2 MB artifact** in the run directory for the manual C2 measurement,
    generated in memory with sentinels near head and tail so a test can prove the
    route serves the full content while the initial render stays bounded.
- Add a **minimal, dependency-free JS test harness** for the behavioural B/C
  tests: pytest executes the served `app.js` in a plain `node` subprocess
  (a development/test-time tool only — not a runtime dependency) inside a
  stubbed environment: DOM, `fetch`, `performance` (marks/measures),
  `requestAnimationFrame` and task scheduling are hand-written stubs whose
  deferred responses and rAF/task queue the test drives deterministically. No
  npm package, no browser automation (the harness is not a browser); the stubs
  live with the tests. `node` is a **required verification tool** of the test
  gate: if it is unavailable, the behavioural tests **fail** with a clear
  message — they never skip and never weaken to source-text assertions, so the
  gate cannot go green with B1/B2/C1 untested.
- Reuse these fixtures across the endpoint, template-behavior, reachability and
  supersession tests and the manual timing evidence in the guide.

### 2. Task A — bound the initial entry-node DOM by count (A1–A3)

- **A1 (bounded initial DOM, global budgets).** Ensure the number of **entry
  nodes** materialised on initial render is bounded by a hard cap independent of
  the total number of entries — **at most 200 per collection** at every fixture
  size (200 / 2 000 / 20 000). The cap is **global per collection over the
  complete initial document**, not per subtree or per pane:
  - **Trace tree (left):** one global initial budget of ≤ 200 trace-entry
    markers across **all nesting levels together**. Per-sibling-group slicing
    (`node.children[:limit]` applied recursively) multiplies the budget at each
    recursion level and is **insufficient**; the implementation enforces one
    shared budget for the whole tree.
  - **Tool entries (right):** one global initial budget of ≤ 200 tool-entry
    markers present **anywhere in the initial detail-pane DOM, including
    hidden/non-active panes**. Rendering up to `limit` panes each with up to
    `limit` tools defeats the cap; hidden intermediate panes are not eagerly
    materialised but brought in on demand.
  The default window (`_DISPLAY_WINDOW`) stays ≤ 200; windowing stays by node
  **count**, and where a collection is not yet bounded by a global count, bound
  it.
- **Stable per-entry marker.** Give each rendered entry — one per trace-tree
  entry and one per tool entry — exactly **one** machine-readable DOM marker
  (a `data-` attribute or ARIA role; the concrete selector is the
  implementation's choice). Document that selector in
  `docs/gui-response-time.md`; it is the selector the automated tests count.
- **A2 (reachability with the bound maintained).** Every trace-tree entry and
  every Tools entry stays reachable through the existing views, including
  entries outside the initial subset. Reaching a not-yet-materialised entry
  (via the chosen window/paging/expand mechanism) brings it into the DOM and it
  renders correctly; no entry is dropped or made permanently unreachable. The
  bound is **not an initial-render-only property**: the materialised entry set
  stays within the same hard cap **throughout navigation** — after paging,
  scrolling, expansion or jumping. A monotonically growing prefix (the existing
  "Load more" raising `?limit`) is therefore insufficient as the reachability
  mechanism: reaching a middle or the final entry must not require
  materialising all preceding entries, so the prefix mechanism is **replaced or
  constrained** (e.g. a moving offset window — the concrete choice stays free).
  Navigation reaches the final entries without eagerly materialising
  intermediate hidden panes and without multiplying the cap at any recursion
  level. Selection and navigation keep working when the requested entry was not
  initially materialised.
- **A3 (presentation fidelity).** Bounded rendering changes presentation only:
  displayed entries appear in the underlying order, content is not truncated or
  reordered relative to the data served, and the complete payload of every entry
  stays reachable (E8, the existing lazy `loadToolBody` / events route). Tool
  entries keep their call/result identity. This is asserted on the rendered
  output; log immutability is **not** an automated assertion (the log-producing
  files are off limits and unchanged).

Tests (FastAPI `TestClient`, server-rendered markup + DOM node-count assertions;
every count is taken over the **complete initial document**, never a single
subtree or only the active pane):
- For 200 / 2 000 / 20 000 trace-tree entries — including the **deeply nested**
  fixture shape — the total count of the trace-entry marker in the initial
  document is **≤ 200** and does not grow with the total (A1).
- For 200 / 2 000 / 20 000 tool entries spread over **multiple tool-bearing
  nodes**, the total count of the tool-entry marker in the initial document
  (hidden panes included) is likewise ≤ 200 and flat across sizes (A1).
- Exactly **one** marker element per rendered entry on both collections (the
  counting definition).
- Navigating to the **beginning, middle and final** sentinels (each initially
  absent where applicable) materialises them and they render correctly, and
  **after every transition** the DOM-entry bound still holds — reaching a late
  entry does not re-materialise all preceding entries and does not eagerly
  materialise intermediate hidden panes; no entry is unreachable (A2).
- Rendered entries keep the underlying order and are not truncated relative to
  the served data; a windowed-out entry's full payload is still reachable (A3).

### 3. Task B — latest-interaction-wins / supersession (B1, B2)

- Introduce a per-interaction **generation token** (a monotonically increasing
  counter captured when a selection starts). A selection's asynchronous
  work — the tool-body fetch and the post-paint end mark — is applied **only if
  its generation is still the current one** when it returns.
- **B1 (correct final content).** Given two quick node selections A then B, the
  detail pane ends showing node **B**'s content. A superseded selection's
  late-returning fetch does **not** write its content into the DOM while a newer
  node is selected. (Concretely: `applySelection` / `loadToolBody` guard the DOM
  write behind the generation check; the current-selection state — `selectedSeq`
  and the active pane toggle — always reflects the latest selection.) The guard
  covers supersession both before and after the async fetch is dispatched.
- **B2 (no measure for a superseded interaction).** A superseded interaction
  records **no** end mark and produces **no** measure; only the winning (latest)
  interaction is measured. The post-paint end-mark scheduling is skipped when the
  interaction's generation is no longer current, so start and end marks from
  different selections are never paired into one measure — no measure ever
  combines A's start mark with B's end mark.
- Cancellation stays an internal implementation detail — no separately exposed
  product state or interface (Deferred). The only observable requirement is that
  obsolete results have no observable effect.

Tests (**behavioural**, via the minimal JS harness of step 1 — no browser
automation):
- Drive two selections A then B with **deferred responses resolved in reverse
  order** (A settles last): the final detail-pane content belongs to **B**, and
  A's late response performs **no DOM mutation** (B1).
- The superseded selection records **no end mark and no `adw:select` measure**;
  exactly the winning selection produces a measure; no measure pairs A's start
  mark with B's end mark (B2).
- Supersession holds whether B interrupts **before or after** A's fetch was
  dispatched.
- Source-text assertions on the served script remain only for the pinned
  mark/measure names.

### 4. Task C — third measure `adw:artifact` (C1, C3)

- Instrument **opening an artifact** with a measure named **`adw:artifact`**,
  using the **same construction** as `adw:select`/`adw:tab`:
  - set the start mark (`adw:artifact:start`) when the artifact is opened
    (the triggering input event);
  - record the end mark (`adw:artifact:end`) and the `adw:artifact` measure only
    **after the browser has painted the artifact's content** — a task scheduled
    from a `requestAnimationFrame` callback (rAF → task after paint), reusing
    `perfEndAfterPaint` / `perfEndAfterContent`.
- **Asynchronously fetched content counts toward the measure** (C1): the
  post-paint sequence starts only after the fetched artifact content is rendered
  into the DOM (the bounded initial slice inserted by `loadArtifact`), not when
  the request is dispatched or the response arrives — a loading indicator is not
  completion. `loadArtifact` returns/settles a promise the measure waits on.
- If the artifact display is bounded (the existing "Show more" slice), the
  **complete artifact content stays reachable** (E8/E10) and is rendered as
  faithful monospace text via `textContent`, never interpreted as markup.

Tests (**behavioural**, via the minimal JS harness — no browser automation):
- Drive an artifact open through its full sequence — request dispatch, response
  arrival, DOM insertion, `requestAnimationFrame`, the subsequent task — and
  assert `adw:artifact` is **not** complete at dispatch or response arrival and
  **is** complete only after the post-paint rAF → task sequence (C1).
- The start mark is set at the triggering input event; the measure covers the
  asynchronously fetched, rendered slice.
- Source-text assertions on the served script remain for the pinned names
  `adw:artifact:start` / `adw:artifact:end` / `adw:artifact` and the shared
  construction with `adw:select`/`adw:tab`.

### 5. Update the measurement guide `docs/gui-response-time.md` (A1, C3, and the
recorded A4/C2 measurements)

- Add **`adw:artifact`** alongside `adw:select` and `adw:tab`: its completion
  semantics per C1 (rAF → task after paint, async content counts), how to read
  it (`performance.getEntriesByName("adw:artifact").at(-1).duration` / the
  Performance panel), and the **≤ 2000 ms** threshold. Note that if the artifact
  display is bounded, the full content stays reachable (E8/E10).
- Document the **A1 entry-node selectors** — the `data-`/ARIA markers the
  automated tests count, one for the trace tree and one for the Tools tab — and
  the hard cap of 200 per collection, noting that the bound holds **throughout
  navigation**, not only on initial render, so the bound is reproducible and
  the counting definition is explicit.
- Record the performed **manual measurements** as completed evidence:
  - **A4:** on a run with **≥ 2000 tool nodes**, the identified run, its
    verified tool-node count, and the read-off `adw:select` and `adw:tab`
    values — each **≤ 2000 ms**. A fixture alone is **not** accepted as evidence
    (the twice-repeated pitfall).
  - **C2:** on an artifact of **≥ 2 MB**, the artifact's identity, its byte
    size, and the read-off `adw:artifact` value — **≤ 2000 ms**.
- Keep the manual checklist for the checks browser automation is excluded from.

### 6. Verification and handoff

- Run `uv run ruff check .` and `uv run pytest -x -q` (E3). The behavioural
  B/C tests are part of this required gate and always execute — the quick
  A-then-B supersession scenario and the artifact
  DOM-insertion → rAF → task completion scenario run on every gate pass; a
  missing JS runtime is a **verification failure**, not a skip.
- Perform and document, per the guide, the reproducible **manual browser
  measurements**: A4 (`adw:select`, `adw:tab` on ≥ 2000 tool nodes) and C2
  (`adw:artifact` on ≥ 2 MB), each ≤ 2000 ms, recorded in the guide with the
  run/artifact identity and the read-off values.
- Review the diff to confirm: no off-limits file changed (a defect found there
  is a report finding, not a fix); every trace-tree and Tools entry stays
  reachable; existing Trace, Timeline, Artifacts, Raw and Diff views keep
  working; the GUI stays read-only; no new dependency, no third-party asset and
  no forbidden validation command appeared.
- Keep the new automated suite within the issue guideline of roughly **12–18 new
  tests** across A, B and C; add no tests unrelated to the acceptance criteria.

## Verification split (browser automation excluded)

- **Automated** (`pytest`): A1/A2/A3 via server-rendered markup and DOM
  node-count assertions over the complete document, on initial render **and
  after each navigation transition** (`TestClient`);
  B1/B2 and C1's measure semantics **behaviourally** via the minimal
  dependency-free JS harness (stubbed DOM/fetch/performance/rAF/task queue —
  not a browser); source-text assertions on the served script only for the
  pinned mark/measure names. Nothing automated asserts wall-clock timing.
- **Documented manual** (the guide): the ≤ 2000 ms A4 (`adw:select`, `adw:tab` on
  ≥ 2000 tool nodes) and C2 (`adw:artifact` on ≥ 2 MB) readings. Wall-clock
  ≤ 2 s is manual by decision of the issue; the automated half proves the DOM
  bound.

## Definition of Done

1. A1, A2, A3, B1, B2 and C1 are covered by automated tests (server-rendered
   markup and full-document DOM node-count assertions, initial and after
   navigation, via `TestClient`; JS-level
   supersession and measure semantics **behaviourally** via the minimal
   dependency-free harness — no browser automation). These behavioural tests
   are part of the required gate: an unavailable JS runtime is a verification
   failure, never a skip. Roughly 12–18 new tests across A, B and C.
2. A4 and C2 are performed manually per `docs/gui-response-time.md` and their
   read-off values recorded there as completed measurements, each ≤ 2000 ms. The
   guide documents all three measures (C3) and the A1 entry-node selectors.
3. No file listed under the guardrails / Non-goals is modified; any bug in an
   off-limits file is a finding in the report, not a diff.
4. No new runtime dependency and no third-party frontend asset (E5/E7); the web
   stack stays the optional `adw[gui]` extra.
5. Existing behaviour of the Trace, Timeline, Artifacts, Raw and Diff views
   stays available; no existing entry or payload becomes unreachable.
6. Gates green (E3): `uv run ruff check .` and `uv run pytest -x -q`; `flake8`,
   `isort`, `black` are absent from dependencies, config, scripts and commands.

## Deferred (deliberately not built)

Hardening or extension ideas beyond the above — including findings from the
Codex review rounds — belong here, not in the acceptance criteria. A review
finding that asks for one of these is rejected with this rationale, not
implemented.

- Wall-clock performance assertions in automated tests (CI timing is flaky and
  browser-dependent); the automated half proves the DOM bound (A1–A3), the
  wall-clock ≤ 2 s evidence is the manual A4/C2 per the guide, by decision of
  the issue.
- Cancellation as a separately exposed product state or interface; the required
  behaviour is only that obsolete results have no observable effect — the
  internal cancellation strategy is an implementation choice.
- A pinned bound on initially rendered artifact bytes; C2 fixes the observable
  outcome (≤ 2 s on ≥ 2 MB), the display mechanism stays free.
- Search, jump indexes, or navigation controls beyond what is necessary to make
  every entry reachable.
- Extending the bound/measure approach to further views beyond what A1/A2 already
  require (e.g. Raw or Timeline internals).
- Persistent latency records, telemetry, performance dashboards or alerts; the
  User Timing measures plus the guide are sufficient.
- Retention, `adw runs list`/`prune`, gzip, the `trace:` config key, i18n — the
  next and final run.
