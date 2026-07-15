# Agentic Developer Workflow (ADW)

Ein Python-Orchestrator, der ein Issue vollautomatisch durch **sieben Phasen** führt:

> Spec → Plan + Kontrakt → Build (Lanes) → Integration/E2E → Codex-Review → finaler Review + Triage → Push/CI/Staging

Leitsatz: **Kontrollfluss ist Code, nicht Prompt.** Loops, Gates, Merges, Dispatch,
Triage, Limits und CI-Polling sind deterministischer Python-Code (0 Tokens).
Agenten (Claude Agent SDK + Codex CLI) laufen nur dort, wo Urteilsvermögen
gebraucht wird — und Reviewer fixen nie: jeder Fix läuft durch die Build-Agents
und erneut durch alle Gates.

Referenzen: [`docs/SPEC.md`](docs/SPEC.md) (Soll-Verhalten, Schnittstellen, DoD)
und [`docs/PLAN.md`](docs/PLAN.md) (Implementierungsplan).

## Quickstart

```bash
# Abhängigkeiten (uv, Python >= 3.12)
uv sync

# Ziel-Repo vorbereiten: .adw/config.yaml anlegen (siehe examples/config.yaml)
mkdir -p /pfad/zum/repo/.adw
cp examples/config.yaml /pfad/zum/repo/.adw/config.yaml   # und anpassen

# Trockenlauf: kompletter Kontrollfluss mit Mocks — 0 Tokens, kein Netz
uv run adw run --repo /pfad/zum/repo --issue "Demo-Feature" --dry-run --no-approval
uv run adw run --repo /pfad/zum/repo --issue "Demo-Feature" --dry-run --parallel --no-approval

# Echter Lauf (Tokens!): Issue-Text direkt oder GitLab-Issue via glab
uv run adw run --repo /pfad/zum/repo --issue "Bug: Login bricht ab, wenn ..."
uv run adw run --repo /pfad/zum/repo --gitlab-issue 42 --parallel
```

### CLI

```
adw run --repo <pfad> (--issue "Text" | --gitlab-issue <id>)
        [--parallel] [--dry-run] [--no-approval] [--base-branch <name>]
adw resume <run_id> [--repo <pfad>]      # nach Crash; bei Approval-Pause → approve
adw approve <run_id> [--repo <pfad>]     # Plan-Approval erteilen + fortsetzen
adw status [<run_id>] [--repo <pfad>]    # Runs + Phase anzeigen
```

Exit-Codes: `0` done · `2` awaiting_approval (Plan-Approval-Pause) · `1` Eskalation/Fehler.

**Plan-Approval-Gate:** Nach Phase 2 pausiert der Run (Exit 2). Plan und
Kontrakt liegen unter `.adw/runs/<run_id>/plan.md` bzw. `contract.yaml` —
lesen, dann `adw approve <run_id>`. Mit `--no-approval` entfällt die Pause.

## Config-Referenz (`.adw/config.yaml` im Ziel-Repo)

Vollständiges Beispiel: [`examples/config.yaml`](examples/config.yaml).

| Schlüssel | Pflicht | Bedeutung |
| --- | --- | --- |
| `base_branch` | ja | Branch, von dem Lanes forken und gegen den diffs laufen |
| `lanes.<name>.gates[]` | ja (>= 1 Lane) | Gate-Liste je Lane: `name`, `cmd`, `timeout` (Sekunden). Reihenfolge = Ausführungsreihenfolge, fail fast |
| `e2e.cmd` / `e2e.timeout` | optional | E2E-Kommando (Playwright o. ä.) — nur mit `--parallel` relevant |
| `ci.poll_interval` | optional (60) | Sekunden zwischen Pipeline-Polls |
| `ci.timeout` | optional (2700) | Gesamt-Budget fürs CI-Warten |
| `ci.staging_job` | optional | Job-Name, der zusätzlich grün sein muss |

Fehlende oder kaputte Config bricht sofort mit klarer Meldung ab (fail fast).
`--parallel` verlangt eine `backend`- **und** `frontend`-Lane.

## Architektur in 60 Sekunden

```
adw/
  cli.py        typer-Eingang: run/resume/approve/status, Dry-Run-Verdrahtung
  phases.py     die 7 Phasen über einem RunContext — Loops, Limits, Dispatch
  agents.py     Agent-Registry (Fable 5 / Opus 4.8 / Sonnet 5) + SDK-Runner
  codex.py      Codex-CLI als read-only-Subprocess, striktes Findings-Parsing
  findings.py   Findings-Schema (pydantic) + strikter Parser-Kontrakt
  config.py     .adw/config.yaml-Loader (fail fast)
  state.py      RunState: atomar persistiert, Grundlage für `adw resume`
  gates.py      Gate-Runner: subprocess mit echtem Timeout, Env-Whitelist
  worktrees.py  Lane-Worktrees + deterministische Ports
  triage.py     Triage-Regeln, Iterations-Limits, Circuit-Breaker
  ci.py         glab-Polling bis Staging grün, Log-Abruf für den Log-Analyst
  mock.py       skriptbare Mock-Runner für --dry-run und Tests
```

- **Lanes:** je Workstream ein eigener Git-Worktree (`.adw/runs/<id>/trees/<lane>`),
  eigene SDK-Session, eigene Ports. Commits macht ausschließlich der Orchestrator.
- **Limits:** 10 Gate-Iterationen je Task, 10 E2E-/Review-Runden, 3 Fix-Zyklen,
  1 CI-Re-Entry — plus Circuit-Breaker (identische Fehler zweimal → sofort Schluss).
- **Resume:** jeder Übergang und jedes offene Feedback ist in
  `.adw/runs/<run_id>/state.json` checkpointed; `adw resume` setzt exakt dort fort.
- **Sicherheit:** Agenten arbeiten mit Tool-Whitelists und Pfad-Regeln; Subprozesse
  laufen mit Env-Whitelist (keine Secrets); Spec/Plan/Kontrakt und die Config sind
  für Agents effektiv unveränderlich (Orchestrator restauriert sie).

## Troubleshooting

- **Run bricht ab (Exit 1):** `.adw/runs/<run_id>/escalation.md` lesen — dort
  stehen erreichter Stand, Phase und der konkrete Grund (Gate-Output,
  Merge-Konflikt, Limit, Circuit-Breaker). Nach manueller Klärung neuen Run
  starten; eskalierte Runs sind bewusst nicht fortsetzbar.
- **Run hängt in `awaiting_approval`:** `adw status`, dann
  `adw approve <run_id>` — oder künftig `--no-approval`.
- **`scope_gap`-Findings:** landen in `.adw/runs/<run_id>/followups.md` als
  Follow-up-Issues (kein Auto-Restart) — der Run läuft regulär weiter.
- **CI rot trotz Fix:** genau ein automatischer Log-Analyst-Re-Entry ist
  vorgesehen; danach eskaliert der Run mit den Job-Logs im Report.
- **Dry-Run zum Verifizieren der Config:** `--dry-run` fährt den kompletten
  Kontrollfluss inklusive simuliertem Gate-Fail und (mit `--parallel`)
  E2E-Triage-Pfad — ohne Tokens, ohne Push, ohne GitLab.

## Entwicklung

```bash
uv run pytest          # komplette Suite (~330 Tests, mocks-only, echtes git)
uv run ruff check .    # Lint
uv run ruff format .   # Formatierung
```
