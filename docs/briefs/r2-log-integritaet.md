# R2: Event-Log und State überstehen Crash, Prune und Resume

Zweiter Lauf der Reihe aus `ONBOARDING-SPEC-PLAN.md` (Abschnitt 3, Befunde
C6–C12). Setzt auf dem gemergten Stand **0.31.2** (nach R1) auf. Die Ausgangslage ist am
2026-11-08 im Code geprüft und vor dem Bauen gegen den dann aktuellen Stand zu
prüfen (Zeilennummern können sich durch R1 verschoben haben).

`state.json` und `events.jsonl` sind die Wiederherstellungsgrundlage jedes Laufs
und ab dem Lauf W13 die Datenquelle des Ledgers. Sieben Stellen lassen sie heute
bei Crash, Prune oder Resume Daten verlieren oder falsch werden. Dieser Lauf
schließt genau diese sieben Stellen.

## Ausgangslage (im Code verifiziert, 2026-11-08, Stand 0.31.1)

### C6 — „Session vorhanden" gilt als „Test-Lauf fertig"

`_confirm_red` (`adw/lanes.py:~299`) startet den reinen Test-Lauf nur, wenn
`lane_state.session_id is None`. Die Session-ID wird aber schon gespeichert,
sobald der Stream sie meldet (`on_session_id=… _checkpoint_lane_session`,
`adw/lanes.py:~375`, `adw/context.py:350`), also **bevor** der Agent etwas
geschrieben hat. Bricht der Test-Lauf ab (Abo-Limit → `AgentRunError`, Ctrl-C),
überspringt `adw resume` ihn: Ohne Änderungen eskaliert der Lauf dauerhaft mit
„keine Änderungen", mit halb geschriebenen Tests gelten diese als RED-Beweis.
Die Tests `tests/test_phases.py::test_resume_without_any_test_changes_does_not_prove_red`
und `::test_resume_after_the_test_run_repeats_only_the_red_check` stammen aus der
Zeit vor dem frühen Checkpoint und schreiben die alte Annahme fest.

### C7 — Snapshot-Refs ohne atomare Vergabe

`snapshots.capture` (`adw/snapshots.py:~65–67`) liest die höchste Seq der Refs
`refs/adw/<run>/` (`_next_ref_seq`) und schreibt dann mit einfachem
`git update-ref <ref> <commit>`. Parallele Lanes (Threads) lesen dieselbe Seq; der
zweite Schreiber überschreibt den Ref des ersten, beide `snapshot`-Events nennen
denselben Ref → die GUI zeigt für eine Lane den Tree der anderen.

### C8 — Prune ohne den Lock des Laufs

`retention.prune` (`adw/retention.py:~405–440`) vertraut der gescannten Phase
(`done`/`escalated`) und löscht bzw. komprimiert, ohne den per-Run-Lock
`.adw/runs/.lock-<run_id>` (`adw/cli.py` `_run_execution_lock`) zu nehmen. Ein
Lauf speichert seine Endphase, **bevor** der Executor fertig ist (letzte Events,
parallele Lanes nach Eskalation). Ein gleichzeitiges `adw runs prune --gzip`
komprimiert einen Schnappschuss des Logs und entfernt das Original; spätere
Appends gehen verloren.

### C9 — Resume eines fertigen Laufs zerstört die Historie

`adw resume` (`adw/cli.py:~308–343`) lehnt nur `escalated` ab. Ein Lauf in Phase
`done` wird „fortgesetzt": `EventEmitter` öffnet eine neue Run-Spanne. Ist das Log
bereits zu `events.jsonl.gz` komprimiert, entsteht ein neues `events.jsonl` ab
Seq 1, die Leser sehen die `.gz`-Historie nicht mehr, und die nächste Komprimierung
ersetzt das Archiv durch die neuen Records.

### C10 — `state.json` ohne fsync

`RunState._write_snapshot` (`adw/state.py:~209–222`) schreibt in eine Temp-Datei
und ersetzt `state.json` per `os.replace`, ohne `fsync` der Datei und des
Verzeichnisses. Nach Stromausfall oder Kernel-Crash kann `state.json` leer sein;
`load()` scheitert dann und der Lauf ist nicht mehr fortsetzbar.

