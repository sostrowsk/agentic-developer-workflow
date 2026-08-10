# Plan — Run detail: Tools responsiveness, Diff tab, Raw tab

Authoritative on conflict: `docs/GUI-SPEC.md`, especially §5 (snapshots and step
diffs), §7.2 (views), §7.4 (API), §8 (security). This plan implements
`.adw/spec.md` and builds strictly against `.adw/contract.yaml`. **Single-lane:**
there is exactly one workstream, **backend** — the GUI web layer
(`adw/gui/app.py`, the Jinja2 templates, and the packaged vanilla assets
`static/app.css`, `static/app.js`) belongs wholly to this one lane; no separate
frontend lane exists. This run completes the run-detail view with three tasks
(A/B/C) plus a language fix (D); it is completion of an existing view, not a
redesign. The mechanism for bounded DOM rendering (windowing, paging,
virtualization, lazy loading) remains an implementation choice and is not pinned.

## Guardrails

- **Web layer only.** Changes touch exclusively `adw/gui/app.py`, the templates
  (`adw/gui/templates/*.html`), the own assets (`adw/gui/static/*`) and the
  associated tests/fixtures. `adw/events.py`, `adw/snapshots.py`,
  `adw/gui/reader.py`, `adw/gui/model.py` and the orchestrator stay
  **unchanged**. If a task turns out not to be solvable without such a change,
  that is a **finding for the report**, not a silent scope widening.
- **Strictly read-only.** No GUI code path writes to `state.json`, the repo or
  the event log; no write HTTP route. The diff endpoint runs `git diff`
  **reading only** — no worktree switch, no ref created, updated or deleted.
  Run data is read only below the resolved `.adw/runs/<run_id>/` directory,
  under the same containment / `RUN_ID_RE` / slug backstops as today, applied
  before any run is read or git is invoked.
- **New HTTP route only where task B strictly requires it** — the diff
  endpoint — and read-only. No other new route.
- **No new runtime dependency, no third-party frontend asset** (E5): vanilla
  JS, own CSS, system fonts; no CDN, no node toolchain, no browser-automation
  dependency. The web stack stays the optional extra `adw[gui]` (E7).
- **No new views beyond the specified tabs, no navigation rebuild.** The Diff
  tab lives in the existing detail pane; the Raw tab lives at run level in the
  existing detail view. No redesign, no timeline, no artifacts tab, no i18n,
  no run-list/prune, no retention, no gzip, no config keys, no caching or
  precomputation.
- **E8 applies to the display, not the log.** Trimming / collapsing / paged
  loading in the display is explicitly permitted and is **not** a violation of
  "no payload truncation" (that concerns the log). A finding that reads task A,
  B7 or C4 as such a violation is rejected with a reason. Every logged payload
  stays fully reachable.
- Missing values are treated as missing, never reinterpreted as a displayed
  `0` or `null`.
- Real gates (E3): `uv run ruff check .` and `uv run pytest -x -q`. `flake8`,
  `isort`, `black` appear nowhere — not in dependencies, config, scripts or
  validation commands.

## Starting point (verified in the code)

- **Detail delivery (`app.py`).** `_run_detail` builds `run`/`phases`/`tree`/
  `problems` from the read layer; `_serialize` embeds each node's payloads and
  recursively its `children`. `run_detail.html` renders the tree and the tabbed
  detail pane; `app.js` drives selection and the SSE live tail. The Tools tab
  and the trace tree are where the ≥ 1500-entry node count must stay
  responsive (task A).
- **Tabs (`run_detail.html:44-46`).** The detail pane already has switchable
  tabs **Prompt / Antwort / Tools** (`data-tab` buttons + `data-tab-panel`
  sections). The label reads **"Antwort"** — task D renames it to **"Answer"**.
  No Diff tab and no Raw tab exist yet; this run adds both.
- **Snapshots (read-only inputs).** `snapshots.py` emits one `snapshot` point
  event per step with payload `lane` / `tree` / `ref` / `label`, where `ref` is
  `refs/adw/<run_id>/<seq>` and `<seq>` is unique per run. These events,
  already in the log this run reads, are the sole source both for the
  endpoint's ref allowlist (B2) and for the Diff tab's bracketing pair (B5).
  `snapshots.py` is **not** modified — the GUI only reads the events it
  produced.
- **git invocation pattern.** `snapshots.py:_run_git` shows the orchestrator's
  invocation to mirror (B4): `["git", "-C", cwd, "-c",
  "core.hooksPath=/dev/null", *args]`, `capture_output=True`, `text=True`,
  `timeout=…`, `env=safe_env(...)`, no shell. The diff endpoint reuses this
  shape (read-only subcommands only).
