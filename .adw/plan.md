# Plan — R1: Der Orchestrator schreibt und löscht nur, was ihm gehört

Maßgeblich ist `.adw/spec.md`. Single-Lane-Projekt (`.adw/config.yaml`): nur der
Workstream **backend**; er umfasst Code, Tests und Changelogs. Umgesetzt werden
ausschließlich A1–A5 (Befunde C1–C4).

Basis: Stand **0.31.1**. Die Code-Stellen der Spec sind am 2026-11-08 erneut geprüft
und stimmen: `adw/lanes.py:526` (`_seed_artifacts`), `adw/lanes.py:585`
(`_restore_approved_artifacts`), `adw/phases.py:642` (`_push_branch`, nacktes
`--force-with-lease` in Zeile 653; Aufrufer `run_ci_phase`, Zeile 548, der in Dry-Run
nicht pusht), `adw/cli.py:112` (`_preflight`), `adw/cli.py:183/310/348`
(`run`/`resume`/`approve`), `adw/cli.py:860` (`_run_execution_lock`),
`adw/preflight.py:27/139/193` (`SELF_HEAL_ARTIFACTS`, `check_worktree`, `run_all`),
`adw/context.py:33` (`ARTIFACTS`), `adw/retention.py:225/268/399` (`_run_worktrees`,
`_delete_run`, `prune`), `adw/state.py:108` (`RunState`, pydantic mit
`extra="forbid"`; `authoring_session` Zeile 167). Vor dem Bauen gegen den dann
aktuellen Stand prüfen.

Betroffene Dateien (abschließend): `adw/lanes.py`, `adw/phases.py`, `adw/state.py`,
`adw/cli.py`, `adw/preflight.py`, `adw/retention.py`, Tests unter `tests/`,
`CHANGELOG.md`, `CHANGELOG.de.md`. `pyproject.toml` bleibt unverändert.

Contract: `.adw/contract.yaml` pinnt nur CLI-Exit/Meldung bei belegtem Repo-Lock, das
State-Feld `pushed_shas` und die Lock-Datei. Helfernamen, Signaturen und Schalter in
diesem Plan sind Empfehlungen, keine Vertragsfläche.

## Workstream backend

### B1 — Gemeinsamer Artefakt-Helfer und sicheres Seeding (A1, AC 1–3)

Datei: `adw/lanes.py`.

1. Die heutige Symlink-/Datei-/Verzeichnis-Behandlung aus
   `_restore_approved_artifacts` (Zeilen 591–612) in **einen** privaten Helfer ziehen:
   `.adw` als Symlink oder Datei entfernen (nie folgen) und als echtes Verzeichnis
   anlegen; je Artefaktpfad einen Symlink entfernen bzw. ein Verzeichnis entfernen;
   erst dann aus dem Run-Ordner kopieren, sodass eine reguläre Datei entsteht (E1).
2. `_restore_approved_artifacts` nutzt den Helfer; beobachtbares Verhalten exakt wie
   heute: fehlende Run-Ordner-Artefakte werden übersprungen, kopiert wird nur bei
   Abweichung, Config-Restore (Zeilen 613–642) unverändert.
3. `_seed_artifacts` nutzt denselben Helfer für die drei Einträge aus `ARTIFACTS`,
   bei jedem Lane-Start einschließlich Resume. Die bestehende Eskalation bei
   fehlendem Run-Ordner-Artefakt bleibt und greift **vor** jeder Änderung im Worktree.
4. Commit nur über die drei Pfade `.adw/<name>` (E2): `git status --porcelain --
   <Pfade>` entscheidet, `git add -- <Pfade>`, `git commit -m … -- <Pfade>`. Kein
   `git add .adw`. Fremddateien in `.adw/` und anderweitig gestagte Änderungen bleiben
   unberührt (gestagt, uncommittet) und lösen allein keinen Seed-Commit aus.

### B2 — Push mit festem Erwartungswert (A2, AC 4–6)

Dateien: `adw/state.py`, `adw/phases.py`.

1. `RunState.pushed_shas: dict[str, str]` mit `Field(default_factory=dict)`; alte
   `state.json` ohne das Feld laden mit `{}` (pydantic-Default, kein Migrationscode).
