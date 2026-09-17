# GUI-Redesign 2/2: Ein Lauf hat eine Wahrheit — und die Liste erzählt sie

Zweiter Brief der Redesign-Reihe. Der erste (Lauf `4609107b`) hat das
Gestaltungsfundament gelegt und das Phasenband zur maßstäblichen Zeitachse
gemacht. Dieser hier bringt die **Zahlen** in Ordnung, die die Oberfläche über
einen Lauf behauptet, und macht die Run-Liste zu dem, was sie sein soll: die
Antwort auf „was braucht mich, was läuft, was hat es gekostet".

**Abhängigkeit:** Dieser Brief setzt auf dem gemergten Ergebnis von Brief 1 auf.
Tokens, Schrift- und Abstandsskalen und die Zeitachse im Kopf des Run-Details
stammen von dort und werden hier **benutzt, nicht neu erfunden**. Die
Ausgangslage unten beschreibt ausschließlich Flächen, die Brief 1 ausdrücklich
nicht angefasst hat; sie ist am 2026-09-13 im Code verifiziert und vor dem Bauen
gegen den dann aktuellen Stand zu prüfen.

## Ausgangslage (im Code verifiziert, 2026-09-13)

### Die gemeldeten Zahlen eines Laufs sind für die meisten Läufe falsch

`_summary` (`adw/gui/app.py:1014`) baut die Kennzahlen eines Laufs. Es nimmt
`start` aus der **ersten** `run`-Spanne, aber `duration` und `cost` aus dem
`totals`-Payload der **letzten** (`adw/gui/app.py:1018`, `1061–1063`, über
`_run_span`, `adw/gui/app.py:938`). Ein Lauf mit Freigabe-Gates ist aber mehrere
CLI-Aufrufe und damit mehrere `run`-Spannen in einem Log — der Docstring von
`_run_span` sagt das ausdrücklich.

Über alle 20 vorhandenen Event-Logs nachgerechnet: **16 Läufe haben mehr als
eine `run`-Spanne.** Für sie meldet die GUI nur den letzten Abschnitt:

| Lauf | Spannen | gemeldet | tatsächlich |
|---|---:|---:|---:|
| `e4e70373` | 3 | **$0.00** / 124 s | **$7.52** / 1185 s |
| `f6ff2d80` | 2 | $0.61 / 65 s | $2.02 / 1943 s |
| `6d3f9974` | 2 | $30.52 / 2802 s | $49.81 / 4824 s |
| `f4942ef3` | 2 | $31.29 / 2970 s | $48.02 / 5099 s |
| `16f39431` | 3 | $47.16 / 4743 s | $56.88 / 6084 s |
| `ea16f932` | 4 | $35.68 / 4480 s | $46.24 / 7327 s |

Nur die vier Läufe ohne Gate (`6bb32b6d`, `81795e53`, `8b82561b`, `e93329fe`)
stimmen. Das ist auch die Erklärung für den scheinbaren Formatierungs-Widerspruch
bei den Kosten: das Run-Kontext-Panel zeigt den **richtigen** kumulierten Wert,
die Run-Liste den falschen. Es sind nicht zwei Formate derselben Zahl, sondern
zwei verschiedene Zahlen, von denen eine falsch ist.

### Es gibt drei verschiedene Zeitgrößen, und sie werden verwechselt

Über die Event-Logs nachgerechnet, je Lauf drei klar unterscheidbare Größen:

1. **Arbeit** — Summe der `totals.duration` über alle `run`-Spannen. Die Zeit,
   in der der Orchestrator wirklich lief. Sie trägt auch die Kosten.
2. **Phasenzeit** — Summe der Phasen-Spannen (erster bis letzter Zeitstempel je
   Phase). Das ist, was die Zeitachse aus Brief 1 als farbige Segmente zeichnet.
3. **Gesamt** — erster Phasenstart bis letztes Phasenende.

