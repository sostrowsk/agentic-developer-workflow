# Spec: GUI-Redesign 1/2 — Gestaltungsfundament und die Zeitachse des Laufs

## Goal

Die ADW-GUI bekommt ein tragfähiges Gestaltungsfundament und ein maßstäbliches
Phasenband:

1. Alle Farben, Schriftgrößen und Abstände stammen aus einem benannten Token-
   und Skalensystem statt aus verstreuten Literalen. Ein Dark Mode entsteht
   allein durch Umschalten dieser Tokens über `prefers-color-scheme`.
2. Signalfarben werden diszipliniert: „ein Mensch muss handeln", „arbeitet
   gerade" und „technisch wartend" sind visuell eindeutig unterscheidbar.
3. Das Phasenband im Kopf des Run-Detail wird von der Chip-Reihe (Breite folgt
   der Beschriftungslänge) zur maßstäblichen Zeitachse: jede Phase sitzt an
   ihrer wirklichen zeitlichen Position mit ihrer wirklichen Dauer als Breite;
   die Lücken an den Freigabe-Gates werden als Wartesegmente sichtbar; darunter
   stehen drei Zahlen: Arbeit, Warten, Gesamt.

Die Informationsarchitektur (Run-Liste, Anordnung der Blöcke im Run-Detail,
DOM-Deckel) bleibt in diesem Brief ausdrücklich unberührt — sie ist Gegenstand
von Brief 2.

## Scope

Betroffen sind ausschließlich Darstellung und die eine additive
Contract-Erweiterung:

- **`adw/gui/static/app.css`** — Token-Schicht, Schrift- und Abstandsskala,
  Signalfarben-Disziplin, Dark Mode, reduced-motion, Zeitachsen-Styling.
- **`adw/gui/templates/run_detail.html`** — die Chip-Reihe des Phasenbands wird
  an ihrer heutigen Stelle im Kopf durch die Zeitachse ersetzt; die Chip-Reihe
  bleibt als Rückfallebene erhalten und wird im neuen Gestaltungssystem
  gerendert. Der Run-Kontext-Eintrag für `cost_usd` (heute `ctx_num`,
  Zeile 96) wechselt auf den gemeinsamen Kostenformatierer.
- **`adw/gui/app.py`** — `_phase_bar` (Zeile 1090) gibt je Phase zusätzlich
  `start` und `end` aus (additiv); serverseitige Kostenformatierung über
  `_fmt_cost`.
- **`adw/gui/static/app.js`** — ausschließlich die Kosten-Projektion des
  Run-Kontext-Panels (heute `formatContextValue`, rundet auf 6 Nachkommastellen):
  sie übernimmt das einheitliche Kostenformat, damit es auch nach Knotenauswahl
  und bestehender Live-Aktualisierung erhalten bleibt.
- **`adw/gui/i18n.py`** — neue Beschriftungsschlüssel der Zeitachse (Arbeit,
  Warten, Gesamt, Segment-Titel) in beiden Sprachen über die bestehende Mechanik.
- **`docs/GUI-SPEC.md` / `docs/GUI-SPEC.de.md`** — synchrone Beschreibung von
  Token-System, den beiden Schriftrollen, Signalfarben-Regel, Dark Mode und der
  Zeitachse samt Rechenregeln.
- **`CHANGELOG.md` / `CHANGELOG.de.md`** — synchrone Ergänzung der
  `Unreleased`-Sektion.

### Normative Definitionen (übernommen, nicht neu herzuleiten)

**Farbtokens** — die Werte sind durchgerechnet (jede Text-auf-Grund-Paarung
erreicht in beiden Themes mindestens 4,5:1) und werden wörtlich übernommen:

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

`--accent`, `--fg`, `--muted`, `--border` dürfen als Aliase auf die neuen
Tokens bestehen bleiben, solange Markup sie referenziert; neue Regeln benutzen
die neuen Namen. Die Dry-Run-Kennzeichnung behält ihren eigenen, von `--signal`
verschiedenen Braunton — wegen der Null-Hex-Regel (AC 1) als benanntes Token in
der Token-Schicht — und ihre `position: sticky`-Regel.

