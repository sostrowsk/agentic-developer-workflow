# Spec — R3: Die Live-Ansicht bleibt live — und die GUI hält stand

Basis: gemergter Stand 0.36.0 (nach W2). Befunde C5, C13–C18 aus
`ONBOARDING-SPEC-PLAN.md` §3. Die Ausgangslage des Issues ist am 2026-11-08 im Code
verifiziert und vor dem Bauen gegen den dann aktuellen Stand zu prüfen; Abweichungen
werden gemeldet, nicht stillschweigend umgedeutet. Die Vorentscheidungen E1–E8 des
Issues sind bindend und kein Finding, auch nicht im Review-Loop.

## Ziel

Die Live-Ansicht eines Laufs bleibt über alle Run-Spannen hinweg (Spec-/Plan-Gate,
`approve`, `resume`) verbunden und endet genau einmal mit einem terminalen Event.
Untätige Streams belegen keinen Worker-Thread und enden bei Client-Abbruch; regulär
beendete Streams lösen keine Reconnect-Schleife aus, fertige Läufe werden nicht erneut
abgespielt. Die GUI weist fremde Hosts ab, verliert bei parallelen Registrierungen
keine Einträge, lässt sich von falsch typisierten Event-Feldern nicht in einen 500
reißen und behält in der Tools-Navigation alle Filter.

## Scope

### Live-Stream über Run-Spannen — A1 / C13 (E1)

- Ein `run`-Ende beendet den Stream nur, wenn seine Span-ID zur **letzten bisher
  gestarteten** Run-Spanne des Logs gehört (Abgleich wie `_run_span`) **und** der
  Run-State danach nicht `awaiting_approval` ist. Maßgeblich sind das vorhandene Log
  und der vorhandene Run-State; es entsteht kein zusätzlicher persistenter
  Abschlusszustand.
- Der Wiederaufnahmepunkt (`Last-Event-ID` bzw. `last_id`) begrenzt nur die
  **Ausgabe**, nicht die Ermittlung der maßgeblichen Spanne. Daraus folgt beides:
  Historische End-Records **früherer** Spannen schließen den Stream nicht, auch wenn
  sie oberhalb des Wiederaufnahmepunkts liegen. Das Ende der **letzten gestarteten**
  Spanne schließt den Stream nach E1 unabhängig davon, ob es bereits unterhalb des
  Wiederaufnahmepunkts liegt: Ein Reconnect nach dem letzten `run`-Ende, aber vor
  Empfang von `closed`, erhält keine erneute Event-Ausgabe, sondern sofort `closed`
  (sofern der Lauf nicht `awaiting_approval` ist). Sonst würde die Verbindung
  dauerhaft weiterpollen.
- Bei `awaiting_approval` bleibt die Verbindung offen; Events, die ein späteres
  `approve`/`resume` in dasselbe Log schreibt, erscheinen über dieselbe Verbindung.
- Der Client (`adw/gui/static/app.js`) schließt die `EventSource` **nicht** mehr beim
  `run`-Ende, sondern nur beim terminalen Event `closed` (E3). Das Rendering des
  fertigen Stands bleibt unverändert.

### Kein Thread-Verbrauch — A2 / C14 (E2)

- `_stream` ist ein async Generator mit `await asyncio.sleep(...)` statt `time.sleep`;
  das Lesen der Datei bleibt synchron.
- Jeder leere Poll liefert den SSE-Kommentar `": ping\n\n"`, damit ein Client-Abbruch
  bemerkt wird. Ein Client-Abbruch beendet den Generator und dessen Polling.
- Keine neue Dependency.

### Terminales Event, kein Replay — A3 / C16 (E3)

- Jedes reguläre Stream-Ende (Ende der letzten Spanne nach E1, Lauf ohne Trace,
  `.gz`-Log) sendet zuletzt genau einmal `event: closed\ndata: {}\n\n`. Ein `.gz`-Log
  liefert seine ausstehenden Events einmal und endet dann auch ohne End-Record mit
  `closed`.
