<div>

<div class="wrap">

<span class="badge">TECHNISCHE SPEZIFIKATION · Stand 2026-07-15 · adw 0.1.0 · main 9b89dd6</span>

# ADW — Funktionsweise, Implementierung, Design-Entscheidungen

Wie der 7-Phasen-Orchestrator gebaut ist: Architektur, Kontrollfluss, Crash-Resume-Engineering, Sicherheitsmodell und Tech-Stack.

</div>

</div>

<div role="main">

<div id="tldr" class="section tldr">

## ⚡ Auf einen Blick

- **Architekturprinzip:** „Kontrollfluss ist Code, nicht Prompt." Loops, Gates, Merges, Dispatch, Triage, Limits, CI-Polling = deterministisches Python (0 Tokens). Agenten liefern ausschließlich Urteilsvermögen — hinter zwei schmalen Interfaces (`AgentRunner`, `CodexClient`), die per Mock ersetzbar sind.
- **Dual-Authoring in Phase 1–2:** Spec und Plan entstehen je **zweimal parallel und unabhängig** — einmal durch einen Claude-Agenten (Opus), einmal durch Codex (`author`) — und werden anschließend von einem Synthese-Agenten (Fable) zu EINEM Best-of-Artefakt plus einer Zusammenfassung fürs Freigabe-Gate gemerged.
- **Aufbau:** Package `adw/` mit deterministischem Kern (config, state, findings, gates, worktrees, triage, ci), zwei Agent-Adaptern (Claude Agent SDK, Codex CLI), Phasen-Orchestrierung (`phases.py`, ~1.300 Zeilen) und typer-CLI.
- **Verlässlichkeit:** Jeder Übergang und jedes offene Feedback wird atomar in `state.json` checkpointed (flock + tmp/rename); Gate-Beweise sind an Baum-Hashes gebunden; alle Loops haben harte Limits + Circuit-Breaker.
- **Sicherheit:** Tool- und Pfad-Whitelists je Agent, sandboxed Bash im Worktree, Env-Whitelist für alle Subprozesse, Commits nur durch den Orchestrator, unveränderliche Artefakte (Restore nach jedem Agent-Lauf).
- **Stack:** Python ≥ 3.12, uv, pydantic v2, typer, PyYAML, claude-agent-sdk (≥ 0.2.118, spawnt die **Claude-Code-CLI** headless — Auth/Abrechnung über den Plan-Login, kein API-Token-Payment), anyio; extern: codex-CLI, glab (GitLab), gh (GitHub), git. 350 pytest-Tests, mocks-only (kein Netz, keine Tokens), echtes git.

</div>

<div id="kernaussagen" class="section">

## Kernaussagen

<div class="kern">

<div class="card">

**Drei Akteure, klar getrennt.** Claude-Agenten (SDK), Codex (CLI-Subprocess) und deterministischer Code. Reviewer fixen nie; jeder Fix nimmt den validierten Pfad durch alle Gates.

</div>

<div class="card">

**Strukturierte Übergaben statt Freitext.** Alle Reviewer antworten im selben Findings-JSON-Schema; der Parser ist bewusst strikt — ein Parse-Fehler ist safe, ein falsches „ok" nicht.

</div>

<div class="card">

**Misstrauen als Designannahme.** Der Orchestrator verifiziert Agent-Ergebnisse kryptographisch-nah (Tree-Hashes, HEAD-Invarianten, Branch-Checks) statt ihnen zu glauben.

</div>

<div class="card">

**v1 = v2 mit einer Lane.** Ein Codepfad, eine CLI; `--parallel` aktiviert Mehr-Lanen-Betrieb, Integration und E2E — keine getrennten Skripte.

</div>

</div>

</div>

