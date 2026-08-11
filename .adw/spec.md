# Spec — Timeline tab, Artifacts tab, measurable response time

Scope source: `.adw/issue.md` (tasks A, B, C). Governing document on any
conflict: `docs/GUI-SPEC.md`, in particular §7.2 (Views), §7.4 (API), §8
(Security), §9 (Performance), §12 (Open points). The run-level tabs completed
already are `Trace` and `Raw`; this run adds the two remaining tabs of
GUI-SPEC §11 step 10 (`Timeline`, `Artifacts`) and makes the "reaction ≤ 2 s"
promise checkable.

The GUI stays strictly read-only. No production module outside the GUI viewer
is touched; `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
`adw/gui/model.py` and the orchestrator stay as they are (issue non-goals, E1,
E2). The UI is English throughout (E9). Rendering uses own means only — vanilla
JS, own CSS, system fonts, inline-generated SVG where needed; no CDN, no
third-party frontend asset, no charting and no Markdown library (E5, E10).

## Goal

A person inspecting an ADW run in `adw gui` can, without reading source code:

1. see **where a run's time goes** — which strand was active and which was
   merely waiting (CI polling, gate runtime) — on a run-level `Timeline` tab,
   and jump from any bar to the matching node in the `Trace` tab;
2. **read every artifact** the run produced — including the two dual-authoring
   drafts of each artifact side by side against the synthesis — on a run-level
   `Artifacts` tab, over a route that only ever serves files from *this* run's
   directory through a fixed name whitelist;
3. **prove the ≤ 2 s reaction promise** for node selection, tab switching and
   artifact opening, by reading a named `performance.measure()` in the browser
   — no browser automation, following a short written guide in the repo.

## Scope

- Two further run-level tabs so the run detail view carries the full §7.2 set:
  `Trace` (default) · `Timeline` · `Artifacts` · `Raw`.
- **Timeline** derives its lanes and bars from the already-loaded event log of
  the run (the same events `Trace` uses); it introduces no new reader, no
  change to `model.py`.
- **Artifacts** is served by a new read-only artifact-content route under the
  existing run-detail API surface (GUI-SPEC §7.4:
  `GET /api/runs/{repo}/{run_id}/artifacts/{name}`), guarded by the same
  containment discipline the diff/events routes already use.
- Client-side `performance.mark()`/`performance.measure()` instrumentation of
  node selection, tab switching and artifact opening, with stable documented
  names and defined completion semantics, plus a short guide document in the
  repo.

Artifact name whitelist (top-level files in the run directory):
`issue.md`, `spec.md`, `plan.md`, `contract.yaml`, `escalation.md`,
`followups.md`, `spec-summary.md`, `plan-summary.md`; plus the `drafts/`
directory holding the per-author drafts `<stem>.claude.<ext>` and
`<stem>.codex.<ext>` of a whitelisted artifact (e.g. `spec.claude.md`,
`spec.codex.md`, `plan.claude.md`, `contract.codex.yaml`).

## Non-goals

Explicit ceilings from the issue and the pre-decided points — not built in this
run, and a review finding that reintroduces one is rejected with a reason
(Deferred valve):

- No i18n, no language switch; the UI stays English until i18n (E9).
- No `adw runs list`, no `adw runs prune`, no retention, no gzip, no `trace:`
  config key.
- No change to `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
  `adw/gui/model.py` or the orchestrator. Orphan spans and the `run`-span
  boundary stay as they are; any repair is on read, in the finished model
  (E1, E2).
- No write path anywhere; the GUI stays strictly read-only.
- No new runtime dependency, no third-party frontend asset, no charting library
  and no Markdown library. Artifacts render as faithful monospace text; the
  §7.2 phrase "rendered as Markdown" is met that way for this run (E5, E10).
- No automated browser measurement; no Playwright, no Selenium.
- No redesign of the existing `Trace`/`Raw` views.

## Acceptance criteria

### A — Timeline tab

- **A1.** The run detail view offers a run-level `Timeline` tab in the §7.2 tab
  set `Trace` (default) · `Timeline` · `Artifacts` · `Raw`. Selecting it shows
  horizontal swimlanes with time running left to right; `Trace` remains the
  default tab.
- **A2.** There is one lane per strand present in the run: orchestrator, spec,
  plan, each build lane, codex, CI. A strand absent from a run contributes no
  lane and is never an error.
- **A3.** Active sections and waiting sections are visually distinguishable.
  Waiting covers at least CI polling and gate runtime — the intervals where
  time passes without anyone working.