- **Containment backstops (`app.py`).** `require_run` already enforces
  `RUN_ID_RE`, slug resolution and symlink containment and answers 404 on the
  error cases; the diff endpoint reuses it before doing anything else.
- **Fixtures/tests.** `tests/gui_app_helpers.py` provides the builders (`rec`,
  `write_run`, …) and the `home` fixture; the app is exercised via
  `create_app(repos=…)` with FastAPI's `TestClient` against fixture logs /
  temp repos. New tests build on these helpers.

## Workstream: backend

### 1. Build deterministic large-run and diff fixtures

- Extend the existing GUI test-fixture support with deterministic data for
  (Definition of Done §3):
  - an `agent.run` with at least **1500 tool entries** (calls and results
    combined, small contents) — tasks A1–A3;
  - a run log with at least **3000 events**, including an unknown event type,
    a `seq` gap and a broken line reported by the existing reader — tasks
    C1–C4;
  - a temporary git repository whose two snapshot refs differ in at least
    **100 files** and at least **1500 changed lines**, plus a case with a
    changed **binary** file — tasks B1/B7;
  - a run with at least **two bracketed nodes in one lane**, plus snapshot
    events in another lane that must not affect pairing — task B8;
  - nodes missing either or both same-lane snapshot boundaries — tasks B6/B8.
- Keep large fixtures generated by the helpers rather than checked in as bulky
  artifacts.
- Reuse these fixtures for endpoint tests, template-behavior tests,
  reachability tests and the manual responsiveness evidence.

### 2. Derive snapshot brackets for detail nodes

- In the GUI app layer, derive the snapshot ref allowlist exclusively from
  `snapshot` events in the requested run's event log.
- For each detail node, identify its first event (lowest `seq`) and last event
  (highest `seq`).
- Compute a bracket only within the node's lane (B5):
  - `from` is the same-lane `snapshot` event with the **highest `seq` at or
    before** the node's first event;
  - `to` is the same-lane `snapshot` event with the **lowest `seq` at or
    after** the node's last event.
- Snapshot events of other lanes are never used; because `seq` is unique, the
  rule is deterministic.
- Treat a node as unbracketed if it has no lane or either boundary is absent
  (B8 → B6). Never substitute a snapshot from another lane or synthesize a
  nearest pair.
- Expose only the derived bracket information the detail template needs; no
  internal model or reader API is added or pinned.

Tests:
- Exact pairing for at least two bracketed nodes in one lane — each resolves
  its **own** `from`/`to` pair (B8).
- Snapshots of another lane are never chosen.
- The inclusive boundary rule at the node's first and last `seq`.
- A missing before- or after-boundary yields the unbracketed state.

### 3. Add the read-only diff endpoint

- Add `GET /api/runs/{repo}/{run_id}/diff?from=REF&to=REF` in the existing
  FastAPI app.
- Resolve repo and run through the existing slug / run-ID / containment checks
  (`require_run`) before anything else.
- Require one non-empty, well-formed `from` and `to`; missing or malformed
  parameters yield **400 or 404**, never 5xx (B3).
- **Security (B2, non-negotiable):** before executing git, load this run's
  `snapshot` events and require each supplied value to equal, exactly, a ref
  from that allowlist (`refs/adw/<run_id>/<seq>`). A pattern match alone is
  insufficient. Any ref not in the list — arbitrary git refs, revisions,
  ranges, option-like strings, refs of another run — is rejected with 400 or
  404, never 5xx, and **without executing git**.
- For allowed refs, run git like the orchestrator (B4): no shell, separate
  arguments, a timeout, `safe_env()`, `-c core.hooksPath=/dev/null`. Reads
  only — no worktree switch, no ref mutation.
- Obtain the unified patch and the `git diff --numstat` data, both over the
  two validated refs, in git's reported order. Convert counts to non-negative
  integers; binary-file `-` counts become JSON `null`.
- Return exactly the contract shape (B1):
  `{"files": [{"path": string, "additions": int|null, "deletions": int|null}], "patch": string}`;
  an empty diff is HTTP 200 with `{"files": [], "patch": ""}`.
- The 400/404-never-5xx guarantee covers exactly the pinned pre-execution
  validation failures (B2/B3). A genuine execution failure after two valid
  refs reach git (e.g. a timeout) follows the app's existing error handling;
  no additional client-facing status is defined for it.

