# Plan — W13: Jeder Lauf hinterlässt ein Protokoll im zentralen Ledger

Maßgeblich ist `.adw/spec.md` (Scope 1–10, AC 1–13). Single-Lane-Projekt
(`.adw/config.yaml`): nur der Workstream **backend**; er umfasst Code, Tests, beide
Handbücher und beide Changelogs. Kein Frontend-Workstream.

Basis: Stand **0.32.1** (`9a4e16e`). Die Code-Stellen der Spec sind am 2026-11-08 geprüft
und stimmen: `_maybe_auto_prune`-Aufrufe `adw/cli.py:313`, `:361`, `:437` (außerhalb des
`_run_execution_lock`, innerhalb `_repo_lock`); `_execute` `adw/cli.py:743` (alle
Abbruchpfade enden in `typer.Exit`); `_run_span` `adw/cli.py:147` (Start-Payload mit
`adw_version`, Ende-Payload `{status, totals: {duration, cost, tokens}}`);
`retention._prune_locked` `adw/retention.py:426–440`; `retention.scan_runs` sortiert
`(date, run_id)` aufsteigend; `adw.git.run_git(cwd, *args, timeout, env=None)`
`adw/git.py:16`; Event-Records `{ts, type, kind, span, parent, …, payload}`
(`adw/events.py:196`), `kind` ∈ `start`/`end`/Punkt; `agent.run`-Start-Payload
`adw/agents.py:198` (`agent`, `model`, `max_turns`, `timeout_s`; kein `effort`), Ende mit
`usage`, `cost_usd`, `is_error`; Lane-Felder `branch`, `base_sha`, `gate_iterations`,
`fix_cycles`, `red_confirmed` (`adw/state.py:69–89`); `--repo` hat bei `runs list/prune`
den Default `Path(".")` und läuft durch `_require_repo`. `adw runs prune` nimmt heute
**keinen** `_repo_lock` — `adw ledger export` nimmt ihn trotzdem (Spec §9: kein
gleichzeitiger Lauf exportiert). Vor dem Bauen gegen den dann aktuellen Stand prüfen.

Betroffene Dateien (abschließend): neu `adw/ledger.py`; `adw/cli.py`, `adw/retention.py`;
Tests unter `tests/` (neu `tests/test_ledger.py`, Fixture-Daten unter
`tests/fixtures/ledger/`, `tests/conftest.py`, `tests/test_retention.py`);
`docs/ADW-HANDBOOK.md`, `docs/ADW-HANDBOOK.de.md`, `CHANGELOG.md`, `CHANGELOG.de.md`.
`adw/state.py`, `adw/events.py`, `adw/config.py`, GUI und `pyproject.toml` (bleibt
`0.32.1`) unverändert.

Contract: `.adw/contract.yaml` pinnt nur die extern beobachtbaren Flächen aus §Contract der
Spec. Helfernamen und -aufteilung in diesem Plan sind Empfehlungen.

### Zwei Hinweise zur Umsetzung (kein Abweichen vom Spec-Wortlaut)

