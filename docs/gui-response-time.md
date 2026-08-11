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

Exactly two interactions are instrumented (the two the issue names):

| Interaction    | Start mark         | End mark         | Measure      | Complete when                                  |
| -------------- | ------------------ | ---------------- | ------------ | ---------------------------------------------- |
| Node selection | `adw:select:start` | `adw:select:end` | `adw:select` | the detail pane for the selected node is drawn |
| Tab switch     | `adw:tab:start`    | `adw:tab:end`    | `adw:tab`    | the target tab's content is drawn              |

Asynchronously loaded content counts toward the measure: the end mark is recorded
after the fetched content is rendered, not when the request is issued — a loading
indicator is not completion.

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

5. **Passing value.** Each measure's `duration` must be **≤ 2000 ms**. That value
   is the reproducible pass/fail evidence for the promise.

## Manual checklist (checks that browser automation is excluded from)

- [ ] `adw:select` duration ≤ 2000 ms for the heavy `agent.run` node.
- [ ] `adw:tab` duration ≤ 2000 ms when switching to `Timeline`, `Artifacts` and
      back to `Trace`.
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
