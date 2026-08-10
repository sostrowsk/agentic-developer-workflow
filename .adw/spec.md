# Specification — Run detail: Tools responsiveness, Diff tab, Raw tab

Source: `.adw/issue.md`. Authoritative reference on conflict: `docs/GUI-SPEC.md`,
especially §5 (snapshots and step diffs), §7.2 (views), §7.4 (API), §8
(security). This closes the last of the four debug questions — "what did this
step change in the code?" — and adds the always-works fallback view.

## Goal

Complete the run-detail view of `adw gui` so that:

1. Selecting an `agent.run` node with very many tool entries and switching to the
   **Tools** tab no longer blocks the interface — the bottleneck is DOM node
   count, not payload size (task A, carried over from the polish run).
2. A **Diff** endpoint and a **Diff** tab answer "what did this step change?"
   for nodes bracketed by two snapshots, using only refs that provably belong to
   this run (task B).
3. A run-level **Raw** tab shows the event log as a filterable list — the
   fallback that works even for event types the GUI does not model (task C).

Product behavior only: observable interface, response format, data states. The
mechanism (windowed rendering, paging, virtualization) is an implementation
choice and is deliberately not pinned.

## Scope

- Run-detail server routes and templates/JS/CSS under `adw/gui/` (app layer).
- The new diff endpoint `GET /api/runs/{repo}/{run_id}/diff?from=REF&to=REF`.
- The Diff tab in the detail pane and the Raw tab at run level.
- Windowed/lazy rendering of the Tools tab, the trace tree, the Diff patch and
  the Raw list so that large inputs stay responsive.
- Fixtures needed to exercise the node-count and event-count criteria.
- Renaming the tab "Antwort" back to "Answer" so the UI is uniformly English (E9).

## Non-goals

Explicit scope ceiling from the issue — NOT built in this run:

- No timeline, no artifacts tab (next run).
- No i18n, no language switch, no `adw runs list`, no `adw runs prune`, no
  retention, no gzip, no `trace:` config key (run after next).
