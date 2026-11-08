# Plan — R2: Event-Log und State überstehen Crash, Prune und Resume

Maßgeblich ist `.adw/spec.md` (A1–A8, AC 1–12). Single-Lane-Projekt (`.adw/config.yaml`):
nur der Workstream **backend**; er umfasst Code, Regressionstests und beide Changelogs.
Umgesetzt werden ausschließlich A1–A8 (Befunde C6–C12), ohne neue Mechanismen darüber hinaus.

Basis: Stand **0.31.2**. Die Code-Stellen der Spec sind am 2026-11-08 erneut geprüft und
stimmen: `adw/lanes.py:280/299/352` (`_confirm_red`, Bedingung `session_id is None`,
`_run_test_only_pass` mit `resume=None` in Zeile 375 und Session-Save in 381–385),
`adw/snapshots.py:65–67/94` (`_next_ref_seq` + einfaches `update-ref`),
`adw/retention.py:32/72/171/421/446` (`_TERMINAL_PHASES`, `_git` ohne
Exception-Übersetzung, `_phase_of`, `prune`, Phasenfilter beim Scan),
`adw/state.py:65/212` (`LaneState`, `extra="forbid"`; `_write_snapshot` ohne fsync),
`adw/cli.py:317/332/356` (`resume`, nur `escalated` abgelehnt; Auto-Prune nach dem
Lock-Block), `adw/cli.py:601–610` (`runs prune` mit Berichtspfad für `RetentionError`),
`adw/cli.py:627` (`_maybe_auto_prune`), `adw/cli.py:903` (`_run_execution_lock`). Vor dem
Bauen gegen den dann aktuellen Stand prüfen.

Betroffene Dateien (abschließend): `adw/lanes.py`, `adw/state.py`, `adw/snapshots.py`,
`adw/retention.py`, `adw/cli.py`, Tests unter `tests/`, `CHANGELOG.md`,
`CHANGELOG.de.md`. `pyproject.toml` bleibt `0.31.2`; `adw/events.py` bleibt unberührt.

Contract: `.adw/contract.yaml` pinnt nur die extern beobachtbaren Flächen aus der Spec
(Resume eines `done`-Laufs, Auto-Prune-Verhalten, State-Feld `test_pass_done`,
Prune-Ergebnis bei belegtem Lock bzw. geänderter Phase, `runs prune` bei Git-Fehler,
Lock-Datei des Laufs). Helfernamen, Signaturen und Ablageorte in diesem Plan sind
Empfehlungen, keine Vertragsfläche.

## Workstream backend

### B1 — Test-Lauf erst nach regulärer Rückkehr erledigt (A1, AC 1, 2, 11)

Dateien: `adw/state.py`, `adw/lanes.py`.

1. `LaneState.test_pass_done: bool = False` (pydantic-Default; alte States laden mit
   `False`, kein Migrationscode).
2. `_confirm_red`: Bedingung `lane_state.session_id is None` durch
   `not lane_state.test_pass_done` ersetzen. Kommentar über dem Restore (Zeilen 301–302)
   an die neue Bedeutung anpassen: eine gespeicherte `session_id` heißt nur noch
   „Session vorhanden", nicht „Test-Lauf fertig".
3. `_run_test_only_pass`: `resume=lane_state.session_id` statt `resume=None` (vorhandene
   Session fortsetzen, sonst neu). Der abschließende Save (Zeilen 381–385) setzt —
   **nach** `_require_no_agent_commit` und `_require_lane_branch` — `session_id` und
   `test_pass_done = True` gemeinsam unter `ctx.state_lock` in **einem** `ctx.save()`.
   Abbruch (`AgentRunError`, Ctrl-C) oder Eskalation einer Nach-Prüfung erreicht diesen
   Save nie; das Flag bleibt `False`. Der frühe Session-Checkpoint per `on_session_id`
   bleibt.
4. Ist das Flag `True`, geht Resume direkt zum RED-Check. RED-Check, Teständerungs- und
   Lösch-Prüfung sowie `red_confirmed`-Save unverändert; das Flag ersetzt keinen
   RED-Beweis.

### B2 — Atomare Snapshot-Ref-Vergabe (A2, AC 3)

Datei: `adw/snapshots.py`.

