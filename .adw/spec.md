# Spec — R4: ADW liest das CI-Ergebnis richtig

Basis: gemergter Stand 0.36.1 (`pyproject.toml` = `0.36.1`). Die Ausgangslage C19–C23 ist am
2026-11-08 im Code bestätigt (`adw/ci.py`, `adw/github.py`, `adw/phases.py:run_ci_phase`) und
vor dem Bauen gegen den dann aktuellen Stand zu prüfen; Zeilenangaben im Issue sind
Orientierung, nicht Vertrag.

Contract: Single-Lane. Vertrag sind ausschließlich extern beobachtbare Ergebnisse von Phase 7
(`done`, Eskalation, Meldungen, Felder im `ci.wait`-End-Payload). Interne Helper-Signaturen
(`poll_until`, `_evaluate`, `_jobs`, `_failed_logs`, …) sind kein Contract.

## Ziel

Phase 7 urteilt korrekt über `done` vs. Eskalation und hält `ci.timeout` ein:

- Ein übersprungener Staging-Job ist bei GitHub nicht grün.
- Jobs und Workflow-Runs auf Folgeseiten werden bei GitHub gesehen und bewertet.
- Das Zeitbudget gilt für die gesamte Wartezeit, nicht nur für die Schlafzeit.
- Nur echter Log-Inhalt zählt bei GitHub als Evidenz; ein log-loser Run bricht das Sammeln
  nicht ab.
- Eine an einem manuellen Job hängende GitLab-Pipeline eskaliert sofort mit klarer Meldung
  statt bis zum Timeout zu warten.

## Scope

- **A1 (C19, E1) — GitHub Staging-Job:** Für Jobs mit `name == cfg.staging_job` gilt nur
  `conclusion == "success"` als grün; `skipped` und `neutral` sind nicht grün. Die Menge
  `_GREEN_CONCLUSIONS` (`success`, `skipped`, `neutral`) bleibt unverändert für die Bewertung
  der Workflow-Runs. Ein nicht-grüner Staging-Job nimmt denselben Pfad wie heute ein roter
  Staging-Job (Log-Auszug der Runs, dann die bestehende Phase-7-Behandlung).
- **A2 (C20, E2) — GitHub Pagination:** Jobs je Run und Workflow-Runs werden über alle Seiten
  gelesen. Zulässig sind die beiden Varianten aus E2: `gh api --paginate` mit `--jq` auf
  `.jobs[]` bzw. `.workflow_runs[]`, oder eine Seiten-Schleife über `page=`, bis eine Seite
  weniger als `per_page` Einträge liefert. Die bestehenden Filter (`head_sha` bzw. `branch`)
  bleiben für jede Seite erhalten. Obergrenze 10 Seiten; wird sie erreicht, ohne dass die
  Ergebnismenge vollständig ist, folgt `CiError` mit einer Meldung zum Seitenlimit statt einer
  Bewertung unvollständiger Daten (bei `--paginate` entsprechend über die Eintragszahl
  `> 10 × per_page`). Kein neues Python-Paket.
