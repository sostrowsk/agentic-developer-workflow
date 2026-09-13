# Spec — GUI-Redesign 2/2: Ein Lauf hat eine Wahrheit — und die Liste erzählt sie

## Goal

Die Oberfläche berichtet die Kennzahlen eines Laufs (Dauer, Kosten, Token)
korrekt über den **ganzen** Lauf statt nur über die letzte CLI-Spanne, benennt
die verschiedenen Zeitgrößen (Arbeit, Phasenzeit, Wartezeit, Gesamt) mit einem
einheitlichen Vokabular, ohne sie zu verwechseln, und macht die Run-Liste zur
Antwort auf „was braucht mich, was läuft, was hat es gekostet": einzeilige
Titel, eine statt zwei redundanter Spalten, serverseitiges Sortieren und
Filtern. Aufbauend auf Brief 1 (Lauf `4609107b`), dessen Tokens, Skalen und
Zeitachse hier **benutzt, nicht revidiert** werden.

## Scope

- **A1 — Kennzahlen über den ganzen Lauf.** `_summary` (`adw/gui/app.py:1014`)
  summiert `duration`, `cost` und die im `totals`-Payload vorhandenen
  Token-Zahlen über **alle abgeschlossenen `run`-Spannen** des Logs, statt sie
  aus der letzten zu nehmen (`_run_span`, `adw/gui/app.py:938`). `start` bleibt
  die erste Spanne; Status- und Phasenableitung bleiben unverändert. Run-Liste
  und Run-Detail-Kopf speisen beide aus `_summary` und stimmen dadurch überein.
- **A2 — Drei benannte Zeitgrößen, ein Vokabular.** Die Oberfläche benennt
  **Arbeit**, **Gesamt** und die **Wartezeit** als drei verschiedene Größen;
  für jede wird überall dasselbe Wort verwendet. Die Zeitachse aus Brief 1 wird
  auf dieses Vokabular gebracht: farbige Segmente = **Phasenzeit**, Lücken =
  **Wartezeit**. Der i18n-Schlüssel `tl_work` (heute „Arbeit"/„Work",
  `adw/gui/i18n.py`) beschriftet die Summe der Phasendauern und wird deshalb
  umbenannt und neu beschriftet; die echte Arbeitszeit aus A1 kommt als
  zusätzlicher, eigener Schlüssel und eigene Zahl daneben. `tl_waiting` und
  `tl_total` behalten Bedeutung und Wert.
- **A3 — Titelzeile statt Roh-Markdown.** Die Issue-Spalte zeigt je Lauf eine
  einzeilige, abgeschnittene Titelzeile (Ableitung siehe Normative
  Definitionen); der vollständige rohe Issue-Text bleibt über das
  `title`-Attribut erreichbar (als Attributtext korrekt escaped, nie als
  Markdown oder HTML interpretiert).
- **A4 — Phase und Status werden eine Spalte.** Eine Spalte, die den Status
  nennt und die Phase nur dann zusätzlich, wenn sie etwas hinzufügt (laufender
  oder wartender Lauf). Bei einem abgeschlossenen Lauf steht dort genau ein
  Wort; `done`/`done` und `escalated`/`escalated` entfallen.
- **A5 — Sortieren und Filtern, serverseitig.** Die Run-Liste erhält sichtbare
  Steuerung für Sortierung nach Start, Dauer, Kosten und Ereigniszahl sowie
  Filter nach Repo und Status, serverseitig über Query-Parameter wie beim
  Raw-Tab (`?raw_q`/`?raw_type`) — kein Client-Zustand, keine Persistenz.
  Dauer und Kosten sortieren nach den korrigierten Laufwerten aus A1,
  Ereigniszahl nach `event_count`. Die bestehende Statusgruppierung
  (`_status_rank`, `adw/gui/app.py:2546–2548`: `awaiting_approval`, dann
  `running`, dann Rest; neueste zuerst innerhalb der Gruppe) bleibt
  unangetastet und behält Vorrang vor jeder gewählten Sortierung.
- **A6 — i18n.** Alle neuen Beschriftungen (Zeitgrößen, Spaltenköpfe,
  Sortier-/Filtersteuerung, leere Ergebnismenge) liegen in `adw/gui/i18n.py`
  in beiden Sprachen vor: identische Schlüsselmengen, korrekte Pluralformen.
  Die Sprachumschaltung erhält Sortierung und Filterung, weil beide in der URL
  stehen und der bestehende `switch_qs`-Mechanismus die Query weiterträgt.
