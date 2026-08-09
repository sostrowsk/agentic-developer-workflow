# Plan — `adw gui`: read-only Web-App (Run-Liste, Run-Detail, SSE) + Home-Isolation-Bugfix

Maßgeblich bleibt `docs/GUI-SPEC.md` (§7 Web-App, §8 Sicherheit, §4.2 Modell);
bei Widerspruch gilt die GUI-SPEC. Dieser Plan setzt `.adw/spec.md` um und baut
strikt gegen `.adw/contract.yaml`. **Single-Lane:** es gibt genau den Workstream
**backend** — die App, ihre Templates und die mitgelieferten Vanilla-Assets
gehören alle in diese eine Lane; eine eigenständige Frontend-Lane existiert
nicht. Die Lese-Datenschicht (`adw/gui/reader.py`, `adw/gui/model.py`,
`adw/gui/registry.py`) ist gebaut und liegt auf `main`. `adw/gui/reader.py` und
`adw/gui/model.py` werden in diesem Lauf **nur konsumiert, nicht verändert**;
`adw/gui/registry.py` erhält **ausschließlich** die in Aufgabe A geforderte
isolierbare Pfad-Quelle und bleibt sonst unverändert.

## Leitplanken

- `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py` und
  `adw/gui/model.py` bleiben **unverändert**. Reicht deren API nicht aus, ist
  das ein Befund für den Bericht — keine stille Erweiterung (Non-Goal).
  `adw/gui/registry.py` darf **allein** die isolierbare Pfad-Quelle aus
  Aufgabe A erhalten; Format und Laufzeitverhalten bleiben sonst gleich.
- Kein Refactoring von `phases.py`; außer Aufgabe A keine Änderung am
  Orchestrator (`phases.py`, Run-Pfad in `cli.py`). Keine zusätzlichen
  `phase`/`lane`-Felder (E6).
- **Strikt read-only (§8):** kein Codepfad der GUI schreibt in `state.json`,
  ins Repo oder ins Event-Log; keine schreibende HTTP-Route; kein Ausführen
  eines externen Programms (auch kein `git diff`). Run-Daten werden
  ausschließlich unterhalb des aufgelösten `.adw/runs/<run_id>/` gelesen —
  ausgenommen: die Registry-Datei (lesend), reine Existenz-/Lesbarkeitsprüfungen
  für Registry-Auflösung und `repo_exists` (Metadaten, kein Inhalt) und die
  paketeigenen Ressourcen (Templates, CSS/JS).
- **Verpackung (E7):** FastAPI, uvicorn und Jinja2 ausschließlich als optionales
  Extra `adw[gui]`; das Kernpaket und der `adw run`-Importpfad bleiben frei von
  Web-Abhängigkeiten. Kein Fremd-Frontend-Asset (E5): Vanilla JS
  (`fetch`, natives `EventSource`), handgeschriebenes CSS, System-Fonts —
  kein htmx, kein CDN, keine node-Toolchain, kein Vendoring, nichts aus dem
  Netz.
- Keine Persistenz: keine Index-, Cache-, Offset- oder Cursor-Dateien; auch der
  Stream schreibt keinen Zustand auf die Platte. Ein Byte-Offset im Speicher ist
  erlaubt.
- Reale Gates (E3): `uv run ruff check .` und `uv run pytest -x -q`.
  `flake8`, `isort`, `black` tauchen nirgends auf.
- Oberfläche einsprachig **Englisch** (keine i18n, kein `--lang` — Lauf 5).

## Ausgangslage (verifiziert im Code)

- **Reader** (`adw/gui/reader.py`): `EventReader(path)` mit In-Memory-`offset`;
  `read() -> ReadResult` liefert nur neue, vollständige `\n`-terminierte Zeilen
  in Dateireihenfolge, meldet `bad_line`/`seq_gap` als `ReadProblem`, führt die
  `seq`-Kontinuität über Aufrufe hinweg. Eine unvollständige Schlusszeile wird
  weder geparst noch bestätigt. Genau das Werkzeug für Snapshot-Lesen (`/api`)
  UND Byte-Offset-Tailing (`/stream`).
