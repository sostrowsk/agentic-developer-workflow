# Spec: `adw/events.py` — Event-Emitter für das ADW-Run-Event-Log

Maßgeblich ist `docs/GUI-SPEC.md` (deutsche Fassung `docs/GUI-SPEC.de.md`),
§4.1–§4.4. Bei Widerspruch zwischen dieser Spec und der GUI-SPEC gilt die
GUI-SPEC.

## Goal

Ein eigenständiges Modul `adw/events.py`, das ADW-Run-Events als JSON-Lines
append-only nach `.adw/runs/<run_id>/events.jsonl` schreibt — Schritt 1 der
Umsetzungsreihenfolge aus GUI-SPEC §11. Dieser Lauf liefert **ausschließlich
den Emitter samt öffentlicher API und Zeilenformat**, ohne einen einzigen
Aufrufer. Die wichtigste Invariante: der Emitter ist fail-open — kein Fehler
aus ihm darf einen Run abbrechen.

## Scope

- Neues Modul `adw/events.py` mit öffentlicher API für:
  - einen aktiven, an `repo` und `run_id` gebundenen Emitter,
  - das Emittieren einzelner Point-Events,
  - einen Span-Kontextmanager mit start-/end-Event,
  - einen No-Op-Emitter mit derselben aufrufbaren Oberfläche.
- Der Emitter erhält alles, was er braucht (Repo, Run-ID, Event-Kontext),
  als Parameter über seine öffentliche API; er liest keine Config.
- Persistenz ausschließlich in `.adw/runs/<run_id>/events.jsonl`.
- Neue Tests `tests/test_events.py` (Richtwert 12–18 Tests).
- Kontrakt ist nur die extern beobachtbare Fläche: die unten festgelegte
  öffentliche API von `adw/events.py` und das Zeilenformat von
  `events.jsonl`. Interne Helper-Signaturen sind es nicht.

### Öffentliche API (Kontrakt)

Die minimale öffentliche Fläche; weitere interne Helfer sind nicht Teil des
Kontrakts:

- `EventEmitter(repo: Path, run_id: str)` — der aktive Emitter, gebunden an
  Zielrepo und Run.
- `NoOpEmitter()` — der No-Op-Emitter mit exakt denselben Methodensignaturen.
- Beide bieten:
  - `emit(type: str, payload: dict | None = None, *,
    phase: str | None = None, lane: str | None = None,
    round: int | None = None, span: str | None = None,
    parent: str | None = None) -> None` — schreibt ein Point-Event
    (`kind: "point"`); Rückgabewert ist immer `None`. Nicht übergebene
    Kontextwerte landen als `null` im Record.
  - `span(type: str, payload: dict | None = None, *,
    phase: str | None = None, lane: str | None = None,
    round: int | None = None)` — Kontextmanager, der dem Body ein
    Span-Handle liefert.
- Das Span-Handle trägt `id: str` (die erzeugte Span-ID) und ein
  beschreibbares `end_payload: dict`: was der Body dort vor dem Verlassen
  ablegt, wird als Payload des end-Events geschrieben — so werden die
  getrennten Start-/End-Payloads aus §4.4 übergeben. Auch der No-Op-Emitter
  und eine deaktivierte Instanz liefern ein funktionsfähiges Handle
  (mit ID), schreiben aber nichts.

## Non-Goals (in DIESEM Lauf bewusst nicht gebaut)

- **Keine Aufrufer.** `phases.py`, `agents.py`, `gates.py`, `codex.py`,
  `state.py` und `cli.py` bleiben unverändert.
- Kein Reader, kein Span-Baum-Modell, keine GUI, kein FastAPI, keine Registry.
- Keine Snapshots und keine git-Refs (GUI-SPEC §5) — eigener späterer Lauf.
- Kein `trace:`-Key in `adw/config.py`, kein `adw runs prune`, keine
  Retention (GUI-SPEC §4.5) — eigener späterer Lauf.
- Keine Redaction und keine Kappung von Payloads (GUI-SPEC §8) — roher
  Mitschnitt ist gewollt.
