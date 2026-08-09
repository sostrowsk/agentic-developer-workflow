# Plan — GUI-Politur Lauf 5 (sieben Korrekturen an der Run-Inspector-Web-App)

Maßgeblich bleibt `docs/GUI-SPEC.md` (§7.2 Views, §9 Performance); bei Widerspruch
gilt die GUI-SPEC. Dieser Plan setzt `.adw/spec.md` um und baut strikt gegen
`.adw/contract.yaml`. **Single-Lane:** es gibt genau den Workstream **backend** —
die Web-Schicht der GUI (`adw/gui/app.py`, die Jinja2-Templates und die
mitgelieferten Vanilla-Assets `static/app.css`, `static/app.js`) gehört ganz in
diese eine Lane; eine eigenständige Frontend-Lane existiert nicht. Behoben werden
ausschließlich die sieben Aufgaben A–G; es ist **Politur an Vorhandenem**, kein
Neubau und kein Redesign.

## Leitplanken

- **Nur die Web-Schicht.** Geändert werden ausschließlich `adw/gui/app.py`, die
  Templates (`adw/gui/templates/*.html`), die Eigen-Assets (`adw/gui/static/*`)
  und die zugehörigen Tests/Fixtures — samt der statusableitenden
  Modell-**Anbindung in der Web-Schicht**. `adw/events.py`, `adw/snapshots.py`,
  `adw/gui/reader.py`, `adw/gui/model.py` und der Orchestrator bleiben
  **unverändert**. Ist eine Aufgabe ohne eine solche Änderung nicht lösbar, ist
  das ein **Befund für den Bericht**, keine stille Ausweitung (Non-Goal).
- **Strikt read-only.** Kein Codepfad der GUI schreibt in `state.json`, ins Repo
  oder ins Event-Log; keine schreibende HTTP-Route; kein Ausführen eines externen
  Programms. Run-Daten werden weiterhin nur unterhalb des aufgelösten
  `.adw/runs/<run_id>/` gelesen — unter denselben Containment-/`RUN_ID_RE`-/
  Slug-Backstops wie bisher.
- **Keine neuen Views, keine neue Informationsarchitektur, kein Navigationsumbau.**
  Korrektur der Statusableitung (A) und der Darstellung (B–G) an den vorhandenen
  Views (`/`, `/runs/{repo}/{run_id}` und die `/api`-Routen).
- **Neue HTTP-Routen nur, soweit Aufgabe B sie zwingend braucht**, und
  ausschließlich read-only; sonst keine neue Route. Bevorzugter Weg (siehe
  Aufgabe B): Nachladen über die **bereits vorhandene** Events-Route, dann
  entsteht gar keine neue Route. Der Mechanismus für B bleibt
  Umsetzungsentscheidung und ist **nicht kontraktuell**.
- **Keine neuen Laufzeit-Dependencies, kein Frontend-Fremdasset** (Vanilla JS,
  handgeschriebenes CSS, System-Fonts; kein CDN, keine node-Toolchain). Der
  Web-Stack bleibt optionales Extra `adw[gui]` (E7).
- Fehlende Werte werden durchgängig als fehlend behandelt und niemals zu
  darzustellendem `0` oder `null` umgedeutet.
- Reale Gates (E3): `uv run ruff check .` und `uv run pytest -x -q`. `flake8`,
  `isort`, `black` tauchen nirgends auf.
- Kappung/Einklappen/abschnittsweises Nachladen **in der Anzeige** ist per E8
  ausdrücklich erlaubt und **kein** Verstoß gegen „keine Kappung von Payloads“
  (das betrifft das Log, nicht die Darstellung).

## Ausgangslage (verifiziert im Code)

- **Statusableitung (Aufgabe A).** `app.py:_run_span` wählt per `next(...)` den
  **ersten** `run`-Start und das **erste** `run`-End; `_summary` liest den Status
  aus dessen End-Payload (`status` bzw. `"running"`, wenn kein End). Bei mehreren
  `run`-Spans in einer Datei zeigen Liste **und** Detail damit den Status des
  ersten Spans. Korrektur liegt vollständig in der Web-Schicht (`app.py`).
