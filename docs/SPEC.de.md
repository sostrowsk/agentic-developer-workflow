# SPEC — Agentic Developer Workflow (ADW)

[English](SPEC.md) | **Deutsch**

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
| Spec-Agent (Entwurfs-Autor) | Opus 4.8 | Read/Grep/Glob + Write (nur `.adw/spec.md`) | Unabhängiger Entwurf der Spezifikation; implementiert nie |
| Plan-Agent (Entwurfs-Autor) | Opus 4.8 | Read/Grep/Glob + Write (nur `.adw/plan.md` + `.adw/contract.yaml`) | Unabhängiger Entwurf von Plan (Workstreams FE/BE) + Kontrakt |
| Spec-Synthese | Fable 5 | Read/Grep/Glob + Write (nur `.adw/spec.md` + `.adw/spec-summary.md`) | Best-of-Merge beider Spec-Entwürfe + Zusammenfassung fürs Freigabe-Gate |
| Plan-Synthese | Fable 5 | Read/Grep/Glob + Write (nur `.adw/plan.md`, `.adw/contract.yaml` + `.adw/plan-summary.md`) | Best-of-Merge beider Plan-/Kontrakt-Entwürfe + Zusammenfassung |
| Build-Agent (je Lane) | Opus 4.8 | Read/Write/Edit/Bash, `cwd` = Lane-Worktree | Workstream strikt gegen Kontrakt; Fix-Pläne = Empfehlung |
| Codex | Codex (CLI) | `codex exec --sandbox read-only` | **Zweiter Entwurfs-Autor** von Spec und Plan (`author`) **und** Reviewer von Spec, Plan, Code (`review`); Findings + `remediation_plan` als JSON |
| E2E-Triage | Sonnet 5 (Workhorse) | Read | Playwright-Fehler einer Lane zuordnen; fixt nichts |
| Log-Analyst | Sonnet 5 (Workhorse) | Read | CI-Logs → strukturierte Findings mit Lane-Zuordnung |
| Finaler Reviewer | Fable 5 | Read/Grep/Glob (strikt read-only) | Implementierung gegen Spec prüfen; nur Findings |

Grundregeln: **Reviewer fixen nie.** Jeder Fix läuft durch die Build-Agents und erneut durch
alle Gates — kein Sonderweg für „triviale" Fixes.