**Skalen:**

- Schriftgrößen, sechs Stufen: `0.75 · 0.8125 · 0.875 · 1 · 1.25 · 1.5` rem.
  Kein anderer Wert.
- Abstände, sechs Stufen: `0.25 · 0.5 · 0.75 · 1 · 1.5 · 2` rem. Kein anderer
  Wert für `padding`/`margin`.
- Radien: `3px` und `6px`, beide heute schon in Gebrauch. Keine weiteren.
- Monospace-Stack: `ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo,
  Consolas, monospace` — für Maschinenwerte (Lauf-IDs, Sequenznummern, Pfade,
  Dauern, Kosten, Token- und Ereigniszahlen, Phasennamen). Die UI-Schrift
  bleibt wörtlich der heutige Stack. Jede Zahl, die sich ändern kann, bekommt
  `font-variant-numeric: tabular-nums`.

**Zustandsfarben nach der Bereinigung:**

| Zustand | Token |
|---|---|
| `.phase-completed`, `.node-done`, `.node-passed` | `--ok` |
| `.phase-active`, `.node-running` | `--busy` |
| `.node-waiting`, Warte-Balken der Timeline, Wartesegment der Achse | `--wait` |
| `.phase-awaiting`, `.run-status.status-awaiting_approval` | `--signal` |
| `.phase-failed`, `.node-failed`, `.problem`, `.raw-problem` | `--fail` |
| `.phase-pending` | `--ink-soft` |

**Zeitachse — Rechenregeln (bindend):**

- `T_start` = kleinstes parsebares `start`. `T_end` = größtes `end`; hat eine
  Phase mit Status `active` kein `end`, ist `T_end` der Zeitpunkt des
  Seitenaufbaus. `T = T_end − T_start`.
- Ist `T` nicht bestimmbar oder ≤ 0, oder hat weniger als eine Phase einen
  parsebaren `start`, wird die heutige Chip-Reihe gerendert. Sie bleibt die
  Rückfallebene und wird nicht entfernt.
- Segment je Phase: Versatz `(start − T_start) / T`, Breite `(end − start) / T`.
  Die Beschriftungslänge beeinflusst Position und Breite nicht. Eine Phase mit
  `start`, ohne `end` und mit Status `active` reicht bis zum rechten Rand und
  wird als offen markiert (dieselbe Konvention wie `.tl-bar.bar-running`).
- Wartesegmente: Phasen nach `start` sortieren; ist der Abstand zwischen dem
  `end` der einen und dem `start` der nächsten größer als null, entsteht dort
  ein als „Warten" beschriftetes Segment. Warum gewartet wurde, wird nicht
  ermittelt und nicht behauptet. Vor dem ersten Phasenstart und nach dem
  letzten Phasenende wird keine Wartezeit erfunden.
- Eine Phase ohne parsebaren `start` (nie gelaufen, z. B. `integration`)
  bekommt kein Segment; sie bleibt unter der Schiene in der Legende gedämpft
  sichtbar. Phasennamen und Dauern bleiben lesbar.
- Mindestbreite jedes Segments: **2 px**. Sehr kurze Phasen sind dadurch bewusst
  nicht maßstäblich; die Zeitwerte und Summen ändert das nicht. Diese Frage ist
  entschieden.
- Drei Zahlen unter der Schiene: **Arbeit** = Summe der Phasendauern (bei einer
  offenen aktiven Phase zählt die bis zum Seitenaufbau verstrichene Zeit),
  **Warten** = Summe der Lücken, **Gesamt** = `T`. Es gilt
  Arbeit + Warten = Gesamt bis auf Rundung.

## Non-Goals / Scope-Deckel

- Keine neue Route, kein neues Tab, keine Änderung an `build_tree`
  (`adw/gui/model.py`), an der Verdichtungsschicht, an der Blätterung
  (`?offset`, `?tools_offset`, Fenstergröße 100), am SSE-Pfad, an den
  Ereignistypen, an der Instrumentierung oder an der Retention.
