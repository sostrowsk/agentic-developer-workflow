# Specification — GUI responsiveness: keep the ≤ 2 s promise, supersession, `adw:artifact`

Authoritative reference: `docs/GUI-SPEC.md`, in particular §9 (Performance and
limits) and §7.2 (Views). On any conflict between this spec and the GUI-SPEC,
the GUI-SPEC wins — **except** where the issue's pre-decided points (E1–E10)
and scope ceilings override older GUI-SPEC text for this run. Concretely:
GUI-SPEC §7.2 says artifacts are "rendered as Markdown"; the binding E10
decision supersedes that wording — artifacts are shown as **monospace text, no
Markdown library**. This spec describes **product behaviour only** —
observable behaviour, DOM/data states, and the pinned measure contract. It
prescribes no rendering mechanism, no template/CSS/JS structure, and no
internal helper signatures.

## Goal

Make the read-only run inspector actually honour the "reaction ≤ 2 s" promise
on real, heavy runs, and restore the two pieces of correctness / verifiability
that were dropped in the prior run:

1. **A —** Node selection (`adw:select`) and tab switch (`adw:tab`) complete in
   ≤ 2000 ms on a run with **≥ 2000 tool nodes**, because the number of entry
   nodes materialised in the DOM initially is bounded independently of the
   total number of entries — on both sides: the trace tree (left) and the
   detail pane including its Tools tab (right). Every entry stays reachable.
2. **B —** Latest-interaction-wins: a superseded selection never writes into
   the detail pane and never produces a measure, so the pane always reflects
   the node the user last chose. This is a **correctness** requirement — a
   debug tool must never display data of the wrong node.
3. **C —** A third measure, `adw:artifact`, makes the "large artifacts do not
   block the surface" promise verifiable, measured on an artifact of ≥ 2 MB.

The root cause is established and not in dispute: pure render load from DOM
node **count**, not a broken code path (real run `bf831719`: node `spec_agent`,
2 126 tool nodes, 12 738 total DOM nodes; the click never completed after
40 s, no JS error). Offloading only the *contents* (lazy payload fetch,
collapsing) was tried twice and is insufficient.

## Scope

- The client-side rendering of the run-detail view (`adw/gui/static/app.js`,
  `adw/gui/static/app.css`, `adw/gui/templates/run_detail.html`) and, only
  where needed to bound the initially rendered node set, the server-side
  rendering that produces that markup (`adw/gui/app.py` and the templates).
- Bounded rendering of entry collections: the visible DOM may contain only a
  subset of the complete data, but the user can reach every entry through the
  existing views.
- Latest-interaction-wins behaviour for node selection and its asynchronously
  loaded detail content.
- The three User Timing measures `adw:select`, `adw:tab`, `adw:artifact`.
- The response-time guide `docs/gui-response-time.md`: it gains the third
  measure and records the performed manual measurements.

The mechanism for bounding the DOM is an implementation decision — windowing
(only the visible slice in the DOM), paging, expand-a-section-on-demand.
Windowing, if chosen, is written by hand (no library). The only requirement is
that the user can still reach every entry, just not all at once in the DOM.

## Non-goals

Nothing in the following list is built in this run.

- No i18n, no language switch, no `adw runs list`, no `adw runs prune`, no
  retention, no gzip, no `trace:` config key. (Next and final run.)
