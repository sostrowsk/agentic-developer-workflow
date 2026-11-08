# Spec — W13: Jeder Lauf hinterlässt ein Protokoll im zentralen Ledger

Basis: Stand 0.32.1 (`main`, 9a4e16e). Ausgangslage am 2026-11-08 gegen den Code
geprüft: `trace.keep_runs = 20` (`adw/config.py:112`); `_maybe_auto_prune` nur im
Erfolgspfad von `run`/`resume`/`approve`, nach Freigabe des per-Run-Locks
(`adw/cli.py:313`, `:361`, `:437`); `_execute` beendet Eskalation/Pause/`AgentRunError`
mit `typer.Exit`; `retention._prune_locked` hält den per-Run-Lock, prüft die Phase
erneut und ruft dann `_delete_run` (`adw/retention.py:426–440`); `_delete_run` entfernt
Worktrees und Snapshot-Refs, behält aber den Lane-Branch; `adw.git.run_git(cwd, *args,
timeout=…)` vorhanden; `escalation.md` trägt den Grund unter `## Grund`
(`adw/context.py:195`); `fix_cycles` existiert nur je Lane (`adw/state.py:76`). Ein
Ledger-Modul existiert noch nicht. Zeilenangaben sind Richtwerte.

## Ziel

Jeder ausgeführte Lauf hinterlässt ein deterministisches Protokoll (`run-record.json`,
0 Tokens) plus ein Fall-Bündel in einem zentralen, privaten Git-Ledger, das den Prune
der Run-Ordner überlebt. Das ist die Datengrundlage für spätere Auswertung (W3, W14,
W15), die selbst nicht Teil dieses Laufs ist.

Konkreter Schaden heute: Auto-Prune löscht nach jedem erfolgreichen Lauf ältere
Run-Ordner samt `state.json`, Event-Log, Spec/Plan/Contract und Follow-ups; die Läufe
vor 0.22.0 sind bereits verloren, eine zweite Ablage gibt es nicht.

Exit-Codes und Phasen der Läufe bleiben auch bei Exportfehlern unverändert. Ohne
eingerichtetes Ledger bleibt ADW wie bisher nutzbar.

## Scope

### 1. Modul `adw/ledger.py` (A1, A2, E2)

Neues Modul mit genau zwei öffentlichen Funktionen, keine Klassenhierarchie:

- `build_run_record(repo, run_id) -> dict` — rein lesend, deterministisch, keine
  Schreibzugriffe, keine Netzaufrufe, keine Modellaufrufe (einziger Subprozess:
  `git remote get-url origin` für `repo_slug`). Gleiche Eingangsdaten → gleiches Dict.
- `export_run(repo, run_id) -> str` — Ergebnis genau einer von `"exported"`,
  `"unchanged"`, `"skipped"`, `"failed"`. Wirft nie; jede Ausnahme wird zu `"failed"`
  (Meldung auf stderr, Event, s. §6). Nimmt keine Run-Locks (der Aufrufer hält sie
  ggf. schon, §8).

### 2. Ledger-Ort (E1)