- **Modell** (`adw/gui/model.py`): `build_tree(events) -> list[SpanNode]`.
  Wurzeln sind die `run`-Spans in Log-Reihenfolge; Waisen sind bereits nach der
  Enthaltungsregel eingehängt (E2). `SpanNode`/`PointNode` tragen
  `type`/`seq`/`ts`-Felder, `children` und den vollständigen Original-Record
  (`start_record`/`end_record` bzw. `record`) — die GUI serialisiert den Baum
  **unverändert** und rekonstruiert nichts selbst.
- **Registry** (`adw/gui/registry.py`): `load_registry() -> Registry`,
  `Registry.resolve(slug) -> RepoEntry | None`, `RepoEntry(path, slug,
  last_seen, exists)`. `register_repo(path)` wird von `cli.py:run` (Z. 152,
  fail-open) aufgerufen. `_registry_path()` (Z. 45) liefert heute fest
  `Path.home() / ".adw" / "repos.json"` — der einzige Punkt, den Aufgabe A
  testisolierbar machen muss.
- **Record-/Span-Schema:** jeder Record trägt `seq, ts, type, kind, span,
  parent, phase, lane, round, payload`. Der `run`-Span-Start (`cli.py:
  _run_start_payload`) trägt `issue, parallel, dry_run, repo, base_branch,
  adw_version, lanes`; sein End-Payload trägt `status` und `totals`
  (`duration, cost, tokens`). Daraus stammen die Run-Metadaten.
- **`RUN_ID_RE`** = `^[0-9a-f]{8}$` (`adw/state.py`). `RunState.load(repo,
  run_id)` liest `state.json` unter `.adw/runs/<run_id>/` und liefert die
  aktuelle `phase`; `RUNS_RELPATH = .adw/runs`.
- `cli.py` ist eine `typer.Typer`-App; neue Kommandos werden per `@app.command()`
  registriert. Der `adw run`-Importpfad zieht heute `adw.gui.registry` (reine
  Stdlib) — er darf keine Web-Stacks importieren.

## Workstream: backend

### Aufgabe A — Home-Isolation-Bugfix

Ziel: kein Test schreibt mehr in das reale `~/.adw/repos.json`; das produktive
Verhalten von `adw run` bleibt unverändert. Erlaubt ist **allein**, den
Pfad-Bezug der Registry überschreibbar zu machen — Format, atomisches
Schreiben, Slugs, `last_seen` und Fehlerbehandlung von `adw/gui/registry.py`
bleiben sonst gleich.

- **A1. Pfad-Quelle isolierbar (AC 2).** Den Bezug in `_registry_path()`
  überschreibbar machen, ohne das Default-Verhalten zu ändern. Mechanik frei und
  **nicht kontraktuell** — z. B. der Default liest `HOME`/eine Umgebungsvariable
  bzw. eine überschreibbare Pfad-Quelle. Außerhalb der Testisolation bleibt der
  aufgelöste Pfad exakt `~/.adw/repos.json` im bestehenden Format. Keine
  testbezogenen Verzweigungen im Laufzeitpfad.
- **A2. Geteilte Fixture (AC 1/2).** Eine (autouse) pytest-Fixture in `conftest`
  isoliert den Registry-Pfad in ein `tmp_path`-Verzeichnis für **jeden** Test,
  der `adw run` oder die Auto-Registrierung auslöst — nicht nur die
  Registry-eigenen Tests. Die bestehenden Registry-eigenen Tests werden auf
  dieselbe Isolation ausgerichtet. Temporäre pytest-Repo-Pfade erscheinen nie
  im echten Home.
