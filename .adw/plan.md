# Plan — GUI-Redesign 1/2: Gestaltungsfundament und die Zeitachse des Laufs

Single-Lane-Projekt (`.adw/config.yaml`): nur der Workstream **backend**. Er
umfasst hier auch CSS, JS, Templates, i18n und Doku — die GUI-Assets sind Teil
des Python-Pakets; es gibt keinen frontend-Lane.

Betroffene Dateien (aus der Spec, abschließend): `adw/gui/static/app.css`,
`adw/gui/templates/run_detail.html`, `adw/gui/app.py`, `adw/gui/static/app.js`,
`adw/gui/i18n.py`, `docs/GUI-SPEC.md`, `docs/GUI-SPEC.de.md`, `CHANGELOG.md`,
`CHANGELOG.de.md` und neue Tests unter `tests/test_gui_*.py`.

Der einzige API-Berührungspunkt ist die **additive** Erweiterung der
`phases`-Einträge von `GET /api/runs/{repo}/{run_id}` um `start` und `end`
(`.adw/contract.yaml`). Token-Werte, Klassennamen, CSS-/Markup-Wortlaut und
Helfer-Signaturen sind bewusst NICHT Contract-Fläche.

TDD ist Pflicht (`pytest`-Gate hat `tdd: true` in `.adw/config.yaml`): Tests
zuerst, RED bestätigen, dann implementieren. Richtwert **~12 neue Tests**,
deutlich mehr als ~18 ist Scope-Drift. Die bestehenden GUI-Tests bleiben grün,
**ohne inhaltlich umgeschrieben** zu werden.

## Workstream: backend

### B0 — RED: Tests zuerst (`tests/test_gui_*.py`)

Neue Tests anlegen (an bestehende `test_gui_*.py`-Konventionen halten; für
reines Client-Verhalten steht `tests/gui_js_harness.js` /
`tests/gui_js_harness.py` bereit). Die Referenzläufe `16f39431` und `81795e53`
werden über **reproduzierbare Testdaten** nachgebildet, nicht über lokal
vorhandene Run-Verzeichnisse. Vor der Implementierung ausführen und RED
bestätigen. Keine Tests für Deferred-Themen (z. B. hypothetisch überlappende
Phasen).

1. **Token-Disziplin, Hex-Literale (AC 1).** `app.css` enthält außerhalb der
   beiden Token-Blöcke (`:root`, `@media (prefers-color-scheme: dark)`) **null**
   Hex-Literale; auch andere Farbnotationen umgehen die Token-Schicht nicht.
2. **Skalen (AC 2).** Höchstens **sechs** verschiedene `font-size`-Werte und
   höchstens **sechs** verschiedene rem-Werte für `padding`/`margin`, alle aus
   den vorgegebenen Skalen; Radien nur `3px`/`6px`.
3. **Echte Fallback-Tokens (AC 3).** `--ok`, `--fail` (bzw. `--err` als Verweis
   auf `--fail`) und `--code-bg` sind in beiden Themes definiert; kein
   `var(--name, literal)` verlässt sich mehr auf einen Fallback für eine nicht
   existierende Variable.
4. **Dark Mode & reduced-motion (AC 4).** `@media (prefers-color-scheme: dark)`
   definiert **jedes** der zwölf normativen Tokens neu;
   `@media (prefers-reduced-motion: reduce)` existiert und schaltet die
   Übergänge ab.
5. **Aufgelöste Farb-Doppelrolle (AC 5).** Kein Selektor setzt denselben Farbton
   für `.node-waiting` und für `.phase-awaiting` / `.status-awaiting_approval`;
   „arbeitet gerade" trägt ein von beiden verschiedenes Token.
6. **Gepinnte Selektoren erhalten (AC 6).** `.phase-active`, `.phase-completed`,
   `.node-running`, `.node-done`, `node-waiting`, `awaiting`, `trace-summary`,
   `nowrap` sowie eine `dry`-Regel mit `position: sticky|fixed` sind unverändert
   vorhanden.
7. **Zeitachse, Maßstab (AC 7).** Für die `16f39431`-Fixture ist das
   `build`-Segment (2607 s) mindestens **20-mal** so breit wie das
   `ci`-Segment (124 s); zusätzlich prüfen, dass die 2-px-Mindestbreite die
   Zeitwerte und Summen nicht verändert.
