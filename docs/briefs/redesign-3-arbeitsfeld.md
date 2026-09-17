# GUI-Redesign 3: Das Arbeitsfeld zuerst

Dritter Brief der Redesign-Reihe. Brief 1 hat das Fundament gelegt und die
Zeitachse gebaut (Release 0.22.0), Brief 2 die Zahlen richtiggestellt und die
Run-Liste in Ordnung gebracht (Release 0.23.0). Dieser hier räumt die
**Run-Detail-Seite** auf: das Instrument nach oben, die Zusammenfassungen
darunter, und die Timeline bekommt dieselbe Regel beigebracht, die die
Zeitachse schon kennt — Geometrie in die Spur, Wörter in eine feste Spalte.

**Abhängigkeit:** Setzt auf dem gemergten Stand **0.23.0** auf. Tokens,
Skalen, Signalfarbe, Dark Mode und die Zeitachse stammen aus Brief 1; die
Zeitgrößen und ihr Vokabular aus Brief 2. Beides wird hier **benutzt, nicht
revidiert**. Die Ausgangslage unten ist am 2026-09-13 gegen genau diesen Stand
gemessen — also nach beiden Releases — und vor dem Bauen gegen den dann
aktuellen Stand zu prüfen.

## Ausgangslage (im Code verifiziert, 2026-09-13, Stand 0.23.0)

### Das Arbeitsfeld steht unter zwei Zusammenfassungen

Gemessen an Lauf `16f39431` bei 1440 px Viewport-Breite, Stand 0.23.0:

- Zwischen Seitenkopf und Arbeitsfeld stehen zwei Blöcke über die volle Breite:
  „Planned tasks" (**257 px** hoch) und „Change scope" (**493 px**). Beide
  tragen `grid-column: 1 / -1` im `trace-layout`-Grid.
- Der Seitenkopf selbst ist **169 px** hoch — er trägt seit 0.22.0 die
  Zeitachse und seit 0.23.0 zusätzlich die benannten Zeitgrößen.
- Der Trace-Baum (`.trace-list`) beginnt dadurch **1023 px** unterhalb des
  Dokumentanfangs. Auf einem 900 px hohen Fenster ist er **gar nicht** mehr
  sichtbar, ohne zu scrollen. Vor der Redesign-Reihe waren es 854 px — die
  beiden bisherigen Releases haben den Kopf gehaltvoller gemacht und das
  Problem damit verschärft, nicht entschärft.
- Beide Blöcke sind immer vollständig ausgeklappt; es gibt keine Möglichkeit,
  sie zuzuklappen, und keine Zusammenfassungszeile, die ihren Inhalt in einem
  Satz nennt.

### Die Baum-Spalte ist die schmalste, obwohl sie das Instrument ist

Das `trace-layout`-Grid steht auf `1fr 1.4fr minmax(9rem, 0.7fr)`. Gemessen bei
1440 px: Trace-Baum **434 px**, Detail-Panes **607 px**, Run-Kontext **304 px**.
Von den 577 als eigene Zeile gerenderten Knotenbeschriftungen brechen
**103 (17,8 %)** auf mehr als eine Zeile um — Worktree-Pfade wie
`.adw/runs/16f39431/trees/backend/adw/gui/app.py` passen nicht in 434 px.

### Die Timeline-Balken verstecken ihre eigene Beschriftung

`_timeline_bar_label` (`adw/gui/app.py:1806`) liefert den vollständigen Namen
(`codex_review`, `backend`, `pytest`, `codex.author`). Die Beschriftung steht
aber **im Balken**, und der Balken ist proportional zur Dauer breit
(`run_detail.html:535`: `style="left:…%;width:…%"`, dazu `white-space: nowrap;
overflow: hidden`). Kurze Balken schneiden ihren eigenen Namen ab.