- Der Client schließt bei `closed` die `EventSource`.
- Die Seite eines Laufs, der beim Rendern bereits `done` oder `escalated` ist, öffnet
  **keine** `EventSource`. Der Client liest das aus einem Datenattribut am
  Seitenelement.
- Für laufende Läufe verbindet der Client mit der zuletzt gerenderten Seq als
  Startpunkt über den vorhandenen Mechanismus (`last_id` am Stream bzw.
  `Last-Event-ID`); bereits gerenderte Events werden nicht erneut ausgegeben. Keine
  neue Semantik.

### Host-Allowlist — A4 / C5 (E4)

- `create_app` nutzt Starlettes `TrustedHostMiddleware` mit
  `["127.0.0.1", "localhost", "::1", "[::1]"]`. Ein Port im `Host`-Header verhindert
  die Zulassung nicht (`127.0.0.1:<port>`).
- Bei `--i-know` und einem anderen `--host` wird genau dieser Host zusätzlich erlaubt;
  es werden nicht pauschal beliebige Hosts zugelassen.
- Fremder Host → HTTP 400, bevor GUI- oder API-Inhalte ausgeliefert werden.
- Loopback-Bindung und Bedeutung von `--i-know` in `adw/cli.py` bleiben; die CLI reicht
  den Host nur an die Allowlist weiter.

### Registry unter Interprozess-Lock — A5 / C15 (E5)

- `register_repo` hält ein `fcntl.flock` auf einer Lock-Datei neben der Registry über
  Laden, Ändern und atomares Ersetzen. Der Schutz gilt über Prozessgrenzen hinweg.
- Registry-Format, kanonische Pfade, Slugs und Aktualisierungsregeln bleiben gleich.

### Typprüfung an einer Stelle — A6 / C17 (E6)

- Eine Funktion im Reader-Pfad (`adw/gui/reader.py`) prüft vor den Projektionen:

  | Feld | zulässiger Typ eines vorhandenen Werts |
  |---|---|
  | `type`, `kind`, `span`, `parent`, `ts` | String |
  | `payload` | Dict |
  | `payload.status` (bei Dict-Payload) | String |

- Falsch typisierte Werte werden als **fehlend** behandelt (auf `None` gesetzt); der
  Record bleibt mit seinen übrigen Informationen nutzbar. Es wird ein `problem`
  gemeldet, wie der Reader es für kaputte Zeilen schon tut. Kein erfundener
  Ersatzwert, keine Änderung der Quelldatei.
- Keine verstreuten Prüfungen in Projektionen (`runs.py`, `serialize.py`, `model.py`)
  oder Templates.

### Navigation behält Query-Parameter — A7 / C18 (E7)

- Die Tools-Prev/Next-Links entstehen aus dem aktuellen Query-String mit ersetztem
  `tools_offset` (wie `switch_qs` für andere Links). `limit`, `raw_q`, `raw_type`,
  `raw_from_seq`, `raw_to_seq` und der bestehende `focus` bleiben erhalten.

### Dokumentation — A8

- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron, wo das Verhalten beschrieben
  ist: Stream-Ende (Spannen-Regel, `closed`, Ping, kein Stream für fertige Läufe,
  Startpunkt des Clients) und Host-Prüfung.
- `CHANGELOG.md` / `CHANGELOG.de.md` synchron: neue Sektion
  `## [0.36.1] — Unreleased` mit `### Fixed`.
- Version in `pyproject.toml` **nicht** ändern.

## Non-Goals

- Gestaltung, Inhalt, Reihenfolge und Tastaturbedienung der Seiten.
- Regionentausch, Entprellung (200 ms), Nachladen der Panes (Release 0.26.0).
- Felder und Werte der `/api`-Routen (einzige neue Fläche: SSE `event: closed` und
  Ping-Kommentar).
- Run-, Gate-, Approval- und Resume-Semantik.
- Loopback-Bindung und `--i-know` in `adw/cli.py`.
- Neue persistente Stream-Verwaltung, Cursor auf Disk oder zusätzlicher Run-State. Die
  Lock-Datei der Registry ist die einzige, vom Issue vorgegebene neue Datei.
- Neue Laufzeit-Dependency, CDN, Frontend-Paket.
- Versionsänderung in `pyproject.toml`.

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

