# Diagramme

[English](README.md) | **Deutsch**

Kontrakt für die SVG-Quellen der Doku-Diagramme (nur für Contributors).

Jede SVG hier wird dreifach genutzt:

1. **GitHub / Markdown:** das Dokument referenziert sie als normales Bild —
   `![Bildunterschrift](diagrams/<doc>/<name>.svg)` (deutsche Dateien: `<name>.de.svg`).
2. **HTML-Export:** `scripts/build_docs.py` bettet die SVG inline ein,
   `docs/assets/diagram.js` macht sie interaktiv.
3. **DOCX-Export:** dieselbe SVG wird mit Inkscape nach **EMF** konvertiert und in
   Seitenbreite (16 cm) eingebettet.

Build: `python scripts/build_docs.py` (braucht `pandoc` und `inkscape`) → `docs/export/`.

## Ablage

```
docs/diagrams/
  workflow/<name>.svg   + <name>.de.svg   → docs/ADW-WORKFLOW(.de).md
  handbook/<name>.svg   + <name>.de.svg   → docs/ADW-HANDBOOK(.de).md
  spec/<name>.svg       + <name>.de.svg   → docs/ADW-SPEC(.de).md
```

EN- und DE-Variante haben dieselbe Geometrie; nur der Text unterscheidet sich.

## Regeln (die ersten vier erzwingt der Build)

- **Kein `<marker>`** — EMF verliert Pfeilspitzen. Jede Pfeilspitze als `<polygon>` zeichnen.
- **Kein `<style>`-Element** — Präsentationsattribute verwenden (`fill=`, `stroke=`,
  `font-size=` …). `class="flow"` ist erlaubt (wirkt nur im HTML).
- **Kein `<filter>`** (Schatten, Blur) und **kein `<foreignObject>`**.
- Wurzel: `xmlns="http://www.w3.org/2000/svg"`, `viewBox` plus passende
  `width`/`height`, ein `<title>` und ein `<desc>`. Erstes Kind: ein weißes
  Hintergrund-`<rect>` über die ganze Fläche.
- Schrift: `font-family="Liberation Sans, Arial, Helvetica, sans-serif"`,
  Größe ≥ 13 für Beschriftungen; Code und YAML in `Liberation Mono, Consolas, monospace`. Zeilenumbrüche von Hand mit `<tspan x=… dy=…>`.
  Breite 900–1000 Einheiten, damit es in Word bei 16 cm lesbar bleibt.
- Palette (hell; das HTML legt die SVG auch im Dark Mode auf eine weiße Karte):
  deterministischer Code `#e0f2fe`/`#0369a1` · Agent/KI `#ede9fe`/`#6d28d9` ·
  Gate/Prüfung `#dcfce7`/`#15803d` · Mensch/Freigabe `#fef3c7`/`#b45309` ·
  Eskalation/Fehler `#fee2e2`/`#b91c1c` · neutral `#f1f5f9`/`#475569` ·
  Text `#0f172a`.

## Interaktions-Hooks (nur HTML, im EMF wirkungslos)

| Attribut | Wirkung |
| --- | --- |
| `data-step="N"` an einem `<g>` | Schrittfolge (Abspielen / ◀ ▶). Spätere Schritte werden abgedimmt, der aktuelle hervorgehoben. Knoten **und** seine eingehende Kante in denselben Schritt legen. |
| `data-info="…"` an einem `<g>` | Erklärung unter dem Diagramm bei Hover, Fokus oder Klick; die Gruppe wird per Tastatur fokussierbar. Klartext, 1–3 Sätze, Sprache der Datei. |
| `class="flow"` an `<path>`/`<line>` | Laufende Strichelung — für die Hauptflussrichtung und Schleifen. |

Die Hervorhebung umrandet die `rect`/`circle`/`polygon` der aktuellen Gruppe —
Form und Beschriftung eines Knotens daher in ein gemeinsames `<g>` legen.