- Keine Validierung typspezifischer Payload-Inhalte: die Tabellen aus §4.4
  definieren die spätere Nutzung; der Emitter akzeptiert beliebige, auch
  unbekannte `type`-Werte unverändert (Vorwärtskompatibilität).
- Keine neuen Laufzeit-Dependencies.
- Keine neuen Persistenzzustände über `events.jsonl` hinaus: keine Index-,
  Offset- oder Sidecar-Dateien, keine Erweiterung von `RunState`.

## Acceptance Criteria

Beobachtbares Verhalten; jedes Kriterium ist über die öffentliche API und den
Inhalt von `events.jsonl` prüfbar.

### Datei & Zeilenformat (§4.1)

1. Der Emitter schreibt nach `.adw/runs/<run_id>/events.jsonl` im Zielrepo.
   Jedes Event ist genau ein JSON-Objekt pro Zeile, `\n`-terminiert, UTF-8,
   append-only — bereits geschriebene Bytes werden nie verändert oder ersetzt.
2. Eine neu angelegte `events.jsonl` erhält Dateirechte `0600`.
3. Vor dem ersten Write ruft der Emitter `ensure_runs_gitignored(repo)` aus
   `adw/worktrees.py` auf. Auch dieser vorbereitende Schritt unterliegt der
   Fail-open-Garantie (Kriterien 17–18).
4. Nach jedem Write wird `flush()` aufgerufen, `fsync()` **nicht** (bewusster
   Trade-off, §4.3).

### Record-Schema (§4.2)

5. Jeder geschriebene Record enthält exakt die Top-Level-Felder `seq`, `ts`,
   `type`, `kind`, `span`, `parent`, `phase`, `lane`, `round`, `payload` —
   keine zusätzlichen Top-Level-Felder, keine Schema-Version.
6. `ts` ist der Schreibzeitpunkt in UTC mit Millisekunden-Genauigkeit und
   endet auf `Z` (Form `YYYY-MM-DDTHH:MM:SS.mmmZ`, z. B.
   `2026-08-05T14:02:20.117Z`).
7. `kind` ist einer von `"start"`, `"end"`, `"point"`; einzelne Events tragen
   `kind: "point"`.
8. Nicht gesetzte Kontextwerte (`parent`, `phase`, `lane`, `round`, ggf.
   `span`) werden als JSON `null` geschrieben; `phase`, `lane`, `round` und
   der Span-Kontext für Point-Events werden vom Aufrufer explizit übergeben
   (GUI-SPEC §6: explizite `emit()`-Aufrufe, keine Magie).
9. `payload` wird roh und ungekürzt serialisiert; der Emitter redigiert,
   filtert oder kappt nichts.

### seq: monoton, lückenlos, unter Lock (§4.3)

10. `seq` ist je Run streng monoton steigend und lückenlos; bei leerer oder
    neuer Datei beginnt die Sequenz bei `1` (nötig, damit ein Reader später
    Kopf-Trunkierung als Lücke erkennen kann).
11. `seq`-Vergabe und Write erfolgen unter einem exklusiven `fcntl.flock` auf
    der Event-Datei (Muster analog `adw/state.py:_repo_lock`): `open("a")` →
    `LOCK_EX` → seq zuweisen → write → flush → unlock.
12. Wird ein Emitter auf eine bestehende, nicht-leere `events.jsonl` geöffnet
    (resume, neuer Prozess), liest er die höchste vorhandene `seq` aus den
    vollständigen Records und zählt lückenlos weiter — der erste neue Event
    trägt `höchste + 1`. Es entsteht kein zusätzlicher persistenter
    Sequenzzustand.
13. Nebenläufige Emits aus mehreren Threads desselben Prozesses erzeugen
    weder doppelte noch fehlende `seq`-Werte und keine ineinander
    verschachtelten (halb geschriebenen) Zeilen. Prozessübergreifend
    serialisiert derselbe Datei-Lock; das Resume-Szenario ist durch
    Kriterium 12 abgedeckt.

### Span-API (§4.2, §4.4)

