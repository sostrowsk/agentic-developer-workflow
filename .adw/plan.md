# Plan — R3: Die Live-Ansicht bleibt live — und die GUI hält stand

Maßgeblich ist `.adw/spec.md`; die Vorentscheidungen E1–E8 sind bindend und kein
Finding. Single-Lane: Workstream **backend** umfasst auch `adw/gui/static/app.js`,
die Templates und die Doku (keine eigene Frontend-Lane). Der Contract
`.adw/contract.yaml` pinnt nur extern beobachtbare Flächen (SSE-Stream, Host-Prüfung,
Datenattribute/Links im Template, `/api` unverändert), keine Helper-Signaturen.

## Ausgangslage (am 2026-11-08 im Code geprüft) und Abweichungen

Vor dem Bauen gegen den aktuellen Stand prüfen; Abweichungen melden, nicht umdeuten.

- `adw/gui/app.py:151` `_stream`: synchroner Generator, `time.sleep`, endet beim ersten
  `run`-Ende (auch unterhalb des Cursors, `:175`), kein `closed`, kein Ping, kein
  Abgleich mit Run-State oder letzter gestarteter Spanne. `.gz` wird bereits einmalig
  gelesen (`:181`).
- **Abweichung zur Issue-Formulierung „vorhandener Mechanismus":** Die Route
  `api_run_stream` (`app.py:313–320`) liest nur den Header `Last-Event-ID`
  (`_parse_last_event_id`); ein Query-Parameter `last_id` existiert nicht, und der Client
  öffnet `streamUrl` ohne Startpunkt (`app.js:35`, `:1334`). `last_id` muss ergänzt
  werden — mit derselben Seq-Semantik und demselben Parser wie der Header.
- `adw/gui/runs.py:114` `_run_span` ordnet das Ende bereits der letzten gestarteten
  Span-ID zu. `runs.py:289–291` behandelt die State-Phasen `awaiting_approval` **und**
  `awaiting_spec_approval` als Warten auf Approval; diese Abbildung ist für E1
  wiederzuverwenden.
- `app.js:1334–1348`: `EventSource` immer; `source.close()` beim `run`-Ende; der
  finale Refresh (`setTimeout(refresh, 250)`) wird dort ausgelöst.
- `run_detail.html:120` `window_nav` baut `?param=N[&focus=]` neu; `<body>` trägt
  bereits `data-repo`/`data-run-id` (`app.js:14–17`), aber weder Status noch Start-Seq.
- `create_app` (`app.py:210`) ohne Host-Middleware; `adw/cli.py:559–569` prüft
  Bind-Adresse und `--i-know`.
- `adw/gui/registry.py:167` `register_repo`: atomarer Ersatz vorhanden, Read-Modify-Write
  ungeschützt.
- `adw/gui/reader.py`: erkennt kaputte Zeilen und Seq-Lücken, prüft die E6-Feldtypen nicht.
- Alle bestehenden HTTP-Tests nutzen `TestClient(create_app(...))` ohne `base_url`,
  d. h. Host `testserver` — nach E4 würde das 400 liefern (siehe B5).

## Workstream: backend

Reihenfolge: B7 (Reader) ist unabhängig und kommt zuerst; B1→B2→B3 vor B4 (Client
braucht `last_id` und `closed`); B5, B6, B8 danach; B9 nach dem endgültigen Verhalten.

### B1 — Stream über Run-Spannen (A1/C13, E1) — `adw/gui/app.py`

- Die maßgebliche Spanne aus **allen bisher gelesenen** Records bestimmen (letzte
  gestartete Run-Spanne, Abgleich wie `_run_span`: Ende gehört zur zuletzt gestarteten
  Span-ID und liegt nach deren Start), unabhängig vom Ausgabe-Cursor.
- Einen vollständig gelesenen Batch auswerten, bevor ein Ende als Abschluss gilt: ein
  späterer Start im selben Batch macht das Ende historisch. Enden früherer Spannen
  beenden den Stream nie.