1. In `_capture` nach `commit-tree`: Schleife über höchstens 10 Versuche. Je Versuch
   `seq = _next_ref_seq(...)`, `ref = refs/adw/<run_id>/<seq>`, dann
   `git update-ref <ref> <commit> ""` (Anlegen nur, wenn nicht vorhanden; ein
   vorhandener Ref wird nie überschrieben).
2. Erfolg → Schleife verlassen, `snapshot`-Event erst jetzt, mit dem tatsächlich
   vergebenen `ref` und zugehörigem `tree`, Format wie bisher.
3. Fehlschlag: existiert der Ref nun (`git show-ref --verify --quiet <ref>` oder
   gleichwertige Prüfung) → nächster Versuch mit neu ermittelter Seq; jeder andere
   Fehler → `RuntimeError` wie heute (fail-open in `capture`). Nach 10 belegten
   Versuchen → `RuntimeError` (Warnung, kein Event, kein Abbruch des Laufs).
4. Kein Python-Lock, kein Lane-Name im Pfad, kein Zähler nebenher; die vorhandenen Refs
   bleiben die einzige Quelle der Seq-Wahl. Modul-Docstring (Punkt 2) an das bedingte
   `update-ref` anpassen.

### B3 — Gemeinsamer Run-Lock und Prune unter dem Lock (A3, AC 4, 5)

Dateien: `adw/state.py` (Empfehlung für den Helfer), `adw/cli.py`, `adw/retention.py`.

1. Einen einzigen Lock-Helfer an einem von CLI und Retention gemeinsam importierbaren
   Ort (Empfehlung: `adw/state.py`, dort liegen `RUNS_RELPATH`/`runs_root`; Retention
   und CLI importieren `adw.state` bereits — kein Import-Zyklus). Datei
   `.adw/runs/.lock-<run_id>`, `open(..., "a+")`, `fcntl.flock`; Parameter für
   blockierend (Default) / nicht blockierend. Nicht blockierend belegt → signalisiert
   „belegt" (z. B. `BlockingIOError` durchreichen oder `False` liefern). Datei wird nie
   gelöscht (Begründung aus dem heutigen Docstring von `_run_execution_lock` übernehmen).
2. `_run_execution_lock` in `adw/cli.py` delegiert an den Helfer (blockierend); Name und
   Verhalten für `run`/`resume`/`approve` unverändert. Repo-Lock und Lease-Logik aus R1
   unverändert.
3. `retention.prune`, je Kandidat nach Alters- und Scan-Phasenfilter (Zeilen 444–448
   bleiben): Lock nicht blockierend erwerben.
   - Belegt → `PruneResult(run_id, "skipped", reason="läuft noch")`, weiter zum nächsten
     Kandidaten; Run-Ordner, Log, Worktrees und Snapshot-Refs unangetastet. Kein Warten.
   - Erworben → Phase per `_phase_of` aus `state.json` neu lesen; nicht in
     `_TERMINAL_PHASES` → `skipped` mit `reason=f"phase {phase}"` (gleiches Format wie
     beim Scan).
   - Sonst `_compress_run`/`_delete_run` **innerhalb** des gehaltenen Locks; Freigabe
     auf allen Pfaden (auch bei `RetentionError`, deren Berichtspfad unverändert bleibt).
4. Die Lock-Datei bleibt nach dem Löschen eines Laufs stehen (wie heute nach jedem
   Lauf); sie passt nicht auf `RUN_ID_RE` und wird nicht als Lauf gezählt.

Deadlock-Freiheit: Auto-Prune läuft in `run`/`resume`/`approve` bereits **nach** dem
Verlassen des eigenen `_run_execution_lock` (z. B. `adw/cli.py:356`); zusätzlich ist
der eigene Lauf über B6 ausgeschlossen. Prune nimmt den Repo-Lock (`.repo.lock`) nicht.

### B4 — `resume` eines fertigen Laufs (A4, AC 6)

Datei: `adw/cli.py`.

1. In `resume` unter dem per-Run-Lock direkt nach dem Neu-Laden, neben dem
   `escalated`-Check (Zeile 332): Phase `done` →
   `typer.echo(f"Run {run_id} ist bereits abgeschlossen")` und `return` aus dem Befehl.
   Der `return` innerhalb des `with`-Blocks verlässt die Funktion, sodass weder
   `_config_for_continuation`, `_preflight`, `EventEmitter`, `_run_span`, ein Save noch
   das nachgelagerte `_maybe_auto_prune` (Zeile 356) erreicht werden. Exit 0.