- **A3 (C21, E3) — Zeitbudget:** `poll_until` (forge-neutral, gilt für GitHub und GitLab)
  rechnet mit einer monotonen Deadline `Start + cfg.timeout`. Fetch-, Auswertungs- und
  Schlafzeit zählen. Vor jedem Schlafen wird die Schlafdauer auf die Restzeit gekappt. Ist die
  Deadline nach einem `fetch()` überschritten und der Status nicht final, folgt der heutige
  Timeout-Pfad (`CiTimeoutError` → Eskalation „CI-Timeout: …"). Ein finaler Status wird
  weiterhin ausgewertet, auch wenn die Deadline inzwischen überschritten ist. Die Uhr ist
  injizierbar (Parameter mit Default `time.monotonic`). Das Feld für die verstrichene Zeit im
  `ci.wait`-End-Payload heißt im Code `duration` (im Issue als `elapsed` bezeichnet); es trägt
  die mit der Uhr gemessene Gesamtzeit, der Feldname bleibt `duration`.
- **A4 (C22, E4) — GitHub Log-Evidenz:** `_failed_logs` nimmt einen Run nur mit Überschrift
  und Inhalt in den Auszug auf, wenn der Log-Inhalt nach `strip()` nicht leer ist. Ein
  `CiError` aus `gh run view --log-failed` wird je Run gefangen; dieser Run trägt nichts bei,
  die übrigen roten Runs werden weiter gesammelt, `poll_ci` scheitert nicht. Tragen alle Runs
  nichts bei, ist der Auszug exakt `""`, sodass der vorhandene Guard in `run_ci_phase` greift
  („Pipeline rot ohne verwertbare Job-Logs (Pipeline <ID>) — Mensch übernimmt").
- **A5 (C23, E5) — GitLab manual:** `manual` ist für GitLab ein finaler Pipeline-Status.
  Die Bewertung liefert dafür kein Grün, sondern eine sofortige Eskalation an den Menschen mit
  der Meldung „Pipeline wartet auf einen manuellen Job (<Name>) — Mensch übernimmt"; der Name
  stammt aus den Pipeline-Jobs mit Status `manual`, soweit ermittelbar, sonst entfällt die
  Klammer. Kein Log-Analyst, kein Re-Entry, keine Timeout-Meldung. Der Weg zur Eskalation
  (eigener Fehlertyp über den bestehenden `CiError`-Handler oder ein direkter Pfad) ist
  Implementierungssache; beobachtbar ist die Meldung.
- **A6 — Changelog:** `CHANGELOG.md` und `CHANGELOG.de.md` erhalten inhaltlich synchron eine
  neue Sektion `## [0.36.2] — Unreleased` mit `### Fixed` (je ein Eintrag zu C19–C23, Format
  wie die bestehenden Sektionen). Version in `pyproject.toml` bleibt `0.36.1`.

## Non-Goals

- Umbau des Phase-7-Ablaufs (Push, Polling, Log-Analyst, ein Re-Entry, dann Mensch). Die in E5
  geforderte direkte Eskalation bei `manual` ist davon ausgenommen.
- Änderung der Bewertung optionaler GitHub-Workflow-Runs (`skipped`/`neutral` bleiben grün).
- Änderung von Bedeutung oder Default von `ci.timeout`, `ci.poll_interval`, `ci.staging_job`.
- Änderung der SHA-Bindung des Pollings an den gepushten Commit.
- Änderung der bestehenden GitLab-Jobs-Pagination oder der Forge-Erkennung.
- Versionsbump in `pyproject.toml`.
- Contract oder Stabilitätszusagen für interne Helper-Signaturen.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Automatisches Auslösen manueller Jobs.
- Bewertung weiterer Spezialstatus (`waiting_for_resource`, `scheduled`, `preparing`) über
  ihr heutiges Verhalten hinaus.
- Mehrere Staging-Jobs, Job-Matching per Muster.
- Konfigurierbares Seitenlimit für die Pagination (fest 10).
- Hartes Abbrechen eines laufenden `gh`/`glab`-Aufrufs durch die Polling-Deadline; der
  bestehende CLI-Timeout (`_CLI_TIMEOUT = 120` s) bleibt der Backstop.
- Retry-, Cache- oder Persistenzmechanismen für fehlende Fehler-Logs.

## Akzeptanzkriterien

1. **GitHub Staging-Job (A1):** Bei ansonsten grünen Workflow-Runs ist ein konfigurierter
   Staging-Job mit `conclusion == "skipped"` (ebenso `neutral`) nicht grün: Phase 7 nimmt den
   bestehenden Pfad wie bei einem roten Staging-Job (Log-Auszug, kein `done`). Mit `success`
   ist die Staging-Bedingung erfüllt. Ein optionaler Workflow-Run mit `skipped` oder `neutral`
   verhindert ein ansonsten berechtigtes `done` weiterhin nicht.
2. **GitHub Pagination (A2):** Ein erfolgreicher Staging-Job, der erst auf Seite 2 der Jobs
   steht, wird gefunden; die Meldung „Staging-Job existiert in keinem Workflow-Run" entfällt.
   Workflow-Runs über mehrere Seiten werden vollständig bewertet: ein roter Run auf einer
   Folgeseite macht das Ergebnis rot, ein noch laufender Run auf einer Folgeseite verhindert
   den vorzeitigen Abschluss. Mehr als 10 Seiten → `CiError` mit Meldung zum Seitenlimit,
   kein `done` aus Teilergebnissen.
3. **Zeitbudget (A3):** Budget 1 s, zwei Fetches à 0,6 s (Fake-Uhr), Status nie final →
   Timeout nach dem zweiten Fetch, kein dritter Fetch. Zwischen den Fetches wird höchstens die
   nach dem ersten Fetch verbleibende Restzeit geschlafen; nach erkanntem Timeout wird nicht
   mehr geschlafen. `duration` im `ci.wait`-End-Payload entspricht der mit der Fake-Uhr
   gemessenen Gesamtzeit (Fetches plus Schlafzeit). Ein finaler Status nach überschrittener
   Deadline wird ausgewertet, nicht als Timeout gemeldet.
4. **GitHub Log-Evidenz (A4):** Roter Run, dessen `--log-failed` leer oder nur Whitespace ist
   → Auszug `""`; bei noch nicht verbrauchtem Re-Entry-Budget eskaliert Phase 7 mit „Pipeline
   rot ohne verwertbare Job-Logs (Pipeline <ID>) — Mensch übernimmt", ohne Log-Analyst und
   ohne Re-Entry. Ein roter Run, dessen `gh run view --log-failed` `CiError` wirft, wird
   übersprungen; die Logs der anderen roten Runs stehen mit Überschrift im Auszug, und
   `poll_ci` scheitert nicht.
5. **GitLab manual (A5):** Pipeline-Status `manual` → sofortige Eskalation mit der Meldung aus
   A5 (Jobname enthalten, falls ermittelbar). Kein weiteres Polling, kein Warten bis
   `ci.timeout`, keine „CI-Timeout"-Meldung, kein Log-Analyst, kein Re-Entry.
6. **Changelog (A6):** `CHANGELOG.md` und `CHANGELOG.de.md` enthalten inhaltlich
   deckungsgleich `## [0.36.2] — Unreleased` mit `### Fixed` zu C19–C23;
   `pyproject.toml` steht weiterhin auf `0.36.1`.
7. **Toolchain (E6):** `uv run ruff check .` und `uv run pytest -x -q` grün.

Richtwert: 7–10 neue Tests. Deutlich mehr gilt als Gold-Plating.

## Definition of Done

- Akzeptanzkriterien 1–7 erfüllt; AK 1–5 durch Tests belegt, AK 6 durch Prüfung der Dateien.
- Tests nutzen die vorhandenen Fakes (`FakeGh`, `FakeGlab`) und für Zeitfälle eine Fake-Uhr
  mit simuliertem Schlafen; kein Netz, kein echtes Warten.
- Kein neues Python-Paket; Non-Goals und Deferred-Punkte unangetastet.
- Changelog EN/DE synchron, Version in `pyproject.toml` unverändert.