1. **`approvals`** zählt gemäß Issue E3 und Spec §3 **alle** `approval`-Events, unabhängig
   vom `payload.event` (`awaited`/`granted`, `adw/cli.py:430`, `:737`, `:765`, `:777`).
   Ein kompletter Gate-Durchlauf ergibt damit 2. Der Golden-Fixture aus AC 1 („einem
   approval") enthält genau **ein** `approval`-Event und erwartet `approvals: 1`.
2. **Event bei gzip-only-Log.** Spec §6 verlangt ein `ledger.export`-Event nach jedem
   Exportversuch. Liegt im Run-Ordner nur `events.jsonl.gz` (kein `events.jsonl`), darf
   kein neues `events.jsonl` entstehen: `_compress_run` behandelt bei Koexistenz die
   `.jsonl` als maßgeblich und legt sie über die `.gz` (`adw/retention.py:375–376`,
   `:386–415`) — Verlust des Verlaufs beim nächsten `--gzip`-Prune. Das Event wird in
   diesem Fall als **neues gzip-Member an `events.jsonl.gz` angehängt** (B2.6).
   `gzip.open(path, "rb")` liest mehrteilige gzip-Dateien transparent; `_iter_events`,
   `_is_valid_gzip` (`adw/retention.py:84–91`, `:107–114`) und der Ledger-Export selbst
   lesen das Event damit ohne Code-Änderung. `adw/events.py` bleibt unverändert.

## Workstream backend

### B1 — `adw/ledger.py`: `build_run_record` (Scope 1, 3; AC 1, 2)

Genau zwei öffentliche Funktionen im Modul; alles andere `_privat`, keine Klassen. Das Modul
importiert **nicht** `adw.retention` (retention importiert ledger, B4) und nicht `adw.cli`.

1. Eingaben lesen: `state.json` über `RunState.load(repo, run_id)` (bzw. als JSON, falls
   das für Rohfelder einfacher ist); Event-Log aus `events.jsonl`, sonst aus
   `events.jsonl.gz` (gzip). Zeilen einzeln `json.loads`, unlesbare/nicht-Dict-Zeilen
   überspringen. Ein gemeinsamer Helfer `_read_events(run_dir) -> list[dict]` dient auch
   B2 (dort werden die Rohzeilen gebraucht, s. B2.4).
2. Spannen: Start und Ende über die `span`-ID paaren (`kind == "start"`/`"end"`),
   Reihenfolge = Reihenfolge der Starts im Log.
3. Felder exakt nach Spec-Tabelle §3; Hinweise:
   - `repo_slug`: einziger Subprozess, `run_git(repo, "remote", "get-url", "origin",
     timeout=10)`; Rückgabecode ≠ 0, leere Ausgabe oder Fehler → `repo.resolve().name`.
     Normalisierung: `.git`-Suffix ab, dann `scheme://host/` bzw. `user@host:` abtrennen.
   - `adw_version`/`adw_versions`/`started_at`/`ended_at`/`invocations` aus den
     `run`-Spannen; `ended_at` = `ts` des Endes der **letzten** Spanne, sonst `null`.
   - `duration_s`/`cost_usd`/`tokens`: Summe über die `totals`-Werte vorhandener
     `run`-Enden, je Messwert getrennt; liefert kein Ende einen Zahlenwert → `null`
     (nicht `0`). Wartezeit zwischen Spannen wird nicht aufgeschlagen.
   - `approvals`: Anzahl aller Records mit `type == "approval"` (Hinweis 1).
   - `issue_title`: erste nicht-leere Zeile von `issue.md`, `lstrip("#").strip()`; ohne
     Datei `null`.
   - `escalation_reason`: Absatz = zusammenhängende nicht-leere Zeilen; zuerst der erste
     Absatz nach einer Zeile `## Grund`, sonst der erste Absatz, der nicht mit `#`
     beginnt; Zeilen mit `"\n"` verbunden, `strip()`; ohne Datei `null`.
   - `lanes`: `{name: {gate_iterations, red_confirmed}}`; `fix_cycles` = Summe der
     Lane-`fix_cycles` (es gibt kein Run-Level-Feld).
   - `agents`: je `agent.run`-Spanne; `effort` konstant `null`; `duration_s` =
     Differenz der ISO-`ts` (Format `YYYY-MM-DDTHH:MM:SS.mmmZ`) in Sekunden, auf 3
     Nachkommastellen gerundet (deterministisch, kein Float-Rauschen); Spanne ohne Ende →
     `duration_s`/`cost_usd`/`usage`/`is_error` `null`. `usage` wird unverändert aus dem
     End-Payload übernommen (kein neues Usage-Schema).
   - `findings`: je `triage.decision` `{finding_key, severity, action, reason}` aus dem
     Payload, `phase`/`round` aus den Record-Feldern; Log-Reihenfolge.
   - `review_counts`: je `codex.review`-**Ende** `{phase, round, n=len(payload.findings)}`
     (`phase`/`round` aus dem Record; fehlt die `findings`-Liste → `n = 0`, wie Spec §3
     „fehlende Einträge ergeben Zähler 0").
   - `followups`: Zeilen von `followups.md` mit Präfix `- `; ohne Datei `0`.
4. Keine Prompts, `system_append`, Session-IDs oder Nachrichteninhalte im Dict.
5. Rein lesend: keine Datei-, Log- oder Git-Schreibzugriffe.

### B2 — `adw/ledger.py`: `export_run` (Scope 1, 2, 4, 5, 6; AC 3–5, 8, 9)

`export_run(repo, run_id) -> str`, wirft nie (äußeres `try/except Exception` →
`"failed"`, Meldung auf stderr). Nimmt keine Locks. Ablauf:

1. State laden; Fehler → `"failed"`. `state.dry_run` → `"skipped"`, `reason "dry_run"`,
   nichts im Ledger (gilt damit automatisch für `resume`/`approve`, Prune und Nachtrag).
2. Ledger-Pfad: `Path(os.environ.get("ADW_LEDGER", "~/.adw/ledger")).expanduser()`.
   Kein Verzeichnis oder `run_git(ledger, "rev-parse", "--show-toplevel")` ≠ 0 bzw.
   Toplevel ≠ Pfad → `"skipped"`, eine stderr-Zeile „Ledger nicht gefunden: <pfad>,
   Export übersprungen", `reason "no_ledger"`; nichts anlegen.
3. Zielordner `ledger/runs/<slug mit / → __>/<run_id>`: sicherstellen, dass er
   (aufgelöst) unter `ledger/runs/` liegt (Slug stammt aus einer URL; vor `rmtree` nötig),
   sonst `"failed"`. Existiert er, `shutil.rmtree`, dann neu anlegen (Überschreiben statt
   Mischen — verschwundene Artefakte bleiben nicht als veraltete Kopien zurück).
4. Schreiben: `run-record.json` (`json.dumps(..., sort_keys=True, indent=2) + "\n"`);
   vorhandene Artefakte aus Spec §4 per `shutil.copyfile` (inkl. Glob
   `authoring-*-known-findings.md`); `events.jsonl.gz` = Rohzeilen des Logs **ohne**
   Records mit `type == "ledger.export"`, unverändert als Text wieder verbunden, dann
   `gzip.compress(data, mtime=0)` (kein Dateiname im Header). Ohne Log keine Datei.
   `final.diff`: je Lane in `sorted(state.lanes)` mit `base_sha` und existierendem Branch
   (`run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")`)
   `run_git(repo, "diff", f"{base_sha}..{branch}")`, Ausgaben aneinanderhängen; keine
   Lane → keine Datei. Kein `drafts/`, kein `trees/`. Das Ziel-Repo wird nicht verändert.
5. Git im Ledger, alle über `run_git` mit Timeout (Empfehlung 30 s, Push 60 s):
   `add -- runs/<slug>/<run_id>` (erfasst auch Löschungen); dann
   `diff --cached --quiet -- runs/<slug>/<run_id>`: Rückgabe 0 → `"unchanged"`, sonst
   `commit -m "run <run_id> <phase> (<repo_slug>)"` → `"exported"`. Nicht-null-Rückgabe
   von `add`/`commit` → Ausnahme → `"failed"`. Danach in **beiden** Fällen `push` (60 s),
   damit liegengebliebene Commits nachgeholt werden; Rückgabe ≠ 0 oder Timeout/OSError →
   stderr-Warnung, `pushed = False`, Ergebnis bleibt. Kein Pull/Rebase/Force.
6. Event `ledger.export` `{outcome, pushed, reason}` (Spec §6) nach **jedem** Versuch,
   auch im `failed`-Pfad; `enabled` aus `AdwConfig.load(repo).trace.enabled`, bei
   Ladefehler `True`; `False` → kein Event. Das Schreiben steht in einem eigenen
   `try/except` (fail-open, Fehler nur als Warnung auf stderr). Zwei Wege je Log-Zustand:
   - `events.jsonl` vorhanden (oder gar kein Log): frischer
     `EventEmitter(repo, run_id, enabled=…)`, Punkt-Event ohne Span (`emitter.emit`).
   - nur `events.jsonl.gz` vorhanden (Hinweis 2): privater Helfer baut den Record in der
     Form von `EventEmitter._append` (`{seq, ts, type: "ledger.export", kind: "point",
     span: null, parent: null, phase: null, lane: null, round: null, payload}`,
     `seq` = höchstes `seq` der lesbaren Records + 1, `ts` wie `_utc_millis`) und hängt
     ihn mit `gzip.open(gz, "ab")` als eigenes gzip-Member plus `\n` an. Keine neue
     `.jsonl`. Der Ledger-Export rekonstruiert `events.jsonl.gz` ohnehin aus allen
     dekomprimierten Zeilen ohne `ledger.export` (B2.4), also bleibt der zweite Export
     `"unchanged"` (E9).
7. Rückgabe des Ergebnisses.

### B3 — Andocken an `run`/`resume`/`approve` (Scope 7; AC 5–7, 9)

Datei: `adw/cli.py`.

1. Kleiner Helfer `_export_after_execution(repo, run_id)`: ruft `ledger.export_run` in
   `try/except Exception` (zweite Sicherung, Meldung auf stderr), Ergebnis wird
   verworfen.
2. In allen drei Kommandos den Block `with _run_execution_lock(...)` in `try/finally`
   einschließen; ein lokales Flag `executed` wird unmittelbar vor `_execute(ctx)` auf
   `True` gesetzt. Im `finally`: `if executed: _export_after_execution(repo, run_id)`.
   Damit läuft der Export nach Ende der `run`-Spanne und nach Freigabe des per-Run-Locks,
   unter `_repo_lock`, vor `_maybe_auto_prune`. Ein `typer.Exit`/eine Ausnahme aus
   `_execute` läuft unverändert weiter; eine Export-Ausnahme ersetzt sie nie.
3. `_maybe_auto_prune` bleibt außerhalb des `finally` im bisherigen Erfolgspfad; Pausen
   und Fehler lösen keinen zusätzlichen Auto-Prune aus.
4. Abweisungen vor `_execute` (unbekannt, `done`, `escalated`, falsche Phase bei
   `approve`, Vorflug, Repo-Lock belegt) setzen das Flag nicht → kein Export.
5. Dry-Run: `export_run` liefert `"skipped"` (B2.1); keine Sonderbehandlung in der CLI.

### B4 — Prune exportiert zuerst (Scope 8; AC 10)

Datei: `adw/retention.py`, `_prune_locked`.

Nach der Phasen-Prüfung, nur im löschenden Modus (der `gzip_mode`-Zweig bleibt davor und
exportiert nicht), unmittelbar vor `_delete_run`:
`if ledger.export_run(repo, rec.run_id) == "failed": return PruneResult(rec.run_id,
"skipped", reason="Ledger-Export fehlgeschlagen")`. Run-Ordner, Worktrees und
Snapshot-Refs bleiben dann erhalten. `"exported"`, `"unchanged"`, `"skipped"` → Löschen
wie heute (ein Push-Fehler allein blockiert nicht). Gilt automatisch für manuelles
`adw runs prune` und Auto-Prune. `export_run` nimmt keinen Lock (kein Selbst-Deadlock
unter dem gehaltenen per-Run-Lock). Keine weitere Änderung an Auswahl, Schutzbedingungen
oder Ergebnisformaten.

### B5 — CLI `adw ledger export` (Scope 9; AC 11)

Datei: `adw/cli.py`, neben `runs_app`: `ledger_app = typer.Typer(...)`,
`app.add_typer(ledger_app, name="ledger")`, Kommando `export`.

1. `--repo` wie bei `runs list/prune` (`Path(".")`, dann `_require_repo(repo)`); dann
   `with _repo_lock(repo):`.
2. Mit `RUN_ID`: Existenz wie `_load_state` prüfen (unbekannt → `_fail`, Exit 1).
   Ohne: `retention.scan_runs(repo)` in derselben Startreihenfolge wie Retention
   (Sortierung `(date, run_id)` aufsteigend).
3. Je Lauf `export_run`, Zeile `<run_id>: <ergebnis>` auf stdout; kein Abbruch bei
   `"failed"`. Am Ende Exit 1, wenn ein Ergebnis `"failed"`, sonst 0 (auch bei lauter
   `"skipped"`).

### B6 — Doku und Changelog (Scope 10; AC 12)

- Beide Handbücher: Abschnitt „Ledger" (EN-Datei englisch, DE-Datei deutsch, inhaltlich
  gleich): Zweck; `ADW_LEDGER` mit Default `~/.adw/ledger` und Verhalten ohne Repo
  (übersprungen, nichts angelegt; einmalig selbst anlegen/klonen); Layout
  `runs/<slug>/<run_id>/`; Export in jedem Ausgang, Prune exportiert zuerst und löscht
  bei Fehlschlag nicht; `adw ledger export [RUN_ID]`; Warnung: Ledger muss privat sein
  (Issues, Prompts, Diffs ungeschwärzt).
- `CHANGELOG.md`/`CHANGELOG.de.md`: `## [0.33.0] — Unreleased` oberhalb `0.32.1`,
  `### Added` mit den vier Punkten der Spec (Lauf-Protokoll, Ledger-Export,
  Prune-Anbindung, Nachtragskommando), im Stil der vorhandenen Einträge.
- `pyproject.toml` unverändert.

## Reihenfolge

B1 → B2 → B3 und B4 (beide brauchen `export_run`) → B5 → B6. Jeden neuen Test zuerst rot
sehen.

## Tests (Richtwert 12–16 neue Tests; geplant 15)

Hermetik (E10):
- **autouse**-Fixture in `tests/conftest.py` (Muster `_isolate_registry`): `ADW_LEDGER`
  auf einen nicht existierenden Pfad unter `tmp_path_factory` — kein bestehender Test
  (Prune, Auto-Prune, CLI) berührt je `~/.adw/ledger`.
- Fixture `ledger` (in `tests/test_ledger.py`): Bare-Repo + Klon mit `user.email`/`name`,
  Initial-Commit per `push -u origin HEAD` (Upstream gesetzt), `ADW_LEDGER` auf den Klon.
- Ziel-Repo: bestehendes `target_repo`, mit `git remote add origin
  git@example.com:acme/widget.git` für einen festen Slug.
- CLI-Ausgänge (AC 5–7) ohne echte Agenten: `cli._execute` per `monkeypatch` durch eine
  Funktion ersetzen, die die gewünschte Phase speichert und `typer.Exit(1)`/`(2)` wirft
  bzw. normal zurückkehrt; Lauf mit `dry_run=False` über `CliRunner`. Vorflug ggf. wie in
  bestehenden CLI-Tests neutralisieren.

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 1 | Golden File: Fixture-Run-Ordner (zwei `run`-Spannen mit Versionen/Totals, genau ein `approval`-Event → `approvals: 1`, zwei `codex.review`-Runden, `triage.decision`, `agent.run`-Spannen, eine unlesbare Zeile) → Dict == `expected-run-record.json` | `tests/test_ledger.py`, Daten `tests/fixtures/ledger/` |
| 2 | Ohne `escalation.md`/`followups.md`: `escalation_reason` `null`, `followups` `0`; dieselben Events nur als `.gz` → identisches Dict | dto. |
| 2/4 (gzip-only) | Export eines Laufs mit nur `events.jsonl.gz`: `ledger.export`-Event steht danach im `.gz` (über `retention._iter_events` lesbar, `_is_valid_gzip` wahr), kein `events.jsonl` entstanden; zweiter Export → `"unchanged"` | dto. |
| 3 | Export in Test-Ledger: Dateien aus §4 vorhanden (inkl. `final.diff` in Lane-Reihenfolge), kein `drafts/`/`trees/`, genau ein neuer Commit mit Nachricht, im Bare-Repo angekommen, `"exported"` | dto. |
| 4 | Zweiter Export → `"unchanged"`, kein neuer Commit, obwohl ein `ledger.export`-Event im Log steht | dto. |
| 5 | `origin` des Ledgers auf unerreichbaren Pfad → `"exported"`, Commit lokal, stderr-Warnung, Event `pushed: false` | dto. |
| 8 | `ADW_LEDGER` auf leeres Verzeichnis ohne Repo → `"skipped"`, stderr, Event `reason: "no_ledger"`, Verzeichnis bleibt leer | dto. |
| 9 | Dry-Run-State → `"skipped"`, `reason: "dry_run"`, kein Commit | dto. |
| 7 | CLI `run` mit Eskalation (Exit 1) bzw. `approve`/`resume` mit Gate-Pause (Exit 2), parametrisiert → Exit-Code wie ohne Export, Lauf im Ledger | dto. |
| 6 | CLI-Lauf, `ledger.build_run_record` wirft → Exit-Code und Phase unverändert, stderr-Meldung, Event `outcome: "failed"` | dto. |
| 5 (CLI) | CLI-Lauf `done` mit unerreichbarem Ledger-`origin` → Exit 0 | dto. (ggf. als Parameter von AC 7) |
| 10 | Prune: Export eines Kandidaten scheitert (z. B. `build_run_record` gepatcht) → Ordner bleibt, `skipped`/„Ledger-Export fehlgeschlagen" | `tests/test_retention.py` |
| 10 | Prune ohne Ledger löscht wie bisher; mit Ledger liegt der gelöschte Lauf danach im Ledger; `--gzip` exportiert nicht | dto. |
| 11 | `adw ledger export --repo` über drei Läufe (einer scheitert) → je Zeile `<id>: <ergebnis>`, Exit 1; ohne Fehlschlag Exit 0 | `tests/test_ledger.py` |
| 11 | `adw ledger export <unbekannt>` → Exit 1 | dto. |
| 12 | Doku/Changelogs: kein Test, Prüfung im Review | — |
| 13 | `uv run ruff check .`, `uv run pytest -x -q` | Gate |

Bestandstests: Durch das neue `ledger.export`-Event (auch bei `"skipped"`) hängt nach
jedem `run`/`resume`/`approve` ein Punkt-Event hinter dem `run`-Ende. Tests, die eine
vollständige Event-Folge oder „letztes Event = `run`-Ende" pinnen, und GUI-Projektionen,
die den Laufstatus aus dem letzten Event ableiten, gegen diesen Fall prüfen. Bricht ein
Bestandstest nur deshalb, die Assertion um `ledger.export` filtern — Verhalten
(Event-Format, GUI) nicht ändern. Keine Testmatrix über URL-Varianten oder Event-Typen
hinaus; keine Zusatztests für offene Spannen, Push-Wiederholung oder Event-Schreibfehler
jenseits der ACs.

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); bestehende Suite grün.
- AC 1–13 erfüllt, außer 12 durch Tests belegt.
- `adw/ledger.py` hat genau `build_run_record` und `export_run` öffentlich; alle Git-Aufrufe
  im Ledger über `adw.git.run_git`.