- Vor der Abschlussentscheidung den vorhandenen Run-State laden; die Approval-Phasen
  (`awaiting_approval`, `awaiting_spec_approval`) nach der vorhandenen Abbildung aus
  `runs.py` behandeln. Bei Approval-Wartezustand offen bleiben und weiterpollen;
  spätere `approve`/`resume`-Events aus demselben Log laufen über dieselbe Verbindung.
- Der Cursor (`Last-Event-ID`/`last_id`) begrenzt nur die Ausgabe (`seq > Cursor`).
  Liegt das Ende der letzten Spanne auf oder unter dem Cursor und wartet der Lauf nicht
  auf Approval → sofort `closed`, ohne erneute Record-Ausgabe.
- Kein neuer persistenter Abschlusszustand; Log und Run-State sind die einzige Quelle.
- `last_id` als Query-Parameter an denselben Parser und dieselbe Filterung wie
  `Last-Event-ID` anschließen. Ein gültiger Header hat Vorrang (der Browser-Reconnect
  trägt den fortgeschrittenen Cursor im Header, die URL den alten `last_id`).

### B2 — Async-Generator, Ping, Abbruch (A2/C14, E2) — `adw/gui/app.py`

- `_stream` → `async def`, `await asyncio.sleep(_POLL_SECONDS)` statt `time.sleep`;
  Dateilesen über den bestehenden `EventReader` bleibt synchron.
- Jeder leere Poll einer weiterhin offenen Verbindung liefert `": ping\n\n"`.
- Client-Abbruch (Cancellation/`GeneratorExit` der `StreamingResponse`) beendet
  Generator und Polling; Cancellation nicht unterdrücken, kein terminales Event an einen
  abgebrochenen Client erzwingen. Keine neue Dependency, keine weitere Route auf async.

### B3 — Terminales `closed` (A3/C16, E3) — `adw/gui/app.py`

- Jedes reguläre Ende sendet zuletzt genau einmal `event: closed\ndata: {}\n\n`:
  Ende der letzten Spanne nach B1, Lauf ohne Trace (`events_path is None`), `.gz`-Log
  nach einmaliger Ausgabe der ausstehenden Events auch ohne End-Record.
- Bestehende Frames (`id:`/`data:`-Records, `event: problem`), Pfadprüfung und
  Fehlerantworten (404) bleiben.

### B4 — Client und gerenderter Startpunkt (A1/A3, E3) — `run_detail.html`, `app.js`, `app.py`

- `<body>` erhält `data-run-status` (gerenderter Run-Status) und `data-last-seq`
  (höchste gültige Seq des für den Snapshot geladenen Logs, nicht aus der
  gefilterten/paginierten Raw-/Tools-Ansicht; `0` ohne gültige Seq). Keine neuen
  JSON-API-Felder.
- `app.js` (~Z. 1334): bei `done`/`escalated` keine `EventSource`; übrige
  Initialisierung und Bedienung laufen weiter. Sonst Verbindung mit
  `?last_id=<data-last-seq>`.
- Beim `run`-Ende den finalen Refresh behalten, `source.close()` entfernen.
  Listener für `closed` schließt die `EventSource`. Regionentausch, 200-ms-Entprellung,
  Lazy-Panes unverändert.

### B5 — Host-Allowlist (A4/C5, E4) — `adw/gui/app.py`, `adw/cli.py`

- `create_app` nimmt intern einen optionalen zusätzlichen Host entgegen und fügt
  `TrustedHostMiddleware` mit `["127.0.0.1", "localhost", "::1", "[::1]"]` plus ggf.
  genau diesem Host hinzu. Port im `Host`-Header ist zulässig; fremder Host → 400, bevor
  GUI-/API-Inhalte ausgeliefert werden. Keine Wildcard.
