# GUI-Redesign 1/2: Gestaltungsfundament und die Zeitachse des Laufs

Erster von zwei Briefen. Dieser legt das Fundament (Farb-, Schrift- und
Abstands-System, Dark Mode) und baut das eine markante Element, an dem die
Richtung ablesbar wird: das Phasenband wird zur maßstäblichen Zeitachse.
Der zweite Brief nimmt sich die Informationsarchitektur vor (Run-Liste,
Anordnung der Blöcke im Run-Detail, DOM-Deckel) — was dort steht, ist hier
ausdrücklich NICHT Gegenstand.

## Ausgangslage (im Code verifiziert, 2026-09-13, Stand 0.21.4)

**Das Stylesheet hat kein System.** `adw/gui/static/app.css` hat 284 Zeilen und
ist Aufgabe für Aufgabe gewachsen (die Kommentare heißen „Aufgabe A", „Aufgabe
F", „R4/E3"). Messbar:

- **39 Hex-Literale, 26 verschiedene**, aber nur **vier** definierte Tokens
  (`--fg #1c1c1c`, `--muted #777`, `--border #ddd`, `--accent #2a6`).
- **Drei Variablen werden referenziert, die nirgends definiert sind:**
  `var(--ok, #1a7f37)`, `var(--err, #cf222e)`, `var(--code-bg, #f6f8fa)`. Die
  Literal-Fallbacks gewinnen deshalb immer. Ergebnis: zwei Grüns für „gut"
  (`#2a6` und `#1a7f37`), drei Rots für „kaputt" (`#c33`, `#a33`, `#cf222e`),
  vier Blaus.
- **Neun verschiedene `font-size`-Werte** (0.7 / 0.75 / 0.8 / 0.85 / 0.9 / 0.95
  / 1 / 1em / 1.4 rem), **21 verschiedene `padding`/`margin`-rem-Werte**
  (0.05 bis 1.5 rem).
- **Eine einzige `font-family`-Deklaration** für die ganze Anwendung (Zeile 14).
  Bezeichner, Pfade, Sequenznummern, Kosten und Dauern laufen in derselben
  Proportionalschrift wie der Fließtext.
- **Null `@media`-Regeln.** Kein Dark Mode, kein Breakpoint, kein
  `prefers-reduced-motion`.

**Eine Farbe trägt zwei Bedeutungen.** `#3651d6` steht in `app.css` an drei
Stellen: `.phase-awaiting` (Zeile 49) und `.run-status.status-awaiting_approval`
(Zeile 57) — beides „ein Mensch muss handeln" — sowie `.node-waiting`
(Zeile 181), das „technisch wartend" meint (offener `ci.wait`, laufendes Gate).
Genau die Unterscheidung, für die Release 0.9.0 gebaut wurde („arbeitet" vs.
„wartet" vs. „wartet auf Menschen"), ist in einer einzigen Farbe gerendert.

**Dieselbe Größe wird zweimal verschieden formatiert.** Die Kosten laufen in
Run-Liste und Timeline-Kopf durch `_fmt_cost` (`adw/gui/app.py:577`,
`f"${cost:.2f}"` → `$47.16`), im Run-Kontext-Panel dagegen durch das Makro
`ctx_num` (`adw/gui/templates/run_detail.html:96`, `round(6)` → `56.87666`).

**Das Phasenband bildet Dauer nicht ab.** `adw/gui/templates/run_detail.html:357`
rendert je Phase einen Chip, dessen Breite allein aus der Länge der
Beschriftung folgt. Gemessen an Lauf `16f39431` bei 1440 px Viewport-Breite:

| Phase | Dauer | Chip-Breite |
|---|---:|---:|
| spec | 807 s | 102 px |
| plan | 534 s | 93 px |
| build | **2607 s** | 105 px |
| integration | — | 89 px |
| codex_review | 1145 s | **150 px** |
| final_review | 867 s | 148 px |
| ci | 124 s | 68 px |

