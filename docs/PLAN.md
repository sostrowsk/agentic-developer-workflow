# ADW Orchestrator — Implementation Plan

**English** | [Deutsch](PLAN.de.md)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the 7-phase ADW orchestrator specified in `docs/SPEC.md` as a uv Python project — TDD, one task = one cycle = one commit.

**Architecture:** Package `adw/` with a deterministic core (config, state, findings, gates, worktrees, triage, ci) and two encapsulated agent interfaces (`AgentRunner` via Claude Agent SDK, `CodexRunner` via `codex exec`). `phases.py` orchestrates the 7 phases, `cli.py` (typer) is the entry point. `--dry-run` injects mocks — the complete control flow is testable without tokens.

**Tech Stack:** Python ≥ 3.12, uv, pydantic v2, typer, PyYAML, claude-agent-sdk, pytest (+ pytest-asyncio), ruff.

**Assumptions about the environment** (checked on 2026-07-14):
- `codex` 0.144.0, `glab` 1.53.0, `claude` CLI present; `ANTHROPIC_API_KEY` or Claude login active.
- `claude_agent_sdk` API: `query(prompt, options=ClaudeAgentOptions(...))`, options include `model`, `cwd`, `resume`, `allowed_tools`, `system_prompt` (preset `claude_code` + `append`), `permission_mode`, `max_turns`. The session ID comes from the streamed messages (init/result). **Verify against the installed version in the first SDK task.**
- Tests need neither network nor tokens: SDK/Codex/glab are never called for real in tests (mocks/fixtures); git is real (tmp_path repos).

**Conventions for every task:** Test first → confirm RED → implement minimally → confirm GREEN → `uv run ruff check . && uv run ruff format .` → commit (`feat:`/`test:` prefix, Co-Authored-By Claude). Test data via fixtures in `tests/conftest.py`, no copy-paste repos in test functions.

---

### Task 0: Project scaffold

**Files:** Create: `pyproject.toml`, `adw/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

**Steps:**
1. Adapt `uv init --package --python 3.12`: project name `adw`, dependencies `pydantic>=2`, `typer`, `pyyaml`, `claude-agent-sdk`; dev dependencies `pytest`, `pytest-asyncio`, `ruff`. Entry point `adw = "adw.cli:app"`.
2. `tests/conftest.py` with base fixture `target_repo(tmp_path)`: creates a real mini git repo (git init, one commit on `staging`, valid `.adw/config.yaml` with one backend lane whose Gates are fast shell commands: `true` as pass gate, `false` configurable as fail gate).
3. Smoke test `tests/test_smoke.py::test_package_importable` → `uv run pytest` green, `uv run ruff check .` green.
4. Commit: `chore: uv-Projektgerüst mit pytest/ruff und target_repo-Fixture`

### Task 1: Findings schema (`adw/findings.py`)

**Files:** Create: `adw/findings.py`, `tests/test_findings.py`

Pydantic models exactly per SPEC §5: `Finding` (`severity: Literal["P1","P2","P3"]`, `lane: Literal["frontend","backend","unknown"]`, `file`, `issue`, `remediation_plan: list[str]`, `category: Literal["scope_gap","implementation","trivial"] | None`) and `ReviewResult` (`verdict: Literal["ok","needs_fixes"]`, `findings: list[Finding]`). Plus `extract_review_result(text: str) -> ReviewResult` following the **strict parser contract** (SPEC §5): whole text = JSON object OR content of the last ```json fence; everything else → `FindingsParseError` (retry/escalation case). No prose tolerance heuristics — cannot be sealed, stale-ok risk.

**Tests (one behavior each):** valid parse (pure + last fence); broken/truncated JSON and unclosed fences → `FindingsParseError` with raw text; prose around bare JSON → error; `verdict=ok` with empty findings; schema violations (severity, extra keys, verdict consistency) → ValidationError; adversarial inputs (duplicate keys, nesting depth, giant integers) fail closed in linear time.
RED → implement → GREEN → commit `feat: Findings-Schema mit striktem Parser-Kontrakt`.

### Task 2: Target-repo config (`adw/config.py`)

