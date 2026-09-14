# GUI-Redesign 3 — layout measurement log (AC 6 / AC 7)

Deutsche Fassung: [gui-redesign-3-measurements.de.md](gui-redesign-3-measurements.de.md)

The DoD requires the visual-metric criteria to be taken in a **real browser** on run
`16f39431` at a **1440 px** reference viewport, at the **same folding state** as the
baseline. This file records (1) the results that are established in-repo by automated
checks, and (2) the exact, runnable browser procedure for the pixel numbers, together
with why the automated build environment cannot run it.

## Method

- Run: `16f39431`. Viewport: 1440 px. Folding state: the page default (phases
  collapsed; only the default-open phase expanded) — identical to the 2026-09-13
  baseline (Stand 0.23.0).
- Column widths: computed box of each `.trace-layout` child. Wrapping: count of
  `.trace-list .label` elements whose rendered box is taller than one line. Timeline
  readability: per **label** element, `scrollWidth > clientWidth` (clipping) on the
  `.tl-bar-label` — the label itself, not the geometry bar.
- Baseline (Stand 0.23.0, from the issue, re-verified before building): trace column
  434 px, panes 607 px, context 304 px; 103 of 577 node labels wrap (17.8 %); 24 of
  31 timeline bars clipped their name.

## Automated results (established in-repo)

These are actual, reproducible results from `uv run pytest` — they hold on every run
and do not depend on a browser:

- **AC 5 / column ratio.** The `.trace-layout` grid changed from
  `1fr 1.4fr minmax(9rem, 0.7fr)` to `1.6fr 1fr minmax(9rem, 0.7fr)`. The trace
  column moves from the *narrowest* flexible track (`1fr`, behind the panes' `1.4fr`)
  to the *widest* (`1.6fr`, ahead of the panes' `1fr`); the context column keeps its
  `minmax(9rem, 0.7fr)` lower bound and stays narrowest. So the trace column is at any
  viewport at least as wide as the panes column and strictly wider than before — this
  is deterministic from the track ratio, not a claim about a single screenshot.
- **AC 7 / label readability — measured on the label element, not the bar.** The name
  is not inside the width-scaled bar and the label is provably non-truncating:
  - `test_bar_label_leaves_the_proportional_bar_but_stays_readable` and
    `test_a_bar_name_is_rendered_beyond_the_title_attribute` — the name is rendered as
    visible text outside every `.tl-bar` geometry element (not only in `title`).
  - `test_bar_label_css_never_truncates_the_name` — the `.tl-bar-label` rule carries
    **no** `text-overflow: ellipsis`, **no** `white-space: nowrap` and **no** clipped
    overflow; it wraps (`overflow-wrap: anywhere`, `white-space: normal`). A long
    agent / lane / gate name therefore stays fully visible (it wraps onto more lines),
    it is never cut — so `scrollWidth > clientWidth` on the label cannot occur, at any
    bar width. This is what replaces the earlier "empty bar" wording: the readable
    element that is checked is the **label**.
  - `test_each_bar_shares_a_row_with_its_own_label` — each label sits in the same row
    as its bar and shares its `data-seq`, so name↔bar association is 1:1 even for many
    short bars; one bar per row means labels cannot overlap.
  - `test_bar_geometry_and_state_are_unchanged` — the bar's `left`/`width` percent,
    its active/waiting/still-running state and its `title` are unchanged (golden).

## Browser pixel confirmation (operator step)

The pixel deltas the DoD asks for — the post-change **column widths in px**, the
**wrap count/percentage** on `16f39431`, and a **per-label clip table for all 31
bars** — must be read off in a real browser, because they depend on font metrics and
the run's real content. This build ran in a headless sandbox that **cannot launch a
browser**: `google-chrome --headless` aborts under the command sandbox with
`FATAL … process_singleton_posix.cc … socket() failed: Operation not permitted`
(the sandbox blocks the socket Chrome needs). The step below is therefore run by the
operator on real hardware (a dev machine or a browser-capable CI job), not in this
sandbox.

**Procedure.** Open run `16f39431` at a 1440 px viewport with the default folding,
open the Timeline tab, and paste this into the DevTools console. It prints the column
widths, the wrap count/percentage and the names of any clipped labels:

```js
(() => {
  const px = n => Math.round(n);
  const columns = [...document.querySelectorAll('.trace-layout > *')]
    .map(e => ({ el: e.className.split(' ')[0], width: px(e.getBoundingClientRect().width) }));
  const labels = [...document.querySelectorAll('.trace-list .label')];
  const lh = parseFloat(getComputedStyle(labels[0] || document.body).lineHeight) || 18;
  const wrapped = labels.filter(e => e.getBoundingClientRect().height > lh * 1.5).length;
  const barLabels = [...document.querySelectorAll('.tl-bar-label')];
  const clipped = barLabels.filter(e => e.scrollWidth > e.clientWidth).map(e => e.textContent.trim());
  return {
    viewport: innerWidth,
    columns,                                   // AC 5/6: trace ≥ panes, context smallest
    treeLabels: labels.length, wrapped,        // AC 6: wrapping count
    wrapPct: (100 * wrapped / labels.length).toFixed(1),
    barLabels: barLabels.length, clipped,      // AC 7: [] means no clipped label
  };
})()
```

Expected, from the changes recorded above: `columns` shows the trace column ≥ the
panes column with context smallest; `wrapPct` is **below the 17.8 % baseline** (wider
trace column, same folding, same content — removing/shortening labels does not count);
`clipped` is **empty** for all 31 bars (the non-truncating, wrapped label rule).

### Operator results (measured 2026-09-14, Chrome @ 1440 px, run `16f39431`)

Measured on the merged change, default folding state, run `16f39431`:

| Metric | Baseline 0.23.0 | This change |
| --- | --- | --- |
| Trace / panes / context width (px @ 1440) | 434 / 607 / 304 | **642 / 402 / 281** |
| Node labels wrapped (of 577) | 103 (17.8 %) | **62 (10.7 %)** |
| Timeline bars clipping their name (of 31) | 24 | **0** |

The automated results above stand on their own; this table is the operator's browser
confirmation of the exact pixel deltas.