- **A4.** The timeline header shows total duration, total cost, and tokens per
  model for the run. A value the log does not carry (e.g. cost/tokens under a
  dry run, where mocks produce no `usage`) renders empty, never a false `0`
  (GUI-SPEC §12).
- **A5.** Clicking a bar navigates to the corresponding node in the `Trace` tab
  (switches to `Trace` and reveals/selects that node).
- **A6.** A span still running has its bar drawn to the current right edge
  rather than omitted; the timeline renders for a live run and a finished run
  through the same path.
- **A7.** The timeline is drawn with own means only — CSS and, where needed,
  inline-generated SVG. No charting library and no third-party asset is
  introduced (E5).
- **A8.** A run with no event log shows the timeline empty with a clear "no
  trace" indication, never a 5xx (parallel to the existing no-trace handling).

### B — Artifacts tab

- **B1.** The run detail view offers a run-level `Artifacts` tab listing the
  whitelisted artifacts of the run: `issue.md`, `spec.md`, `plan.md`,
  `contract.yaml`, `escalation.md`, `followups.md`, the summaries
  (`spec-summary.md`, `plan-summary.md`), and the dual-authoring drafts.
- **B2.** For an artifact that was dual-authored, the two drafts
  (`<stem>.claude.<ext>`, `<stem>.codex.<ext>`) are presented against the
  synthesis so a reader can compare what each author contributed. A missing
  draft — including one whose author failed and left only a failure marker
  (e.g. `spec.codex.FAILED`) instead of the draft file — is shown as missing,
  not an error.
- **B3.** An absent artifact (e.g. `escalation.md` on a run that did not
  escalate, or a missing draft) is marked as missing; it is never an error and
  never a 5xx.
- **B4.** Artifact content is served by a read-only route
  (`GET /api/runs/{repo}/{run_id}/artifacts/{name}`, GUI-SPEC §7.4) that
  returns only files from *this* run's directory. `{name}` is a **single path
  segment** and is never treated as a filesystem path; it is resolved through
  a deterministic lookup against the fixed whitelist:
  - a top-level whitelist name (e.g. `spec.md`) maps to that file in the run
    directory;
  - a draft name of the exact form `<stem>.<author>.<ext>` with
    `author ∈ {claude, codex}` and `<stem>.<ext>` in the top-level whitelist
    (e.g. `spec.claude.md`) maps to `drafts/<name>` in the run directory —
    drafts are addressed by their **flat filename**, never by a
    `drafts/…`-prefixed name;
  - every other name is unknown.