`build` dauert das 21-Fache von `ci` und ist 1,5-mal so breit. Der breiteste
Chip gehört `codex_review`, das nicht einmal halb so lange lief wie `build`.

**Die Wartezeit auf einen Menschen kommt in der GUI nicht vor.** Über den
Event-Logs aller 20 Läufe nachgerechnet: die Phasen-Spans überlappen **in
keinem einzigen Lauf** (0 überlappende Paare bei 20 Läufen). Zwischen ihnen
liegen Lücken — die Zeit an den Freigabe-Gates. Für `16f39431`: Summe der
Phasendauern 6084 s, erster Phasenstart bis letztes Phasenende 9372 s,
Differenz **3289 s (35 %)**, davon 3206 s am Spec-Gate. **In 9 der 20 Läufe
liegt dieser Anteil über einem Drittel** (`e4e70373`: 1185 s Arbeit in 532 407 s
= 6,2 Tagen, 99,8 %; `d0bdb365`: 88 %; `ea16f932`: 87 %; `b6739174`: 80 %). In
7 Läufen ist er null. Keine der drei Ansichten zeigt diese Zahl.

**Der Baum rendert real 844 Eintragsmarker.** Auf der geladenen Seite von
`16f39431` (1440 px): 844 `data-tree-entry`, 100 `data-tool-entry`, 577
`li.node`, 7177 DOM-Elemente insgesamt. `tests/test_gui_bounded_dom.py` nennt
`CAP = 200` „per collection" und definiert beide Marker — **prüft den Deckel
aber ausschließlich für `data-tool-entry`** (Zeilen 148, 185, 189, 215). Für
`data-tree-entry` existiert nirgends eine Obergrenzen-Assertion. Der Deckel gilt
also faktisch nur für das Tools-Fenster, und im Baum ist er um das 4,2-Fache
gerissen. **Die Bereinigung ist Brief 2** — hier folgt daraus nur die Auflage
E4.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

Das Run-Detail hat heute einen Kopf (Titel, Phasenband, Registerkarten), darunter
in dieser Reihenfolge: „Planned tasks" (242 px hoch, volle Breite), „Change
scope" (402 px, volle Breite) und erst dann das dreispaltige Arbeitsfeld
Trace-Baum (434 px) │ Detail-Panes (607 px) │ Run-Kontext (304 px). Der
Trace-Baum beginnt dadurch 854 px unterhalb des Dokumentanfangs. Die Run-Liste
ist eine Tabelle mit 35 Zeilen, Medianhöhe 172 px, Gesamthöhe 5784 px, ohne
Sortier- oder Filtersteuerung (0 `th a`, 0 `form`, 0 `select`), mit einer
Issue-Spalte, die rohen Issue-Markdown über 6–8 Zeilen umbricht, und mit
`Phase` und `Status` als zwei Spalten, die bei jedem fertigen Lauf denselben
String zeigen. **Dieser gesamte Bestand bleibt in diesem Issue unverändert** —
er ist der Gegenstand von Brief 2. Siehe E3.

## Aufgabe

**A1 — Token-Schicht.** Alle Farbwerte kommen aus benannten Custom Properties,
die in genau zwei Blöcken definiert werden: `:root` (hell) und
`@media (prefers-color-scheme: dark)`. Außerhalb dieser beiden Blöcke steht kein
Hex-Literal mehr. Jeder heutige Literalwert wird auf genau ein Token abgebildet;
die drei heute nur als Fallback existierenden Variablen (`--ok`, `--err`,
`--code-bg`) werden echte Tokens.

**A2 — Schrift- und Abstands-Skala.** Zwei Schriftrollen statt einer: die
bestehende UI-Schrift für Fließtext, Beschriftungen und Überschriften, und ein
System-Monospace-Stack für Maschinenwerte (Lauf-IDs, Sequenznummern, Pfade,
Dauern, Kosten, Token- und Ereigniszahlen, Phasennamen). Eine Größenskala mit
sechs Stufen und eine Abstandsskala mit sechs Stufen ersetzen die 9 bzw. 21
Ad-hoc-Werte. Jede Zahl, die sich ändern kann, bekommt
`font-variant-numeric: tabular-nums`.