In **13 von 20** Läufen sind (1) und (2) auf die Sekunde gleich. In drei Läufen
laufen sie weit auseinander, weil eine Phasenspanne über eine Unterbrechung
hinweg offen blieb und dann Totzeit mitzählt: `7fe9d702` 8732 s Arbeit gegen
216 730 s Phasenzeit (**24,8-fach**), `f4942ef3` 5099 gegen 38 958 (7,6-fach),
`72a042ad` 4421 gegen 31 300 (7,1-fach). **Phasenzeit ist deshalb kein Maß für
Arbeit** und darf nicht so beschriftet werden.

Die Differenz zwischen (3) und (2) ist die Zeit an den Freigabe-Gates; sie
überschreitet in 9 von 20 Läufen ein Drittel der Gesamtzeit (`e4e70373`: 1185 s
Arbeit in 6,2 Tagen). Die Phasen-Spannen überlappen dabei in **keinem** Lauf
(0 überlappende Paare bei 20 Läufen) — die Zerlegung in Phasenzeit und Lücken
ist also eindeutig.

### Die Run-Liste beantwortet ihre Frage nicht

- 35 Zeilen, **Medianhöhe 172 px**, Tabellenhöhe **5784 px** (gemessen bei
  1440 px Viewport-Breite). Ursache ist die Issue-Spalte: sie zeigt die ersten
  120 Zeichen des rohen Issue-Textes (`_ISSUE_MAX = 120`,
  `adw/gui/app.py:830`), also Markdown samt `#`-Überschriften und
  Abschnittsmarken, umgebrochen über 6–8 Zeilen.
- `Phase` und `Status` sind zwei Spalten, die bei jedem abgeschlossenen Lauf
  denselben String zeigen (`done`/`done`, `escalated`/`escalated`).
