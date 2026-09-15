# Plan — GUI-Redesign 5: Die Seite überträgt, was niemand sieht

Single-Lane-Projekt (`.adw/config.yaml`): nur der Workstream **backend**. Er
umfasst hier auch Templates, Client-JS, i18n, den JS-Harness und die Doku — die
GUI-Assets sind Teil des Python-Pakets; es gibt keinen frontend-Lane.

Setzt auf dem gemergten Stand **0.25.0** auf (Briefe 1 bis 4). Gestaltung
(Brief 1), Zahlen (Brief 2), Anordnung (Brief 3) und Tastaturpfad (Brief 4)
werden **benutzt, nicht revidiert** (E7). Dieser Brief ändert den **Live-Pfad**:
was während eines laufenden Laufs über die Leitung geht, nicht das, was die
Seite zeigt (E6). Zwei verifizierte Verschwendungen werden beseitigt — die
Vollantwort auf einen entprellten Refresh (A1) und die 62 unsichtbaren
Detail-Panes (A2) — und die Spec wird an den Code angeglichen (A3).

Betroffene Dateien (aus der Spec, abschließend):
`adw/gui/app.py` (Header-Auswertung in `run_detail_page`, Teilantwort als
engerer Ausschnitt desselben Renderpfads, Steuerung welcher Pane-Körper
gerendert wird), `adw/gui/templates/run_detail.html` (die beiden Regionen
`header.run-header` und `main.detail` als gemeinsam genutzte Bausteine für Voll-
und Teilantwort; Trennung von Pane-Hülle und Pane-Körper; `data-latest-context`
in der Teilantwort erreichbar; ggf. ein kleines Fragment-Template bzw. eine
Regionen-Partial), `adw/gui/static/app.js` (`swapRegions` verträgt das Fragment
und liest `data-latest-context` aus der Region; neuer Pane-Nachladeweg nach dem
`loadToolBody`-Muster; Verdrahtung in `applySelection`), `adw/gui/i18n.py`
(Lade-/Fehlertexte des Panes), `tests/gui_js_harness.js` /
`tests/gui_js_harness.py` (Pane-Nachladen samt Wettlauf, Doppelanfrage,
Fehlerfall — Entwicklungswerkzeug, keine Laufzeit-Dependency), neue
`tests/test_gui_*.py`, `docs/GUI-SPEC.md`, `docs/GUI-SPEC.de.md`,
`CHANGELOG.md`, `CHANGELOG.de.md`.
Die Datenableitung (`_run_detail`, `_summary`, `build_tree`, Verdichtung, die
`/api`-Routen) bleibt **unberührt** (E8); `_pane_nodes` bestimmt weiterhin, wer
einen Span-Pane bekommt — geändert wird nur, welcher Körper mit ausgeliefert
wird.

## Contract-Fläche (`.adw/contract.yaml`)

Der Contract pinnt für diesen Brief drei Dinge:

1. Die **bewusste Verhaltensänderung** an der HTML-Route
   `GET /runs/{repo}/{run_id}`: **mit** dem Header `X-Requested-With: fetch`
   antwortet sie mit einem Fragment (genau die Regionen `header.run-header` und
   `main.detail`, in Dokumentreihenfolge, ohne `<html>`/`<head>`/`<body>`);
   **ohne** den Header unverändert mit einem vollständigen Dokument
   (unveränderter Dokumentmodus). Allein der Header entscheidet. Mit
   `?focus=<seq>` enthält die Antwort den durch die bestehende Fokusauflösung
   adressierten Pane-Körper; die Pane-Reduktion aus A2 gilt in beiden Modi.
2. Die **Unveränderlichkeit** aller Routen unter `/api` in Feldern, Typen und
   Werten (AC 13) — keine neue Route, kein neuer Query-Parameter.
3. Das extern beobachtbare **Bedien-Ergebnis** des Nachladens (Panes bei Bedarf,
   Ladezustand, Wettlauf-Sieger, keine Doppelanfrage, Fehlerhinweis,
   Kontext-Panel folgt der Teilantwort) — beschrieben als Ergebnis, nicht als
   Attribut- oder Funktionsname.