**A3 — Signalfarben-Disziplin.** Genau ein Farbton bedeutet „ein Mensch muss
handeln" und wird für nichts anderes verwendet. Die Doppelrolle von `#3651d6`
wird aufgelöst: „technisch wartend" (`.node-waiting`, Warte-Balken der Timeline)
und „wartet auf einen Menschen" (`.phase-awaiting`,
`.status-awaiting_approval`) bekommen verschiedene Tokens. „Arbeitet gerade"
bekommt ein drittes, von beiden verschiedenes Token.

**A4 — Dark Mode.** Beide Themes über dieselben Token-Namen, umgeschaltet
ausschließlich über `prefers-color-scheme`. Zusätzlich eine
`prefers-reduced-motion: reduce`-Regel, die die Übergänge abschaltet.

**A5 — Das Phasenband wird zur maßstäblichen Zeitachse.** An seiner heutigen
Stelle im Kopf, anstelle der Chip-Reihe: eine durchgehende Schiene, auf der jede
Phase an ihrer wirklichen zeitlichen Position und mit ihrer wirklichen Dauer als
Breite sitzt. Die Lücken zwischen den Phasen — die Zeit an den Freigabe-Gates —
sind als eigene Segmente sichtbar und als Warten beschriftet. Unter der Schiene
stehen drei beschriftete Zahlen: Arbeit, Warten, Gesamt. Die Phasennamen und
ihre Dauern bleiben lesbar; eine Phase, die nie lief, bleibt sichtbar, nimmt
aber keine Fläche auf der Schiene ein.

**A6 — `_phase_bar` liefert die Zeitpunkte mit.** `adw/gui/app.py:1090`
berechnet Start und Ende jeder Phase bereits intern, gibt aber nur `name`,
`status` und `duration` aus. Die Funktion gibt zusätzlich `start` und `end` je
Phase aus (ISO-8601-String wie im Event-Log, oder `null`). Additiv — kein Feld
wird entfernt oder umbenannt. Siehe Contract-Hinweis.

**A7 — Eine Formatierung je Größe.** Die Kosten erscheinen in Run-Liste,
Timeline-Kopf und Run-Kontext-Panel in genau einem Format. Der
Run-Kontext-Eintrag für `cost_usd` benutzt denselben Formatierer wie die beiden
anderen Stellen; `ctx_num` bleibt für die übrigen Zahlenfelder, wie es ist.

**A8 — i18n.** Die neuen Beschriftungen der Zeitachse (Arbeit, Warten, Gesamt
und die Segment-Titel) liegen in `adw/gui/i18n.py` in beiden Sprachen vor,
identische Schlüsselmengen, Pluralformen korrekt.

**A9 — Doku und Changelog.** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
beschreiben synchron das Token-System, die beiden Schriftrollen, die
Signalfarben-Regel, den Dark Mode und die Zeitachse einschließlich ihrer
Rechenregeln. Die `Unreleased`-Sektion in `CHANGELOG.md` und `CHANGELOG.de.md`
wird synchron ergänzt.

## Normative Definitionen (bindend, nicht neu herzuleiten)

### Farbtokens

Die Werte sind durchgerechnet: jede Text-auf-Grund-Paarung erreicht in beiden
Themes mindestens 4,5:1 (kleinster Wert 5,01). Sie werden übernommen, nicht neu
gewählt.