2. `_push_branch`:
   - Lokalen HEAD des Worktrees vor dem Push ermitteln.
   - Lease ausschließlich aus dem gespeicherten Eintrag bilden (E3):
     `--force-with-lease=refs/heads/<branch>:<pushed_shas[branch]>`, ohne Eintrag
     `--force-with-lease=refs/heads/<branch>:`. Remote-Tracking-Refs und ein
     zwischenzeitliches `git fetch` beeinflussen die Erwartung nicht. Übrige Argumente
     (`-u origin <branch>`, Timeout 600, `safe_env`, `SSH_AUTH_SOCK`) unverändert.
   - Erfolg → `pushed_shas[branch] = HEAD`, unter `ctx.state_lock` mit dem bestehenden
     `ctx.save()` persistiert (kein neuer Persistenzweg, kein weiteres Feld).
   - Lease-Ablehnung erkennen (rc ≠ 0 und git meldet `rejected` / `stale info` im
     stderr). Dann `git ls-remote origin refs/heads/<branch>` mit gleicher Env und
     Timeout (E4): Remote-SHA == HEAD → wie Erfolg (speichern, kein Fehler); sonst
     `escalate` mit einer Meldung, die „Remote-Branch hat fremde Commits“ und den
     Branch enthält. Schlägt `ls-remote` selbst fehl → normale Eskalation.
   - Timeout und alle anderen Push-Fehler (Netz, Auth) eskalieren wie heute und werden
     nie über den Crash-Fenster-Pfad zu Erfolgen.
3. `run_ci_phase` bleibt bis auf den Aufruf unverändert; `pushed_sha` für das
   CI-Polling wird weiter wie heute ermittelt. Dry-Run pusht wie heute nicht, also
   bleibt `pushed_shas` dort leer. Der Neuaufbau des Integrations-Branches als
   Non-Fast-Forward funktioniert weiter, solange der Remote auf dem zuletzt selbst
   gepushten SHA steht.

### B3 — Repo-Lock und Resume-Schutz (A3, AC 7–8)

Dateien: `adw/cli.py`, `adw/preflight.py`.

1. Neuer Kontextmanager analog zu `_run_execution_lock`: zuerst
   `ensure_runs_gitignored(repo)` (`adw/worktrees.py:52`; legt `.adw/runs/` samt
   selbst-ignorierender `.gitignore` an, idempotent), dann `.adw/runs/.repo.lock`
   öffnen (`a+`) und `fcntl.flock(LOCK_EX | LOCK_NB)`; bei `BlockingIOError`
   `_fail(...)` (Exit 1) mit der Meldung, dass in diesem Repo bereits ein
   ADW-Kommando läuft. Die Datei wird nie gelöscht; ihr Name passt wie
   `.seq`/`.lock-*` nicht auf `RUN_ID_RE`, Retention zählt sie also nicht als Lauf (E5).
   **Warum `ensure_runs_gitignored` statt `mkdir`:** `run` ruft es heute erst nach dem
   Preflight (`adw/cli.py:283`). Legte der Lock vorher nur den Ordner an, stünde beim
   ersten Lauf in einem Repo ohne `.adw/runs/` der Pfad `.adw/runs/` untracked im
   `git status`, gehört nicht zu `SELF_HEAL_ARTIFACTS`, und `check_worktree` würde
   einen sonst sauberen Checkout verweigern — dauerhaft, weil die Lock-Datei bleibt.
   Die `.gitignore` ist selbst ignoriert; sie ist weder Run-Ordner noch
   `.adw`-Artefakt noch State, der Abbruch bei belegtem Lock bleibt also wie in E5
   gefordert ohne beobachtbare Mutation. Der spätere Aufruf in `run` (Zeile 283) bleibt
   unverändert stehen (idempotent).
2. Einbau, jeweils gehalten bis zum Kommandoende einschließlich `_maybe_auto_prune`
   und auf allen Ausstiegspfaden freigegeben; der per-Run-Lock liegt innerhalb:
   - `run`: Erwerb nach `_load_config`, unmittelbar vor `_preflight`. Die
     Issue-Quellen-Prüfung, `_resolve_gate_mode` und `register_repo` (GUI-Registry,
     nicht `.adw`) bleiben davor.
   - `resume`/`approve`: Erwerb direkt nach `repo.resolve()`, vor der Existenzprüfung
     und vor `_run_execution_lock`.
   - Bei belegtem Lock: kein Preflight, keine Selbstheilung, kein Run-Ordner, kein
     State angelegt oder verändert, keine Eskalation (alle drei Kommandos).