- **A3. Produktverhalten unverändert (AC 3).** `adw run` registriert weiterhin
  fail-open nach `~/.adw/repos.json` im bestehenden Format; ein Registry-Fehler
  verhindert keinen Run. Kein Diff am Run-Pfad außer der isolierbaren
  Pfad-Quelle.

Tests A:
- **Regressionstest (AC 1):** ein Aufruf, der die Auto-Registrierung mit einem
  `tmp_path`-Repo auslöst, lässt — unter der Isolation — das reale
  `~/.adw/repos.json` unangetastet: existiert es vorher nicht, wird es nicht
  angelegt; existiert es, bleibt sein Inhalt bitgleich; der `tmp_path`-Pfad
  erscheint dort nie.
- Die Isolation greift für einen Auto-Registrierungs-Pfad **außerhalb** der
  Registry-eigenen Tests (z. B. über den Dry-Run-Einstieg).

### Aufgabe B — Web-App

Neues Modul `adw/gui/app.py` (FastAPI-App-Factory) samt Jinja2-Templates und
statischen Eigen-Assets (handgeschriebenes CSS + Vanilla JS), ausgeliefert als
Paketressourcen. Alle HTTP-Antwortformate, das SSE-Format und das CLI sind im
Kontrakt gepinnt; Template-/CSS-/JS-Dateistruktur und Markup sind es **nicht**.

- **B1. Verpackung (AC 21/22, §7.1, E7).** In `pyproject.toml` ein optionales
  Extra `[project.optional-dependencies] gui = [fastapi, uvicorn, jinja2]`
  deklarieren; das Kernpaket bleibt frei von Web-Abhängigkeiten. Der `adw run`-
  Importpfad importiert keinen dieser Web-Stacks — die Web-Imports leben in
  `adw/gui/app.py` und werden erst beim Aufruf von `adw gui` gezogen (lazy).
  Templates und statische Eigen-Assets sind als Paketressourcen in Wheel-/
  Installationsartefakten enthalten. Fehlt das Extra, endet `adw gui`
  **kontrolliert** mit einer verständlichen Installationsanweisung für
  `adw[gui]` (kein Traceback), während `adw run` und die übrigen Kernkommandos
  unverändert laufen.
- **B2. CLI `adw gui` (AC 4/5, §8).** Neues `@app.command()` `gui` mit
  `--repo PATH` (mehrfach), `--host` (default `127.0.0.1`), `--port` (default
  `8765`), `--open`, `--i-know`. Ohne `--i-know` wird eine
  Nicht-Loopback-`--host`-Adresse mit verständlicher Fehlermeldung abgelehnt —
  **kein Bind, kein Serverstart**; mit `--i-know` zugelassen (Grund: das Log
  enthält rohe, unredigierte Agent-Ausgaben). `--repo` macht die genannten Repos
  zusätzlich zu den Registry-Repos verfügbar. `--open` öffnet den Browser auf
  der lokalen Adresse; ohne `--open` wird kein Browser geöffnet. Startet die App
  über uvicorn. Die Loopback-Prüfung ist **vor** dem uvicorn-Start testbar
  (ohne echten Bind).
- **B3. App-Factory & Repo-Auflösung (AC 6/7, §7.4).** `create_app(repos=...)`
  baut die FastAPI-App über einer Auflösungsschicht `Registry-Repos ∪
  --repo-Repos`, adressiert je Repo über den stabilen Registry-Slug. Ein per
  `--repo` hinzugefügtes Repo erhält denselben stabilen Slug wie in der
  Registry; doppelte oder kollidierende `--repo`-Pfade werden kanonisch
  aufgelöst und deterministisch behandelt. `{repo}` wird **nie** als
  Dateisystempfad interpretiert; ein unbekannter Slug → `404`.
  Templates/Assets werden als Paketressourcen eingebunden. Testbar mit FastAPIs
  `TestClient` gegen Fixture-Logs.
