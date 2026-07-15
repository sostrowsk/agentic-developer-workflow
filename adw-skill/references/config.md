# .adw/config.yaml — Referenz

Die komplette projektspezifische Konfiguration lebt im ZIEL-Repo unter
`.adw/config.yaml`. Vorlage: `assets/config-template.yaml`. ADW validiert
fail-fast: fehlende Datei, unbekannte Keys, doppelte Keys, Lane ohne Gates
oder Gate ohne Timeout brechen den Run sofort mit klarer Meldung ab.

## Schema

```yaml
base_branch: staging           # Pflicht: Fork-/Diff-Basis der Lanes

lanes:                         # Pflicht: mindestens eine Lane (backend|frontend)
  backend:
    gates:                     # Pflicht je Lane: >= 1 Gate
      - {name: black,  cmd: "black --check .",      timeout: 120}
      - {name: isort,  cmd: "isort --check-only .", timeout: 120}
      - {name: pytest, cmd: "pytest -x -q",         timeout: 1800}
  frontend:                    # optional; --parallel verlangt BEIDE Lanes
    gates:
      - {name: eslint, cmd: "npm run lint",         timeout: 300}

e2e:                           # optional; läuft nur mit --parallel
  cmd: "npx playwright test"
  timeout: 1800

ci:                            # optional; Defaults siehe unten
  provider: gitlab             # gitlab | github; ohne Angabe Auto-Erkennung
  poll_interval: 60            # Sekunden zwischen CI-Polls (Default 60)
  timeout: 2700                # CI-Warte-Budget in Sekunden (Default 2700)
  staging_job: deploy-staging  # Job, der zusätzlich grün sein muss (optional)
```

## Regeln und Stolperfallen

- **Gates = Qualitätsgrenze.** ADW committet Lane-Ergebnisse nur nach grünen
  Gates. Was kein Gate prüft, prüft in Phase 3 niemand — mindestens
  Linter + Testsuite konfigurieren. Reihenfolge = Ausführungsreihenfolge,
  das erste rote Gate stoppt (fail fast).
- **Timeouts sind Pflicht** (Sekunden, > 0) — jedes Gate-Kommando läuft mit
  hartem Subprocess-Timeout.
- **Ports:** Gates bekommen `BACKEND_PORT`/`FRONTEND_PORT` als Env-Variablen
  (deterministisch je Run/Lane) — für Dev-Server in Gate-/E2E-Kommandos nutzen.
- **`ci.provider`:** Ohne Angabe erkennt ADW das Hosting am Hostnamen der
  origin-URL (`github*` → GitHub, `gitlab*` → GitLab, auch Self-Hosted mit
  sprechendem Hostnamen). Bei anderem Hostnamen (z. B. `code.firma.de`) ist
  der Key Pflicht, sonst eskaliert Phase 7.
- **`staging_job`:** Name des CI-Jobs, der zusätzlich zur grünen Pipeline
  erfolgreich sein muss (GitLab: Pipeline-Job; GitHub: Job-Name in einem
  Workflow-Run). Weglassen, wenn es keinen Staging-Deploy gibt.
- **Env der Gates ist eine Whitelist** (PATH, HOME, LANG, VIRTUAL_ENV, …) —
  Secrets/API-Keys aus der Umgebung erreichen Gate-Kommandos nicht. Braucht
  ein Gate Umgebungswerte, gehören sie in Projekt-Dateien (z. B. `.env`, die
  das Kommando selbst lädt), nicht in die Shell-Umgebung.
- Die Config ist für alle ADW-Agenten unveränderlich — Änderungen macht nur
  der Mensch, per normalem Commit. `base_branch`-Änderungen wirken erst auf
  NEUE Runs (laufende Runs pinnen ihre Basis beim Start).