### C11 — Auto-Prune nach Dry-Run und gegen den eigenen Lauf

`_maybe_auto_prune` (`adw/cli.py:~610`) läuft bei jedem Lauf, der `done` erreicht —
auch bei `--dry-run`, und löscht dann echte Läufe. Und `retention.prune` schließt
den gerade beendeten Lauf nicht aus: Ein Lauf, der eine Woche am Plan-Gate wartete
und erst nach ≥ `keep_runs` neueren Läufen per `approve` fertig wird, löscht
seinen eigenen Run-Ordner.

### C12 — Git-Fehler in der Retention ohne Übersetzung

`retention._git` (`adw/retention.py:~72`) reicht `subprocess.TimeoutExpired` und
`OSError` ungefiltert durch. `adw runs prune` fängt nur `RetentionError`: Traceback
statt Meldung, und die in diesem Durchgang bereits gelöschten Läufe werden nicht
berichtet.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Der Repo-Lock aus R1 (`_repo_lock`, `.adw/runs/.repo.lock`, hält `run`/`resume`/`approve` samt Auto-Prune) und die Lease-Logik aus R1 bleiben unverändert.
- Das Event-Format, die Seq-Vergabe im Event-Log und `fsync`-Verzicht beim
  Event-Append (`adw/events.py`, bewusst: „fsync is deliberately not called")
  bleiben — nur `state.json` bekommt `fsync`.
- Retention-Regeln (`--keep`, `--older-than`, `--gzip`, `keep_runs`) bleiben.
- Alle übrigen Befunde (C5, C13–C29) sind eigene Läufe.

## Aufgabe

**A1 (C6)** Der Test-Lauf gilt erst als erledigt, wenn er regulär zurückgekehrt ist.
**A2 (C7)** Snapshot-Refs werden atomar vergeben.
**A3 (C8)** Prune nimmt den Lock des Laufs.
**A4 (C9)** `resume` eines fertigen Laufs ändert nichts.
**A5 (C10)** `state.json` wird dauerhaft geschrieben.
**A6 (C11)** Kein Auto-Prune nach Dry-Run, der eigene Lauf ist immer ausgenommen.
**A7 (C12)** Git-Fehler der Retention werden zu `RetentionError`.
**A8** `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
`## [0.31.3] — Unreleased` mit `### Fixed`, je Befund ein Punkt im Stil der
vorhandenen Einträge. Version in `pyproject.toml` **nicht** ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 (C6):** Neues Feld `LaneState.test_pass_done: bool = False`. Es wird `True`
  gesetzt, nachdem der Test-Lauf regulär zurückgekehrt ist und die
  Nach-Prüfungen (`_require_no_agent_commit`, `_require_lane_branch`) bestanden
  sind, mit **einer** Speicherung. `_confirm_red` startet den Test-Lauf, solange das
  Flag `False` ist; existiert dabei schon eine `session_id`, wird **diese Session
  fortgesetzt** (`resume=session_id`) statt einer neuen. Keine Migration: Alte
  States ohne Feld bekommen den Default `False` (im schlimmsten Fall setzt ein alter
  Lauf seinen Test-Lauf in derselben Session fort — akzeptiert).
- **E2 (C6):** Die beiden genannten Bestandstests werden an E1 angepasst (sie
  schreiben das Fehlverhalten fest); im Changelog erwähnen.
- **E3 (C7):** Ref anlegen mit `git update-ref <ref> <commit> ""` (leerer alter Wert
  = nur anlegen, wenn er nicht existiert). Scheitert das, weil der Ref existiert,
  mit der nächsten Seq erneut versuchen, höchstens 10 Versuche, dann der bisherige
  Fehlerpfad. Kein Python-Lock, kein Lane-Name im Ref-Pfad (Ref-Schema bleibt).
- **E4 (C8):** `prune` erwirbt für jeden Kandidaten den per-Run-Lock
  `.adw/runs/.lock-<run_id>` **nicht blockierend**. Belegt → Ergebnis `skipped`
  mit Grund „läuft noch". Unter dem Lock wird die Phase aus `state.json` neu
  gelesen; ist sie nicht terminal → `skipped`. Der Lock-Helfer wird mit der CLI
  geteilt (kein zweiter Lock-Dateiname, keine zweite Implementierung).
- **E5 (C9):** `resume` eines Laufs in Phase `done` beendet sich mit Exit 0 und der
  Meldung „Run <id> ist bereits abgeschlossen" — **vor** Preflight und Emitter;
  nichts wird geschrieben.
- **E6 (C10):** `_write_snapshot`: `fh.flush()` + `os.fsync(fh.fileno())` vor
  `os.replace`, danach `fsync` des Run-Verzeichnisses (`os.open(dir, O_RDONLY)`).
  Nur hier; das Event-Log bleibt ohne fsync.
- **E7 (C11):** `_maybe_auto_prune` kehrt sofort zurück, wenn `state.dry_run`.
  `retention.prune` bekommt einen Parameter `exclude: set[str]` (Default leer);
  Auto-Prune übergibt die eigene Run-ID. Der ausgenommene Lauf zählt nicht als
  Kandidat und erscheint nicht im Ergebnis.
- **E8 (C12):** `retention._git` fängt `subprocess.TimeoutExpired` und `OSError`
  und wirft `RetentionError` mit Kommando und Ursache; der bestehende Pfad, der
  bereits erreichte Ergebnisse berichtet, greift damit automatisch.
- **E9:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Kein
  flake8, isort, black, mypy.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- `fsync` für das Event-Log oder andere Dateien.
- Ein Python-Lock oder Lane-Namen in Snapshot-Refs.
- Wiederherstellung schon verlorener Historie oder Reparatur leerer `state.json`.
- Blockierendes Warten des Prune auf laufende Läufe.
- Alle übrigen Befunde (C5, C13–C29).

## Akzeptanzkriterien

Jedes AC ist ein Regressionstest, der **vor** dem Fix rot ist.

1. Test-Lauf meldet eine Session-ID und bricht dann mit `AgentRunError` ab; der
   anschließende Resume startet den Test-Lauf erneut mit `resume=<diese Session>`
   und eskaliert nicht wegen „keine Änderungen".
2. Test-Lauf regulär beendet, Crash vor dem RED-Check: Resume startet **keinen**
   zweiten Test-Lauf (das Flag ist gesetzt).
3. Zwei gleichzeitige `capture`-Aufrufe (Threads, gleicher Lauf) erzeugen zwei
   **verschiedene** Refs, jeder zeigt auf seinen eigenen Commit.
4. Prune, während ein anderer Prozess/Thread `.lock-<run_id>` hält: der Lauf wird
   `skipped`, Ordner und Log sind unverändert.
5. `resume` eines `done`-Laufs mit `events.jsonl.gz`: Exit 0, Meldung, kein neues
   `events.jsonl`, `.gz` byte-gleich.
6. `_write_snapshot` ruft `os.fsync` für Datei und Verzeichnis auf (per
   Monkeypatch nachgewiesen).
7. Dry-Run bis `done` mit `keep_runs: 1` und einem älteren echten, terminalen Lauf:
   der ältere Lauf existiert danach noch.
8. Auto-Prune nach `done` mit `keep_runs: 1`, wenn der eigene Lauf nach
   Startzeit nicht der neueste ist: der eigene Run-Ordner existiert danach noch.
9. `git` wirft in der Retention `TimeoutExpired`: `adw runs prune` endet mit Exit 1
   und Meldung, ohne Traceback, und nennt die bis dahin bearbeiteten Läufe.
10. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 10–14 neue Tests (plus die zwei angepassten aus E2).
Deutlich mehr ist ein Zeichen für Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen
(CLI-Verhalten aus E5/E7, Felder `test_pass_done` im State, `skipped`-Grund im
Prune-Ergebnis), keine internen Helper-Signaturen.