8. **Zeitachse, Warten (AC 8).** Für `16f39431` weist die Achse ein
   Wartesegment aus; die drei Zahlen sind Arbeit 6084 s, Warten 3289 s, Gesamt
   9372 s (Formatierung frei, Toleranz ±2 s); Arbeit + Warten = Gesamt bis auf
   Rundung (gebrochene Fixture-Zeitwerte für den Rundungsfall).
9. **Zeitachse, lückenloser Lauf (AC 9).** Für `81795e53` (Phasendauern-Summe
   3656 s = Gesamt 3656 s) zeigt die Achse **kein** Wartesegment und Warten = 0.
10. **Zeitachse, offene Phase & Rückfallebene (AC 10).** Eine aktive Phase ohne
    `end` reicht bei kontrolliertem Seitenaufbau-Zeitpunkt bis zum rechten Rand
    und ist als offen markiert; ungestartete Phasen erscheinen nur gedämpft in
    der Legende. Parametrisiert: ohne parsebare Zeitstempel, bei `T ≤ 0` oder
    < 1 Phase mit parsebarem `start` wird die heutige Chip-Reihe gerendert —
    keine leere/kaputte Schiene, kein zweites Band.
11. **Einheitliche Kostenformatierung (AC 11).** Kosten erscheinen in Run-Liste,
    Timeline-Kopf und Run-Kontext-Panel in **genau einem** Format
    (`_fmt_cost`-Konvention, `$47.16`) — auch nach Knotenauswahl und
    Live-Aktualisierung (Client-Verhalten über den JS-Harness). `56.87666` kommt
    in keiner formatierten Kostenanzeige mehr vor (`→ $56.88`); ein fehlender
    Wert bleibt leer. Negative Assertions auf formatierte Kostenfelder
    beschränken (getreue Payload-Wiedergabe nach E10 bleibt unberührt).
12. **Marker-Zahl unverändert (AC 12).** Die Anzahl der `data-tree-entry`-Marker
    je gerenderter Seite ist bei gleichen Eingabedaten unverändert; kein Element
    wird je wiederholtem Eintrag hinzugefügt (E4).
13. **Contract-Regression `phases`/`tree` (AC 13).** `GET
    /api/runs/{repo}/{run_id}` liefert je `phases`-Eintrag zusätzlich `start`
    und `end` (ISO-8601 wie im Event-Log oder `null`); ein fehlendes Phasenende
    bleibt in der API `null` (kein Seitenaufbau-Zeitpunkt). `name`, `status`,
    `duration` und die Struktur von `tree` sind unverändert.
14. **i18n vollständig (AC 15).** Die neuen Zeitachsen-Schlüssel (Arbeit,
    Warten, Gesamt, Segment-Titel) liegen in beiden Sprachen vor, mit
    identischen Schlüsselmengen und korrekten Pluralformen.

RED bestätigen: die neuen Tests gezielt ausführen; die CSS-, Zeitachsen-,
Kosten- und Contract-Tests scheitern vor der Implementierung.

### B1 — `_phase_bar` liefert `start`/`end` additiv (A6, AC 13)

In `adw/gui/app.py:1090` (`_phase_bar`) je Phaseneintrag zusätzlich `start` und
`end` ausgeben — die ISO-Zeitstempel, die intern bereits aus den `phase`-Events
ermittelt werden, unverändert übernommen, oder `null`. `name`, `status`,
`duration` bleiben wörtlich; kein Feld wird entfernt oder umbenannt, kein
weiteres Feld kommt hinzu. `duration` wird nicht aus `start`/`end` neu
abgeleitet; ein fehlendes Ende bleibt `null` (kein Seitenaufbau-Zeitpunkt in
der API). Die Erweiterung erscheint automatisch in der Detailantwort
(`adw/gui/app.py:2212`); Statusableitung einschließlich Approval- und
Eskalationsregeln bleibt unverändert.

### B2 — Serverseitige Kostenformatierung (A7, AC 11)

