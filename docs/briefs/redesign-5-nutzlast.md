# GUI-Redesign 5: Die Seite überträgt, was niemand sieht

Fünfter Brief der Redesign-Reihe. Die ersten vier haben die Oberfläche
gestaltet (1), ihre Zahlen richtiggestellt (2), sie geordnet (3) und bedienbar
gemacht (4). Dieser hier nimmt sich den **Live-Pfad** vor: was während eines
laufenden Laufs tatsächlich über die Leitung geht, und wie viel davon niemand
je zu sehen bekommt.

**Abhängigkeit:** Setzt auf dem gemergten Stand **0.25.0** auf (Briefe 1 bis 4).
Gestaltung, Zahlen, Anordnung und Tastaturpfad werden **benutzt, nicht
revidiert**. Die Ausgangslage ist am 2026-09-15 gegen genau diesen Stand
gemessen und vor dem Bauen gegen den dann aktuellen Stand zu prüfen.

## Ausgangslage (im Code verifiziert, 2026-09-15, Stand 0.25.0)

### Fast die Hälfte der Seite sind Panes, von denen höchstens einer sichtbar ist

Die Run-Detailseite von `16f39431` ist **847 791 Byte** groß. Darin stecken
**62 server-gerenderte Detail-Panes mit zusammen 384 044 Byte — 45,3 % des
Dokuments**. Sichtbar ist davon per CSS immer höchstens einer:
`.panes .pane { display: none }`, `.panes .pane.selected { display: block }`.
Beim Aufruf ohne `?focus` ist **keiner** ausgewählt — dann werden 384 KB
Pane-Inhalt übertragen, von denen null Byte angezeigt wird.

`_pane_nodes` (`adw/gui/app.py:179`) bestimmt, wer einen eigenen Pane bekommt:
die Span-Knoten. Deren Zahl wächst mit dem Lauf.

### Jedes SSE-Ereignis holt das ganze Dokument neu

Der Client abonniert den Ereignisstrom und ruft nach 200 ms Entprellung
`refresh()` (`adw/gui/static/app.js`). `refresh()` holt die **vollständige
Seite** erneut und übergibt sie an `swapRegions`, das
`REGIONS = ["header.run-header", "main.detail"]` austauscht — also praktisch
das ganze Dokument. Der Kommentar im Code nennt es selbst einen „wholesale
region swap".

Der Client schickt dabei `headers: { "X-Requested-With": "fetch" }` — er
kündigt also an, dass er nur Regionen braucht. **Der Server wertet diesen
Header nirgends aus** (null Fundstellen in `adw/gui/app.py`) und rendert jedes
Mal das vollständige Dokument samt `<head>`.

Wie oft das passiert, aus den Ereignis-Zeitstempeln aller Läufe gerechnet
(Entprellung 200 ms):

| Lauf | Ereignisse | Refreshes | Spitze / 10 s |
|---|---:|---:|---:|
| `7fe9d702` | 1296 | **661** | 21 |
| `d1c9de00` | 964 | 485 | 26 |
| `16f39431` | 905 | **481** | 21 |
| `f4942ef3` | 890 | 436 | 27 |
| `81795e53` | 628 | 319 | 28 |

Bei `16f39431` sind das rund 481 vollständige Abrufe einer Seite, die am Ende
848 KB misst — und 481-mal `DOMParser` über das ganze Dokument, gefolgt vom
Austausch eines mehrere tausend Elemente großen Teilbaums.

### Die Spec beschreibt etwas anderes, als der Code tut

- **§7.3** sagt: „The client patches the tree incrementally; the GUI never
  re-renders the whole page." Der Code tut genau das Gegenteil und sagt es im
  eigenen Kommentar auch.
- **§7.2 A** sagt über die Run-Liste: „Live-updating." `run_list.html` enthält
  **kein einziges `<script>`** und keine `EventSource`; eine SSE-Route auf
  Listenebene gibt es nicht. Die Liste aktualisiert sich nie von selbst.

### Es gibt bereits ein erprobtes Muster für Nachladen

`loadToolBody` (`adw/gui/static/app.js`) lädt den Inhalt eines
Werkzeug-Eintrags **bei Bedarf** über `GET …/events?from_seq=X&to_seq=X` —
ausdrücklich nur diesen einen Datensatz, „not the whole tail". Die schwierigen
Fälle sind dort bereits gelöst und im Code kommentiert: eine laufende Anfrage
wird wiederverwendet statt verdoppelt (`_loadPromise`), und eine verspätete
Antwort für einen nicht mehr ausgewählten Knoten schreibt nichts
(`stillOurs`, Befunde P1/P2 früherer Läufe). Dieses Muster ist die Vorlage für
A2 — es wird übernommen, nicht neu erfunden.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