**Files:** Create: `adw/config.py`, `tests/test_config.py`

`AdwConfig.load(repo: Path) -> AdwConfig` reads `.adw/config.yaml` (schema from SPEC §5: `base_branch`, `lanes.{name}.gates[]` with `name/cmd/timeout`, optional `e2e`, `ci` with defaults `poll_interval=60`, `timeout=2700`, `staging_job`). Fail fast: missing file, unknown top-level keys, lane without Gates, Gate without timeout → `ConfigError` with path+reason.

**Tests:** valid config loads (fixture repo); missing file → ConfigError ".adw/config.yaml fehlt"; Gate without `timeout` → ConfigError; `ci` defaults apply; second lane optional (`is_parallel_capable`).
Commit `feat: Config-Loader mit fail-fast-Validierung`.

### Task 3: RunState + persistence (`adw/state.py`)

**Files:** Create: `adw/state.py`, `tests/test_state.py`

`RunState` (pydantic): `run_id` (8 characters, `secrets.token_hex(4)`), `issue: str`, `phase: Literal["spec","plan","awaiting_approval","build","integration","codex_review","final_review","ci","done","escalated"]`, `parallel: bool`, `lanes: dict[str, LaneState]` (`worktree`, `branch`, `session_id`, `ports: dict[str,int]`, `gate_iterations: int`, `fix_cycles: int`), `approval_granted: bool`. Methods: `save(repo)` → `.adw/runs/<run_id>/state.json` (atomic: tmp+rename), `RunState.load(repo, run_id)`, `RunState.find_latest(repo)`.

**Tests:** round-trip save→load is identical (basis for acceptance criterion 5); load unknown run_id → `StateNotFoundError`; save creates the directory; find_latest picks the most recent run.
Commit `feat: RunState mit atomarer Persistenz und Resume-Round-Trip`.

### Task 4: Gate runner (`adw/gates.py`)

**Files:** Create: `adw/gates.py`, `tests/test_gates.py`

`run_gates(gates: list[Gate], cwd: Path, env: dict) -> GateReport`; `GateReport.passed: bool`, `failures: list[GateFailure]` (`gate`, `exit_code`, `output` — stdout+stderr, capped to the last 200 lines). Every `subprocess.run` with a **real `timeout`** (from the Gate config); `TimeoutExpired` → GateFailure with `exit_code=None, timed_out=True`. Runs all Gates in order, stops at the first fail (fail fast). Env comes from `adw/env.py::safe_env()` (whitelist: PATH, HOME, LANG, LC_*, VIRTUAL_ENV, Node/Python basics — part of this task).

**Tests:** all Gates pass → `passed=True`; first fail stops subsequent Gates (marker-file trick in the fixture); timeout → `timed_out=True`; output capping; `safe_env` contains no `ANTHROPIC_API_KEY`.
Commit `feat: Gate-Runner mit Timeouts, fail-fast und Env-Whitelist`.

### Task 5: Worktrees + ports (`adw/worktrees.py`)

**Files:** Create: `adw/worktrees.py`, `tests/test_worktrees.py`

`create_lane_worktree(repo, run_id, lane, base_branch) -> Path`: `git worktree add -b adw/<run_id>/<lane> .adw/runs/<run_id>/trees/<lane> <base_branch>` (idempotent: if the Worktree exists according to `git worktree list`, it is returned). `ports_for(run_id, lane) -> dict`: deterministically `9100 + (int(run_id,16) + hash(lane)) % 50` for backend / `9200 + …` for frontend, with socket bind check and linear fallback. `remove_lane_worktree` for cleanup.

**Tests (real git in tmp_path):** Worktree is created on the right branch off base_branch; second call idempotent; ports deterministic (same input → same port) and different per lane; occupied port (socket bound in the test) → fallback port.
Commit `feat: Lane-Worktrees und deterministische Port-Zuteilung`.

### Task 6: Triage, limits, circuit breaker (`adw/triage.py`)

**Files:** Create: `adw/triage.py`, `tests/test_triage.py`