`adw/gui/templates/run_detail.html:96/105`: der Run-Kontext-Eintrag für
`cost_usd` wechselt von `ctx_num` (`round(6)`) auf den vorhandenen
`_fmt_cost`-Formatierer, wie ihn Run-Liste und Timeline-Kopf schon nutzen.
`ctx_num` bleibt für die übrigen Zahlenfelder unverändert. Ein fehlender Wert
bleibt leer; für Null- und Randwerte gilt unverändert die bestehende
`_fmt_cost`-Konvention („nie als 0"-Regel) — keine neue Formatregel erfinden.

### B3 — Client-Kostenprojektion (A7, AC 11)

`adw/gui/static/app.js`, `formatContextValue`: der `cost_usd`-Zweig rundet
heute auf sechs Nachkommastellen. Er übernimmt das `_fmt_cost`-Format
(`$` + zwei Nachkommastellen), damit der Wert nach Knotenauswahl und
bestehender Live-Aktualisierung identisch zur serverseitigen Anzeige bleibt.
`null`/`undefined` bleibt leer. Keine weitere JS-Änderung (E6: kein
Theme-Umschalter, kein neuer Client-Zustand).

### B4 — Token-Schicht, Skalen, Signalfarben, Dark Mode (A1–A4, AC 1–6)

`adw/gui/static/app.css`:

- **A1/AC 1,3:** Alle Farbwerte in zwei Token-Blöcke ziehen — `:root` (hell)
  und `@media (prefers-color-scheme: dark)`. Die zwölf normativen Tokens
  wörtlich übernehmen (`--paper`, `--surface`, `--ink`, `--ink-soft`, `--rule`,
  `--code-bg`, `--signal`, `--signal-ink`, `--ok`, `--fail`, `--busy`,
  `--wait`). Jeder heutige Literalwert wird auf genau ein Token abgebildet;
  weitere benötigte Farben (insbesondere das Dry-Run-Braun, verschieden von
  `--signal`) werden benannte Tokens in der Token-Schicht (Null-Hex-Regel).
  `--accent`/`--fg`/`--muted`/`--border` dürfen als Aliase bestehen bleiben;
  neue Regeln benutzen die neuen Namen.
- **A2/AC 2:** Sechsstufige Schrift- und Abstandsskala (Werte wörtlich aus der
  Spec), zwei Radien (`3px`, `6px`). Zweite Schriftrolle: Monospace-Stack
  `ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo, Consolas, monospace`
  für Maschinenwerte (Lauf-IDs, Sequenznummern, Pfade, Dauern, Kosten, Token-
  und Ereigniszahlen, Phasennamen) über Selektoren auf vorhandenem Markup;
  UI-Schrift bleibt wörtlich der heutige Stack. Änderbare Zahlen erhalten
  `font-variant-numeric: tabular-nums`.
- **A3/AC 5:** Zustandsfarben gemäß Tabelle der Spec zuordnen; `--signal`
  bedeutet genau „ein Mensch muss handeln" (E5), `.node-waiting`/
  Timeline-Warte-Balken/Achsen-Wartesegment tragen `--wait`, `.phase-active`/
  `.node-running` tragen `--busy`, `.phase-pending` trägt `--ink-soft`.
- **A4/AC 4:** Dark Mode ausschließlich über `prefers-color-scheme` (E6);
  Übergänge nur Farbe/Hintergrund, höchstens 150 ms, abgeschaltet unter
  `@media (prefers-reduced-motion: reduce)` (E9).
- Zeitachsen-Styling ergänzen (siehe B5); den Chip-Fallback in dasselbe
  Gestaltungssystem einbinden. Klassennamen bleiben erhalten (E2); die
  Timeline-Registerkarte erhält nur die globale Token-Gestaltung (E7).
  Dry-Run-Sticky-Regel, `trace-layout`-Breitenverhältnisse, `min-width: 0`
  und `html, body { max-width: 100%; overflow-x: hidden }` bleiben (E12).

### B5 — Phasenband → maßstäbliche Zeitachse (A5, AC 7–10, AC 12)

`adw/gui/templates/run_detail.html:357` (Chip-Reihe im Kopf): an derselben
Stelle die Zeitachse rendern; die Chip-Reihe bleibt als **Rückfallebene**
erhalten (E7: kein zweites Band, Timeline-Registerkarte unangetastet). Die
Darstellungswerte werden serverseitig beim Seitenaufbau aus den
Phasen-Zeitstempeln berechnet (darstellungsnaher Helfer in `adw/gui/app.py`
oder Jinja — Implementierungsdetail, keine Contract-Fläche); die JSON-Antwort
wird nicht um ein Zeitachsenmodell erweitert. Kein neues Polling, keine
laufende Client-Uhr, keine Änderung am SSE-/Refresh-Verhalten. Die
Zeitachsen-Elemente sitzen nur im Kopf und haben eine feste,
phasenzahl-abhängige Anzahl — kein Element je Baum-/Ereigniseintrag (E4,
AC 12).

Bindende Rechenregeln (aus der Spec, nicht neu herleiten):

- `T_start` = kleinstes parsebares `start`; `T_end` = größtes `end`, bei
  aktiver offener Phase ohne `end` der Seitenaufbau-Zeitpunkt;
  `T = T_end − T_start`.
- Ist `T` nicht bestimmbar/≤ 0 oder hat < 1 Phase parsebaren `start`: heutige
  Chip-Reihe rendern (AC 10).
- Segment je Phase: Versatz `(start − T_start)/T`, Breite `(end − start)/T`;
  Beschriftungslänge beeinflusst Position/Breite nicht. Aktive offene Phase
  reicht bis zum rechten Rand, als offen markiert (Konvention
  `.tl-bar.bar-running`).
- Wartesegmente: Phasen nach `start` sortieren; nur eine positive Lücke
  zwischen `end` und nächstem `start` ergibt ein „Warten"-Segment (kein Grund
  erfunden; keine Wartezeit vor dem ersten Start oder nach dem letzten Ende).