| Token | hell | dunkel | Rolle |
|---|---|---|---|
| `--paper` | `#f6f7f9` | `#12151a` | Seitengrund |
| `--surface` | `#ffffff` | `#191d24` | abgesetzte Fläche (Panes, Karten) |
| `--ink` | `#191d24` | `#e6e9ee` | Haupttext |
| `--ink-soft` | `#5a626e` | `#949cab` | Sekundärtext (ersetzt `--muted`) |
| `--rule` | `#dde1e7` | `#2a3039` | Linien und Rahmen (ersetzt `--border`) |
| `--code-bg` | `#eef1f5` | `#1e232b` | Monospace-Blöcke, `pre` |
| `--signal` | `#a8530a` | `#e8a33d` | **nur** „ein Mensch muss handeln" |
| `--signal-ink` | `#ffffff` | `#1a1207` | Text auf `--signal` |
| `--ok` | `#256b45` | `#4ea87a` | gelungen / fertig |
| `--fail` | `#a5241d` | `#e0716a` | fehlgeschlagen / eskaliert |
| `--busy` | `#1d5f8a` | `#59a7d8` | arbeitet gerade |
| `--wait` | `#5b6472` | `#8b93a1` | technisch wartend (CI-Poll, Gate-Laufzeit) |

`--accent` und `--fg` und `--muted` und `--border` dürfen als Aliase auf die
neuen Tokens bestehen bleiben, wenn das Markup sie referenziert; neue Regeln
benutzen die neuen Namen.

### Skalen

- Schriftgrößen, sechs Stufen: `0.75` · `0.8125` · `0.875` · `1` · `1.25` ·
  `1.5` rem. Kein anderer Wert.
- Abstände, sechs Stufen: `0.25` · `0.5` · `0.75` · `1` · `1.5` · `2` rem. Kein
  anderer Wert für `padding` und `margin`.
- Radien: `3px` und `6px`, beide heute schon in Gebrauch. Keine weiteren.
- Monospace-Stack: `ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo,
  Consolas, monospace`. Die UI-Schrift bleibt wörtlich der heutige Stack.

### Die Zeitachse (A5) — Rechenregeln

Aus den Phasen-Einträgen mit parsebarem `start`:

- `T_start` = kleinstes `start`. `T_end` = größtes `end`; hat eine Phase mit
  Status `active` kein `end`, ist `T_end` der Zeitpunkt des Seitenaufbaus.
  `T` = `T_end − T_start`.
- **Ist `T` nicht bestimmbar oder ≤ 0, oder hat weniger als eine Phase einen
  parsebaren `start`, wird die heutige Chip-Reihe gerendert.** Sie bleibt die
  Rückfallebene und wird nicht entfernt.
- Segment je Phase: Versatz `(start − T_start) / T`, Breite `(end − start) / T`.
  Eine Phase mit `start`, ohne `end` und mit Status `active` reicht bis zum
  rechten Rand und wird als offen markiert (dieselbe Konvention, die
  `.tl-bar.bar-running` in der Timeline schon benutzt).
- **Wartesegmente:** Phasen nach `start` sortieren; ist der Abstand zwischen dem
  `end` der einen und dem `start` der nächsten größer als null, entsteht dort ein
  Segment der Art „Warten". Warum gewartet wurde, wird nicht ermittelt und nicht
  behauptet.
- Eine Phase ohne parsebaren `start` (nie gelaufen, z. B. `integration`) bekommt
  **kein** Segment. Sie bleibt unter der Schiene in der Legende sichtbar, gedämpft.
- **Mindestbreite jedes Segments: 2 px.** Sehr kurze Phasen sind dadurch bewusst
  nicht maßstäblich — anders wären sie unsichtbar. Diese Frage ist entschieden.
- Die drei Zahlen unter der Schiene: **Arbeit** = Summe der Phasendauern,
  **Warten** = Summe der Lücken, **Gesamt** = `T`. Es gilt
  Arbeit + Warten = Gesamt bis auf Rundung (für `16f39431`: 6084 + 3289 = 9373
  gegen 9372).

### Zustandsfarben nach der Bereinigung

| Zustand | Token |
|---|---|
| `.phase-completed`, `.node-done`, `.node-passed` | `--ok` |
| `.phase-active`, `.node-running` | `--busy` |
| `.node-waiting`, Warte-Balken der Timeline, Wartesegment der Achse | `--wait` |
| `.phase-awaiting`, `.run-status.status-awaiting_approval` | `--signal` |
| `.phase-failed`, `.node-failed`, `.problem`, `.raw-problem` | `--fail` |
| `.phase-pending` | `--ink-soft` |