Pure functions (no I/O): `triage_final_review(result: ReviewResult) -> TriageDecision` (`scope_gap` Findings → `followups`, rest grouped by lane → `fix_tasks`; `lane=unknown` → all lanes resp. single lane). `LimitGuard` class: `check_gate_iterations(lane_state)` (max 10), `check_fix_cycles(lane_state)` (max 3), `check_progress(prev_failures, curr_failures)` — circuit breaker: identical failure set as in the previous round → `NoProgressError`. Limits as constants `MAX_GATE_ITERATIONS=10`, `MAX_FIX_CYCLES=3`.

**Tests:** scope_gap → followup, not fix_task; lane routing; unknown-lane fallback; 10th iteration ok / 11th → `LimitExceededError`; zero progress → `NoProgressError`, real progress (fewer failures) does not.
Commit `feat: Triage-Regeln, Iterations-Limits und Circuit-Breaker`.

### Task 7: AgentRunner + registry (`adw/agents.py`, `adw/mock.py`)

**Files:** Create: `adw/agents.py`, `adw/mock.py`, `tests/test_agents.py`

`AgentSpec` (name, model, allowed_tools, system_append, permission_mode) + `REGISTRY` exactly per SPEC §3 (model IDs: `claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-5`). Protocol `AgentRunner` with one method `run(agent: AgentSpec, task: str, cwd: Path, resume: str | None) -> AgentResult` (`text`, `session_id`). `SdkAgentRunner` implements this via `claude_agent_sdk.query` + `ClaudeAgentOptions` (system_prompt preset `claude_code` with `append`; session ID from the init/result message; sync wrapper via `anyio.run`). `MockAgentRunner` (in `mock.py`): delivers scriptable answers from a queue per agent name and records all calls (`calls` list) — basis of all phase tests.

**Tests (SDK mocked via monkeypatch on `adw.agents.query`):** SdkAgentRunner passes model/cwd/resume/allowed_tools correctly through into the options (recording of the options); session ID is extracted from the message stream; registry contains 6 Claude agents with read-only tools for reviewers (no Write/Edit/Bash in the final-reviewer); MockAgentRunner returns scripted answers in order.
Commit `feat: Agent-Registry, SDK-Runner und skriptbarer Mock-Runner`.

### Task 8: CodexRunner (`adw/codex.py`)

**Files:** Create: `adw/codex.py`, Modify: `adw/mock.py`, `tests/test_codex.py`

`CodexRunner.review(kind: Literal["spec","plan","code"], content_paths, cwd) -> ReviewResult`: builds the prompt (schema instruction from SPEC §5 embedded), calls `codex exec --sandbox read-only -C <cwd> …` via subprocess (timeout 900 s), parses stdout via `extract_review_result`. `MockCodexRunner`: queue of ReviewResults.

**Tests (subprocess mocked):** command line contains `--sandbox read-only` and cwd; schema instruction in the prompt; broken Codex output → `FindingsParseError` (no silent ok!); timeout → clear error.
Commit `feat: Codex-Reviewer als read-only-Subprocess mit striktem Findings-Parsing`.

### Task 9: CI monitoring (`adw/ci.py`)

**Files:** Create: `adw/ci.py`, `tests/test_ci.py`

`poll_pipeline(repo, branch, cfg: CiConfig, sleep=time.sleep, runner=subprocess.run) -> CiResult`: polls `glab ci list --output json` (branch filter) at `poll_interval` until the pipeline is final; checks the `staging_job` status via `glab ci view/api`. Returns `passed | failed(log_excerpt) | TimeoutError after cfg.timeout`. `fetch_failed_job_logs(...)` for the log analyst. Injectable `sleep`/`runner` → tests without waiting and without glab.

**Tests:** success path after 2 poll rounds; failed pipeline delivers logs; timeout after `cfg.timeout` (fake sleep counts up); glab JSON parsing.
Commit `feat: GitLab-CI-Polling mit injizierbarer Zeit und Log-Abruf`.

### Task 10: Phase orchestration (`adw/phases.py`)

**Files:** Create: `adw/phases.py`, `tests/test_phases.py` — the biggest task, in sub-cycles:

