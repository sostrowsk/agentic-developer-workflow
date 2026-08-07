# Plan: `adw/events.py` — Event-Emitter für das ADW-Run-Event-Log

Umsetzung von `.adw/spec.md` (maßgeblich `docs/GUI-SPEC.md` §4.1–§4.4; bei
Widerspruch gilt die GUI-SPEC). Ein einziger Workstream: **backend**. Keine
Frontend-Lane — reines Python-Library-Modul, kein HTTP, keine GUI, keine
Aufrufer. Die wichtigste Invariante: der Emitter ist fail-open — kein Fehler
aus ihm darf einen Run abbrechen.

Der Kontrakt (`.adw/contract.yaml`) pinnt ausschließlich die extern
beobachtbare Fläche: die öffentliche API von `adw/events.py` und das
Zeilenformat von `events.jsonl`. Alles hier Genannte an internen Helfern ist
Umsetzungsdetail, **nicht** Teil des Kontrakts, und darf abweichen, solange
Kontrakt und Acceptance Criteria erfüllt bleiben.

## Workstream: backend

### Zu erstellende/ändernde Dateien
- **Neu:** `adw/events.py` — das gesamte Modul.
- **Neu:** `tests/test_events.py` — Testabdeckung (Richtwert 12–18 Tests).
- Keine Änderung an `phases.py`, `agents.py`, `gates.py`, `codex.py`,
  `state.py`, `cli.py`, `config.py` oder den Projekt-Metadaten
  (keine neue Laufzeit-Dependency).

### Referenzmuster im Bestand (nur Vorlage, nicht 1:1 zu importieren)
- `adw/worktrees.py:53` `ensure_runs_gitignored(repo)` — vor dem ersten Write
  aufzurufen (AC 3).
- `adw/state.py:225` `_repo_lock` — Muster für `fcntl.flock`
  (`open("a")` → `LOCK_EX` → … → unlock). Der Event-Emitter lockt aber die
  **eigene Event-Datei**, nicht `.adw/runs/.seq` (AC 11, kein neuer
  Sequenzzustand).
- `RUNS_RELPATH = Path(".adw") / "runs"` (aus `adw/state.py`) — Zielpfad ist
  `repo / RUNS_RELPATH / run_id / "events.jsonl"`.

### Umsetzungsschritte

1. **Modulgerüst & Imports.** Nur Stdlib: `json`, `os`, `fcntl`, `logging`,
   `threading`, `datetime`, `contextlib`, `pathlib`, `uuid` (o. ä. für
   Span-IDs). Modul-`logger = logging.getLogger(__name__)`. Keine neue
   Dependency; keine Config-Lektüre — der Emitter bekommt alles als Parameter.

2. **`EventEmitter(repo, run_id)`** (Kontrakt).
   - Speichert `repo`, `run_id`, berechnet den Dateipfad
     `repo / ".adw" / "runs" / run_id / "events.jsonl"`.
   - Der Deaktivierungszustand ist **run-scoped, nicht instanz-scoped**: ein
     modulweites (prozessweites) Registry — z. B. ein Set deaktivierter
     `(resolved repo, run_id)`-Schlüssel unter einem Modul-`threading.Lock`.
     Kein persistenter Zustand (Sidecar verboten), nur In-Memory.
   - Konstruktion selbst ist fail-open: ein Fehler hier deaktiviert den Run
     still nach einmaliger Warnung, statt zu werfen (AC 17).