**Ausdrücklich NICHT Contract (frei änderbar):** Klassennamen, ARIA- und
`data-*`-Attributnamen, Markup-Wortlaut, interne Helfer und ihre Signaturen
(`_pane_nodes`, `loadToolBody`, `swapRegions`, `_loadPromise`, `stillOurs`,
`REGIONS`), die konkrete Wahl zwischen Fragment-Template und Regionen-Partial
sowie die flüchtigen Client-Zustandsstrukturen. Diese Namen stehen zur
Orientierung in Spec und Plan, nicht im Vertrag.

**Bindender Architektur-Fakt (A1, E3):** Voll- und Teilantwort verwenden
denselben Renderpfad und dieselbe Markup-Erzeugung; die Teilantwort ist deren
engerer Ausschnitt (dieselben Regionen), und bei gleichen Eingabedaten und
Parametern entspricht der Regionsinhalt dem der Vollantwort. Es entsteht
**keine** zweite Render-Wahrheit für die Regionen. Ausgelöst wird die
Teilantwort **allein** vom vorhandenen Header, das Nachladen **allein** vom
vorhandenen `?focus` — keine neue Route, kein neuer Query-Parameter, kein
Content-Negotiation-Verfahren.

## Workstream: backend

### B1 — Messbasis prüfen (vor dem Bauen)

Die Referenzwerte (2026-09-15, Lauf `16f39431`, Stand 0.25.0: **847 791 Byte**
HTML, **62** Span-Panes mit zusammen **384 044 Byte = 45,3 %**, ~481 Refreshes)
sind Referenzwerte **genau dieses Laufs**, keine allgemeingültigen
Fixture-Größen. Vor dem Bauen gegen den dann aktuellen Stand prüfen und
protokollieren: Revision, Anfrageparameter, die unkomprimierte Seitengröße ohne
`?focus`, Zahl und gemeinsamer Byteumfang der ausgelieferten
Span-Pane-Körper, die Auswertung des `X-Requested-With`-Headers (heute null
Fundstellen in `adw/gui/app.py`); Abweichungen zu den Referenzwerten
festhalten. Die ~481 Refreshes sind Kontext, kein neues Mess- oder
Telemetrievorhaben. Das Abnahmeziel bleibt höchstens **551 064 Byte** für die
vollständige Antwort ohne `?focus`; eine abweichende aktuelle Ausgangsmessung
wird kenntlich gemacht und ersetzt weder Referenzwert noch Abnahmeziel (AC 5).
Ein fehlender oder nicht reproduzierbarer Referenzlauf ist als fehlender
Abnahmenachweis auszuweisen; eine ähnlich benannte synthetische Fixture ersetzt
ihn nicht, und Messwerte werden **nicht** erfunden.

Für AC 13 vor der Änderung bestätigen, dass die bestehende API-Regression
(`tests/test_gui_display_only_contract.py` bzw. das dortige eingefrorene
Baseline-Fixture) gegen 0.25.0 grün ist; Erwartungswerte niemals aus dem gerade
geänderten Antwortpfad neu erzeugen.

### B2 — Tests zuerst (`tdd: true`)

Das `pytest`-Gate trägt `tdd: true` (`.adw/config.yaml`): neue/geänderte
Verhaltenstests erst RED bestätigen, dann implementieren; Invarianten-Tests
(Seitengröße, Pane-Zahl, API-Regression) dürfen sofort grün sein. Vorhandene
Helfer in `tests/gui_app_helpers.py`, bestehende API-Fixtures und der Harness
`tests/gui_js_harness.js` / `tests/gui_js_harness.py` (reiner `node`-Prozess
gegen das ausgelieferte `app.js`, kein Browser) werden verwendet. Keine Tests
für Deferred-Themen. Richtwert **~13 neue Tests**:

| Nr. | Nachweis | Ort / AC |
| --- | --- | --- |
| 1 | Fetch-Header liefert genau die beiden Regionen in Dokumentreihenfolge, ohne `<html>`/`<head>`/`<body>`; der Regionsinhalt entspricht bei gleichen Daten und Parametern der Vollantwort; der aktuelle Kontextwert steht an einem Element **innerhalb** des Fragments | `tests/test_gui_*.py`; AC 1, 3 |
| 2 | Ohne Header vollständiger Dokumentmodus und bisheriges Produktverhalten unter A2 (Byteidentität zu 0.25.0 wird **nicht** verlangt — mit A2 unvereinbar) | `tests/test_gui_*.py`; AC 2, 13 |
| 3 | Beide Modi liefern ohne `?focus` **null** und mit Span-Fokus genau **einen** Span-Pane-Körper; leere Hüllen und der gemeinsame Punktknoten-Pane zählen nicht; die bestehende Fokusauflösung einschließlich der Umleitung eines foldbaren Tool-Ergebnisses auf seinen Call bleibt korrekt (mit und ohne Header) | `tests/test_gui_*.py`; AC 4, 10 |
| 4 | Referenzlauf `16f39431`: Vollantwort ohne `?focus` höchstens 551 064 unkomprimierte Byte; Lauf, Anfrageparameter und Bytezahl im Nachweis | `tests/test_gui_*.py`; AC 5 |
| 5 | Alle sechs `/api`-Routen unverändert in Feldern, Typen und Werten — auch nach vorausgehendem Voll- und Fragment-Abruf; nutzt die in B1 verifizierte Baseline, keine featurebedingten Unterschiede weg-normalisieren | `tests/test_gui_*.py`; AC 13 |
| 6 | Fragment mit neuerem Kontextwert aktualisiert das Kontext-Panel ohne Auswahl nach dem Regionentausch; kein Einfrieren auf dem Wert des Seitenaufrufs | Node-Harness; AC 3 |
| 7 | Auswahl eines ungeladenen Span-Panes fordert die vorhandene Seite mit `?focus=<seq>`, dem Header und erhaltenen Parametern (insb. `?tools_offset`) an; Ladezustand wird durch den Zielinhalt ersetzt, kein leerer Kasten; die Registerkarten des eingesetzten Körpers tragen Tab-Rollen, Panel-Zuordnungen und Tastaturbedienung wie ein servergerendert ausgelieferter Pane | Node-Harness; AC 6, 12 |
| 8 | Späte Antwort nach Auswahl eines anderen Knotens schreibt nichts; der zuletzt gewählte Knoten zeigt seinen eigenen Inhalt | Node-Harness; AC 7 |
| 9 | Späte Antwort auf einen inzwischen durch Regionentausch ersetzten Ziel-Pane schreibt weder Inhalt noch Fehlerhinweis | Node-Harness; AC 7 |
| 10 | Zweimalige Auswahl und A → B → A teilen den laufenden Abruf; ein geladener Pane wird bis zum Regionentausch nicht erneut geholt | Node-Harness; AC 8 |
| 11 | Fehlgeschlagener Abruf erhält die übrige Ansicht, zeigt den Fehlerhinweis statt eines dauerhaften Ladehinweises und gilt nicht als geladen (Retry möglich) | Node-Harness; AC 9 |
| 12 | Tastaturauswahl nutzt denselben Nachladeweg wie ein Klick; bloße Navigation ohne Auswahl fordert keinen Pane an | Node-Harness; AC 11 |
| 13 | Nach einem Regionentausch verhalten sich Klappzustand, Auswahl, Standard-Faltung und Tools-Fenster (samt 200er-Schranke) unverändert; der ausgewählte Pane wird gegen die frischen Hüllen erneut nachgeladen. Szenario ausdrücklich: Abschnitt (Tool/Raw) **innerhalb** eines nachgeladenen Panes öffnen, Refresh auslösen, Pane-Abruf abschließen lassen — der Abschnitt ist danach weiterhin offen (Klappzustand überlebt den asynchronen Nachladevorgang) | Node-Harness (+ Serverassertions); AC 12 |

Für API-Vergleiche unveränderte Felder, Typen, Werte und Reihenfolgen prüfen;
bei Artefakten die vollständigen Bytes, beim Stream die bestehenden Ereignisse
und Reconnect-/Endbedingungen; zeitabhängige Antworten unter gleichen
Auswertungsbedingungen vergleichen. Vorhandene Route-Tests weiterverwenden,
keine neue umfassende API-Testmatrix aufbauen. Nur die für die Szenarien
erforderlichen DOM-/Fetch-Simulationen im Harness ergänzen; kein
Browser-Test-Subsystem.

### B3 — A1 serverseitig: Teilantwort auf den vorhandenen Header