- **Freeze (Aufgabe B).** `run_detail.html` rendert über `all_panes(...)` **alle**
  Panes inline und bettet je `agent.run` **jeden** Tool-Call/-Result mit vollem
  `payload | tojson` ein; zusätzlich steckt das gesamte `detail | tojson` als
  `<script id="run-data">` in der Seite. Der Client (`app.js`) parst bei jedem
  SSE-Refresh die **ganze** Seite per `DOMParser` neu. Bei einem mehrere MB
  großen Log ist das die Ursache des ~35-s-Freezes. B verlangt beobachtbar:
  Die Auswahl blockiert die Oberfläche nicht spürbar, und alle vollen Inhalte
  bleiben erreichbar — ob dafür die initiale Auslieferung begrenzt oder die
  volle Auslieferung nicht-blockierend materialisiert wird, ist
  Umsetzungsentscheidung.
- **Tool-Call-Labels (Aufgabe C).** `_node_label` liefert für `agent.tool.call`/
  `agent.tool.result` nur den Typnamen; im Tools-Bereich steht `c.type` plus
  rohes Payload-JSON. Werkzeugname und Hauptargument liegen im Payload bereit
  (`tool`, `input`; bei Result `is_error`, `content`).
- **Reiter (Aufgabe D).** `run_detail.html` hat für `agent.run` bereits
  Abschnitte Prompt/Answer/Tools, aber als gleichzeitig sichtbare
  `<section class="tab">` (gestapelt), nicht als umschaltbare Reiter. Ein
  Diff-Reiter existiert nicht (bleibt so).
- **Formatierung (Aufgabe E).** Dauern als `"%.1f"|format(..)+"s"`, Kosten als
  roher Float, Zeitstempel als rohes ISO mit `Z`; in der Run-Liste bricht der
  Start-Zeitstempel um. Aufbereitung erfolgt in der Web-Schicht (Serialisierung/
  Template/CSS).
- **Überlauf (Aufgabe F).** `app.css`: `pre` hat `overflow-x:auto` +
  `white-space:pre-wrap`, aber Prompt-Pane/Seite laufen rechts aus dem Viewport —
  typisch fehlende Schrumpfgrenzen (`min-width`) an Grid-/Flex-Containern.
  Reine CSS-Korrektur.
- **Läufe ohne Event-Log (Aufgabe G).** `_list_runs` überspringt Runs ohne
  `events.jsonl` (`if events_file is None … continue`). `require_run` toleriert
  bereits State-only-Runs (`has_events or has_state`). G ist damit eine
  Web-Schicht-Änderung an `_list_runs` und der Detail-Aufbereitung.
- **Fixtures/Tests.** `tests/gui_app_helpers.py` liefert die Builder (`rec`,
  `write_run`, `comprehensive_lines` …) und die `home`-Fixture; die App wird über
  `create_app(repos=…)` mit FastAPIs `TestClient` gegen Fixture-Logs geprüft.
  Neue Tests bauen auf diesen Helfern auf.

## Workstream: backend

### Aufgabe A — Statusableitung (Defekt)

- **A1.** Die Run-Status-Ableitung in der Web-Schicht (`app.py`) so korrigieren,
  dass sie den Status des **letzten** `run`-Spans (in Log-Reihenfolge) verwendet —
  in Run-Liste (`/api/runs`, `/`) **und** Run-Detail (`/api/runs/{repo}/{run_id}`,
  `/runs/{repo}/{run_id}`) identisch; ältere abgeschlossene oder wartende
  `run`-Spans überschreiben den sichtbaren Status nicht.
- **A2.** Hat der letzte `run`-Span noch kein `end`, wird der Lauf als `running`
  angezeigt.