3. Resume-Schutz in `adw/preflight.py` (E6): `run_all` erhält einen Schalter (z. B.
   `protect_artifacts: bool = False`). Ist er gesetzt und besteht die dirty Menge
   ausschließlich aus `SELF_HEAL_ARTIFACTS`, kehrt die Arbeitsbaum-Prüfung ohne
   Heilung zurück; fremde oder gemischte dirty Pfade verweigern wie heute, ohne etwas
   zu verwerfen. Ohne Schalter bleibt das Verhalten byte-gleich.
   **Kompatibilität:** `tests/test_cli.py:836` patcht `adw.preflight.check_worktree`
   mit `lambda repo: None`. `run_all` ruft im Normalfall weiterhin
   `check_worktree(repo)` ohne Zusatzargument auf; der Schutzpfad läuft über einen
   separaten Aufruf bzw. ein nur bei gesetztem Schalter übergebenes Argument.
4. `_preflight` reicht den Schalter durch. `resume` und `approve` setzen ihn auf
   `state.authoring_session is not None` (State ist dort vor dem Preflight unter dem
   per-Run-Lock geladen). `run` setzt ihn nie. Reihenfolge Forge → Arbeitsbaum →
   Modelle und die Menge der sechs Pfade bleiben.

### B4 — `trees`-Grenze beim Prune (A4, AC 9)

Datei: `adw/retention.py`.

1. `_run_worktrees` prüft **vor** dem Auflösen den unaufgelösten Pfad
   `repo.resolve() / RUNS_RELPATH / run_id / "trees"` (E7):
   - `is_symlink()` (auch dangling) → `RetentionError`;
   - existiert und ist kein Verzeichnis → `RetentionError`;
   - aufgelöster Pfad nicht innerhalb des aufgelösten Run-Ordners → `RetentionError`;
   - existiert nicht → weiter wie heute (Läufe ohne Worktrees bleiben prunebar).
2. Sweep-Verhalten (verifiziert): `prune` (Zeile 432–438) **re-raist** heute jede
   `RetentionError` aus `_delete_run` und bricht den Sweep ab. AC 9 verlangt aber, dass
   der betroffene Lauf als Fehler erscheint und weitere Läufe bearbeitet werden.
   Deshalb: `_run_worktrees` wirft die `RetentionError` mit gesetztem
   `failed=PruneResult(run_id, "failed", reason=…)`; `_delete_run` fängt genau diese
   Ablehnung um den `_run_worktrees`-Aufruf und gibt `exc.failed` zurück — dasselbe
   „partial failure, not an abort“-Muster wie bei einem nicht sicher entfernbaren
   Worktree (Zeilen 304–314). `adw runs prune` endet dadurch wie heute über die
   `failed`-Ergebnisse mit Exit 1; `_maybe_auto_prune` meldet es fail-open. Die
   `RetentionError` aus `git worktree list` (Zeile 231, repo-weiter Fehler) bleibt ein
   Abbruch wie heute.
3. Da `_run_worktrees` vor jeder Löschung läuft, bleiben Worktrees, Snapshot-Refs und
   Run-Ordner des betroffenen Laufs unberührt. `--keep`, `--older-than` und
   Auto-Prune unverändert.

### B5 — Changelog (A5, AC 10)

- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
  `## [0.31.2] — Unreleased` mit `### Fixed`, je ein Punkt für C1 (Seeding), C2
  (Push-Lease), C3 (Repo-Lock + Resume-Schutz), C4 (`trees`-Grenze) im Stil der
  vorhandenen Einträge (fetter Leitsatz, dann Problem → Folge → Fix). Angepasste
  bestehende Tests, die altes Fehlverhalten festschrieben, dort nennen.
- `pyproject.toml` bleibt `0.31.1`.

## Reihenfolge

B1–B4 unabhängig voneinander; B2 umfasst State-Modell und Push-Pfad gemeinsam, B3 CLI
und Preflight gemeinsam. B5 nach den funktionalen Änderungen. Jeden Regressionstest
zuerst gegen den Ausgangsstand rot sehen, dann den Fix bauen.

## Tests (Richtwert 10–16 neue Tests)

