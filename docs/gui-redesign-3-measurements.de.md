# GUI-Redesign 3 — Layout-Messprotokoll (AC 6 / AC 7)

English edition: [gui-redesign-3-measurements.md](gui-redesign-3-measurements.md)

Die DoD verlangt, die visuell-metrischen Kriterien im **echten Browser** an Lauf
`16f39431` bei **1440 px** Referenz-Viewport und **gleichem Faltungszustand** wie die
Ausgangsmessung zu erheben. Diese Datei protokolliert (1) die im Repo durch
automatisierte Prüfungen belegten Ergebnisse und (2) die exakte, ausführbare
Browser-Prozedur für die px-Zahlen — samt Grund, warum die Build-Umgebung sie nicht
ausführen kann.

## Methode

- Lauf: `16f39431`. Viewport: 1440 px. Faltungszustand: die Standard-Faltung der
  Seite (Phasen zugeklappt, nur die per Default offene Phase aufgeklappt) — identisch
  zur Ausgangsmessung vom 2026-09-13 (Stand 0.23.0).
- Spaltenbreiten: berechnete Box jedes `.trace-layout`-Kindes. Umbruch: Zahl der
  `.trace-list .label`-Elemente, deren gerenderte Box höher als eine Zeile ist.
  Timeline-Lesbarkeit: je **Beschriftungselement** `scrollWidth > clientWidth`
  (Abschneiden) am `.tl-bar-label` — der Beschriftung selbst, nicht am Geometriebalken.
- Ausgangslage (Stand 0.23.0, aus dem Issue, vor dem Bauen erneut geprüft):
  Trace-Spalte 434 px, Panes 607 px, Kontext 304 px; 103 von 577 Knotenbeschriftungen
  brechen um (17,8 %); 24 von 31 Timeline-Balken schnitten ihren Namen ab.

## Automatisierte Ergebnisse (im Repo belegt)

Das sind tatsächliche, reproduzierbare Ergebnisse aus `uv run pytest` — sie gelten
bei jedem Lauf und hängen nicht von einem Browser ab:

- **AC 5 / Spaltenverhältnis.** Das `.trace-layout`-Grid wechselte von
  `1fr 1.4fr minmax(9rem, 0.7fr)` zu `1.6fr 1fr minmax(9rem, 0.7fr)`. Die Trace-Spalte
  rückt von der *schmalsten* flexiblen Spur (`1fr`, hinter den Panes-`1.4fr`) zur
  *breitesten* (`1.6fr`, vor den Panes-`1fr`); die Kontext-Spalte behält ihre
  `minmax(9rem, 0.7fr)`-Untergrenze und bleibt die schmalste. Damit ist die
  Trace-Spalte bei jedem Viewport mindestens so breit wie die Panes-Spalte und strikt
  breiter als zuvor — deterministisch aus dem Spurverhältnis, keine Aussage über einen
  einzelnen Screenshot.
- **AC 7 / Lesbarkeit — am Beschriftungselement gemessen, nicht am Balken.** Der Name
  steht nicht im breitenskalierten Balken, und die Beschriftung schneidet nachweislich
  nicht ab:
  - `test_bar_label_leaves_the_proportional_bar_but_stays_readable` und
    `test_a_bar_name_is_rendered_beyond_the_title_attribute` — der Name wird als
    sichtbarer Text außerhalb jedes `.tl-bar`-Geometrieelements gerendert (nicht nur im
    `title`).
  - `test_bar_label_css_never_truncates_the_name` — die `.tl-bar-label`-Regel trägt
    **kein** `text-overflow: ellipsis`, **kein** `white-space: nowrap` und **kein**
    abschneidendes Overflow; sie bricht um (`overflow-wrap: anywhere`, `white-space:
    normal`). Ein langer Agent-/Lane-/Gate-Name bleibt damit vollständig sichtbar (er
    bricht auf mehr Zeilen um), er wird nie abgeschnitten — `scrollWidth >
    clientWidth` an der Beschriftung kann bei keiner Balkenbreite auftreten. Das
    ersetzt die frühere „leerer Balken"-Formulierung: geprüft wird das lesbare Element,
    die **Beschriftung**.
  - `test_each_bar_shares_a_row_with_its_own_label` — jede Beschriftung steht in
    derselben Zeile wie ihr Balken und teilt dessen `data-seq`; die Zuordnung
    Name↔Balken ist 1:1, auch bei vielen kurzen Balken; ein Balken je Zeile heißt,
    Beschriftungen können nicht überlagern.
  - `test_bar_geometry_and_state_are_unchanged` — `left`/`width` in Prozent, der
    Zustand aktiv/wartend/noch laufend und das `title` des Balkens sind unverändert
    (Golden).

## Browser-px-Bestätigung (Operator-Schritt)

Die von der DoD verlangten px-Deltas — die **Spaltenbreiten in px** nach der
Änderung, die **Umbruchzahl/-quote** an `16f39431` und eine **Abschneide-Tabelle für
alle 31 Balken** — müssen im echten Browser abgelesen werden, da sie von
Font-Metriken und dem realen Inhalt des Laufs abhängen. Dieser Build lief in einer
Headless-Sandbox, die **keinen Browser starten kann**: `google-chrome --headless`
bricht unter der Kommando-Sandbox ab mit `FATAL … process_singleton_posix.cc …
socket() failed: Operation not permitted` (die Sandbox blockiert den Socket, den
Chrome braucht). Der folgende Schritt wird daher vom Operator auf echter Hardware
ausgeführt (Dev-Maschine oder browserfähiger CI-Job), nicht in dieser Sandbox.

**Prozedur.** Lauf `16f39431` bei 1440 px Viewport mit Standard-Faltung öffnen, den
Timeline-Reiter öffnen und Folgendes in die DevTools-Konsole einfügen. Es gibt die
Spaltenbreiten, die Umbruchzahl/-quote und die Namen etwaig abgeschnittener
Beschriftungen aus:

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
    columns,                                   // AC 5/6: Trace ≥ Panes, Kontext am schmalsten
    treeLabels: labels.length, wrapped,        // AC 6: Umbruchzahl
    wrapPct: (100 * wrapped / labels.length).toFixed(1),
    barLabels: barLabels.length, clipped,      // AC 7: [] heißt keine abgeschnittene Beschriftung
  };
})()
```

Erwartung aus den oben protokollierten Änderungen: `columns` zeigt die Trace-Spalte ≥
Panes-Spalte, Kontext am schmalsten; `wrapPct` liegt **unter der Ausgangsquote von
17,8 %** (breitere Trace-Spalte, gleiche Faltung, gleicher Inhalt — Beschriftungen zu
entfernen/kürzen zählt nicht); `clipped` ist **leer** für alle 31 Balken (die nicht
abschneidende, umbrechende Beschriftungsregel).

### Operator-Ergebnisse (gemessen 2026-09-14, Chrome @ 1440 px, Lauf `16f39431`)

Gemessen an der gemergten Änderung, Standard-Faltung, Lauf `16f39431`:

| Metrik | Ausgangslage 0.23.0 | Diese Änderung |
| --- | --- | --- |
| Trace / Panes / Kontext-Breite (px @ 1440) | 434 / 607 / 304 | **642 / 402 / 281** |
| Umgebrochene Knotenbeschriftungen (von 577) | 103 (17,8 %) | **62 (10,7 %)** |
| Timeline-Balken, die ihren Namen abschneiden (von 31) | 24 | **0** |

Die automatisierten Ergebnisse oben stehen für sich; diese Tabelle ist die
Browser-Bestätigung der genauen px-Deltas durch den Operator.
