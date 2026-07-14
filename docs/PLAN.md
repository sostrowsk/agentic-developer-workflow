# ADW-Orchestrator — Implementierungsplan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Den in `docs/SPEC.md` spezifizierten 7-Phasen-ADW-Orchestrator als uv-Python-Projekt bauen — TDD, ein Task = ein Zyklus = ein Commit.

**Architecture:** Package `adw/` mit deterministischem Kern (config, state, findings, gates, worktrees, triage, ci) und zwei gekapselten Agent-Schnittstellen (`AgentRunner` via Claude Agent SDK, `CodexRunner` via `codex exec`). `phases.py` orchestriert die 7 Phasen, `cli.py` (typer) ist der Eingang. `--dry-run` injiziert Mocks — der komplette Kontrollfluss ist ohne Tokens testbar.

**Tech Stack:** Python ≥ 3.12, uv, pydantic v2, typer, PyYAML, claude-agent-sdk, pytest (+ pytest-asyncio), ruff.

**Annahmen über die Umgebung** (geprüft am 2026-07-14):
- `codex` 0.144.0, `glab` 1.53.0, `claude`-CLI vorhanden; `ANTHROPIC_API_KEY` bzw. Claude-Login aktiv.
- `claude_agent_sdk`-API: `query(prompt, options=ClaudeAgentOptions(...))`, Optionen u. a. `model`, `cwd`, `resume`, `allowed_tools`, `system_prompt` (Preset `claude_code` + `append`), `permission_mode`, `max_turns`. Session-ID kommt aus den gestreamten Messages (init/result). **Beim ersten SDK-Task gegen die installierte Version verifizieren.**
- Tests brauchen weder Netz noch Tokens: SDK/Codex/glab werden in Tests nie echt aufgerufen (Mocks/Fixtures); git ist echt (tmp_path-Repos).

**Konventionen für jeden Task:** Test zuerst → RED bestätigen → minimal implementieren → GREEN bestätigen → `uv run ruff check . && uv run ruff format .` → Commit (`feat:`/`test:`-Prefix, Co-Authored-By Claude). Testdaten über Fixtures in `tests/conftest.py`, keine Copy-Paste-Repos in Testfunktionen.

---

### Task 0: Projektgerüst

**Files:** Create: `pyproject.toml`, `adw/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

**Steps:**
1. `uv init --package --python 3.12` anpassen: Projektname `adw`, Dependencies `pydantic>=2`, `typer`, `pyyaml`, `claude-agent-sdk`; Dev-Dependencies `pytest`, `pytest-asyncio`, `ruff`. Entry-Point `adw = "adw.cli:app"`.
2. `tests/conftest.py` mit Basis-Fixture `target_repo(tmp_path)`: legt ein echtes Mini-Git-Repo an (git init, ein Commit auf `staging`, gültige `.adw/config.yaml` mit einer backend-Lane, deren Gates schnelle Shell-Kommandos sind: `true` als Pass-Gate, `false` als Fail-Gate konfigurierbar).
3. Smoke-Test `tests/test_smoke.py::test_package_importable` → `uv run pytest` grün, `uv run ruff check .` grün.
4. Commit: `chore: uv-Projektgerüst mit pytest/ruff und target_repo-Fixture`

### Task 1: Findings-Schema (`adw/findings.py`)

**Files:** Create: `adw/findings.py`, `tests/test_findings.py`

Pydantic-Models exakt nach SPEC §5: `Finding` (`severity: Literal["P1","P2","P3"]`, `lane: Literal["frontend","backend","unknown"]`, `file`, `issue`, `remediation_plan: list[str]`, `category: Literal["scope_gap","implementation","trivial"] | None`) und `ReviewResult` (`verdict: Literal["ok","needs_fixes"]`, `findings: list[Finding]`). Dazu `extract_review_result(text: str) -> ReviewResult`: extrahiert den letzten ```json-Block bzw. rohes JSON aus Freitext (tolerant), validiert strikt.

**Tests (je ein Verhalten):** valid parse; legacy-freier Fehlerfall (kaputtes JSON → `FindingsParseError` mit Roh-Text im Message); JSON eingebettet in Prosa/Codefence wird gefunden; `verdict=ok` mit leeren findings; unbekannte `severity` → ValidationError.
RED → implementieren → GREEN → Commit `feat: Findings-Schema mit toleranter JSON-Extraktion`.

### Task 2: Ziel-Repo-Config (`adw/config.py`)

**Files:** Create: `adw/config.py`, `tests/test_config.py`