- **A3.** Für das Beispiel-Log aus dem Issue (drei `run`-Spans, letzter
  `status=done`) erscheint der Lauf als `done`, nicht als `awaiting_approval` — in
  Liste und Detail gleich.

Tests A:
- Log mit mehreren `run`-Spans (älterer Span `awaiting_approval`, letzter
  `status=done`): Liste und Detail zeigen `done` (A1/A3).
- Log, dessen letzter `run`-Span kein `end` hat: `running` (A2).
- Deckt A1–A3 ab (A4: Regressionstest über mehrere `run`-Spans).

### Aufgabe B — Auswahl eines Knotens blockiert die Oberfläche nicht (Defekt)

- **B1. Deterministische Fixture.** Eine reproduzierbar erzeugte `events.jsonl`
  mit einem `agent.run`-Span, der **≥ 40** Tool-Call-/Tool-Result-Paare mit vollen
  Ein-/Ausgaben enthält; Payloads zusammen **≥ 5 MB**, darunter mindestens ein
  einzelnes Tool-Ergebnis **≥ 1 MB** (deutlich unter der 200-MB-Grenze aus §9).
  Dieselbe Fixture dient manuellem Check und automatisierten Tests. Sie wird in
  den Testhelfern deterministisch erzeugt (nicht als großes Binärartefakt
  eingecheckt, sofern vermeidbar).
- **B2. Anforderung (beobachtbar) und Mechanismus (Umsetzungsentscheidung).**
  Beobachtbar gefordert ist nur: Das Auswählen eines `agent.run`-Knotens
  blockiert die Oberfläche nicht spürbar (Messgrenze aus B1/B4), und der Nutzer
  kommt an **jeden** vollständigen Inhalt heran (Prompt, Antwort, jeder
  Tool-Call-Input, jedes Tool-Ergebnis) — nicht zwingend sofort und alles auf
  einmal, aber erreichbar. Der **Inhalt der initialen HTTP-Auslieferung wird
  nicht vorgeschrieben.** Zulässige Mechanismen sind z. B.: (a) die initiale
  Auslieferung begrenzen und volle Inhalte pro Abschnitt nachladen, oder
  (b) die vollen Daten initial ausliefern, aber nicht synchron in einen
  blockierenden DOM materialisieren (nur den sichtbaren Ausschnitt rendern,
  Inhalte eingeklappt/gekürzt, Aufklappen bei Bedarf). Ein Reiterwechsel bleibt
  während etwaigen Nachladens/Renderns unmittelbar sichtbar; Fehler beim
  Nachladen werden im Pane verständlich angezeigt. Die Wahl des Mechanismus
  bleibt frei und ist nicht kontraktuell.
- **B3. Bevorzugt keine neue Route.** Braucht der Mechanismus Nachladen, wird
  zuerst die **vorhandene** read-only Route
  `GET /api/runs/{repo}/{run_id}/events` genutzt — dann entsteht keine neue
  Route. Nur wenn das nachweislich nicht genügt, entsteht **read-only** und
  ausschließlich soweit zwingend nötig eine neue HTTP-Route unter denselben
  Containment-/`RUN_ID_RE`-/Slug-Backstops (nie ein Zugriff außerhalb
  `.adw/runs/<run_id>/`, nie 5xx auf den gepinnten Fehlerfällen); die
  Begründung des zwingenden Bedarfs geht in den Bericht. Der genaue Routenpfad
  ist **nicht kontraktuell**.
- **B4. Nachweis.** Die 2-Sekunden-Grenze wird durch einen dokumentierten,
  reproduzierbaren **manuellen** Browser-Check an der B1-Fixture belegt: Messung
  **beginnt mit dem Klick** auf den `agent.run`-Knoten; **unmittelbar danach**
  Reiterwechsel (z. B. auf **Tools**); Messung **endet, sobald der gewählte Reiter
  sichtbar aktiv ist** und Inhalt zeigt; Gesamtzeit ≤ 2 s. Ablauf und Ergebnis
  gehen in den Bericht. Automatisierte Browser-/Performance-Messung ist Deferred
  (würde eine neue Toolchain erfordern).