- **B4. Path-Traversal-Schutz (AC 8, §7.4).** In allen `{run_id}`-Routen: nur
  `run_id`, die `RUN_ID_RE` (aus `adw/state.py`) vollständig erfüllen — formal
  ungültig → `400`; formal gültig, aber nicht vorhanden → `404`. Weder Slug noch
  `run_id` können aus `.adw/runs/<run_id>/` herausführen. Nie ein Serverfehler,
  nie ein Zugriff außerhalb `.adw/runs/<run_id>/`.
- **B5. Run-Liste — `GET /api/runs` und `GET /` (AC 9/10/11, §7.2 A).** JSON:
  je Run mindestens `run_id`, `repo` (Slug), `repo_exists`, `issue` (gekürzt),
  `phase`, `status`, `start`, `duration`, `cost`, `event_count`. **Laufende
  Runs zuerst.** Metadaten stammen aus dem `run`-Span (Start-/End-Payload) und
  `state.json` — beides unter `.adw/runs/<run_id>/`. Ein registriertes Repo,
  dessen Pfad nicht (mehr) erreichbar ist, liefert **einen
  Repo-Platzhalter-Eintrag** mit mindestens `repo`, `repo_exists: false` und
  einem darstellbaren Hinweis (z. B. registrierter Pfad) — ohne Run-Felder, ohne
  rekonstruiertes Run-Metadatum, ohne persistentes Caching; in der HTML-Liste
  eindeutig ausgegraut. Im Log fehlende Werte (z. B. Kosten in Dry-Runs ohne
  `usage`) werden als leer/`null` dargestellt, nicht als Fehler. Weder HTML noch
  JSON schlagen wegen eines fehlenden Repos fehl; die übrigen Repos bleiben
  normal gelistet. `GET /` rendert dieselbe Datenbasis servergerendert
  (englisch); Runs verlinken über Slug und Run-ID auf die Detailseite.
- **B6. Run-Detail-Daten — `GET /api/runs/{repo}/{run_id}` (AC 12/13/15/16).**
  JSON mindestens: Run-Metadaten, die **Phasen-Statusleiste**, den **Span-Baum**
  aus `adw.gui.model.build_tree` (serialisiert: je Knoten Typ/Label/Dauer/
  Payload und Kinder, unverändert aus dem Modell — keine eigene Rekonstruktion,
  Waisen bleiben wie eingehängt) und die vom Reader gemeldeten **Probleme**
  (`seq`-Lücken, kaputte Zeilen) mit Positions-/Sequenzinformation.
  Phasen-Statusleiste: die sieben Phasen `spec, plan, build, integration,
  codex_review, final_review, ci` in Workflow-Reihenfolge, je Phase Status
  (erledigt / aktiv / ausstehend / gescheitert) und Dauer, soweit aus den Daten
  (Phasen-Spans + `state.json`-Phase) bestimmbar; fehlende Dauer bleibt `null`.
  Unbekannte Event-Typen bleiben im Baum erhalten und werden generisch getragen
  (Typ-Label + rohes Payload).
- **B7. Roh-Events — `GET /api/runs/{repo}/{run_id}/events?from_seq=N`
  (AC, Kontrakt).** Liefert die vom Reader akzeptierten Roh-Event-Records mit
  ALLEN Originalfeldern in Dateireihenfolge, ab `seq >= N` (default: von
  Beginn). Unbekannte Event-Typen werden **nicht** gefiltert. Auch Records ohne
  gültige ganzzahlige `seq` werden mit ihrem Originalwert geliefert — nur der
  SSE-Stream schließt sie aus und meldet stattdessen ein Problem; ihr Verhalten
  unter dem `from_seq`-Filter ist nicht kontraktuell. Ein ungültiger
  `from_seq`-Wert wird kontrolliert als Client-Fehler beantwortet (kein
  Serverfehler; der exakte Statuscode ist nicht kontraktuell). Kein
  `type`-Filter, keine Pagination (deferred).