- Keine Änderung an der API-Antwort außer der additiven Erweiterung um `start`
  und `end` (A6). `tree`, `raw`, `latest_context`, `problems` und die
  Statuswerte bleiben unverändert; `name`, `status`, `duration` bleiben
  wörtlich, wie sie sind.
- Keine neue Persistenz, kein Polling, kein neues Zustands-Subsystem. Keine
  Änderung an Auswahl-, Pane-, `?focus`- oder Deep-Link-Verhalten.
- **Kein Umbau der Informationsarchitektur (E3):** Reihenfolge und Anordnung
  von „Planned tasks", „Change scope", Trace-Baum, Detail-Panes,
  Run-Kontext-Panel, Registerkarten und Run-Liste bleiben exakt, einschließlich
  der Spalten der Run-Liste und der Breitenverhältnisse des
  `trace-layout`-Grids.
- **Keine Umbenennung von Klassennamen (E2):** alle heutigen Zustands- und
  Struktur-Klassennamen bleiben erhalten; verändert wird nur, wie die
  Selektoren aussehen.
- **Kein zusätzliches Element je wiederholtem Eintrag (E4):** gestaltet wird
  über Selektoren auf dem vorhandenen Markup. Neue Elemente nur im Kopf für die
  Zeitachse — ihre Anzahl hängt von der festen Phasenmenge ab, nicht von der
  Ereigniszahl.
- **`--signal` bedeutet genau eine Sache (E5):** kein anderer Zustand, kein
  Hover, kein Fokusring, keine Überschrift, kein Diagramm-Element trägt diesen
  Ton.
- **Kein manueller Theme-Umschalter (E6):** Dark Mode ausschließlich über
  `prefers-color-scheme`; kein `localStorage`, kein Query-Parameter, kein neuer
  Client-Zustand.
- **Kein zweites Band (E7):** die Zeitachse ersetzt die Chip-Reihe an deren
  heutiger Stelle. Die Registerkarte „Timeline" wird nicht angefasst — weder
  Spuren noch Balkengeometrie noch Beschriftungen; ihre vorhandenen Selektoren
  erhalten lediglich die globale Token-Gestaltung (ihre Warte-Balken tragen
  `--wait` gemäß Zustandsfarben-Tabelle).
- **`duration`-Angabe des Laufs unangetastet (E8):** Run-Liste und
  Timeline-Kopf zeigen weiter die heutige Zahl; welche der drei Dauer-Zahlen
  die „Dauer eines Laufs" ist, entscheidet Brief 2 — kein Finding.
- **Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
  Webfont (E1):** nur System-Schrift-Stacks, handgeschriebenes CSS, Vanilla JS.
- **Animationsverzicht (E9):** außer Farb- und Hintergrundübergängen von
  höchstens 150 ms, die `prefers-reduced-motion` respektieren, keine Bewegung,
  kein Einblenden, kein Skalieren, kein Schatten-Spiel.
- Artefakte bleiben getreuer Monospace-Text, keine Markdown-Bibliothek (E10).
- Keine Änderung an der i18n-Mechanik, nur neue Schlüssel in beiden Sprachen
  (E11); Event-, Pfad-, Werkzeug- und Payload-Inhalte werden nicht übersetzt.
- **Kein Responsive-Umbau (E12):** keine Breakpoints, keine Mobilansicht. Die
  bestehende Regel `html, body { max-width: 100%; overflow-x: hidden }` und die
  `min-width: 0`-Regeln der Grid-Kinder bleiben.

## Acceptance Criteria (messbar)

1. **Farb-Literale gebündelt.** `adw/gui/static/app.css` enthält außerhalb der
   beiden Token-Blöcke (`:root` und `@media (prefers-color-scheme: dark)`)
   **null** Hex-Literale (heute 39); andere Farbnotationen umgehen die
   Token-Schicht nicht.