3. **Interner fail-open-Wrapper.** Eine private Hilfe, die jede
   datei-/serialisierungsberührende Aktion umschließt: bei Erfolg nichts, beim
   **ersten** internen Fehler für einen Run (in diesem Prozess) genau ein
   `logger.warning`, dann wird der `(repo, run_id)`-Schlüssel im Registry als
   deaktiviert markiert; danach sind **alle** Emit-/Span-Aufrufe aller
   Emitter-Instanzen dieses Runs in diesem Prozess stille No-Ops ohne
   Dateizugriff und ohne weitere Warnungen (AC 17–18). Deckt Konstruktion,
   Vorbereitung, Sequenzermittlung, Serialisierung, Locking, Write, Flush,
   Span-Verarbeitung ab (Disk full, Permissions, Encoding, nicht
   serialisierbare Payloads). Emitter anderer Runs bleiben unbeeinflusst.

   **Auflösung Warn-Scope — normative Überschreibung von AC 18:** Die
   maßgebliche GUI-SPEC §4.3 garantiert die Warnung „einmal pro Run";
   `.adw/spec.md` AC 18 formuliert „einmal pro Instanz" und stützt die
   Run-Garantie auf eine nicht durchsetzbare Eine-Instanz-Annahme des
   Orchestrators. Nach der Vorrangregel der Spec selbst (bei Widerspruch
   gilt die GUI-SPEC) gilt für diesen Build verbindlich: **genau eine
   Warnung je Repo/Run und Prozess, instanzübergreifend**, danach
   run-scoped Deaktivierung in diesem Prozess — durchgesetzt über das
   In-Memory-Registry. Die Eine-Instanz-Annahme ist nicht tragend und wird
   nicht vorausgesetzt. Prozessübergreifend ist die Garantie ohne verbotenen
   Sidecar-Zustand nicht durchsetzbar: ein neuer Prozess startet frisch und
   darf erneut einmal warnen. **Lesart für Spec-Text:** wo AC 18 bzw. die
   Definition of Done „Instanz dauerhaft still" sagt, ist „Run in diesem
   Prozess dauerhaft still (alle Instanzen desselben Repo/Runs)" zu lesen;
   Test 14 prüft entsprechend zwei Instanzen desselben Runs. Die
   Textkorrektur von `.adw/spec.md` AC 18 auf genau diese Formulierung
   steht noch aus (aus dieser Stufe nicht editierbar) und ändert nichts am
   hier festgelegten Verhalten.

4. **Vorbereitung vor erstem Write.** Einmal pro Instanz (idempotent, unter
   dem fail-open-Wrapper): `ensure_runs_gitignored(repo)` aus
   `adw/worktrees.py` aufrufen und das Run-Verzeichnis sicherstellen (AC 3).
   Die Anlage der `events.jsonl` erhält Rechte `0600` (AC 2) — z. B. via
   `os.open(..., 0o600)`; das Recht muss auf der **neu** angelegten Datei
   sitzen.

5. **Lock + seq-Vergabe + atomarer Zeilen-Write** (AC 10–13). Ein privater
   Schreibpfad, der pro Record:
   - die Event-Datei im Append-Modus öffnet, `fcntl.flock(..., LOCK_EX)`,
   - unter dem Lock die nächste `seq` bestimmt: bei leerer/neuer Datei →
     `seq = 1`; sonst höchste `seq` aus den **vollständigen** vorhandenen
     Records lesen und `+1` — damit ist auch das Resume-Szenario (neuer
     Prozess auf bestehender Datei, AC 12) ohne zusätzlichen persistenten
     Sequenzzustand abgedeckt,
   - den Record als **eine** `\n`-terminierte JSON-Zeile (UTF-8) anhängt,
   - `flush()` (kein `fsync()`, AC 4), dann Lock freigibt.
   Serialisierung: exakt die Top-Level-Felder aus dem Kontrakt in fester
   Struktur; nicht gesetzte Kontextwerte als JSON `null`; Payload roh und
   ungekürzt (AC 5, 8, 9). `ts` = aktueller UTC-Zeitpunkt, Millisekunden,
   `Z` (AC 6). Zusätzlich prozessintern ein `threading.Lock`, damit
   nebenläufige Threads desselben Prozesses keine verschachtelten Halbzeilen
   erzeugen (AC 13); der `flock` serialisiert prozessübergreifend.

6. **`emit(type, payload=None, *, phase, lane, round, span, parent)`**
   (Kontrakt). Baut einen `kind="point"`-Record aus den übergebenen Werten
   und ruft den Schreibpfad (Schritt 5) über den fail-open-Wrapper. Rückgabe
   immer `None`. Unbekannte `type`-Werte unverändert akzeptieren (AC 7, 8).

