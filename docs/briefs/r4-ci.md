# R4: ADW liest das CI-Ergebnis richtig

Lauf R4 aus `ONBOARDING-SPEC-PLAN.md` (Abschnitt 3, Befunde C19–C23). Setzt auf dem
gemergten Stand **0.36.1** (nach R3) auf. Die Ausgangslage ist am 2026-11-08 im Code geprüft
und vor dem Bauen gegen den dann aktuellen Stand zu prüfen.

Phase 7 entscheidet, ob ein Lauf `done` ist. Fünf Stellen lassen sie heute falsch
urteilen oder unnötig lange warten.

## Ausgangslage (im Code verifiziert, 2026-11-08)

### C19 — Übersprungener Staging-Job gilt als grün (GitHub)

`adw/github.py:23` `_GREEN_CONCLUSIONS = {"success", "skipped", "neutral"}` gilt für
Workflow-Runs **und** für den konfigurierten Staging-Job (`:111`). Ein Deploy-Job, der
wegen einer falschen `if`-Bedingung `skipped` ist, macht den Lauf `done`, obwohl nichts
deployt wurde. Bei GitLab verlangt `_evaluate` (`adw/ci.py:207`) für den Staging-Job
bereits `status == "success"`.

### C20 — Keine Pagination (GitHub)

`_jobs` (`adw/github.py:117–118`) liest nur `per_page=100`, ohne weitere Seiten;
`_workflow_runs` (`:79–88`) ebenso mit `_RUNS_PER_PAGE`. Liegt der Staging-Job auf
Seite 2, meldet ADW „Staging-Job existiert in keinem Workflow-Run" → unnötige
CI-Reparatur bzw. Eskalation; fehlende Runs können rote oder laufende Runs verbergen.

### C21 — Das Timeout rechnet nur Schlafzeit

`poll_until` (`adw/ci.py:67–110`) zieht vom Budget `remaining` nur die Schlafdauer
ab; die Dauer von `fetch()` (gh/glab-Aufrufe, je bis `_CLI_TIMEOUT = 120` s) und
`evaluate` zählt nicht. Im Review gemessen: Budget 1 s mit zwei Fetches à 350 ms →
1,7 s. Bei langsamen API-Antworten überzieht `ci.timeout` um Minuten.

### C22 — Leere Fehler-Logs gelten als Beweis (GitHub)

`_failed_logs` (`adw/github.py:126–133`) hängt für jeden roten Run eine Überschrift
an, auch wenn `gh run view --log-failed` leer zurückkommt. Damit ist der Auszug nie
leer, und der Guard für „rot ohne Evidenz" in `run_ci_phase` greift nicht — der
Log-Analyst bekommt nur Überschriften. Zusätzlich: Für rote Runs ohne Job-Logs
(`startup_failure`, `action_required`, `cancelled`) kann `gh run view --log-failed`
mit Fehler enden; `run_gh` wirft dann `CiError`, `poll_ci` scheitert, und die Logs der
übrigen roten Runs gehen verloren.

### C23 — GitLab `manual` wartet bis zum Timeout

`_TERMINAL_STATUSES = {"success", "failed", "canceled", "skipped"}` (`adw/ci.py:16`).
Eine Pipeline, die an einem `when: manual`-Job mit `allow_failure: false` hängt, hat
den Status `manual` und wird nie final: ADW pollt das ganze `ci.timeout` und eskaliert
mit einem irreführenden „CI-Timeout".

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Der Ablauf von Phase 7 (Push, Polling, Log-Analyst, ein Re-Entry, dann Mensch)
  bleibt.
- Die Bewertung **optionaler** Workflow-Runs bei GitHub (Runs, die nicht der
  Staging-Job sind) darf weiter `skipped`/`neutral` als grün werten.
- `ci.timeout`, `ci.poll_interval`, `ci.staging_job` bleiben in Bedeutung und Default.

## Aufgabe

