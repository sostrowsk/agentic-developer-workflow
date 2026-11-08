# Spec — R1: Der Orchestrator schreibt und löscht nur, was ihm gehört

Basis: Stand **0.31.1** (`pyproject.toml`). Die Ausgangslage aus dem Issue ist am
2026-11-08 im Code nachgeprüft und trifft zu: `_seed_artifacts` (`adw/lanes.py:526`),
`_restore_approved_artifacts` (`adw/lanes.py:585`), `_push_branch` mit nacktem
`--force-with-lease` (`adw/phases.py:653`), `_run_execution_lock` (`adw/cli.py:860`),
`check_worktree` (`adw/preflight.py:139`), `SELF_HEAL_ARTIFACTS` mit sechs Pfaden
(`adw/preflight.py:27`), `ARTIFACTS = ("spec.md", "plan.md", "contract.yaml")`
(`adw/context.py:33`), `_run_worktrees` (`adw/retention.py:225`). Ein Feld
`pushed_shas` existiert noch nicht. Zeilenangaben sind vor dem Bauen gegen den dann
aktuellen Stand zu prüfen.

## Ziel

Der unsandboxte Orchestrator schreibt, committet, pusht und löscht an genau den vier
Stellen der Befunde C1–C4 nur noch, was ihm gehört:

- Das Lane-Seeding folgt keinem Symlink und committet nur die drei Artefaktpfade.
- Der Push überschreibt nie fremde Commits auf dem Remote-Branch, auch nicht nach
  einem `git fetch` durch IDE oder Nutzer.
- Zwei ADW-Kommandos im selben Repo laufen nie gleichzeitig; ein `resume`/`approve`
  mitten im Authoring verwirft die eigenen Zwischenstände nicht.
- Prune entfernt nie einen Worktree, auf den ein `trees`-Symlink zeigt.

Dieser Lauf schließt genau diese vier Stellen — nicht mehr.

## Umfang

### A1 — Seeding sicher (C1, E1–E2)

- Bei jedem Lane-Start, einschließlich Resume, werden ausschließlich `spec.md`,
  `plan.md` und `contract.yaml` aus dem Run-Ordner nach `<worktree>/.adw/` kopiert.
- Vor dem Kopieren gilt dieselbe Behandlung wie in `_restore_approved_artifacts`:
  Ist `.adw` ein Symlink oder eine Datei, wird er/sie entfernt (nie gefolgt) und
  `.adw` als echtes Verzeichnis angelegt. Ist ein Artefaktpfad ein Symlink, wird der
  Link entfernt; ist er ein Verzeichnis, wird es entfernt. Erst dann wird kopiert.
  Ergebnis: Das Artefakt liegt im Worktree als reguläre Datei; Symlink-Ziele
  außerhalb des Worktrees bleiben unverändert.
- Diese Behandlung ist **ein** gemeinsamer Helfer, den Seeding und
  `_restore_approved_artifacts` beide nutzen (E1). Das beobachtbare Verhalten von
  `_restore_approved_artifacts` inklusive Config-Restore bleibt unverändert; die
  Helfer-Signatur ist keine Vertragsfläche.
- Commit-Entscheidung, Staging und Commit beziehen sich ausschließlich auf die drei
  Pfade aus `ARTIFACTS` (E2): Status nur über diese Pfade, `git add -- <Pfade>`,
  `git commit -m … -- <Pfade>`. Fremddateien in `.adw/` werden nicht aufgenommen und
  lösen allein keinen Seed-Commit aus; andere gestagte Änderungen bleiben gestagt
  und uncommittet.

### A2 — Push mit festem Erwartungswert (C2, E3–E4)

- `RunState` erhält das Feld `pushed_shas: dict[str, str]` (Branch → zuletzt
  erfolgreich gepushter SHA), Default leer. Bestehende `state.json` ohne das Feld
  laden weiterhin mit leerem Default.
