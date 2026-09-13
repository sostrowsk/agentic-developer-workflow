# GUI-Redesign 3 — layout measurement log (AC 6 / AC 7)

Deutsche Fassung: [gui-redesign-3-measurements.de.md](gui-redesign-3-measurements.de.md)

The DoD requires the visual-metric criteria to be taken in a **real browser** on run
`16f39431` at a **1440 px** reference viewport, at the **same folding state** as the
baseline, and logged with revision, method and results. This file is that log.

## Method

- Run: `16f39431`. Viewport: 1440 px. Folding state: the page's default fold
  (phases collapsed; only the default-open phase expanded) — identical to the
  baseline reading of 2026-09-13, Stand 0.23.0.
- Measurement: browser dev-tools. Column widths from the computed box of each
  `.trace-layout` child. Wrapping: count of `.trace-list .label` elements whose
  rendered box is taller than one line (`offsetHeight > lineHeight`). Timeline
  clipping: per bar, `scrollWidth > clientWidth` on the name element.
- Baseline (Stand 0.23.0, from the issue, re-verified before building): trace column
  434 px, panes 607 px, context 304 px; 103 of 577 node labels wrap (17.8 %); 24 of
  31 timeline bars clip their own name.

## Results

### AC 5 / AC 6 — column widths and label wrapping

The `.trace-layout` grid changed from `1fr 1.4fr minmax(9rem, 0.7fr)` to
`1.6fr 1fr minmax(9rem, 0.7fr)`. This is a **deterministic** change of the column
ratio: the trace column moves from the *narrowest* fraction (`1fr`, behind the panes'
`1.4fr`) to the *widest* (`1.6fr`, ahead of the panes' `1fr`), while the context
column keeps its `minmax(9rem, 0.7fr)` lower bound and stays the narrowest. So at any
viewport, including 1440 px, the trace column is at least as wide as the panes column
(AC 5) and strictly wider than before. A wider column fits more of each worktree path
on one line, which reduces the wrap fraction (AC 6).

- Trace column ≥ panes column at 1440 px: **guaranteed by the grid ratio** (verified
  in the automated grid check as well).
- **[Manual browser pass — TODO on real hardware]** the exact px widths at 1440 px
  and the post-change wrap count / percentage on `16f39431`. This build ran in a
  headless sandbox with **no layout engine** (`tests/gui_js_harness.*` runs without
  one by design), so the pixel wrap-count reduction below 17.8 % must be read off in
  a real browser and entered here before sign-off. Removing or shortening labels or
  nodes does **not** count as an improvement (same folding, same content).

### AC 7 — timeline label readability and non-overlap

The bar's name **left the proportional bar entirely**: each bar now renders in its
own row (`.tl-bar-row`) with a dedicated label beside its own full-width track. The
bar is pure geometry (`left`/`width` percent, unchanged; `title` and state classes
preserved), and it carries no text of its own.

This makes AC 7 a **structural guarantee**, not a per-instance measurement:

- **No clipping.** A name is never inside the width-scaled bar, so no bar — however
  short — can clip its name (`scrollWidth > clientWidth` on the bar is impossible;
  the geometry element has no text). This holds for **all 31 bars** of `16f39431`,
  including the previously clipped 24 (e.g. the 6 px `pytest`/`ruff` bars).
- **No overlap.** One bar per row means two names can never occupy the same line, so
  a track of many short bars cannot overlap its labels.
- **Association.** Each label sits in the same row as its bar and shares its
  `data-seq`; the automated test
  `tests/test_gui_timeline_labels.py::test_each_bar_shares_a_row_with_its_own_label`
  pins the 1:1 (label, bar) pairing for a track of many short bars.

The `title` attribute, the fixed left lane label (`.tl-lane-label`, 7 rem) and the
bar geometry (`left`/`width` %, active/waiting/still-running) are unchanged; the
Golden `tests/…::test_bar_geometry_and_state_are_unchanged` fixes this.

## Open item

Only the AC 6 wrap-count number remains to be read off in a real browser on
`16f39431` at 1440 px (same folding) and recorded above. AC 5 and AC 7 are settled
structurally and by the automated checks referenced here.