Die Dry-Run-Kennzeichnung behält ihren eigenen, von `--signal` verschiedenen
Braunton und ihre `position: sticky`-Regel.

## Vorentscheidungen (entschieden — kein Finding, auch nicht im Review-Loop)

**E1** — Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, **keine
Webfonts**. Nur System-Schrift-Stacks, handgeschriebenes CSS, Vanilla JS
(`docs/GUI-SPEC.md:316`). Diese Frage ist entschieden.

**E2** — **Alle heutigen Zustands- und Struktur-Klassennamen bleiben erhalten.**
Umbenannt wird nichts. Bestehende Tests prüfen die Anwesenheit dieser Strings in
`app.css` bzw. im Markup: `.phase-active`, `.phase-completed`, `.node-running`,
`.node-done`, `node-waiting`, `awaiting` (`tests/test_gui_waiting_presentation.py`
Zeilen 61–65), `trace-summary` (`tests/test_gui_static_cache.py:51`), `nowrap`
(`tests/test_gui_polish_formatting.py:77`) sowie eine Regel, deren Selektor `dry`
enthält und `position: sticky|fixed` setzt (`tests/test_gui_dry_run.py:220–226`).
Verändert wird ausschließlich, wie diese Selektoren aussehen. Diese Frage ist
entschieden.

**E3** — **Kein Umbau der Informationsarchitektur.** Reihenfolge und Anordnung
von „Planned tasks", „Change scope", Trace-Baum, Detail-Panes, Run-Kontext-Panel,
Registerkarten und Run-Liste bleiben exakt, wie sie sind, einschließlich der
Spalten der Run-Liste und der Breitenverhältnisse des `trace-layout`-Grids.
Dieses Issue ändert Farbe, Typografie, Abstand — und zusätzlich das Phasenband
nach A5. Kein Abbau, kein Umbau, kein Ausbau darüber hinaus, auch nicht als
Review-Finding. Diese Frage ist entschieden.

**E4** — **Kein zusätzliches Element je Eintrag.** Der Baum rendert real 844
`data-tree-entry`-Marker auf einer Seite; jede zusätzliche Hülle, jedes
zusätzliche `<span>` je Zeile multipliziert sich mit dieser Zahl. Gestaltet wird
über Selektoren auf dem vorhandenen Markup. Neue Elemente sind nur dort erlaubt,
wo A5 sie braucht (Kopf, konstante Anzahl). Diese Frage ist entschieden.

**E5** — `--signal` bedeutet genau eine Sache: ein Mensch muss handeln. Kein
anderer Zustand, kein Hover, kein Fokusring, keine Überschrift und kein
Diagramm-Element trägt diesen Farbton. Diese Frage ist entschieden.

**E6** — Dark Mode ausschließlich über `prefers-color-scheme`. **Kein
Umschalter, kein `localStorage`, kein neuer Client-Zustand, kein neuer
Query-Parameter.** Diese Frage ist entschieden.

**E7** — Die Zeitachse ersetzt die Chip-Reihe an deren heutiger Stelle. Es
entsteht **kein zweites Band**, und die Registerkarte „Timeline" wird nicht
angefasst — weder ihre Spuren noch ihre Balken noch ihre Beschriftungen. Diese
Frage ist entschieden.

**E8** — Die bestehende `duration`-Angabe eines Laufs (Spalte der Run-Liste,
Kopf der Timeline-Registerkarte) wird **nicht angefasst**, obwohl sie für
`16f39431` mit 4743 s eine dritte Zahl neben Arbeit (6084 s) und Gesamt (9372 s)
zeigt. Welche dieser Zahlen die „Dauer eines Laufs" ist, entscheidet Brief 2.
Diese Frage ist hier entschieden — kein Finding.

**E9** — Keine Animation außer Farb- und Hintergrundübergängen von höchstens
150 ms, und diese respektieren `prefers-reduced-motion`. Keine Bewegung, kein
Einblenden, kein Skalieren, kein Schatten-Spiel. Diese Frage ist entschieden.