1.  [Architektur & Modul-Landkarte](#architektur)
2.  [Kontrollfluss der sieben Phasen](#phasen)
3.  [Agent-Registry & Modell-Ökonomie](#agenten)
4.  [Findings-Schema & strikter Parser-Kontrakt](#findings)
5.  [State, Checkpoints & Crash-Resume](#state)
6.  [Limits & Circuit-Breaker](#limits)
7.  [Sicherheitsmodell](#sicherheit)
8.  [Design-Entscheidungen (mit Begründung)](#entscheidungen)
9.  [Pakete, Frameworks & externe Werkzeuge](#stack)
10. [Test-Strategie](#testing)
11. [Known Limitations](#limitations)
12. [Glossar](#glossar)

## 1. Architektur & Modul-Landkarte

                              ┌────────────────────────────────────────────┐
      adw run/resume/approve  │  cli.py (typer)                            │
      ────────────────────────▶  Argument-Validierung · Runner-Verdrahtung │
                              │  Dry-Run-Fixtures · Exit-Codes 0/2/1       │
                              └───────────────┬────────────────────────────┘
                                              │ RunContext (repo, config, state,
                                              │ agents, codex, run_glab, sleep, …)
                              ┌───────────────▼────────────────────────────┐
                              │  phases.py — Orchestrierung der 7 Phasen   │
                              │  Loops · Limits · Dispatch · Triage ·      │
                              │  Eskalation · State-Übergänge              │
                              └──┬──────────┬──────────┬───────────┬───────┘
                 Urteilsvermögen │          │          │           │ deterministischer Kern
            ┌────────────────────▼──┐  ┌────▼──────┐  ┌▼──────────┐│
            │ agents.py             │  │ codex.py  │  │ gates.py  ││ config.py   findings.py
            │ SdkAgentRunner        │  │ CodexRunner│ │ worktrees │▼ state.py    triage.py
            │ (Claude Agent SDK)    │  │ (codex exec│ │ ci.py     │  mock.py (Dry-Run/Tests)
            └───────────────────────┘  │ read-only) │ └───────────┘
                                       └────────────┘

| Modul              | Verantwortung                                                                                                                                                          | Kern-API                                                                                                                                        |
|--------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| `adw/cli.py`       | typer-Eingang: `run/resume/approve/status`, Issue-Intake (Text oder `glab issue view`), Dry-Run-Verdrahtung, Exit-Codes                                                | `app`, `_build_context()`, `_execute()`                                                                                                         |
| `adw/phases.py`    | Die 7 Phasen über einem `RunContext` — sämtliche Loops, Limits, Dispatch, Triage, Eskalation                                                                           | `run_spec_and_plan`, `run_build_phase`, `run_integration_phase`, `run_codex_review_phase`, `run_final_review_phase`, `run_ci_phase`, `escalate` |
| `adw/agents.py`    | Agent-Registry (Modelle, Tools, Prompts, Permissions) + `SdkAgentRunner` über das Claude Agent SDK; Login-Pflicht, Env-Härtung, Pfad-Deny-Regeln                       | `REGISTRY`, `AgentRunner`-Protokoll, `AgentResult(text, session_id)`                                                                            |
| `adw/codex.py`     | Codex-CLI als read-only-Subprocess mit isoliertem `CODEX_HOME`; baut Review- UND Authoring-Prompts (Schema bzw. Marker-Protokoll eingebettet), parst strikt            | `CodexRunner.review(kind, content_refs, cwd)`, `CodexRunner.author(kind, task, cwd)`, `CodexClient`-Protokoll                                   |
| `adw/findings.py`  | Findings-Schema (pydantic, `extra="forbid"`) + strikter Parser + geteilte Schema-Instruktion für alle Reviewer-Prompts                                                 | `Finding`, `ReviewResult`, `extract_review_result`, `SCHEMA_INSTRUCTION`                                                                        |
| `adw/config.py`    | Loader für `.adw/config.yaml`, fail fast (StrictLoader lehnt Duplikate ab; Lane ohne Gates, Gate ohne Timeout → Fehler)                                                | `AdwConfig.load(repo)`, `Gate`, `E2eConfig`, `CiConfig`                                                                                         |
| `adw/state.py`     | Persistierter Run-Zustand: atomare Snapshots, Transaktionen, monotone Sequenz                                                                                          | `RunState`, `LaneState`, `save/load/update/find_latest`                                                                                         |
| `adw/gates.py`     | Gate-Runner: `subprocess` mit echtem Timeout, Prozessgruppen-Kill, RAM-bounded Output-Tail (200 Zeilen à max. 4 KiB)                                                   | `run_gates(gates, cwd, extra_env) → GateReport`                                                                                                 |
| `adw/worktrees.py` | Lane-Worktrees (idempotent, Ready-Marker, Recovery von Partial-Adds) + deterministische Ports mit Bind-Check                                                           | `create_lane_worktree`, `remove_lane_worktree`, `ports_for`, `lane_branch`                                                                      |
| `adw/triage.py`    | Reine Funktionen: Finding-Routing, Iterations-Limits, Circuit-Breaker                                                                                                  | `triage_final_review`, `check_gate_iterations/fix_cycles/progress`                                                                              |
| `adw/ci.py`        | GitLab-Polling über glab (SHA-gebunden), Job-Log-Abruf, injizierbare Zeit                                                                                              | `poll_pipeline(…, sha=…)`, `fetch_failed_job_logs`                                                                                              |
| `adw/github.py`    | GitHub-Actions-Polling über gh — gleiches Ergebnis (`CiResult`) und gleiche Fehlerklassen wie ci.py; „grün" = alle Workflow-Runs der Push-SHA completed und keiner rot | `poll_ci(…, run_gh, sha=…)`                                                                                                                     |
| `adw/forge.py`     | Hosting-Erkennung GitLab/GitHub: `ci.provider`-Override gewinnt, sonst Hostname der origin-URL; unbekannter Host → fail fast                                           | `detect_forge(repo, override)`                                                                                                                  |
| `adw/env.py`       | Env-Whitelist für alle Subprozesse (kein Secret-Leakage)                                                                                                               | `safe_env(extra)`                                                                                                                               |
| `adw/mock.py`      | Skriptbare Mock-Runner: Antwort-Queues je Agent, simulierte Datei-Outputs (statisch oder als `Callable(cwd)`), Aufruf-Aufzeichnung                                     | `MockAgentRunner`, `MockCodexRunner`                                                                                                            |

Verbindendes Element ist der **`RunContext`** (dataclass): Repo-Pfad, geladene Config, `RunState`, die beiden Runner-Interfaces sowie injizierbare Seams für glab (`run_glab`) und Zeit (`sleep`). Ein `threading.RLock` serialisiert State-Mutationen über parallele Lane-Threads. Weil **alle** Außenwelt-Kontakte (Agenten, Codex, glab, sleep, Push) über diesen Kontext laufen, ist derselbe Produktions-Codepfad vollständig mit Mocks betreibbar — das ist die technische Grundlage von `--dry-run` und der gesamten Testsuite.

## 2. Kontrollfluss der sieben Phasen

Jede Phasenfunktion prüft selbst, ob sie „dran" ist (`state.phase`-Guard) — die CLI ruft immer die komplette Kette; ein Resume startet dadurch automatisch in der richtigen Phase.

Phase 1–2 — `run_spec_and_plan`: Authoring-Loops + Approval-Gate

<div class="inner">

Jede der beiden Phasen fährt zuerst eine **Draft-Stage** (`_draft_stage`): Der Claude-Entwurfs-Autor (`spec_agent`/`plan_agent`, Opus) und `ctx.codex.author(kind, …)` erzeugen ihre Entwürfe **gleichzeitig** (`ThreadPoolExecutor`, 2 Worker). Der Claude-Autor schreibt wie bisher in den Checkout; der Orchestrator kopiert das Ergebnis nach `.adw/runs/<id>/drafts/<name>.claude.<ext>` (atomar via tmp + `os.replace`) und setzt den Checkout danach zurück. Codex liefert seinen Inhalt read-only und landet als `<name>.codex.<ext>`: Weil die read-only-Sandbox nicht schreiben darf, fordert `author()` die Dateiinhalte in Marker-Blöcken (`===BEGIN <name> <nonce>===` … `===END …===`) mit einer Nonce je Aufruf an und parst sie strikt vorwärts ab dem letzten Begin-Marker des ersten Namens — ein reiner Prompt-Echo ergibt leere Blöcke und damit einen `CodexAuthorError` (Subklasse von `CodexError`), und eine Spec, die dieses Protokoll selbst dokumentiert, beendet ihren eigenen Block nicht. Idempotenz läuft über die DATEIEN, nicht über den State: ein vorhandener, nicht leerer Entwurf überspringt seinen Autor, ein `<kind>.codex.FAILED`-Marker verhindert einen erneuten Codex-Versuch. Crash-Reste der Phase im Checkout (Artefakte *und* Summary) werden im Hauptthread geräumt, **bevor** ein Autor läuft — ein Rest darf weder als frischer Entwurf durchgehen noch dem parallel lesenden Codex als Zwischenstand erscheinen. Ein Codex-Ausfall **degradiert** nur (Warnung + Marker, einquellige Synthese); ein fehlender, leerer oder unveränderter Claude-Entwurf eskaliert.

Der **Synthese-Agent** (`spec_synthesis`/`plan_synthesis`, Fable) ist danach der ERSTE Lauf des Authoring-Loops. Sein Task nennt `.adw/issue.md`, beide Entwurfspfade und die Zielartefakte inklusive Summary; fehlt der Codex-Entwurf, steht statt eines toten Pfads der Hinweis auf die Ein-Quellen-Basis im Task. Die **Zusammenfassung** (`spec-summary.md`/`plan-summary.md`) läuft als Loop-Artefakt mit: Sie muss entstehen (fehlend ODER leer eskaliert — sie reviewt niemand, ein Weißraum-Ergebnis fiele sonst erst dem Menschen am Gate auf), die Fix-Tasks bekommen den Zusatz, sie aktuell zu halten, und sie wird wie die Artefakte archiviert. In der Plan-Phase ist sie `protected` wie `spec.md`, sie steht **nicht** in `_seed_artifacts` (keine Build-Lane baut dagegen) und ist **keine** Codex-Review-Referenz. Die Gate-Meldungen der CLI nennen die Zusammenfassung zuerst — sie liegt im ignorierten Run-Ordner und wäre sonst unsichtbar.

Gemeinsamer Baustein ist der **Reviewed-Authoring-Loop**: Agent schreibt Artefakt(e) → Codex reviewt (`kind=spec` bzw. `plan`, der Plan-Review bekommt die Spec als Referenz mit) → Findings gehen als Folge-Task an **dieselbe SDK-Session** (`resume=session_id`) → bis Verdict `ok`, maximal 5 Runden (`AUTHORING_MAX_ROUNDS`). Je Runde sinkt die Severity-Schwelle (Runde 1: alle Findings, Runde 2: P1+P2, ab Runde 3: nur P1 — darunter liegende Findings werden als Known Limitations akzeptiert statt weiter iteriert), und ab Runde 2 erhält Codex die Vorrunden-Findings inkl. Disposition als Review-Kontext, damit erledigte oder bewusst abgewiesene Punkte nicht erneut gemeldet werden. Am Cap: offene P1 → Eskalation, sonst Accept + Known-Limitations-Report. Absicherungen:

- **Prior-Content-Check:** Ein Altbestand (z. B. gemergte Artefakte eines früheren Runs) adelt keinen untätigen Agenten — das Artefakt muss sich beim Erstlauf ändern, sonst Eskalation.
- **Protected Files:** `.adw/config.yaml` und (ab Phase 2) die reviewte Spec werden nach *jedem* Agent-Lauf byte-genau restauriert — Agents können sie technisch beschreiben, aber nie effektiv ändern.
- **Uncommitted-Guard:** Getrackte Artefakte mit Nutzer-Änderungen brechen den Lauf ab, statt Edits still zu verwerfen; eigene (gecrashte) Zwischenstände sind als Ausnahme erkannt.
- **Archivierung:** Reviewte Artefakte wandern nach `.adw/runs/<id>/`; der Haupt-Checkout wird auf den eingecheckten Stand zurückgesetzt und bleibt sauber.

Danach: `phase=awaiting_approval`, State gespeichert, `AwaitingApproval`-Exception → CLI Exit 2. `--no-approval` bzw. `approval_granted` überspringt die Pause; beides ist im State persistiert.

</div>

Phase 3 — `run_build_phase` / `_run_lane`: der Gate-Loop

<div class="inner">

Lanes laufen sequenziell oder (bei `--parallel`) im `ThreadPoolExecutor`. Pro Lane:

1.  Worktree erzeugen (idempotent), Fork-Punkt als `base_sha` pinnen, Artefakte hineinkopieren und committen.
2.  **RED-Stufe** (`_confirm_red`, nur bei ≥ 1 Gate `tdd: true` und nur im Initial-Build — kein `pending_task`, `red_confirmed` false, `gate_iterations == 0`): ein reiner Test-Lauf des Agents mit denselben Invarianten wie jeder Build-Lauf, danach `_run_lane_gates` über **genau die markierten Gates**. Mindestens eines rot = Beweis: `red_confirmed`, die geänderten Pfade als `red_test_paths` und der Implementierungs-Task als `pending_task` landen in **einem** Save, sodass der folgende Loop dieselbe Session mit dem gekürzten roten Gate-Output fortsetzt. Alle grün → Eskalation (die Tests decken das geforderte Verhalten nicht ab), kein Retry-Loop.
3.  **Loop:** Limit-Check → Checkpoint (HEAD als `expected_head`, Iteration++) → Build-Agent (Opus, Session-Resume, Nachbar-Lanes per Deny-Regel unsichtbar) → Invarianten-Checks (kein Agent-Commit, richtiger Branch) → Artefakt-Restore → Gates.
4.  Gates rot: Fehlerausgabe + Circuit-Breaker-Basis als `pending_task`/`last_failures` persistieren, zurück zu 3. Gates grün: `red_test_paths` noch vorhanden (sonst Eskalation), dann `gates_passed`-Beweis **mit Baum-Hash** persistieren, dann committet der Orchestrator.

Der RED-Beweis gehört dem Orchestrator, nicht der Behauptung des Agenten: Bewiesen wird, dass die markierten Gates vor dem Implementierungs-Lauf rot waren — „nur Tests" ist eine Anweisung an den Agenten, denn keine stack-neutrale Regel unterscheidet Test- von Produktivpfaden. Nach beiden Seiten ist er fälschungssicher: Ein Test-Lauf, der den Worktree unverändert lässt oder Dateien **löscht**, beweist nichts (beide Prüfungen laufen auch im Resume-Pfad, nach dem Artefakt-Restore), und grüne Gates zählen nur, solange die Tests, die RED bewiesen haben, noch da sind. Der RED-Check verbraucht **keine** Gate-Iteration — das Budget gehört der Implementierung. Fix-Dispatches aus Phase 4–7 (`pending_task` gesetzt) und Lanes ohne markiertes Gate behalten das einstufige Verhalten Byte für Byte.

Der Baum-Hash entsteht über einen temporären Git-Index (`GIT_INDEX_FILE`, geseedet aus HEAD, dann `add -A` + `write-tree`) — er bindet den „Gates waren grün"-Beweis an exakt den geprüften Inhalt inklusive untracked Files. Beim Resume wird auch eine als `completed` markierte Lane dagegen revalidiert; stimmt der Baum nicht mehr, geht die Lane zurück in den Loop statt ungeprüft weitergereicht zu werden.

</div>

Phase 4 — `run_integration_phase` / `_integration_loop`: Merge + E2E

<div class="inner">

Der Integrations-Branch wird **je Runde frisch** ab Base-Branch aufgebaut (Worktree + Branch löschen, neu anlegen, Lane-Branches mergen) — dadurch ist die Operation idempotent und crash-sicher, es gibt keinen halb-gemergten Zustand, den ein Resume interpretieren müsste. Merge-Konflikt oder Merge-Timeout → `merge --abort` + Eskalation. Das E2E-Gate läuft mit den Ports beider Lanes im Env. Bei Rot: Runden-Zähler persistieren → E2E-Triage-Agent (Sonnet 5) → Findings über `_dispatch_lane_fixes` in die Lanes (voller Gate-Loop!) → neu integrieren. `_integration_loop` ist bewusst wiederverwendbar: Auch die Review-Phasen holen sich darüber ihren Worktree — **jeder Review-Fix läuft wieder durch Merge + E2E**, nicht nur durch die Lane-Gates.

</div>

Phase 5–6 — Codex-Review + finaler Review + Triage

<div class="inner">

Vor jedem Review: `_resume_pending_lanes` schickt **jede** Lane durch `_run_lane` — unfertige Fixes (Crash-Fenster) holen Gates + Commit nach, fertige werden per Tree-Hash revalidiert. Kein ungegateter Stand erreicht je ein Review.

**Phase 5:** `codex.review("code", <geänderte Dateien via 3-Punkte-Diff>, cwd=Review-Worktree)`. `needs_fixes` → Runde persistieren, Limit-Check *vor* dem Dispatch (kein „Terminal-Fix", den nie wieder ein Review prüft), Circuit-Breaker auf identische Finding-Mengen, Dispatch in die Lanes, Re-Review. Es gilt dieselbe **Review-Loop-Policy** wie im Authoring: max. 5 Runden (`MAX_REVIEW_ROUNDS`), absteigende Severity-Schwelle je Runde (1: alle, 2: P1+P2, ab 3: nur P1 — Gedroppte wandern nach `followups.md`), Vorrunden-Findings + Disposition gehen als Kontext an Codex; Circuit-Breaker und Dispatch arbeiten nur auf der actionable Finding-Menge.

**Phase 6:** Finaler Reviewer (Fable, read-only) antwortet im Findings-JSON **mit Pflicht-`category`** (fehlt sie, ist keine Triage möglich → Eskalation statt stiller Default). `triage_final_review` (reiner Code) trennt: `scope_gap` → deduplizierter Follow-up-Report; Rest → Fix-Zyklen je Lane (max. 3). Das Zyklus-Inkrement wird **im selben Save** wie der gestagte Fix-Task persistiert (`mutate_staged`-Hook im Dispatch) — ein Crash kann kein Budget verbrennen, ohne dass der zugehörige Fix beim Resume nachgeholt wird.

</div>

Phase 7 — `run_ci_phase`: Push + Pipeline-Überwachung

<div class="inner">

Push per `git push --force-with-lease -u origin <branch>` (der Integrations-Branch wird je Runde neu aufgebaut, non-fast-forward ist also erwartet; fremde Remote-Änderungen werden trotzdem nie überschrieben). Die Forge wird per `ci.provider` bzw. origin-URL bestimmt (`forge.py`; im Dry-Run Fallback gitlab, sonst fail fast). GitLab: `poll_pipeline` fragt die Pipeline **server-seitig SHA-gefiltert** ab (`glab api projects/:id/pipelines?ref=…&sha=…`); GitHub: `github.poll_ci` pollt alle Workflow-Runs der Push-SHA (`gh api …/actions/runs?head_sha=…`) bis alle completed sind, rote Runs liefern `gh run view --log-failed`-Excerpts: weder bewertet die terminale Pipeline des vorherigen Pushes das neue Ergebnis, noch kann eine fremde neuere Pipeline die gesuchte verdecken. Das Zeitbudget wird als Restzeit geführt und der Sleep darauf gekappt. Rot mit Logs → Log-Analyst (Sonnet 5, cwd = gepushter Worktree) → ein Re-Entry über die Lane-Loops; rot *ohne* Logs (canceled/YAML-Fehler) → direkte Eskalation statt Analyse auf Null-Evidenz.

</div>

## 3. Agent-Registry & Modell-Ökonomie

Jeder Agent ist ein deklarativer `AgentSpec` (Modell, Tool-Restriktion, Pfad-Regeln, System-Prompt-Zusatz, Permission-Mode). Der `SdkAgentRunner` übersetzt ihn in `ClaudeAgentOptions` (system_prompt-Preset `claude_code` + append, `cwd`, `resume`, isolierte Settings: keine repo-kontrollierten Hooks/MCP-Server).

| Agent            | Modell                       | Werkzeuge                                                            | Auftrag / harte Regel                                                                               |
|------------------|------------------------------|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| `spec_agent`     | Opus 4.8 (`claude-opus-4-8`) | Read/Grep/Glob + Write **nur** `.adw/spec.md`                        | Entwurfs-Autor der Spezifikation nach fester Vorlage; implementiert nie                             |
| `plan_agent`     | Opus 4.8                     | Read/Grep/Glob + Write **nur** `.adw/plan.md` + `.adw/contract.yaml` | Entwurfs-Autor von Plan (Workstreams) + Schnittstellen-Kontrakt; implementiert nie                  |
| `spec_synthesis` | Fable 5 (`claude-fable-5`)   | Read/Grep/Glob + Write **nur** `.adw/spec.md` + `.adw/spec-summary.md` | Best-of-Merge aus Claude- und Codex-Spec-Entwurf + Zusammenfassung fürs Freigabe-Gate             |
| `plan_synthesis` | Fable 5                      | Read/Grep/Glob + Write **nur** `.adw/plan.md`, `.adw/contract.yaml` + `.adw/plan-summary.md` | Best-of-Merge beider Plan-/Kontrakt-Entwürfe + Zusammenfassung              |
| `build_agent`    | Opus 4.8 (`claude-opus-4-8`) | Read/Write/Edit + **sandboxed** Bash, Schreiben nur worktree-relativ | Workstream strikt gegen Kontrakt, TDD; **committet nicht**; darf von Fix-Plänen begründet abweichen |
| `e2e_triage`     | Sonnet 5 (`claude-sonnet-5`) | read-only                                                            | Playwright-Fehler → Lane-Zuordnung; fixt nichts; antwortet nur Schema-JSON                          |
| `log_analyst`    | Sonnet 5                     | read-only                                                            | CI-Logs → strukturierte Findings mit Lane; fixt nichts                                              |
| `final_reviewer` | Fable 5                      | strikt read-only (kein Write/Edit/Bash)                              | Implementierung gegen Spec; nur Findings, Pflichtfeld `category`                                    |

**Modell-Ökonomie:** Opus schreibt die Authoring-Entwürfe und baut — der Entwurf ist der tief denkende Teil; Fable (breit, günstiger) merged die beiden Entwürfe und macht den finalen Review; Sonnet 5 erledigt die Fließband-Triage. Die inhaltlichen Vorgaben von Entwurfs-Autor und Synthese liegen in je einer geteilten Konstante (`_SPEC_CONTENT_RULES`/`_PLAN_CONTENT_RULES`): Driften sie auseinander, kippt die Synthese die Scope-Gegenkraft des Autors wieder um. Merge- und Summary-Vorgaben (`_BEST_OF_MERGE_RULES`, `_SUMMARY_FORMAT_RULES`) teilen sich beide Synthese-Agents — eine je Phase anders aufgebaute Zusammenfassung wäre am Freigabe-Gate nur Rauschen. Die Pfad-Regeln sind **artefakt-exakt** (z. B. `Write(.adw/spec.md)` statt `Write(.adw/**)`) — ein pauschales `.adw/**` würde sonst auch `.adw/runs/<id>/state.json` beschreibbar machen; die Schreibrechte der Synthese-Agents umfassen deshalb explizit ihre Summary-Datei. Zusätzlich sind die Run-Verzeichnisse für alle Agents deny-gelistet und parallele Lanes sehen einander per `deny_read_paths` nicht.

## 4. Findings-Schema & strikter Parser-Kontrakt

Alle Reviewer (Codex und Claude-Reviewer) antworten im selben JSON-Schema; die Instruktion dazu (`SCHEMA_INSTRUCTION`) ist **eine geteilte Konstante** in `findings.py` und wird in jeden Reviewer-Prompt eingebettet:

    {
      "verdict": "ok | needs_fixes",
      "findings": [{
        "severity": "P1 | P2 | P3",
        "lane": "frontend | backend | unknown",
        "file": "pfad/relativ/zum/repo",
        "issue": "Beschreibung des Problems",
        "remediation_plan": ["Schritt 1", "Schritt 2"],
        "category": "scope_gap | implementation | trivial"   // nur finaler Reviewer
      }]
    }

Validierung strikt via pydantic: `extra="forbid"`, Verdict-Findings-Konsistenz (`ok` nur mit leerer Liste, `needs_fixes` braucht ≥ 1 Finding), Pflichtfelder. Der Parser `extract_review_result` akzeptiert **ausschließlich** (a) Output, der als Ganzes ein JSON-Objekt ist, oder (b) den Inhalt des **letzten** ```` ```json ````-Fence. Alles andere — Prosa um nacktes JSON, Entwürfe, abgeschnittene Antworten, unclosed Fences — ist ein `FindingsParseError` und damit Eskalationsfall. Adversariale Inputs (Duplicate Keys, Nesting \> 100, Integer \> 100 Stellen) failen geschlossen in linearer Zeit. In den Phasen wird zusätzlich `ValidationError` gefangen: schema-verletzendes, aber valides JSON eskaliert genauso sauber wie kaputtes.

## 5. State, Checkpoints & Crash-Resume

`RunState` (pydantic) ist die einzige Wahrheitsquelle für den Fortschritt eines Runs. Persistenz-Mechanik:

- **Atomar:** Snapshot in Temp-Datei + `os.replace` — es existiert nie ein halb geschriebener State.
- **Serialisiert:** exklusiver `flock` auf `.adw/runs/.seq` um jeden Write; `RunState.update()` bietet Load→Mutate→Write als Transaktion für parallele Lane-Threads.
- **Monoton:** eine Sequenznummer (statt Datei-mtime mit Kernel-Tick-Granularität) bestimmt `find_latest`; eine korrupte `.seq` wird aus den persistierten States rekonstruiert.

Die wichtigsten Checkpoint-Felder und ihr Zweck

<div class="inner">

| Feld                                                                                   | Ebene    | Überlebt damit                                                                                                      |
|----------------------------------------------------------------------------------------|----------|---------------------------------------------------------------------------------------------------------------------|
| `authoring_session / _pending_task / _last_findings / _rounds / _prior_context`        | Run      | Crash mitten im Spec-/Plan-Review-Zyklus: Session, offener Fix-Task, Circuit-Breaker-Basis, Runden-Budget, Findings-Historie |
| `pending_task`, `last_failures`                                                        | Lane     | Crash zwischen Gate-Fail und Fix-Lauf                                                                               |
| `gates_passed` + `gates_tree`                                                          | Lane     | Crash zwischen „Gates grün" und Commit — der Beweis ist an den exakten Baum-Hash gebunden und damit nicht fälschbar |
| `expected_head`                                                                        | Lane     | Erkennung von Fremd-Commits über ein Crash-Fenster hinweg (Orchestrator-only-Commit-Invariante)                     |
| `session_id` des Test-Laufs                                                            | Lane     | Crash zwischen Test-Lauf und RED-Check: Die gecheckpointete Session markiert den Lauf als erledigt, ein Resume wiederholt nur den Check |
| `red_confirmed` + `red_test_paths`                                                     | Lane     | Crash zwischen RED-Beweis und Implementierungs-Lauf (beide landen mit `pending_task` in einem Save); die Pfade binden den Beweis an die Tests, die ihn geliefert haben |
| `base_sha`                                                                             | Lane     | Fork-Punkt der Lane — Restaurationen nutzen den unbeweglichen Stand, nicht den weiterrückenden Base-Branch          |
| `integration_rounds / review_rounds / fix_cycles / ci_reentries` (+ `*_last_failures`, `review_prior_context`) | Run/Lane | Alle Loop-Budgets — ein Neustart verschafft keine Extra-Versuche; Limit-Checks stehen *vor* teurer Arbeit           |
| `dry_run`, `skip_approval`, `pinned_base_branch`                                       | Run      | CLI-Entscheidungen, die der Resume-Aufruf nicht mehr kennt                                                          |

Durchgängiges Muster: **Budget-Inkremente werden atomar mit der Arbeit persistiert, die sie rechtfertigen** (via `mutate_staged`-Hook im Fix-Dispatch), und **Circuit-Breaker-Baselines erst *nach* dem Fix-Dispatch fortgeschrieben** — sonst würde ein Crash dazwischen beim Resume als „identische Runde" fehl-eskalieren.

</div>

## 6. Limits & Circuit-Breaker

| Loop                           | Limit (Konstante)                                                         | Zusätzlich                                                                                                                               |
|--------------------------------|---------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| Gate-Loop je Lane              | 10 (`MAX_GATE_ITERATIONS`) — pro Task, Reset bei neuem Fix-Task           | Circuit-Breaker `check_progress`: exakt dieselbe Failure-/Finding-Menge wie in der Vorrunde → sofortige Eskalation statt Limit ausreizen; der RED-Check verbraucht keine Iteration |
| Integration/E2E                | 10 Runden (`MAX_E2E_ROUNDS`, run-weit)                                    |                                                                                                                                          |
| Authoring-Loop (Spec/Plan)     | 5 Runden (`AUTHORING_MAX_ROUNDS`)                                         | Severity-Schwelle je Runde: 1 alle, 2 P1+P2, ab 3 nur P1; am Cap P1 → Eskalation, sonst Accept + Known Limitations                       |
| Codex-Code-Review              | 5 Runden (`MAX_REVIEW_ROUNDS`)                                            | Gleiche Severity-Schwelle; Vorrunden-Findings + Disposition als Codex-Kontext, Sub-Schwellen-Findings → `followups.md`                   |
| Fix-Zyklen nach finalem Review | 3 je Lane (`MAX_FIX_CYCLES`)                                              |                                                                                                                                          |
| CI-Re-Entry                    | 1 (`MAX_CI_REENTRIES`)                                                    |                                                                                                                                          |
| RED-Stufe je Lane              | genau 1 Durchlauf (kein Retry-Loop)                                       | Kein Circuit-Breaker: markierte Gates nach dem Test-Lauf grün → Eskalation statt Test-Nachbesserungs-Loop                                |
| Gate-/Codex-/glab-Subprozesse  | Timeout je Gate aus der Config; Codex 900 s; glab 120 s; CI-Budget 2700 s | Prozessgruppen-Kill (`start_new_session` + `killpg`) auf allen Exit-Pfaden — keine Zombie-Prozesse                                       |

## 7. Sicherheitsmodell

| Ebene               | Mechanismus                                                                                                                                                                                                                                                                                                                                                                               |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Agent-Werkzeuge     | `tools=` **restringiert** die verfügbaren Tools (nicht nur Auto-Approve); `allowed_tools` mit artefakt-exakten Pfad-Regeln; Reviewer strikt read-only; Build-Bash sandboxed und auf den Worktree begrenzt.                                                                                                                                                                                |
| Prozess-Umgebung    | `safe_env()`-Whitelist für **alle** Subprozesse (PATH, HOME, LANG, … — keine API-Keys/Cloud-Creds). Das SDK merged `os.environ`; Nicht-Whitelist-Variablen werden per `""`-Override geblankt. `SSH_AUTH_SOCK` nur dem Push-Subprozess, `CODEX_HOME` nur dem CodexRunner.                                                                                                                  |
| Auth & Abrechnung   | Nur gespeicherter Claude-CLI-Login (`_require_stored_login()`, fail fast bevor Tokens fließen) — Verbrauch läuft damit über den Claude-Plan, nie token-by-token gegen die API: `ANTHROPIC_API_KEY` & Co. werden per `""`-Override geblankt. Codex mit isoliertem `CODEX_HOME` (nur `auth.json`, keine user-konfigurierten MCP-Server), Token-Rotation wird per CAS + flock zurückgesynct. |
| Git-Integrität      | Orchestrator-Git immer mit `core.hooksPath=/dev/null` + Env-Whitelist. Commits nur durch den Orchestrator; Agent-Commits, Branch-Wechsel (symbolic-ref-Check) und HEAD-Bewegungen im Crash-Fenster werden erkannt und eskalieren.                                                                                                                                                         |
| Artefakt-Integrität | Spec/Plan/Kontrakt/Config sind für Agents effektiv unveränderlich: byte-genauer Restore nach jedem Agent-Lauf bzw. vor Gates/Commit; Symlink-/Verzeichnis-Ersetzungen werden erkannt und ersetzt, nie verfolgt (kein Schreiben außerhalb der Lane); eine eingeschleuste Config wird aus dem gepinnten Fork-Punkt restauriert oder — positiv verifiziert — gelöscht.                       |
| Lane-Isolation      | Eigener Worktree je Lane; Nachbar-Lanes per `deny_read_paths` unlesbar; Ports deterministisch aus der run_id mit Bind-Check.                                                                                                                                                                                                                                                              |

## 8. Design-Entscheidungen (mit Begründung)

<div class="decision">

**D1 — Kontrollfluss ist Code, nicht Prompt.** Loops, Limits, Dispatch, Merges, Polling als Python statt als Agenten-Anweisungen.

<div class="why">

**Warum:** Wiederholbares gehört in deterministischen Code — 0 Tokens, immer gleiches Verhalten, testbar. Agenten sind nur dort, wo Urteilsvermögen gebraucht wird. (Grundsatz aus der Video-Analyse „Forget Loop Engineering", validiert im Brainstorming.)

</div>

</div>

<div class="decision">

**D2 — Reviewer ≠ Fixer; jeder Fix nimmt den validierten Pfad.** Reviews liefern nur Findings; Fixes laufen ausnahmslos durch Build-Agents + alle Gates (+ E2E im Parallel-Modus).

<div class="why">

**Warum:** „Triviale" Direkt-Fixes umgehen genau die Prüfungen, die Fehler fangen sollen. Der Fix-Plan eines Reviewers ist Empfehlung — der Build-Agent darf begründet abweichen, denn er kennt Spec und Konventionen.

</div>

</div>

<div class="decision">

**D3 — Strikter Parser-Kontrakt statt Toleranz-Heuristiken.** Nur Ganz-JSON oder letzter ```` ```json ````-Fence; alles andere ist ein Fehler.

<div class="why">

**Warum:** Toleranz-Heuristiken sind gegen adversariale/verrauschte Outputs nicht abdichtbar — im Zweifel akzeptieren sie ein veraltetes oder erfundenes „ok". Ein Parse-Fehler ist safe (Retry/Eskalation), ein falsches „ok" nicht. Die Entscheidung fiel, nachdem eine tolerante Prosa-Extraktion im Review-Loop oszillierte.

</div>

</div>

<div class="decision">

**D4 — v1 = v2 mit einer Lane: ein Codepfad, ein Flag.** `--parallel` aktiviert Mehr-Lanen-Betrieb; keine getrennten Skripte.

<div class="why">

**Warum:** Zwei Codepfade divergieren zwangsläufig. Der Single-Lane-Modus ist der Parallel-Modus mit n=1 — dieselben Tests, dieselben Garantien.

</div>

</div>

<div class="decision">

**D5 — Config im Ziel-Repo, fail fast, keine geratenen Defaults.** `.adw/config.yaml` gehört dem Zielprojekt; der Orchestrator ist generisch.

<div class="why">

**Warum:** Gates/Branches/E2E sind Projektwissen. Stille Defaults erzeugen stilles Fehlverhalten — die einzigen Defaults sind die dokumentierten CI-Poll-Werte.

</div>

</div>

<div class="decision">

**D6 — Session-Resume statt Kontext-Neuaufbau.** Fix-Tasks gehen an dieselbe SDK-Session (`resume=session_id`); Session-IDs sind Teil des persistierten States.

<div class="why">

**Warum:** Der Agent, der den Code geschrieben hat, fixt ihn mit vollem Kontext schneller und konsistenter — und es spart die Tokens des Neuaufbaus.

</div>

</div>

<div class="decision">

**D7 — Verifizieren statt vertrauen: Beweise mit Baum-Hashes.** `gates_passed` gilt nur zusammen mit dem `gates_tree`-Hash des exakten Worktree-Inhalts; completed-Lanes werden beim Resume revalidiert.

<div class="why">

**Warum:** Commit-Messages oder Flags wären vom Agenten fälschbar bzw. könnten in Crash-Fenstern veralten. Der Inhalts-Hash bindet den Beweis an genau den geprüften Stand.

</div>

</div>

<div class="decision">

**D8 — Idempotente Rekonstruktion statt Zustands-Interpretation.** Integrations-Worktree je Runde frisch aufbauen; Review-Worktrees jederzeit wiederherstellbar.

<div class="why">

**Warum:** Einen halb-gemergten oder manipulieren Zustand korrekt zu *interpretieren* ist fehleranfällig; ihn wegzuwerfen und deterministisch neu zu erzeugen ist billig und beweisbar korrekt.

</div>

</div>

<div class="decision">

**D9 — Eskalation als First-Class-Ergebnis.** Jedes erschöpfte Limit, jeder Konflikt, jede unlesbare Reviewer-Antwort endet kontrolliert: Report + `phase=escalated` + Exit ≠ 0. Eskalierte Runs sind nicht fortsetzbar.

<div class="why">

**Warum:** Ein Orchestrator, der „irgendwie weitermacht", produziert teuren Müll. Der Mensch bekommt den erreichten Stand und den konkreten Grund — und entscheidet.

</div>

</div>

<div class="decision">

**D10 — Gepinnte Basis: `pinned_base_branch` (Run) + `base_sha` (Lane).** Der effektive Base-Branch wird beim Start persistiert; Lanes pinnen ihren Fork-SHA.

<div class="why">

**Warum:** Base-Branches rücken weiter und Configs ändern sich mid-run. Ohne Pinning würden Fortsetzungen gegen eine andere Historie integrieren, diffen oder pushen als die, von der die Lanes geforkt sind.

</div>

</div>

<div class="decision">

**D11 — Dry-Run als Produkt-Feature, nicht als Testtrick.** `--dry-run` injiziert die Mocks in denselben Produktions-Codepfad, inkl. kanonischer Fehl-Fixtures (synthetisches Gate, das erst der zweite Lauf grün macht; E2E-Rot, das ein triagierter Lane-Fix löst). Die Simulations-Stufe wird aus dem Worktree-Inhalt abgeleitet, damit auch ein Dry-Run-Resume korrekt fortsetzt.

<div class="why">

**Warum:** Config, Gates und der komplette Kontrollfluss lassen sich so ohne Tokens/Netz/Push abnehmen — und die Akzeptanztests (SPEC §8) fahren exakt diesen Pfad.

</div>

</div>

<div class="decision">

**D12 — Codex als CLI-Subprocess, nicht als zweites SDK.** `codex exec --sandbox read-only`, isoliertes `CODEX_HOME`, hartes Timeout.

<div class="why">

**Warum:** Ein unabhängiger Reviewer mit anderem Modell-Stack fängt andere Fehlerklassen; die CLI-Grenze hält die Kopplung minimal und die read-only-Sandbox verhindert Mutationen.

</div>

</div>

<div class="decision">

**D13 — Plan-Abrechnung: Claude-Code-CLI unter der Haube, stored-login-only.** Das Agent SDK spawnt die Claude-Code-CLI als headless Subprocess; ADW erzwingt den gespeicherten CLI-Login (`_require_stored_login()`) und blankt API-Key-Umgebungsvariablen aktiv — es existiert kein token-by-token-API-Pfad. Schlägt ein Agent-Aufruf fehl (typisch: Abo-Fenster erschöpft), fängt die CLI den `AgentRunError`, beendet mit Exit 1 + Resume-Hinweis und lässt den Run am Checkpoint stehen — bewusst KEINE Eskalation, denn `phase=escalated` wäre endgültig und der Zustand ist transient: `adw resume` setzt nach dem Limit-Reset fort.

<div class="why">

**Warum:** Kostenkontrolle strukturell statt disziplinarisch — Verbrauch zählt gegen die Plan-Kontingente (5-h-Fenster/Wochenlimit), ein versehentlicher API-Budget-Abfluss ist technisch unmöglich. Der Preis ist ein Verfügbarkeitsrisiko, das das Crash-Resume-Engineering ohnehin abdeckt.

</div>

</div>

## 9. Pakete, Frameworks & externe Werkzeuge

### Python-Dependencies (pyproject.toml)

| Paket                       | Version      | Rolle im ADW                                                                                                                                                                                                                                                                                                                                                                                                              |
|-----------------------------|--------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `pydantic`                  | ≥ 2          | Alle Datenverträge: Config-Schema, `RunState`/`LaneState`, Findings (`extra="forbid"`, Cross-Validatoren)                                                                                                                                                                                                                                                                                                                 |
| `typer`                     | ≥ 0.12       | CLI (`run/resume/approve/status`, Annotated-Style), Test-Client `CliRunner`                                                                                                                                                                                                                                                                                                                                               |
| `pyyaml`                    | ≥ 6          | Config-Parsing mit eigenem StrictLoader (Duplikat-Keys → Fehler)                                                                                                                                                                                                                                                                                                                                                          |
| `claude-agent-sdk`          | ≥ 0.2.118    | Claude-Agenten headless: `query()` + `ClaudeAgentOptions` (model, cwd, resume, tools/allowed_tools, permission_mode, sandbox, setting_sources). **Das SDK ist ein Wrapper um die Claude-Code-CLI** — es spawnt `claude` als headless Subprocess (verifiziert im SDK-Quellcode, `subprocess_cli.py`); Auth und Abrechnung laufen deshalb über den CLI-Login/Plan. Untergrenze wegen `tools`/`strict_mcp_config`/`sandbox`. |
| `anyio`                     | ≥ 4          | Sync-Wrapper um den async SDK-Stream (`anyio.run`)                                                                                                                                                                                                                                                                                                                                                                        |
| `pytest` / `pytest-asyncio` | ≥ 8 / ≥ 0.24 | Testsuite (331 Tests)                                                                                                                                                                                                                                                                                                                                                                                                     |
| `ruff`                      | ≥ 0.6        | Lint (E,F,W,I,UP,B) + Formatter, line-length 100, target py312                                                                                                                                                                                                                                                                                                                                                            |

### Externe Werkzeuge (zur Laufzeit via Subprocess)

| Tool                                 | Rolle                                                        | Absicherung                                                                                  |
|--------------------------------------|--------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| **git**                              | Worktrees, Branches, Merges, Tree-Hashes, Push               | immer `core.hooksPath=/dev/null` + Env-Whitelist + Timeouts                                  |
| **codex** (CLI, geprüft mit 0.144.0) | Unabhängige Reviews (spec/plan/code) + zweiter Entwurfs-Autor für Spec/Plan | read-only-Sandbox, `mcp_servers={}`, isoliertes `CODEX_HOME`, 900-s-Timeout, strikter Parser (Findings-JSON bzw. Marker-Blöcke mit Nonce je Aufruf) |
| **glab** (geprüft mit 1.53.0)        | GitLab: Issue-Intake, Pipeline-/Job-Status, Job-Logs         | injizierbar (`run_glab`-Seam), 120-s-Timeout, SHA-gebundene Pipeline-Abfrage                 |
| **gh** (geprüft mit 2.95.0)          | GitHub: Issue-Intake, Actions-Runs/Jobs, `--log-failed`-Logs | injizierbar (`run_gh`-Seam), 120-s-Timeout, head_sha-gebundene Run-Abfrage                   |
| **uv** (0.10.x)                      | Projekt-/Dependency-Management, Entry-Point `adw`            | —                                                                                            |

## 10. Test-Strategie

- **331 Tests, TDD-first entwickelt** (jeder Task/Bugfix begann mit einem roten Test). Kein Test braucht Netz oder Tokens: SDK, Codex und glab sind an ihren Interfaces gemockt — **git ist echt** (Wegwerf-Repos in `tmp_path`).
- **Skriptbare Mocks als Test-Rückgrat:** `MockAgentRunner` (Antwort-Queues je Agent, simulierte Datei-Outputs statisch oder als `Callable(cwd)` für per-Lane-Verhalten, vollständige Aufruf-Aufzeichnung inkl. `resume`/`deny_read_paths`) und `MockCodexRunner`.
- **Schwerpunkt Crash-Fenster:** Ein großer Teil der Phasen-Tests simuliert gezielt Abbrüche zwischen zwei Checkpoints (State manipulieren, Prozess „sterben lassen", Resume) sowie Agent-Manipulation (Fremd-Commits, Symlink-Artefakte, umgeschriebene Configs, manipulierte Worktrees nach dem completed-Checkpoint).
- **Akzeptanzebene:** `tests/test_e2e_dry_run.py` bildet die DoD-Kriterien aus `docs/SPEC.de.md` §8 ab — komplette CLI-Dry-Runs (single + parallel), Gate-Fail→Same-Session-Fix, Approval-Gate, Triage-Pfade, Crash-Resume, Dual-Authoring und beide RED-Gate-Pfade (mit und ohne `tdd`-Gate).
- **Review-Gate im Entwicklungsprozess:** pro Task `uv run pytest` + `ruff check/format` + `codex review --uncommitted`; über die Tasks 10–13 wurden dabei 8 P1- und 19 P2-Findings gefunden und jeweils mit Regressionstest zuerst gefixt.

## 11. Known Limitations (dokumentiert, bewusst akzeptiert)

| Limitation                                                                                                                        | Einordnung                                                                                                                                   |
|-----------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| Die Git-Konfiguration des **Ziel-Repos** (clean-Filter, Signing-Programme) gilt als vertrauenswürdig.                             | Sie wird vom Nutzer kontrolliert und liegt außerhalb der Agent-Schreibpfade; konfigurierte Filter laufen wie bei jedem manuellen git-Aufruf. |
| Die Codex-read-only-Sandbox verhindert Mutationen, aber keine Reads außerhalb des cwd.                                            | Gleiches Risiko wie bei jedem manuellen `codex review`; mitigiert durch Secret-freies Env und deaktivierte MCP-Server.                       |
| In der Draft-Stage liest der Codex-Autor **denselben Checkout** wie der parallele Claude-Autor und könnte theoretisch ein halb geschriebenes Artefakt sehen. | Bewusst: Eine Worktree-Isolation nähme dem Codex-Entwurf die nur im Checkout liegenden Phasen-Inputs (`.adw/spec.md`). Der Claude-Entwurf wird danach validiert (nicht leer, verändert) und die Artefakte werden vor der Synthese zurückgesetzt. |
| Codex-Token-Rücksync hält den Lock nicht über die gesamte Review-Dauer.                                                           | Bewusst — sonst würden parallele Reviews minutenlang serialisiert. Im Extremfall (zwei gleichzeitige Rotationen) einmalig `codex login`.     |
| Ein Crash zwischen CI-Re-Entry-Checkpoint und erneutem Poll kann dazu führen, dass der Resume ohne zweiten Fix-Versuch eskaliert. | Bounded und safe-seitig: lieber ein Re-Entry zu wenig als ein unbudgetierter Loop.                                                           |
| Der Circuit-Breaker vergleicht exakte Fehlertexte — variierende Ausgaben (Timestamps, Zähler) umgehen ihn.                        | Dann greifen die harten Runden-Limits.                                                                                                       |
| Der RED-Beweis hängt an den Test-**Pfaden**, nicht an ihrem Inhalt — ein Implementierungs-Lauf könnte einen Test an Ort und Stelle entschärfen; die Pfade werden auch nicht als Tests klassifiziert, ein Test-Lauf, der zusätzlich Produktivcode schreibt, fällt also nicht auf. | Bewusst: Inhalts-Immutabilität würde den legitimen Reparaturpfad des Gate-Loops (der Agent fixt seinen eigenen kaputten Test) zur Eskalation machen; entschärfte Tests fängt der nachgelagerte Codex-Code-Review ab. Ebenso fehlt ein Grün-Baseline-Lauf vor dem Test-Lauf — ein schon auf HEAD rotes Gate (fehlendes Tool, Timeout) zählt als RED, ist aber ein Projektfehler, den der Loop unmittelbar danach sichtbar macht. Ein Crash **vor** dem Session-Checkpoint des Test-Laufs wiederholt diesen Agent-Lauf einmal (die Worktree-Änderungen bleiben erhalten, der zweite Lauf sieht sie). |

## 12. Glossar

| Begriff                               | Bedeutung                                                                                                                                                           |
|---------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **RunContext**                        | Dataclass mit allen Abhängigkeiten einer Phase (Repo, Config, State, Runner, glab-/sleep-Seams, Lock) — die Dependency-Injection-Wurzel.                            |
| **AgentRunner / CodexClient**         | Die zwei Protokolle (PEP-544), hinter denen alle Agent-Aufrufe liegen; `CodexClient` deckt beide Codex-Rollen ab (`review` + `author`). Produktions- und Mock-Implementierung sind austauschbar. |
| **Draft-Stage (`_draft_stage`)**      | Phase 1–2: Claude-Autor und Codex schreiben ihre Entwürfe parallel nach `.adw/runs/<id>/drafts/`; idempotent über die Entwurfsdateien, ein Codex-Ausfall degradiert. |
| **Synthese (`spec_/plan_synthesis`)** | Der Agent, der beide Entwürfe zu EINEM Best-of-Artefakt merged und die Zusammenfassung fürs Freigabe-Gate schreibt; er ist der erste Lauf des Authoring-Loops.       |
| **Gate-Beweis (`gates_tree`)**        | Baum-Hash des kompletten Worktree-Inhalts (inkl. untracked) über einen temporären Git-Index — bindet „Gates grün" an exakt den geprüften Stand.                     |
| **RED-Stufe (`_confirm_red`)**        | Initial-Build einer Lane mit `tdd: true`-Gate: reiner Test-Lauf, danach beweist der Orchestrator genau die markierten Gates rot — `red_confirmed` + `red_test_paths` als persistierter Beweis. |
| **Circuit-Breaker**                   | `check_progress`: identische Failure-/Finding-Menge wie in der Vorrunde → sofortige Eskalation.                                                                     |
| **Dispatch (`_dispatch_lane_fixes`)** | Zentrale Fix-Routing-Funktion: Findings je Lane gruppieren, Lane-State atomar stagen (optional mit Budget-Inkrement via `mutate_staged`), dann regulärer Lane-Loop. |
| **Session-Resume**                    | SDK-Feature: Folge-Task an eine bestehende Agent-Session (`resume=session_id`) — voller Kontext ohne Neuaufbau.                                                     |
| **Worktree**                          | Zweiter Checkout desselben Git-Repos (`git worktree add`); Grundlage der Lane-Isolation ohne Repo-Klone.                                                            |
| **Eskalation**                        | Kontrolliertes Run-Ende: `escalation.md` + `phase=escalated` + Exit ≠ 0.                                                                                            |

[↑ nach oben](#tldr)

</div>

ADW Technische Spezifikation · generiert am 2026-07-15 · Quellen: Repo `agentic-developer-workflow` (Code unter `adw/`, `docs/SPEC.de.md`, `docs/PLAN.de.md`, `pyproject.toml`, Commit-Historie bis main `9b89dd6`) · Bedienung: `docs/handbuch/ADW-USER-HANDBUCH.de.html`