Was die Seite **zeigt**, ändert sich nicht: Gestaltung, Zeitachse, Kennzahlen,
Blockreihenfolge, Trace-Baum mit seiner Verdichtung, Tools-Fenster,
Registerkarten, Run-Kontext-Panel, Tastaturpfad und `?focus`-Deep-Links bleiben
in Aussehen und Verhalten, wie sie sind. Auch die Entprellung, die
Reconnect-Logik über `Last-Event-ID` und das Schließen des Stroms nach dem
Lauf-Ende bleiben unverändert.

## Aufgabe

**A1 — Eine Refresh-Anfrage bekommt nur die Regionen.** Der Server wertet den
`X-Requested-With: fetch`-Header aus, den der Client bereits schickt, und
liefert dann ausschließlich die Regionen, die `swapRegions` austauscht — ohne
`<head>`, ohne Dokumentrahmen. Derselbe Renderpfad, dieselbe Markup-Erzeugung,
nur ein engerer Ausschnitt.

**A2 — Detail-Panes werden bei Bedarf geladen.** Die ausgelieferte Seite
enthält den Pane-Körper des ausgewählten Knotens; die übrigen Panes werden
nachgeladen, wenn ihr Knoten ausgewählt wird. Das Muster ist `loadToolBody`
samt seiner Absicherungen: laufende Anfrage wiederverwenden, verspätete
Antwort für einen nicht mehr ausgewählten Knoten verwerfen, Ladezustand
anzeigen.

**A3 — Die Spec sagt, was der Code tut.** §7.3 beschreibt den tatsächlichen
Mechanismus (Regionentausch nach entprelltem Neuabruf, nicht inkrementelles
Patchen) einschließlich der neuen Teilantwort aus A1 und des Nachladens aus
A2. Die Zusage „Live-updating" der Run-Liste in §7.2 A wird als **noch nicht
umgesetzt** gekennzeichnet und in die Deferred-Liste der Spec aufgenommen —
sie wird in diesem Issue **nicht** gebaut (E5).

**A4 — i18n.** Jeder neue sichtbare Text (etwa der Ladezustand eines Panes)
liegt in `adw/gui/i18n.py` in beiden Sprachen vor, identische Schlüsselmengen.

**A5 — Doku und Changelog.** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
synchron nach A3. `CHANGELOG.md` und `CHANGELOG.de.md` synchron ergänzen.

## Normative Definitionen (bindend, nicht neu herzuleiten)

### Teilantwort (A1)

- Ausgelöst **allein** durch den Header `X-Requested-With: fetch`. Kein neuer
  Query-Parameter, keine neue Route, kein Content-Negotiation-Verfahren.
- Die Antwort enthält genau die Regionen aus `REGIONS`, in Dokumentreihenfolge,
  als HTML-Fragment. Der Client parst sie wie bisher und tauscht wie bisher.
- Das `data-latest-context`-Attribut, das heute vom `<body>` gelesen wird und
  **nicht** Teil der getauschten Regionen ist, muss weiterhin erreichbar sein —
  entweder an einem Element der Teilantwort oder auf demselben Weg wie heute.
  Ohne das friert das Kontext-Panel ohne Auswahl auf dem Wert des Seitenaufrufs
  ein (der Code weist heute ausdrücklich darauf hin).
- Ohne den Header ist die Antwort unverändert das vollständige Dokument.

### Nachladen der Panes (A2)

- Der Nachladeweg benutzt die **vorhandene** Seite mit dem bestehenden
  `?focus=<seq>` und dem Header aus A1. **Keine neue Route, kein neuer
  Query-Parameter.**
- Eine bereits laufende Anfrage für denselben Knoten wird wiederverwendet, nicht
  verdoppelt.
- Eine Antwort, deren Knoten nicht mehr der ausgewählte ist, schreibt nichts und
  hinterlässt keinen halben Zustand.
- Während des Ladens zeigt der Pane einen Ladezustand, keinen leeren Kasten.
- Ein Knoten, der bereits geladen wurde, wird nicht erneut geholt, solange die
  Seite nicht ausgetauscht wurde.