- **Es gibt keine Sortier- und keine Filtersteuerung.** Im gerenderten Dokument:
  0 `th a`, 0 `form`, 0 `select`. `docs/GUI-SPEC.md` §7.2 A verlangt beides
  wörtlich („Sortable, filter by repo and status"). Die dort ebenfalls verlangte
  Gruppierung nach Status **existiert** bereits (`_status_rank`,
  `adw/gui/app.py:2465–2468`: stabile Sortierung, neueste zuerst innerhalb der
  Gruppe) und bleibt unangetastet.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

Das Run-Detail behält seine heutige Blockreihenfolge (Kopf mit Zeitachse,
„Planned tasks", „Change scope", dann das dreispaltige Arbeitsfeld), seine
Registerkarten, die Detail-Panes, das Run-Kontext-Panel, die Verdichtung des
Trace-Baums, die Blätterung und die Timeline-Registerkarte mit ihren Spuren und
Balken. Der Trace-Baum beginnt weiterhin weit unten auf der Seite, die
Timeline-Balken tragen weiterhin verstümmelte Beschriftungen, und der DOM-Deckel
im Baum bleibt gerissen. **Das ist bekannt und hier ausdrücklich nicht
Gegenstand** — siehe E3 und die Deferred-Liste.

## Aufgabe

**A1 — Kennzahlen über den ganzen Lauf.** `duration`, `cost` und, sofern im
`totals`-Payload vorhanden, die Token-Zahlen werden über **alle abgeschlossenen
`run`-Spannen** des Logs summiert statt aus der letzten genommen. `start` bleibt
die erste Spanne. Damit stimmen Run-Liste und Run-Detail-Kopf zugleich, weil
beide aus `_summary` speisen.

**A2 — Drei benannte Zeitgrößen, ein Vokabular.** Die Oberfläche benennt
**Arbeit**, **Gesamt** und die Wartezeit als drei verschiedene Größen und
benutzt für jede überall dasselbe Wort. Die Zeitachse aus Brief 1 wird auf
dieses Vokabular gebracht: ihre farbigen Segmente sind **Phasenzeit**, ihre
Lücken sind **Wartezeit**, und die Beschriftung „Arbeit" verschwindet dort,
weil Phasenzeit keine Arbeit misst (siehe Ausgangslage). Die echte Arbeitszeit
aus A1 tritt als eigene, benannte Zahl daneben.

Konkret: Release 0.22.0 hat die drei Zahlen unter der Schiene mit den
i18n-Schlüsseln `tl_work` / `tl_waiting` / `tl_total` beschriftet („Arbeit" /
„Warten" / „Gesamt", `adw/gui/i18n.py`). Der von `tl_work` beschriftete Wert
ist die **Summe der Phasendauern**, nicht die Arbeitszeit — der Schlüssel wird
deshalb umbenannt und neu beschriftet, und die Arbeitszeit aus A1 kommt als
zusätzlicher, eigener Schlüssel hinzu. `tl_waiting` und `tl_total` bleiben
inhaltlich richtig und behalten ihre Bedeutung.

**A3 — Die Issue-Spalte wird eine Titelzeile.** Die Liste zeigt je Lauf eine
einzeilige, abgeschnittene Titelzeile statt rohen Markdowns; der vollständige
Issue-Text bleibt über das `title`-Attribut erreichbar. Ableitungsregel siehe
Normative Definitionen.

**A4 — Phase und Status werden eine Spalte.** Eine Spalte, die den Status
nennt und die Phase nur dann zusätzlich, wenn sie etwas hinzufügt (laufender
oder wartender Lauf). Bei einem abgeschlossenen Lauf steht dort genau ein Wort.

**A5 — Sortieren und Filtern.** Die Liste bekommt die von §7.2 A verlangte
Steuerung: Sortierung nach den Spalten Start, Dauer, Kosten und Ereigniszahl,
sowie Filter nach Repo und nach Status. **Serverseitig über Query-Parameter**,
wie es das Raw-Tab mit `?raw_q` / `?raw_type` schon macht — kein Client-Zustand,
keine Persistenz. Die Statusgruppierung aus §7.2 A behält Vorrang vor jeder
gewählten Sortierung: `awaiting_approval` steht immer oben.

**A6 — i18n.** Alle neuen Beschriftungen (Zeitgrößen, Spaltenköpfe,
Filter-/Sortiersteuerung, leere Ergebnismenge) liegen in `adw/gui/i18n.py` in
beiden Sprachen vor, identische Schlüsselmengen, Pluralformen korrekt. Die
Sprachumschaltung erhält die gewählte Sortierung und Filterung, weil beide in der
URL stehen.

**A7 — Doku und Changelog.** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
beschreiben synchron die drei Zeitgrößen mit ihren Rechenregeln, die
Summierung über alle Spannen, die Titelableitung und die Sortier-/Filter-
Parameter. `CHANGELOG.md` und `CHANGELOG.de.md` werden synchron ergänzt. Da A1
eine gemeldete Zahl korrigiert, gehört der Eintrag unter `Fixed`/`Behoben` mit
der Angabe, dass frühere Werte für Läufe mit Gates zu niedrig waren.

## Normative Definitionen (bindend, nicht neu herzuleiten)

### Die drei Zeitgrößen

- **Arbeit** = Summe von `totals.duration` über alle `run`-Spannen mit `end`.
  Eine noch offene Spanne trägt nichts bei (sie hat noch keine `totals`).
  Gibt es keine abgeschlossene Spanne, ist Arbeit unbestimmt und wird nicht
  angezeigt — nie als 0 erfunden.
- **Phasenzeit** = Summe der Phasen-Spannen mit parsebarem `start` und `end`.
  Das ist die Fläche der farbigen Segmente der Zeitachse, **nicht** Arbeit.
- **Gesamt** = kleinster Phasenstart bis größtes Phasenende; bei einer noch
  aktiven Phase ohne Ende bis zum Zeitpunkt des Seitenaufbaus.
- **Wartezeit** = Gesamt − Phasenzeit. Sie ist die Summe der Lücken zwischen den
  Phasen. Dass diese Zerlegung eindeutig ist, ist verifiziert: die Phasen-Spannen
  überlappen in keinem der 20 Läufe.
- Kosten und Token-Zahlen werden wie Arbeit über alle abgeschlossenen Spannen
  summiert.

### Titelableitung aus dem Issue-Text (A3)

Angewandt auf den rohen Issue-Text, in dieser Reihenfolge:

1. Unter den ersten zwölf Zeilen die erste, die mit `#` beginnt; ihr Text ohne
   führende `#` und ohne umgebende Leerzeichen. Eine Überschrift, die nur
   „Issue" benennt (Regex `^issue\b`, ohne Beachtung der Groß-/Kleinschreibung),
   wird übersprungen — die nächste Überschrift gewinnt.
2. Gibt es keine solche Überschrift: die erste nicht-leere Zeile.
3. Aus dem Ergebnis wird ein führendes `ADW-Issue:` oder `Issue:` entfernt.
4. Länger als **90 Zeichen** wird auf 89 gekürzt und mit `…` markiert.
5. Ist der Text leer oder fehlt er, bleibt die Zelle leer — nie ein Platzhalter.

Gegen die 21 vorhandenen Läufe durchgerechnet: 14 erhalten den Text einer
Überschrift, die übrigen 7 ihre erste Zeile. Kein Ergebnis enthält ein `#`.

### Sortier- und Filterparameter (A5)

- `?sort=` mit den Werten `start`, `duration`, `cost`, `events`; `?dir=` mit
  `asc` oder `desc`. Unbekannte oder fehlende Werte fallen auf das heutige
  Verhalten zurück (`start`, `desc`) — nie ein Fehler, nie eine leere Liste.
- `?repo=` filtert auf einen Repo-Slug, `?status=` auf einen Statuswert.
  Unbekannte Werte ergeben eine **leere Trefferliste mit erklärendem Hinweis**,
  nicht die ungefilterte Liste und keinen Fehler.
- Die Statusgruppierung (`awaiting_approval` zuerst, dann `running`, dann der
  Rest) wird **vor** der gewählten Sortierung angewendet und ist durch sie nicht
  abschaltbar. Diese Frage ist entschieden.
- Alle Parameter überleben die Sprachumschaltung, weil der bestehende
  `switch_qs`-Mechanismus die Query weiterträgt.

## Vorentscheidungen (entschieden — kein Finding, auch nicht im Review-Loop)

**E1** — Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
Webfont. Vanilla JS, handgeschriebenes CSS, System-Schriften
(`docs/GUI-SPEC.md:316`). Diese Frage ist entschieden.

**E2** — Sortierung und Filterung sind **serverseitig über Query-Parameter**.
Kein clientseitiges Sortieren, keine Tabellen-Bibliothek, kein `localStorage`,
keine Cookies, kein neuer Client-Zustand. Diese Frage ist entschieden.

**E3** — **Das Run-Detail wird nicht umgebaut.** Blockreihenfolge,
Registerkarten, Detail-Panes, Run-Kontext-Panel, Trace-Baum, Verdichtung,
Blätterung und die Timeline-Registerkarte bleiben exakt, wie Brief 1 sie
hinterlässt. Berührt wird dort ausschließlich, was A1 und A2 an **Zahlen und
deren Beschriftung** ändern. Kein Abbau, kein Umbau, kein Ausbau darüber
hinaus, auch nicht als Review-Finding. Diese Frage ist entschieden.

**E4** — Die Gestaltungsentscheidungen aus Brief 1 (Tokens, Skalen, Signalfarbe,
Dark Mode, Aufbau der Zeitachse) werden **benutzt, nicht revidiert**. Neue
Flächen bedienen sich aus den vorhandenen Tokens; es entstehen keine neuen
Farben, keine neuen Schriftgrößen, keine neuen Abstandswerte. Diese Frage ist
entschieden.

**E5** — Phasenzeit wird nirgends als Arbeit beschriftet. Die beiden Größen
gehen in drei Läufen um das 7- bis 25-Fache auseinander; eine gemeinsame
Beschriftung wäre in genau den Fällen falsch, die am meisten erklären. Diese
Frage ist entschieden.

**E6** — Eine unbestimmte Zahl bleibt leer. Weder Arbeit noch Kosten noch Tokens
werden als `0` dargestellt, wenn keine abgeschlossene Spanne vorliegt. Das ist
die bestehende Linie des Run-Kontext-Panels („Null fields render empty, never a
fabricated 0") und gilt hier genauso. Diese Frage ist entschieden.

**E7** — Der Issue-Rohtext wird **nicht** als Markdown gerendert, weder in der
Liste noch im `title`. Die Titelableitung ist Textverarbeitung, kein Rendering.
Diese Frage ist entschieden.

**E8** — Keine Änderung an Ereignistypen, Instrumentierung, Event-Payloads,
`build_tree`, der Verdichtungsschicht, der Blätterung, dem SSE-Pfad oder der
Retention. Die Korrektur aus A1 geschieht ausschließlich bei der **Auswertung**
des vorhandenen Logs. Diese Frage ist entschieden.

**E9** — Kein Responsive-Umbau, keine Breakpoints, keine Mobilansicht (wie
Brief 1, E12). Diese Frage ist entschieden.

## Nicht-Ziele / Scope-Deckel

Keine neue Route, kein neues Tab, keine Änderung an der Run-Detail-Anordnung,
keine Änderung am Trace-Baum oder seinen Eintragsmarkern, keine Änderung an der
Timeline-Registerkarte, keine Persistenz, kein Polling, kein neues
Zustands-Subsystem, keine Änderung an Auswahl-, Pane-, `?focus`- oder
Deep-Link-Verhalten.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

Erkannt, begründet, aber nicht Gegenstand dieses Issues. Was hier steht, wird im
Codex-/Fix-Zyklus **nicht** nachgebaut:

- Die Anordnung der Blöcke im Run-Detail (der Trace-Baum beginnt weit unterhalb
  des Seitenanfangs).
- Die Größe der gerenderten Baum-Spalte. Sie ist keine gerissene Invariante,
  sondern eine bewusste Entscheidung: `_tree_rows` (`adw/gui/app.py:131`)
  rendert den Baum vollständig, weil die Verdichtung den Schnitt ersetzt hat.
  Was fehlt, ist eine Aussage darüber in der Spec und ein Test, der die
  Zähldefinition fixiert.
- Spur- und Balkenbeschriftung der Timeline-Registerkarte.
- Volltextsuche über Läufe, gespeicherte Filter, Spaltenauswahl, Export.
- Eine Kennzahl „Kosten pro Lauf gegen Kosten pro Phase" oder sonstige
  Auswertung über mehrere Läufe hinweg.
- Rückwirkende Korrektur oder Migration alter Logs — es wird nur anders
  ausgewertet, nie geschrieben.

## Contract-Hinweis

Single-Lane-Projekt (`backend`, siehe `.adw/config.yaml`).

- **Bewusste Bedeutungsänderung:** `GET /api/runs` und
  `GET /api/runs/{repo}/{run_id}` liefern `duration` und `cost` weiterhin unter
  denselben Namen und Typen, aber mit korrigierter Bedeutung: sie beziffern ab
  jetzt den **ganzen Lauf** statt nur der letzten CLI-Spanne. Für Läufe mit
  Gates steigen die Werte dadurch. Das ist der Zweck des Issues und im
  Spec-Abschnitt „Contract" ausdrücklich zu benennen.
- **Additiv:** neue Felder für die benannten Zeitgrößen (Arbeit, Phasenzeit,
  Wartezeit, Gesamt) an der Stelle, an der sie gebraucht werden. Kein
  vorhandenes Feld wird entfernt oder umbenannt; `start`, `status`, `phase`,
  `issue`, `event_count`, `dry_run`, `has_trace` und `repo_exists` bleiben
  wörtlich, wie sie sind.
- Die Route `/` nimmt neue optionale Query-Parameter entgegen (`sort`, `dir`,
  `repo`, `status`). Ohne Parameter ist das Verhalten unverändert.
- `tree`, `raw`, `latest_context` und `problems` bleiben unverändert.
- Regressionstests fixieren beides: die neuen Felder sind da, und die
  unveränderten Felder sind unverändert.

## Toolchain (Fakten, nicht aus Allgemeinwissen ableiten)

Gates sind `uv run ruff check .` und `uv run pytest -x -q`. Es gibt **kein**
flake8, **kein** isort, **kein** black; `ruff format` ist bewusst kein Gate.
Tests liegen flach unter `tests/` als `test_gui_*.py`. Für reines
Client-Verhalten steht der Harness `tests/gui_js_harness.js` /
`tests/gui_js_harness.py` bereit. Der Abnahmepunkt 10 in `docs/GUI-SPEC.md`
nennt noch „flake8 + isort" — veralteter Spec-Text, wird nicht befolgt.

## Akzeptanzkriterien (messbar)

1. **Summierung:** Für ein Log mit drei abgeschlossenen `run`-Spannen
   (`totals.duration` 100 / 200 / 300, `totals.cost` 1 / 2 / 3) meldet
   `_summary` 600 bzw. 6 — nicht 300 bzw. 3.
2. **Offene Spanne:** Für ein Log, dessen letzte `run`-Spanne noch offen ist,
   zählen nur die abgeschlossenen Spannen; die offene trägt nichts bei, und es
   entsteht keine Ausnahme.
3. **Keine abgeschlossene Spanne:** Arbeit und Kosten bleiben leer, nicht `0`.
4. **Ein Gate-Lauf stimmt:** Für den Lauf `16f39431` meldet die Run-Liste
   $56.88 (heute $47.16); für `e4e70373` einen Wert größer null (heute `$0.00`).
5. **Zeitgrößen getrennt:** Für ein Log, in dem eine Phasenspanne eine
   Unterbrechung überdauert (Phasenzeit deutlich größer als die Summe der
   Spannen-Dauern), zeigt die Oberfläche beide Zahlen verschieden an, und
   Phasenzeit trägt nirgends die Beschriftung „Arbeit".
6. **Zerlegung stimmt:** Phasenzeit + Wartezeit = Gesamt bis auf Rundung.
7. **Titelzeile:** Ein Issue mit `# Überschrift` liefert `Überschrift` ohne
   `#`; eine Überschrift `# Issue (…)` wird übersprungen; ein Issue ohne
   Überschrift liefert seine erste nicht-leere Zeile; nichts länger als 90
   Zeichen; ein leerer Issue-Text liefert eine leere Zelle.
8. **Zeilenhöhe:** Die Issue-Zelle rendert einzeilig, und der vollständige
   Text steht im `title`-Attribut.
9. **Eine Statusspalte:** Bei einem abgeschlossenen Lauf erscheint der
   Statuswert genau einmal je Zeile.
10. **Sortierung:** `?sort=cost&dir=desc` ordnet nach Kosten absteigend,
    **aber** ein `awaiting_approval`-Lauf steht weiterhin über allen anderen.
11. **Filter:** `?status=escalated` liefert nur eskalierte Läufe; ein
    unbekannter Wert liefert eine leere Liste mit Hinweis, keinen Fehler und
    nicht die ungefilterte Liste.
12. **Robustheit:** Unbekannte `sort`/`dir`-Werte fallen auf `start`/`desc`
    zurück, ohne Ausnahme und ohne leere Liste.
13. **Sprachumschaltung:** Sortierung und Filter überleben den Wechsel der
    Sprache.
14. **Contract:** `/api/runs` und `/api/runs/{repo}/{run_id}` behalten alle
    heutigen Feldnamen und -typen; die neuen Zeitgrößen sind zusätzlich da;
    `tree` ist strukturell unverändert.
15. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
    Laufzeit-Dependency, kein Frontend-Paket, kein CDN.

## Testumfang

Richtwert **~14 neue Tests** unter `tests/test_gui_*.py`; deutlich mehr als ~20
ist Scope-Drift. Die bestehenden GUI-Tests bleiben grün. **Achtung:** Tests, die
heute die Kennzahlen eines Laufs mit Gates erwarten, prüfen möglicherweise die
alten, zu niedrigen Werte — solche Erwartungen werden auf die korrigierten Werte
gehoben, und zwar mit einem Kommentar, der sagt warum. Das ist die einzige
erlaubte inhaltliche Änderung an bestehenden Tests.