- `adw/cli.py`: bei `--i-know` und anderem `--host` diesen Host durchreichen;
  Bind-Prüfung, Bedeutung von `--i-know` und Lazy-Imports unverändert.
- Bestehende HTTP-Tests auf eine erlaubte Basisadresse umstellen (z. B.
  `TestClient(..., base_url="http://127.0.0.1")`, zentral in `tests/gui_app_helpers.py`
  und den direkten `TestClient(create_app(...))`-Stellen). `testserver` **nicht** in die
  Produktions-Allowlist aufnehmen.

### B6 — Registry-Lock (A5/C15, E5) — `adw/gui/registry.py`

- `register_repo` (Z. 167): stabile Lock-Datei neben der tatsächlich verwendeten
  Registry; `fcntl.flock(LOCK_EX)` über Laden → Ändern → atomares Ersetzen; Lock auch
  im Fehlerfall freigeben. Registry-Verzeichnis bei Bedarf vor dem Öffnen anlegen; die
  Lock-Datei nicht entfernen (gleiches Lock-Ziel für parallele Prozesse).
- Format, kanonische Pfade, Slugs, Zeitstempel und Fehlerbehandlung unverändert; keine
  anderen Registry-Schreiber sperren.

### B7 — Typprüfung an einer Stelle (A6/C17, E6) — `adw/gui/reader.py`

- Eine Funktion nach dem JSON-/dict-Check und vor den Projektionen: `type`, `kind`,
  `span`, `parent`, `ts` → String; `payload` → dict; `payload.status` (bei
  dict-Payload) → String. Fehlende/`None`-Werte bleiben fehlend.
- Falsch typisiert → `None`, Record bleibt sonst nutzbar, `problem` über den bestehenden
  Kanal mit Zeilenposition. Kein Ersatzwert, Quelldatei unverändert, keine Prüfungen in
  `runs.py`/`serialize.py`/`model.py`/Templates.

### B8 — Tools-Navigation (A7/C18, E7) — `run_detail.html`, ggf. `app.py`

- Prev/Next von `window_nav(tool_window, "tools_offset", …)` aus dem aktuellen
  Query-String mit ersetztem `tools_offset` bauen (wie `switch_qs`). `limit`, `raw_q`,
  `raw_type`, `raw_from_seq`, `raw_to_seq` und ein vorhandener `focus` bleiben; ein
  vorhandener `focus` wird nicht durch `node.seq` überschrieben, die bisherige
  Fokus-Ergänzung bei fehlendem `focus` darf bleiben. Offset-Rechnung und Fenstergröße
  unverändert.

### B9 — Dokumentation (A8)

- `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` synchron: Spannen-Regel,
  Approval-Wartezustand, Cursor-unabhängige Abschlussprüfung, `closed`, Ping und
  Abbruch, kein Stream für fertige Seiten, Client-Startpunkt (`last_id`), Host-Allowlist.
  Aussagen zum Schließen bei jedem `run`-Ende ersetzen.
- `CHANGELOG.md` + `CHANGELOG.de.md`: oben `## [0.36.1] — Unreleased` mit `### Fixed`.
- `pyproject.toml` **nicht** ändern.

## Tests (Richtwert 10–14; bestehende Tests anpassen statt doppeln)