- Der Push nennt den Erwartungswert ausdrücklich (E3):
  `--force-with-lease=refs/heads/<branch>:<gespeicherter SHA>`; ist für den Branch
  keiner gespeichert, `--force-with-lease=refs/heads/<branch>:` (Remote-Branch darf
  noch nicht existieren). Ein zwischenzeitliches `git fetch` verändert diese
  Erwartung nicht.
- Nach erfolgreichem Push wird der gepushte SHA in `pushed_shas[<branch>]`
  eingetragen und mit dem bestehenden `state.save` in derselben Speicherung wie der
  übrige Fortschritt nach dem Push persistiert.
- Lehnt git den Push wegen der Lease ab (E4), fragt der Orchestrator
  `git ls-remote origin refs/heads/<branch>` ab:
  - Remote-SHA **gleich lokalem HEAD** → Push gilt als bereits erfolgt (Crash
    zwischen Push und Speichern): SHA speichern, Lauf geht weiter.
  - sonst → Eskalation mit einer Meldung, die „Remote-Branch hat fremde Commits“
    enthält und den Branch nennt. Der Remote wird nicht verändert.
- Andere Push-Fehler (Timeout, Netz, Auth) eskalieren wie heute und werden nicht zu
  Erfolgen umgedeutet.
- Der erwartete Non-Fast-Forward des Integrations-Branches (Neuaufbau je Runde)
  funktioniert weiterhin, solange der Remote auf dem zuletzt selbst gepushten SHA
  steht.

### A3 — Repo-Lock und Resume-Schutz (C3, E5–E6)

- Repo-weiter Lock (E5): `flock` auf `.adw/runs/.repo.lock` (Geschwister von
  `.seq` und `.lock-<run_id>`, wird nie gelöscht, auch nicht durch Retention),
  nicht blockierend (`LOCK_EX | LOCK_NB`).
- `adw run`, `adw resume` und `adw approve` erwerben ihn **vor dem Preflight** und
  halten ihn bis zum Ende des Kommandos; mit dem Ende wird er freigegeben.
  Reihenfolge: Repo-Lock vor dem per-Run-Lock; dessen Semantik bleibt.
- Ist der Lock belegt: Exit 1 mit einer Meldung, dass in diesem Repo bereits ein
  ADW-Kommando läuft. Kein Preflight, keine Selbstheilung, kein Run-Ordner und kein
  State angelegt oder verändert, keine Eskalation. Das gilt für alle drei Kommandos.
- Resume-Schutz (E6): `resume` und `approve` eines Laufs, dessen State
  `authoring_session` gesetzt hat, heilen dirty `SELF_HEAL_ARTIFACTS` im
  Haupt-Checkout **nicht** — sie bleiben unverändert stehen, das Kommando läuft
  weiter. Betroffen sind genau die sechs Pfade `.adw/issue.md`, `.adw/spec.md`,
  `.adw/plan.md`, `.adw/contract.yaml`, `.adw/spec-summary.md`,
  `.adw/plan-summary.md`. Sind daneben fremde Pfade dirty, verweigert der Preflight
  wie heute, ohne Änderungen zu verwerfen. Ohne gesetztes `authoring_session` und
  bei `run` bleibt die Selbstheilung wie heute.
- Die Preflight-Reihenfolge Forge → Arbeitsbaum → Modelle bleibt.

### A4 — `trees`-Grenze (C4, E7)

- `_run_worktrees` prüft den **unaufgelösten** Pfad `<run_dir>/trees`: Ist er ein
  Symlink (auch ein ins Leere zeigender) oder ein vorhandener Nicht-Verzeichnispfad,
  oder liegt sein aufgelöster Pfad nicht innerhalb des Run-Ordners → `RetentionError`
  für diesen Lauf. Für diesen Lauf wird nichts gelöscht (kein Worktree, keine
  Snapshot-Refs, kein Run-Ordner).