- Fehlschlägt die Anfrage, bleibt die letzte gute Ansicht stehen und der Pane
  sagt, dass er nicht geladen werden konnte — wie `refresh()` es heute mit
  `/* transient read error: keep the last good view */` hält.

### Was gleich bleiben muss

Auswahl per Klick und per Tastatur, `?focus`-Deep-Links aus der Timeline, das
Tools-Fenster (`?tools_offset`) samt seiner 200er-Schranke, der Klappzustand
über einen Regionentausch hinweg (`captureOpenState` / `reapplyOpenState`), die
Wiederanwendung der Auswahl (`applySelection`), die Standard-Faltung
(`initTreeFold`) und die Entprellung von 200 ms.

## Vorentscheidungen (entschieden — kein Finding, auch nicht im Review-Loop)

**E1** — Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
Template-Engine im Client. Vanilla JS, Jinja2 auf dem Server
(`docs/GUI-SPEC.md:316`). Diese Frage ist entschieden.

**E2** — **Kein inkrementelles Patchen des Baums.** Die Zusage aus §7.3 wird
nicht eingelöst, sondern korrigiert: seit der Verdichtung (Release 0.17.0)
kann ein einzelnes neues Ereignis die Mitgliedschaft und die Zähler ganzer
Sammelknoten verändern, und ein Patcher müsste die komplette
Verdichtungslogik im Client zweitimplementieren. Der Regionentausch bleibt der
Mechanismus. Diese Frage ist entschieden.

**E3** — **Keine neue Route und kein neuer Query-Parameter.** A1 hängt am
vorhandenen Header, A2 am vorhandenen `?focus`. Diese Frage ist entschieden.

**E4** — Die Entprellung bleibt bei 200 ms, und der Auslöser bleibt der
Ereignisstrom. Kein Polling, kein längeres Intervall, keine Ratenbegrenzung als
Ersatz für A1/A2 — die Nutzlast wird kleiner, nicht seltener. Diese Frage ist
entschieden.

**E5** — Die Run-Liste wird in diesem Issue **nicht** live gemacht. A3
korrigiert nur die Zusage in der Spec. Diese Frage ist entschieden.

**E6** — Was die Seite zeigt, ändert sich nicht: keine neue Information, keine
geänderte Reihenfolge, kein geändertes Aussehen. Ein Nutzer soll den
Unterschied nur daran merken, dass die Ansicht während eines laufenden Laufs
ruhiger und schneller ist. Diese Frage ist entschieden.

**E7** — Gestaltung (Brief 1), Zahlen (Brief 2), Anordnung (Brief 3) und
Tastaturpfad (Brief 4) werden benutzt, nicht revidiert. Diese Frage ist
entschieden.

**E8** — Keine Änderung an Ereignistypen, Instrumentierung, `build_tree`, der
Verdichtung, dem Tools-Fenster, der Retention oder der Reconnect-Logik. Diese
Frage ist entschieden.

## Nicht-Ziele / Scope-Deckel

Kein neues Tab, keine neue Ansicht, keine Änderung an der Run-Liste, keine
Persistenz, kein neues Zustands-Subsystem, keine Änderung an den API-Routen
unter `/api`. Keine Kompression, kein Caching-Header-Tuning, kein
ETag-Verfahren — die Seite soll weniger enthalten, nicht dasselbe kleiner
verpackt.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Live-Aktualisierung der Run-Liste (E5).
- Inkrementelles Patchen des Trace-Baums (E2).
- Virtualisiertes Rendern der Baum-Spalte, Lazy-Rendering nach Sichtbarkeit.
- Kompression, ETags, bedingte Anfragen, Caching-Strategie.
- Nachladen weiterer Flächen (Artefakte, Raw-Tab, Change-Scope).
- Vorausladen des wahrscheinlich nächsten Panes.
- Messpunkte oder Telemetrie über die Nutzlast im Betrieb.

## Contract-Hinweis

Single-Lane-Projekt (`backend`, siehe `.adw/config.yaml`).

- **Bewusste Verhaltensänderung an einer HTML-Route:**
  `GET /runs/{repo}/{run_id}` antwortet **mit** dem Header
  `X-Requested-With: fetch` ab jetzt mit einem Fragment statt einem vollen
  Dokument. Ohne den Header ist die Antwort unverändert. Das ist im
  Spec-Abschnitt „Contract" ausdrücklich zu benennen.