Tests:
- Two real snapshot refs in a temp repo return the correct patch and per-file
  +/- counts in the exact B1 schema and git order; one case has a **binary**
  changed file with `null`/`null`; the empty-diff case returns
  `{"files": [], "patch": ""}`.
- Missing, empty, malformed, option-like, arbitrary, range, foreign-run and
  unknown refs each return 400 or 404, never 5xx (B2/B3).
- A git-invocation spy proves **no git execution happens on rejection**.
- Allowed execution uses no shell, disabled hooks, `safe_env()` and a timeout,
  and performs no ref or worktree mutation.
- Invalid repo slugs, run IDs and absent runs keep the existing controlled
  containment behavior.

### 4. Add the node-level Diff tab

- Show a **Diff** tab for nodes with a complete bracket from step 2
  (build-lane agent runs, gate iterations, the RED stage) (B5).
- On activation, request exactly that node's derived `from`/`to` pair from the
  diff endpoint.
- Render the changed-file list in response order with +/- counts (binary
  counts shown without inventing numbers), and the unified patch below it.
  Any highlighting uses first-party markup/CSS/JS only; simple is fine (E5).
- For an unbracketed node (authoring and review agents, phases, rounds,
  boundary-missing nodes), either omit the Diff tab or show a clear
  "no snapshot exists for this step" state. No error, no unexplained empty
  area (B6).
- Render a large patch in bounded windows/pages so tab activation responds
  visibly within **2 seconds** against the ≥ 100-file / ≥ 1500-line fixture,
  while the full patch stays reachable (B7).

Tests:
- Bracketed nodes expose the Diff behavior with their exact derived pair;
  multiple bracketed nodes in one lane request their own pairs (B5/B8).
- Unbracketed nodes show the specified unavailable state — never an error or
  unexplained empty area (B6).
- File list, counts, binary state, empty-diff state, patch, loading state and
  controlled endpoint-error state render intelligibly.
- Against the B7 fixture: bounded initial materialization; traversing the
  mechanism's pages/windows reaches the full patch. The 2-second threshold
  itself is evidenced by the manual browser check (below).

### 5. Make the Tools tab and the trace tree responsive by node count

- Replace eager materialization of all tool entries in the Tools pane with a
  bounded, lazy, paged or windowed first-party mechanism (A1); apply the same
  principle to the tool entries in the trace tree (A3).
- Selecting the Tools tab for the ≥ 1500-entry fixture shows actionable
  content within **2 seconds**; not all entries need to be in the DOM at once.
- Preserve chronological order and a deterministic path to **every**
  call/result entry and its complete content (A2, E8); no payload is
  truncated or discarded.
- Keep selection, tab switching and loading/error feedback usable while
  further entries are materialized.

Tests:
- Initial Tools-pane and trace-tree materialization is bounded for the
  ≥ 1500-entry fixture (observable effect of the chosen mechanism).