2. Nichts wird geschrieben: kein neues `events.jsonl`, keine neue Run-Spanne, keine
   Änderung an `events.jsonl`/`events.jsonl.gz` oder `state.json` — insbesondere bei
   bereits komprimiertem Log.
3. `escalated`-Pfad (Exit 1, Hinweis auf `escalation.md`) unverändert.

### B5 — `state.json` dauerhaft schreiben (A5, AC 7)

Datei: `adw/state.py`, `_write_snapshot`.

1. Im `with os.fdopen(...)`: schreiben, `fh.flush()`, `os.fsync(fh.fileno())`.
2. `os.replace(tmp_name, run_dir / "state.json")`.
3. `dir_fd = os.open(run_dir, os.O_RDONLY)`, `os.fsync(dir_fd)`, in `finally`
   `os.close(dir_fd)`.
4. Fehlerbehandlung (Temp-Datei entfernen) unverändert; Event-Log und alle anderen
   Dateien ohne fsync.

### B6 — Kein Auto-Prune nach Dry-Run, eigener Lauf ausgenommen (A6, AC 8, 9)

Dateien: `adw/retention.py`, `adw/cli.py`.

1. `retention.prune(..., exclude: set[str] = ...)` mit leerem Default (z. B.
   `frozenset()`). Die Kandidatenauswahl `_candidates(records, keep)` bleibt unverändert
   (Retention-Regeln unverändert, `keep`-Berechnung nicht umdeuten); ausgenommene Run-IDs
   werden in der Kandidatenschleife vor jeder Prüfung übersprungen, ohne Ergebnis-Eintrag.
2. `_maybe_auto_prune`: sofortige Rückkehr bei `state.dry_run` (deckt auch das
   Fortsetzen eines persistierten Dry-Runs ab); Aufruf mit `exclude={state.run_id}`.
   Da `run`, `resume` und `approve` diese Funktion nutzen, gilt der Ausschluss für alle
   drei Abschlusswege.
3. `runs_prune` übergibt kein `exclude`; Fail-open-Behandlung in `_maybe_auto_prune`
   unverändert.

### B7 — Git-Fehler der Retention als `RetentionError` (A7, AC 10)

Datei: `adw/retention.py`, `_git`.

1. `run_git(...)` in `try`; `except (subprocess.TimeoutExpired, OSError) as exc:`
   `raise RetentionError(f"git {' '.join(args)}: {exc}") from exc` (Kommando und
   Ursache in der Meldung).
2. `prune` hängt wie heute `results` und `failed` an; `runs_prune` gibt die bearbeiteten
   Läufe und den fehlgeschlagenen Kandidaten aus und endet über `_fail` mit Exit 1 ohne
   Traceback. Kein Rollback; bereits ausgeführte Löschungen bleiben.
3. `_worktree_dirty` nutzt `run_git` direkt und fängt bereits selbst — unverändert.

### B8 — Changelog (A8, AC 12)

- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue oberste Sektion
  `## [0.31.3] — Unreleased` mit `### Fixed`, je ein Punkt für C6 (Test-Lauf-Flag, inkl.
  Hinweis auf die zwei angepassten Bestandstests), C7 (atomare Snapshot-Refs), C8
  (Prune unter Run-Lock), C9 (Resume eines fertigen Laufs), C10 (fsync `state.json`),
  C11 (Dry-Run/eigener Lauf beim Auto-Prune), C12 (Git-Timeout/OSError in der
  Retention), im Stil der vorhandenen Einträge, in der jeweiligen Sprache inhaltlich gleich.
- `pyproject.toml` bleibt `0.31.2`.

## Reihenfolge

B1–B7 unabhängig voneinander, mit einer Ausnahme: B3 (Lock in `prune`) und B6
(`exclude`) ändern dieselbe Kandidatenschleife — nacheinander bauen. B8 zuletzt. Jeden
Regressionstest zuerst gegen den Ausgangsstand rot sehen, dann den Fix bauen.

## Tests (Richtwert 10–14 neue Tests + 2 angepasste)

