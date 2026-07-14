# SPEC — Agentic Developer Workflow (ADW)

Stand: 2026-07-14 · validiert im Brainstorming (Stefan + Fable 5) · Referenz: `Handout_AgenticDeveloperWorkflow.html` (v2)

## 1. Ziel

Ein Python-Orchestrator, der ein Issue vollautomatisch durch sieben Phasen führt —
Spec → Plan + API-Kontrakt → Build → Integration/E2E → Codex-Review → finaler Review → Push/CI/Staging —
als Kombination aus **Agents** (Claude Agent SDK + Codex CLI) und **deterministischem Code**
(Gates, Dispatch, Triage, Merges, CI-Polling).

Leitsatz (aus der Video-Analyse „Forget Loop Engineering"): Kontrollfluss ist Code, nicht Prompt.
Agenten nur dort, wo Urteilsvermögen gebraucht wird; alles Wiederholbare läuft als Code —
0 Tokens, deterministisch, immer gleich.

## 2. Scope

**v1 (eine Build-Lane):** Phasen 1–2 (Spec-/Plan-Agent mit Codex-Review-Loop), Plan-Approval-Gate,
eine Build-Lane mit Gates + Fix-Loop, Codex-Code-Review mit Fix-Plan, finaler Review (read-only)
mit Triage, Push + GitLab-CI-Monitoring bis Staging.

**v2 (`--parallel`):** Dispatch in parallele FE-/BE-Lanes (eigene Worktrees, Sessions, Ports, Gates),
Integrations-Merge, Playwright-E2E-Gate, E2E-Triage-Agent, Log-Analyst für rote CI-Pipelines.

v1 = v2 mit einer Lane: **ein** Codepfad, eine CLI (`adw run`), das `--parallel`-Flag aktiviert
Mehr-Lanen-Betrieb. Keine getrennten Skripte.

**Nicht-Ziele (v1/v2):** Kanban-/Webhook-Intake, Workflow-Router (Chore/Bug/Feature/Hotfix),
ZTE (Auto-Merge nach production), Multi-Repo-Runs, eigene Web-UI, Agent-Sandboxes jenseits
von Git-Worktrees.

## 3. Akteure & Agent-Registry

| Agent | Modell | Werkzeuge | Auftrag |
|---|---|---|---|
| Spec-Agent | Fable 5 | Read/Grep/Glob + Write (nur `.adw/`) | `.adw/spec.md` nach Vorlage; implementiert nie |
| Plan-Agent | Fable 5 | Read/Grep/Glob + Write (nur `.adw/`) | `.adw/plan.md` (Workstreams FE/BE) + `.adw/contract.yaml` |
| Build-Agent (je Lane) | Opus 4.8 | Read/Write/Edit/Bash, `cwd` = Lane-Worktree | Workstream strikt gegen Kontrakt; Fix-Pläne = Empfehlung |
| Codex-Reviewer | Codex (CLI) | `codex exec --sandbox read-only` | Reviews von Spec, Plan, Code; Findings + `remediation_plan` als JSON |
| E2E-Triage | Haiku (Workhorse) | Read | Playwright-Fehler einer Lane zuordnen; fixt nichts |
| Log-Analyst | Haiku (Workhorse) | Read | CI-Logs → strukturierte Findings mit Lane-Zuordnung |
| Finaler Reviewer | Fable 5 | Read/Grep/Glob (strikt read-only) | Implementierung gegen Spec prüfen; nur Findings |

Grundregeln: **Reviewer fixen nie.** Jeder Fix läuft durch die Build-Agents und erneut durch
alle Gates — kein Sonderweg für „triviale" Fixes.

## 4. Die sieben Phasen (Soll-Verhalten)

1. **Spec:** Issue (CLI-Text oder GitLab-Issue via `glab`) → Spec-Agent schreibt `.adw/spec.md`
   (Ziel, Scope, Nicht-Ziele, Akzeptanzkriterien, Definition of Done). Codex reviewt; Findings
   gehen an den Spec-Agent zurück (Session-Resume) bis Verdict `ok`.
2. **Plan + Kontrakt:** Plan-Agent erzeugt `.adw/plan.md` + `.adw/contract.yaml`
   (OpenAPI/Typen/Events). Codex reviewt beides gemeinsam bis `ok`.
   **Plan-Approval-Gate:** Workflow pausiert (State persistiert, Exit); Stefan liest Plan +
   Kontrakt und setzt fort via `adw resume <run_id>` bzw. `adw approve <run_id>`.
   Abschaltbar mit `--no-approval`.
3. **Build:** Dispatch (Code) teilt den Plan in Workstreams. Je Lane: eigener Git-Worktree
   (Branch von Base-Branch), eigene SDK-Session, eigene Ports. Lane-Loop: Build-Agent arbeitet →
   Gates laufen (Kommandos aus Ziel-Repo-Config) → bei Fail gehen die Fehlerausgaben als
   Folge-Task an **dieselbe Session** zurück. Max. 10 Iterationen.
4. **Integration + E2E** (nur `--parallel`): Code mergt Lane-Branches auf einen
   Integrations-Branch; E2E-Kommando (Playwright) läuft. Bei Rot ordnet der E2E-Triage-Agent
   jeden Fehler einer Lane zu → Fix in der Lane → erneut integrieren. Max. 10 Runden.
5. **Codex-Code-Review:** Codex reviewt den integrierten Diff, liefert Findings mit
   `remediation_plan`. Findings werden per `lane`-Feld geroutet; Build-Agents prüfen den
   Fix-Plan gegen Spec/Konventionen und dürfen begründet abweichen. Läuft bis Verdict `ok`.
6. **Finaler Review + Triage:** Fable 5 prüft read-only gegen `.adw/spec.md`. Triage (Code):
   `scope_gap` → Follow-up-Issue (Report, kein Auto-Restart); `implementation`/`trivial` →
   Fix-Zyklus in die Lane. Max. 3 Fix-Zyklen.
7. **Push + CI:** Merge/Push des Feature-Branches, dann `glab`-Polling (60-s-Intervall,
   45-min-Timeout) bis Pipeline + Staging-Deploy grün. Bei roter Pipeline: Log-Analyst liest
   Logs → Findings → zurück in Phase 3/4.

**Eskalation:** Jedes erschöpfte Limit und der Circuit-Breaker (eine Fix-Iteration löst
**nichts** auf → sofort abbrechen) beendet den Run mit Exit-Code ≠ 0 und einem
Eskalations-Report (`.adw/runs/<run_id>/escalation.md`): was erreicht, was offen, warum.

## 5. Schnittstellen

### CLI

```
adw run --repo <pfad> (--issue "Text" | --gitlab-issue <id>)
        [--parallel] [--dry-run] [--no-approval] [--base-branch <name>]
adw resume <run_id> [--repo <pfad>]      # nach Crash oder Approval-Pause
adw approve <run_id>                     # Plan-Approval erteilen + fortsetzen
adw status [<run_id>]                    # Runs + Phase anzeigen
```

### Config im Ziel-Repo: `.adw/config.yaml`

```yaml
base_branch: staging
lanes:
  backend:
    gates:                     # Reihenfolge = Ausführungsreihenfolge; jedes: Name + Kommando + Timeout
      - {name: black,  cmd: "black --check .", timeout: 120}
      - {name: isort,  cmd: "isort --check-only .", timeout: 120}
      - {name: pytest, cmd: "pytest -x -q", timeout: 1800}
  frontend:                    # optional; fehlt die Lane, läuft v1-Single-Lane
    gates:
      - {name: eslint, cmd: "npm run lint", timeout: 300}
e2e:                           # optional; nur mit --parallel relevant
  cmd: "npx playwright test"
  timeout: 1800
ci:
  poll_interval: 60
  timeout: 2700
  staging_job: deploy-staging  # Job-Name, der grün sein muss
```

Fehlende/kaputte Config → sofortiger, klarer Fehler (fail fast), kein Raten von Defaults
außer den dokumentierten (`poll_interval`, `timeout`).

### Findings-Schema (JSON, überall identisch)

```json
{
  "verdict": "ok | needs_fixes",
  "findings": [{
    "severity": "P1 | P2 | P3",
    "lane": "frontend | backend | unknown",
    "file": "pfad/relativ/zum/repo.py",
    "issue": "Beschreibung des Problems",
    "remediation_plan": ["Schritt 1", "Schritt 2"],
    "category": "scope_gap | implementation | trivial"
  }]
}
```

`category` befüllt nur der finale Reviewer (Triage-Grundlage). Codex wird per Prompt +
`--output-schema`-ähnlicher Instruktion auf dieses Schema festgelegt.

**Parser-Kontrakt (strikt, bewusste Design-Entscheidung):** Akzeptiert wird ausschließlich
(a) Output, der als Ganzes ein JSON-Objekt ist, oder (b) der Inhalt des letzten
```` ```json ````-Fence. Alles andere (Prosa um nacktes JSON, Entwürfe, abgeschnittene oder
verpackte Antworten, unclosed Fences) → `FindingsParseError` = Retry-/Eskalationsfall.
Toleranz-Heuristiken sind gegen adversariale Outputs nicht abdichtbar (Stale-ok-Risiko);
ein Parse-Fehler ist safe, ein falsches „ok" nicht. Validierung strikt via Pydantic
(`extra="forbid"`, Verdict-Findings-Konsistenz, Pflichtfelder außer `category`).

### Artefakte & State

- `.adw/spec.md`, `.adw/plan.md`, `.adw/contract.yaml` — im Ziel-Repo, werden auf dem
  Feature-Branch **mitcommittet** (Traceability).
- `.adw/runs/<run_id>/` — gitignored: `state.json` (RunState), Agent-Transkripte,
  Gate-Outputs, `escalation.md`.
- `RunState` (Pydantic): run_id, issue, phase, lanes (worktree, branch, session_id, ports,
  iterations), approval-Status, ci-Status. Nach jedem Phasenübergang persistiert →
  `adw resume` setzt exakt dort fort.

## 6. Design-Prinzipien (verbindlich)

1. **Drei Akteure, klar verteilt** — Gates/Merges/Polling/Dispatch/Triage sind Code
   (`subprocess` mit **echtem `timeout`**-Parameter, immer).
2. **Reviewer ≠ Fixer.**
3. **Jeder Fix nimmt den validierten Pfad** (alle Gates, keine Ausnahme).
4. **Strukturierte Übergaben:** JSON/Pydantic zwischen allen Nodes, kein Freitext-Parsing.
5. **Modell-Ökonomie:** Fable 5 nur Spec/Plan/finaler Review; Opus baut; Haiku triagiert.
6. **Sicherheit:** `allowed_tools` pro Agent aus der Registry; Build-Agents via `cwd` auf ihren
   Worktree begrenzt; Env-Whitelist für alle Subprozesse (kein Secret-Leakage);
   niemals pauschales Permission-Skipping.
7. **Session-Resume statt Kontext-Neuaufbau** in allen Fix-Zyklen (SDK `resume=session_id`).

## 7. Technik

- Python ≥ 3.12, **uv** (pyproject.toml + uv.lock), Package `adw/`, Entry-Point `adw` (typer).
- `claude-agent-sdk` (query + ClaudeAgentOptions: model, cwd, resume, allowed_tools,
  system_prompt-preset `claude_code` + append, permission_mode).
- Codex als CLI-Subprocess (`codex exec --sandbox read-only`), kein zweites SDK.
- `glab` für GitLab (Issue lesen, Push, Pipeline-Status), `git worktree` für Lanes,
  Ports deterministisch aus run_id (Basis-Port + Hash-Offset, Socket-Bind-Check als Fallback).
- Agent- und Codex-Aufrufe hinter je einem Interface (`AgentRunner`, `CodexRunner`);
  `--dry-run` injiziert Mocks mit kanonischen Fixtures (simulierte Gate-Fails,
  Review-Findings) — kompletter v1-/v2-Kontrollfluss ohne Tokens testbar.

## 8. Akzeptanzkriterien (Definition of Done)

1. `adw run --repo <test-repo> --issue "…" --dry-run` durchläuft alle 7 Phasen (Single-Lane)
   ohne Token-Verbrauch; `--dry-run --parallel` durchläuft beide Lanes inkl. E2E-Triage-Pfad.
2. Simulierter Gate-Fail führt zum Fix-Task an dieselbe Session; nach 10 erfolglosen
   Iterationen bzw. bei Null-Fortschritt (Circuit-Breaker) entsteht `escalation.md` und
   Exit-Code ≠ 0.
3. Plan-Approval-Gate: Run pausiert nach Phase 2, `adw approve` setzt fort; `--no-approval`
   überspringt.
4. Triage: `scope_gap`-Finding erzeugt Follow-up-Report statt Fix-Zyklus;
   `implementation`-Finding routet in die richtige Lane; nach 3 Fix-Zyklen Eskalation.
5. `adw resume <run_id>` setzt einen abgebrochenen Run in derselben Phase fort
   (State-Round-Trip-Test).
6. Alle deterministischen Module (config, state, findings, triage, gates, worktrees/ports,
   dispatch, ci-polling) haben pytest-Tests (TDD, test-first); Lint (`ruff` oder
   flake8+isort+black) grün.
7. Ein echter (Token-)Lauf gegen ein kleines Test-Repo mit echtem Issue ist erst **nach**
   Abnahme des Dry-Run-Gerüsts vorgesehen (nicht Teil dieser DoD).