- Pfad aus `ADW_LEDGER`, Default `~/.adw/ledger` (`~` expandiert).
- Ist der Pfad kein Git-Arbeitsverzeichnis (fehlt oder kein Repo): kein Export,
  Ergebnis `"skipped"`, eine Zeile auf stderr (sinngemäß „Ledger nicht gefunden:
  <pfad>, Export übersprungen"), Event mit `reason: "no_ledger"`; im Verzeichnis wird
  nichts angelegt.
- ADW legt das Ledger nie an, klont nie; kein Config-Feld in `.adw/config.yaml`.

### 3. Schema `run-record.json` (E3)

`schema_version: 1`. Alle Werte aus `state.json` und dem Event-Log (`events.jsonl`
oder `events.jsonl.gz`); nicht ermittelbare Einzelwerte sind `null`, fehlende
Ereignisse/Einträge ergeben leere Listen bzw. Zähler `0`. Nicht verfügbare Messwerte
werden nicht als gemessene `0` ausgegeben.

| Gruppe | Schlüssel | Quelle / Regel |
|---|---|---|
| Kopf | `schema_version` | konstant `1` |
| | `run_id` | State |
| | `repo_slug` | `git remote get-url origin` ohne Schema/Host-Präfix und ohne `.git` (`git@host:a/b.git` und `https://host/a/b.git` → `a/b`); ohne verwendbares `origin` der Verzeichnisname des Repos |
| | `adw_version` | `adw_version` des **ersten** `run`-Starts |
| | `adw_versions` | alle verschiedenen `adw_version` der `run`-Starts, in Reihenfolge des ersten Auftretens |
| | `started_at` / `ended_at` | `ts` des ersten `run`-Starts / des letzten `run`-Endes (`null`, wenn die letzte Spanne kein Ende hat) |
| | `phase`, `dry_run`, `parallel` | State |
| | `gates` | `{skip_approval, spec_approval}` aus dem State |
| | `issue_title` | erste nicht-leere Zeile von `issue.md`, führende `#` und Leerraum entfernt |
| Ausgang | `escalation_reason` | erster Absatz unter `## Grund` in `escalation.md` (heutiges Format), sonst erster Absatz, der keine Überschrift ist; ohne Datei `null` |
| | `invocations` | Anzahl `run`-Spannen (Start-Events) — Gate-Pausen beschränken nicht auf die letzte Spanne |
| | `approvals` | Anzahl `approval`-Events |
| | `duration_s`, `cost_usd`, `tokens` | Summen über `totals.duration`/`cost`/`tokens` aller `run`-Enden; Wartezeit zwischen Spannen wird nicht aufgeschlagen; `null`, wenn kein Ende einen Wert liefert |
| Ablauf | `lanes` | je Lane `{gate_iterations, red_confirmed}` aus dem State |
| | `review_rounds`, `ci_reentries` | State (Run-Ebene) |
| | `fix_cycles` | Summe der Lane-`fix_cycles` aus dem State (das Feld existiert nur je Lane) |
| | `circuit_breakers` | Anzahl `circuit_breaker`-Events |
| `agents` | Liste | je `agent.run`-Spanne in Startreihenfolge `{agent, model, effort, max_turns, timeout_s, duration_s, cost_usd, usage, is_error}`; `agent`/`model`/`effort`/`max_turns`/`timeout_s` aus dem Start, `cost_usd`/`usage`/`is_error` aus dem Ende derselben Spanne, `duration_s` = Ende-`ts` − Start-`ts`; `effort` bis W2 `null`; Spanne ohne Ende → Ende-Werte `null` |
| `findings` | Liste | je `triage.decision` `{finding_key, severity, action, reason, phase, round}` (`phase`/`round` aus den Event-Feldern) |
| | `review_counts` | je `codex.review`-Ende `{phase, round, n}` mit `n` = Anzahl `findings` |
| | `followups` | Anzahl Zeilen in `followups.md`, die mit `- ` beginnen; ohne Datei `0` |

Prompts, `system_append`, Session-IDs und Nachrichteninhalte stehen **nicht** im
Protokoll (sie liegen im Event-Log des Bündels). Unlesbare Event-Zeilen
(abgeschnittene Fragmente) werden übersprungen.

### 4. Layout im Ledger (E4, E9)

`runs/<repo_slug mit / → __>/<run_id>/` enthält:

- `run-record.json` — `json.dumps(..., sort_keys=True, indent=2)` plus
  abschließender Zeilenumbruch.
- Kopien der vorhandenen Dateien `issue.md`, `spec.md`, `plan.md`, `contract.yaml`,
  `spec-summary.md`, `plan-summary.md`, `followups.md`, `escalation.md`,
  `authoring-*-known-findings.md`, `state.json`.
- `events.jsonl.gz` — Event-Log gzip-komprimiert mit `mtime=0` (aus `.jsonl` oder
  aus bestehender `.gz` dekomprimiert und neu geschrieben), **ohne**
  `ledger.export`-Events: sonst würde jeder Export das eigene Log verändern und der
  zweite Export nie `"unchanged"` liefern (E9).
- `final.diff` — `git diff <base_sha>..<branch>` je Lane mit bekanntem `base_sha` und
  existierendem Branch, aneinandergehängt in Lane-Namen-Reihenfolge; qualifiziert
  sich keine Lane, entsteht keine Datei.
- Kein `drafts/`, kein `trees/`, keine Snapshot-Refs.

Vor dem Schreiben wird der Zielordner im Ledger vollständig gelöscht (Überschreiben
statt Mischen): inzwischen entfernte Artefakte bleiben nicht als veraltete Kopien
zurück.

### 5. Commit und Push (E5)

Alle Git-Aufrufe über `adw.git.run_git` (Hooks aus, Timeouts) im Ledger-Klon:

1. `git add -- runs/<slug>/<run_id>` (erfasst auch gelöschte Dateien des Ordners).
2. Nichts gestagt → `"unchanged"`, kein Commit, aber Push-Versuch für
   liegengebliebene Commits wie in Schritt 4.
3. Sonst `git commit -m "run <run_id> <phase> (<repo_slug>)"` → `"exported"`.
4. `git push` mit Timeout 60 s. Fehlschlag ist nicht fatal: Ergebnis bleibt
   `"exported"` bzw. `"unchanged"`, Warnung auf stderr, Event mit `pushed: false`.
   Kein Pull, kein Rebase, kein Force.

Der Commit nutzt die Git-Identität des Ledger-Klons; der Export ändert nichts im
Ziel-Repo des Laufs.

### 6. Event `ledger.export`

Punkt-Event im Event-Log des exportierten Laufs über den bestehenden
Event-Mechanismus, nach jedem Exportversuch, Payload `{outcome, pushed, reason}`:

- `outcome`: `"exported"` | `"unchanged"` | `"skipped"` | `"failed"`.
- `pushed`: `true`/`false` nach Push-Versuch, sonst `null`.
- `reason`: bei `"skipped"` `"no_ledger"` oder `"dry_run"`, bei `"failed"` die
  Fehlermeldung, sonst `null`.

Das Event-Schreiben ist fail-open wie jedes Event (auch bei `trace.enabled=false`
oder nicht beschreibbarem Log); ein Logging-Fehler wird nicht zum Lauf-Fehler.

### 7. Andocken an `run`, `resume`, `approve` (A2, E6)

- Sobald eine Ausführung (`_execute`) stattgefunden hat, wird der Lauf exportiert — in
  jedem Ausgang: `done`, Eskalation, Gate-/Spec-Pause, `AgentRunError` und sonstige
  Ausnahme (per `try/finally` um die Ausführung).
- Reihenfolge: nach Ende der `run`-Spanne und Freigabe des per-Run-Locks, **vor**
  `_maybe_auto_prune`, noch unter dem Repo-Lock (`_repo_lock`). Die Voraussetzungen
  für Auto-Prune bleiben unverändert.
- Der Export ändert nie Exit-Code (0/1/2 wie heute) oder Phase des Laufs; eine
  ursprüngliche Ausnahme/`typer.Exit` wird nicht ersetzt.
- Dry-Runs werden nicht exportiert: `"skipped"`, Event mit `reason: "dry_run"`, keine
  Ledger-Dateien, kein Commit, kein Push. Das gilt auch bei Fortsetzung und beim
  Nachtrag (§9).
- Aufrufe, die vor jeder Ausführung abgewiesen werden (Run unbekannt, bereits `done`,
  bereits eskaliert, Vorflug verweigert), exportieren nicht — der Lauf hat sich nicht
  verändert.

### 8. Prune exportiert zuerst (A3, E7)

- Im löschenden Modus ruft `prune` für jeden Lauf, den es löschen würde, in
  `_prune_locked` unter dessen per-Run-Lock und nach der Phasen-Prüfung `export_run`
  auf — unmittelbar vor `_delete_run`, also bevor Worktrees, Snapshot-Refs oder der
  Run-Ordner entfernt werden. Gilt für manuelles `adw runs prune` und Auto-Prune.
- `"failed"` → Lauf wird **nicht** gelöscht: `PruneResult` `skipped` mit Grund
  „Ledger-Export fehlgeschlagen".
- `"exported"`, `"unchanged"`, `"skipped"` (kein Ledger) → Löschen wie heute. Ein
  Push-Fehler allein verhindert die Löschung nicht (Commit liegt lokal).
- `--gzip`-Modus: kein Export.
- `export_run` fordert den bereits gehaltenen per-Run-Lock nicht erneut an (kein
  Selbst-Deadlock). Bestehende Retention-Regeln und Schutzbedingungen gelten weiter.

### 9. CLI `adw ledger export` (A4, E8)

`adw ledger export [RUN_ID] --repo <pfad>` (`--repo` Default wie bei den übrigen
Kommandos):

- Mit `RUN_ID`: exportiert genau diesen Lauf; unbekannte ID → Fehlermeldung, Exit 1.
- Ohne `RUN_ID`: alle Läufe unter `.adw/runs/` in Startreihenfolge (Sortierung wie
  `retention.scan_runs`). Ein `"failed"` bricht die Verarbeitung der übrigen nicht ab.
- Je Lauf eine Zeile `<run_id>: <ergebnis>` auf stdout.
- Exit 1, wenn mindestens ein Ergebnis `"failed"` ist, sonst 0 (auch bei lauter
  `"skipped"`).
- Läuft unter dem Repo-Lock wie `prune`, damit kein gleichzeitiger Lauf exportiert.
- Zielauswahl, Dry-Run-Ausschluss, Bündel, Idempotenz und Push-Verhalten wie beim
  automatischen Export.

### 10. Doku und Changelog (A5)

- `docs/ADW-HANDBOOK.md` und `docs/ADW-HANDBOOK.de.md`: kurzer Abschnitt „Ledger" —
  Zweck, `ADW_LEDGER` (Default, Verhalten ohne Repo), Layout, `adw ledger export`,
  Hinweis: Ledger muss privat sein, es enthält Issues, Prompts und Diffs ungeschwärzt.
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
  `## [0.33.0] — Unreleased` oberhalb von `0.32.1` mit `### Added` (Lauf-Protokoll,
  Ledger-Export, Prune-Anbindung, Nachtragskommando), im Stil der vorhandenen Einträge.
- Version in `pyproject.toml` unverändert.

### Contract (Single-Lane)

Nur extern beobachtbare Flächen, keine internen Helper-Signaturen:

- Umgebungsvariable `ADW_LEDGER` (Default `~/.adw/ledger`; kein Git-Repo → Export
  übersprungen).
- Ledger-Layout `runs/<slug mit __>/<run_id>/` mit den Dateien aus §4 und
  Commit-Nachricht `run <run_id> <phase> (<repo_slug>)`.
- Schema `run-record.json` `schema_version: 1` mit den Schlüsseln aus §3.
- CLI `adw ledger export [RUN_ID] --repo <pfad>`: Ausgabe `<run_id>: <ergebnis>` je
  Lauf, Exit 1 bei mindestens einem `"failed"`.
- Event `ledger.export` mit Payload `{outcome, pushed, reason}`.
- Prune: Kandidat mit fehlgeschlagenem Export → `skipped`, „Ledger-Export
  fehlgeschlagen".

## Non-Goals

- Retro-Agent, `adw rate`, `adw feedback` (W14); `adw kpis` (W3); Wochenschleife (W15);
  keine Bewertung oder Interpretation der Laufqualität.
- Änderungen an Event-Format, Phasen, Gate-Entscheidungen, Run-Ordner-Layout oder
  Retention-Auswahlregeln (außer dem vorgeschalteten Export-Schritt mit dem
  Löschschutz aus E7).
- GUI-Änderungen; die GUI liest weiter die Run-Ordner.
- Schwärzung/Redaktion von Inhalten.
- Anlegen/Klonen des Ledgers, Pull/Rebase/Konfliktbehandlung.
- Config-Feld in `.adw/config.yaml`.
- Lauf-Daten im (öffentlichen) ADW-Repo.
- Zusätzliche persistente Export-Zustände, Marker, Retry-Queues oder
  Hintergrunddienste.
- Versionsänderung in `pyproject.toml`.

## Akzeptanzkriterien

1. **Golden File (Issue-AC 1).** `build_run_record` liefert für einen
   Fixture-Run-Ordner (abgeleitet aus einem echten Lauf, mit zwei `run`-Spannen, einem
   `approval`, zwei `codex.review`-Runden und `triage.decision`-Events) exakt das
   erwartete JSON des Golden Files, inklusive `invocations: 2`, `approvals: 1`,
   summierter `totals`, `adw_versions`, `agents` mit `duration_s` aus den
   Span-Zeitstempeln und `review_counts`.
2. **Robust gegen Lücken (Issue-AC 2).** Ohne `escalation.md` und `followups.md` ist
   `escalation_reason` `null` und `followups` `0`; ein nur als `events.jsonl.gz`
   vorliegendes Log liefert dasselbe Protokoll wie die unkomprimierte Fassung; keine
   Ausnahme.
3. **Export (Issue-AC 3).** Export eines Nicht-Dry-Run-Fixture-Laufs in ein
   Test-Ledger legt `runs/<slug>/<id>/` mit `run-record.json`, `events.jsonl.gz` und
   den vorhandenen Artefakten aus §4 an (kein `drafts/`, kein `trees/`), erzeugt genau
   einen Commit mit Nachricht `run <id> <phase> (<repo_slug>)`, der im Bare-Repo
   (`origin`) angekommen ist; Ergebnis `"exported"`.
4. **Idempotent (Issue-AC 4).** Ein zweiter Export desselben unveränderten Laufs
   liefert `"unchanged"` und keinen neuen Commit — obwohl der erste Export ein
   `ledger.export`-Event ins Log des Laufs geschrieben hat.
5. **Push fail-open (Issue-AC 5).** Ist `origin` des Ledgers unerreichbar, ist das
   Ergebnis `"exported"`, der Commit liegt lokal, stderr enthält eine Warnung, das
   Event trägt `pushed: false`; über die CLI bleibt der Exit-Code des Laufs
   unverändert.
6. **Export-Ausnahme fail-open (Issue-AC 6).** Wirft der Export innerhalb von
   `run`/`resume`/`approve` eine Ausnahme, bleiben Exit-Code und Phase des Laufs wie
   ohne Export; es gibt eine stderr-Meldung und ein Event mit `outcome: "failed"`.
7. **Exit-Pfade (A2, E6).** Ein Lauf, der eskaliert bzw. an einem Gate pausiert, wird
   trotzdem exportiert (Exit-Code 1 bzw. 2 unverändert).
8. **Kein Ledger (Issue-AC 7).** `ADW_LEDGER` zeigt auf ein Verzeichnis ohne Git-Repo
   → `"skipped"`, stderr-Meldung, Event mit `reason: "no_ledger"`, im Verzeichnis wird
   nichts angelegt, Lauf unverändert.
9. **Dry-Run (Issue-AC 8).** Ein Dry-Run wird nicht exportiert (`"skipped"`,
   `reason: "dry_run"`, kein Commit im Ledger).
10. **Prune (Issue-AC 9).** Liefert der Export eines Prune-Kandidaten `"failed"`,
    bleibt dessen Run-Ordner erhalten und das Ergebnis ist `skipped` mit Grund
    „Ledger-Export fehlgeschlagen"; ohne Ledger wird wie bisher gelöscht; mit Ledger
    liegt der gelöschte Lauf danach im Ledger.
11. **Nachtrag (Issue-AC 10).** `adw ledger export --repo <pfad>` exportiert alle
    Läufe, gibt je Lauf eine Zeile `<run_id>: <ergebnis>` aus und endet mit Exit 1,
    wenn mindestens ein Lauf `"failed"` ist, sonst 0.
12. **Doku/Changelog (A5).** Beide Handbücher enthalten den Abschnitt „Ledger", beide
    Changelogs die Sektion `## [0.33.0] — Unreleased` mit `### Added` und inhaltlich
    gleichen Punkten; `pyproject.toml` unverändert.
13. **Gates (Issue-AC 11).** `uv run ruff check .` und `uv run pytest -x -q` grün.

Alle Export-Tests nutzen ein temporäres Ledger-Repo mit lokalem Bare-Repo als `origin`
über `ADW_LEDGER` (Fixture setzt die Variable); kein Test berührt `~/.adw/ledger` oder
das Netz (E10).

Richtwert: 12–16 neue Tests. Deutlich mehr ist Gold-Plating; keine Testmatrix über
alle Event-Typen oder URL-Varianten hinaus.

## Definition of Done

- Akzeptanzkriterien 1–13 erfüllt und (außer 12) durch Tests belegt.
- `adw/ledger.py` hat genau die öffentlichen Funktionen `build_run_record` und
  `export_run`; alle Git-Aufrufe im Ledger laufen über `adw.git.run_git`.
- Exit-Codes, Phasen und Auto-Prune-Verhalten von `run`/`resume`/`approve` sind ohne
  Ledger unverändert; bestehende Tests bleiben grün.
- Beide Handbücher und beide Changelogs synchron; Version in `pyproject.toml`
  unverändert.
- Contract bleibt auf die externen Flächen aus §Contract beschränkt.
- `uv run ruff check .` und `uv run pytest -x -q` grün.

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