- **B5.** An unknown name yields **404**, never 5xx, and never a file read
  outside the run directory — consistent with the containment used by the
  existing diff/events routes (GUI-SPEC §8). Endpoint tests cover at least:
  - `GET …/artifacts/spec.claude.md` → 200, serving `drafts/spec.claude.md`;
  - `GET …/artifacts/drafts/spec.claude.md` and the encoded-separator form
    `GET …/artifacts/drafts%2Fspec.claude.md` → 404;
  - traversal attempts (`..%2F…`, absolute path, any raw or encoded `/` or
    `\` in the name) → 404 without touching the filesystem;
  - a whitelisted name whose file is a symlink pointing out of the run
    directory → 404, the target is never read;
  - an unknown flat name (e.g. `spec.gpt.md`, `state.json`) → 404.
- **B6.** Only files reachable through the B4 mapping are served. Any other
  `drafts/` entry — including failure markers like `spec.codex.FAILED` — has
  no external name and is never served; the corresponding draft counts as
  missing per B2/B3.
- **B7.** Content renders as faithful monospace text (E10). Opening an
  artifact inserts only a **bounded initial portion** into the DOM — bounded
  meaning its size does not grow with the artifact — and the full content
  stays reachable through explicit further loading or bounded incremental
  steps (E8: paging/collapsing the display is allowed, no content is
  dropped). The test fixtures include a large artifact of at least **2 MB
  and 20,000 lines**; automated tests verify the route serves its full
  content and the initial render is bounded. That opening this fixture does
  not block the interface is proven via the `adw:artifact` measure (C2):
  read per the guide, it satisfies the ≤ 2 s promise — a documented manual
  check, not a browser-automated one.

### C — Measurable response time

- **C1.** For each instrumented interaction the client sets a start
  `performance.mark()` at the triggering input event and an end mark **only
  once the interaction is observably complete**: the resulting content has
  been inserted into the DOM **and the browser has painted it**. Because a
  `requestAnimationFrame` callback runs *before* the associated paint, the
  end mark is recorded in a task scheduled *from within* a
  `requestAnimationFrame` callback (e.g. rAF → `setTimeout(0)`), which runs
  after that paint — the same post-paint sequence for all three
  interactions. Asynchronously loaded content counts toward the measure —
  the sequence starts after the fetched content is rendered, not when the
  request is issued; a loading indicator is not completion. Between the
  marks a named `performance.measure()` is created, readable via
  `performance.getEntriesByName(...)` and in the browser's Performance
  panel.
- **C2.** The mark and measure names are stable and documented. The pinned
  names and their completion conditions (per C1) are:
  - node selection: marks `adw:select:start` / `adw:select:end`, measure
    `adw:select` — complete when the detail pane for the selected node is
    rendered;
  - tab switch: marks `adw:tab:start` / `adw:tab:end`, measure `adw:tab` —
    complete when the target tab's content is rendered;
  - artifact opening: marks `adw:artifact:start` / `adw:artifact:end`,
    measure `adw:artifact` — complete when the artifact's bounded initial
    portion (B7) is rendered.
- **C3.** A short guide document in the repo describes the procedure: which
  run and which node to pick (an `agent.run` node with a large payload — the
  class of node that blocked for 30 s before — and the large fixture artifact
  from B7), which measure name to read, the exact post-paint completion
  sequence from C1, and the passing value (measure duration ≤ 2000 ms) — so a
  person can verify the promise in about a minute rather than inferring from
  a screenshot timeout. The guide also contains a short rapid-reselection
  check confirming that clicking on while an interaction is still loading
  yields no stale measure (C5).
- **C4.** The measurement path adds no new dependency and no
  browser-automation tool; it is vanilla `performance` API usage in the
  existing client script.
- **C5.** Interactions supersede each other (**latest-interaction-wins**):
  each instrumented interaction starts a new generation, and when a newer
  interaction begins before an older one completes, the older one is stale —
  its late completion neither updates the DOM nor records an end mark or a
  measure. A superseded interaction produces **no measure**; the public
  measure names stay exactly as pinned in C2, and every recorded measure
  pairs the start and end of one and the same generation. This is what makes
  the readings reproducible in the very scenario that motivated task C:
  clicking around while diagnosing a stall.

## Definition of Done

1. All acceptance criteria A1–A8, B1–B7, C1–C5 are met. Verification is split
   explicitly, since browser automation is excluded:
   - **Automated** (server/static, via `pytest`): route behavior, the B4 name
     mapping and B5 rejection cases, tab presence in the rendered view,
     bounded serving/rendering wiring for B7, the pinned mark/measure names,
     the post-paint completion sequence (C1) and the supersession wiring (C5)
     in the served client script. Source-text assertions attest wiring and
     stable names only; they are not presented as proof of runtime behavior.
   - **Documented manual checks** (a short checklist in the C3 guide): the
     visual active/waiting distinction (A3), bar-click navigation to the
     trace node (A5), the ≤ 2 s readings of `adw:select`, `adw:tab` and
     `adw:artifact` against the fixtures named in the guide (C, B7), and the
     rapid-reselection check for stale completions (C5).
2. `Timeline` and `Artifacts` are reachable run-level tabs in the run detail
   view; `Trace` stays the default and the existing `Trace`/`Raw` views are
   unchanged in behavior.
3. The artifact route serves only names resolvable through the B4 mapping;
   unknown name, nested/encoded-separator, traversal and escaping-symlink
   attempts return 404 and never read outside the run directory, verified
   against the FastAPI `TestClient` with a fixture run directory (including
   the large B7 artifact, drafts and a `*.FAILED` marker).
4. The performance instrumentation carries the documented names, the C1
   post-paint completion semantics (end mark in a task scheduled from a
   `requestAnimationFrame` callback, after the paint) and the C5
   latest-interaction-wins supersession; tests assert the served client
   script contains the pinned names, instruments all three interactions and
   carries the supersession wiring; the guide document is present in the
   repo, names the same measures, the fixtures, the post-paint sequence and
   the passing value, and contains the manual checklist from DoD 1.
5. No new runtime dependency, no third-party frontend asset, no charting and
   no Markdown library is added; the web stack stays the optional `adw[gui]`
   extra (E5, E7, E10).
6. Roughly 18–25 new tests across A, B and C (issue guideline).
7. Real gates green: `uv run ruff check .` and `uv run pytest -x -q`.
   (`flake8`, `isort`, `black` must not appear as dependency, config or
   command — E3.)

## Deferred (deliberately not built)

Ideas that are defensible but out of proportion or explicitly ceilinged for
this run go here, not into acceptance criteria. A review finding that asks for
one of these is rejected with this rationale documented, not implemented.

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