Tests B (automatisierbarer Teil, gegen die B1-Fixture mit `TestClient`; die
Tests decken den **beobachtbaren Effekt des gewählten Mechanismus** ab und
richten sich nach ihm):
- Bei Mechanismus (a) — begrenzte Auslieferung: die initiale Detail-Auslieferung
  (`/runs/{repo}/{run_id}` bzw. `/api/runs/{repo}/{run_id}`) enthält nicht
  sämtliche vollen Payloads auf einmal (z. B. Antwortgröße deutlich unter der
  Summe der Payloads, oder das größte Tool-Ergebnis nicht vollständig inline).
- Bei Mechanismus (b) — volle Auslieferung mit nicht-blockierendem Rendern: das
  initial materialisierte/sichtbare Markup des Detail-Panes trägt nicht
  sämtliche vollen Payloads gleichzeitig als gerenderten Inhalt (begrenzte
  initiale DOM-Materialisierung als beobachtbarer Effekt; das Aufklappen
  liefert den vollen Inhalt).
- In beiden Fällen: Jeder vollständige Inhalt bleibt erreichbar (der vom
  Mechanismus vorgesehene Weg liefert das ≥ 1 MB große Tool-Ergebnis
  vollständig).
- Wird für B doch eine neue Route eingeführt: sie ist read-only, respektiert
  `RUN_ID_RE`/Slug/Containment und liefert nie 5xx auf den Fehlerfällen.

### Aufgabe C — Lesbarkeit der Tool-Call-Einträge

- **C1.** Ein `agent.tool.call`-Knoten zeigt Werkzeugname und werkzeugspezifisches
  Hauptargument aus dem Payload. Feldpriorität: **Read → Dateipfad**,
  **Bash → Kommandozeile** (ggf. gekürzt), **Grep → Suchmuster** (z. B.
  „Read models.py“, „Bash pytest -x -q“, „Grep RUN_ID_RE“). Für andere Werkzeuge:
  Werkzeugname plus im Payload eindeutig identifizierbares Hauptargument, sonst
  Werkzeugname allein. Keine Unterstützung über den Payload-Inhalt hinaus.
- **C2.** Ein `agent.tool.result`-Knoten zeigt kompakt seinen Ausgang
  (Fehler/Erfolg; bei Bash der Exit-Code, sofern im Payload vorhanden).
- **C3. Rückfall — nichts wird erfunden.** Fehlt der **Werkzeugname**, bleibt der
  unveränderte Typname (`agent.tool.call`/`agent.tool.result`). Ist der
  Werkzeugname da, aber das Hauptargument fehlt, wird der Werkzeugname allein
  gezeigt — kein Ersatzwert. Fehlen bei einem Ergebnis die Ausgangsfelder, bleibt
  es beim Typnamen.

Tests C:
- Read/Bash/Grep sowie mindestens ein weiteres Werkzeug zeigen Werkzeug +
  Hauptargument nach der Feldpriorität (C1).
- `agent.tool.result` zeigt Fehler/Erfolg, bei Bash den Exit-Code, sofern
  vorhanden (C2).
- Rückfälle: fehlender Werkzeugname → Typname; fehlendes Argument →
  Werkzeugname allein; Result ohne Ausgangsfelder → Typname (C3).

### Aufgabe D — Reiter im Detail-Pane

- **D1.** Für `agent.run` bietet das Detail-Pane die Reiter **Prompt**,
  **Antwort** und **Tools** als umschaltbare Reiter (immer genau einer
  sichtbar/aktiv), statt gestapelter Abschnitte; Umschaltung per Vanilla JS.
  Die Beschriftung ist per Spec D1 genau „Prompt“, „Antwort“, „Tools“; der
  vorhandene gestapelte Abschnitt „Answer“ wird dabei zum Reiter „Antwort“.
  Inhalte wie bisher: Prompt = voller Task-String inkl.
  System-Append; Antwort = finaler Text plus Zwischen-Assistant-Messages;
  Tools = chronologische Tool-Call-Liste (mit den Labels aus C, voller Inhalt
  über den B-Mechanismus erreichbar).
