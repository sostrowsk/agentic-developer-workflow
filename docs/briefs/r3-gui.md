# R3: Die Live-Ansicht bleibt live — und die GUI hält stand

Lauf R3 aus `ONBOARDING-SPEC-PLAN.md` (Abschnitt 3, Befunde C5, C13–C18). Setzt auf
dem gemergten Stand **0.36.0** (nach W2) auf. Die Ausgangslage ist am 2026-11-08 im Code
geprüft und vor dem Bauen gegen den dann aktuellen Stand zu prüfen.

## Ausgangslage (im Code verifiziert, 2026-11-08)

### C13 — Der Live-Stream endet bei der ersten Run-Spanne

`_stream` (`adw/gui/app.py:151–185`) gibt Events des Laufs per SSE aus und **beendet
den Stream beim ersten** Record mit `type == "run"` und `kind == "end"`
(`is_end`, `:165`, `:175`, `:179`) — auch wenn dieser bereits unter `Last-Event-ID`
liegt. Der Client schließt die Verbindung ebenfalls beim ersten `run`-Ende
(`adw/gui/static/app.js:1334–1348`, `source.close()`). Ein Lauf mit Spec- oder
Plan-Gate schreibt aber **mehrere** `run`-Spannen in dasselbe Log (`adw approve` /
`adw resume` öffnen je eine neue). Nach dem ersten Gate ist die Live-Ansicht damit
eingefroren: Die eigentliche Arbeit (Build, Reviews, CI) erscheint nicht live.
Reproduziert im Review: Log mit Spanne A (Start/Ende) + Spanne B (Start) + Phase-Start
→ `_stream` liefert nur Seq 1–2. `_run_span` in derselben Datei unterscheidet die
Spannen bereits über die Span-ID.

### C14 — Untätige Streams belegen Worker-Threads

`_stream` ist ein synchroner Generator, der bei leerem Log `time.sleep(_POLL_SECONDS)`
ausführt, **ohne** etwas zu liefern (`:185`). Starlette ruft jedes `next()` über
`anyio.to_thread.run_sync` auf, das nicht abbrechbar ist. Ein Lauf, der ohne
`run`-Ende abgebrochen wurde (SIGKILL), schreibt nie wieder — jeder Seitenaufruf
belegt dann dauerhaft einen Thread des Standard-Pools (40). Alle GUI-Routen sind
synchrone Handler: nach ~40 Aufrufen hängt jede Anfrage.

### C16 — Endlose Reconnects

Der Stream endet mit 200 OK ohne `run`-Ende in zwei Fällen: Lauf ohne Trace
(`events_path is None`, `:156`) und `.gz`-Log ohne End-Record (`:181`). `EventSource`
verbindet sich dann alle ~3 s neu, unbegrenzt; bei `.gz` wird jedes Mal das ganze
Archiv dekomprimiert. Außerdem verbindet sich **jede** Seite, auch eines fertigen
Laufs, ohne `Last-Event-ID` und bekommt das gesamte Log noch einmal zugespielt
(mit je einem Refresh danach).

### C5 — Keine Host-Prüfung

`create_app` (`adw/gui/app.py:210`) prüft den `Host`-Header nicht. Die Bindung an
Loopback (`adw/cli.py`, erzwungen außer mit `--i-know`) verhindert kein
DNS-Rebinding: eine fremde Seite, deren Hostname auf 127.0.0.1 zeigt, liest
`/api/runs` und die Events (ungeschwärzte Agent-Ausgaben, GUI-SPEC §8).

### C15 — Registry verliert Einträge

`register_repo` (`adw/gui/registry.py:~166`) lädt die Registry, ändert sie und
ersetzt die Datei atomar — aber ohne Lock über Lesen **und** Schreiben. Zwei
gleichzeitige Registrierungen verschiedener Repos: eine geht verloren (im Review
mit zwei Threads reproduziert).

### C17 — Falsch typisierte Felder führen zu 500

Der Reader garantiert nur, dass ein Record ein dict ist. Gültiges JSON mit falschem
Typ (`payload.status` als Liste, `ts` als Zahl, `type`/`span` als Liste) lässt
Projektionen in `adw/gui/runs.py`, `serialize.py`, `model.py` mit `TypeError` scheitern;
`run_list.html:31/51` verkettet `"status_" + e.status`. Folge: `/` und `/api/runs`
liefern 500 für **alle** Repos, entgegen der Regel „ein Lauf darf die übrigen nicht
mitreißen" (`_list_runs`).

### C18 — Tools-Navigation verliert Filter

`window_nav(tool_window, "tools_offset", node.seq)` (`run_detail.html:268`) baut die
Prev/Next-Links neu als `?tools_offset=N[&focus=]` und verliert `limit`, `raw_q`,
`raw_type`, `raw_from_seq`, `raw_to_seq`.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Gestaltung, Inhalt, Reihenfolge und Tastaturbedienung der Seiten bleiben.
- Regionentausch, Entprellung (200 ms), Nachladen der Panes (Release 0.26.0) bleiben.
- Die API-Routen unter `/api` bleiben in Feldern und Werten gleich (außer dem neuen
  terminalen SSE-Event aus E3).
- Loopback-Bindung und `--i-know` in `adw/cli.py` bleiben.

## Aufgabe