Im Browser direkt gemessen (`scrollWidth` gegen `clientWidth` je Balken) für
`16f39431` bei 1440 px, Stand 0.23.0: **24 von 31 Balken (77 %)** schneiden
ihre eigene Beschriftung ab. Extremfälle aus der Orchestrator-Spur: ein
`pytest`-Balken ist **6 px** breit und bräuchte **43 px**, ein `ruff`-Balken
**6 px** gegen **29 px**. Die **Spur**beschriftungen links
(`.tl-lane-label`, feste 7 rem) sind davon nicht betroffen und bleiben lesbar.

### Der Trace-Baum rendert vollständig — das ist eine Entscheidung, kein Versehen

`_tree_rows` (`adw/gui/app.py:131`) gibt jeden Knoten des Baums als Zeile aus.
Der Docstring sagt es ausdrücklich: „the column is not paged … What keeps the
column readable is the compaction …, not a cut: folding hides nothing, a window
did." `?offset` ist für den Trace-Baum inert, und die Blätter-Navigation wird
für ihn folgerichtig **nicht** mehr gerendert (`window_nav` erscheint in
`run_detail.html` nur noch für `tools_offset`, Zeile 258); an ihrer Stelle steht
die Zeilenbilanz `.trace-balance` (Zeile 479).

Die Folge ist eine DOM-Größe, die linear mit dem Lauf wächst. Gemessen über alle
21 Läufe: ein Baum hat im Median **570** Knoten, der größte heute
(`7fe9d702`, 1296 Events) **1208**. Für `16f39431` stimmt die Zahl der
`data-tree-entry`-Marker exakt mit der Knotenzahl überein (844 = 844) — genau
ein Marker je Knoten, wie die Zähldefinition es verlangt.

**Es gibt dafür keine Schranke und keinen Test.** Jede `CAP`-Assertion in
`tests/test_gui_bounded_dom.py` (Zeilen 148, 185, 189, 215) prüft ausschließlich
`data-tool-entry`; für `data-tree-entry` existiert nirgends eine Aussage über die
gerenderte Menge. `docs/GUI-SPEC.md` nennt in §9 als einzige Schranke eine
Lazy-Render-Regel ab 200 MB Logdatei — eine andere Dimension als Knotenzahl. Der
Kommentar „at most this many entry markers per collection" in der Testdatei
beschreibt damit einen Stand, den der Code für den Baum bewusst verlassen hat.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

Die Verdichtung des Trace-Baums (Ergebnis-Faltung, Wiederholungs- und
Gruppenknoten, drei Klappebenen), die Detail-Panes samt ihrem eigenen
Tools-Fenster (`?tools_offset`), die Auswahl per Klick, `?focus`-Deep-Links, das
Run-Kontext-Panel, die Registerkarten Artefakte und Raw, der SSE-Pfad, die
Zeitachse aus Brief 1 und die Run-Liste aus Brief 2 bleiben **unverändert**.
Siehe E2 bis E5.

## Aufgabe

**A1 — Das Arbeitsfeld steht direkt unter dem Kopf.** „Planned tasks" und
„Change scope" rücken **unter** das dreispaltige Arbeitsfeld und sind dort
**zugeklappt** — mit nativen `<details>`/`<summary>`, ohne JavaScript, ohne
Persistenz, wie die Sammelknoten des Trace-Baums es schon machen. Der
Trace-Baum beginnt damit unmittelbar unter dem Seitenkopf.

**A2 — Die Zusammenfassungszeile trägt die Aussage.** Jeder der beiden
zugeklappten Blöcke nennt in seiner `<summary>`-Zeile das, wofür man ihn heute
aufklappen muss: „Planned tasks" die Zahl der Aufgaben je Lane und deren
Zustand, „Change scope" die Zahl der geänderten Dateien mit der Summe der
Plus- und Minuszeilen. Fehlen die Daten, sagt die Zeile das — und klappt nicht
in einen leeren Block auf.

**A3 — Die Baum-Spalte bekommt Platz.** Das Verhältnis des
`trace-layout`-Grids wird so verschoben, dass die Baum-Spalte nicht mehr die
schmalste der drei ist. Die Spaltenzahl bleibt drei, die Reihenfolge bleibt,
`min-width: 0` und die Überlauf-Regeln bleiben.