14. Der Kontextmanager `span(...)` bildet eine Span: beim Betreten wird ein
    Event mit `kind: "start"` geschrieben und eine je Run eindeutige Span-ID
    erzeugt (dem Body über das Span-Handle verfügbar); beim Verlassen ein
    Event mit `kind: "end"`, **demselben** `type` und **derselben** Span-ID.
    Der End-Payload wird über `end_payload` des Span-Handles übergeben
    (getrennte Start-/End-Verträge, §4.4).
15. Verschachtelte Spans setzen `parent` der inneren start-/end-Events auf
    die Span-ID der umschließenden Span; die äußerste Span hat
    `parent: null`. Die Verschachtelungs-Verfolgung ist thread-sicher:
    parallel laufende Threads (Lanes laufen als Threads, §4.3) vermischen
    ihre Span-Kontexte nicht.
16. Tritt im Body des Kontextmanagers eine Exception auf, wird das
    `end`-Event dennoch geschrieben und die Body-Exception unverändert
    weiterpropagiert — die Span-API verschluckt oder ersetzt
    Aufrufer-Exceptions nicht. Ein Fehler beim Schreiben des end-Events
    selbst bleibt fail-open.

### Fail-open — die zentrale Invariante (§4.3)

17. Kein Fehler aus dem Emitter erreicht den Aufrufer — das gilt für
    Konstruktion, Vorbereitung (`ensure_runs_gitignored`, Dateianlage),
    Sequenzermittlung, Serialisierung, Locking, Write, Flush und
    Span-Verarbeitung (Disk full, Permissions, Encoding, nicht
    serialisierbare Payloads eingeschlossen).
18. Beim ersten internen Fehler einer `EventEmitter`-Instanz wird genau
    einmal `logger.warning` geloggt; danach ist **diese Instanz** dauerhaft
    deaktiviert: weitere Emit- und Span-Aufrufe sind stille No-Ops ohne
    Dateizugriff und ohne weitere Warnungen. Die Garantie „einmal pro Run"
    aus §4.3 ergibt sich aus der vorgesehenen Nutzung — der Orchestrator
    hält je Run und Prozess genau eine Emitter-Instanz; eine
    instanzübergreifende oder prozessweite Koordination wird bewusst nicht
    gebaut. Andere Instanzen (insbesondere anderer Runs) bleiben
    unbeeinflusst.
19. `NoOpEmitter` hat exakt dieselben Signaturen wie der aktive Emitter: er
    schreibt nichts, legt keine Datei an, und kein intern von ihm
    verursachter Fehler erreicht den Aufrufer. Exceptions aus dem Body eines
    No-Op-Spans werden dagegen — wie beim aktiven Emitter (Kriterium 16) —
    unverändert weiterpropagiert, niemals verschluckt. Aufrufer können damit
    später bedingungslos emittieren.

## Definition of Done

1. `adw/events.py` existiert und erfüllt alle Acceptance Criteria; kein
   anderes Produktionsmodul (`phases.py`, `agents.py`, `gates.py`,
   `codex.py`, `state.py`, `cli.py`, `config.py`) wurde verändert.
2. `tests/test_events.py` deckt die Kriterien ab (Richtwert 12–18 Tests),
   insbesondere: Feldschema und `ts`-Format; `seq` monoton/lückenlos ab 1;
   Weiterzählen auf bestehender Datei; Thread-Nebenläufigkeit ohne
   Lücken/kaputte Zeilen; Span-Verschachtelung über `parent`; end-Event auch
   bei Exception im Body samt unveränderter Propagation; `0600`-Rechte;
   `ensure_runs_gitignored` vor erstem Write; flush ohne fsync; fail-open
   bei erzwungenem internen Fehler → genau eine `logger.warning`, danach
   Instanz dauerhaft still; No-Op-Emitter schreibt nichts, legt nichts an
   und propagiert Body-Exceptions seiner Spans unverändert.
3. Außer `events.jsonl` (und der von `ensure_runs_gitignored` verantworteten
   `.adw/runs/.gitignore`) entstehen keine neuen persistenten Zustände.
4. `flake8` + `isort` + `pytest` grün; keine neue Laufzeit-Dependency in den
   Projekt-Metadaten.

## Deferred (bewusst nicht gebaut)

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
