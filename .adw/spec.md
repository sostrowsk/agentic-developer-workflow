# Spec — GUI-Redesign 5: Die Seite überträgt, was niemand sieht

Setzt auf dem gemergten Stand **0.25.0** auf (Briefe 1 bis 4). Gestaltung
(Brief 1), Zahlen (Brief 2), Anordnung (Brief 3) und Tastaturpfad (Brief 4)
werden **benutzt, nicht revidiert** (E7). Die im Issue genannten Messwerte
(2026-09-15, Lauf `16f39431`: 847 791 Byte HTML, 62 Detail-Panes mit zusammen
384 044 Byte = 45,3 % des Dokuments, ~481 Refreshes) sind Referenzwerte genau
dieses Laufs — keine allgemeingültigen Fixture-Größen — und vor dem Bauen gegen
den dann aktuellen Stand zu prüfen. Dieses Issue ändert den **Live-Pfad**,
nicht das, was die Seite zeigt.

## Goal

Die Nutzlast des laufenden Laufs verkleinern, ohne zu ändern, was ein Nutzer
sieht. Zwei verifizierte Verschwendungen werden beseitigt: (1) jeder entprellte
Refresh holt das vollständige Dokument samt `<head>`, obwohl der Client per
`X-Requested-With: fetch` bereits ankündigt, dass er nur die getauschten
Regionen braucht — der Server wertet den Header heute nirgends aus; (2) die
Seite trägt bis zu 62 server-gerenderte Detail-Panes (45,3 % des Dokuments)
aus, von denen ohne `?focus` keiner sichtbar ist. Der Server liefert künftig
auf den vorhandenen Header hin nur die Regionen und nur den Pane des
ausgewählten Knotens; die übrigen Panes werden bei Auswahl nach dem erprobten
`loadToolBody`-Muster nachgeladen. Zusätzlich wird die Spec (§7.3, §7.2 A) an
das gebracht, was der Code tut. Ein Nutzer merkt den Unterschied nur daran,
dass die Ansicht während eines laufenden Laufs ruhiger und schneller ist (E6).

## Scope

### A1 — Teilantwort auf den vorhandenen Header

`GET /runs/{repo}/{run_id}` wertet den Header `X-Requested-With: fetch` aus,
den der Client bereits schickt, und liefert dann ausschließlich die Regionen
aus `REGIONS = ["header.run-header", "main.detail"]`, in Dokumentreihenfolge,
als HTML-Fragment — ohne `<html>`, `<head>`, `<body>` oder sonstigen
Dokumentrahmen. Vollantwort und Fragment verwenden denselben Renderpfad und
dieselbe Markup-Erzeugung; die Teilantwort ist deren engerer Ausschnitt.
Ausgelöst **allein** durch den Header: kein neuer Query-Parameter, keine neue
Route, kein Content-Negotiation-Verfahren (E3). Der Client parst und tauscht
das Fragment wie bisher.

Das heute vom `<body>` gelesene Attribut `data-latest-context` — nicht Teil
der getauschten Regionen — bleibt nach der Teilantwort erreichbar: entweder an
einem Element der Teilantwort oder auf demselben Weg wie heute. Ohne das
friert das Kontext-Panel ohne Auswahl auf dem Wert des Seitenaufrufs ein (der
Code weist heute ausdrücklich darauf hin).

Ohne den Header ist die Antwort unverändert ein vollständiges Dokument; die
unter A2 bewusst geänderte Pane-Auslieferung gilt auch für dieses Dokument.

### A2 — Detail-Panes bei Bedarf

Die ausgelieferte Voll- oder Teilantwort enthält höchstens den Pane-Körper des
**ausgewählten** Knotens (ohne `?focus`: keinen). Das betrifft die
Span-Knoten-Panes aus `_pane_nodes`; leere Pane-Hüllen und der vorhandene
gemeinsame Pane für Punktknoten zählen nicht als ausgelieferte
Span-Pane-Körper.

Ein noch nicht geladener Pane wird bei Auswahl über die **vorhandene** Seite
mit dem bestehenden `?focus=<seq>` und dem Header aus A1 angefordert — keine
neue Route, kein neuer Query-Parameter (E3). Bestehende relevante
URL-Parameter, insbesondere `?tools_offset`, bleiben wirksam. Vorlage sind die
Absicherungen von `loadToolBody` (sie werden übernommen, nicht neu erfunden):

- Eine bereits laufende Anfrage für denselben Knoten wird wiederverwendet,
  nicht verdoppelt (`_loadPromise`).
- Eine Antwort, deren Knoten nicht mehr der ausgewählte ist, schreibt nichts
  und hinterlässt keinen halben Zustand (`stillOurs`). Das gilt auch für
  Antworten, deren Ziel-Pane inzwischen durch einen Regionentausch ersetzt
  wurde.