- No new views, no redesign, no new tabs.
- No change to `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
  `adw/gui/model.py`.
- No change to any orchestrator file: `phases.py`, `cli.py`, `gates.py`,
  `codex.py`, `worktrees.py`, `state.py`, `triage.py`, `ci.py`, `github.py`.
  A bug found there is a **finding in the report, not a diff** — even a
  correct fix. (In the prior run `worktrees.py` was changed against this
  boundary; that must not recur.)
- No new runtime dependencies, no third-party frontend asset, no
  virtualisation library.
- No browser automation, no Playwright, no Selenium.
- Pre-decided and not reopened: E1 (`run`-span boundary unchanged), E2 (orphan
  spans unchanged, `events.py` untouched, repair stays in the finished model),
  E3 (**ruff** only — no `flake8`/`isort`/`black` as dependency, config or
  command), E5 (vanilla JS, own CSS, system fonts), E7 (web stack only as the
  optional `adw[gui]` extra), E8 ("no payload capping" concerns the LOG, not
  the DISPLAY — bounded *rendering* is explicitly wanted as long as the full
  content stays reachable; a finding that reads Task A as violating E8 is
  rejected), E9 (UI is English throughout until i18n), E10 (artifacts shown as
  monospace text, no Markdown library).

## Acceptance criteria

### A — The 2 s promise, actually kept

- **A1 (bounded initial DOM, automated).** The number of **entry nodes**
  materialised in the DOM on initial render is bounded by a hard cap
  independent of the total number of entries: for otherwise equivalent
  collections of 200, 2 000 and 20 000 entries, the initial count of rendered
  entry nodes is **at most 200 per collection** at every fixture size. This
  holds for both collections that list entries: the **trace tree** on the left
  and the **Tools** tab of a selected node in the **detail pane** on the
  right. *Counting definition:* an entry node is an element carrying a stable
  machine-readable DOM marker (a `data-` attribute or ARIA role, exactly one
  marked element per rendered trace-tree entry resp. per rendered tool entry);
  the concrete selector is the implementation's choice, is documented in
  `docs/gui-response-time.md`, and is the selector the automated tests count.
  The fixture must have the correct shape — node count, not byte size, and
  enough nodes.
- **A2 (reachability, automated).** Every trace-tree entry and every Tools
  entry remains reachable through the existing views, including entries
  outside the initially rendered subset. Reaching a not-yet-materialised entry
  (via scrolling window, paging, or section expand) brings it into the DOM and
  it renders correctly; no entry is dropped or made permanently unreachable.
- **A3 (presentation fidelity).** Bounded rendering changes presentation only:
  displayed entries appear in the underlying order, displayed content is not
  truncated or reordered relative to the data served, and the complete payload
  of every entry remains reachable (E8). This is asserted on the rendered
  output; immutability of the underlying event log is **not** an automated
  assertion — the log-producing files are off limits and unchanged by this
  run.
- **A4 (measured on a heavy run, manual, documented).** Following
  `docs/gui-response-time.md`, on a run with **≥ 2000 tool nodes**, the
  `adw:select` and `adw:tab` measures are read off and are each **≤ 2000 ms**.
  The guide records the performed measurement — the identified run, its tool
  node count, and the read-off values. A fixture alone is **not** accepted as
  evidence for A4; twice a fixture mirrored the wrong size (bytes instead of
  nodes, then too few nodes) while the real view blocked.

### B — Latest-interaction-wins (supersession)

- **B1 (correct final content).** Given a quick succession of two node
  selections (A then B), the detail pane ends showing node **B**'s content. A
  superseded selection's late-returning fetch does **not** write its content
  into the DOM while a newer node is selected.
- **B2 (no measure for a superseded interaction).** A superseded interaction
  records no end mark and produces **no** measure; only the winning (latest)
  interaction is measured. Start and end marks from different selections are
  never paired into one measure.

### C — Third measure `adw:artifact`

- **C1 (measure exists, correct completion semantics).** Opening an artifact
  is instrumented with a measure named **`adw:artifact`**: the start mark is
  set when the artifact is opened, the end mark and measure are recorded only
  **after the browser has painted the artifact's content**, using the same
  construction as `adw:select`/`adw:tab` (a task scheduled from a
  `requestAnimationFrame` callback, i.e. rAF → task after paint).
  Asynchronously fetched content counts toward the measure — a loading
  indicator, request dispatch, or response arrival is not completion.
- **C2 (measured on a ≥ 2 MB artifact, manual, documented).** Following the
  guide, opening an artifact of **≥ 2 MB** yields an `adw:artifact` measure of
  **≤ 2000 ms**; the performed measurement, with the artifact size and the
  read-off value, is recorded in the guide.
- **C3 (documented).** `docs/gui-response-time.md` documents `adw:artifact`
  alongside `adw:select` and `adw:tab`: its completion semantics per C1, how
  to read it, and the ≤ 2000 ms threshold. If the artifact display is bounded,
  the complete artifact content remains reachable (E8/E10).

### Measure contract (pinned observable surface)

| Interaction    | Measure name   | Completes when                                    |
| -------------- | -------------- | ------------------------------------------------- |
| Node selection | `adw:select`   | the detail pane for the selected node is painted  |
| Tab switch     | `adw:tab`      | the target tab's content is painted               |
| Artifact open  | `adw:artifact` | the opened artifact's content is painted          |

Also pinned: the bound on initially rendered entry nodes (A1), the
reachability of every entry (A2), and the supersession behaviour (B1/B2). Not
pinned: internal helper signatures, template/CSS/JS structure, or the choice
of windowing vs. paging vs. expand-on-demand.

## Definition of Done

1. A1, A2, A3, B1, B2 and C1 are covered by automated tests (server-rendered
   markup and DOM node-count assertions via FastAPI `TestClient`; JS-level
   supersession and measure semantics via the project's existing
   client-behaviour test approach — no browser automation). Guideline: roughly
   12–18 new tests across A, B and C.
2. A4 and C2 are performed manually per `docs/gui-response-time.md`, and their
   read-off values are recorded in that guide as completed measurements, each
   ≤ 2000 ms. The guide documents all three measures (C3).
3. No file listed under Non-goals is modified; any bug discovered in an
   off-limits file is written up as a finding in the report, not fixed in the
   diff.
4. No new runtime dependency and no third-party frontend asset (E5/E7); the
   web stack remains the optional `adw[gui]` extra.
5. Existing behaviour of the trace, timeline, artifacts, raw and diff views
   remains available; no existing entry or payload becomes unreachable.
6. Gates green: `uv run ruff check .` and `uv run pytest -x -q`.

## Deferred (deliberately not built)

Hardening or extension ideas beyond the above — including findings from the
Codex review rounds — belong here, not in the acceptance criteria:

- Wall-clock performance assertions in automated tests (CI timing is flaky and
  browser-dependent); the automated half proves the DOM bound (A1–A3), the
  wall-clock ≤ 2 s evidence is the manual A4/C2 per the guide, by decision of
  the issue.
- Cancellation as a separately exposed product state or interface; the
  required behaviour is only that obsolete results have no observable effect —
  the internal cancellation strategy is an implementation choice.
- A pinned bound on initially rendered artifact bytes; C2 fixes the observable
  outcome (≤ 2 s on ≥ 2 MB), the display mechanism stays free.
- Search, jump indexes, or navigation controls beyond what is necessary to
  make every entry reachable.
- Extending the bound/measure approach to further views beyond what A1/A2
  already require (e.g. Raw or Timeline internals).
- Persistent latency records, telemetry, performance dashboards or alerts; the
  User Timing measures plus the guide are sufficient.
- Retention, `adw runs list`/`prune`, gzip, the `trace:` config key, i18n —
  the next and final run.