**A1 (C13)** Der Stream und der Client bleiben über Run-Spannen hinweg verbunden.
**A2 (C14)** Untätige Streams belegen keinen Thread und enden bei Client-Abbruch.
**A3 (C16)** Kein endloses Reconnect; fertige Läufe werden nicht erneut abgespielt.
**A4 (C5)** Host-Allowlist.
**A5 (C15)** Registry-Änderungen unter Interprozess-Lock.
**A6 (C17)** Typprüfung an einer Stelle.
**A7 (C18)** Navigation behält alle Query-Parameter.
**A8** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron, wo das Verhalten
beschrieben ist (Stream-Ende, Host-Prüfung). `CHANGELOG.md`/`CHANGELOG.de.md`
synchron: neue Sektion `## [0.36.1] — Unreleased` mit `### Fixed`. Version in
`pyproject.toml` **nicht** ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 (C13):** Ein `run`-Ende beendet den Stream nur, wenn es das Ende der
  **letzten bisher gestarteten** Run-Spanne ist (Abgleich über die Span-ID, wie
  `_run_span`) **und** der State des Laufs danach nicht `awaiting_approval` ist. Ein
  Lauf in `awaiting_approval` bleibt verbunden (ein späteres `approve` schreibt weiter
  in dasselbe Log). Der Client schließt **nicht** mehr selbst beim `run`-Ende, sondern
  nur beim terminalen Event aus E3; das Rendering des fertigen Stands bleibt.
- **E2 (C14):** `_stream` wird ein **async** Generator mit `await asyncio.sleep(...)`
  statt `time.sleep`; Lesen der Datei bleibt synchron (klein, schnell). Bei jedem
  leeren Poll wird ein SSE-Kommentar `": ping\n\n"` geliefert, damit ein Abbruch des
  Clients bemerkt wird. Keine neue Dependency.
- **E3 (C16):** Wenn der Stream regulär endet (E1, Lauf ohne Trace, `.gz`-Log),
  sendet der Server zuletzt `event: closed\ndata: {}\n\n`. Der Client schließt bei
  `closed` die `EventSource`. Die Seite eines Laufs, der beim Rendern bereits `done`
  oder `escalated` ist, öffnet **keine** `EventSource` (Datenattribut am Seitenelement,
  das der Client liest). Für laufende Läufe verbindet der Client mit der zuletzt
  gerenderten Seq als Startpunkt (Query-Parameter `last_id` am Stream oder
  `Last-Event-ID` — der vorhandene Mechanismus wird genutzt, keine neue Semantik).
- **E4 (C5):** `TrustedHostMiddleware` aus Starlette mit
  `["127.0.0.1", "localhost", "::1", "[::1]"]`; bei `--i-know` und einem anderen
  `--host` wird dieser Host zusätzlich erlaubt. Fremder Host → 400.
- **E5 (C15):** `register_repo` hält ein `fcntl.flock` auf einer Lock-Datei neben der
  Registry über Laden, Ändern und atomares Ersetzen.
- **E6 (C17):** Eine Funktion im Reader-Pfad (`adw/gui/reader.py`), die vor den
  Projektionen `type`, `kind`, `span`, `parent`, `ts` und `payload.status` auf Strings
  (bzw. `payload` auf dict) prüft und falsche Typen als **fehlend** behandelt
  (auf `None` setzt) und dabei ein `problem` meldet, wie es der Reader für kaputte
  Zeilen schon tut. Keine Prüfungen verstreut in den Projektionen.
- **E7 (C18):** Die Prev/Next-Links entstehen aus dem aktuellen Query-String mit
  ersetztem `tools_offset` (wie es `switch_qs` für andere Links schon tut).
- **E8:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Client-
  Verhalten im vorhandenen JS-Harness (`tests/gui_js_harness.js`/`.py`). Keine neue
  Laufzeit-Dependency, kein CDN, kein Frontend-Paket.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Authentifizierung/Token für die GUI.
- Live-Aktualisierung der Run-Liste.
- Umstellung weiterer Routen auf async.
- Weitere Härtungen über C5 und C13–C18 hinaus.

## Akzeptanzkriterien

1. Log mit Spanne A (Start/Ende) und Spanne B (Start, Phase-Start, weitere Events):
   der Stream liefert alle Events beider Spannen und bleibt verbunden; nach dem Ende
   von B (Lauf `done`) folgt `event: closed`.
2. Reconnect mit `Last-Event-ID` hinter dem Ende von A: Stream liefert B und endet
   **nicht** sofort.
3. Lauf in `awaiting_approval` nach dem Ende von A: kein `closed`, Stream bleibt offen.
4. Leeres Log ohne Ende: der Stream liefert Ping-Kommentare; ein Client-Abbruch
   beendet den Generator (kein dauerhaft belegter Thread).
5. Lauf ohne Trace und `.gz`-Log: `event: closed`, und der Client öffnet für einen
   fertigen Lauf keine `EventSource` (JS-Harness).
6. Anfrage mit `Host: evil.example` → 400; mit `127.0.0.1:<port>` und `localhost` → 200.
7. Zwei gleichzeitige `register_repo`-Aufrufe für verschiedene Pfade (Threads oder
   Prozesse): beide Einträge stehen danach in der Registry.
8. Record mit `payload.status` als Liste bzw. `ts` als Zahl: `/` und `/api/runs`
   antworten 200, der betroffene Lauf zeigt ein Problem, die übrigen sind unverändert.
9. Tools-Prev/Next-Link mit `?limit=400&raw_q=foo&tools_offset=200`: der Link behält
   `limit` und `raw_q`.
10. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 10–14 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (SSE-Stream
inkl. `event: closed` und Ping-Kommentar, 400 bei fremdem Host, `/api`-Antworten
unverändert), keine internen Helper-Signaturen.