- Existiert `trees` nicht, verhält sich Prune wie heute; Läufe ohne angelegte
  Worktrees bleiben prunebar.
- Der Sweep über die übrigen Läufe läuft wie heute weiter; der betroffene Lauf
  erscheint im Ergebnis als Fehler. `--keep`, `--older-than` und Auto-Prune bleiben
  unverändert.

### A5 — Changelog

- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
  `## [0.31.2] — Unreleased` mit `### Fixed`, je Befund C1–C4 ein Punkt
  (Problem → Folge → Fix, im Stil der vorhandenen Einträge). Angepasste bestehende
  Tests, die altes Fehlverhalten festschrieben, werden dort genannt.
- Version in `pyproject.toml` bleibt `0.31.1`; der Bump gehört zum Release-Schritt.

## Nicht-Ziele

- Verhaltensänderung an `_restore_approved_artifacts` oder dessen Config-Restore
  (nur Wiederverwendung des gemeinsamen Helfers).
- Änderung an Semantik oder Datei des per-Run-Locks `_run_execution_lock`.
- Änderung der Preflight-Reihenfolge, der Menge `SELF_HEAL_ARTIFACTS` oder der
  Verweigerung fremder Arbeitsbaum-Änderungen.
- Änderung der Retention-Regeln (`--keep`, `--older-than`, Auto-Prune) außer der
  `trees`-Grenzprüfung.
- Neue persistente Zustände außer `pushed_shas`; keine weitere Lock-Verwaltung
  neben dem festgelegten `flock`.
- Versionssprung in `pyproject.toml`.
- Neue Toolchain-Werkzeuge (kein flake8, isort, black, mypy — E8).
- Alle übrigen Review-Befunde C5–C29.

## Akzeptanzkriterien

Jedes AC 1–9 ist durch einen automatisierten Regressionstest belegt, der gegen den
Stand 0.31.1 fehlschlägt.

1. **Seeding, Artefakt-Symlink:** Ist `<worktree>/.adw/contract.yaml` vor dem
   Seeding ein Symlink auf eine Datei außerhalb des Worktrees, ist diese Datei
   danach byte-gleich unverändert, und `<worktree>/.adw/contract.yaml` ist eine
   reguläre Datei mit dem Inhalt aus dem Run-Ordner.
2. **Seeding, `.adw`-Symlink:** Ist `<worktree>/.adw` ein Symlink auf ein
   Verzeichnis außerhalb des Worktrees, entsteht dort nach dem Seeding keine Datei;
   `<worktree>/.adw` ist ein echtes Verzeichnis mit den drei Artefakten.
3. **Seeding, Commit-Umfang:** Mit einer zusätzlich gestagten Datei außerhalb von
   `.adw` und einer Fremddatei in `.adw/` enthält der Seed-Commit genau die
   geänderten Artefaktpfade; die gestagte Datei ist danach weiterhin gestagt und
   nicht committet, die Fremddatei nicht committet. Sind die drei Artefakte
   unverändert, löst die Fremddatei allein keinen Seed-Commit aus.
4. **Push, fremder Commit nach Fetch:** Mit einem lokalen Bare-Repo als `origin`,
   einem fremden Commit auf dem Remote-Branch und anschließendem `git fetch` im
   Repo wird der Push abgelehnt; der fremde Commit ist danach weiterhin Spitze des
   Remote-Branches; der Lauf eskaliert mit einer Meldung, die „Remote-Branch hat
   fremde Commits“ enthält.
5. **Push, Normalfall:** Der erste Push eines noch nicht existierenden Branches
   gelingt; danach steht der gepushte SHA in `pushed_shas[<branch>]` im
   persistierten State. Ein zweiter Push nach einem neuen lokalen Commit — auch als
   Non-Fast-Forward — gelingt und aktualisiert den Eintrag.