`AdwConfig.load(repo: Path) -> AdwConfig` liest `.adw/config.yaml` (Schema aus SPEC §5: `base_branch`, `lanes.{name}.gates[]` mit `name/cmd/timeout`, optional `e2e`, `ci` mit Defaults `poll_interval=60`, `timeout=2700`, `staging_job`). Fail fast: fehlende Datei, unbekannte Top-Level-Keys, Lane ohne Gates, Gate ohne timeout → `ConfigError` mit Pfad+Grund.

**Tests:** gültige Config lädt (Fixture-Repo); fehlende Datei → ConfigError „.adw/config.yaml fehlt"; Gate ohne `timeout` → ConfigError; `ci`-Defaults greifen; zweite Lane optional (`is_parallel_capable`).
Commit `feat: Config-Loader mit fail-fast-Validierung`.

### Task 3: RunState + Persistenz (`adw/state.py`)

**Files:** Create: `adw/state.py`, `tests/test_state.py`

`RunState` (pydantic): `run_id` (8-stellig, `secrets.token_hex(4)`), `issue: str`, `phase: Literal["spec","plan","awaiting_approval","build","integration","codex_review","final_review","ci","done","escalated"]`, `parallel: bool`, `lanes: dict[str, LaneState]` (`worktree`, `branch`, `session_id`, `ports: dict[str,int]`, `gate_iterations: int`, `fix_cycles: int`), `approval_granted: bool`. Methoden: `save(repo)` → `.adw/runs/<run_id>/state.json` (atomar: tmp+rename), `RunState.load(repo, run_id)`, `RunState.find_latest(repo)`.

**Tests:** Round-Trip save→load ist identisch (Akzeptanzkriterium 5-Grundlage); load unbekannte run_id → `StateNotFoundError`; save legt Verzeichnis an; find_latest wählt jüngsten Run.
Commit `feat: RunState mit atomarer Persistenz und Resume-Round-Trip`.

### Task 4: Gate-Runner (`adw/gates.py`)

**Files:** Create: `adw/gates.py`, `tests/test_gates.py`

`run_gates(gates: list[Gate], cwd: Path, env: dict) -> GateReport`; `GateReport.passed: bool`, `failures: list[GateFailure]` (`gate`, `exit_code`, `output` — stdout+stderr, auf letzte 200 Zeilen gekappt). Jeder `subprocess.run` mit **echtem `timeout`** (aus Gate-Config); `TimeoutExpired` → GateFailure mit `exit_code=None, timed_out=True`. Läuft alle Gates der Reihe nach, stoppt beim ersten Fail (fail fast). Env kommt von `adw/env.py::safe_env()` (Whitelist: PATH, HOME, LANG, LC_*, VIRTUAL_ENV, Node/Python-Basics — Teil dieses Tasks).

**Tests:** alle Gates pass → `passed=True`; erstes Fail stoppt Folge-Gates (Marker-File-Trick im Fixture); Timeout → `timed_out=True`; Output-Kappung; `safe_env` enthält kein `ANTHROPIC_API_KEY`.
Commit `feat: Gate-Runner mit Timeouts, fail-fast und Env-Whitelist`.

### Task 5: Worktrees + Ports (`adw/worktrees.py`)

**Files:** Create: `adw/worktrees.py`, `tests/test_worktrees.py`

`create_lane_worktree(repo, run_id, lane, base_branch) -> Path`: `git worktree add -b adw/<run_id>/<lane> .adw/runs/<run_id>/trees/<lane> <base_branch>` (idempotent: existiert der Worktree laut `git worktree list`, wird er zurückgegeben). `ports_for(run_id, lane) -> dict`: deterministisch `9100 + (int(run_id,16) + hash(lane)) % 50` für backend / `9200 + …` für frontend, mit Socket-Bind-Check und linearem Ausweichen. `remove_lane_worktree` für Cleanup.

**Tests (echtes git im tmp_path):** Worktree entsteht auf richtigem Branch ab base_branch; zweiter Aufruf idempotent; Ports deterministisch (gleiche Eingabe → gleicher Port) und verschieden je Lane; belegter Port (Socket im Test gebunden) → Ausweich-Port.
Commit `feat: Lane-Worktrees und deterministische Port-Zuteilung`.

### Task 6: Triage, Limits, Circuit-Breaker (`adw/triage.py`)

**Files:** Create: `adw/triage.py`, `tests/test_triage.py`

Reine Funktionen (kein I/O): `triage_final_review(result: ReviewResult) -> TriageDecision` (`scope_gap`-Findings → `followups`, Rest gruppiert nach Lane → `fix_tasks`; `lane=unknown` → alle Lanes bzw. Single-Lane). `LimitGuard`-Klasse: `check_gate_iterations(lane_state)` (max 10), `check_fix_cycles(lane_state)` (max 3), `check_progress(prev_failures, curr_failures)` — Circuit-Breaker: identische Failure-Menge wie Vorrunde → `NoProgressError`. Limits als Konstanten `MAX_GATE_ITERATIONS=10`, `MAX_FIX_CYCLES=3`.