| Nr. | Nachweis | Ort |
|---|---|---|
| 1 | AK1: A (Start/Ende) + B (Start, Phase, Events): alle Events beider Spannen in Reihenfolge, offen; B-Ende bei `done` → genau ein `closed` zuletzt. | `tests/test_gui_app_sse.py` |
| 2 | AK2a: Cursor hinter A-Ende: keine Wiederholung, B kommt, kein sofortiges Ende; Header und `last_id` filtern gleich. | SSE-Tests |
| 3 | AK2b: Cursor auf/hinter B-Ende bei `done`: keine Records, sofort ein `closed`; gültiger Header übersteuert älteren `last_id`. | SSE-Tests |
| 4 | AK3: `awaiting_approval` nach A-Ende: kein `closed`; später angehängte Events kommen über dieselbe Verbindung. | SSE-Tests |
| 5 | AK4: leeres offenes Log: `: ping`; Warten asynchron (kein Thread); simulierter Abbruch beendet Generator und Polling. | SSE-Tests, bestehende Async-/ASGI-Technik |
| 6 | AK5a/b: Lauf ohne Trace → `closed`; `.gz` ohne End-Record → Events einmal + `closed`. | Archivtest anpassen, No-Trace-Fall ergänzen |
| 7 | AK5c: JS-Harness: `done`/`escalated` → keine `EventSource`; sonst URL mit `last_id` = `data-last-seq`. | `tests/gui_js_harness.js`/`.py` |
| 8 | AK5d: JS-Harness: `run`-Ende → Refresh, aber kein Close; `closed` → Close. | JS-Harness |
| 9 | AK6a: `Host: evil.example` → 400 (GUI und `/api`); `127.0.0.1:<port>`, `localhost` → 200. | GUI-HTTP-Tests |
| 10 | AK6b: `--i-know --host X`: X → 200, anderer fremder Host → 400. | `tests/test_gui_cli.py` |
| 11 | AK7: zwei parallele `register_repo` (Threads oder Prozesse) verschiedener Pfade: beide + Altbestand vorhanden. | `tests/test_gui_registry.py` |
| 12 | AK8: `payload.status` Liste / `ts` Zahl: Reader liefert `None` + `problem`; `/` und `/api/runs` → 200, Problem am Lauf, übrige unverändert. | `tests/test_gui_reader.py`, Runlist-Tests |
| 13 | AK9: `?limit=400&raw_q=foo&tools_offset=200`: Prev/Next behalten `limit`, `raw_q` (und vorhandenen `focus`), ersetzen nur `tools_offset`. | `tests/test_gui_app_detail.py` |

Für offene Streams koordinierte Append-/Disconnect-Signale und begrenzte Laufzeit
nutzen; keine ungepufferten GETs auf dauerhaft offene Quellen. Bestehende SSE-Tests auf
async, Ping-Kommentare und das terminale Frame anpassen.

## Gates und Definition of Done

- AK1–9 durch die Tests oben belegt; AK10: `uv run ruff check .` und
  `uv run pytest -x -q` grün (in Build-Sandboxes ggf. `.venv/bin/python -m pytest`).
- E1–E8 umgesetzt; `/api`-Felder und -Werte, Rendering, Bedienung,
  Run-/Gate-/Approval-/Resume-Semantik, Loopback/`--i-know` unverändert.
- GUI-Specs und Changelogs synchron; `pyproject.toml` unverändert.
- Keine neue Laufzeit-Dependency, kein CDN, kein Frontend-Paket; kein Deferred-Punkt gebaut.

## Non-Goals

Wie Spec: Gestaltung/Tastatur, Regionentausch/Entprellung/Panes, `/api`-Felder,
Run-/Gate-/Approval-/Resume-Semantik, Loopback/`--i-know`; keine persistente
Stream-Verwaltung (einzige neue Datei: Registry-Lock); keine Dependency/CDN/Frontend-
Paket; keine Versionsänderung.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Authentifizierung/Token für die GUI.
- Live-Aktualisierung der Run-Liste.
- Umstellung weiterer Routen auf async.
- Weitere Härtungen über C5 und C13–C18 hinaus, insbesondere:
  - Watchdogs, Inaktivitätsfristen oder serverseitige Reconnect-Verwaltung für den
    Stream. Ein offener Lauf ohne End-Record darf bei bestehender Verbindung weiter auf
    Events warten; asynchrones Polling plus Abbrucherkennung beheben C14.
  - Lock für andere Registry-Schreiber als `register_repo`.
  - Allgemeine Event-Schema-Validierung über die in E6 genannten Felder hinaus.
  - Reconnect-Backoff im Client.