- Während des Ladens zeigt der Pane einen Ladezustand, keinen leeren Kasten.
- Ein bereits geladener Pane wird nicht erneut geholt, solange die Seite nicht
  ausgetauscht wurde. Ein fehlgeschlagener Abruf gilt nicht als geladen.
- Schlägt die Anfrage fehl, bleibt die letzte gute Ansicht stehen und der Pane
  sagt, dass er nicht geladen werden konnte — wie `refresh()` es heute mit
  `/* transient read error: keep the last good view */` hält.

Der vorhandene Einzelereignis-Abruf von `loadToolBody`
(`GET …/events?from_seq=X&to_seq=X`) bleibt erhalten; A2 ergänzt das Nachladen
der bisher vollständig ausgelieferten Span-Panes und ersetzt diesen Weg nicht.

### A3 — Die Spec sagt, was der Code tut

§7.3 in `docs/GUI-SPEC.md` beschreibt den tatsächlichen Mechanismus: SSE als
Auslöser des um 200 ms entprellten Neuabrufs, Regionentausch (nicht
inkrementelles Patchen), die Teilantwort aus A1 und das Nachladen aus A2 sowie
den unveränderten Reconnect über `Last-Event-ID` und das unveränderte
Verhalten beim Lauf-Ende. Die Aussage zum inkrementellen Patchen entfällt.
Die Zusage „Live-updating" der Run-Liste in §7.2 A wird als **noch nicht
umgesetzt** gekennzeichnet und in die Deferred-Liste der Spec aufgenommen; sie
wird in diesem Issue **nicht** gebaut (E5).

### A4 — i18n

Jeder neue sichtbare Text (etwa Ladezustand und Fehlerhinweis eines Panes)
liegt in `adw/gui/i18n.py` in beiden Sprachen mit identischer Schlüsselmenge
vor. Passende bestehende Texte dürfen wiederverwendet werden.

### A5 — Doku und Changelog

`docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron nach A3;
`CHANGELOG.md` und `CHANGELOG.de.md` synchron ergänzt, einschließlich der
bewusst geänderten HTML-Antwort und der unveränderten API.

### Was gleich bleiben muss (bindend)

Auswahl per Klick und per Tastatur, `?focus`-Deep-Links aus der Timeline, das
Tools-Fenster (`?tools_offset`) samt seiner 200er-Schranke, der Klappzustand
über einen Regionentausch hinweg (`captureOpenState` / `reapplyOpenState`),
die Wiederanwendung der Auswahl (`applySelection`), die Standard-Faltung
(`initTreeFold`) und die Entprellung von 200 ms. Was die Seite **zeigt**,
ändert sich nicht: Gestaltung, Zeitachse, Kennzahlen, Blockreihenfolge,
Trace-Baum mit Verdichtung, Tools-Fenster, Registerkarten, Run-Kontext-Panel,
Tastaturpfad und `?focus`-Deep-Links bleiben in Aussehen und Verhalten
unverändert. Auch Reconnect-Logik über `Last-Event-ID` und das Schließen des
Stroms nach dem Lauf-Ende bleiben unverändert.

### Contract

- **Bewusste Verhaltensänderung an einer HTML-Route:**
  `GET /runs/{repo}/{run_id}` antwortet **mit** dem Header
  `X-Requested-With: fetch` ab jetzt mit einem Fragment (genau die Regionen
  aus `REGIONS`, in dieser Reihenfolge) statt mit einem vollen Dokument.
  Allein der Header bestimmt Vollantwort oder Fragment. Mit bestehendem
  `?focus=<seq>` und dem Header enthält die Teilantwort den durch `focus`
  adressierten Pane gemäß bestehender Fokusauflösung.
- **„Vollantwort unverändert" heißt:** unveränderter Dokumentmodus
  (vollständiges Dokument mit `<html>`, `<head>`, `<body>`) und bisheriges
  Produktverhalten. Eine byteidentische Vollantwort zu 0.25.0 wäre mit A2 und
  dem Größenkriterium unvereinbar — die Pane-Reduktion aus A2 gilt
  ausdrücklich auch für die Vollantwort.
- **Unverändert:** alle Routen unter `/api` in Feldern, Typen und Werten —
  `/api/runs`, `/api/runs/{repo}/{run_id}`, `/api/runs/{repo}/{run_id}/events`,
  `…/diff`, `…/artifacts/{name}`, `…/stream`. (`artifacts/{name}` ist die
  vorhandene Artifact-Route; der verkürzte Issue-Verweis auf „artifact"
  begründet keine zusätzliche Route.) Keine neue Route, kein neuer
  Query-Parameter.
- Regressionstests fixieren beides: die Vollantwort ohne Header und die
  `/api`-Antworten.

## Non-Goals / Scope-Deckel

- Kein neues Tab, keine neue Ansicht, keine Änderung an der Run-Liste, keine
  Persistenz, kein neues Zustands-Subsystem, keine Änderung an den Routen
  unter `/api`. Laufende Anfragen und geladene Panes benötigen nur flüchtigen
  Zustand für die aktuell angezeigte Seite.
- Keine Kompression, kein Caching-Header-Tuning, kein ETag-Verfahren, keine
  bedingten Anfragen — die Seite soll weniger enthalten, nicht dasselbe
  kleiner verpackt.
- **E1** Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
  Template-Engine im Client. Vanilla JS im Client, Jinja2 auf dem Server.
- **E2** Kein inkrementelles Patchen des Baums; der Regionentausch bleibt der
  Mechanismus. Seit der Verdichtung (0.17.0) kann ein einzelnes Ereignis
  Mitgliedschaft und Zähler ganzer Sammelknoten ändern; ein Patcher müsste die
  Verdichtungslogik im Client zweitimplementieren.
- **E3** Keine neue Route und kein neuer Query-Parameter: A1 hängt am
  vorhandenen Header, A2 am vorhandenen `?focus`.
- **E4** Entprellung bleibt bei 200 ms, Auslöser bleibt der Ereignisstrom.
  Kein Polling, kein längeres Intervall, keine Ratenbegrenzung als Ersatz für
  A1/A2 — die Nutzlast wird kleiner, nicht seltener.
- **E5** Die Run-Liste wird in diesem Issue **nicht** live gemacht; A3
  korrigiert nur die Zusage in der Spec.
- **E6** Was die Seite zeigt, ändert sich nicht: keine neue Information, keine
  geänderte Reihenfolge, kein geändertes Aussehen — ausgenommen die
  geforderten vorübergehenden Lade- und Fehlerhinweise.
- **E7** Gestaltung, Zahlen, Anordnung und Tastaturpfad (Briefe 1–4) werden
  benutzt, nicht revidiert.
- **E8** Keine Änderung an Ereignistypen, Instrumentierung, `build_tree`, der
  Verdichtung, dem Tools-Fenster, der Retention oder der Reconnect-Logik.

Die Vorentscheidungen E1–E8 sind entschieden — kein Finding, auch nicht im
Review-Loop.

## Acceptance Criteria (messbar)

1. **Teilantwort:** Eine Anfrage an `GET /runs/{repo}/{run_id}` **mit**
   `X-Requested-With: fetch` liefert ein Fragment ohne `<html>`, `<head>` und
   `<body>`; es enthält genau die Regionen `header.run-header` und
   `main.detail` in Dokumentreihenfolge. Bei gleichen Eingabedaten und
   Parametern entspricht der Regionsinhalt dem der Vollantwort.
2. **Vollantwort unverändert:** Dieselbe Anfrage **ohne** den Header liefert
   weiterhin ein vollständiges Dokument (unveränderter Dokumentmodus und
   bisheriges Produktverhalten; die Pane-Auslieferung folgt A2, siehe
   Contract).
3. **Kontext ohne Auswahl:** Enthält eine Teilantwort einen neueren
   Kontextwert, zeigt das Kontext-Panel ohne Auswahl nach dem Regionentausch
   diesen Wert und friert nicht auf dem Wert des ersten Seitenaufrufs ein.
4. **Panes bei Bedarf:** Die für `16f39431` ausgelieferte Voll- oder
   Teilantwort enthält höchstens **einen** Span-Pane-Körper (heute 62, davon
   beim Aufruf ohne `?focus` null sichtbar): mit Auswahl den des Zielknotens,
   ohne Auswahl keinen. Leere Pane-Hüllen und der gemeinsame
   Punktknoten-Pane zählen nicht als Span-Pane-Körper.
5. **Seitengröße:** Die für `16f39431` ohne `?focus` ausgelieferte
   vollständige Seite ist gegenüber dem Referenzwert von 847 791 Byte um
   mindestens **35 %** kleiner (also höchstens 551 064 Byte), gemessen als
   unkomprimierte Antwort in Byte. Eine abweichende aktuelle Ausgangsmessung
   wird kenntlich gemacht; sie ersetzt nicht stillschweigend Referenzwert
   oder Abnahmeziel.
6. **Auswahl lädt nach:** Die Auswahl eines Knotens ohne geladenen Pane holt
   dessen Inhalt über die vorhandene Seite mit `?focus=<seq>` und dem Header
   aus A1 und zeigt ihn; währenddessen steht ein Ladezustand, kein leerer
   Kasten.
7. **Wettlauf:** Wird während eines laufenden Ladevorgangs ein anderer Knoten
   gewählt, schreibt die verspätete Antwort nichts, und der zuletzt gewählte
   Knoten zeigt seinen eigenen Inhalt. Das gilt auch für Antworten, deren
   Ziel-Pane inzwischen durch einen Regionentausch ersetzt wurde.
8. **Keine Doppelanfrage:** Zweimaliges Auswählen desselben Knotens erzeugt
   nicht zwei Anfragen (auch nicht in der Folge A → B → A bei noch laufendem
   Abruf für A); ein bereits geladener Pane wird nicht erneut geholt, solange
   die Seite nicht ausgetauscht wurde.
9. **Fehlerfall:** Schlägt das Nachladen fehl, bleibt die übrige Ansicht
   stehen, und der Pane sagt, dass er nicht geladen werden konnte — kein
   leerer Kasten, kein dauerhafter Ladehinweis.
10. **Deep-Link:** Ein Aufruf mit `?focus=<seq>` zeigt den Ziel-Pane wie
    bisher; die bestehende Fokusauflösung bleibt erhalten.
11. **Tastatur:** Die Auswahl per Tastatur löst denselben Nachladeweg aus wie
    ein Klick. Bloße Tastaturnavigation ohne Auswahl löst keinen Pane-Abruf
    aus.
12. **Zustand über den Tausch:** Klappzustand, Auswahl, Standard-Faltung und
    das Tools-Fenster (`?tools_offset` samt 200er-Schranke) verhalten sich
    nach einem Regionentausch unverändert.
13. **Contract:** Die Antworten aller `/api`-Routen sind in Feldern, Typen und
    Werten unverändert; die Vollantwort ohne Header ist unverändert (im Sinn
    des Contract-Abschnitts). Keine neue Route, kein neuer Query-Parameter.
    Regressionstests fixieren beides.
14. **Gates grün:** `uv run ruff check .` und `uv run pytest -x -q`. Keine
    neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN.

## Definition of Done

- Alle Acceptance Criteria 1–14 erfüllt und, soweit automatisiert prüfbar,
  durch Tests belegt.
- Serverseitige Aussagen — Teilantwort, Vollantwort-Modus, Pane-Zahl,
  Seitengröße, `/api`-Contract (AC 1–5, 10, 13) — in den üblichen
  `tests/test_gui_*.py`; der Größentest weist Referenzlauf,
  Anfrageparameter und unkomprimierte Bytezahl aus. Das Client-Verhalten —
  Nachladen, Ladezustand, Wettlauf, Doppelanfrage, Fehlerfall,
  Tastaturauslösung, Zustand über den Tausch (AC 6–9, 11–12) — im
  vorhandenen Harness `tests/gui_js_harness.js` / `tests/gui_js_harness.py`
  gegen das ausgelieferte `app.js`. Der Harness ist ein reiner
  `node`-Prozess ohne Browser (Entwicklungswerkzeug, keine
  Laufzeit-Dependency); kein neues Browser-Test-Subsystem entsteht.
- Richtwert **~13 neue Tests** unter `tests/`; deutlich mehr als ~18 ist
  Scope-Drift. Bestehende GUI-Tests bleiben grün. Einzige erlaubte
  inhaltliche Änderung an bestehenden Tests: Tests, die heute voraussetzen,
  dass jeder Pane-Körper im ausgelieferten HTML steht, werden auf den
  Nachladeweg gehoben, mit einem Kommentar, der sagt warum.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Kein flake8,
  kein isort, kein black; `ruff format` ist kein Gate — der veraltete
  Hinweis in `docs/GUI-SPEC.md` (Abnahmepunkt 10) begründet keine
  zusätzlichen Gates.
- i18n vollständig in beiden Sprachen mit identischer Schlüsselmenge (A4);
  Doku (§7.3 und §7.2 A) und Changelog in beiden Sprachen synchron (A3, A5).
  Deferred-Funktionen werden nicht als umgesetzt dargestellt.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Live-Aktualisierung der Run-Liste (E5).
- Inkrementelles Patchen des Trace-Baums (E2).
- Virtualisiertes Rendern der Baum-Spalte, Lazy-Rendering nach Sichtbarkeit.
- Kompression, ETags, bedingte Anfragen, Caching-Strategie.
- Nachladen weiterer Flächen (Artefakte, Raw-Tab, Change-Scope).
- Vorausladen des wahrscheinlich nächsten Panes.
- Messpunkte oder Telemetrie über die Nutzlast im Betrieb.
