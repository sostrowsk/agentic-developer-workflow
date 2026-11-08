# W13: Jeder Lauf hinterlässt ein Protokoll im zentralen Ledger

Fünfter Lauf der Reihe aus `ONBOARDING-SPEC-PLAN.md` (Paket W13, Entscheidungen
D6 und D9). Setzt auf dem gemergten Stand **0.32.1** (nach R1, R2, W1 und W4) auf. Die
Ausgangslage ist am 2026-11-08 gemessen und vor dem Bauen gegen den dann aktuellen
Stand zu prüfen.

Ziel ist die Datengrundlage für die Selbstverbesserung von ADW: ein
deterministisches Protokoll pro Lauf (0 Tokens), abgelegt in einem zentralen,
privaten Ledger, das den Prune der Run-Ordner überlebt. Bewertung durch ein Modell
(Retro-Agent, W14), Auswertung (`adw kpis`, W3) und Wochenschleife (W15) sind
**nicht** Teil dieses Laufs.

## Ausgangslage (gemessen, 2026-11-08)

### Die Erfahrungen gehen mit dem Prune verloren

`trace.keep_runs` ist 20 (`adw/config.py:112`); Auto-Prune löscht nach jedem
erfolgreichen Lauf die älteren Run-Ordner samt `state.json`, `events.jsonl`,
Spec/Plan/Contract und `followups.md`. Im ADW-Repo liegen noch 16 Run-Ordner; die
Läufe vor 0.22.0 sind bereits gelöscht. Es gibt keine zweite Ablage.

### Die Rohdaten sind vollständig vorhanden

Ein Run-Ordner enthält `state.json`, `events.jsonl` (bzw. `.gz`), `issue.md`,
`spec.md`, `plan.md`, `contract.yaml`, `spec-summary.md`, `plan-summary.md`,
`drafts/`, optional `followups.md`, `escalation.md`,
`authoring-*-known-findings.md` und `trees/`. Größtes `events.jsonl`: 1,9 MB.

Ereignistypen über alle vorhandenen Läufe (Anzahl): `agent.tool.call`/`.result`
3190, `agent.message` 1340, `state.saved` 610, `agent.run` 351, `round` 337,
`gate` 210, `codex.review` 208, `artifact` 201, `phase` 186, `lane` 182,
`snapshot` 181, `ci.poll` 92, `triage.decision` 74, `codex.author` 63, `run` 60,
`commit` 45, `ci.wait` 32, `approval` 23, `followup` 18, `red.check` 15, `log` 5,
`ci.reentry` 1, `circuit_breaker` 0.

Für das Protokoll relevante Payloads (Beispiel Lauf `e7f3819b`):
- `run` start: `issue`, `parallel`, `dry_run`, `repo`, `base_branch`,
  `adw_version`, `lanes`; `run` end: `status`, `totals` (`duration`, `cost`,
  `tokens`). Ein Lauf mit Gate-Pausen hat **mehrere** `run`-Spannen.
- `agent.run` start: `agent`, `model`, `prompt`, `system_append`, … (ab W1 auch
  `max_turns`, `timeout_s`); end: `session_id`, `usage`, `cost_usd`, `is_error`.
- `codex.review` end: `findings`, `parse_ok`.
- `triage.decision`: `finding_key`, `severity`, `action`, `reason`.

### Wo der Export andocken muss

`run`, `resume` und `approve` (`adw/cli.py`) laufen unter dem Repo-Lock aus R1
(`_repo_lock`) und darin unter dem per-Run-Lock (`run_lock`). `_execute` beendet
Eskalation, Gate-Pause und `AgentRunError` mit `typer.Exit` (1 bzw. 2);
`_maybe_auto_prune` wird nur im Erfolgspfad aufgerufen (`adw/cli.py:313`, `:361`,
`:437`), **nach** dem Freigeben des per-Run-Locks. Der Export muss deshalb über
`try/finally` auch die Exit-Pfade abdecken. Löschen geschieht in
`retention._prune_locked` → `_delete_run` (`adw/retention.py:426–440`).

### Das Ledger-Repo existiert

Privates GitLab-Repo `addvendo/adw-ledger` (Entscheidung D6/D9), lokal geklont
unter `~/.adw/ledger` (Remote `git@gitlab.com:addvendo/adw-ledger.git`, Branch
`main`, enthält nur ein `README.md`). Das ADW-Repo selbst ist öffentlich — keine
Lauf-Daten dorthin.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Event-Format, Run-Ordner-Layout und Retention-Regeln bleiben; Prune bekommt
  nur den Export-Schritt vorgeschaltet (E7).
- Die GUI liest weiter die Run-Ordner, nicht das Ledger.
- Kein Retro-Agent, kein `adw rate`/`feedback` (W14), kein `adw kpis` (W3), keine
  Wochenschleife (W15).