- **10a Spec phase:** `run_spec_phase(ctx)` — spec agent writes `.adw/spec.md`, Codex loop until `ok` (Findings → same session via resume). Tests: loop terminates on ok; Findings go as a follow-up task to the same session (check resume argument in mock `calls`).
- **10b Plan phase + approval:** analogous for `.adw/plan.md`/`contract.yaml`; then `phase="awaiting_approval"`, state saved, `AwaitingApproval` exception (CLI catches it and exits with a hint). `--no-approval` skips. Test: state file has phase=awaiting_approval; with `approval_granted=True` it continues.
- **10c Build lane loop:** `run_lane(ctx, lane)` — Worktree, build task to the Opus session, Gates, on fail error output as a follow-up task (resume), LimitGuard + circuit breaker. Tests: fail→fix→pass sequence (mock Gates via fixture commands, scriptable MockAgent); 10 limit → escalation; zero progress → escalation. Commit of the lane results in the Worktree (git add/commit via code).
- **10d Integration + E2E (parallel only):** Merge of the lane branches onto `adw/<run_id>/integration`, E2E gate, on red E2E Triage agent (Findings → lane fix). Tests: merge conflict → escalation; E2E red → Triage call with Playwright output → fix task in the right lane.
- **10e Codex review + final review + Triage:** Codex loop (Findings → lane), then final reviewer (read-only registry spec), `triage_final_review`, fix cycles (max 3), follow-up report `.adw/runs/<run_id>/followups.md`. Tests: scope_gap → report instead of loop; 3 cycles → escalation.
- **10f Push + CI + escalation report:** Push (subprocess), `poll_pipeline`; on failed → log analyst → Findings → back into the lane (one re-entry, then escalation). `write_escalation(ctx, reason)` creates `escalation.md`. Tests: CI green → phase=done; CI red → log analyst call; escalation report contains the state reached.

One commit per sub-cycle (`feat: Phase X …`). All tests run with `MockAgentRunner`/`MockCodexRunner`/fake CI — no network, no tokens.

### Task 11: CLI (`adw/cli.py`)

**Files:** Create: `adw/cli.py`, `tests/test_cli.py`

typer app per SPEC §5: `run` (--repo, --issue XOR --gitlab-issue, --parallel, --dry-run, --no-approval, --base-branch), `resume <run_id>`, `approve <run_id>`, `status`. `--gitlab-issue` fetches title+description via `glab issue view <id> --output json` (subprocess, mocked in tests). `--dry-run` wires up mock runners with canonical fixture answers (happy path + 1 simulated Gate fail). Exit codes: 0 done, 2 awaiting_approval, 1 escalation/error.

**Tests (CliRunner):** --issue and --gitlab-issue are mutually exclusive; dry-run happy path ends with exit 0 and phase=done in the state; approve continues awaiting_approval; status lists runs.
Commit `feat: adw-CLI mit run/resume/approve/status und Dry-Run-Modus`.

### Task 12: End-to-end dry run (acceptance tests)

**Files:** Create: `tests/test_e2e_dry_run.py`

The DoD criteria 1–5 from SPEC §8 as integration tests against the `target_repo` fixture: complete single-lane dry run; `--parallel` dry run incl. the E2E Triage path; Gate-fail→fix→escalation chain; approval pause+resume; crash resume (manipulate state, resume, same phase). If behavior is missing → back to the respective task (do not hide a fix in the E2E test).
Commit `test: End-to-End-Dry-Run-Akzeptanztests`.

### Task 13: README + example config

**Files:** Create: `README.md`, `examples/config.yaml`, Modify: `docs/SPEC.md` (only if deviations arose)

Quickstart (uv sync, example invocations), config reference, short architecture overview with reference to SPEC/handout, troubleshooting (reading escalation reports). Docs commit.

---

## Verification (after Task 13)

1. `uv run pytest` — all green, `uv run ruff check .` clean.
2. `codex review --uncommitted` or on the latest state `codex review` — P1 = 0.
3. Manual dry run against a fresh throwaway repo (not the fixture):
   `uv run adw run --repo /tmp/spielwiese --issue "Demo" --dry-run` → exit 0, then `--parallel --dry-run`.
4. **Only after acceptance by Stefan:** first real token run with a small issue against a test repo (SPEC §8.7).
