# Spec: R2 — Event-Log und State überstehen Crash, Prune und Resume

## Ziel

`state.json`, `events.jsonl` und die Snapshot-Refs sind die Wiederherstellungsgrundlage
jedes Laufs (ab W13 auch Datenquelle des Ledgers). Sieben Stellen (C6–C12) lassen sie
heute bei Crash, paralleler Snapshot-Erfassung, Prune oder Resume Daten verlieren oder
falsch werden. Dieser Lauf schließt genau diese sieben Stellen mit den bindenden
Vorentscheidungen E1–E9 des Issues und ohne neue Mechanismen darüber hinaus:
ein unterbrochener Test-Lauf wird korrekt fortgesetzt, abgeschlossene Läufe behalten
ihre Historie, Prune und Executor koordinieren sich über den vorhandenen Run-Lock.

Ausgangsstand: 0.31.2 (nach R1). Am 2026-11-08 im Code bestätigt (Zeilen können sich
noch verschieben; maßgeblich ist das beschriebene Verhalten):
`_confirm_red` prüft `lane_state.session_id is None` (`adw/lanes.py:299`);
`snapshots.capture` vergibt die Seq per `_next_ref_seq` und schreibt mit einfachem
`update-ref` (`adw/snapshots.py:65–67`); `retention.prune` (`adw/retention.py:421`)
nimmt keinen Lock und kennt keinen Ausschluss; `retention._git` (`adw/retention.py:72`)
übersetzt keine `TimeoutExpired`/`OSError`; `RunState._write_snapshot` (`adw/state.py:212`)
ruft kein `fsync`; `resume` lehnt nur `escalated` ab (`adw/cli.py:332`);
`_maybe_auto_prune` (`adw/cli.py:627`) prüft kein `dry_run`; der per-Run-Lock ist
`_run_execution_lock` (`adw/cli.py:903`). `pyproject.toml` steht auf `0.31.2`.

## Scope

### A1 (C6) — Test-Lauf gilt erst nach regulärer Rückkehr als erledigt
- Neues persistiertes Feld `LaneState.test_pass_done: bool = False` in `state.json`.
- Es wird `True`, nachdem der reine Test-Lauf regulär zurückgekehrt ist **und**
  `_require_no_agent_commit` sowie `_require_lane_branch` bestanden sind — in
  **einer** Speicherung. Bei Abbruch oder fehlgeschlagener Nach-Prüfung bleibt es `False`.
- `_confirm_red` startet den Test-Lauf, solange `test_pass_done` `False` ist.
  Existiert bereits eine `session_id`, wird diese Session fortgesetzt
  (`resume=<session_id>`), sonst eine neue gestartet. Eine gespeicherte `session_id`
  bedeutet nur noch „Session vorhanden", nicht „Test-Lauf fertig".
- Ist `test_pass_done` `True`, startet Resume keinen zweiten Test-Lauf, sondern geht
  direkt zum RED-Check. Das Flag ersetzt keinen RED-Beweis: die bestehenden Prüfungen
  auf Teständerungen und rote Gates bleiben unverändert.
- Alte States ohne das Feld laden mit Default `False`; keine Migration (ein alter Lauf
  setzt im schlimmsten Fall seinen Test-Lauf in derselben Session fort — akzeptiert).
- Die Bestandstests
  `tests/test_phases.py::test_resume_without_any_test_changes_does_not_prove_red` und
  `::test_resume_after_the_test_run_repeats_only_the_red_check` werden an dieses
  Verhalten angepasst (sie schreiben das Fehlverhalten fest).

### A2 (C7) — Atomare Vergabe der Snapshot-Refs
- `capture` legt den Ref mit `git update-ref <ref> <commit> ""` an (leerer alter Wert:
  nur anlegen, wenn er nicht existiert). Ein vorhandener Ref wird nie überschrieben.
- Scheitert das Anlegen, weil der Ref bereits existiert, wird mit der nächsten Seq erneut
  versucht, höchstens 10 Versuche; danach greift der bisherige Fehlerpfad (Warnung,
  kein `snapshot`-Event, kein Abbruch des Laufs). Andere Fehler bleiben im bisherigen Pfad.
- Das `snapshot`-Event nennt den tatsächlich vergebenen Ref und den zugehörigen Tree.
- Ref-Schema `refs/adw/<run_id>/<seq>` bleibt; die vorhandenen Refs bleiben die einzige
  Quelle der Seq-Wahl (kein Python-Lock, kein Lane-Name im Pfad, kein Zähler nebenher).

### A3 (C8) — Prune nimmt den Lock des Laufs
- `retention.prune` erwirbt je Kandidat den per-Run-Lock `.adw/runs/.lock-<run_id>`
  **nicht blockierend**, bevor es löscht oder komprimiert, und hält ihn bis zum Ende
  der Löschung bzw. Komprimierung dieses Kandidaten.