**E10** — Artefakte bleiben getreuer Monospace-Text, keine
Markdown-Bibliothek (E10 der GUI-Reihe, unverändert gültig).

**E11** — Keine Änderung an der i18n-Mechanik; nur neue Schlüssel in beiden
Sprachen. Event-, Pfad-, Werkzeug- und Payload-Inhalte werden nicht übersetzt.

**E12** — Kein Responsive-Umbau, keine Breakpoints, keine Mobilansicht. Die
Anwendung bindet an Loopback und wird am Rechner gelesen. Die bestehende Regel
`html, body { max-width: 100%; overflow-x: hidden }` und die `min-width: 0`-
Regeln der Grid-Kinder bleiben. Diese Frage ist entschieden.

## Nicht-Ziele / Scope-Deckel

Keine neue Route, kein neues Tab, keine Änderung an `build_tree`
(`adw/gui/model.py`), an der Verdichtungsschicht, an der Blätterung (`?offset`,
`?tools_offset`, Fenstergröße 100), am SSE-Pfad, an den Ereignistypen, an der
Instrumentierung oder an der Retention. Keine Änderung an der API-Antwort außer
der in A6 benannten additiven Erweiterung. Keine neue Persistenz, kein Polling,
kein neues Zustands-Subsystem. Keine Änderung an Auswahl-, Pane-, `?focus`- oder
Deep-Link-Verhalten.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

Diese Punkte sind erkannt und begründet, gehören aber in Brief 2 oder später.
Was hier steht, wird im Codex-/Fix-Zyklus **nicht** nachgebaut:

- Informationsarchitektur der Run-Liste: umbrechende Issue-Spalte, die Dopplung
  von `Phase` und `Status`, fehlende Sortier- und Filtersteuerung (§7.2 A der
  Spec verlangt sie, das Markup hat sie nicht).
- Anordnung der Blöcke im Run-Detail (Trace-Baum beginnt bei 854 px).
- Der gerissene DOM-Deckel im Baum (844 statt 200) und die fehlende Assertion
  für `data-tree-entry`.
- Die drei verschiedenen Dauer-Begriffe eines Laufs (siehe E8).
- Spur- und Balkenbeschriftung der Timeline (heute auf „co", „b", „p"
  verstümmelt).
- Manueller Theme-Umschalter, Mobilansicht, eigenes Icon-Set,
  Barrierefreiheits-Audit über die Kontrastschwelle hinaus.

## Contract-Hinweis

Single-Lane-Projekt (`backend`, siehe `.adw/config.yaml`). `GET
/api/runs/{repo}/{run_id}` liefert unter `phases` die Einträge aus `_phase_bar`
— die Antwort ist damit Contract-Fläche. Dieses Issue erweitert jeden Eintrag um
**`start` und `end`** (ISO-8601-String wie im Event-Log, oder `null`).
**Additiv:** kein Feld wird entfernt, umbenannt oder in Typ oder Bedeutung
geändert; `name`, `status` und `duration` bleiben wörtlich, wie sie sind. Alle
übrigen Antwortflächen, insbesondere `tree`, `raw`, `latest_context`,
`problems` und die Statuswerte, bleiben unverändert. Ein Regressionstest fixiert
beides: die neuen Felder sind da, und `tree` ist strukturell unverändert. Nicht
Teil des Contracts: interne Helper-Signaturen, Klassennamen, Markup- und
CSS-Wortlaut, die konkreten Token-Werte.

## Toolchain (Fakten, nicht aus Allgemeinwissen ableiten)

Gates sind `uv run ruff check .` und `uv run pytest -x -q`. Es gibt **kein**
flake8, **kein** isort, **kein** black; `ruff format` ist bewusst kein Gate.
Tests liegen flach unter `tests/` als `test_gui_*.py`. Für reines
Client-Verhalten steht der bestehende Harness `tests/gui_js_harness.js` /
`tests/gui_js_harness.py` bereit. Der Abnahmepunkt 10 in `docs/GUI-SPEC.md`
nennt noch „flake8 + isort" — das ist veralteter Spec-Text und wird nicht
befolgt.