**A4 — Timeline: Geometrie in die Spur, Wörter daneben.** Die Beschriftung
eines Balkens verlässt den proportional bemessenen Balken. Der Balken wird reine
Geometrie; sein Name erscheint an einer Stelle, die nicht von seiner Dauer
abhängt. Das `title`-Attribut bleibt erhalten, die Spurbeschriftung links
bleibt, `left`/`width` in Prozent bleiben die Geometriequelle, und die
Unterscheidung aktiv / wartend / noch laufend bleibt wie sie ist.

**A5 — Die Baum-Größe wird ausgesagt und geprüft, nicht gefenstert.** Ein Test
fixiert die Beziehung, die heute nur zufällig stimmt: je Baumknoten genau ein
`data-tree-entry`, keine Dopplung, bei mehreren Fixture-Größen. Die Spec sagt
ausdrücklich, dass die Baum-Spalte vollständig rendert und dass die
Lesbarkeit von der Verdichtung kommt, nicht von einem Schnitt — und dass die
Schranke „höchstens 200 Marker je Sammlung" nur noch für die Tools-Einträge
gilt. Der Kommentar in `tests/test_gui_bounded_dom.py` wird auf diesen Stand
gebracht.

**A6 — i18n.** Neue Beschriftungen (Zusammenfassungszeilen aus A2, etwaige
Timeline-Beschriftung aus A4) in `adw/gui/i18n.py` in beiden Sprachen,
identische Schlüsselmengen, Pluralformen korrekt.

**A7 — Doku und Changelog.** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
synchron: neue Blockreihenfolge der Run-Detail-Seite, die zugeklappten
Zusammenfassungen, die Timeline-Beschriftung, die Aussage zur Baum-Größe aus A5.
`CHANGELOG.md` und `CHANGELOG.de.md` synchron ergänzen.

## Normative Definitionen (bindend, nicht neu herzuleiten)

- **Reihenfolge der Run-Detail-Seite nach A1:** Kopf (Titel, Zeitachse,
  Registerkarten) → Arbeitsfeld (Trace-Baum │ Detail-Panes │ Run-Kontext) →
  „Planned tasks" (zugeklappt) → „Change scope" (zugeklappt). Keine weitere
  Umstellung.
- **Zugeklappt heißt** `<details>` ohne `open`-Attribut. Kein JavaScript, kein
  Client-Zustand, keine Persistenz, kein Query-Parameter. Ein Klick auf die
  Zusammenfassungszeile ist die einzige Bedienung.
- **Zusammenfassungszeile „Planned tasks":** je Lane deren Name, die Zahl der
  Aufgaben und der Lane-Zustand. Gibt es kein Plan-Skelett, wird der Block gar
  nicht gerendert — wie heute.
- **Zusammenfassungszeile „Change scope":** Zahl der geänderten Dateien über
  alle beobachteten Lanes sowie die Summen der Plus- und Minuszeilen. Binärdateien
  zählen bei der Dateizahl mit, tragen aber nichts zu den Zeilensummen bei.
  Liegt kein verwertbarer Diff vor, sagt die Zeile das.
- **Grid-Verhältnis nach A3:** Die Baum-Spalte ist mindestens so breit wie die
  Panes-Spalte. Die Kontext-Spalte behält ihre `minmax`-Untergrenze und bleibt
  die schmalste. Genaue Werte sind Gestaltungsspielraum.
- **Balkenbeschriftung nach A4:** Ob der Name über, unter oder neben dem Balken
  steht, ist Gestaltungsspielraum. Bindend ist: die Lesbarkeit des Namens hängt
  nicht mehr von der Balkenbreite ab, und in einer Spur mit vielen kurzen Balken
  überlagern sich die Namen nicht.
- **Zähldefinition aus A5:** je Knoten des serialisierten Baums genau ein
  `data-tree-entry` in der gerenderten Baum-Spalte. Ein nach A1 der
  Verdichtung an seinen Aufruf gehängtes Ergebnis behält dabei seinen eigenen
  Marker — das ist der heutige, korrekte Stand (844 Knoten, 844 Marker).