## Aufgabe

**A1 — Lauf-Protokoll.** Eine reine Funktion baut aus einem Run-Ordner ein
`run-record.json` (Schema E3).

**A2 — Export ins Ledger.** Nach jedem `run`, `resume` und `approve` (egal ob
`done`, `escalated`, Gate-Pause oder `AgentRunError`) wird der Lauf ins Ledger
exportiert: Protokoll plus Fall-Bündel (E4), Commit im Ledger-Klon, Push
fail-open.

**A3 — Prune exportiert zuerst.** Bevor ein Lauf gelöscht wird, wird er exportiert
(E7).

**A4 — Nachtrag.** Ein Kommando exportiert vorhandene Läufe nachträglich (E8).

**A5 — Doku und Changelog.** `docs/ADW-HANDBOOK.md` und `docs/ADW-HANDBOOK.de.md`
bekommen einen kurzen Abschnitt „Ledger" (Zweck, `ADW_LEDGER`, Layout,
Datenschutz-Hinweis privat); `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue
Sektion `## [0.33.0] — Unreleased` mit `### Added`. Version in `pyproject.toml`
**nicht** ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 — Ort des Ledgers:** Umgebungsvariable `ADW_LEDGER`, Default
  `~/.adw/ledger`. Ist das Verzeichnis kein Git-Repo, wird **nicht** exportiert:
  eine Zeile auf stderr („Ledger nicht gefunden …, Export übersprungen") und ein
  `ledger.export`-Event mit `outcome: "skipped"`. Kein Config-Feld in
  `.adw/config.yaml` (das Ledger ist repo-übergreifend). ADW legt das Ledger nie
  selbst an und klont nie.
- **E2 — Modul:** neues Modul `adw/ledger.py` mit genau zwei öffentlichen
  Funktionen: `build_run_record(repo, run_id) -> dict` (rein lesend, deterministisch)
  und `export_run(repo, run_id) -> str` (Ergebnis `"exported"`, `"unchanged"`,
  `"skipped"` oder `"failed"`). Keine Klassenhierarchie.
- **E3 — Schema `run-record.json`** (`schema_version: 1`), alle Werte aus
  `state.json` und dem Event-Log, fehlende Werte `null`:
  - Kopf: `run_id`, `repo_slug`, `adw_version` (aus dem **ersten** `run`-Start),
    `adw_versions` (alle verschiedenen Versionen über die Spannen), `started_at`,
    `ended_at`, `phase` (aus `state.json`), `dry_run`, `parallel`, `gates`
    (`skip_approval`, `spec_approval`), `issue_title` (erste nicht-leere Zeile
    des Issues ohne `#`).
  - Ausgang: `escalation_reason` (erster Absatz aus `escalation.md` oder `null`),
    `invocations` (Anzahl `run`-Spannen), `approvals` (Anzahl `approval`-Events),
    `duration_s`, `cost_usd`, `tokens` (Summen über die `run`-End-`totals`).
  - Ablauf: `lanes` je Lane `{gate_iterations, red_confirmed}` aus dem State;
    `review_rounds`, `fix_cycles`, `ci_reentries` aus dem State;
    `circuit_breakers` (Anzahl Events).
  - `agents`: Liste je `agent.run`-Spanne `{agent, model, effort, max_turns,
    timeout_s, duration_s, cost_usd, usage, is_error}` (`effort` ist bis W2 `null`).
  - `findings`: Liste je `triage.decision` `{finding_key, severity, action,
    reason, phase, round}` plus je `codex.review`-Ende die Anzahl Findings pro
    Runde (`review_counts: [{phase, round, n}]`).
  - `followups`: Anzahl Einträge in `followups.md` (Zeilen, die mit `- ` beginnen).
  `repo_slug`: `git remote get-url origin` ohne Host-Präfix und `.git`
  (z. B. `sostrowsk/agentic-developer-workflow`), Fallback Verzeichnisname.
- **E4 — Layout im Ledger:** `runs/<repo_slug mit / → __>/<run_id>/` mit
  `run-record.json` (sortierte Schlüssel, Einrückung 2) und dem Fall-Bündel:
  Kopien von `issue.md`, `spec.md`, `plan.md`, `contract.yaml`,
  `spec-summary.md`, `plan-summary.md`, `followups.md`, `escalation.md`,
  `authoring-*-known-findings.md` (nur vorhandene), `state.json`, das Event-Log
  **gzip-komprimiert** als `events.jsonl.gz` (aus `.jsonl` oder bestehender `.gz`),
  und `final.diff` = `git diff <base_sha>..<lane-branch>` für jede Lane mit
  bekanntem `base_sha` und existierendem Branch (fehlt beides → keine Datei). Kein
  `drafts/`, kein `trees/`.
- **E5 — Commit und Push:** Alle Git-Aufrufe über `adw.git.run_git` (Hooks aus,
  Timeouts). `git add -- runs/<slug>/<run_id>`; ist danach nichts gestagt →
  `"unchanged"`, sonst `git commit -m "run <run_id> <phase> (<repo_slug>)"`.
  Danach `git push` mit Timeout 60 s; ein Fehlschlag ist **nicht** fatal (Ergebnis
  bleibt `"exported"`, Warnung auf stderr, Event mit `pushed: false`); der nächste
  Export pusht die liegengebliebenen Commits mit. Kein Pull, kein Rebase, kein
  Force.
- **E6 — Wann und wie fail-open:** Der Export läuft in `run`, `resume` und
  `approve` **nach** Ende der Ausführung (auch bei Eskalation, Pause und
  `AgentRunError`), **vor** dem Auto-Prune, noch unter dem Repo-Lock aus R1. Er
  ändert nie Exit-Code oder Phase des Laufs; jede Ausnahme wird gefangen, auf
  stderr gemeldet und als `ledger.export`-Event mit `outcome: "failed"`
  festgehalten. Dry-Runs werden **nicht** exportiert (`outcome: "skipped"`,
  Grund `dry_run`).
- **E7 — Prune:** Im löschenden Modus ruft `prune` vor dem Löschen eines Laufs
  `export_run`. Ergebnis `"failed"` → der Lauf wird **nicht** gelöscht
  (`skipped`, Grund „Ledger-Export fehlgeschlagen"). Ergebnis `"skipped"` (kein
  Ledger) → Verhalten wie heute (löschen). `--gzip`-Modus: kein Export.
- **E8 — Nachtrag:** `adw ledger export [RUN_ID] --repo <pfad>`; ohne `RUN_ID`
  alle Läufe des Repos in Startreihenfolge. Ausgabe je Lauf eine Zeile mit
  Ergebnis; Exit 1, wenn mindestens einer `"failed"` ist.
- **E9 — Idempotenz:** Ein erneuter Export desselben Laufs überschreibt dessen
  Ordner im Ledger vollständig (vorher löschen, dann schreiben) und erzeugt nur
  bei inhaltlicher Änderung einen Commit. `events.jsonl.gz` wird mit `mtime=0`
  geschrieben, damit gleicher Inhalt gleiche Bytes ergibt.
- **E10:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Tests
  benutzen ein temporäres Ledger-Repo (lokales Bare-Repo als `origin`) über
  `ADW_LEDGER`; kein Test berührt `~/.adw/ledger` oder das Netz.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Retro-Agent, `adw rate`, `adw feedback` (W14); `adw kpis` (W3); Wochenschleife
  (W15).
- Schwärzung/Redaktion von Inhalten (das Ledger ist privat, D6).
- Anlegen oder Klonen des Ledgers durch ADW; Pull/Rebase/Konfliktbehandlung.
- Ledger-Konfiguration in `.adw/config.yaml`.
- Anzeige des Ledgers in der GUI.
- Export der `drafts/` und Snapshot-Refs.

## Akzeptanzkriterien

1. `build_run_record` liefert für einen Fixture-Run-Ordner (abgeleitet aus einem
   echten Lauf mit zwei `run`-Spannen, einem `approval`, zwei Review-Runden und
   `triage.decision`-Events) exakt das erwartete JSON (Golden-File-Test).
2. Fehlende Dateien (`escalation.md`, `followups.md`) und ein `.gz`-Log ergeben
   `null`/0 bzw. werden korrekt gelesen, ohne Ausnahme.
3. Export eines nicht als `dry_run` markierten Fixture-Laufs legt `runs/<slug>/<id>/` mit `run-record.json`,
   `events.jsonl.gz` und den Artefakten an und erzeugt genau einen Commit im
   Test-Ledger, der ins Bare-Repo gepusht ist.
4. Zweiter Export ohne Änderung → `"unchanged"`, kein neuer Commit.
5. Push-Fehler (Remote unerreichbar) → `"exported"`, Commit lokal vorhanden,
   Exit-Code des Laufs unverändert.
6. Eine Ausnahme im Export ändert Exit-Code und Phase des Laufs nicht.
7. Kein Ledger (`ADW_LEDGER` zeigt auf ein Nicht-Repo) → `"skipped"`, Meldung,
   Lauf unverändert.
8. Dry-Run → kein Export.
9. Prune löscht einen Lauf, dessen Export `"failed"` liefert, **nicht**; ohne
   Ledger löscht er wie bisher.
10. `adw ledger export --repo <pfad>` exportiert alle Läufe, Ausgabe je Lauf,
    Exit 1 bei einem Fehlschlag.
11. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 12–16 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen
(`ADW_LEDGER`, Layout und Schema aus E3/E4, CLI `adw ledger export`,
`ledger.export`-Event), keine internen Helper-Signaturen.