- **B8. SSE-Live-Stream — `GET /api/runs/{repo}/{run_id}/stream` (AC 18/19/20,
  §7.3).** `text/event-stream`; tailt `events.jsonl` per Byte-Offset über den
  Reader (Poll-Intervall **500 ms**, **keine** Filesystem-Watch-Abhängigkeit,
  **kein** Zustand auf Platte).
  - Jede neue vollständige, vom Reader akzeptierte Zeile mit gültiger ganzzahliger
    `seq` → SSE-Nachricht mit `id:` = `seq` und `data:` = vollständiger
    Event-Record (dieselben Felder wie im Log).
  - Eine unvollständige Schlusszeile wird **nicht** gesendet, sondern beim
    nächsten Poll erneut geprüft.
  - Eine kaputte Zeile / ein Record ohne gültige `seq` erzeugt **keine**
    Event-Nachricht und beendet den Stream nicht: übersprungen, weiter getailt,
    stattdessen eine SSE-Nachricht `event: problem` **ohne** `id:`-Feld (damit
    bleibt `Last-Event-ID` unberührt), deren `data:` die Problembeschreibung in
    derselben Form trägt wie die Reader-Probleme im Run-Detail-JSON (AC 15 live).
  - **Erstverbindung** (kein `Last-Event-ID`): Stream beginnt **am Dateianfang**
    und liefert alle akzeptierten Events. **Reconnect** (`Last-Event-ID`
    vorhanden): fortsetzen **nach** dieser `seq` — keine Dopplung, keine Lücke.
  - Nach dem `run`-End-Event wird der Stream serverseitig geschlossen; zuvor
    werden alle bis dahin vollständigen Events ausgeliefert. Bei einem bereits
    fertigen Run werden alle vollständigen Records bis einschließlich des
    End-Events geliefert, dann wird geschlossen.
- **B9. HTML-Run-Detail & Detail-Pane (AC 12–17, §7.2 B).** `GET /runs/{repo}/
  {run_id}` rendert auf demselben Snapshot-/Serialisierungspfad wie die
  Detail-API den Kopf (Phasen-Statusleiste), links den aufklappbaren,
  chronologischen Trace-Baum (je Knoten Icon/Status, Label, Dauer; bei
  Loop-Knoten `round` zusätzlich `n/cap`) und rechts das knotenabhängige
  Detail-Pane:
  - `agent.run`: genau die Reiter **Prompt** (vollständiger Task-String inkl.
    System-Append), **Answer** (finaler Text plus Zwischen-Assistant-Messages)
    und **Tools** (chronologische Tool-Call-Liste, je Eintrag mit vollem Input
    und vollem Result). **Kein** Diff-Reiter (Lauf 5).
  - `gate`: Kommando, Exit-Code, voller Output.
  - `codex.review`: Findings als Tabelle (mindestens Severity, Key, Datei,
    Message) plus rohes `stdout`.
  - `phase`/`lane`/`round`: Aggregation der Kinder (mindestens Dauer, Kosten
    soweit vorhanden, Outcome).
  - Vom Reader gemeldete Probleme werden dem Nutzer **sichtbar** gemacht (im
    HTML und im JSON). Unbekannte Event-Typen generisch (Typ-Label + rohes
    Payload), nie verworfen.
  - Ein später geöffneter, bereits abgeschlossener Run rendert identisch zu
    einem live beobachteten — Snapshot- und Live-Pfad nutzen denselben
    fachlichen Rendering-Pfad (AC 20).
- **B10. Client (Vanilla JS, AC 17, §7.3).** Ausschließlich `fetch` + natives
  `EventSource`; neue SSE-Events werden inkrementell in die bestehende Ansicht
  übernommen, **ohne** Reload (Baum, Phasen, Pane-relevante Daten und Probleme
  über denselben fachlichen Rendering-Pfad). Der Client merged Stream-Records
  über die ganzzahlige `seq` und ignoriert Records, die sein initialer Snapshot
  schon abdeckt (keine Dopplung, keine Lücke — auch für ein zwischen
  Snapshot-Abruf und Stream-Start angehängtes Event). `event: problem`-
  Nachrichten werden ohne Reload in die bestehende Problem-Anzeige übernommen.
  Ausgelieferte Seiten und Assets referenzieren **keine** externe Ressource.