- No change to `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
  `adw/gui/model.py`, or the orchestrator. Orphan spans (E2) and the `run` span
  boundary (E1) stay as they are.
- No write path: the GUI stays strictly read-only. The diff endpoint runs
  `git diff` — reading only, no worktree switch, no ref set or delete.
- No new runtime dependencies, no third-party frontend asset (E5): vanilla JS,
  own CSS, system fonts.
- No redesign of the existing views.
- No requirement on how the highlighting looks; simple is fine (E5).

## Acceptance criteria

Each criterion traces to a concrete task in the issue and is externally
observable. The interface surface pinned by the contract is the diff route
(parameters, response format, rejection of foreign refs), the presence and
behavior of the Diff and Raw tabs, and the responsiveness promise stated as a
node/event-count criterion. Internal helper signatures, template/CSS/JS
structure and the concrete rendering mechanism are NOT pinned.

### A — Tools tab and trace tree responsive by node count

- **AC-A1** — An `agent.run` node with at least **1500 tool entries** (calls and
  results combined, small contents) responds visibly within **2 seconds** when
  the Tools tab is selected. "Visibly responds" means the tab shows content the
  user can act on; not all entries need to be in the DOM at once.
- **AC-A2** — The user can reach **every** tool entry of such a node (e.g. by
  paging or scrolling that loads further entries); full content of each entry
  remains reachable (E8 — collapsing/paging the display is allowed, no payload
  is dropped).
- **AC-A3** — The trace tree on the left, which lists the same entries, holds the
  same responsiveness under the same input.

### B — Diff endpoint and Diff tab

- **AC-B1** — `GET /api/runs/{repo}/{run_id}/diff?from=REF&to=REF` returns, for
  two snapshot refs of this run, HTTP 200 with a JSON object of exactly this
  shape:

  ```json
  {
    "files": [{"path": "<repo-relative path>", "additions": 0, "deletions": 0}],
    "patch": "<unified git diff text>"
  }
  ```

  `files` holds one object per changed file, in the order git reports them;
  `path` is a string, `additions` and `deletions` are each a **non-negative
  integer or `null`** — `null` exactly for binary files, mirroring
  `git diff --numstat`. `patch` is the unified patch as a single string. An
  empty diff (no changes between the two refs) is HTTP 200 with
  `{"files": [], "patch": ""}`. Endpoint tests assert this exact schema,
  including one case with a binary changed file and its `null` counts.
- **AC-B2** (security, non-negotiable) — Both `from` and `to` are accepted
  **only** if the exact ref name appears in a `snapshot` event of **exactly this
  run** (`refs/adw/<run_id>/<seq>`). Validation is against the ref list drawn
  from this run's event log, not merely a pattern match. Any ref not in that
  list — arbitrary git refs, revisions, ranges, option-like strings, refs of
  another run — is rejected with **400 or 404**, never a 5xx, and **without
  executing git**.
- **AC-B3** — A missing or malformed `from`/`to` parameter yields **400 or 404**,
  never 5xx.
- **AC-B4** — When the endpoint does run git, it runs it like the orchestrator:
  `core.hooksPath=/dev/null`, `safe_env()`, a timeout, no shell. It reads only —
  no worktree switch, no ref created, updated or deleted.
- **AC-B5** — The detail pane shows a **Diff** tab for nodes bracketed by two
  snapshots (build-lane agent runs, gate iterations, the RED stage). The
  bracketing pair is determined observably from this run's event log: `from` is
  the `snapshot` event of the node's lane with the **highest `seq` at or
  before** the node's first event; `to` is the `snapshot` event of the same
  lane with the **lowest `seq` at or after** the node's last event. Snapshot
  events of other lanes are never used; because `seq` is unique, the rule is
  deterministic. The tab shows the per-file list with +/- counts and, below
  it, the patch. Any highlighting uses own means only (no third-party asset).
- **AC-B6** — A node **not** bracketed by two snapshots (authoring and review
  agents, phases, rounds) either has **no Diff tab** or a Diff tab that states
  clearly that no snapshot exists for this step. No error, no unexplained empty
  area.
- **AC-B7** — A large patch does not block the interface: against a fixture
  diff with at least **100 changed files** and at least **1500 changed lines**
  in total, the Diff tab responds visibly within **2 seconds** (same threshold
  and meaning as AC-A1). Windowed/paged/lazy display is allowed; the full
  patch stays reachable.
- **AC-B8** — If either boundary of the AC-B5 rule does not exist (no same-lane
  snapshot at or before the start, or at or after the end), the node counts as
  not bracketed and AC-B6 applies. With multiple bracketed nodes in the same
  lane (several agent runs, several gate iterations), each node's Diff tab
  requests exactly its own pair per the AC-B5 rule — verified against a
  fixture containing at least two bracketed nodes in one lane.

### C — Raw tab

- **AC-C1** — A run-level **Raw** tab shows the event log as a list, including
  events of a `type` the GUI does not model (rendered generically, not dropped).
- **AC-C2** — The Raw list can be filtered at least by `type` and by free-text
  search over the payload.
- **AC-C3** — Problems the reader reports for this log (`seq` gaps, broken lines)
  are visible in the Raw tab, not only in the detail pane.
- **AC-C4** — Against a fixture log with at least **3000 events**, selecting
  the Raw tab and applying a filter each respond visibly within **2 seconds**
  (same threshold and meaning as AC-A1). Not all events need to be in the DOM
  at once; every event stays reachable.

### D — Language consistency

- **AC-D1** — The interface is uniformly English (E9). The tab previously labeled
  "Antwort" reads **"Answer"**; no mixed-language labels remain in the run-detail
  view.

## Definition of Done

1. Server-observable behavior — routes, the diff endpoint's exact JSON schema
   (AC-B1) and its rejections (AC-B2/B3), the snapshot pairing (AC-B5/B8),
   paged/windowed delivery and filters — is covered by tests against a fixture
   log / temp repo via FastAPI `TestClient` (as the existing GUI tests do).
2. The responsiveness thresholds (AC-A1/A3, AC-B7, AC-C4) are evidenced at the
   browser level by a documented manual check against the named fixtures —
   measurement starts with the click on the node, tab or filter and ends when
   the visible reaction defined in AC-A1 appears — plus automated tests that
   assert the observable effect of the chosen mechanism (e.g. the initial
   delivery does not inline all entries; the union of paged responses contains
   every entry, which also evidences AC-A2 reachability). No browser-automation
   dependency is introduced.
3. The fixtures embody the stated sizes: at least 1500 tool entries with small
   contents (A), a diff with at least 100 files and 1500 changed lines (AC-B7),
   a log with at least 3000 events (AC-C4), and a run with at least two
   bracketed nodes in one lane (AC-B8).
4. The diff endpoint's rejection of foreign/unknown/malformed refs (AC-B2/B3) is
   tested, including that no git execution happens on rejection.
5. The step diff between two real snapshot refs shows the correct patch and
   correct per-file +/- counts in the exact AC-B1 schema (temp repo test).
6. No change to `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
   `adw/gui/model.py` or the orchestrator; the GUI remains read-only.
7. No new runtime dependency and no third-party frontend asset are introduced.
8. Real gates green (E3): `uv run ruff check .` and `uv run pytest -x -q`.
   `flake8`, `isort` and `black` are not added to dependencies, configuration,
   scripts or validation commands.
9. Guideline (not a hard gate): roughly **18–25 new tests** across A, B and C.

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