Reale git-Repos in `tmp_path`, keine Mocks für git-Semantik. Die Tabelle ergibt 14
neue Tests (Test 7 parametrisiert zählt als einer). Push-Tests nutzen den
vorhandenen Helfer `add_bare_origin` (`tests/test_phases.py:2947`). Parametrisierung
nur für die ausdrücklich genannten Varianten.

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 1 | Artefakt-Symlink nach außen: Ziel byte-gleich, Worktree-Datei regulär mit Run-Inhalt | `tests/test_phases.py` (bei den Restore-Tests, ab Zeile 1508) |
| 2 | `.adw`-Symlink nach außen: außen keine Datei, `.adw` echtes Verzeichnis mit drei Artefakten | dto. |
| 3a | Seed-Commit enthält nur geänderte Artefaktpfade; fremde gestagte Datei bleibt gestagt und uncommittet; Fremddatei in `.adw` uncommittet | dto. |
| 3b | Unveränderte Artefakte + Fremddatei in `.adw` → kein Seed-Commit | dto. |
| 4 | Bare-Origin, fremder Commit + `git fetch` → Eskalation „Remote-Branch hat fremde Commits“, Remote-Spitze unverändert | `tests/test_phases.py` (bei den CI-Tests) |
| 5 | Erster Push neuer Branch → `pushed_shas` persistiert; zweiter Push als Non-Fast-Forward gelingt und aktualisiert | dto. |
| 6 | Remote == HEAD, `pushed_shas` leer → kein Fehler, SHA gespeichert | dto. |
| — | Alte `state.json` ohne `pushed_shas` lädt mit `{}` | `tests/test_state.py` |
| 7 | Gehaltener `.repo.lock` → `run`, `resume`, `approve` (parametrisiert): Exit 1 mit Meldung, kein neuer Run-Ordner, dirty `.adw`-Artefakt unverändert, State unverändert | `tests/test_cli.py` |
| 7c | Erster `adw run` in einem sauberen Repo ohne `.adw/runs/` und ohne Ignore-Regel: der Preflight verweigert nicht wegen der Lock-Datei, `.adw/runs/.gitignore` existiert, `git status` zeigt `.adw/runs` nicht | `tests/test_cli.py` |
| 8 | `resume` mit `authoring_session` + dirty `.adw/plan.md` → Datei byte-gleich, Preflight verweigert nicht | `tests/test_cli.py` |
| 8b | Dasselbe mit zusätzlich fremdem dirty Pfad → Verweigerung wie heute, nichts verworfen | dto. |
| 9 | `trees`-Symlink auf fremden registrierten Worktree → fremder Worktree + Registrierung bleiben, Run-Ordner bleibt, Ergebnis `failed`, zweiter Lauf im Sweep geprunt | `tests/test_retention.py` |
| 10 | Changelog-Sektion EN/DE und Version 0.31.1 — per Review, kein eigener Test | — |

Bestehende Tests nur anpassen, wo sie altes Fehlverhalten festschreiben, und das im
Changelog nennen. Kein bestehender Test referenziert `_push_branch` oder
`--force-with-lease`; die CI-Tests pushen echt gegen ein Bare-Origin. Pusht einer
davon auf einen bereits vorbefüllten Remote-Branch ohne `pushed_shas`, wäre das die
zu nennende Anpassung.

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); die bestehenden 1362 Tests bleiben grün.
- AC 1–11 erfüllt; AC 1–9 durch Regressionstests belegt, die gegen 0.31.1 rot sind.
- Changelogs EN/DE synchron, Version unverändert.
- Contract beschränkt auf CLI-Exit/Meldung, `pushed_shas` und Lock-Datei.
- Keine Änderungen außerhalb A1–A5; keine neue Toolchain (E8).

## Deferred (bewusst nicht gebaut)

- Feineres Lock nur über die Authoring-Phasen (paralleles Bauen zweier Läufe im
  selben Repo) — E5 serialisiert bewusst ganze Kommandos.
- Blockierendes Warten auf den Repo-Lock oder Anzeige des Lock-Halters.
- Nutzung gepushter SHAs für andere Zwecke (GUI, Kennzahlen).
- Symlink-Prüfungen an weiteren Stellen als den hier genannten.
- Alle Befunde C5–C29.

Diese Punkte sind keine Findings im Review-Loop.