- Lock belegt → Ergebnis `skipped` mit Grund „läuft noch"; Run-Ordner, Log, Worktrees
  und Snapshot-Refs des Laufs bleiben unverändert. Es wird nicht gewartet.
- Unter dem Lock wird die Phase aus `state.json` neu gelesen; nur `done` und
  `escalated` sind terminal. Ist sie nicht terminal → `skipped` mit dem Grund, der die
  Phase nennt (wie bisher beim Scan).
- Lock-Helfer, Dateiname und Implementierung sind ein einziger, von CLI und Retention
  gemeinsam genutzter. Die blockierende Semantik von `_run_execution_lock` für
  `run`/`resume`/`approve` bleibt.

### A4 (C9) — `resume` eines fertigen Laufs ändert nichts
- `adw resume <id>` eines Laufs in Phase `done` gibt „Run <id> ist bereits
  abgeschlossen" aus und endet mit Exit 0 — **vor** Preflight, Emitter und jeder
  State-Mutation.
- Es wird nichts geschrieben: kein neues `events.jsonl`, keine neue Run-Spanne, keine
  Änderung an `events.jsonl`/`events.jsonl.gz` oder `state.json`, kein Auto-Prune.
  Das gilt insbesondere bei bereits komprimiertem `events.jsonl.gz`.
- Das Verhalten für `escalated` (Exit 1 mit Hinweis auf `escalation.md`) bleibt.

### A5 (C10) — `state.json` dauerhaft schreiben
- `_write_snapshot` in dieser Reihenfolge: Temp-Datei schreiben, `fh.flush()`,
  `os.fsync(fh.fileno())`, `os.replace` nach `state.json`, dann `fsync` des per
  `os.open(run_dir, O_RDONLY)` geöffneten Run-Verzeichnisses (Deskriptor danach schließen).
- Nur hier; das Event-Log bleibt ohne fsync.

### A6 (C11) — Kein Auto-Prune nach Dry-Run, eigener Lauf ausgenommen
- `_maybe_auto_prune` kehrt sofort zurück, wenn `state.dry_run` gesetzt ist. Ein bis
  `done` geführter Dry-Run verändert keine echten Läufe.
- `retention.prune` bekommt `exclude: set[str]` (Default leer). Ausgenommene Läufe
  zählen nicht als Kandidat und erscheinen nicht im Ergebnis.
- Auto-Prune übergibt stets die eigene Run-ID — unabhängig davon, über welchen Befehl
  (`run`, `resume`, `approve`) der Lauf `done` erreicht. Ein spät abgeschlossener Lauf,
  dessen Startzeit älter ist als die durch `keep_runs` geschützten, bleibt so erhalten.
- Manuelles `adw runs prune` übergibt nichts; die übrigen Retention-Regeln und die
  Fail-open-Fehlerbehandlung von Auto-Prune bleiben.

### A7 (C12) — Git-Fehler der Retention werden `RetentionError`
- `retention._git` fängt `subprocess.TimeoutExpired` und `OSError` und wirft
  `RetentionError` mit ausgeführtem Kommando und Ursache.
- Damit greift der bestehende Berichtspfad von `adw runs prune`: Exit 1, Meldung ohne
  Traceback, Ausgabe der bis dahin bearbeiteten Läufe und des fehlgeschlagenen
  Kandidaten. Bereits ausgeführte Löschungen bleiben bestehen (kein Rollback).

### A8 — Changelog
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue oberste Sektion
  `## [0.31.3] — Unreleased` mit `### Fixed`, je Befund C6–C12 ein Punkt im Stil der
  vorhandenen Einträge, in der jeweiligen Sprache inhaltlich gleich. Der C6-Punkt
  erwähnt die Anpassung der beiden Bestandstests aus A1.
- Version in `pyproject.toml` bleibt `0.31.2`.

## Non-Goals

- Repo-Lock (`_repo_lock`, `.adw/runs/.repo.lock`) und Lease-Logik aus R1 — unverändert.
- Event-Format, Seq-Vergabe im Event-Log, fsync-Verzicht beim Event-Append
  (`adw/events.py`) — unverändert.
- Retention-Regeln (`--keep`, `--older-than`, `--gzip`, `keep_runs`) — unverändert,
  abgesehen von Lock, Phasen-Neuprüfung und `exclude` aus A3/A6.
- Neue Phasen oder persistierte Zustände über `test_pass_done` hinaus; Migration alter
  States; Änderung des Snapshot-Ref-Schemas.
- Ledger-Implementierung; Befunde C5 und C13–C29.
- Versionssprung in `pyproject.toml`.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- `fsync` für das Event-Log oder andere Dateien außer `state.json`.
- Ein Python-Lock oder Lane-Namen in Snapshot-Refs; ein persistierter Snapshot-Seq-Zähler.
- Wiederherstellung bereits verlorener Historie oder Reparatur leerer `state.json`.
- Blockierendes Warten des Prune auf laufende Läufe.
- Zusätzliche Journale oder Wiederherstellungsmarker.
- Alle übrigen Befunde (C5, C13–C29).