**A1 (C19)** Staging-Job bei GitHub nur mit `success` grün.
**A2 (C20)** Pagination für Jobs und Workflow-Runs.
**A3 (C21)** Monotone Deadline über das ganze Warten.
**A4 (C22)** Nur echter Log-Inhalt ist Evidenz; ein log-loser Run bricht das Sammeln
nicht ab.
**A5 (C23)** GitLab `manual` ist final und eskaliert mit klarer Meldung.
**A6** `CHANGELOG.md`/`CHANGELOG.de.md` synchron: neue Sektion
`## [0.36.2] — Unreleased` mit `### Fixed`. Version in `pyproject.toml` **nicht**
ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 (C19):** Für Jobs mit `name == cfg.staging_job` gilt nur `conclusion ==
  "success"` als grün. `_GREEN_CONCLUSIONS` bleibt für Workflow-Runs.
- **E2 (C20):** Pagination über `gh api --paginate` mit `--jq` auf das jeweilige
  Array (`.jobs[]` bzw. `.workflow_runs[]`), oder alternativ Seiten-Schleife über
  `page=` bis eine Seite weniger als `per_page` Einträge hat. Höchstens 10 Seiten;
  danach `CiError` mit Meldung. Kein neues Python-Paket.
- **E3 (C21):** `poll_until` rechnet mit `time.monotonic()`-Deadline (Start + 
  `cfg.timeout`); vor jedem Schlafen wird auf die Restzeit gekappt; ist die Deadline
  nach einem `fetch()` überschritten und der Status nicht final → Timeout wie heute.
  Die Uhr ist injizierbar (Parameter mit Default `time.monotonic`), damit Tests ohne
  echtes Warten laufen. `elapsed` im End-Payload ist die gemessene Zeit.
- **E4 (C22):** `_failed_logs` fügt eine Überschrift nur hinzu, wenn der Log-Inhalt
  nach `strip()` nicht leer ist. `CiError` aus `gh run view --log-failed` wird **je
  Run** gefangen; dieser Run trägt dann nichts bei. Sind alle leer, ist der Auszug
  `""` — damit greift der vorhandene Guard „rot ohne Evidenz".
- **E5 (C23):** `manual` kommt in die finalen Status für GitLab. `_evaluate` liefert
  für `manual` kein Grün, sondern eine Eskalation mit der Meldung „Pipeline wartet
  auf einen manuellen Job (<Name, falls ermittelbar>) — Mensch übernimmt"; **kein**
  Log-Analyst, kein Re-Entry.
- **E6:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Tests mit den
  vorhandenen Fakes für `gh`/`glab` (kein Netz).

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Automatisches Auslösen manueller Jobs.
- Bewertung weiterer Spezialstatus (`waiting_for_resource`, `scheduled`,
  `preparing`) über ihr heutiges Verhalten hinaus.
- Mehrere Staging-Jobs, Job-Matching per Muster.

## Akzeptanzkriterien

1. GitHub: Staging-Job `skipped` → nicht grün (Log-Pfad bzw. Meldung wie bei rot);
   `success` → grün; ein optionaler Workflow-Run `skipped` bleibt grün.
2. GitHub: Staging-Job auf Seite 2 der Jobs wird gefunden; Workflow-Runs über mehrere
   Seiten werden vollständig bewertet.
3. Timeout: Budget 1 s, zwei Fetches à 0,6 s (Fake-Uhr) → Timeout nach dem zweiten
   Fetch, kein drittes Polling, keine Schlafzeit über die Restzeit hinaus.
4. GitHub: roter Run mit leerem `--log-failed` → Auszug leer, Guard „rot ohne
   Evidenz" greift; ein Run, dessen `gh run view` `CiError` wirft, wird übersprungen,
   die Logs der anderen roten Runs bleiben im Auszug.
5. GitLab: Pipeline `manual` → sofortige Eskalation mit der Meldung aus E5, kein
   weiteres Polling.
6. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 7–10 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (Ergebnis
von Phase 7: `done`/Eskalation und Meldungen), keine internen Helper-Signaturen.
