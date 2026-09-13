# Plan — GUI-Redesign 2/2: Ein Lauf hat eine Wahrheit — und die Liste erzählt sie

Single-Lane-Projekt (`.adw/config.yaml`): nur der Workstream **backend**. Er
umfasst hier auch Templates, JS/CSS, i18n und Doku — die GUI-Assets sind Teil
des Python-Pakets; es gibt keinen frontend-Lane, `--parallel` ist bewusst
nicht verfügbar.

Betroffene Dateien (aus der Spec, abschließend): `adw/gui/app.py`,
`adw/gui/i18n.py`, `adw/gui/templates/run_list.html`,
`adw/gui/templates/run_detail.html`, `adw/gui/static/app.css` und ggf.
`adw/gui/static/app.js` (nur wo A2 Beschriftungen berührt), `docs/GUI-SPEC.md`,
`docs/GUI-SPEC.de.md`, `CHANGELOG.md`, `CHANGELOG.de.md` sowie neue Tests unter
`tests/test_gui_*.py`.

Contract-Fläche (`.adw/contract.yaml`): die BEWUSSTE Bedeutungsänderung von
`duration`/`cost`, die ADDITIVEN Zeitgrößen-Felder (`work_seconds`,
`phase_seconds`, `wait_seconds`, `total_seconds`) und die additive Token-Summe
an der Laufzusammenfassung `_summary` (`adw/gui/app.py:1014`, gespeist in Liste
UND Detail-Kopf), die Regression aller übrigen Felder (insb. `issue`, das NICHT
zum Anzeigetitel umgedeutet wird) und die vier neuen optionalen Query-Parameter
der HTML-Route `/` samt Listenverhalten. Helfer-Signaturen und Markup-/CSS-
Wortlaut sind NICHT Contract-Fläche.

Verifizierter Fakt zur Token-Summe: der `totals`-Payload der `run`-Spannen
führt eine **skalare** Token-Summe `tokens` (`adw/cli.py:299–303`,
`RunTotals`, `adw/agents.py:165`). Sie wird wie `cost` über alle
abgeschlossenen Spannen summiert und additiv als `tokens` ausgewiesen.

TDD ist Pflicht (`pytest`-Gate hat `tdd: true` in `.adw/config.yaml`): Tests
zuerst, RED bestätigen, dann implementieren. Richtwert **~14 neue Tests**,
deutlich mehr als ~20 ist Scope-Drift. Bestehende GUI-Tests bleiben grün; die
EINZIGE erlaubte inhaltliche Änderung an ihnen ist, Erwartungen für Läufe mit
Gates von den alten, zu niedrigen Kennzahlen auf die korrigierten Werte zu
heben — mit Kommentar, der das Warum nennt.

## Workstream: backend

### B0 — RED: Tests zuerst (`tests/test_gui_*.py`)

Neue Tests anlegen (an bestehende `test_gui_*.py`-Konventionen und Fixtures
halten; für reines Client-Verhalten steht `tests/gui_js_harness.js` /
`tests/gui_js_harness.py` bereit). Referenzläufe und Mehrspannen-Fälle werden
über **reproduzierbare Fixtures** nachgebildet, nicht über lokal vorhandene
Run-Verzeichnisse (Retention). Vor der Implementierung ausführen und RED
bestätigen. Keine Tests für Deferred-Themen (z. B. hypothetisch überlappende
Phasen).

1. **Summierung (AC 1).** Log mit drei abgeschlossenen `run`-Spannen
   (`totals.duration` 100/200/300, `totals.cost` 1/2/3): `_summary` meldet
   `duration` 600 und `cost` 6, nicht 300/3; vorhandene `tokens` werden
   summiert; `start` bleibt der erste Beginn.
2. **Offene Spanne (AC 2).** Letzte `run`-Spanne offen: nur die abgeschlossenen
   Spannen zählen, die offene trägt nichts bei, keine Ausnahme.
3. **Keine abgeschlossene Spanne (AC 3).** `duration`/`cost`/`work_seconds` und
   `tokens` bleiben `null`/leer, nie eine erfundene `0`. Echte Nullwerte aus
   dem Payload (z. B. Mock-Runner-Totals 0/0) bleiben dagegen `0`.
4. **Gate-Lauf stimmt (AC 4).** Mehrspannen-Fixture nach dem Muster von
   `16f39431` ($56.88 statt $47.16) und `e4e70373` (> 0 statt $0.00); die
   Run-Liste meldet den korrigierten Wert, Liste und Detail-Kopf stimmen
   überein.