## Akzeptanzkriterien

AC 1–10 sind Regressionstests, die vor dem Fix rot sind; zusammengehörige Varianten
dürfen parametrisiert werden.

1. **C6 Abbruch nach Session-ID:** Der Test-Lauf meldet eine Session-ID und bricht dann
   mit `AgentRunError` ab; `test_pass_done` bleibt `False`. Der anschließende Resume
   startet den Test-Lauf erneut mit `resume=<diese Session>` und eskaliert nicht wegen
   „keine Änderungen".
2. **C6 regulär beendet:** Der Test-Lauf kehrt regulär zurück (Nach-Prüfungen bestanden),
   danach Crash vor dem RED-Check. `state.json` enthält `test_pass_done: true`; der
   Resume startet **keinen** zweiten Test-Lauf und führt den ausstehenden RED-Check aus.
3. **C7:** Zwei gleichzeitige `capture`-Aufrufe (Threads, gleicher Lauf) erzeugen zwei
   verschiedene Refs; jeder zeigt auf seinen eigenen Commit, und jedes `snapshot`-Event
   nennt seinen Ref.
4. **C8 Lock belegt:** Während ein anderer Prozess/Thread `.lock-<run_id>` eines
   terminalen Kandidaten hält, liefert `prune` (löschend und `--gzip`) für diesen Lauf
   `skipped` mit Grund „läuft noch", ohne zu warten; Run-Ordner und Log sind unverändert.
5. **C8 Phase unter dem Lock:** Ein beim Scan terminaler Kandidat ist beim Neu-Lesen
   unter dem Lock nicht mehr terminal: `prune` meldet `skipped` mit der aktuellen Phase
   und löscht oder komprimiert nicht.
6. **C9:** `adw resume` eines `done`-Laufs mit `events.jsonl.gz`: Exit 0, Meldung
   „Run <id> ist bereits abgeschlossen", kein neues `events.jsonl`, `.gz` und
   `state.json` byte-gleich, kein Auto-Prune.
7. **C10:** `_write_snapshot` ruft `os.fsync` für die Datei (vor `os.replace`) und für
   das Run-Verzeichnis auf (per Monkeypatch nachgewiesen).
8. **C11 Dry-Run:** Ein Dry-Run bis `done` mit `keep_runs: 1` und einem älteren echten,
   terminalen Lauf: der ältere Lauf existiert danach noch.
9. **C11 eigener Lauf:** Auto-Prune nach `done` mit `keep_runs: 1`, wobei der eigene
   Lauf nach Startzeit nicht der neueste ist: der eigene Run-Ordner existiert danach noch
   und erscheint nicht im Prune-Ergebnis.
10. **C12:** Nach mindestens einem erfolgreich bearbeiteten Kandidaten wirft `git` in der
    Retention `subprocess.TimeoutExpired`: `adw runs prune` endet mit Exit 1 und einer
    Meldung mit Kommando und Ursache ohne Traceback und nennt die bis dahin bearbeiteten
    Läufe. `OSError` wird gleich übersetzt.
11. Die zwei Bestandstests aus A1 sind an das neue Verhalten angepasst und grün.
12. Changelog-Sektion `## [0.31.3] — Unreleased` / `### Fixed` mit je einem Punkt für
    C6–C12 in `CHANGELOG.md` und `CHANGELOG.de.md`, inhaltlich synchron, inkl. Hinweis
    auf die angepassten Tests; `pyproject.toml`-Version unverändert `0.31.2`.

Richtwert: 10–14 neue Tests (plus die zwei angepassten). Deutlich mehr ist ein Zeichen
für Gold-Plating.

## Contract (Single-Lane)

Nur extern beobachtbare Flächen:
- CLI `adw resume <id>` bei Phase `done`: Exit 0, Meldung „Run <id> ist bereits
  abgeschlossen", keine Schreibzugriffe.
- CLI `adw run --dry-run` bis `done`: kein Auto-Prune. Auto-Prune nach echtem `done`
  lässt den eigenen Lauf aus.
- `state.json`: Lane-Feld `test_pass_done: bool` (Default `false`).
- Prune-Ergebnis: Aktion `skipped` mit Grund „läuft noch" bei belegtem Run-Lock.
- `adw runs prune` bei Git-Timeout/OSError: Exit 1, Meldung, bearbeitete Läufe genannt.

Keine internen Helper-Signaturen. Der Thread-Nachweis aus AC 3 erweitert den Contract
nicht um einen Multi-Lane-Workflow.

## Definition of Done

- AC 1–12 erfüllt.
- `uv run ruff check .` grün.
- `uv run pytest -x -q` grün (gesamte Suite). Keine weitere Toolchain gefordert.
- Keine Änderungen außerhalb des Scopes; Non-Goals und Deferred unangetastet.