## Vorentscheidungen (entschieden — kein Finding, auch nicht im Review-Loop)

**E1** — Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
Webfont (`docs/GUI-SPEC.md:316`). Diese Frage ist entschieden.

**E2** — **Der Trace-Baum bekommt kein Fenster zurück.** `_tree_rows` rendert
bewusst vollständig; die Verdichtung hat den Schnitt ersetzt, und das steht so
im Code. Weder A5 noch Performance-Erwägungen noch ein Review-Finding führen
eine Blätterung, ein Lazy-Rendering oder eine Knotenobergrenze für die
Baum-Spalte wieder ein. Diese Frage ist entschieden.

**E3** — Die Verdichtung des Baums (Faltung, Wiederholungen, Gruppen, drei
Klappebenen, Standard-Faltung, `?focus`-Verhalten) wird **nicht angefasst**.
Diese Frage ist entschieden.

**E4** — Die Gestaltungsentscheidungen aus Brief 1 (Tokens, Skalen,
Signalfarbe, Dark Mode, Aufbau der Zeitachse) werden benutzt, nicht revidiert.
Es entstehen keine neuen Farben, keine neuen Schriftgrößen, keine neuen
Abstandswerte. Diese Frage ist entschieden.

**E5** — Die Zahlen und ihr Vokabular aus Brief 2 (Arbeit, Phasenzeit,
Wartezeit, Gesamt; Summierung über alle Spannen) werden benutzt, nicht
revidiert. Die Run-Liste wird nicht angefasst. Diese Frage ist entschieden.

**E6** — Das zweite Fenster über die Werkzeug-Einträge in den Detail-Panes
(`_tool_entries` / `_tool_window` / `?tools_offset`) und seine 200er-Schranke
bleiben **unverändert** — einschließlich der Blätter-Navigation dort. A5 sagt
nur aus, dass diese Schranke für den Baum nicht gilt. Diese Frage ist
entschieden.

**E7** — Kein Responsive-Umbau, keine Breakpoints, keine Mobilansicht. Diese
Frage ist entschieden.

**E8** — Keine Änderung an Ereignistypen, Instrumentierung, Event-Payloads,
`build_tree`, dem SSE-Pfad oder der Retention. Diese Frage ist entschieden.

## Nicht-Ziele / Scope-Deckel

Keine neue Route, kein neues Tab, keine Änderung an Auswahl-, Pane-, `?focus`-
oder Deep-Link-Verhalten, an den Registerkarten Artefakte und Raw, am
Recovery-Karten-Verhalten, an der Run-Liste, an der Zeitachse oder an den
Kennzahlen. Keine Persistenz, kein Polling, kein neues Zustands-Subsystem.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Volltextsuche oder Filterchips über dem Trace-Baum.
- Zusammenklappbare oder in der Breite ziehbare Spalten des Arbeitsfelds.
- Ein Lazy-Rendering des Baums oder irgendeine andere Knotenschranke (E2).
- Zoom, Schwenken oder Zeitlupe in der Timeline; Zusammenfassen von Balken.
- Zusammenführen von Zeitachse und Timeline zu einer einzigen Darstellung.
- Eine eigene Ansicht für die Reader-Probleme.
- Persistieren des Auf-/Zuklappzustands der beiden Zusammenfassungsblöcke.

## Contract-Hinweis

Single-Lane-Projekt (`backend`, siehe `.adw/config.yaml`). Dieses Issue ist
**rein darstellend**: `GET /api/runs` und `GET /api/runs/{repo}/{run_id}`
bleiben in allen Feldern, Typen und Werten unverändert — einschließlich
`tree`, `phases`, `raw`, `latest_context`, `problems` und der Kennzahlen aus
Brief 2. Es entstehen keine neuen Routen und keine neuen Query-Parameter. Ein
Regressionstest fixiert, dass die API-Antwort durch dieses Feature unverändert
bleibt. Nicht Teil des Contracts: Klassennamen, Reihenfolge der Blöcke im
Markup, Markup- und CSS-Wortlaut, Grid-Verhältnisse.