Tests B (gegen Fixture-Logs mit FastAPIs `TestClient`; Byte-Anhängen für das
Tailing):
- **Verpackung:** `adw run` importiert/läuft ohne das `gui`-Extra (kein
  Import-Fehler); `adw gui` ohne Extra endet mit Installationsanweisung statt
  Traceback (DoD 5).
- **CLI/Sicherheit:** Nicht-Loopback-`--host` ohne `--i-know` → Ablehnung ohne
  Bind; mit `--i-know` zugelassen; Browser nur mit `--open` (AC 4/5).
- **Path-Traversal (DoD 4):** unbekannter Slug → `404`; ungültige `run_id` →
  `400`; nicht vorhandene `run_id` → `404`; kein Zugriff außerhalb
  `.adw/runs/<run_id>/`.
- **Run-Liste:** Felder je Run inkl. `event_count`; Laufend-zuerst-Reihenfolge;
  Repo-Platzhalter (`repo_exists: false`, ohne Run-Felder) für ein fehlendes
  Repo, HTML ausgegraut, kein Fehler; HTML-Liste deckt sich mit der
  JSON-Datenbasis; fehlende Kosten → `null` (AC 9–11).
- **Run-Detail:** Phasen-Kopf; Trace-Baum unverändert aus dem Modell;
  Detail-Pane je Knotentyp (`agent.run` mit genau Prompt/Answer/Tools und ohne
  Diff-Reiter, `gate`, `codex.review`, `phase`/`lane`/`round`); sichtbare
  Reader-Probleme; generische Darstellung unbekannter Typen; keine externen
  Referenzen in Seiten/Assets (AC 12–17).
- **`/events`:** Records ab `seq >= N` in Dateireihenfolge; unbekannte Typen
  nicht gefiltert; default von Beginn; ungültiger `from_seq` → kontrollierter
  Client-Fehler.
- **SSE (DoD 3):** neue Zeilen als `id:`/`data:`; unvollständige Schlusszeile
  wird nicht gesendet, nach Vervollständigung genau einmal; Erstverbindung ab
  Dateianfang — ein zwischen Snapshot-Abruf und Stream-Start angehängtes Event
  erscheint (mit Client-`seq`-Merge) genau einmal; kaputte Zeile nach
  Verbindungsaufbau → `event: problem` ohne `id:`, ohne Stream-Abbruch, spätere
  gültige Events folgen; `Last-Event-ID`-Reconnect setzt strikt nach der `seq`
  fort; Schließen nach `run`-End; abgeschlossener Run rendert identisch zu live
  beobachtetem Bestand (AC 18–20).
- **Read-only-Nachweis:** ein Route-Durchlauf verändert keine Datei des Runs;
  keine schreibende Route existiert (AC 6).

## Guardrail Testumfang

Richtwert rund **20–28** neue Tests für A und B zusammen. Maßgeblich ist die
Abdeckung der Akzeptanzkriterien, nicht die exakte Anzahl. Deutlich mehr ist ein
Signal für Scope-Drift.

## Definition of Done

1. Aufgabe A und B sind gebaut; die Akzeptanzkriterien und die im Kontrakt
   festgelegte öffentliche Fläche (CLI, Routen, Antwortformate, SSE-Format) sind
   erfüllt.
2. Ein vollständiger Testlauf lässt das reale `~/.adw/repos.json` nachweislich
   unverändert (Regressionstest); die Isolation greift für alle Tests, die
   `adw run`/Auto-Registrierung auslösen.