## Akzeptanzkriterien

1. **Mehrere Spannen (C13, E1):** Log mit Spanne A (Start/Ende) und Spanne B (Start,
   Phase-Start, weitere Events): der Stream liefert alle Events beider Spannen in
   Log-Reihenfolge und bleibt verbunden; nach dem Ende von B (Lauf `done`) folgt als
   letztes Event `closed`.
2. **Reconnect (C13, E1/E3):** Reconnect mit `Last-Event-ID` hinter dem Ende von A:
   bereits bestätigte Events werden nicht erneut ausgegeben, der Stream liefert B und
   endet **nicht** sofort. Reconnect mit `Last-Event-ID` auf oder hinter dem Ende von
   B (Lauf `done`): keine erneute Event-Ausgabe, aber sofort `closed`.
3. **Approval-Gate (C13, E1):** Lauf in `awaiting_approval` nach dem Ende von A: kein
   `closed`, der Stream bleibt offen; danach ins Log geschriebene Events kommen über
   dieselbe Verbindung.
4. **Untätigkeit und Abbruch (C14, E2):** Leeres Log ohne Ende: der Stream liefert
   `: ping`-Kommentare, ohne während der Wartezeit einen Worker-Thread zu belegen; ein
   Client-Abbruch beendet den Generator.
5. **Endliche Quellen (C16, E3):** Lauf ohne Trace und `.gz`-Log ohne End-Record enden
   mit `event: closed\ndata: {}\n\n`. Im JS-Harness öffnet der Client für einen
   fertigen Lauf (`done`/`escalated`) keine `EventSource` und schließt sie bei
   `closed`, nicht beim `run`-Ende.
6. **Host-Prüfung (C5, E4):** Anfrage mit `Host: evil.example` → 400; mit
   `127.0.0.1:<port>` und `localhost` → 200. Ein über `--i-know`/`--host` zusätzlich
   erlaubter Host → 200, andere fremde Hosts bleiben 400.
7. **Parallele Registrierung (C15, E5):** Zwei gleichzeitige `register_repo`-Aufrufe
   für verschiedene Pfade (Threads oder Prozesse): beide Einträge stehen danach in der
   Registry, vorhandene Einträge bleiben erhalten.
8. **Falsch typisierte Records (C17, E6):** Record mit `payload.status` als Liste bzw.
   `ts` als Zahl: `/` und `/api/runs` antworten 200, der betroffene Lauf zeigt ein
   Problem, die übrigen Läufe sind unverändert.
9. **Query-Erhalt (C18, E7):** Tools-Prev/Next-Link bei
   `?limit=400&raw_q=foo&tools_offset=200`: der Link behält `limit=400` und
   `raw_q=foo` und ersetzt nur `tools_offset`.
10. **Toolchain (E8):** `uv run ruff check .` und `uv run pytest -x -q` grün.

Richtwert: 10–14 neue Tests. Deutlich mehr ist ein Zeichen für Gold-Plating.

## Contract (Single-Lane, nur extern beobachtbare Flächen)

- SSE-Stream `GET /api/runs/{repo}/{run_id}/stream`: Event-Records wie bisher;
  Wiederaufnahme über `Last-Event-ID` / `last_id` wie bisher; Ping als `": ping\n\n"`;
  reguläres Ende als `event: closed\ndata: {}\n\n`.
- Fremder `Host` → HTTP 400.
- `/api`-Antworten in Feldern und Werten unverändert.
- Keine internen Helper-Signaturen.

## Definition of Done

- Akzeptanzkriterien 1–10 erfüllt und durch Tests belegt (pytest; Client-Verhalten im
  vorhandenen JS-Harness `tests/gui_js_harness.js` / `.py`).
- Vorentscheidungen E1–E8 umgesetzt; kein Deferred-Punkt gebaut.
- `docs/GUI-SPEC.md`/`.de.md` und `CHANGELOG.md`/`.de.md` synchron aktualisiert;
  `pyproject.toml` unverändert.
- Keine neue Laufzeit-Dependency, kein CDN, kein Frontend-Paket.