5. **Zeitgrößen getrennt (AC 5).** Fixture, in der eine Phasenspanne eine
   Unterbrechung überdauert (Phasenzeit ≫ Summe der Spannen-Dauern):
   `work_seconds` und `phase_seconds` sind verschieden, und Phasenzeit trägt
   nirgends die Beschriftung „Arbeit"/„Work" (E5).
6. **Zerlegung stimmt (AC 6).** Phasen 0–100 s und 300–500 s ⇒ `phase_seconds`
   300, `wait_seconds` 200, `total_seconds` 500; `phase_seconds + wait_seconds
   = total_seconds` bis auf Rundung. Zusatzfall aktive offene Phase: `Gesamt`
   nutzt einen festen Testzeitpunkt als Berechnungszeitpunkt; das Phasenende
   in der API bleibt `null`, nichts wird nachgetragen.
7. **Titelableitung (AC 7).** `# Überschrift` → `Überschrift` ohne `#`;
   `# Issue (…)` wird übersprungen (`^issue\b`, case-insensitive); Überschrift
   erst ab Zeile 13 gewinnt nicht; ohne Überschrift die erste nicht-leere Zeile;
   führendes `ADW-Issue:`/`Issue:` entfernt; > 90 Zeichen → 89 + `…`; leerer/
   fehlender Text → leere Zelle. Reine Textverarbeitung (E7).
8. **Zeilenhöhe & title-Attribut (AC 8).** Die Issue-Zelle rendert einzeilig;
   der vollständige rohe Text steht escaped im `title`-Attribut — auch bei
   langem Text und HTML-Sonderzeichen, nie als Markdown/HTML interpretiert,
   und NICHT aus dem auf `_ISSUE_MAX` gekürzten API-Feld `issue` gespeist.
9. **Eine Statusspalte (AC 9).** Bei einem abgeschlossenen Lauf erscheint der
   Statuswert genau einmal je Zeile; `done`/`done` und `escalated`/`escalated`
   entfallen. Bei laufendem/wartendem Lauf ergänzt die Phase.
10. **Sortierung mit Gruppenvorrang (AC 10).** `?sort=cost&dir=desc` ordnet nach
    korrigierten Kosten absteigend, ein `awaiting_approval`-Lauf steht dennoch
    über allen anderen; ebenso je ein Fall für `start`, `duration`, `events`.
11. **Filter & Leerzustand (AC 11).** `?status=escalated` liefert nur eskalierte
    Läufe; `?repo=` filtert auf den Slug; kombiniert = Schnittmenge; ein
    unbekannter Wert liefert eine leere Liste mit lokalisiertem Hinweis, keinen
    Fehler und nicht die ungefilterte Liste.
12. **Robustheit (AC 12).** Unbekannte `sort`/`dir`-Werte fallen auf
    `start`/`desc` zurück, ohne Ausnahme und ohne leere Liste; fehlende
    Kennzahlen (`null`) sortieren ohne Ausnahme.
13. **Sprachumschaltung (AC 13).** `sort`/`dir`/`repo`/`status` überleben den
    Sprachwechsel über `switch_qs` (stehen weiter in der URL).
14. **Contract-Regression (AC 14).** `/api/runs` und `/api/runs/{repo}/{run_id}`
    behalten alle heutigen Feldnamen/-typen (`start`, `status`, `phase`,
    `issue`, `event_count`, `dry_run`, `has_trace`, `repo_exists`); `issue`
    bleibt der bisherige Wert (nicht der Anzeigetitel); die neuen Felder
    `work_seconds`/`phase_seconds`/`wait_seconds`/`total_seconds` und `tokens`
    sind zusätzlich da; `tree`, `raw`, `latest_context`, `problems` sind
    strukturell unverändert.
15. **i18n vollständig (AC 15-Beleg).** Alle neuen Schlüssel (Zeitgrößen-
    Vokabular, Spaltenköpfe, Sortier-/Filtersteuerung, leere Trefferliste)
    liegen in beiden Sprachen mit identischen Schlüsselmengen und korrekten
    Pluralformen vor.

RED bestätigen: die neuen Tests gezielt ausführen; Summierungs-, Zeitgrößen-,
Titel-, Sortier-/Filter- und Contract-Tests scheitern vor der Implementierung.