3. Die Web-App ist gegen Fixture-Logs mit `TestClient` getestet: Run-Liste
   (inkl. Repo-Platzhalter und Laufend-zuerst-Reihenfolge), Run-Detail mit
   Phasen-Kopf, Trace-Baum und Detail-Pane je Knotentyp, sichtbare
   Reader-Probleme, generische Darstellung unbekannter Typen sowie der
   SSE-Stream (neue Zeilen, unvollständige Schlusszeile, Erstverbindung ab
   Dateianfang mit Genau-einmal-Erscheinen des Zwischen-Events, `event: problem`
   ohne Abbruch/Reload, `Last-Event-ID`-Reconnect, Schließen nach `run`-End).
4. Der Path-Traversal-Schutz ist getestet: unbekannter Slug → `404`, ungültige
   `run_id` → `400`, nicht vorhandene `run_id` → `404`; kein Zugriff außerhalb
   `.adw/runs/<run_id>/`.
5. Ein Test belegt, dass `adw run` ohne das `gui`-Extra unverändert importiert
   und läuft; `adw gui` ohne Extra endet mit der Installationsanweisung.
6. `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py` und
   `adw/gui/model.py` sind unverändert; an `adw/gui/registry.py` ist
   ausschließlich die isolierbare Pfad-Quelle aus Aufgabe A geändert; außer der
   Home-Isolation aus Aufgabe A ist der Orchestrator (`phases.py`, Run-Pfad in
   `cli.py`) nicht verändert. Reicht eine eingefrorene API nicht aus, ist das
   als Befund dokumentiert.
7. Gates grün: `uv run ruff check .` und `uv run pytest -x -q` (`flake8`,
   `isort`, `black` tauchen nirgends auf — E3).

## Deferred (bewusst nicht gebaut)

Weitergehende Härtungs- oder Erweiterungsideen — auch Befunde aus den
Codex-Review-Runden — gehören hierher, nicht in die Akzeptanzkriterien. Ein
Finding, das einen dieser Punkte oder einen vorentschiedenen Punkt (E1–E7)
einführen will, wird abgewiesen und mit Begründung dokumentiert.

- Timeline, Artefakte-Reiter, Raw-Reiter, Diff-Reiter, Diff-/Artefakt-Endpoint
  (`/diff`, `/artifacts/{name}`) samt `git diff`-Ausführung — Lauf 5.
- i18n/Sprachumschaltung, `--lang`, Einsatz von `adw/gui/i18n.py` — Lauf 5.
- Prunen, Retention, gzip-Reader-Unterstützung, `trace:`-Config-Key,
  `adw runs list`/`prune` — Lauf 5.
- `type`-Filter und Pagination auf `/events` (§7.4 volle Form) — in diesem Lauf
  nur `from_seq`.
- Sortier-/Filter-Bedienelemente und Live-Update der Run-**Liste** (§7.2 A
  "sortable, filter, live-updating") — über „laufende zuerst" hinaus nichts.
- Jegliche Schreib-/Steuerfunktion in der GUI (approve/resume/abort/start);
  Redaction/Maskierung von Secrets im Log (GUI-SPEC §2/§8).
- Authentifizierung, TLS, Mehrbenutzerbetrieb, Remote-Zugriff über die
  Loopback-Bindung hinaus (nur `--i-know` schaltet Nicht-Loopback frei).
- Lazy-Rendering großer Logs (> 200 MB, §9), LRU-/Cross-Prozess-Caching,
  Dateisystem-Watcher statt Polling, persistente Indizes/Tail-Cursor/Caches.
- Neue Schutzmechanismen für Risiken, die vorhandene Backstops
  (Registry-Auflösung, `RUN_ID_RE`, Loopback-Default, Vollzeilen-Lesen,
  sichtbare Reader-Probleme) bereits abdecken.
- Änderungen an `events.py`, `snapshots.py`, `reader.py`, `model.py` oder eine
  Cross-Thread-Parent-API im Emitter (E2) — die Datenschicht wird nur
  konsumiert; eine tatsächlich fehlende Fähigkeit wird als Befund dokumentiert.