- **A7 — Doku und Changelog.** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
  beschreiben synchron die drei Zeitgrößen samt Rechenregeln, die Summierung
  über alle Spannen, die bewusste Bedeutungsänderung der API-Kennzahlen samt
  additiver Felder, die Titelableitung und die Sortier-/Filterparameter
  einschließlich Rückfall- und Leerzuständen. `CHANGELOG.md`/`CHANGELOG.de.md`
  werden synchron ergänzt; der A1-Eintrag steht unter `Fixed`/`Behoben` mit
  dem Hinweis, dass frühere Werte für Läufe mit Gates zu niedrig waren.

## Normative Definitionen (bindend, nicht neu herzuleiten)

### Die drei Zeitgrößen

- **Arbeit** = Summe von `totals.duration` über alle `run`-Spannen mit `end`.
  Eine noch offene Spanne trägt nichts bei (sie hat noch keine `totals`).
  Gibt es keine abgeschlossene Spanne, ist Arbeit unbestimmt und wird nicht
  angezeigt — nie als 0 erfunden.
- **Phasenzeit** = Summe der Phasen-Spannen mit parsebarem `start` und `end`.
  Das ist die Fläche der farbigen Segmente der Zeitachse, **nicht** Arbeit.
- **Gesamt** = kleinster Phasenstart bis größtes Phasenende; bei einer noch
  aktiven Phase ohne Ende bis zum Zeitpunkt des Seitenaufbaus (der
  Auswertungszeitpunkt ist ein Berechnungszeitpunkt, kein nachgetragenes
  Phasenende).
- **Wartezeit** = Gesamt − Phasenzeit, die Summe der Lücken zwischen den
  Phasen. Die Zerlegung ist eindeutig: die Phasen-Spannen überlappen in keinem
  der 20 vorhandenen Läufe.
- Kosten und Token-Zahlen werden wie Arbeit über alle abgeschlossenen Spannen
  summiert; ohne abgeschlossene Spanne bleiben sie leer.

Verbindliches Vokabular (je Größe überall dasselbe Wort):
Arbeit/Work · Phasenzeit/Phase time · Wartezeit/Waiting · Gesamt/Total.

### Titelableitung aus dem Issue-Text (A3)

Angewandt auf den rohen Issue-Text, in dieser Reihenfolge:

1. Unter den ersten zwölf Zeilen die erste, die mit `#` beginnt; ihr Text ohne
   führende `#` und ohne umgebende Leerzeichen. Eine Überschrift, die nur
   „Issue" benennt (Regex `^issue\b`, ohne Beachtung der
   Groß-/Kleinschreibung), wird übersprungen — die nächste Überschrift
   gewinnt. Eine Überschrift außerhalb der ersten zwölf Zeilen erhält keinen
   Vorrang.
2. Gibt es keine solche Überschrift: die erste nicht-leere Zeile.
3. Aus dem Ergebnis wird ein führendes `ADW-Issue:` oder `Issue:` entfernt.
4. Länger als **90 Zeichen** wird auf 89 gekürzt und mit `…` markiert.
5. Ist der Text leer oder fehlt er, bleibt die Zelle leer — nie ein
   Platzhalter.

Die Titelableitung ist Textverarbeitung, kein Rendering (E7).

### Sortier- und Filterparameter (A5)

- `?sort=` mit `start`, `duration`, `cost`, `events`; `?dir=` mit `asc` oder
  `desc`. Unbekannte oder fehlende Werte fallen auf das heutige Verhalten
  zurück (`start`, `desc`) — nie ein Fehler, nie eine leere Liste.
- `?repo=` filtert auf einen Repo-Slug, `?status=` auf einen Statuswert.
  Kombinierte Filter wirken als Schnittmenge. Unbekannte Werte ergeben eine
  **leere Trefferliste mit erklärendem, lokalisiertem Hinweis**, nicht die
  ungefilterte Liste und keinen Fehler.
- Die Statusgruppierung (`awaiting_approval` zuerst, dann `running`, dann der
  Rest) wird **vor** der gewählten Sortierung angewendet und ist durch sie
  nicht abschaltbar. Diese Frage ist entschieden.
- Alle Parameter überleben die Sprachumschaltung über den bestehenden
  `switch_qs`-Mechanismus.

## Contract

Single-Lane-Projekt (`backend`, `.adw/config.yaml`).