7. **`span(type, payload=None, *, phase, lane, round)`** als
   `@contextmanager` (Kontrakt).
   - Erzeugt eine je Run eindeutige Span-ID.
   - Ermittelt `parent` aus dem umschließenden Span-Kontext des **aktuellen
     Threads** (thread-lokaler Stack; AC 15 — parallele Threads/Lanes
     vermischen ihre Kontexte nicht). Äußerste Span → `parent=null`.
   - Schreibt beim Betreten `kind="start"` (mit `payload`, `span`=ID,
     `parent`), pusht die ID auf den Thread-Stack, liefert dem Body ein
     **Span-Handle** mit `id` und leerem, beschreibbarem `end_payload`.
   - `try/finally`: beim Verlassen Stack poppen und `kind="end"` mit
     **demselben** `type`, **derselben** Span-ID und `parent` schreiben,
     Payload = `handle.end_payload` (getrennte Start-/End-Payloads, §4.4).
     Das end-Event wird auch bei Body-Exception geschrieben; die
     Body-Exception propagiert unverändert und wird niemals durch einen
     Emitterfehler ersetzt (AC 14, 16). Ein Fehler beim end-Write bleibt
     fail-open.

8. **Span-Handle-Typ** (Kontrakt): schlichtes Objekt mit `id: str` (read)
   und `end_payload: dict` (read/write). Auch der No-Op-Emitter und ein
   Emitter eines deaktivierten Runs liefern ein funktionsfähiges Handle
   (mit ID), schreiben aber nichts.

9. **`NoOpEmitter()`** (Kontrakt) mit exakt denselben Signaturen für `emit`
   und `span`: schreibt nichts, legt keine Datei an, kein intern verursachter
   Fehler erreicht den Aufrufer. `span` liefert ein Handle mit ID; im
   `finally` wird nichts geschrieben, aber eine **Body-Exception propagiert
   unverändert** (AC 19). Gemeinsame Span-/Handle-Semantik ggf. so
   faktorisieren, dass Aktiv- und No-Op-Pfad dieselbe Kontextmanager-Kontur
   haben.

10. **Abschlussprüfung.**
    - Kein anderes Produktionsmodul und keine Projekt-Metadaten geändert;
      außer `events.jsonl` (und der von `ensure_runs_gitignored`
      verantworteten `.adw/runs/.gitignore`) kein neuer persistenter Zustand.
    - `flake8`, `isort` und `pytest` ausführen; Befunde innerhalb des
      beschriebenen Scopes beheben.

### Tests — `tests/test_events.py` (Richtwert 12–18)

Alle Kriterien über öffentliche API und `events.jsonl`-Inhalt prüfbar,
`tmp_path` als Repo:

1. Feldschema: genau die 10 Top-Level-Felder, keine zusätzlichen, keine
   Schema-Version (AC 5).
2. `ts`-Format: UTC, Millisekunden, endet auf `Z` (Regex/`strptime`) (AC 6).
3. `emit` schreibt `kind="point"`; nicht gesetzte Kontextwerte = `null`
   (AC 7, 8).
4. `payload` roh/ungekürzt durchgereicht (AC 9).
5. `seq` monoton, lückenlos, beginnt bei `1` (AC 10).
6. Resume: Emitter auf bestehende nicht-leere Datei zählt lückenlos weiter
   (`höchste + 1`), kein zusätzlicher Sequenzzustand (AC 12).
7. Thread-Nebenläufigkeit: viele Emits aus mehreren Threads → keine
   doppelten/fehlenden `seq`, jede Zeile valides JSON (keine Halbzeilen)
   (AC 13).
8. Span start/end: gleicher `type`, gleiche Span-ID; `payload` des Aufrufs im
   start-Record, `end_payload` im end-Record (AC 14).
9. Verschachtelte Spans: `parent` der inneren = ID der äußeren; äußerste
   `parent=null` (AC 15). Optional: thread-getrennte Verschachtelung.
10. Exception im Span-Body: end-Event trotzdem geschrieben, Exception
    propagiert unverändert (AC 16).