In `run_detail_page` (`adw/gui/app.py`) den Header `X-Requested-With: fetch`
auswerten (Groß-/Kleinschreibung wie beim Client). Ist er gesetzt, liefert die
Route ein HTML-Fragment aus genau den Regionen `header.run-header` und
`main.detail`, in Dokumentreihenfolge, **ohne** `<html>`, `<head>`, `<body>`
oder sonstigen Dokumentrahmen; ist er nicht gesetzt, unverändert das
vollständige Dokument. Ausgelöst **allein** durch den Header — kein neuer
Query-Parameter, keine neue Route, keine Content-Negotiation (E3). Bestehende
Sprachwahl, Query-Auswertung sowie Status- und Fehlerantworten (`require_run`,
404) bleiben unverändert.

Voll- und Teilantwort **müssen denselben Renderpfad und dieselbe
Markup-Erzeugung teilen** (A1): die beiden Regionen werden aus **einem**
gemeinsamen Template-Baustein erzeugt (empfohlen: die Region-Markup je in eine
Jinja-Partial/Makro heben, die sowohl `run_detail.html` als auch ein schlankes
Fragment-Template aufruft). Ein serverseitiges Nach-Parsen des gerenderten
Voll-HTML (neue Parser-Abhängigkeit) ist ausgeschlossen (E1). Die konkrete
Mechanik ist Gestaltungsspielraum und **nicht** vom Contract gepinnt; bindend
ist, dass bei gleichen Eingabedaten und Parametern der Regionsinhalt der
Teilantwort dem der Vollantwort entspricht und keine zweite Regionsvorlage mit
abweichender Darstellung entsteht.

Das `data-latest-context`-Attribut, das der Client heute vom `<body>` liest und
das **nicht** Teil der getauschten Regionen ist, muss in der Teilantwort
erreichbar bleiben — an einem Element **innerhalb** einer Region (empfohlen:
`main.detail`). Auf der Vollseite bleibt es zusätzlich am `<body>` (der
Erstaufruf und bisherige Verbraucher lesen es dort). Ohne das friert das
Kontext-Panel ohne Auswahl auf dem Wert des Seitenaufrufs ein (AC 3).

### B4 — A2 serverseitig: Detail-Panes bei Bedarf

Die ausgelieferte Voll- oder Teilantwort enthält höchstens den **Körper** des
Panes des durch `?focus` aufgelösten Knotens; ohne `?focus` keinen. Betroffen
sind die Span-Knoten-Panes aus `_pane_nodes` (`_pane_nodes` selbst und die
Menge der Panes bleiben unverändert — jeder Span-Knoten bekommt weiterhin seine
**Hülle**). Geändert wird ausschließlich, dass der **Körper** (Tabs,
`node_body`, Tools-Fenster, Prompt-Diff, Findings-Tabelle usw.) nur für den
fokussierten Knoten gerendert wird; alle übrigen Panes bleiben als **leere
Hülle** stehen, maschinenlesbar als „noch nicht geladen" markiert und für die
Auswahl weiterhin von Punktknoten unterscheidbar. **Keine** Körper durch bloßes
CSS-Verstecken in der Antwort behalten — sie dürfen gar nicht erst ausgeliefert
werden. Leere Hüllen und der gemeinsame Punktknoten-Pane zählen nicht als
Span-Pane-Körper.

Die bestehende Fokusauflösung (`_focus_index`, die Umleitung eines foldbaren
Tool-Ergebnisses auf seinen Call) bleibt unverändert und bestimmt, welcher Pane
seinen Körper erhält (AC 10). `?tools_offset` samt 200er-Schranke und alle
übrigen bestehenden URL-Parameter bleiben wirksam (der ausgelieferte
Pane-Körper trägt sein bestehendes Tools-Fenster). Der Renderpfad des
Pane-Körpers ist unverändert — es entsteht kein neues Markup für den Körper
selbst, nur die leeren Hüllen und ihr Marker kommen hinzu. Die anfängliche
Auto-Auswahl des ersten Knotens (heute `applySelection()` beim Init) bleibt
erhalten und läuft über den Nachladeweg, ohne dadurch das Kontext-Panel ohne
explizite Auswahl an den ersten Knoten zu binden. Weder Baumaufbau und
Verdichtung noch JSON-Detaildaten ändern (E8).