- **Unverändert:** alle Routen unter `/api` in Feldern, Typen und Werten —
  `/api/runs`, `/api/runs/{repo}/{run_id}`, `/api/runs/{repo}/{run_id}/events`,
  `…/diff`, `…/artifact`, `…/stream`. Keine neue Route, kein neuer
  Query-Parameter.
- Regressionstests fixieren beides: die Vollantwort ohne Header ist unverändert,
  und die API-Antworten sind unverändert.

## Toolchain (Fakten, nicht aus Allgemeinwissen ableiten)

Gates sind `uv run ruff check .` und `uv run pytest -x -q`. Es gibt **kein**
flake8, **kein** isort, **kein** black; `ruff format` ist bewusst kein Gate.
Tests liegen flach unter `tests/` als `test_gui_*.py`. Für Client-Verhalten
(Auswahl, Nachladen, Regionentausch) ist der Harness
`tests/gui_js_harness.js` / `tests/gui_js_harness.py` der vorgesehene Ort. Der
Abnahmepunkt 10 in `docs/GUI-SPEC.md` nennt noch „flake8 + isort" — veralteter
Spec-Text, wird nicht befolgt.

## Akzeptanzkriterien (messbar)

1. **Teilantwort:** Eine Anfrage an `GET /runs/{repo}/{run_id}` **mit**
   `X-Requested-With: fetch` liefert ein Fragment ohne `<head>` und ohne
   `<html>`; es enthält die Regionen `header.run-header` und `main.detail`.
2. **Vollantwort unverändert:** Dieselbe Anfrage **ohne** den Header liefert
   weiterhin ein vollständiges Dokument.
3. **Kontext ohne Auswahl:** Nach einer Teilantwort führt das Kontext-Panel
   ohne Auswahl weiterhin den aktuellen Wert nach und friert nicht auf dem Wert
   des ersten Seitenaufrufs ein.
4. **Panes bei Bedarf:** Die für `16f39431` ausgelieferte Seite enthält
   höchstens **einen** Pane-Körper (heute 62, davon beim Aufruf ohne `?focus`
   null sichtbar).
5. **Seitengröße:** Die ausgelieferte Seite für `16f39431` ist gegenüber dem
   heutigen Stand von 847 791 Byte um mindestens **35 %** kleiner
   (durchgerechnet: allein die 61 nicht sichtbaren Panes sind 45,3 % des
   Dokuments).
6. **Auswahl lädt nach:** Die Auswahl eines Knotens ohne geladenen Pane holt
   dessen Inhalt und zeigt ihn; währenddessen steht ein Ladezustand, kein leerer
   Kasten.
7. **Wettlauf:** Wird während eines laufenden Ladevorgangs ein anderer Knoten
   gewählt, schreibt die verspätete Antwort nichts, und der zuletzt gewählte
   Knoten zeigt seinen eigenen Inhalt.
8. **Keine Doppelanfrage:** Zweimaliges Auswählen desselben Knotens erzeugt
   nicht zwei Anfragen; ein bereits geladener Pane wird nicht erneut geholt.
9. **Fehlerfall:** Schlägt das Nachladen fehl, bleibt die übrige Ansicht stehen,
   und der Pane sagt, dass er nicht geladen werden konnte.
10. **Deep-Link:** Ein Aufruf mit `?focus=<seq>` zeigt den Ziel-Pane wie bisher.
11. **Tastatur:** Die Auswahl per Tastatur löst denselben Nachladeweg aus wie
    ein Klick.
12. **Zustand über den Tausch:** Klappzustand, Auswahl, Standard-Faltung und
    das Tools-Fenster verhalten sich nach einem Regionentausch unverändert.
13. **Contract:** Die Antworten aller `/api`-Routen sind unverändert.
14. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
    Laufzeit-Dependency, kein Frontend-Paket, kein CDN.

## Testumfang

Richtwert **~13 neue Tests**; deutlich mehr als ~18 ist Scope-Drift.
Serverseitig die Teilantwort und die Seitengröße (AC 1–5, 13), im JS-Harness
das Nachladen samt Wettlauf, Doppelanfrage und Fehlerfall (AC 6–9, 11–12). Die
bestehenden GUI-Tests bleiben grün. **Achtung:** Tests, die heute davon
ausgehen, dass jeder Pane-Körper im ausgelieferten HTML steht, prüfen eine
Eigenschaft, die dieses Issue absichtlich aufhebt — sie werden auf den
Nachladeweg gehoben, mit einem Kommentar, der sagt warum. Das ist die einzige
erlaubte inhaltliche Änderung an bestehenden Tests.