11. Dateirechte `0600` auf neu angelegter `events.jsonl` (AC 2).
12. `ensure_runs_gitignored` vor erstem Write aufgerufen (Monkeypatch/Spy;
    `.adw/runs/.gitignore` existiert) (AC 3).
13. `flush` ohne `fsync` (Spy/Monkeypatch auf `os.fsync` bzw. Stream) (AC 4).
14. Fail-open: erzwungener interner Fehler (z. B. Payload nicht
    serialisierbar oder Write-Fehler) → genau eine `logger.warning`
    (`caplog`), Aufrufer sieht keine Exception, danach der Run dauerhaft
    still: weitere Emits derselben Instanz **und** einer zweiten Instanz
    desselben Runs schreiben/warnen nicht; ein Emitter eines anderen Runs
    bleibt funktionsfähig (AC 17, 18; Warn-Scope pro Run und Prozess, siehe
    Schritt 3).
15. `NoOpEmitter`: schreibt nichts, legt keine Datei an; `span` liefert
    Handle mit ID; `span`-Body-Exception propagiert unverändert (AC 19).

### Definition of Done (aus Spec)
- `adw/events.py` erfüllt alle Acceptance Criteria — AC 18 in der in
  Schritt 3 festgelegten Run/Prozess-Lesart; kein anderes Produktionsmodul
  geändert.
- `tests/test_events.py` deckt die Kriterien ab (12–18 Tests).
- Außer `events.jsonl` (und der von `ensure_runs_gitignored` verantworteten
  `.adw/runs/.gitignore`) keine neuen persistenten Zustände.
- `flake8` + `isort` + `pytest` grün; keine neue Laufzeit-Dependency.

## Nicht-Ziele (in DIESEM Lauf bewusst nicht gebaut)

- Keine Aufrufer oder Instrumentierung in `phases.py`, `agents.py`,
  `gates.py`, `codex.py`, `state.py`, `cli.py` oder anderen bestehenden
  Produktionsmodulen.
- Kein Reader, kein Span-Baum-Modell, keine GUI, kein FastAPI, keine
  Registry.
- Keine Snapshots und keine git-Refs (GUI-SPEC §5).
- Kein `trace:`-Key in `adw/config.py`, kein `adw runs prune`, keine
  Retention (GUI-SPEC §4.5).
- Keine Redaction, keine Payload-Kappung, keine typspezifische
  Payload-Validierung (§4.4-Tabellen definieren spätere Nutzung; unbekannte
  `type`-Werte werden unverändert akzeptiert).
- Keine neuen Laufzeit-Dependencies.
- Keine neuen Persistenzzustände über `events.jsonl` hinaus: keine Index-,
  Offset- oder Sidecar-Dateien, keine Erweiterung von `RunState`.

## Deferred (unverändert aus Spec übernommen)

Weitergehende Härtung/Erweiterung — ausdrücklich **kein** Akzeptanzkriterium,
spätere Läufe oder verworfen:

- Log-Rotation, Kompression (`--gzip`), Retention/Pruning (GUI-SPEC §4.5).
- Schema-Versionierung des Record-Formats und Migration alter Logs.
- Integritätsprüfung, Prüfsummen, Erkennung/Reparatur korrupter oder
  angeschnittener Logs — Sache des späteren Readers/der GUI (§4.2).
- Sequenz- oder Offset-Indizes zur Beschleunigung des Resume-Scans.
- `fsync`-basierte Crash-Sicherheit (bewusst verworfen, §4.3;
  `state.json` bleibt die Resume-Autorität).
- Performance-Optimierung (Batch-Writes, Writer-Thread) über das
  <1 ms/Event-Ziel aus §9 hinaus.
- Nebenläufigkeitskoordination über den `fcntl.flock` aus §4.3 hinaus.
- Snapshots und git-Refs (§5), Reader, Span-Baum-Modell, GUI, FastAPI,
  Registry, `trace:`-Config, Aufrufer-Instrumentierung
  (Umsetzungsschritte 2–13 aus §11).
