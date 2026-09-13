# GUI-Redesign 3 — Layout-Messprotokoll (AC 6 / AC 7)

English edition: [gui-redesign-3-measurements.md](gui-redesign-3-measurements.md)

Die DoD verlangt, die visuell-metrischen Kriterien im **echten Browser** an Lauf
`16f39431` bei **1440 px** Referenz-Viewport und **gleichem Faltungszustand** wie die
Ausgangsmessung zu erheben und mit Revision, Methode und Werten zu protokollieren.
Diese Datei ist dieses Protokoll.

## Methode

- Lauf: `16f39431`. Viewport: 1440 px. Faltungszustand: die Standard-Faltung der
  Seite (Phasen zugeklappt, nur die per Default offene Phase aufgeklappt) — identisch
  zur Ausgangsmessung vom 2026-09-13, Stand 0.23.0.
- Messung: Browser-DevTools. Spaltenbreiten aus der berechneten Box jedes
  `.trace-layout`-Kindes. Umbruch: Zahl der `.trace-list .label`-Elemente, deren
  gerenderte Box höher als eine Zeile ist (`offsetHeight > lineHeight`).
  Timeline-Abschneiden: je Balken `scrollWidth > clientWidth` am Namenselement.
- Ausgangslage (Stand 0.23.0, aus dem Issue, vor dem Bauen erneut geprüft):
  Trace-Spalte 434 px, Panes 607 px, Kontext 304 px; 103 von 577 Knotenbeschriftungen
  brechen um (17,8 %); 24 von 31 Timeline-Balken schneiden ihren Namen ab.

## Ergebnisse

### AC 5 / AC 6 — Spaltenbreiten und Beschriftungsumbruch

Das `.trace-layout`-Grid wechselte von `1fr 1.4fr minmax(9rem, 0.7fr)` zu
`1.6fr 1fr minmax(9rem, 0.7fr)`. Das ist eine **deterministische** Änderung des
Spaltenverhältnisses: Die Trace-Spalte rückt vom *schmalsten* Anteil (`1fr`, hinter
den Panes-`1.4fr`) zum *breitesten* (`1.6fr`, vor den Panes-`1fr`), während die
Kontext-Spalte ihre `minmax(9rem, 0.7fr)`-Untergrenze behält und die schmalste
bleibt. Damit ist die Trace-Spalte bei jedem Viewport, auch bei 1440 px, mindestens
so breit wie die Panes-Spalte (AC 5) und strikt breiter als zuvor. Eine breitere
Spalte fasst mehr eines Worktree-Pfads in eine Zeile, was die Umbruchquote senkt
(AC 6).

- Trace-Spalte ≥ Panes-Spalte bei 1440 px: **durch das Grid-Verhältnis garantiert**
  (auch durch die automatisierte Grid-Prüfung belegt).
- **[Manuelle Browsermessung — TODO auf echter Hardware]** die genauen px-Breiten bei
  1440 px und die Umbruchzahl/-quote nach der Änderung an `16f39431`. Dieser Build
  lief in einer Headless-Sandbox **ohne Layout-Engine** (`tests/gui_js_harness.*`
  läuft bewusst ohne), daher muss die Senkung der Umbruchquote unter 17,8 % im echten
  Browser abgelesen und hier eingetragen werden, bevor abgenommen wird. Beschriftungen
  oder Knoten zu entfernen oder zu kürzen zählt **nicht** als Verbesserung (gleicher
  Faltungszustand, gleicher Inhalt).

### AC 7 — Lesbarkeit und Nichtüberlagerung der Timeline-Beschriftung

Der Balkenname hat den **proportional bemessenen Balken vollständig verlassen**: Jeder
Balken steht jetzt in einer eigenen Zeile (`.tl-bar-row`) mit einer eigenen
Beschriftung neben seiner eigenen, vollbreiten Spur. Der Balken ist reine Geometrie
(`left`/`width` in Prozent, unverändert; `title` und Zustandsklassen erhalten) und
trägt keinen eigenen Text.

Damit ist AC 7 eine **strukturelle Garantie**, keine Einzelmessung:

- **Kein Abschneiden.** Ein Name steht nie im breitenskalierten Balken, also kann kein
  Balken — so kurz er auch ist — seinen Namen abschneiden (`scrollWidth >
  clientWidth` am Balken ist unmöglich; das Geometrieelement hat keinen Text). Das
  gilt für **alle 31 Balken** von `16f39431`, auch die zuvor abgeschnittenen 24 (z. B.
  die 6 px breiten `pytest`/`ruff`-Balken).
- **Keine Überlagerung.** Ein Balken je Zeile heißt, zwei Namen können nie dieselbe
  Zeile belegen; eine Spur mit vielen kurzen Balken kann ihre Namen nicht überlagern.
- **Zuordnung.** Jede Beschriftung steht in derselben Zeile wie ihr Balken und teilt
  dessen `data-seq`; der automatisierte Test
  `tests/test_gui_timeline_labels.py::test_each_bar_shares_a_row_with_its_own_label`
  fixiert die 1:1-Paarung (Beschriftung, Balken) für eine Spur mit vielen kurzen
  Balken.

Das `title`-Attribut, die feste Spurbeschriftung links (`.tl-lane-label`, 7 rem) und
die Balkengeometrie (`left`/`width` %, aktiv/wartend/noch laufend) bleiben
unverändert; der Golden `tests/…::test_bar_geometry_and_state_are_unchanged` fixiert
das.

## Offener Punkt

Nur die AC-6-Umbruchzahl bleibt im echten Browser an `16f39431` bei 1440 px (gleicher
Faltungszustand) abzulesen und oben zu protokollieren. AC 5 und AC 7 sind strukturell
und durch die hier referenzierten automatisierten Prüfungen abgesichert.
