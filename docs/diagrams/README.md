# Diagrams

**English** | [Deutsch](README.de.md)

Contract for the SVG sources of the doc diagrams (contributors only).

Each SVG here is used three ways:

1. **GitHub / Markdown:** the document references it as a plain image —
   `![Caption](diagrams/<doc>/<name>.svg)` (German files: `<name>.de.svg`).
2. **HTML export:** `scripts/build_docs.py` inlines the SVG and
   `docs/assets/diagram.js` makes it interactive.
3. **DOCX export:** the same SVG is converted to **EMF** with Inkscape and embedded
   at page width (16 cm).

Build: `python scripts/build_docs.py` (needs `pandoc` and `inkscape`) → `docs/export/`.

## Layout

```
docs/diagrams/
  workflow/<name>.svg   + <name>.de.svg   → docs/ADW-WORKFLOW(.de).md
  handbook/<name>.svg   + <name>.de.svg   → docs/ADW-HANDBOOK(.de).md
  spec/<name>.svg       + <name>.de.svg   → docs/ADW-SPEC(.de).md
```

EN and DE variants have the same geometry; only the text differs.

## Rules (the build enforces the first four)

- **No `<marker>`** — EMF loses arrowheads. Draw every arrowhead as a `<polygon>`.
- **No `<style>` element** — use presentation attributes (`fill=`, `stroke=`,
  `font-size=` …). `class="flow"` is fine (it only matters in HTML).
- **No `<filter>`** (shadows, blur) and **no `<foreignObject>`**.
- Root: `xmlns="http://www.w3.org/2000/svg"`, `viewBox` plus matching
  `width`/`height`, a `<title>` and a `<desc>`. First child: a white
  background `<rect>` covering the canvas.
- Fonts: `font-family="Liberation Sans, Arial, Helvetica, sans-serif"`,
  size ≥ 13 for body labels; code and YAML in `Liberation Mono, Consolas, monospace`. Wrap lines by hand with `<tspan x=… dy=…>`.
  Width 900–1000 user units, so 16 cm in Word stays readable.
- Palette (light; the HTML puts the SVG on a white card in dark mode too):
  deterministic code `#e0f2fe`/`#0369a1` · agent/AI `#ede9fe`/`#6d28d9` ·
  gate/check `#dcfce7`/`#15803d` · human/approval `#fef3c7`/`#b45309` ·
  escalation/error `#fee2e2`/`#b91c1c` · neutral `#f1f5f9`/`#475569` ·
  text `#0f172a`.

## Interactivity hooks (HTML only, harmless in EMF)

| Attribute | Effect |
| --- | --- |
| `data-step="N"` on a `<g>` | Step-through sequence (Play / ◀ ▶). Later steps are dimmed, the current one is highlighted. Put the node **and** its incoming edge into the step. |
| `data-info="…"` on a `<g>` | Explanation shown below the diagram on hover, focus or click; the group becomes keyboard-focusable. Plain text, 1–3 sentences, same language as the file. |
| `class="flow"` on a `<path>`/`<line>` | Moving dashes — use for the main flow direction and loops. |

Highlighting outlines the `rect`/`circle`/`polygon` inside the current group, so
wrap each node's shape and its label in one `<g>`.
