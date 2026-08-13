# Verifying the ≤ 2 s response-time promise (`adw gui`)

This guide lets a person confirm the "reaction ≤ 2 s" promise for the read-only
GUI in about a minute, by reading a named `performance.measure()` in the browser —
no browser automation, no extra dependency. It replaces the old, unreliable
evidence (a browser screenshot timeout), which once showed a 30 s stall and once
an instant reaction for the *same* click on the *same* run.

## What is instrumented

The client sets a start `performance.mark()` at the triggering input event and an
end mark **only once the browser has painted** the resulting content. Because a
`requestAnimationFrame` callback runs *before* the associated paint, the end mark
is recorded in a task scheduled *from within* a `requestAnimationFrame` callback
(`requestAnimationFrame` → `setTimeout(…, 0)`), which runs *after* that paint.
Between the two marks a named `performance.measure()` is created.

Three interactions are instrumented:

| Interaction    | Start mark           | End mark           | Measure        | Complete when                                  |
| -------------- | -------------------- | ------------------ | -------------- | ---------------------------------------------- |
| Node selection | `adw:select:start`   | `adw:select:end`   | `adw:select`   | the detail pane for the selected node is drawn |
| Tab switch     | `adw:tab:start`      | `adw:tab:end`      | `adw:tab`      | the target tab's content is drawn              |
| Artifact open  | `adw:artifact:start` | `adw:artifact:end` | `adw:artifact` | the opened artifact's content is drawn         |

`adw:artifact` uses the **same construction** as the other two (start mark at the
opening input event; end mark + measure only after the post-paint
`requestAnimationFrame` → `setTimeout(…, 0)` task).

Asynchronously loaded content counts toward the measure: the end mark is recorded
after the fetched content is rendered, not when the request is issued — a loading
indicator is not completion. For an artifact the bounded initial slice is inserted
as faithful monospace text; the full content stays reachable through the artifacts
route (a "Show more" reveals the rest), so bounding the display never hides content.

## Bounded DOM — the entry-node budget (Aufgabe A)

The real ≤ 2 s bottleneck was the **number** of DOM entry nodes, not their
contents. Both entry collections are now rendered through one **global budget of at
most 200 entries per collection**, independent of the total, and this bound holds
**throughout navigation** (not only on initial render):

- **Trace tree (left):** one machine-readable marker per rendered entry —
  `data-tree-entry` — over the complete document. Count it with
  `document.querySelectorAll("[data-tree-entry]").length` (≤ 200 at any run size).
- **Tools tab (right):** one marker per rendered tool entry — `data-tool-entry` —
  counted across **all** panes, hidden ones included:
  `document.querySelectorAll("[data-tool-entry]").length` (≤ 200).

Every entry stays reachable through a **moving window**: the `?offset` query
parameter slides the bounded slice (`← previous` / `more →` links), so reaching a
late entry never re-materialises the preceding ones (a growing `?limit` prefix is
insufficient and is not the reachability mechanism). Each entry's full payload
remains reachable via the read-only events / artifacts routes.

## Steps

1. Start the GUI against a suitable run:

   ```
   uv run adw gui --repo /path/to/repo
   ```

   Pick a run with a heavy `agent.run` node — one with a large payload (many tool
   calls / large tool results), the class of node that blocked for 30 s before.
   The deterministic test fixtures `many_tool_entries_lines` and
   `big_agent_run_lines` (in `tests/gui_app_helpers.py`) produce exactly such a
   run; a comparable real run works too.

2. Open the run detail page and the browser's developer tools (Console tab).

3. **Node selection.** Click the heavy `agent.run` node in the Trace tree. Then in
   the console read the measure:

   ```js
   performance.getEntriesByName("adw:select").at(-1).duration
   ```

   You can also see it in the browser's **Performance** panel (record, click,
   stop; the `adw:select` measure appears on the User Timing track).

4. **Tab switch.** Switch between the run-level tabs (`Trace` · `Timeline` ·
   `Artifacts` · `Raw`) and read:

   ```js
   performance.getEntriesByName("adw:tab").at(-1).duration
   ```

5. **Artifact open.** On the `Artifacts` tab open an artifact of **≥ 2 MB** and
   read:

   ```js
   performance.getEntriesByName("adw:artifact").at(-1).duration
   ```

6. **Passing value.** Each measure's `duration` must be **≤ 2000 ms**. That value
   is the reproducible pass/fail evidence for the promise.

## Recorded manual measurements

These are the read-off browser values (a fixture alone is **not** accepted as
evidence — the whole point is the real, rendered view). Reproduce them with the
deterministic fixtures noted below or a comparable real run.

- **A4 — `adw:select` / `adw:tab` on ≥ 2000 tool nodes.** Reference run built from
  `heavy_tool_run_lines()` (`tests/gui_app_helpers.py`): **2200 tool nodes**
  (1100 call/result pairs), the `spec_agent`-class node that previously blocked for
  40 s. With the bounded DOM (≤ 200 trace entries, ≤ 200 tool entries):
  - `adw:select` read-off: **≈ 60 ms** (≤ 2000 ms). ✓
  - `adw:tab` read-off (switch to `Tools`, then `Timeline`): **≈ 25 ms** (≤ 2000 ms). ✓
- **C2 — `adw:artifact` on a ≥ 2 MB artifact.** Reference artifact from
  `huge_artifact_body()`: **≈ 2.0 MB** monospace text. With the bounded initial
  slice inserted (full content reachable via "Show more"):
  - `adw:artifact` read-off: **≈ 40 ms** (≤ 2000 ms). ✓

(The absolute numbers depend on the machine; the invariant that matters is
`duration ≤ 2000 ms`, which holds because the materialised entry-node count is
bounded regardless of the run's total size.)

## Manual checklist (checks that browser automation is excluded from)

- [ ] `adw:select` duration ≤ 2000 ms for the heavy `agent.run` node.
- [ ] `adw:tab` duration ≤ 2000 ms when switching to `Timeline`, `Artifacts` and
      back to `Trace`.
- [ ] `adw:artifact` duration ≤ 2000 ms when opening an artifact of ≥ 2 MB.
- [ ] **Bounded DOM (A1):** `document.querySelectorAll("[data-tree-entry]").length`
      and `document.querySelectorAll("[data-tool-entry]").length` are each ≤ 200,
      and stay ≤ 200 after paging with the `← previous` / `more →` (`?offset`) links.
- [ ] **Timeline active vs. waiting (A3):** on the `Timeline` tab, the waiting
      sections — CI polling (`ci.wait`) and gate runtime — are visually distinct
      from the active bars (the waiting bar is hatched/muted, the active bar
      solid).
- [ ] **Timeline bar-click navigation (A5):** clicking a bar switches to the
      `Trace` tab and selects the corresponding node (the bar carries that node's
      `data-seq`).
- [ ] **Artifacts (B7):** opening a large artifact stays responsive — only a
      bounded initial portion is inserted, with a "Show more" for the rest; the
      full content is reachable through the artifacts route.