**Tests:** scope_gap → followup, nicht fix_task; Lane-Routing; unknown-Lane-Fallback; 10. Iteration ok / 11. → `LimitExceededError`; Null-Fortschritt → `NoProgressError`, echter Fortschritt (weniger Failures) nicht.
Commit `feat: Triage-Regeln, Iterations-Limits und Circuit-Breaker`.

### Task 7: AgentRunner + Registry (`adw/agents.py`, `adw/mock.py`)

**Files:** Create: `adw/agents.py`, `adw/mock.py`, `tests/test_agents.py`

`AgentSpec` (name, model, allowed_tools, system_append, permission_mode) + `REGISTRY` exakt nach SPEC §3 (Modell-IDs: `claude-fable-5`, `claude-opus-4-8`, `claude-haiku-4-5-20251001`). Protokoll `AgentRunner` mit einer Methode `run(agent: AgentSpec, task: str, cwd: Path, resume: str | None) -> AgentResult` (`text`, `session_id`). `SdkAgentRunner` implementiert das via `claude_agent_sdk.query` + `ClaudeAgentOptions` (system_prompt-Preset `claude_code` mit `append`; Session-ID aus Init-/Result-Message; sync-Wrapper via `anyio.run`). `MockAgentRunner` (in `mock.py`): liefert skriptbare Antworten aus einer Queue je Agent-Name und zeichnet alle Aufrufe auf (`calls`-Liste) — Basis aller Phasen-Tests.

**Tests (SDK gemockt via monkeypatch auf `adw.agents.query`):** SdkAgentRunner reicht model/cwd/resume/allowed_tools korrekt in Options durch (Aufzeichnung der Options); Session-ID wird aus dem Message-Strom extrahiert; Registry enthält 6 Claude-Agents mit read-only-Tools für Reviewer (kein Write/Edit/Bash im final-reviewer); MockAgentRunner gibt gescriptete Antworten in Reihenfolge.
Commit `feat: Agent-Registry, SDK-Runner und skriptbarer Mock-Runner`.

### Task 8: CodexRunner (`adw/codex.py`)

**Files:** Create: `adw/codex.py`, Modify: `adw/mock.py`, `tests/test_codex.py`

`CodexRunner.review(kind: Literal["spec","plan","code"], content_paths, cwd) -> ReviewResult`: baut Prompt (Schema-Instruktion aus SPEC §5 eingebettet), ruft `codex exec --sandbox read-only -C <cwd> …` per subprocess (timeout 900 s), parst stdout via `extract_review_result`. `MockCodexRunner`: Queue von ReviewResults.

**Tests (subprocess gemockt):** Kommandozeile enthält `--sandbox read-only` und cwd; Schema-Instruktion im Prompt; kaputter Codex-Output → `FindingsParseError` (kein Silent-ok!); Timeout → klarer Fehler.
Commit `feat: Codex-Reviewer als read-only-Subprocess mit striktem Findings-Parsing`.

### Task 9: CI-Monitoring (`adw/ci.py`)

**Files:** Create: `adw/ci.py`, `tests/test_ci.py`

`poll_pipeline(repo, branch, cfg: CiConfig, sleep=time.sleep, runner=subprocess.run) -> CiResult`: pollt `glab ci list --output json` (Branch-Filter) im `poll_interval`, bis Pipeline final; prüft `staging_job`-Status via `glab ci view/api`. Rückgabe `passed | failed(log_excerpt) | TimeoutError nach cfg.timeout`. `fetch_failed_job_logs(...)` für den Log-Analyst. Injectable `sleep`/`runner` → Tests ohne Warten und ohne glab.

**Tests:** success-Pfad nach 2 Poll-Runden; failed-Pipeline liefert Logs; Timeout nach `cfg.timeout` (fake sleep zählt hoch); glab-JSON-Parsing.
Commit `feat: GitLab-CI-Polling mit injizierbarer Zeit und Log-Abruf`.

### Task 10: Phasen-Orchestrierung (`adw/phases.py`)

**Files:** Create: `adw/phases.py`, `tests/test_phases.py` — der größte Task, in Sub-Zyklen:

- **10a Spec-Phase:** `run_spec_phase(ctx)` — Spec-Agent schreibt `.adw/spec.md`, Codex-Loop bis `ok` (Findings → gleiche Session via resume). Tests: Loop terminiert bei ok; Findings gehen als Folge-Task an dieselbe Session (Mock-`calls` prüfen resume-Argument).
- **10b Plan-Phase + Approval:** analog für `.adw/plan.md`/`contract.yaml`; danach `phase="awaiting_approval"`, State gespeichert, `AwaitingApproval`-Exception (CLI fängt sie und beendet mit Hinweis). `--no-approval` überspringt. Test: State-File hat phase=awaiting_approval; mit `approval_granted=True` läuft es weiter.
- **10c Build-Lane-Loop:** `run_lane(ctx, lane)` — Worktree, Build-Task an Opus-Session, Gates, bei Fail Fehlerausgabe als Folge-Task (resume), LimitGuard + Circuit-Breaker. Tests: Fail→Fix→Pass-Sequenz (Mock-Gates via Fixture-Kommandos, skriptbarer MockAgent); 10er-Limit → Eskalation; Null-Fortschritt → Eskalation. Commit der Lane-Ergebnisse im Worktree (git add/commit per Code).
- **10d Integration + E2E (nur parallel):** Merge der Lane-Branches auf `adw/<run_id>/integration`, E2E-Gate, bei Rot E2E-Triage-Agent (Findings → Lane-Fix). Tests: Merge-Konflikt → Eskalation; E2E-Rot → Triage-Aufruf mit Playwright-Output → Fix-Task in richtiger Lane.
- **10e Codex-Review + finaler Review + Triage:** Codex-Loop (Findings → Lane), dann finaler Reviewer (read-only Registry-Spec), `triage_final_review`, Fix-Zyklen (max 3), Follow-up-Report `.adw/runs/<run_id>/followups.md`. Tests: scope_gap → Report statt Loop; 3 Zyklen → Eskalation.
- **10f Push + CI + Eskalations-Report:** Push (subprocess), `poll_pipeline`; bei failed → Log-Analyst → Findings → zurück in Lane (ein Re-Entry, dann Eskalation). `write_escalation(ctx, reason)` erzeugt `escalation.md`. Tests: CI-grün → phase=done; CI-rot → Log-Analyst-Aufruf; Eskalations-Report enthält erreichten Stand.

Je Sub-Zyklus ein Commit (`feat: Phase X …`). Alle Tests laufen mit `MockAgentRunner`/`MockCodexRunner`/Fake-CI — kein Netz, keine Tokens.

### Task 11: CLI (`adw/cli.py`)

**Files:** Create: `adw/cli.py`, `tests/test_cli.py`

typer-App nach SPEC §5: `run` (--repo, --issue XOR --gitlab-issue, --parallel, --dry-run, --no-approval, --base-branch), `resume <run_id>`, `approve <run_id>`, `status`. `--gitlab-issue` holt Titel+Beschreibung via `glab issue view <id> --output json` (subprocess, in Tests gemockt). `--dry-run` verdrahtet Mock-Runner mit kanonischen Fixture-Antworten (happy path + 1 simulierter Gate-Fail). Exit-Codes: 0 done, 2 awaiting_approval, 1 Eskalation/Fehler.

**Tests (CliRunner):** --issue und --gitlab-issue schließen sich aus; dry-run happy path endet mit Exit 0 und phase=done im State; approve setzt awaiting_approval fort; status listet Runs.
Commit `feat: adw-CLI mit run/resume/approve/status und Dry-Run-Modus`.

### Task 12: End-to-End-Dry-Run (Akzeptanztests)

**Files:** Create: `tests/test_e2e_dry_run.py`

Die DoD-Kriterien 1–5 aus SPEC §8 als Integrationstests gegen das `target_repo`-Fixture: kompletter Single-Lane-Dry-Run; `--parallel`-Dry-Run inkl. E2E-Triage-Pfad; Gate-Fail→Fix→Eskalationskette; Approval-Pause+Resume; Crash-Resume (State manipulieren, resume, gleiche Phase). Fehlt Verhalten → zurück in den jeweiligen Task (kein Fix im E2E-Test verstecken).
Commit `test: End-to-End-Dry-Run-Akzeptanztests`.

### Task 13: README + Beispiel-Config

**Files:** Create: `README.md`, `examples/config.yaml`, Modify: `docs/SPEC.md` (nur falls Abweichungen entstanden)

Quickstart (uv sync, Beispiel-Aufrufe), Config-Referenz, Architektur-Kurzüberblick mit Verweis auf SPEC/Handout, Troubleshooting (Eskalations-Reports lesen). Docs-Commit.

---

## Verifikation (nach Task 13)

1. `uv run pytest` — alles grün, `uv run ruff check .` clean.
2. `codex review --uncommitted` bzw. auf letztem Stand `codex review` — P1 = 0.
3. Manueller Dry-Run gegen ein frisches Wegwerf-Repo (nicht das Fixture):
   `uv run adw run --repo /tmp/spielwiese --issue "Demo" --dry-run` → Exit 0, danach `--parallel --dry-run`.
4. **Erst nach Abnahme durch Stefan:** erster echter Token-Lauf mit kleinem Issue gegen ein Test-Repo (SPEC §8.7).