### B1 — Kennzahlen über den ganzen Lauf (A1, AC 1–4, AC 14)

In `_summary` (`adw/gui/app.py:1014`) `duration`, `cost` und `tokens` über
**alle abgeschlossenen `run`-Spannen** des Logs summieren statt aus der letzten
(`_run_span`, `adw/gui/app.py:938`) zu nehmen. `start` bleibt die erste Spanne;
die Status- und Phasenableitung (letzte Spanne, Approval-/Eskalationsregeln)
bleibt unverändert — die spanbezogene Auswahl des letzten Run-Endes für den
Status wird NICHT durch die Summierung ersetzt. Eine noch offene Spanne (ohne
`totals`) trägt nichts bei; ohne jede abgeschlossene Spanne bleiben die Zahlen
`null`/leer — nie eine erfundene `0` (E6); echte Nullwerte aus dem Payload
bleiben `0`. Nur die Auswertung ändert sich; kein Event-Typ, keine
Instrumentierung, kein Log wird geschrieben (E8). `_summary` speist Liste und
Detail-Kopf gleichermaßen — beide stimmen dadurch überein; Repo-Platzhalter
bleiben in ihrer bisherigen Form.

### B2 — Additive Zeitgrößen an der Laufzusammenfassung (A2, AC 5, AC 6, AC 14)

`_summary` um die vier Felder ergänzen, jeweils Zahl oder `null` nach den
normativen Definitionen:

- `work_seconds` = Arbeit = Summe `totals.duration` über alle `run`-Spannen mit
  `end` (numerisch identisch zu `duration`); `null` ohne abgeschlossene Spanne.
- `phase_seconds` = Phasenzeit = Summe der Phasen-Spannen mit parsebarem `start`
  und `end` (die Fläche der farbigen Achsensegmente aus Brief 1) — NICHT Arbeit.
- `total_seconds` = Gesamt = kleinster Phasenstart bis größtes Phasenende, bei
  aktiver Phase ohne Ende bis zum Auswertungszeitpunkt. Der
  Berechnungszeitpunkt wird innerhalb eines Seitenaufbaus konsistent verwendet
  und nie als nachgetragenes Phasenende ausgegeben.
- `wait_seconds` = Wartezeit = `total_seconds − phase_seconds`; `null`, wenn
  nicht bestimmbar. Es gilt `phase_seconds + wait_seconds = total_seconds` bis
  auf Rundung (Rundung erst bei der Darstellung).

Die Phasen-Spannen stammen aus derselben Ableitung wie das Phasenband aus
Brief 1; die überlappungsfreie Zerlegung wird zugrunde gelegt (Deferred: keine
neue Overlap-Härtung). Keine Änderung an den API-Routen selbst — die Felder
erscheinen automatisch in Liste und Detail-`run`.

### B3 — Drei benannte Zeitgrößen, ein Vokabular (A2, A6, AC 5)

Die Zeitachse aus Brief 1 und der Detail-Kopf werden auf das verbindliche
Vokabular gebracht (Arbeit/Work · Phasenzeit/Phase time · Wartezeit/Waiting ·
Gesamt/Total). Der i18n-Schlüssel `tl_work` (`adw/gui/i18n.py`) beschriftet
heute die Summe der Phasendauern — er wird **umbenannt und neu beschriftet**
(Phasenzeit), und die echte Arbeitszeit aus A1 kommt als **zusätzlicher**
Schlüssel und eigene Zahl daneben. `tl_waiting`/`tl_total` behalten Bedeutung
und Wert. Farbige Segmente = Phasenzeit, Lücken = Wartezeit.

**Zweiter Kennzahlen-Leser:** der Timeline-Kopf liest `totals.duration`/
`totals.cost` heute separat aus einem einzelnen End-Record
(`adw/gui/app.py:1999–2009`). Seine Laufkennzahlen werden auf die korrigierte
Zusammenfassung bezogen; Modellzahlen (`_tokens_per_model`), Spuren und Balken
bleiben unverändert.

Berührt wird am Run-Detail ausschließlich, was A1/A2 an Zahlen und deren
Beschriftung ändern — Blockreihenfolge, Registerkarten, Panes, Trace-Baum,
Verdichtung, Blätterung und Timeline-Registerkarte bleiben exakt wie Brief 1
sie hinterlässt (E3, E4). Betrifft `run_detail.html`, ggf. `app.js` nur an der
Beschriftung, und `app.css` nur, soweit vorhandene Tokens/Klassen (kein neuer
Farb-/Größenwert).