- **D2.** **Kein** Diff-Reiter in diesem Lauf (ebenso kein Raw-/Artefakte-/
  Timeline-Reiter — Non-Goals).

Tests D:
- Das `agent.run`-Detail bietet genau die drei umschaltbaren Reiter Prompt/
  Antwort/Tools und keinen Diff-Reiter (D1/D2).

### Aufgabe E — Formatierung von Zahlen und Zeiten

- **E1.** Dauern lesbar formatiert (z. B. `2828.7s` → `47m 9s`) statt roher
  Sekunden.
- **E2.** Kosten lesbar als Geldbetrag mit zwei Nachkommastellen (z. B.
  `5.795072500000001` → `$5.80`) statt roher Float.
- **E3.** Zeitstempel als `YYYY-MM-DD HH:MM:SS` in **UTC** — ohne `Z`-Suffix,
  ohne Sekundenbruchteile (deterministisch, umgebungsunabhängig testbar; die
  Quelldaten liegen als ISO-UTC vor).
- **E4.** In der Run-Listen-Tabelle steht ein Zeitstempel auf **einer** Zeile,
  ohne Umbruch (geprüft am Tabellen-Standardfall).
- **E5.** Fehlende Werte bleiben leer; nie `0` oder `null` als Text.

Tests E:
- Dauer-Formatierung (E1), Kosten-Formatierung (E2), UTC-Zeitstempelformat ohne
  `Z`/Sekundenbruchteile (E3).
- Kein Umbruch des Zeitstempels in der Run-Liste (E4, am Standardfall).
- Fehlende Dauer/Kosten/Zeit bleiben leer, nie `0`/`null` als Text (E5).

### Aufgabe F — Kein horizontaler Überlauf

- **F1.** Breite Inhalte (Prompts, Tool-Ausgaben, Tabellen) scrollen innerhalb
  ihres eigenen Kastens oder brechen um. Dazu an Page-, Grid- und
  Flex-Containern die nötigen Schrumpfgrenzen setzen (z. B. `min-width: 0`),
  damit breite Kinder den Viewport nicht verbreitern.
- **F2.** Die Seite selbst scrollt nie horizontal; das Prompt-Pane läuft nicht
  rechts aus dem Viewport, kein Text wird abgeschnitten.

Tests F:
- Automatisierbarer Anteil: die relevanten Container tragen die
  containment-/umbruch-erzeugenden Regeln (geprüft am ausgelieferten CSS/Markup,
  ohne Headless-Browser). Die visuelle Bestätigung von „Seite scrollt nie
  horizontal“ ist Teil des manuellen Browser-Checks (kein neuer Headless-Stack —
  Deferred).

### Aufgabe G — Läufe ohne Event-Log

- **G1.** Läufe mit `state.json`, aber ohne `events.jsonl` (z. B. `8f8dc4ff`,
  `e680e005`) erscheinen in der Run-Liste — mit den Angaben aus dem State;
  `_list_runs` nimmt `events.jsonl` nur noch optional hinzu. Nicht belegbare
  Werte bleiben leer (E5).
- **G2.** Für solche Läufe zeigt die Oberfläche einen klaren Hinweis, dass kein
  Trace existiert.
- **G3.** Der Aufruf ihres Details führt nie zu einem Fehler; auch die
  vorhandene Events-Oberfläche antwortet für einen solchen gültigen Run
  kontrolliert und ohne Serverfehler (leere Events-Liste).