- **Bewusste Bedeutungsänderung:** `GET /api/runs` und
  `GET /api/runs/{repo}/{run_id}` liefern `duration` und `cost` weiterhin
  unter denselben Namen und Typen, aber mit korrigierter Bedeutung — sie
  beziffern ab jetzt den **ganzen Lauf** statt nur der letzten CLI-Spanne.
  Für Läufe mit Gates steigen die Werte; das ist der Zweck des Issues.
- **Additiv:** neue Felder für die benannten Zeitgrößen an der
  Laufzusammenfassung, `null` wenn unbestimmt (E6): `work_seconds`,
  `phase_seconds`, `wait_seconds`, `total_seconds` (Zahl oder `null`, nach den
  normativen Definitionen). Token-Summen werden additiv in der Form
  ausgewiesen, die der `totals`-Payload hergibt — sofern vorhanden, nie als
  erfundene 0.
- Kein vorhandenes Feld wird entfernt, umbenannt oder umgedeutet; `start`,
  `status`, `phase`, `issue`, `event_count`, `dry_run`, `has_trace` und
  `repo_exists` bleiben wörtlich, wie sie sind. Insbesondere wird das
  `issue`-Feld **nicht** zum 90-Zeichen-Anzeigetitel umgedeutet; der Titel ist
  Darstellungsdatum der Liste.
- Die Route `/` nimmt neue optionale Query-Parameter entgegen (`sort`, `dir`,
  `repo`, `status`). Ohne Parameter ist das Verhalten unverändert — gemeint
  sind Auswahl und Reihenfolge, nicht die ausdrücklich korrigierten Kennzahlen
  oder die neue Listendarstellung. Die API-Routen erhalten keine
  Sortier-/Filterparameter.
- `tree`, `raw`, `latest_context` und `problems` bleiben unverändert.
- Regressionstests fixieren beides: die neuen Felder sind da, und die
  unveränderten Felder sind unverändert.

## Non-Goals / Scope-Deckel

- Keine neue Route, kein neues Tab, keine Persistenz, kein Polling, kein
  neues Zustands-Subsystem, keine Änderung an Auswahl-, Pane-, `?focus`- oder
  Deep-Link-Verhalten.
- **E1** — Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN,
  keine Webfont; Vanilla JS, handgeschriebenes CSS, System-Schriften.
- **E2** — Sortierung und Filterung ausschließlich serverseitig über
  Query-Parameter; kein clientseitiges Sortieren, keine Tabellen-Bibliothek,
  kein `localStorage`, keine Cookies, kein neuer Client-Zustand.
- **E3** — Das Run-Detail wird nicht umgebaut: Blockreihenfolge,
  Registerkarten, Detail-Panes, Run-Kontext-Panel, Trace-Baum samt
  Eintragsmarkern, Verdichtung, Blätterung und Timeline-Registerkarte bleiben
  exakt, wie Brief 1 sie hinterlässt. Berührt wird dort ausschließlich, was
  A1/A2 an Zahlen und deren Beschriftung ändern — auch nicht als
  Review-Finding.
- **E4** — Gestaltungsentscheidungen aus Brief 1 (Tokens, Skalen, Signalfarbe,
  Dark Mode, Aufbau der Zeitachse) werden benutzt, nicht revidiert; keine
  neuen Farben, Schriftgrößen oder Abstandswerte.
- **E5** — Phasenzeit wird nirgends als Arbeit beschriftet (die Größen gehen
  in drei Läufen um das 7- bis 25-Fache auseinander).
- **E6** — Eine unbestimmte Zahl bleibt leer, nie `0` (Linie des
  Run-Kontext-Panels).
- **E7** — Der Issue-Rohtext wird nicht als Markdown gerendert, weder in der
  Liste noch im `title`.
- **E8** — Keine Änderung an Ereignistypen, Instrumentierung, Event-Payloads,
  `build_tree`, Verdichtungsschicht, Blätterung, SSE-Pfad oder Retention; die
  A1-Korrektur geschieht ausschließlich bei der **Auswertung** des
  vorhandenen Logs.
- **E9** — Kein Responsive-Umbau, keine Breakpoints, keine Mobilansicht.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

Erkannt, begründet, aber nicht Gegenstand; wird im Codex-/Fix-Zyklus **nicht**
nachgebaut:

- Die Anordnung der Blöcke im Run-Detail (der Trace-Baum beginnt weit
  unterhalb des Seitenanfangs).