Reale git-Repos in `tmp_path`, bestehende Fixtures wiederverwenden, keine Mocks für
git-Semantik; Monkeypatch nur, wo die Spec es verlangt (fsync, Git-Timeout,
Agent-Abbruch). Zusammengehörige Varianten parametrisieren. Die Tabelle ergibt 11 neue
Tests.

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 1 | Test-Lauf meldet Session-ID, dann `AgentRunError`: `test_pass_done` false; Resume startet Test-Lauf mit `resume=<Session>` und eskaliert nicht wegen „keine Änderungen" | `tests/test_phases.py` (bei den RED-Tests, ab Zeile 1187) |
| 2 | Test-Lauf regulär zurück (Nach-Prüfungen bestanden), Crash vor RED-Check: `state.json` mit `test_pass_done: true`; Resume ohne zweiten Test-Lauf, RED-Check läuft | dto. |
| 3 | Zwei Threads `capture` auf demselben Lauf (Kollision bei der Ref-Vergabe gezielt synchronisieren): zwei verschiedene Refs, jeder auf eigenem Commit, je ein `snapshot`-Event mit seinem Ref und Tree | `tests/test_snapshots.py` |
| 4 | Gehaltener `.lock-<run_id>` eines terminalen Kandidaten, löschend und `gzip_mode` (parametrisiert): `skipped`/„läuft noch", ohne Warten; Run-Ordner und Log unverändert | `tests/test_retention.py` |
| 5 | Beim Scan terminal, unter dem Lock nicht mehr (Phase zwischen Scan und Lock per Monkeypatch/State-Umschreiben geändert): `skipped` mit aktueller Phase, nichts gelöscht/komprimiert | dto. |
| 6 | `adw resume` eines `done`-Laufs mit `events.jsonl.gz`: Exit 0, Meldung, kein `events.jsonl`, `.gz` und `state.json` byte-gleich, kein Auto-Prune | `tests/test_cli.py` (bei den `resume`-Tests) |
| 7 | `_write_snapshot`: per Monkeypatch Reihenfolge Datei-Flush, Datei-fsync, `os.replace`, Verzeichnis-fsync und Schließen des Verzeichnisdeskriptors nachgewiesen | `tests/test_state.py` |
| — | Alte `state.json`-Lane ohne `test_pass_done` lädt mit `False` | `tests/test_state.py` |
| 8 | Dry-Run bis `done`, `keep_runs: 1`, älterer echter terminaler Lauf existiert danach noch | `tests/test_trace_config.py` (bei den Auto-Prune-Tests) |
| 9 | Auto-Prune nach `done`, `keep_runs: 1`, eigener Lauf nicht der neueste: eigener Run-Ordner bleibt, nicht im Prune-Ergebnis | dto. |
| 10 | Nach einem bearbeiteten Kandidaten wirft `git` `TimeoutExpired` bzw. `OSError` (parametrisiert): `adw runs prune` Exit 1, Meldung mit Kommando und Ursache, kein Traceback, bearbeitete Läufe genannt | `tests/test_runs_cli.py` |
| 11 | Anpassen: `test_resume_without_any_test_changes_does_not_prove_red` und `test_resume_after_the_test_run_repeats_only_the_red_check` modellieren den Checkpoint **nach** einem fertigen Test-Lauf → `LaneState(..., test_pass_done=True)` ergänzen; Assertions bleiben | `tests/test_phases.py:1187/1242` |
| 12 | Changelog EN/DE und Version 0.31.2 — per Review, kein eigener Test | — |

Bestehende Tests nur anpassen, wo sie altes Fehlverhalten festschreiben, und das im
Changelog nennen. Tests, die `_run_execution_lock` direkt referenzieren, bleiben grün,
solange der Name in `adw/cli.py` erhalten bleibt. Keine zusätzliche Testmatrix.

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); die gesamte bestehende Suite bleibt grün.
- AC 1–12 erfüllt; AC 1–10 durch Regressionstests belegt, die gegen 0.31.2 rot sind.
- Changelogs EN/DE synchron, Version unverändert.
- Keine Änderungen außerhalb A1–A8; Non-Goals und Deferred unangetastet. Keine weitere
  Toolchain.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- `fsync` für das Event-Log oder andere Dateien außer `state.json`.
- Ein Python-Lock oder Lane-Namen in Snapshot-Refs; ein persistierter Snapshot-Seq-Zähler.
- Wiederherstellung bereits verlorener Historie oder Reparatur leerer `state.json`.
- Blockierendes Warten des Prune auf laufende Läufe.
- Zusätzliche Journale oder Wiederherstellungsmarker.
- Alle übrigen Befunde (C5, C13–C29).

Diese Punkte sind keine Findings im Review-Loop.