### B4 — Titelzeile statt Roh-Markdown (A3, AC 7, AC 8)

Titelableitung als reine Textverarbeitung auf dem rohen Issue-Text (Helfer in
`adw/gui/app.py`, Implementierungsdetail, keine Contract-Fläche), in dieser
Reihenfolge: (1) unter den ersten zwölf Zeilen die erste `#`-Überschrift, Text
ohne führende `#`/Leerzeichen, eine nur „Issue" benennende Überschrift
(`^issue\b`, case-insensitive) überspringen; (2) sonst die erste nicht-leere
Zeile; (3) führendes `ADW-Issue:`/`Issue:` entfernen; (4) > 90 Zeichen auf 89
kürzen und mit `…` markieren; (5) leerer/fehlender Text → leere Zelle, nie ein
Platzhalter. In `run_list.html` die Issue-Spalte einzeilig (abgeschnitten) mit
vorhandenen Gestaltungswerten rendern; der **vollständige rohe** Text steht
escaped im `title`-Attribut — dafür NICHT das auf `_ISSUE_MAX = 120` gekürzte
API-Feld `issue` als Quelle verwenden (sonst wäre der Tooltip bereits
abgeschnitten), und das API-Feld selbst bleibt unverändert. Nie als
Markdown/HTML interpretieren (E7).

### B5 — Phase und Status werden eine Spalte (A4, AC 9)

In `run_list.html` Phase und Status zu **einer** Spalte zusammenziehen: den
Status nennen und die Phase nur dann zusätzlich, wenn sie etwas hinzufügt
(laufender oder wartender Lauf). Bei einem abgeschlossenen Lauf genau ein Wort;
`done`/`done` und `escalated`/`escalated` entfallen. Tabellenkopf und
Platzhalter-`colspan` an die reduzierte Spaltenzahl anpassen. Keine Änderung an
der Status-/Phasenableitung selbst.

### B6 — Sortieren und Filtern, serverseitig (A5, A6, AC 10–13)

Die HTML-Route `/` nimmt vier **optionale** Query-Parameter entgegen,
ausgewertet wie der Raw-Tab (`?raw_q`/`?raw_type`) — kein Client-Zustand,
keine Persistenz:

- `?sort` ∈ {start, duration, cost, events}, `?dir` ∈ {asc, desc}. Dauer/Kosten
  nach den korrigierten Laufwerten (A1), `events` nach `event_count`. Unbekannt/
  fehlend → Rückfall auf `start`/`desc`, nie Fehler, nie leere Liste. Fehlende
  Kennzahlen (`null`) sortieren ohne Ausnahme und werden nicht als gemessene
  Nullwerte behandelt.
- `?repo` (Repo-Slug) und `?status` (Statuswert); kombiniert = Schnittmenge. Ein
  unbekannter Wert → leere Trefferliste mit erklärendem, lokalisiertem Hinweis,
  nicht die ungefilterte Liste, kein Fehler.
- Die bestehende Statusgruppierung (`_status_rank`, `adw/gui/app.py:2546–2548`:
  `awaiting_approval`, dann `running`, dann Rest; neueste zuerst innerhalb der
  Gruppe) wird **vor** der gewählten Sortierung angewendet und behält Vorrang;
  sie ist nicht abschaltbar und bleibt unangetastet. Ohne Parameter bleiben
  Auswahl und Reihenfolge unverändert.

Sichtbare GET-Steuerung (Spaltenköpfe/`select`-Formulare o. Ä.) im Template;
die Parameter überleben die Sprachumschaltung über den bestehenden
`switch_qs`-Mechanismus, weil sie in der URL stehen. Die **API-Routen**
(`/api/runs`, `/api/runs/{repo}/{run_id}`) erhalten KEINE Sortier-/
Filterparameter. Keine neue Route, kein neues Tab, keine Persistenz, kein
Polling, kein neues Zustands-Subsystem (Scope-Deckel).

### B7 — i18n (A6, AC 15)

`adw/gui/i18n.py`: über die bestehende Mechanik alle neuen Beschriftungen
ergänzen — das Zeitvokabular (inkl. umbenanntem `tl_work` und neuem
Arbeitszeit-Schlüssel), Spaltenköpfe, Sortier-/Filtersteuerung und den
Leer-Trefferlisten-Hinweis — in **beiden** Sprachen, identische Schlüsselmengen,
korrekte Pluralformen. Keine Änderung an der i18n-Mechanik.