## Akzeptanzkriterien (messbar)

1. `adw/gui/static/app.css` enthält außerhalb der beiden Token-Blöcke (`:root`
   und `@media (prefers-color-scheme: dark)`) **null** Hex-Literale (heute 39).
2. Die Datei benutzt höchstens **sechs** verschiedene `font-size`-Werte (heute
   9) und höchstens **sechs** verschiedene rem-Werte für `padding`/`margin`
   (heute 21); alle stammen aus den Skalen.
3. `--ok`, `--fail` (bzw. `--err`) und `--code-bg` sind in beiden Themes
   **definiert**; kein `var(--name, literal)`-Aufruf verlässt sich mehr auf
   einen Fallback für eine nicht existierende Variable.
4. `@media (prefers-color-scheme: dark)` definiert **jedes** der in den
   Normativen Definitionen gelisteten Tokens neu.
   `@media (prefers-reduced-motion: reduce)` existiert und schaltet die
   Übergänge ab.
5. Kein Selektor setzt denselben Farbton für `.node-waiting` und für
   `.phase-awaiting` / `.status-awaiting_approval`.
6. Die gepinnten Strings sind unverändert vorhanden: `.phase-active`,
   `.phase-completed`, `.node-running`, `.node-done`, `node-waiting`,
   `awaiting`, `trace-summary`, `nowrap` sowie eine `dry`-Regel mit
   `position: sticky|fixed`.
7. **Zeitachse, Maßstab:** für den Lauf `16f39431` ist das `build`-Segment
   mindestens **20-mal** so breit wie das `ci`-Segment (durchgerechnet:
   2607 s / 124 s = 21,0).
8. **Zeitachse, Warten:** für `16f39431` weist die Achse ein Wartesegment aus,
   und die drei Zahlen lauten Arbeit 6084 s, Warten 3289 s, Gesamt 9372 s
   (Formatierung frei, Toleranz ±2 s); es gilt Arbeit + Warten = Gesamt bis auf
   Rundung.
9. **Zeitachse, lückenloser Lauf:** für `81795e53` (Summe der Phasendauern
   3656 s = Gesamt 3656 s) zeigt die Achse **kein** Wartesegment und Warten = 0.
10. **Zeitachse, Rückfallebene:** ein Lauf ohne parsebare Phasen-Zeitstempel
    rendert die heutige Chip-Reihe, nicht eine leere oder kaputte Schiene.
11. Die Kosten erscheinen in Run-Liste, Timeline-Kopf und Run-Kontext-Panel in
    **genau einem** Format; `56.87666` kommt nirgends mehr vor.
12. Die Anzahl der `data-tree-entry`-Marker je gerenderter Seite ist gegenüber
    dem heutigen Stand **unverändert** (E4).
13. `GET /api/runs/{repo}/{run_id}` liefert je `phases`-Eintrag `start` und
    `end`; `name`, `status`, `duration` und die Struktur von `tree` sind
    unverändert.
14. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
    Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine Webfont.

## Testumfang

Richtwert **~12 neue Tests** unter `tests/test_gui_*.py`; deutlich mehr als ~18
ist Scope-Drift. Mindestens abgedeckt: Token-Disziplin (AC 1–3), Dark-Mode- und
reduced-motion-Block (AC 4), aufgelöste Farb-Doppelrolle (AC 5), Erhalt der
gepinnten Selektoren (AC 6), Maßstab der Achse (AC 7), Wartesegment mit und ohne
Lücke (AC 8/9), Rückfallebene (AC 10), einheitliche Kostenformatierung (AC 11),
unveränderte Marker-Zahl (AC 12), Contract-Regression für `phases` und `tree`
(AC 13). Die bestehenden GUI-Tests bleiben grün, ohne inhaltlich umgeschrieben
zu werden.