- Exit-Codes, Phasen und Auto-Prune von `run`/`resume`/`approve` ohne Ledger unverändert.
- Handbücher und Changelogs EN/DE synchron; `pyproject.toml` unverändert.
- Contract auf die externen Flächen aus §Contract der Spec beschränkt.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Retro-Agent, `adw rate`, `adw feedback` (W14); `adw kpis` (W3); Wochenschleife (W15).
- Schwärzung/Redaktion (das Ledger ist privat, D6).
- Anlegen/Klonen des Ledgers, Pull/Rebase/Konfliktbehandlung, Retry-Schleifen für Push
  (der nächste Export pusht liegengebliebene Commits mit).
- Ledger-Konfiguration in `.adw/config.yaml`.
- Anzeige des Ledgers in der GUI.
- Export von `drafts/`, `trees/` und Snapshot-Refs.
- Persistente Exportmarker, Retry-Queues, Hintergrunddienste: lokale Ledger-Commits,
  spätere Exportaufrufe und vorhandene Logs sind die Backstops.
- Export bei abgewiesenen Aufrufen ohne Ausführung (Lauf unverändert; der Nachtrag
  `adw ledger export` deckt jeden Bedarf ab).
- Eigene Sperre für das Ledger über Repo-Grenzen hinweg (zwei Repos exportieren
  gleichzeitig): seltener Fall, ein kollidierender `git commit` endet als `"failed"`,
  der Prune löscht dann nicht, der nächste Export holt nach.
- Schema-Migration oder Neuberechnung alter Protokolle bei künftigen
  `schema_version`-Änderungen.