### B8 — Doku & Changelog (A7)

`docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron und paarweise sprachgleich
ergänzen: die drei/vier Zeitgrößen samt Rechenregeln, die Summierung über alle
Spannen, die bewusste Bedeutungsänderung der API-Kennzahlen samt additiver
Felder (inkl. `tokens`), die Titelableitung und die Sortier-/Filterparameter
einschließlich Rückfall- und Leerzuständen. `CHANGELOG.md`/`CHANGELOG.de.md`
synchron ergänzen; der A1-Eintrag steht unter `Fixed`/`Behoben` mit dem
Hinweis, dass frühere Werte für Läufe mit Gates zu niedrig waren. Der veraltete
flake8/isort-Verweis in `docs/GUI-SPEC.md` begründet keine weiteren Gates.

### B9 — Gates grün und Abschluss (AC 15)

- `uv run ruff check .`
- `uv run pytest -x -q`

Alle neuen Tests und die unveränderten bestehenden GUI-Tests sind grün (außer
den ausdrücklich auf korrigierte Gate-Werte gehobenen Erwartungen, jeweils mit
Warum-Kommentar; keine alten Assertions zur Detailstruktur oder sonstigen
Bestandsfunktionen abschwächen). Keine neue Laufzeit-Dependency, kein
Frontend-Paket, kein CDN, keine Webfont (E1). Soweit lokal vorhanden, die Liste
für `16f39431` auf $56.88 und `e4e70373` auf Kosten > 0 kontrollieren —
automatisierte Regressionen hängen aber nicht von retentionsabhängigen
Laufdaten ab. Abschließend am Diff prüfen, dass der Umfang auf die oben
genannten Dateien beschränkt ist, alle AC 1–15 zugeordnet sind und die
Non-Goals/Scope-Deckel eingehalten wurden.

## Reihenfolge

B0 (RED) zuerst. B1 bildet die Grundlage für B2, B3 und B6; B4/B5 sind reine
Listendarstellung. B7 begleitet B3–B6, B8 beschreibt den fertigen Stand,
B9 schließt ab.

## Non-Goals / Scope-Deckel (aus der Spec, bindend)

- Keine neue Route, kein neues Tab, keine Persistenz, kein Polling, kein neues
  Zustands-Subsystem, keine Änderung an Auswahl-, Pane-, `?focus`- oder
  Deep-Link-Verhalten.
- **E1** keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
  Webfont; Vanilla JS, handgeschriebenes CSS, System-Schriften.
- **E2** Sortierung/Filterung ausschließlich serverseitig über Query-Parameter;
  kein clientseitiges Sortieren, keine Tabellen-Bibliothek, kein `localStorage`,
  keine Cookies, kein neuer Client-Zustand.
- **E3** Das Run-Detail wird nicht umgebaut; berührt wird dort ausschließlich,
  was A1/A2 an Zahlen und deren Beschriftung ändern — auch nicht als Finding.
- **E4** Gestaltungsentscheidungen aus Brief 1 (Tokens, Skalen, Signalfarbe,
  Dark Mode, Aufbau der Zeitachse) werden benutzt, nicht revidiert; keine neuen
  Farben, Schriftgrößen oder Abstandswerte.
- **E5** Phasenzeit wird nirgends als Arbeit beschriftet.
- **E6** Eine unbestimmte Zahl bleibt leer, nie `0`.
- **E7** Der Issue-Rohtext wird nicht als Markdown gerendert, weder in der Liste
  noch im `title`.
- **E8** Keine Änderung an Ereignistypen, Instrumentierung, Event-Payloads,
  `build_tree`, Verdichtungsschicht, Blätterung, SSE-Pfad oder Retention — die
  A1-Korrektur geschieht ausschließlich bei der Auswertung des vorhandenen Logs.
- **E9** Kein Responsive-Umbau, keine Breakpoints, keine Mobilansicht.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

Erkannt, begründet, aber nicht Gegenstand; wird im Codex-/Fix-Zyklus **nicht**
nachgebaut, auch nicht als Finding:

- Die Anordnung der Blöcke im Run-Detail (der Trace-Baum beginnt weit unterhalb
  des Seitenanfangs).
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