- Phase ohne parsebaren `start`: kein Segment, aber gedämpft in der Legende
  sichtbar (Name und Dauer lesbar).
- Mindestbreite jedes Segments **2 px** (ändert Zeitwerte/Summen nicht).
- Drei Zahlen unter der Schiene: **Arbeit** = Summe Phasendauern (offene
  aktive Phase bis Seitenaufbau), **Warten** = Summe Lücken, **Gesamt** = `T`;
  Arbeit + Warten = Gesamt bis auf Rundung (Rundung erst bei der Darstellung).

### B6 — i18n (A8, AC 15)

`adw/gui/i18n.py`: über die bestehende Mechanik (E11) die neuen
Zeitachsen-Schlüssel (Arbeit, Warten, Gesamt, Segment-Titel) in **beiden**
Sprachen ergänzen — identische Schlüsselmengen, korrekte Pluralformen. Keine
Änderung an der i18n-Mechanik; Event-/Pfad-/Werkzeug-/Payload-Inhalte werden
nicht übersetzt.

### B7 — Doku & Changelog (A9, AC 16)

`docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron um Token-System, die
beiden Schriftrollen, die Signalfarben-Regel, den Dark Mode und die Zeitachse
samt Rechenregeln (einschließlich Rückfallebene, offener Phasen und
2-px-Ausnahme) ergänzen. Die `Unreleased`-Sektion in `CHANGELOG.md` und
`CHANGELOG.de.md` synchron ergänzen. Der veraltete flake8/isort-Verweis in
`docs/GUI-SPEC.md` begründet keine weiteren Gates.

### B8 — Gates grün und Abschluss (AC 14)

- `uv run ruff check .`
- `uv run pytest -x -q`

Alle neuen Tests und die unveränderten bestehenden GUI-Tests sind grün. Keine
neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine Webfont (E1).
Abschließend am Diff prüfen, dass der Änderungsumfang auf die oben genannten
Dateien beschränkt ist, alle AC 1–16 zugeordnet sind und die
Non-Goals/Scope-Deckel eingehalten wurden.

## Non-Goals / Scope-Deckel (aus der Spec, bindend)

- Keine neue Route, kein neues Tab, keine Änderung an `build_tree`
  (`adw/gui/model.py`), an der Verdichtungsschicht, an der Blätterung
  (`?offset`, `?tools_offset`, Fenstergröße 100), am SSE-Pfad, an den
  Ereignistypen, an der Instrumentierung oder an der Retention.
- Keine Änderung an der API-Antwort außer der additiven Erweiterung um `start`
  und `end`. `tree`, `raw`, `latest_context`, `problems` und die Statuswerte
  bleiben unverändert; `name`, `status`, `duration` bleiben wörtlich.
- Keine neue Persistenz, kein Polling, kein neues Zustands-Subsystem; keine
  Änderung an Auswahl-, Pane-, `?focus`- oder Deep-Link-Verhalten.
- **E2** keine Klassennamen-Umbenennung; **E3** kein IA-Umbau (Reihenfolge/
  Anordnung, Run-Listen-Spalten, `trace-layout`-Grid bleiben); **E4** kein
  zusätzliches Element je wiederholtem Eintrag; **E5** `--signal` genau eine
  Bedeutung; **E6** kein manueller Theme-Umschalter; **E7** kein zweites Band,
  Timeline-Registerkarte nur global tokenisiert; **E8** `duration`-Angabe des
  Laufs unangetastet; **E9** Animationsverzicht bis auf ≤150 ms Farb-/
  Hintergrundübergänge; **E10** Artefakte bleiben getreuer Monospace-Text;
  **E11** i18n-Mechanik unverändert; **E12** kein Responsive-Umbau.

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