- The union of paged/lazy responses (or the mechanism's reachability path)
  contains **every** tool entry in order with complete content — this also
  evidences A2.
- Calls and results remain distinguishable and selectable.
- The mechanism's loading and terminal states are stable for an empty list and
  the large list.

### 6. Add the run-level Raw tab

- Add a **Raw** tab at run level over the run's complete event log (C1).
- Render every event generically enough that unknown `type` values remain
  visible rather than dropped.
- Provide at least a `type` filter and free-text search over the serialized
  payload (C2), each with a clear empty-result state.
- Surface the reader's reported problems (`seq` gaps, broken lines) inside
  the Raw tab, not only in the detail pane (C3).
- Bound/window the list so opening the tab and applying a filter each respond
  visibly within **2 seconds** against the ≥ 3000-event fixture; every
  matching event and its complete payload stay reachable (C4).

Tests:
- Known and unknown event types appear in Raw (C1).
- Type filtering, payload free-text filtering, clearing filters and no-match
  behavior each work (C2); each required filter is tested independently.
- Reader-reported `seq` gaps and broken lines are visible in Raw (C3).
- Against the ≥ 3000-event fixture: bounded initial and filtered
  materialization; the mechanism's path reaches every matching event with
  complete payload (C4).

### 7. Restore English-only run-detail labels

- Rename the tab labeled **"Antwort"** (`run_detail.html:45`) to **"Answer"**
  (D1, E9).
- Check the affected templates and client-generated labels so no
  mixed-language label remains in the run-detail view.

Tests:
- The agent-run answer tab is labeled "Answer"; "Antwort" and any other
  mixed-language run-detail label are absent.

### 8. Verification and handoff

- Run `uv run ruff check .` and `uv run pytest -x -q` (E3).
- Perform and document the reproducible **manual browser check** against the
  named fixtures (see "Responsiveness evidence" below): Tools tab and trace
  tree at ≥ 1500 tool entries; Diff tab at ≥ 100 files / ≥ 1500 changed lines;
  Raw tab plus filter at ≥ 3000 events.
- Confirm the fixture cardinalities, full-content reachability, the read-only
  property, and that no forbidden file, dependency, third-party asset or
  validation command changed.
- Keep the new automated suite roughly within the guideline of **18–25 tests**
  across A, B and C; add no tests unrelated to the acceptance criteria.

## Responsiveness evidence (shared)

The 2-second thresholds (A1/A3, B7, C4) are evidenced at the **browser** level
by a documented, reproducible **manual** check against the named fixtures:
measurement **begins with the click** on the node, tab or filter and **ends
when the visible reaction** defined in A1 appears; total ≤ 2 s. The procedure
and result go into the report. In addition, automated tests assert the
**observable effect of the chosen mechanism** (initial delivery does not
inline all entries; the union of paged responses / the reachability path
contains every entry). **No browser-automation dependency is introduced.**

## Test-scope guardrail

Guideline (not a hard gate): roughly **18–25 new tests** across A, B and C.
Coverage of the acceptance criteria is what matters, not the exact count;
markedly more is a signal of scope drift.

## Definition of Done

1. Server-observable behavior — the diff route, its exact JSON schema (B1) and
   its rejections (B2/B3), the snapshot pairing (B5/B8), paged/windowed
   delivery and the Raw filters — is covered by tests against fixture logs /
   temp repos via FastAPI `TestClient`.
2. The responsiveness thresholds (A1/A3, B7, C4) are evidenced by the
   documented manual browser check against the named fixtures, plus automated
   tests asserting the observable effect of the chosen mechanism. No
   browser-automation dependency is introduced. The manual procedure and its
   result are in the report.
3. The fixtures embody the stated sizes (≥ 1500 tool entries; ≥ 100 files /
   ≥ 1500 changed lines; ≥ 3000 events; ≥ 2 bracketed nodes in one lane).
4. The diff endpoint's rejection of foreign/unknown/malformed refs (B2/B3) is
   tested, including that no git execution happens on rejection.
5. The step diff between two real snapshot refs shows the correct patch and
   correct per-file +/- counts in the exact B1 schema (temp repo test),
   including the binary-file `null` case and the empty-diff case.
6. Snapshot pairing follows the inclusive same-lane `seq` rule for every node,
   including multiple bracketed nodes in one lane and missing-boundary cases.
7. No change to `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
   `adw/gui/model.py` or the orchestrator; the GUI remains read-only; allowed
   git execution uses disabled hooks, `safe_env()`, a timeout, no shell, and
   no ref or worktree mutation.
8. No new runtime dependency and no third-party frontend asset are introduced;
   the run-detail view is uniformly English and uses "Answer" (D1).
9. Real gates green (E3): `uv run ruff check .` and `uv run pytest -x -q`.
   `flake8`, `isort` and `black` are not added to dependencies, configuration,
   scripts or validation commands.
10. Guideline (not a hard gate): roughly 18–25 new tests across A, B and C.

## Deferred (deliberately not built)

Hardening or extension ideas that are defensible but out of proportion to this
run go here, not into acceptance criteria. Findings from review rounds that would
re-introduce a deferred or pre-decided point are rejected with a reason, not
implemented.

- **Diff for non-bracketed nodes** by synthesizing a nearest-snapshot pair — the
  issue explicitly accepts "no snapshot for this step" for those nodes (AC-B6).
- **Diffing arbitrary refs / ranges / revisions**, or a general git-diff surface —
  deliberately excluded by the allowlist security model (AC-B2).
- **Response-size caps or server-side pagination of the diff/Raw payload**
  beyond what responsiveness needs — E8 governs display, not the log; the
  criterion is node/event count, not bytes.
- **Rate limiting / auth / non-loopback hardening** of the new endpoint — the GUI
  binds to loopback per GUI-SPEC §8; unchanged here.
- **Caching or precomputation of diffs**; **filesystem-watch** instead of polling.
- **Timeline, artifacts tab, i18n / language switch, `adw runs list|prune`,
  retention, gzip, `trace:` config key** — assigned to later runs by the scope
  ceiling.