2. **Skalen greifen.** Die Datei benutzt höchstens **sechs** verschiedene
   `font-size`-Werte (heute 9) und höchstens **sechs** verschiedene rem-Werte
   für `padding`/`margin` (heute 21); alle stammen aus den Skalen.
3. **Fallback-Variablen sind echte Tokens.** `--ok`, `--fail` (bzw. `--err`,
   das auf `--fail` verweisen darf) und `--code-bg` sind in beiden Themes
   definiert; kein `var(--name, literal)`-Aufruf verlässt sich mehr auf einen
   Fallback für eine nicht existierende Variable.
4. **Dark Mode & reduced-motion.** `@media (prefers-color-scheme: dark)`
   definiert **jedes** der zwölf normativ gelisteten Tokens neu.
   `@media (prefers-reduced-motion: reduce)` existiert und schaltet die
   Übergänge ab.
5. **Doppelrolle aufgelöst.** Kein Selektor setzt denselben Farbton für
   `.node-waiting` und für `.phase-awaiting` / `.status-awaiting_approval`;
   „arbeitet gerade" trägt ein von beiden verschiedenes Token.
6. **Gepinnte Strings vorhanden.** Unverändert vorhanden: `.phase-active`,
   `.phase-completed`, `.node-running`, `.node-done`, `node-waiting`,
   `awaiting`, `trace-summary`, `nowrap` sowie eine `dry`-Regel mit
   `position: sticky|fixed`.
7. **Zeitachse, Maßstab.** Für den Lauf `16f39431` ist das `build`-Segment
   mindestens **20-mal** so breit wie das `ci`-Segment (durchgerechnet:
   2607 s / 124 s = 21,0).
8. **Zeitachse, Warten.** Für `16f39431` weist die Achse ein Wartesegment aus,
   und die drei Zahlen lauten Arbeit 6084 s, Warten 3289 s, Gesamt 9372 s
   (Formatierung frei, Toleranz ±2 s); es gilt Arbeit + Warten = Gesamt bis auf
   Rundung.
9. **Zeitachse, lückenloser Lauf.** Für `81795e53` (Summe der Phasendauern
   3656 s = Gesamt 3656 s) zeigt die Achse **kein** Wartesegment und Warten = 0.
10. **Zeitachse, Rückfallebene.** Ein Lauf ohne parsebare Phasen-Zeitstempel
    (bzw. `T ≤ 0` oder weniger als eine Phase mit parsebarem `start`) rendert
    die heutige Chip-Reihe — nicht eine leere oder kaputte Schiene und kein
    zweites Band daneben.