6. **Push, Crash-Fenster:** Steht der Remote-Branch bereits auf dem lokalen HEAD
   und `pushed_shas` ist leer, endet der Push ohne Fehler und ohne Eskalation, und
   der SHA wird in `pushed_shas` gespeichert.
7. **Repo-Lock belegt:** Während `.adw/runs/.repo.lock` von einem anderen Prozess
   bzw. Handle gehalten wird, endet `adw run` mit Exit 1 und der Meldung, dass in
   diesem Repo bereits ein ADW-Kommando läuft; es entsteht kein neuer Run-Ordner,
   und kein `.adw`-Artefakt im Haupt-Checkout ist verändert (auch nicht ein
   dirty, sonst selbstheilbares).
8. **Resume-Schutz:** `adw resume` eines Laufs mit gesetztem `authoring_session`
   und dirty `.adw/plan.md` im Haupt-Checkout: die Datei ist nach dem Preflight
   byte-gleich unverändert, der Preflight verweigert nicht.
9. **`trees`-Symlink:** Prune eines Laufs, dessen `trees` ein Symlink auf einen
   fremden registrierten Worktree ist: Der fremde Worktree existiert danach
   weiterhin (Verzeichnis und Registrierung), der Run-Ordner des betroffenen Laufs
   bleibt erhalten, das Ergebnis für diesen Lauf ist ein Fehler, und ein weiterer
   prunebarer Lauf im selben Sweep wird wie gewohnt bearbeitet.
10. **Changelog:** `CHANGELOG.md` und `CHANGELOG.de.md` enthalten jeweils
    `## [0.31.2] — Unreleased` mit `### Fixed` und je einem inhaltlich
    übereinstimmenden Punkt zu C1, C2, C3 und C4; die Version in `pyproject.toml`
    ist unverändert `0.31.1`.
11. **Bestand grün:** `uv run ruff check .` und `uv run pytest -x -q` sind grün;
    die bestehenden 1362 Tests bleiben grün. Bestehende Tests werden nur angepasst,
    wo sie das alte Fehlverhalten festschreiben (z. B. den Push-Aufruf mit nacktem
    `--force-with-lease`), und diese Anpassungen sind im Changelog genannt.

Richtwert: 10–16 neue Tests; deutlich mehr gilt als Gold-Plating.

## Contract (Single-Lane)

Nur extern beobachtbare Flächen:

- **CLI:** `adw run|resume|approve` bei belegtem Repo-Lock → Exit 1, Meldung, dass
  in diesem Repo bereits ein ADW-Kommando läuft, keine Zustandsänderung.
- **State:** `state.json` enthält `pushed_shas` (Objekt Branch → SHA, Default `{}`);
  ältere State-Dateien ohne das Feld bleiben ladbar.
- **Lock-Datei:** `.adw/runs/.repo.lock`, wird nie gelöscht.

Keine internen Helper-Signaturen.

## Deferred (bewusst nicht gebaut)

- Feineres Lock nur über die Authoring-Phasen (paralleles Bauen zweier Läufe im
  selben Repo) — E5 serialisiert bewusst ganze Kommandos.
- Blockierendes Warten auf den Repo-Lock oder Anzeige des Lock-Halters.
- Nutzung gepushter SHAs für andere Zwecke (GUI, Kennzahlen).
- Symlink-Prüfungen an weiteren Stellen als den hier genannten.
- Alle Befunde C5–C29.

Diese Punkte sind keine Findings im Review-Loop.

## Definition of Done

- AC 1–11 erfüllt; AC 1–9 durch Regressionstests belegt, die das alte Fehlverhalten
  erkennen.
- `uv run ruff check .` und `uv run pytest -x -q` grün; bestehende Tests grün.
- Changelogs EN/DE synchron aktualisiert, Version unverändert.
- Contract beschränkt auf CLI-Exit/Meldung und `pushed_shas` im State.
- Keine Änderungen außerhalb des Umfangs A1–A5.