### B5 — A1/A2 clientseitig: Fragment-Tausch und Pane-Nachladen

In `adw/gui/static/app.js`:

- **`swapRegions` verträgt das Fragment (A1).** Der `DOMParser` parst das
  Fragment wie bisher; die Regionen werden wie bisher getauscht
  (`REGIONS`-Reihenfolge). Da das Fragment kein `<body>` mit
  `data-latest-context` trägt, wird der Wert aus dem Element der Teilantwort
  gelesen (empfohlen: `main.detail`) und auf das Live-`<body>` übernommen —
  der bisherige Body-Lesepfad kann als Rückfall bestehen bleiben; sonst friert
  das unausgewählte Kontext-Panel ein (AC 3). Der bestehende
  Klappzustand-Erhalt, `applySelection`, `updateContextPanel`, `initTreeFold`,
  `initTabs` und die Tastatur-Init nach dem Tausch bleiben unverändert (AC 12).
  **Achtung (Review-Finding):** `captureOpenState`/`reapplyOpenState` laufen
  heute um den Tauschzeitpunkt herum — mit nachgeladenen Panes existieren die
  `<details>`-Elemente eines noch nicht geladenen Pane-Körpers zu diesem
  Zeitpunkt nicht. Der festgehaltene Klappzustand wird deshalb über den
  asynchronen Nachladevorgang hinweg aufbewahrt und beim Einsetzen des
  Pane-Körpers auf diesen angewandt (siehe Nachladeweg unten); er darf beim
  Tausch nicht verworfen werden, nur weil sein Ziel noch fehlt (AC 12).
  Auslöser und 200-ms-Entprellung von `refresh()` bleiben unverändert (E4);
  `refresh()` schickt den Header bereits heute.
- **Pane-Körper bei Bedarf nachladen (A2).** Ein neuer, nach dem
  `loadToolBody`-Muster gebauter Weg lädt den Körper eines noch nicht geladenen
  Span-Panes: er fordert die **vorhandene** Seite mit dem bestehenden
  `?focus=<seq>` und dem Header aus A1 an (bestehende URL-Parameter,
  insbesondere `?tools_offset`, bleiben erhalten; nur `focus` wird auf den
  Zielknoten gesetzt), parst die Teilantwort, entnimmt den Körper des
  Ziel-Panes und setzt ihn in die vorhandene Hülle ein. **Kein** vollständiger
  Regionentausch für ein Pane-Nachladen. Übernommen werden die erprobten
  Absicherungen (nicht neu erfunden):
  - Eine bereits laufende Anfrage für denselben Knoten und dieselbe
    Pane-Instanz wird wiederverwendet (`_loadPromise`), nicht verdoppelt —
    auch bei A → B → A (AC 8).
  - Vor jeder sichtbaren Änderung prüfen, ob derselbe Knoten noch ausgewählt
    und der Ziel-Pane noch im aktuellen Dokument ist
    (`stillOurs`/Knoten-Identität). Überholte Antworten schreiben weder Inhalt
    noch Fehlerhinweis und hinterlassen keinen halben Zustand — auch wenn die
    Ziel-Hülle inzwischen durch einen Regionentausch ersetzt wurde (AC 7).
  - Während des Ladens zeigt der Pane einen Ladezustand (i18n), keinen leeren
    Kasten (AC 6).
  - **Nach erfolgreichem Einsetzen wird der eingesetzte Körper initialisiert
    (Review-Finding):** die Tab-Initialisierung (`initTabs`: Tab-Rollen,
    Panel-Zuordnungen, Roving-Tabindex) läuft heute nur beim Seiten-Init und
    beim Regionentausch — also bevor nachgeladener Inhalt ankommt. Sie wird
    nach dem Einsetzen auf den frischen Pane-Körper (erneut) angewandt, damit
    dessen Registerkarten Barrierefreiheit und Tastaturbedienung behalten.
    Ebenso wird der über den Nachladevorgang aufbewahrte Klappzustand dieses
    Panes jetzt auf die eingesetzten `<details>`-Elemente angewandt (AC 12).
  - Erst die erfolgreiche Übernahme gilt als geladen; ein geladener Pane wird
    bis zum Austausch seiner Region nicht erneut geholt, ein fehlgeschlagener
    Abruf gilt nicht als geladen (AC 8).
  - Netzwerkfehler, nicht erfolgreiche HTTP-Antworten oder fehlender
    Zielinhalt erhalten die letzte gute übrige Ansicht; der noch aktuelle
    Ziel-Pane sagt, dass er nicht geladen werden konnte (i18n), und bleibt
    erneut ladbar — wie `refresh()` es heute mit `/* transient read error:
    keep the last good view */` hält (AC 9).