11. **Eine Formatierung je Größe.** Die Kosten erscheinen in Run-Liste,
    Timeline-Kopf und Run-Kontext-Panel in **genau einem** Format
    (`_fmt_cost`-Konvention, z. B. `$47.16`) — auch nach Knotenauswahl und
    bestehender Live-Aktualisierung; die Client-Projektion in `app.js` fällt
    nicht auf die sechsstellige Anzeige zurück. Ein fehlender Kostenwert bleibt
    leer (bestehende „nie als 0"-Regel). `56.87666` kommt in keiner
    formatierten Kostenanzeige mehr vor; getreu wiedergegebene Payload-Inhalte
    (E10) sind davon unberührt. `ctx_num` bleibt für die übrigen Zahlenfelder,
    wie es ist.
12. **Marker-Zahl unverändert.** Die Anzahl der `data-tree-entry`-Marker je
    gerenderter Seite ist bei gleichen Eingabedaten gegenüber heute
    **unverändert**, und kein Element wird je Eintrag hinzugefügt (E4).
13. **Contract additiv erweitert.** `GET /api/runs/{repo}/{run_id}` liefert je
    `phases`-Eintrag zusätzlich `start` und `end` (ISO-8601-String wie im
    Event-Log, oder `null`). Ein fehlendes Phasenende bleibt in der API `null`;
    der Seitenaufbau-Zeitpunkt ist reine Darstellungsregel und wird nicht als
    Phasenende ausgegeben. `name`, `status`, `duration` und die Struktur von
    `tree` sind unverändert.
14. **Gates & Dependency-Freiheit.** `uv run ruff check .` und
    `uv run pytest -x -q` laufen grün. Keine neue Laufzeit-Dependency, kein
    Frontend-Paket, kein CDN, keine Webfont.
15. **i18n vollständig.** Die neuen Zeitachsen-Beschriftungen (Arbeit, Warten,
    Gesamt, Segment-Titel) liegen in `adw/gui/i18n.py` in beiden Sprachen vor,
    mit identischen Schlüsselmengen und korrekten Pluralformen.
16. **Doku & Changelog synchron.** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
    beschreiben synchron Token-System, die beiden Schriftrollen, die
    Signalfarben-Regel, den Dark Mode und die Zeitachse samt Rechenregeln; die
    `Unreleased`-Sektion in `CHANGELOG.md` und `CHANGELOG.de.md` ist synchron
    ergänzt.

## Definition of Done

- Alle Akzeptanzkriterien 1–16 sind erfüllt und durch Tests belegt; Non-Goals
  und Scope-Deckel sind eingehalten.
- Richtwert **~12 neue Tests** unter `tests/test_gui_*.py`; deutlich mehr als
  ~18 ist Scope-Drift. Mindestens abgedeckt: Token-Disziplin (AC 1–3),
  Dark-Mode- und reduced-motion-Block (AC 4), aufgelöste Farb-Doppelrolle
  (AC 5), Erhalt der gepinnten Selektoren (AC 6), Maßstab der Achse (AC 7),
  Wartesegment mit und ohne Lücke (AC 8/9), Rückfallebene (AC 10),
  einheitliche Kostenformatierung (AC 11), unveränderte Marker-Zahl (AC 12),
  Contract-Regression für `phases` und `tree` (AC 13). Für reines
  Client-Verhalten steht `tests/gui_js_harness.js` /
  `tests/gui_js_harness.py` bereit.
- Die Referenzläufe (`16f39431`, `81795e53`) werden über reproduzierbare
  Testdaten geprüft — maßgeblich sind die angegebenen Zeitrelationen und
  Summen, keine Abhängigkeit von lokal vorhandenen historischen
  Run-Verzeichnissen.
- Die bestehenden GUI-Tests bleiben grün, ohne inhaltlich umgeschrieben zu
  werden.
- Beide Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. (Der
  veraltete Verweis auf flake8/isort in `docs/GUI-SPEC.md` begründet keine
  weiteren Gates; `ruff format` und black sind bewusst keine Gates.)

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

Diese Punkte sind erkannt und begründet, gehören aber in Brief 2 oder später.
Im Codex-/Fix-Zyklus werden sie **nicht** nachgebaut, auch nicht als Finding:

- Informationsarchitektur der Run-Liste: umbrechende Issue-Spalte, Dopplung von
  `Phase` und `Status`, fehlende Sortier- und Filtersteuerung.
- Anordnung der Blöcke im Run-Detail (Trace-Baum beginnt bei 854 px).
- Der gerissene DOM-Deckel im Baum (844 statt 200) und die fehlende Assertion
  für `data-tree-entry` — hier folgt daraus nur die Wahrung der Marker-Zahl
  (AC 12).
- Die drei verschiedenen Dauer-Begriffe eines Laufs (siehe E8).
- Spur- und Balkenbeschriftung der Timeline (heute auf „co", „b", „p"
  verstümmelt).
- Manueller Theme-Umschalter, Mobilansicht, eigenes Icon-Set,
  Barrierefreiheits-Audit über die Kontrastschwelle (4,5:1) hinaus.
- Zusätzliche Zeitmodell-Härtung für hypothetisch überlappende Phasen,
  Ursachenklassifikation von Wartezeiten oder ein persistiertes
  Zeitachsenmodell — es gelten die vorgegebenen Rechenregeln auf den
  vorhandenen Ereignisdaten.