**Dual-Authoring (Phase 1–2):** Jedes Authoring-Artefakt entsteht zweimal unabhängig — einmal
durch den Claude-Entwurfs-Autor (Opus), einmal durch Codex (`codex exec --sandbox read-only`;
weil die Sandbox nicht schreiben darf, liefert der Aufruf die Dateiinhalte in Marker-Blöcken
zurück). Beide Entwürfe laufen **parallel** und landen in `.adw/runs/<run_id>/drafts/`. Der
Synthese-Agent (Fable) merged sie danach zu EINEM Best-of-Artefakt — je Abschnitt gewinnt die
stärkere Formulierung, niemals die Vereinigungsmenge; Widersprüche werden zugunsten des Issues
aufgelöst. Entwurfs-Autoren und Synthese teilen sich dieselben inhaltlichen Vorgaben
(Proportionalität, Scope-Grenzen, „Deferred"-Abschnitt), damit die Synthese die Scope-Gegenkraft
der Autoren nicht wieder umkippt.

## 4. Die sieben Phasen (Soll-Verhalten)

1. **Spec:** Issue (CLI-Text, GitLab-Issue via `glab` oder GitHub-Issue via `gh`) → **Draft-Stage:**
   Spec-Agent und Codex schreiben je einen unabhängigen Entwurf von `.adw/spec.md` (Ziel, Scope,
   Nicht-Ziele, Akzeptanzkriterien, Definition of Done), parallel, nach
   `.adw/runs/<run_id>/drafts/`. Danach merged die **Spec-Synthese** beide Entwürfe zu
   `.adw/spec.md` und schreibt die Zusammenfassung `.adw/spec-summary.md`. Codex reviewt das
   Artefakt; Findings gehen an den Synthese-Agenten zurück (Session-Resume) bis Verdict `ok`.
2. **Plan + Kontrakt:** Dieselben zwei Schritte: Draft-Stage (Plan-Agent + Codex, je mit
   `.adw/plan.md` + `.adw/contract.yaml`, OpenAPI/Typen/Events), dann **Plan-Synthese** →
   Best-of-Artefakte + `.adw/plan-summary.md`. Codex reviewt Plan und Kontrakt gemeinsam bis `ok`.
   **Plan-Approval-Gate:** Workflow pausiert (State persistiert, Exit); Stefan liest die
   Zusammenfassung, Plan + Kontrakt und setzt fort via `adw resume <run_id>` bzw.
   `adw approve <run_id>`. Abschaltbar mit `--no-approval`.
3. **Build:** Dispatch (Code) teilt den Plan in Workstreams. Je Lane: eigener Git-Worktree
   (Branch von Base-Branch), eigene SDK-Session, eigene Ports. **RED-Stufe** (nur für Lanes mit
   mindestens einem Gate `tdd: true` und nur im Initial-Build): der Build-Agent schreibt zuerst
   NUR die Tests, dann führt der Orchestrator selbst genau die markierten Gates aus — mindestens
   eines rot ist der RED-Beweis (`red_confirmed` plus die Test-Pfade im State), alle grün
   eskaliert (die Tests decken das geforderte Verhalten nicht ab oder es existiert schon).
   Danach der Lane-Loop: Build-Agent arbeitet (dieselbe Session, mit dem gekürzten roten
   Gate-Output als Task) → Gates laufen (Kommandos aus Ziel-Repo-Config) → bei Fail gehen die
   Fehlerausgaben als Folge-Task an **dieselbe Session** zurück. Max. 10 Iterationen; der
   RED-Check verbraucht keine davon.
4. **Integration + E2E** (nur `--parallel`): Code mergt Lane-Branches auf einen
   Integrations-Branch; E2E-Kommando (Playwright) läuft. Bei Rot ordnet der E2E-Triage-Agent
   jeden Fehler einer Lane zu → Fix in der Lane → erneut integrieren. Max. 10 Runden.
5. **Codex-Code-Review:** Codex reviewt den integrierten Diff, liefert Findings mit
   `remediation_plan`. Findings werden per `lane`-Feld geroutet; Build-Agents prüfen den
   Fix-Plan gegen Spec/Konventionen und dürfen begründet abweichen. Läuft bis Verdict `ok` —
   max. 5 Runden (Review-Loop-Policy unten).
6. **Finaler Review + Triage:** Fable 5 prüft read-only gegen `.adw/spec.md`. Triage (Code):
   `scope_gap` → Follow-up-Issue (Report, kein Auto-Restart); `implementation`/`trivial` →
   Fix-Zyklus in die Lane. Max. 3 Fix-Zyklen.
7. **Push + CI:** Merge/Push des Feature-Branches, dann CI-Polling (60-s-Intervall,
   45-min-Timeout) bis Pipeline + Staging-Deploy grün — GitLab via `glab`, GitHub
   Actions via `gh` (Forge aus `ci.provider` bzw. origin-URL). Bei roter Pipeline:
   Log-Analyst liest Logs → Findings → zurück in Phase 3/4.

**Degradation und Eskalation in der Draft-Stage (Phase 1–2):** Ein fehlgeschlagener
Codex-Entwurf **degradiert** nur — es gibt eine Warnung plus einen
`<kind>.codex.FAILED`-Marker, und die Synthese arbeitet einquellig weiter (und benennt das in
der Zusammenfassung); der Marker verhindert, dass ein Resume einen weiteren Codex-Lauf auf
denselben kaputten Input verbrennt. Ein fehlender, leerer oder unveränderter Claude-Entwurf
**eskaliert** — ohne ihn gibt es nichts zu synthetisieren. Die Stage ist über die
Entwurfs-DATEIEN idempotent, nicht über den State: ein vorhandener, nicht leerer Entwurf
überspringt seinen Autor beim Resume.

**RED-Beweis im Build (Phase 3):** Der Beweis gehört dem Orchestrator, nicht der Behauptung des
Agenten — bewiesen wird, dass die markierten Gates vor dem Implementierungs-Lauf rot waren; „nur
Tests" ist eine Anweisung an den Agenten, denn der Orchestrator kann Test- und Produktivdateien
nicht stack-neutral unterscheiden. Er gilt nur für den Initial-Build einer Lane — Fix-Dispatches aus den Review-/E2E-Phasen
(`pending_task` gesetzt) und jeder Resume nach dem Beweis überspringen die Stufe. Gegen
gefälschte Beweise: ein Test-Lauf, der Dateien löscht oder den Worktree unverändert lässt,
eskaliert, und grüne Gates zählen nur, solange die Tests, die RED bewiesen haben, noch da sind.
Ohne `tdd: true`-Gate baut eine Lane einstufig wie bisher.

**Codex-Review-Loop-Policy** (Authoring-Loops in Phase 1–2 und Code-Review in Phase 5):
Runde 1 behandelt alle Findings (P1–P3), Runde 2 nur noch P1+P2, ab Runde 3 nur noch P1 —
Findings unterhalb der aktuellen Schwelle werden als Follow-ups/Known Limitations dokumentiert
statt gefixt. Ab Runde 2 erhält Codex die Findings der Vorrunden inkl. Disposition
(Fix dispatcht / nicht übernommen + Grund) und meldet erledigte Punkte nicht erneut.
Hartes Cap: 5 Runden — offene P1 eskalieren dann; sonst wird das Artefakt mit dokumentierten
Known Limitations akzeptiert.

**Eskalation:** Jedes erschöpfte Limit und der Circuit-Breaker (eine Fix-Iteration löst
**nichts** auf → sofort abbrechen) beendet den Run mit Exit-Code ≠ 0 und einem
Eskalations-Report (`.adw/runs/<run_id>/escalation.md`): was erreicht, was offen, warum.

**Konfigurierbare Haltepunkte:** Neben den zwei fest verdrahteten Authoring-Approvals (nach
Spec, nach Plan) aktiviert eine optionale Liste `breakpoints:` in `.adw/config.yaml` bis zu
zwei zusätzliche Halte vor den teuren, schwer umkehrbaren Schritten. Genau zwei Werte sind
zulässig: `before_integration` (nach Abschluss aller Build-Lanes, bevor Integrations-/Merge-
oder nachfolgende Review-Arbeit beginnt — im Single-Lane-Betrieb: nach der Build-Lane, vor
`codex_review`) und `before_push` (nach dem finalen Review, bevor JEGLICHE CI-Phasen-Arbeit
beginnt: kein Push, keine CI-/E2E-Vorbereitung, kein Polling). Erreicht der Lauf einen aktiven
Haltepunkt, pausiert er über den **bestehenden** Approval-Pfad — persistierte Phase
`awaiting_approval`, Exit-Code 2, Fortsetzung mit `adw approve <run_id> --repo <pfad>` — und
hält im eigenen State-Feld `pending_breakpoint` (`before_integration`/`before_push`/null) fest,
welcher Haltepunkt wartet. Das `Phase`-Literal wird **nicht** erweitert (kein neuer
Phasenwert); Phasenleiste, Retention und Recovery-Karte bleiben unverändert. Jeder Halt ist ein
`approval`-Event (`gate` = Haltepunktname, `event` = `awaited` beim Eintreten, `granted` bei
der Freigabe), sodass GUI und Timeline ihn ohne Sonderfall darstellen; je tatsächlichem
Eintreten steht genau ein `awaited` im Log, idempotent nachgeholt auch über einen Crash
zwischen State-Save und Event-Write. Ein freigegebener Haltepunkt hält kein zweites Mal — auch
nicht nach Crash + `resume`; ein `resume` an einem noch nicht freigegebenen Haltepunkt bleibt
wartend; `adw approve` auf einen nicht wartenden Lauf ist ein sauberer Fehler. `--no-approval`
(`skip_approval`, auch über `--gates none`) überspringt auch die Haltepunkte — EIN Schalter für
„keine menschliche Freigabe in diesem Lauf". Default (kein Schlüssel oder leere Liste):
heutiges Verhalten, unverändert.

## 5. Schnittstellen

### CLI

```
adw run --repo <pfad> (--issue "Text" | --gitlab-issue <id> | --github-issue <nr>)
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
      # tdd: true markiert das Gate, dessen Fehlschlag im Initial-Build RED beweist
      - {name: pytest, cmd: "pytest -x -q", timeout: 1800, tdd: true}
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
  provider: gitlab             # optional: gitlab | github; sonst Auto-Erkennung via origin-URL
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
- `.adw/spec-summary.md`, `.adw/plan-summary.md` — die Zusammenfassung der Synthese je Phase,
  Entscheidungsgrundlage am Freigabe-Gate: kurz, in der Sprache des Issue-Texts, mit Was/Warum,
  Kernentscheidungen, Scope-Grenzen/Deferred, Herkunft (was kam aus welchem Entwurf, wo
  widersprachen sie sich) und offenen Punkten. Sie läuft als Loop-Artefakt mit (sie muss
  entstehen und darf nicht leer sein, sonst Eskalation; die Fix-Runden halten sie aktuell), ist
  aber **nie** Codex-Review-Referenz und wird in **keine** Build-Lane geseedet. Sie wird in den
  Run-Ordner archiviert und damit nicht mitcommittet.
- `.adw/runs/<run_id>/` — gitignored: `state.json` (RunState), Agent-Transkripte,
  Gate-Outputs, `escalation.md`, die archivierten Artefakte + Zusammenfassungen sowie
  `drafts/` mit den Entwürfen beider Autoren je Authoring-Phase
  (`spec.claude.md` / `spec.codex.md`, `plan.claude.md` / `plan.codex.md`,
  `contract.claude.yaml` / `contract.codex.yaml`, dazu ein `<kind>.codex.FAILED`-Marker,
  wenn ein Codex-Entwurf fehlgeschlagen ist).
- `RunState` (Pydantic): run_id, issue, phase, lanes (worktree, branch, session_id, ports,
  iterations), approval-Status, ci-Status. Nach jedem Phasenübergang persistiert →
  `adw resume` setzt exakt dort fort.

## 6. Design-Prinzipien (verbindlich)

1. **Drei Akteure, klar verteilt** — Gates/Merges/Polling/Dispatch/Triage sind Code
   (`subprocess` mit **echtem `timeout`**-Parameter, immer).
2. **Reviewer ≠ Fixer.**
3. **Jeder Fix nimmt den validierten Pfad** (alle Gates, keine Ausnahme).
4. **Strukturierte Übergaben:** JSON/Pydantic zwischen allen Nodes, kein Freitext-Parsing.
5. **Modell-Ökonomie:** Opus schreibt die Spec-/Plan-Entwürfe und baut; Fable 5 nur Synthese und
   finaler Review; Sonnet 5 triagiert.
6. **Sicherheit:** `allowed_tools` pro Agent aus der Registry; Build-Agents via `cwd` auf ihren
   Worktree begrenzt; Env-Whitelist für alle Subprozesse (kein Secret-Leakage);
   niemals pauschales Permission-Skipping.
7. **Session-Resume statt Kontext-Neuaufbau** in allen Fix-Zyklen (SDK `resume=session_id`).

## 7. Technik

- Python ≥ 3.12, **uv** (pyproject.toml + uv.lock), Package `adw/`, Entry-Point `adw` (typer).
- `claude-agent-sdk` (query + ClaudeAgentOptions: model, cwd, resume, allowed_tools,
  system_prompt-preset `claude_code` + append, permission_mode). Das SDK spawnt die
  **Claude-Code-CLI** als headless Subprocess — Auth/Abrechnung laufen über den
  gespeicherten CLI-Login (Plan-Kontingente), erzwungen via stored-login-only +
  Blanking der API-Key-Env-Variablen; kein token-by-token-API-Pfad. Fehlgeschlagene
  Agent-Aufrufe (z. B. Limit erschöpft) beenden den Run kontrolliert OHNE Eskalation
  — `adw resume` setzt nach dem Reset am Checkpoint fort.
- Codex als CLI-Subprocess (`codex exec --sandbox read-only`), kein zweites SDK — sowohl für
  Reviews (`review`) als auch als Entwurfs-Autor (`author`); weil die read-only-Sandbox nicht
  schreiben darf, liefert der Author-Aufruf die Dateiinhalte in Marker-Blöcken mit einer Nonce
  je Aufruf zurück, die der Orchestrator persistiert.
- `glab` für GitLab bzw. `gh` für GitHub (Issue lesen, Pipeline-/Actions-Status),
  `git worktree` für Lanes,
  Ports deterministisch aus run_id (Basis-Port + Hash-Offset, Socket-Bind-Check als Fallback).
- Agent- und Codex-Aufrufe hinter je einem Interface (`AgentRunner`, `CodexClient`);
  `--dry-run` injiziert Mocks mit kanonischen Fixtures (simulierte Gate-Fails,
  Review-Findings) — kompletter v1-/v2-Kontrollfluss ohne Tokens testbar.

## 8. Akzeptanzkriterien (Definition of Done)

1. `adw run --repo <test-repo> --issue "…" --dry-run` durchläuft alle 7 Phasen (Single-Lane)
   ohne Token-Verbrauch — inklusive beider Entwürfe und beider Zusammenfassungen je
   Authoring-Phase im Run-Ordner; `--dry-run --parallel` durchläuft beide Lanes inkl.
   E2E-Triage-Pfad.
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
8. RED-Gate: Eine Lane mit `tdd: true`-Gate läuft Test-Lauf → RED-Check über genau die
   markierten Gates → Implementierung in derselben Session → normaler Gate-Loop; alle
   markierten Gates grün nach dem Test-Lauf eskaliert; `red_confirmed` überlebt Crash + Resume,
   und ab dem gecheckpointeten Test-Lauf wiederholt ein Resume nur noch den RED-Check; ohne markiertes Gate verhält sich der Build exakt wie bisher. Der
   Dry-Run deckt beide Pfade (mit und ohne `tdd`-Gate) mit 0 Tokens ab.