Tests G:
- Ein Repo mit einem State-only-Run: der Run erscheint in `/api/runs` und `/` mit
  den State-Angaben (G1) und mit einem klaren „kein Trace“-Hinweis (G2).
- Das Detail eines State-only-Runs (`/runs/{repo}/{run_id}` und
  `/api/runs/{repo}/{run_id}`) antwortet fehlerfrei (G3).

## Guardrail Testumfang

Richtwert rund **15–22** neue Tests für A–G zusammen (Richtwert aus dem Issue,
keine harte Grenze). Maßgeblich ist die Abdeckung der Akzeptanzkriterien, nicht
die exakte Anzahl; deutlich mehr ist ein Signal für Scope-Drift. Ausnahme von der
Test-Abdeckung: die 2-Sekunden-Grenze aus B1 wird gemäß B4 durch den
dokumentierten manuellen Browser-Check belegt; der automatisierbare Teil von B
ist durch Tests abgedeckt.

## Definition of Done

1. Alle Akzeptanzkriterien A1–A4, B1–B4, C1–C3, D1–D2, E1–E5, F1–F2, G1–G3 sind
   erfüllt. Alle sind durch automatisierte Tests abgedeckt — mit einer Ausnahme:
   die 2-Sekunden-Grenze aus B1 wird gemäß B4 durch den dokumentierten,
   reproduzierbaren manuellen Browser-Check belegt; der automatisierbare Teil von
   B ist durch Tests abgedeckt.
2. `uv run ruff check .` ist grün.
3. `uv run pytest -x -q` ist grün (die bestehenden 653 Tests plus die neuen
   A–G-Tests).
4. Keine der unter Non-Goals genannten Dateien (`adw/events.py`,
   `adw/snapshots.py`, `adw/gui/reader.py`, `adw/gui/model.py`, Orchestrator)
   wurde geändert; ist eine Aufgabe ohne solche Änderung nicht lösbar, steht das
   als Befund im Bericht statt als stille Ausweitung.
5. Keine neue Laufzeit-Dependency und kein Frontend-Fremdasset ist hinzugekommen;
   neue HTTP-Routen nur die für Aufgabe B zwingend nötigen (bevorzugt: keine),
   alle read-only. Die GUI bleibt strikt read-only.
6. Der manuelle B4-Ablauf und sein Ergebnis stehen im Bericht.

## Deferred (bewusst nicht gebaut)

Weitergehende Härtungs- oder Erweiterungsideen — auch aus den Codex-Review-Runden
— gehören hierher, nicht in die Akzeptanzkriterien. Ein Finding, das einen dieser
Punkte oder einen vorentschiedenen Punkt einführen will, wird abgewiesen und mit
Begründung dokumentiert, nicht umgesetzt.

- Timeline-Reiter, Artefakte-Reiter, Raw-Reiter, Diff-Reiter, Diff-Endpoint.
- i18n / Sprachumschaltung.
- Prunen / Retention / `trace:`-Config-Key.
- Änderungen an der `run`-Span-Grenze (E1) oder an den Waisen-Spans/`events.py`
  (E2).
- Kappung von Payloads im **Log**: Der Deferred-Punkt „keine Kappung von
  Payloads“ betrifft das Log, nicht die Darstellung (E8). Aufgabe B kürzt,
  klappt ein oder lädt abschnittsweise **in der Anzeige** und verstößt damit
  nicht gegen „keine Kappung“; ein Review-Finding, das B als solchen Verstoß
  wertet, wird abgewiesen.
- Redesign, neue Views, neue Informationsarchitektur, Umbau der Navigation.
- HTTP-Routen über die für Aufgabe B zwingend nötigen hinaus.
- Automatisierte Browser-/Responsiveness-Messung (Headless-Browser,
  Performance-Harness): würde eine neue Toolchain erfordern (E5) und ist für
  den Nachweis von B1 nicht nötig — der dokumentierte manuelle Check genügt.
- Zeitzonen-Umschaltung / Anzeige in Lokalzeit statt UTC.
