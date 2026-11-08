# R1: Der Orchestrator schreibt und löscht nur, was ihm gehört

Erster Lauf der Reihe aus `ONBOARDING-SPEC-PLAN.md` (Abschnitt 3, Befunde C1–C4).
Setzt auf dem Stand **0.31.1** auf. Die Ausgangslage ist am 2026-11-08 im Code
geprüft und vor dem Bauen gegen den dann aktuellen Stand zu prüfen.

Der Orchestrator läuft **ohne Sandbox**. Vier Stellen lassen ihn heute Dateien
außerhalb seines Bereichs schreiben, fremde Remote-Commits überschreiben oder
fremde Arbeit löschen. Dieser Lauf schließt genau diese vier Stellen — nicht mehr.

## Ausgangslage (im Code verifiziert, 2026-11-08, Stand 0.31.1)

### C1 — Das Seeding folgt Symlinks und committet zu viel

`_seed_artifacts` (`adw/lanes.py:526–545`) kopiert `spec.md`, `plan.md` und
`contract.yaml` aus dem Run-Ordner mit `shutil.copy2(source, target_dir / name)`
in `<worktree>/.adw/`. `copy2` folgt einem Symlink am Ziel. Das Seeding läuft bei
jedem Lane-Start, **auch beim Resume** (`adw/lanes.py:126`), und damit **vor**
`_restore_approved_artifacts` (`adw/lanes.py:585`), das Symlinks ausdrücklich
entfernt („Symlink NIE folgen", `.adw` selbst als Symlink wird `unlink()`t).

Szenario: Der Build-Agent (darf `Write(./**)` und Bash im Worktree) legt
`.adw/contract.yaml -> ~/.bashrc` an; der Lauf wird abgebrochen; `adw resume`
überschreibt `~/.bashrc` mit dem Contract.

Zweiter Defekt an derselben Stelle: Ob committet wird, entscheidet
`git status --porcelain -- .adw` über den **ganzen** Ordner, committet wird mit
`git add .adw` und `git commit -m …` — das nimmt fremde Dateien in `.adw/` mit und
den **gesamten Index**, also auch gestagte, noch nicht durch Gates gelaufene
Implementierung. Danach hat sich HEAD bewegt und `_require_expected_head`
eskaliert fälschlich mit „HEAD hat sich außerhalb des Orchestrators bewegt".

### C2 — `--force-with-lease` ohne erwarteten Wert

`_push_branch` (`adw/phases.py:642–660`) pusht mit
`git push --force-with-lease -u origin <branch>`. Ohne `=<ref>:<expect>` nimmt git
den **aktuellen Remote-Tracking-Ref** als Erwartung. Holt eine IDE oder der Nutzer
per `git fetch` einen fremden Commit auf diesem Branch, ist die Lease „erfüllt" und
der Push überschreibt ihn — entgegen dem Docstring („foreign remote changes are
never overwritten"). Aufrufer: `run_ci_phase` (`adw/phases.py:548`); der
Integrations-Branch wird je Runde neu von der Basis gebaut, ein Non-Fast-Forward
ist dort erwartet.

### C3 — Kein Repo-weiter Lock vor Preflight und Authoring

`run`, `resume` und `approve` (`adw/cli.py`) halten nur den **per-Run**-Lock
`_run_execution_lock` (`adw/cli.py:860`, Datei `.adw/runs/.lock-<run_id>`). Der
Preflight (`adw/preflight.py:139` `check_worktree`) läuft vorher und heilt die
sechs `SELF_HEAL_ARTIFACTS` im **Haupt-Checkout** (tracked → `checkout HEAD`,
untracked → löschen). Spec- und Plan-Phase schreiben genau diese Dateien im
Haupt-Checkout.

- **Zwei Läufe:** Lauf A schreibt gerade `.adw/spec.md`; ein zweites `adw run` im
  selben Repo heilt sie im Preflight weg.
- **Eigener Resume:** Ein Lauf crasht mitten in einer Fix-Runde der Plan-Synthese
  (`ctx.state.authoring_session` gesetzt, `adw/authoring.py:613`). `adw resume`
  heilt vor der Phase die reviewte Zwischenfassung weg bzw. setzt sie auf die
  Fassung aus HEAD zurück; die Fix-Runde arbeitet dann am falschen Stand weiter.

### C4 — `trees/` als Symlink verschiebt die Lösch-Grenze

`_run_worktrees` (`adw/retention.py:225–242`) bildet die Grenze mit
`(repo.resolve() / RUNS_RELPATH / run_id / "trees").resolve()`. Ist `trees` ein
Symlink auf einen fremden registrierten Worktree, wird **dessen** Pfad zur Grenze,
und `_delete_run` (`adw/retention.py:268`) entfernt ihn per git. Der Run-Ordner
selbst ist bereits gegen Symlinks geprüft (`:273`), `trees` nicht.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- `_restore_approved_artifacts` und sein Config-Restore bleiben im Verhalten
  unverändert; sie werden höchstens als gemeinsamer Helfer wiederverwendet.
- Der per-Run-Lock `_run_execution_lock` und seine Semantik bleiben.
- Die Preflight-Reihenfolge Forge → Arbeitsbaum → Modelle bleibt.
- Retention-Regeln (`--keep`, `--older-than`, Auto-Prune) bleiben; nur die
  Grenzprüfung für `trees` kommt hinzu.
- Alle übrigen Review-Befunde (C5–C29) sind eigene Läufe.

## Aufgabe

**A1 — Seeding sicher (C1).** Vor dem Kopieren gilt dieselbe Behandlung wie in
`_restore_approved_artifacts`: `.adw` als Symlink oder Datei wird entfernt, ein
Artefaktpfad, der Symlink oder Verzeichnis ist, wird entfernt, erst dann wird
kopiert. Committet werden **nur** die drei Artefaktpfade.

**A2 — Push mit festem Erwartungswert (C2).** Der Push nennt den erwarteten
Remote-Stand ausdrücklich.

**A3 — Repo-Lock und Resume-Schutz (C3).** Ein Repo-weiter Lock schützt Preflight
und Ausführung; der Preflight heilt beim Fortsetzen keine Artefakte eines Laufs,
der mitten im Authoring steht.

**A4 — `trees`-Grenze (C4).** `trees` muss ein echtes Verzeichnis innerhalb des
Run-Ordners sein, sonst verweigert Prune diesen Lauf.

**A5 — Changelog.** `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
`## [0.31.2] — Unreleased` mit `### Fixed`, je Befund ein Punkt (Problem → Folge →
Fix, im Stil der vorhandenen Einträge). Version in `pyproject.toml` **nicht**
ändern — das macht der Release-Schritt nach dem Lauf.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 (C1):** Die Symlink-/Verzeichnis-Behandlung wird aus
  `_restore_approved_artifacts` in einen kleinen Helfer gezogen und von **beiden**
  Funktionen genutzt. Kein zweiter, abweichender Code.
- **E2 (C1):** Staging und Commit nur über die Pfade aus `ARTIFACTS`:
  `git add -- <die drei Pfade>` und `git commit -m … -- <die drei Pfade>` (git
  committet bei Pfadangabe nur diese Pfade, andere gestagte Änderungen bleiben
  gestagt und unverändert). Die Commit-Entscheidung prüft ebenfalls nur diese
  drei Pfade.
- **E3 (C2):** Der zuletzt erfolgreich gepushte SHA wird pro Branch im `RunState`
  gespeichert (ein Feld `pushed_shas: dict[str, str]`, Default leer, mit dem
  bestehenden `state.save` persistiert — in derselben Speicherung wie der übrige
  Fortschritt nach dem Push). Push mit
  `--force-with-lease=refs/heads/<branch>:<gespeicherter SHA>`; ist keiner
  gespeichert: `--force-with-lease=refs/heads/<branch>:` (leerer Erwartungswert =
  der Remote-Branch darf noch nicht existieren).
- **E4 (C2, Crash-Fenster):** Lehnt git den Push wegen der Lease ab, fragt der
  Orchestrator den Remote-Stand ab (`git ls-remote origin refs/heads/<branch>`).
  Ist er **gleich dem lokalen HEAD**, gilt der Push als bereits erfolgt (Crash
  zwischen Push und Speichern): SHA speichern, weiter. Sonst Eskalation mit klarer
  Meldung „Remote-Branch hat fremde Commits".
- **E5 (C3):** Ein Repo-weiter Lock als `flock` auf `.adw/runs/.repo.lock`
  (Geschwister von `.seq` und `.lock-<run_id>`, wird wie diese nie gelöscht),
  **nicht blockierend** (`LOCK_EX | LOCK_NB`). `run`, `resume` und `approve`
  erwerben ihn **vor dem Preflight** und halten ihn bis zum Ende des Kommandos.
  Ist er belegt: Abbruch mit Exit 1 und der Meldung, dass in diesem Repo bereits
  ein ADW-Kommando läuft — keine Eskalation, kein State angelegt oder verändert.
  Reihenfolge: Repo-Lock vor dem per-Run-Lock (keine Deadlocks).
- **E6 (C3):** `resume` und `approve` eines Laufs mit gesetztem
  `authoring_session` heilen die dirty `SELF_HEAL_ARTIFACTS` **nicht**, sondern
  lassen sie stehen und laufen weiter (sie gehören diesem Lauf). Fremde, nicht
  ADW-eigene Änderungen verweigern weiterhin wie heute.
- **E7 (C4):** `_run_worktrees` prüft den **unaufgelösten** Pfad `trees`: existiert
  er und ist ein Symlink oder kein Verzeichnis, oder liegt sein aufgelöster Pfad
  nicht innerhalb des Run-Ordners → `RetentionError` für diesen Lauf (der Sweep
  über die anderen Läufe läuft wie heute weiter).
- **E8:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Kein
  flake8, isort, black, mypy.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Ein feineres Lock nur über die Authoring-Phasen (paralleles Bauen zweier Läufe
  im selben Repo). E5 serialisiert bewusst ganze Kommandos.
- Gepushte SHAs für andere Zwecke (GUI, Kennzahlen).
- Prüfungen auf Symlinks an weiteren Stellen als den hier genannten.
- Alle Befunde C5–C29.

## Akzeptanzkriterien

Jedes AC ist ein Regressionstest, der **vor** dem Fix rot ist.

1. Seeding mit `.adw/contract.yaml` als Symlink auf eine Datei außerhalb des
   Worktrees: die Zieldatei ist danach **byte-gleich** unverändert, im Worktree
   liegt das Artefakt als reguläre Datei.
2. Seeding mit `.adw` als Symlink auf ein Verzeichnis außerhalb: dort entsteht
   keine Datei.
3. Seeding bei einer zusätzlich gestagten Datei außerhalb von `.adw` und einer
   Fremddatei in `.adw/`: der Seed-Commit enthält genau die geänderten
   Artefaktpfade; die gestagte Datei bleibt gestagt und uncommittet.
4. Push nach einem fremden Commit auf dem Remote-Branch und anschließendem
   `git fetch` im Repo: der Push wird abgelehnt, der fremde Commit bleibt auf dem
   Remote, der Lauf eskaliert mit der Meldung aus E4 (Test mit lokalem Bare-Repo
   als `origin`).
5. Erster Push eines neuen Branches gelingt; der SHA steht danach in
   `pushed_shas`; ein zweiter Push nach neuem Commit gelingt.
6. Crash-Fenster: Remote enthält bereits den lokalen HEAD, `pushed_shas` ist leer
   → kein Fehler, SHA wird gespeichert.
7. Zweites `adw run`, während der Repo-Lock gehalten wird: Exit 1, Meldung, kein
   neuer Run-Ordner, kein `.adw`-Artefakt verändert.
8. `resume` eines Laufs mit gesetztem `authoring_session` und dirty
   `.adw/plan.md`: die Datei ist nach dem Preflight unverändert.
9. Prune eines Laufs, dessen `trees` ein Symlink auf einen fremden registrierten
   Worktree ist: der fremde Worktree existiert danach noch, das Ergebnis für
   diesen Lauf ist ein Fehler, andere Läufe werden weiter bearbeitet.
10. `uv run ruff check .` und `uv run pytest -x -q` grün; die bestehenden 1362
    Tests bleiben grün (Anpassung bestehender Tests nur, wo sie das alte
    Fehlverhalten festschreiben — dann im Changelog nennen).

**Richtwert Testzahl:** 10–16 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (CLI-Exit
und Meldung aus E5, `pushed_shas` im State), keine internen Helper-Signaturen.