- **Verdrahtung in `applySelection`.** Klick, Timeline-Auswahl und
  Tastaturauswahl laufen weiter durch den gemeinsamen Auswahlpfad
  (`selectNode`; keine synthetischen Klicks); wählt ein Knoten seinen eigenen
  Span-Pane, wird dessen Körper bei Bedarf über den neuen Weg nachgeladen. Die
  anfängliche Auto-Auswahl des ersten Knotens zeigt dessen Pane weiterhin —
  jetzt über das Nachladen; das sichtbare Verhalten bleibt gleich (E6). Bloße
  Tastaturnavigation ohne Auswahl löst **keinen** Abruf aus (AC 11). Nach
  einem Regionentausch sind die Hüllen frisch und leer, sodass der ausgewählte
  Pane erneut nachgeladen wird (AC 12) — das ist genau das gewünschte
  Live-Verhalten des ausgewählten Panes.
- **Punktknoten-Pane und Einzelereignis-Abruf unverändert.** Der gemeinsame
  Punktknoten-Pane und der bestehende `loadToolBody`-Abruf über
  `GET …/events?from_seq=X&to_seq=X` bleiben erhalten; A2 **ergänzt** das
  Nachladen der Span-Panes und ersetzt diesen Weg nicht. SSE-Auslöser,
  Reconnect über `Last-Event-ID` und der abschließende Refresh samt
  Stream-Schließen beim Lauf-Ende bleiben unverändert (E8).

### B6 — A3: Die Spec sagt, was der Code tut

`docs/GUI-SPEC.md` §7.3 auf den tatsächlichen Mechanismus bringen: SSE als
Auslöser des um 200 ms entprellten Neuabrufs, **Regionentausch** (nicht
inkrementelles Patchen — die Aussage zum inkrementellen Patchen entfällt), die
Teilantwort aus A1 auf den `X-Requested-With: fetch`-Header, das Nachladen der
Detail-Panes aus A2 sowie den **unveränderten** Reconnect über `Last-Event-ID`
und das **unveränderte** Verhalten beim Lauf-Ende. Die Zusage „Live-updating"
der Run-Liste in §7.2 A wird als **noch nicht umgesetzt** gekennzeichnet und in
die Deferred-Liste der Spec/Doku aufgenommen; sie wird in diesem Issue
**nicht** gebaut (E5). Deferred-Funktionen werden nicht als umgesetzt
dargestellt.

### B7 — A4: i18n

Jeder neue sichtbare Text (Ladezustand und Fehlerhinweis eines Span-Panes)
liegt in `adw/gui/i18n.py` in **beiden** Sprachen mit identischer
Schlüsselmenge vor. Passende bestehende Texte (`hint_loading`,
`hint_load_failed_*`) dürfen wiederverwendet werden — aber nur, wenn ihr
Wortlaut zum Pane passt: die bestehenden Fehlertexte sagen „erneut
aufklappen"/„erneut öffnen", was für einen Pane, der per Auswahl erneut geladen
wird, nicht zutrifft; in dem Fall eigener Schlüssel statt unpassender
Wiederverwendung. Der bestehende Sprach-Paritätstest
(`tests/test_gui_language.py`) bleibt grün. Keine weiteren sichtbaren
Informationen oder gestalterischen Änderungen (E6).

### B8 — A5: Doku, Changelog, Nachmessung, Gates (DoD)

`docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron nach B6. `CHANGELOG.md`
und `CHANGELOG.de.md` synchron ergänzen — einschließlich der **bewusst
geänderten** HTML-Antwort (Teilantwort auf den Header, Panes bei Bedarf) und
der **unveränderten** API. Die Referenzmessung aus B1 nach der Änderung
wiederholen und die Größenabnahme belegen (AC 5). Der veraltete
„flake8 + isort"-Hinweis (Abnahmepunkt 10 in `docs/GUI-SPEC.md`) begründet
**keine** zusätzlichen Gates und wird nicht befolgt; `ruff format` ist kein
Gate. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
Laufzeit-Dependency, kein Frontend-Paket, kein CDN (E1).

## Testumfang

Richtwert **~13 neue Tests** unter `tests/` (Tabelle in B2); deutlich mehr als
**~18** ist Scope-Drift. Parametrisierte Fälle zählen zum Budget;
zusammengehörige Fälle in Szenarien bündeln. Serverseitig die Teilantwort, die
Pane-Zahl, die Seitengröße, den Deep-Link und die API-Regression (AC 1–5, 10,
13) in `tests/test_gui_*.py`; der Größentest weist Referenzlauf,
Anfrageparameter und unkomprimierte Bytezahl aus. Das Client-Verhalten —
Nachladen, Ladezustand, Wettlauf, Doppelanfrage, Fehlerfall, Tastaturauslösung,
Zustand über den Tausch (AC 6–9, 11–12) — im vorhandenen Harness
`tests/gui_js_harness.js` / `tests/gui_js_harness.py` gegen das ausgelieferte
`app.js`. Der Harness bleibt ein reiner `node`-Prozess ohne Browser; kein neues
Browser-Test-Subsystem entsteht. Fixtures reproduzierbar über die bestehenden
`tests/gui_app_helpers.py`-Konventionen, nicht aus lokalen Run-Verzeichnissen
(Retention).

**Einzige erlaubte inhaltliche Änderung an bestehenden Tests:** Tests, die
heute voraussetzen, dass jeder Span-Pane-Körper im ausgelieferten HTML steht,
werden auf den Nachladeweg bzw. die fokussierte Antwort gehoben — mit einem
Kommentar, der sagt warum. Alle übrigen bestehenden GUI-Tests bleiben grün,
ohne inhaltlich umgeschrieben zu werden.

## Grenzen

Keine neue Route, kein neuer Query-Parameter, kein neues Tab, keine neue
Ansicht, keine Änderung an der Run-Liste, keine Persistenz, kein neues
Zustands-Subsystem, keine Änderung an den Routen unter `/api`. Kein
inkrementelles Patchen des Baums — der Regionentausch bleibt der Mechanismus
(E2). Entprellung bleibt 200 ms, Auslöser bleibt der Ereignisstrom (E4); kein
Polling, keine Ratenbegrenzung als Ersatz für A1/A2. Keine Kompression, kein
ETag/Caching-Header-Tuning, keine bedingten Anfragen — die Seite soll weniger
enthalten, nicht dasselbe kleiner verpackt. Keine Änderung an Ereignistypen,
Instrumentierung, `build_tree`, der Verdichtung, dem Tools-Fenster, der
Retention oder der Reconnect-Logik über `Last-Event-ID` (E8). Was die Seite
**zeigt**, ändert sich nicht — ausgenommen die geforderten vorübergehenden
Lade- und Fehlerhinweise (E6). Gestaltung, Zahlen, Anordnung und Tastaturpfad
(Briefe 1–4) werden benutzt, nicht revidiert (E7). Auswahl per Klick und per
Tastatur, `?focus`-Deep-Links, das Tools-Fenster samt 200er-Schranke,
`captureOpenState` / `reapplyOpenState`, `applySelection`, `initTreeFold`
bleiben in Verhalten und Aussehen unverändert. Die Vorentscheidungen E1–E8
sind entschieden — kein Finding, auch nicht im Review-Loop.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Live-Aktualisierung der Run-Liste (E5).
- Inkrementelles Patchen des Trace-Baums (E2).
- Virtualisiertes Rendern der Baum-Spalte, Lazy-Rendering nach Sichtbarkeit.
- Kompression, ETags, bedingte Anfragen, Caching-Strategie.
- Nachladen weiterer Flächen (Artefakte, Raw-Tab, Change-Scope).
- Vorausladen des wahrscheinlich nächsten Panes.
- Messpunkte oder Telemetrie über die Nutzlast im Betrieb.