## Toolchain (Fakten, nicht aus Allgemeinwissen ableiten)

Gates sind `uv run ruff check .` und `uv run pytest -x -q`. Es gibt **kein**
flake8, **kein** isort, **kein** black; `ruff format` ist bewusst kein Gate.
Tests liegen flach unter `tests/` als `test_gui_*.py`. Für reines
Client-Verhalten steht der Harness `tests/gui_js_harness.js` /
`tests/gui_js_harness.py` bereit. Der Abnahmepunkt 10 in `docs/GUI-SPEC.md`
nennt noch „flake8 + isort" — veralteter Spec-Text, wird nicht befolgt.

## Akzeptanzkriterien (messbar)

1. **Reihenfolge:** Im gerenderten Dokument steht der Trace-Baum
   (`.trace-list`) **vor** „Planned tasks" und „Change scope".
2. **Zugeklappt:** Beide Blöcke rendern als `<details>` ohne `open`; im
   Ausgangszustand ist ihr Inhalt nicht sichtbar.
3. **Zusammenfassung „Planned tasks":** Die `<summary>`-Zeile nennt Lane-Name,
   Aufgabenzahl und Zustand, ohne dass der Block aufgeklappt werden muss.
4. **Zusammenfassung „Change scope":** Die `<summary>`-Zeile nennt die Zahl der
   geänderten Dateien und die Summen der Plus- und Minuszeilen; ein Lauf ohne
   verwertbaren Diff bekommt eine erklärende Zeile statt einer Null.
5. **Spaltenbreite:** Die Baum-Spalte ist im `trace-layout`-Grid mindestens so
   breit wie die Panes-Spalte (heute 434 px gegen 607 px).
6. **Umbrüche:** Der Anteil der Knotenbeschriftungen, die auf mehr als eine
   Zeile umbrechen, sinkt gegenüber dem heutigen Stand von 17,8 % (103 von 577
   bei `16f39431`, 1440 px, Stand 0.23.0) messbar.
7. **Timeline:** Für `16f39431` ist die Beschriftung **jedes** der 31 Balken
   lesbar, unabhängig von seiner Breite — insbesondere die 24 Balken, deren
   Beschriftung heute abgeschnitten wird (gemessen `scrollWidth > clientWidth`;
   u. a. `pytest` mit 6 px gegen 43 px Bedarf).
8. **Timeline, Geometrie unverändert:** `left` und `width` der Balken in
   Prozent sowie die Unterscheidung aktiv / wartend / noch laufend sind
   gegenüber heute unverändert.
9. **Zähldefinition:** Für mindestens drei verschiedene Fixture-Größen gilt: die
   Zahl der `data-tree-entry`-Marker in der Baum-Spalte ist gleich der Zahl der
   Knoten des serialisierten Baums — kein Knoten fehlt, keiner zählt doppelt.
10. **Kein Fenster:** `?offset` verändert die Baum-Spalte nicht, und für sie
    wird keine Blätter-Navigation gerendert (E2).
11. **Tools-Fenster unberührt:** `?tools_offset`, die 200er-Schranke der
    Tools-Einträge und deren Blätter-Navigation verhalten sich unverändert.
12. **Contract:** Die Antwort von `GET /api/runs/{repo}/{run_id}` ist durch
    dieses Feature unverändert.
13. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
    Laufzeit-Dependency, kein Frontend-Paket, kein CDN.

## Testumfang

Richtwert **~11 neue Tests** unter `tests/test_gui_*.py`; deutlich mehr als ~16
ist Scope-Drift. Die bestehenden GUI-Tests bleiben grün, ohne inhaltlich
umgeschrieben zu werden — mit einer erlaubten Ausnahme: Tests, die die heutige
Blockreihenfolge oder die heutige Balkenbeschriftung der Timeline festschreiben,
werden auf den neuen Stand gehoben, mit einem Kommentar, der sagt warum.