- Die Größe der gerenderten Baum-Spalte (`_tree_rows`, `adw/gui/app.py:131`):
  bewusst vollständig gerendert, weil die Verdichtung den Schnitt ersetzt hat.
  Was fehlt, ist eine Aussage darüber in der Spec und ein Test, der die
  Zähldefinition fixiert.
- Spur- und Balkenbeschriftung der Timeline-Registerkarte.
- Volltextsuche über Läufe, gespeicherte Filter, Spaltenauswahl, Export.
- Eine Kennzahl „Kosten pro Lauf gegen Kosten pro Phase" oder sonstige
  Auswertung über mehrere Läufe hinweg.
- Rückwirkende Korrektur oder Migration alter Logs — es wird nur anders
  ausgewertet, nie geschrieben.
- Neue Mechanismen zur Erkennung oder persistenten Kennzeichnung hypothetisch
  überlappender Phasenspannen; die überlappungsfreie Zerlegung ist verifiziert
  und wird zugrunde gelegt.

## Akzeptanzkriterien (messbar)

1. **Summierung:** Für ein Log mit drei abgeschlossenen `run`-Spannen
   (`totals.duration` 100/200/300, `totals.cost` 1/2/3) meldet `_summary`
   600 bzw. 6 — nicht 300 bzw. 3. `start` bleibt der erste Beginn.
2. **Offene Spanne:** Für ein Log, dessen letzte `run`-Spanne noch offen ist,
   zählen nur die abgeschlossenen Spannen; die offene trägt nichts bei, und
   es entsteht keine Ausnahme.
3. **Keine abgeschlossene Spanne:** Arbeit und Kosten bleiben leer, nicht `0`.
4. **Ein Gate-Lauf stimmt:** Für den Lauf `16f39431` meldet die Run-Liste
   $56.88 (heute $47.16); für `e4e70373` einen Wert größer null (heute
   `$0.00`). Der automatisierte Regressionstest fixiert die Korrektur über
   eine Mehrspannen-Fixture, da lokale Laufdaten der Retention unterliegen.
5. **Zeitgrößen getrennt:** Für ein Log, in dem eine Phasenspanne eine
   Unterbrechung überdauert (Phasenzeit deutlich größer als die Summe der
   Spannen-Dauern), zeigt die Oberfläche beide Zahlen verschieden an, und
   Phasenzeit trägt nirgends die Beschriftung „Arbeit"/„Work".
6. **Zerlegung stimmt:** Phasenzeit + Wartezeit = Gesamt bis auf Rundung.
   Beispiel: Phasen von Sekunde 0–100 und 300–500 ergeben 300 s Phasenzeit,
   200 s Wartezeit, 500 s Gesamt.
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
    heutigen Feldnamen und -typen; die neuen Zeitgrößen (`work_seconds`,
    `phase_seconds`, `wait_seconds`, `total_seconds`) sind zusätzlich da;
    `issue` bleibt der bisherige Wert; `tree` ist strukturell unverändert.
15. **Gates grün:** `uv run ruff check .` und `uv run pytest -x -q`. Keine
    neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN.

## Definition of Done

- Alle Akzeptanzkriterien 1–15 erfüllt und durch Tests belegt.
- Neue Tests unter `tests/test_gui_*.py` (Richtwert ~14; deutlich mehr als
  ~20 ist Scope-Drift); reines Client-Verhalten über
  `tests/gui_js_harness.js`/`tests/gui_js_harness.py`. Die bestehenden
  GUI-Tests bleiben grün.
- Bestehende Tests, die für Läufe mit Gates die alten, zu niedrigen
  Kennzahlen erwarten, werden auf die korrigierten Werte gehoben — mit
  Kommentar, der das Warum nennt. Das ist die einzige erlaubte inhaltliche
  Änderung an bestehenden Tests.
- `adw/gui/i18n.py` hat für alle neuen Schlüssel beide Sprachen mit
  identischen Schlüsselmengen und korrekten Pluralformen; das verbindliche
  Zeitvokabular wird überall konsistent verwendet.
- `docs/GUI-SPEC.md`, `docs/GUI-SPEC.de.md`, `CHANGELOG.md`, `CHANGELOG.de.md`
  sind synchron und paarweise sprachgleich ergänzt (A7); der A1-Eintrag steht
  unter `Fixed`/`Behoben`.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Der veraltete
  Hinweis auf flake8/isort in `docs/GUI-SPEC.md` begründet keine weiteren
  Gates; `ruff format` ist kein Gate.
- Die Deferred-Punkte bleiben ungebaut, auch im Review-Loop.
