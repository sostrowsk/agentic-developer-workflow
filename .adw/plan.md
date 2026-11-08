# Plan — R4: ADW liest das CI-Ergebnis richtig

Basis: gemergter Stand 0.36.1 (`pyproject.toml` = `0.36.1`). Verbindliche Grundlage ist
`.adw/spec.md`. Single-Lane: `.adw/config.yaml` definiert nur die Lane `backend`, ein
Frontend-Workstream entfällt.

Contract (`.adw/contract.yaml`) bindet ausschließlich das extern beobachtbare Verhalten von
Phase 7 (`done`/Eskalation, Meldungen, Felder im `ci.wait`-End-Payload). Es entstehen keine
HTTP-Routen, Templates oder neuen Event-Typen. Interne Helper-Signaturen (`poll_until`,
`_evaluate`, `_jobs`, `_failed_logs`, …) sind frei.

Vor dem Bauen: Ausgangslage C19–C23 gegen den aktuellen Stand prüfen — `adw/ci.py`
(`_TERMINAL_STATUSES`, `poll_until`, `_evaluate`), `adw/github.py` (`_GREEN_CONCLUSIONS`,
`_workflow_runs`, `_evaluate`, `_jobs`, `_failed_logs`), `adw/phases.py:run_ci_phase`
(`CiTimeoutError`-/`CiError`-Handler, Guard „rot ohne verwertbare Job-Logs").
Zeilenangaben im Issue sind Orientierung, nicht Vertrag.

## Workstream: backend

**Verantwortung:** A1–A6, Tests für AK 1–5, Toolchain (AK 7).
**Dateien:** `adw/ci.py`, `adw/github.py`, `adw/context.py`, `adw/phases.py` (nur
Uhr-Durchreichung, ggf. `manual`-Sonderfall), `tests/test_ci.py`, `tests/test_github.py`,
`tests/test_phases.py`, `CHANGELOG.md`, `CHANGELOG.de.md`.

### B1 — GitHub Staging-Job nur bei `success` grün (A1, AK 1)

- `adw/github.py:_evaluate`: Für Jobs mit `name == cfg.staging_job` gilt nur
  `conclusion == "success"`; `skipped`/`neutral` sind nicht grün.
- `_GREEN_CONCLUSIONS` bleibt unverändert und gilt weiter für die Workflow-Runs (optionaler
  Run `skipped`/`neutral` bleibt grün). Reihenfolge der Prüfung bleibt: erst rote Runs, dann
  Staging.
- Nicht-grüner Staging-Job nimmt den bestehenden roten Staging-Pfad (Sammlung der Run-Logs
  über `_failed_logs`, kein `done`).

### B2 — GitHub Pagination für Runs und Jobs (A2, AK 2)

- `_workflow_runs` und `_jobs` lesen alle Seiten. Empfohlen: Seiten-Schleife über
  `per_page=100&page=N`, bis eine Seite weniger als `per_page` Einträge liefert — analog zur
  bestehenden GitLab-`_jobs`-Schleife in `adw/ci.py` und direkt mit `FakeGh` (eine Antwort je
  Aufruf) testbar. `gh api --paginate --jq '.workflow_runs[]'`/`'.jobs[]'` ist nach E2
  ebenfalls zulässig (dann Zeilen-JSON parsen, Limit über Eintragszahl > 10 × `per_page`).
- Filter `head_sha` bzw. URL-kodierter `branch` bleiben auf jeder Seite erhalten.
- Fest höchstens 10 Seiten je Sammlung. Ist Seite 10 noch voll (Vollständigkeit nicht
  festgestellt) → `CiError` mit Meldung zum Seitenlimit, keine Bewertung der Teildaten. Phase 7
  eskaliert darüber wie heute mit „CI-Monitoring fehlgeschlagen: <Meldung>".
- Erst die vollständige Run-Liste wird auf Abschluss (`is_terminal`) und Farbe geprüft: ein
  laufender Run auf Seite 2 hält das Polling offen, ein roter Run auf Seite 2 macht rot.
- Kein neues Python-Paket. GitLab-Jobs-Pagination unverändert.

### B3 — Monotone Deadline über das ganze Warten (A3, AK 3)

- `adw/ci.py:poll_until`: neuer Keyword-Parameter `clock` (Default `time.monotonic`);
  `deadline = clock() + cfg.timeout`. Nach jedem `fetch()`: finaler Status → `evaluate`,
  auch wenn die Deadline inzwischen überschritten ist. Nicht final und `clock() >= deadline`
  → `CiTimeoutError` wie heute, ohne weiteres Schlafen und ohne weiteren Fetch. Sonst
  `sleep(min(poll_interval, deadline - clock()))`.
- `duration` im `ci.wait`-End-Payload = `clock() - start` beim Verlassen der Spanne
  (Erfolg, Timeout und Fehlerpfad), also Fetch-, Auswertungs- und Schlafzeit. Feldname bleibt
  `duration`; `status` und `polls` bleiben wie heute.
- Durchreichung der Uhr: `poll_ci` (`adw/github.py`) und `poll_pipeline` (`adw/ci.py`)
  bekommen `clock` als Keyword mit Default `time.monotonic`; `RunContext` (`adw/context.py`)
  erhält analog zu `sleep` ein Feld `clock` (Default `time.monotonic`), das `run_ci_phase`
  an beide Poller übergibt. Dry-Run-Fakes (`adw/dry_run.py`) liefern beim ersten Poll einen
  finalen Status und brauchen keine Änderung.
- **Pflicht-Migration bestehender Tests:** Die vorhandenen `FakeSleep` (tests/test_ci.py,
  tests/test_github.py) rücken keine Uhr vor. Mit echter `time.monotonic` würde
  `test_ci_timeout_escalates` (tests/test_phases.py, `FakeGlab("running")` liefert endlos)
  hängen und die Timeout-Tests in test_ci.py/test_github.py liefen, bis der Fake leer ist.
  Daher: `FakeSleep` um eine Fake-Uhr ergänzen (jeder Schlaf rückt sie vor), Timeout-Tests
  übergeben `clock=`, `test_ci_timeout_escalates` setzt `ctx.clock` gekoppelt an `ctx.sleep`.
  Bestehende Erwartungen (`sleep.calls == [60, 30]` bei Budget 90/Intervall 60,
  `sleep.total == 60` nach zwei Polls) bleiben gültig.

### B4 — GitHub Log-Evidenz (A4, AK 4)

- `adw/github.py:_failed_logs`: je Run `gh run view <id> --log-failed` in `try/except
  CiError`; bei Fehler trägt der Run nichts bei, die Schleife läuft weiter, `poll_ci`
  scheitert nicht.
- Überschrift „### Workflow-Run <id> (<conclusion>)" plus Auszug nur, wenn der Log-Inhalt
  nach `strip()` nicht leer ist. Bestehendes Format und `_LOG_EXCERPT_LINES` bleiben.
- Trägt kein Run etwas bei → Rückgabe exakt `""`; der vorhandene Guard in `run_ci_phase`
  („Pipeline rot ohne verwertbare Job-Logs (Pipeline <ID>) — Mensch übernimmt") greift ohne
  Änderung. Reihenfolge der Phase-7-Behandlung (Re-Entry-Budget zuerst) unverändert.

### B5 — GitLab `manual` eskaliert sofort (A5, AK 5)

- `adw/ci.py`: `manual` in `_TERMINAL_STATUSES` aufnehmen.
- `_evaluate` liefert für `pipeline["status"] == "manual"` kein Grün und keinen Log-Auszug,
  sondern löst die Eskalation aus. Empfohlen: eigene `CiError`-Unterklasse, die der
  bestehende `CiError`-Handler in `run_ci_phase` zur Eskalation macht (Meldung dann mit dem
  vorhandenen Präfix „CI-Monitoring fehlgeschlagen: "). Ein direkter Pfad ist zulässig;
  beobachtbar ist die Meldung, kein Log-Analyst, kein Re-Entry, keine Timeout-Meldung.
- Jobname aus den Pipeline-Jobs (bestehendes `_jobs`) mit `status == "manual"`; Meldung
  „Pipeline wartet auf einen manuellen Job (<Name>) — Mensch übernimmt". Kein manueller Job
  ermittelbar (leer oder Job-Abfrage schlägt fehl) → „Pipeline wartet auf einen manuellen Job
  — Mensch übernimmt" ohne Klammer.
- Nach dem ersten Fetch mit `manual` kein weiterer Fetch und kein Schlafen. SHA-Bindung
  unverändert.

### B6 — Changelog (A6, AK 6)

- `CHANGELOG.md` und `CHANGELOG.de.md`: oberhalb von `## [0.36.1] — 2026-11-08` neue Sektion
  `## [0.36.2] — Unreleased` mit `### Fixed`, Format wie die bestehenden Sektionen
  (ein Fett-Lead, darunter je ein Eintrag zu C19–C23: Staging-Grün, GitHub-Pagination,
  Zeitbudget, Log-Evidenz, GitLab `manual`). Inhaltlich synchron EN/DE.
- `pyproject.toml` bleibt `0.36.1`; kein neues Python-Paket.

### Tests (Richtwert 7–10 neue Tests; Parametrisierungen zählen als ein Test)

Vorhandene Fakes (`FakeGh`, `FakeGlab`, `FakeSleep`, `FakeGlab`/`FakeGhPhases` in
test_phases.py) wiederverwenden. Zeitfälle mit Fake-Uhr und simuliertem Schlaf; kein Netz,
kein echtes Warten. Für `duration` reicht ein kleiner Test-Emitter, der die `ci.wait`-Spanne
(Handle mit `id` und `end_payload`) aufzeichnet.

`tests/test_github.py`
1. Staging-Job `skipped`/`neutral` (parametrisiert) bei sonst grünen Runs → `passed False`,
   Log-Pfad (`gh run view … --log-failed` wird aufgerufen).
2. Staging-Job `success` plus optionaler Run `completed/skipped` → `passed True`.
3. Staging-Job erst auf Jobs-Seite 2 → `passed True`, keine Meldung „existiert in keinem
   Workflow-Run"; der zweite Jobs-Aufruf trägt `page=2`.
4. Runs auf Seite 2 (parametrisiert): roter Run → rot; `in_progress` → weiter pollen.
   `head_sha`-Filter in jedem Runs-Aufruf.
5. Mehr als 10 Seiten (Runs oder Jobs) → `CiError`, Meldung nennt das Seitenlimit, kein
   `done`.
6. Roter Run mit leerem bzw. Whitespace-Log → `log_excerpt == ""`.
7. Zwei rote Runs, erster `gh run view` wirft `CiError` → kein Fehler aus `poll_ci`, Auszug
   enthält Überschrift und Log des zweiten Runs.

`tests/test_ci.py`
8. Budget 1 s, Fake-Uhr: zwei Fetches à 0,6 s, Status nie final → `CiTimeoutError` nach
   Fetch 2, genau zwei Fetches, ein Schlaf ≤ 0,4 s, kein Schlaf nach dem Timeout,
   `ci.wait.end_payload["duration"]` = gemessene Fake-Gesamtzeit.
9. Finaler Status nach überschrittener Deadline (Fetch dauert länger als das Budget) →
   ausgewertet, kein Timeout.
10. Pipeline `manual` (parametrisiert: manueller Job vorhanden / keiner) → Eskalation mit
    Meldung inkl. Name bzw. ohne Klammer; genau ein Pipeline-Fetch, kein Schlaf.

`tests/test_phases.py`
11. Phase 7 GitLab `manual` → `EscalationError` mit der E5-Meldung, keine
    `log_analyst`-Aufrufe, `ci_reentries == 0`, Phase `escalated`, keine Timeout-Meldung.

Bereits abgedeckt, kein neuer Test: Guard „rot ohne verwertbare Job-Logs" auf Phase-Ebene
(`test_ci_failure_without_logs_escalates_directly`). Bestehende Timeout-Tests werden nur auf
die Fake-Uhr umgestellt (B3), nicht dupliziert. Keine Statusmatrix, keine Tests für
Deferred-Punkte.

### Gates (AK 7)

- `uv run ruff check .` und `uv run pytest -x -q` grün.

## Erhaltene Grenzen (Non-Goals)

Ablauf von Phase 7 (Push, Polling, Log-Analyst, ein Re-Entry, dann Mensch) bleibt; einzige
Ausnahme ist die geforderte direkte Eskalation bei GitLab `manual`. Bewertung optionaler
GitHub-Runs (`skipped`/`neutral` grün), Bedeutung und Defaults von `ci.timeout`,
`ci.poll_interval`, `ci.staging_job`, SHA-Bindung, Forge-Erkennung und GitLab-Jobs-Pagination
bleiben unverändert. Kein Versionsbump, keine Stabilitätszusagen für interne Signaturen.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Automatisches Auslösen manueller Jobs.
- Bewertung weiterer Spezialstatus (`waiting_for_resource`, `scheduled`, `preparing`) über
  ihr heutiges Verhalten hinaus.
- Mehrere Staging-Jobs, Job-Matching per Muster.
- Konfigurierbares Seitenlimit für die Pagination (fest 10).
- Hartes Abbrechen eines laufenden `gh`/`glab`-Aufrufs durch die Polling-Deadline; der
  bestehende CLI-Timeout (`_CLI_TIMEOUT = 120` s) bleibt der Backstop.
- Retry-, Cache- oder Persistenzmechanismen für fehlende Fehler-Logs.
