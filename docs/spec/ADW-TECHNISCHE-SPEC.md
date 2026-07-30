<div>

<div class="wrap">

<span class="badge">TECHNICAL SPECIFICATION · As of 2026-07-15 · adw 0.1.0 · main 9b89dd6</span>

# ADW — How It Works, Implementation, Design Decisions

How the 7-phase orchestrator is built: architecture, control flow, crash-resume engineering, security model, and tech stack.

</div>

</div>

<div role="main">

<div id="tldr" class="section tldr">

## ⚡ At a Glance

- **Architectural principle:** "Control flow is code, not prompt." Loops, Gates, merges, Dispatch, Triage, limits, CI polling = deterministic Python (0 tokens). Agents deliver judgment exclusively — behind two narrow interfaces (`AgentRunner`, `CodexReviewer`) that can be replaced by mocks.
- **Structure:** Package `adw/` with a deterministic core (config, state, findings, gates, worktrees, triage, ci), two agent adapters (Claude Agent SDK, Codex CLI), phase orchestration (`phases.py`, ~1,300 lines), and a typer CLI.
- **Reliability:** Every transition and every open feedback item is atomically checkpointed in `state.json` (flock + tmp/rename); Gate evidence is bound to tree hashes; all loops have hard limits + Circuit-Breakers.
- **Security:** Tool and path whitelists per agent, sandboxed Bash in the Worktree, env whitelist for all subprocesses, commits only by the orchestrator, immutable artifacts (restore after every agent run).
- **Stack:** Python ≥ 3.12, uv, pydantic v2, typer, PyYAML, claude-agent-sdk (≥ 0.2.118, spawns the **Claude Code CLI** headless — auth/billing via the plan login, no API-token payment), anyio; external: codex CLI, glab (GitLab), gh (GitHub), git. 350 pytest tests, mocks-only (no network, no tokens), real git.

</div>

<div id="kernaussagen" class="section">

## Key Points

<div class="kern">

<div class="card">

**Three actors, clearly separated.** Claude agents (SDK), Codex (CLI subprocess), and deterministic code. Reviewers never fix; every fix takes the validated path through all Gates.

</div>

<div class="card">

**Structured handovers instead of free text.** All reviewers respond in the same Findings JSON schema; the parser is deliberately strict — a parse error is safe, a false "ok" is not.

</div>

<div class="card">

**Distrust as a design assumption.** The orchestrator verifies agent results near-cryptographically (tree hashes, HEAD invariants, branch checks) instead of believing them.

</div>

<div class="card">

**v1 = v2 with one Lane.** One code path, one CLI; `--parallel` enables multi-lane operation, integration, and E2E — no separate scripts.

</div>

</div>

</div>

1.  [Architecture & Module Map](#architektur)
2.  [Control Flow of the Seven Phases](#phasen)
3.  [Agent Registry & Model Economics](#agenten)
4.  [Findings Schema & Strict Parser Contract](#findings)
5.  [State, Checkpoints & Crash Resume](#state)
6.  [Limits & Circuit-Breakers](#limits)
7.  [Security Model](#sicherheit)
8.  [Design Decisions (with Rationale)](#entscheidungen)
9.  [Packages, Frameworks & External Tools](#stack)
10. [Test Strategy](#testing)
11. [Known Limitations](#limitations)
12. [Glossary](#glossar)

## 1. Architecture & Module Map

                              ┌────────────────────────────────────────────┐
      adw run/resume/approve  │  cli.py (typer)                            │
      ────────────────────────▶  Argument validation · Runner wiring       │
                              │  Dry-run fixtures · Exit codes 0/2/1       │
                              └───────────────┬────────────────────────────┘
                                              │ RunContext (repo, config, state,
                                              │ agents, codex, run_glab, sleep, …)
                              ┌───────────────▼────────────────────────────┐
                              │  phases.py — orchestration of the 7 phases │
                              │  Loops · Limits · Dispatch · Triage ·      │
                              │  escalation · state transitions            │
                              └──┬──────────┬──────────┬───────────┬───────┘
                        judgment │          │          │           │ deterministic core
            ┌────────────────────▼──┐  ┌────▼──────┐  ┌▼──────────┐│
            │ agents.py             │  │ codex.py  │  │ gates.py  ││ config.py   findings.py
            │ SdkAgentRunner        │  │ CodexRunner│ │ worktrees │▼ state.py    triage.py
            │ (Claude Agent SDK)    │  │ (codex exec│ │ ci.py     │  mock.py (dry-run/tests)
            └───────────────────────┘  │ read-only) │ └───────────┘
                                       └────────────┘

| Module             | Responsibility                                                                                                                                               | Core API                                                                                                                                        |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| `adw/cli.py`       | typer entry point: `run/resume/approve/status`, issue intake (text or `glab issue view`), dry-run wiring, exit codes                                         | `app`, `_build_context()`, `_execute()`                                                                                                         |
| `adw/phases.py`    | The 7 phases over a `RunContext` — all loops, limits, Dispatch, Triage, escalation                                                                           | `run_spec_and_plan`, `run_build_phase`, `run_integration_phase`, `run_codex_review_phase`, `run_final_review_phase`, `run_ci_phase`, `escalate` |
| `adw/agents.py`    | Agent registry (models, tools, prompts, permissions) + `SdkAgentRunner` via the Claude Agent SDK; mandatory login, env hardening, path deny rules            | `REGISTRY`, `AgentRunner` protocol, `AgentResult(text, session_id)`                                                                             |
| `adw/codex.py`     | Codex CLI as a read-only subprocess with isolated `CODEX_HOME`; builds review prompts (schema embedded), parses strictly                                     | `CodexRunner.review(kind, content_refs, cwd)`, `CodexReviewer` protocol                                                                         |
| `adw/findings.py`  | Findings schema (pydantic, `extra="forbid"`) + strict parser + shared schema instruction for all reviewer prompts                                            | `Finding`, `ReviewResult`, `extract_review_result`, `SCHEMA_INSTRUCTION`                                                                        |
| `adw/config.py`    | Loader for `.adw/config.yaml`, fail fast (StrictLoader rejects duplicates; Lane without Gates, Gate without timeout → error)                                 | `AdwConfig.load(repo)`, `Gate`, `E2eConfig`, `CiConfig`                                                                                         |
| `adw/state.py`     | Persisted run state: atomic snapshots, transactions, monotonic sequence                                                                                      | `RunState`, `LaneState`, `save/load/update/find_latest`                                                                                         |
| `adw/gates.py`     | Gate runner: `subprocess` with real timeout, process-group kill, RAM-bounded output tail (200 lines of max. 4 KiB each)                                      | `run_gates(gates, cwd, extra_env) → GateReport`                                                                                                 |
| `adw/worktrees.py` | Lane worktrees (idempotent, ready marker, recovery from partial adds) + deterministic ports with bind check                                                  | `create_lane_worktree`, `remove_lane_worktree`, `ports_for`, `lane_branch`                                                                      |
| `adw/triage.py`    | Pure functions: Finding routing, iteration limits, Circuit-Breaker                                                                                           | `triage_final_review`, `check_gate_iterations/fix_cycles/progress`                                                                              |
| `adw/ci.py`        | GitLab polling via glab (SHA-bound), job log retrieval, injectable time                                                                                      | `poll_pipeline(…, sha=…)`, `fetch_failed_job_logs`                                                                                              |
| `adw/github.py`    | GitHub Actions polling via gh — same result (`CiResult`) and same error classes as ci.py; "green" = all workflow runs of the push SHA completed and none red | `poll_ci(…, run_gh, sha=…)`                                                                                                                     |
| `adw/forge.py`     | Hosting detection GitLab/GitHub: `ci.provider` override wins, otherwise hostname of the origin URL; unknown host → fail fast                                 | `detect_forge(repo, override)`                                                                                                                  |
| `adw/env.py`       | Env whitelist for all subprocesses (no secret leakage)                                                                                                       | `safe_env(extra)`                                                                                                                               |
| `adw/mock.py`      | Scriptable mock runners: response queues per agent, simulated file outputs (static or as `Callable(cwd)`), call recording                                    | `MockAgentRunner`, `MockCodexRunner`                                                                                                            |

The connecting element is the **`RunContext`** (dataclass): repo path, loaded config, `RunState`, the two runner interfaces, plus injectable seams for glab (`run_glab`) and time (`sleep`). A `threading.RLock` serializes state mutations across parallel lane threads. Because **all** contact with the outside world (agents, Codex, glab, sleep, push) runs through this context, the same production code path can be operated entirely with mocks — that is the technical foundation of `--dry-run` and the entire test suite.

## 2. Control Flow of the Seven Phases

Each phase function checks by itself whether it is "up" (`state.phase` guard) — the CLI always calls the complete chain; a resume therefore automatically starts in the correct phase.

Phases 1–2 — `run_spec_and_plan`: authoring loops + approval gate

<div class="inner">

The shared building block is the **reviewed authoring loop**: agent writes artifact(s) → Codex reviews (`kind=spec` or `plan`; the plan review receives the spec as a reference) → Findings go as a follow-up task to **the same SDK session** (`resume=session_id`) → until verdict `ok`, at most 5 rounds (`AUTHORING_MAX_ROUNDS`). The severity threshold descends per round (round 1: all findings, round 2: P1+P2, from round 3: P1 only — findings below it are accepted as known limitations instead of iterated further), and from round 2 on Codex receives the previous rounds' findings including their disposition as review context, so settled or deliberately rejected points are not re-reported. At the cap: open P1 → escalation, otherwise accept + known-limitations report. Safeguards:

- **Prior-content check:** Pre-existing content (e.g. merged artifacts from an earlier run) does not vindicate an idle agent — the artifact must change on the first run, otherwise escalation.
- **Protected files:** `.adw/config.yaml` and (from phase 2 on) the reviewed spec are restored byte-exactly after *every* agent run — agents can technically write to them, but never effectively change them.
- **Uncommitted guard:** Tracked artifacts with user changes abort the run instead of silently discarding edits; the tool's own (crashed) intermediate states are recognized as an exception.
- **Archiving:** Reviewed artifacts move to `.adw/runs/<id>/`; the main checkout is reset to the checked-in state and stays clean.

Afterwards: `phase=awaiting_approval`, state saved, `AwaitingApproval` exception → CLI exit 2. `--no-approval` or `approval_granted` skips the pause; both are persisted in the state.

</div>

Phase 3 — `run_build_phase` / `_run_lane`: the Gate loop

<div class="inner">

Lanes run sequentially or (with `--parallel`) in a `ThreadPoolExecutor`. Per Lane:

1.  Create the Worktree (idempotent), pin the fork point as `base_sha`, copy artifacts in and commit them.
2.  **Loop:** limit check → checkpoint (HEAD as `expected_head`, iteration++) → build agent (Opus, session resume, neighboring Lanes invisible via deny rule) → invariant checks (no agent commit, correct branch) → artifact restore → Gates.
3.  Gates red: persist error output + Circuit-Breaker baseline as `pending_task`/`last_failures`, back to 2. Gates green: persist the `gates_passed` evidence **with tree hash**, then the orchestrator commits.

The tree hash is produced via a temporary Git index (`GIT_INDEX_FILE`, seeded from HEAD, then `add -A` + `write-tree`) — it binds the "Gates were green" evidence to exactly the verified content including untracked files. On resume, even a Lane marked `completed` is revalidated against it; if the tree no longer matches, the Lane goes back into the loop instead of being passed on unverified.

</div>

Phase 4 — `run_integration_phase` / `_integration_loop`: merge + E2E

<div class="inner">

The integration branch is rebuilt **freshly each round** from the base branch (delete Worktree + branch, recreate, merge Lane branches) — this makes the operation idempotent and crash-safe; there is no half-merged state a resume would have to interpret. Merge conflict or merge timeout → `merge --abort` + escalation. The E2E Gate runs with both Lanes' ports in the env. On red: persist the round counter → E2E Triage agent (Sonnet 5) → Findings via `_dispatch_lane_fixes` into the Lanes (full Gate loop!) → re-integrate. `_integration_loop` is deliberately reusable: the review phases also obtain their Worktree through it — **every review fix runs through merge + E2E again**, not just through the Lane Gates.

</div>

Phases 5–6 — Codex review + final review + Triage

<div class="inner">

Before every review: `_resume_pending_lanes` sends **every** Lane through `_run_lane` — unfinished fixes (crash windows) catch up on Gates + commit, finished ones are revalidated via tree hash. No ungated state ever reaches a review.

**Phase 5:** `codex.review("code", <changed files via three-dot diff>, cwd=review worktree)`. `needs_fixes` → persist the round, limit check *before* the Dispatch (no "terminal fix" that no review ever checks again), Circuit-Breaker on identical Finding sets, Dispatch into the Lanes, re-review. The same **review-loop policy** as in authoring applies: max. 5 rounds (`MAX_REVIEW_ROUNDS`), descending severity threshold per round (1: all, 2: P1+P2, from 3: P1 only — dropped findings go to `followups.md`), previous rounds' findings + dispositions are passed to Codex as context; Circuit-Breaker and Dispatch operate on the actionable finding set only.

**Phase 6:** Final reviewer (Fable, read-only) responds in the Findings JSON **with mandatory `category`** (if it is missing, no Triage is possible → escalation instead of a silent default). `triage_final_review` (pure code) separates: `scope_gap` → deduplicated follow-up report; the rest → fix cycles per Lane (max. 3). The cycle increment is persisted **in the same save** as the staged fix task (`mutate_staged` hook in the Dispatch) — a crash cannot burn budget without the associated fix being caught up on resume.

</div>

Phase 7 — `run_ci_phase`: push + pipeline monitoring

<div class="inner">

Push via `git push --force-with-lease -u origin <branch>` (the integration branch is rebuilt each round, so non-fast-forward is expected; foreign remote changes are still never overwritten). The forge is determined via `ci.provider` or the origin URL (`forge.py`; in dry-run fallback gitlab, otherwise fail fast). GitLab: `poll_pipeline` queries the pipeline **server-side SHA-filtered** (`glab api projects/:id/pipelines?ref=…&sha=…`); GitHub: `github.poll_ci` polls all workflow runs of the push SHA (`gh api …/actions/runs?head_sha=…`) until all are completed, red runs deliver `gh run view --log-failed` excerpts: neither does the terminal pipeline of the previous push judge the new result, nor can a foreign newer pipeline hide the one being sought. The time budget is tracked as remaining time and the sleep is capped to it. Red with logs → log analyst (Sonnet 5, cwd = pushed worktree) → one re-entry through the Lane loops; red *without* logs (canceled/YAML error) → direct escalation instead of analysis on zero evidence.

</div>

## 3. Agent Registry & Model Economics

Each agent is a declarative `AgentSpec` (model, tool restriction, path rules, system prompt addition, permission mode). The `SdkAgentRunner` translates it into `ClaudeAgentOptions` (system_prompt preset `claude_code` + append, `cwd`, `resume`, isolated settings: no repo-controlled hooks/MCP servers).

| Agent            | Model                        | Tools                                                                 | Mission / hard rule                                                                                               |
|------------------|------------------------------|-----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| `spec_agent`     | Fable 5 (`claude-fable-5`)   | Read/Grep/Glob + Write **only** `.adw/spec.md`                        | Specification following a fixed template; never implements                                                        |
| `plan_agent`     | Fable 5                      | Read/Grep/Glob + Write **only** `.adw/plan.md` + `.adw/contract.yaml` | Plan (workstreams) + interface contract; never implements                                                         |
| `build_agent`    | Opus 4.8 (`claude-opus-4-8`) | Read/Write/Edit + **sandboxed** Bash, writes only worktree-relative   | Workstream strictly against the contract, TDD; **does not commit**; may deviate from fix plans with justification |
| `e2e_triage`     | Sonnet 5 (`claude-sonnet-5`) | read-only                                                             | Playwright failures → Lane assignment; fixes nothing; responds only with schema JSON                              |
| `log_analyst`    | Sonnet 5                     | read-only                                                             | CI logs → structured Findings with Lane; fixes nothing                                                            |
| `final_reviewer` | Fable 5                      | strictly read-only (no Write/Edit/Bash)                               | Implementation against the spec; Findings only, mandatory field `category`                                        |

**Model economics:** the most expensive judgment (Fable) only at the leverage points spec/plan/final review; Opus builds; Sonnet 5 handles the assembly-line Triage. The path rules are **artifact-exact** (e.g. `Write(.adw/spec.md)` instead of `Write(.adw/**)`) — a blanket `.adw/**` would otherwise also make `.adw/runs/<id>/state.json` writable. In addition, the run directories are deny-listed for all agents, and parallel Lanes cannot see each other via `deny_read_paths`.

## 4. Findings Schema & Strict Parser Contract

All reviewers (Codex and Claude reviewers) respond in the same JSON schema; the instruction for it (`SCHEMA_INSTRUCTION`) is **one shared constant** in `findings.py` and is embedded in every reviewer prompt:

    {
      "verdict": "ok | needs_fixes",
      "findings": [{
        "severity": "P1 | P2 | P3",
        "lane": "frontend | backend | unknown",
        "file": "path/relative/to/repo",
        "issue": "Description of the problem",
        "remediation_plan": ["Step 1", "Step 2"],
        "category": "scope_gap | implementation | trivial"   // final reviewer only
      }]
    }

Validation is strict via pydantic: `extra="forbid"`, verdict-findings consistency (`ok` only with an empty list, `needs_fixes` requires ≥ 1 Finding), mandatory fields. The parser `extract_review_result` accepts **exclusively** (a) output that is a JSON object as a whole, or (b) the content of the **last** ```` ```json ```` fence. Everything else — prose around bare JSON, drafts, truncated responses, unclosed fences — is a `FindingsParseError` and thus an escalation case. Adversarial inputs (duplicate keys, nesting \> 100, integers \> 100 digits) fail closed in linear time. In the phases, `ValidationError` is additionally caught: schema-violating but valid JSON escalates just as cleanly as broken JSON.

## 5. State, Checkpoints & Crash Resume

`RunState` (pydantic) is the single source of truth for the progress of a run. Persistence mechanics:

- **Atomic:** snapshot into a temp file + `os.replace` — a half-written state never exists.
- **Serialized:** exclusive `flock` on `.adw/runs/.seq` around every write; `RunState.update()` offers load→mutate→write as a transaction for parallel lane threads.
- **Monotonic:** a sequence number (instead of file mtime with kernel-tick granularity) determines `find_latest`; a corrupt `.seq` is reconstructed from the persisted states.

The most important checkpoint fields and their purpose

<div class="inner">

| Field                                                                                  | Level    | What it survives                                                                                             |
|----------------------------------------------------------------------------------------|----------|--------------------------------------------------------------------------------------------------------------|
| `authoring_session / _pending_task / _last_findings / _rounds / _prior_context`        | Run      | Crash in the middle of the spec/plan review cycle: session, open fix task, Circuit-Breaker baseline, round budget, findings history |
| `pending_task`, `last_failures`                                                        | Lane     | Crash between Gate failure and fix run                                                                       |
| `gates_passed` + `gates_tree`                                                          | Lane     | Crash between "Gates green" and commit — the evidence is bound to the exact tree hash and thus not forgeable |
| `expected_head`                                                                        | Lane     | Detection of foreign commits across a crash window (orchestrator-only commit invariant)                      |
| `base_sha`                                                                             | Lane     | Fork point of the Lane — restorations use the immovable state, not the advancing base branch                 |
| `integration_rounds / review_rounds / fix_cycles / ci_reentries` (+ `*_last_failures`, `review_prior_context`) | Run/Lane | All loop budgets — a restart grants no extra attempts; limit checks come *before* expensive work             |
| `dry_run`, `skip_approval`, `pinned_base_branch`                                       | Run      | CLI decisions that the resume invocation no longer knows                                                     |

The consistent pattern: **budget increments are persisted atomically with the work that justifies them** (via the `mutate_staged` hook in the fix Dispatch), and **Circuit-Breaker baselines are advanced only *after* the fix Dispatch** — otherwise a crash in between would mis-escalate on resume as an "identical round".

</div>

## 6. Limits & Circuit-Breakers

| Loop                          | Limit (constant)                                                            | Additionally                                                                                                                                           |
|-------------------------------|-----------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| Gate loop per Lane            | 10 (`MAX_GATE_ITERATIONS`) — per task, reset on a new fix task              | Circuit-Breaker `check_progress`: exactly the same failure/Finding set as in the previous round → immediate escalation instead of exhausting the limit |
| Integration/E2E               | 10 rounds (`MAX_E2E_ROUNDS`, run-wide)                                      |                                                                                                                                                        |
| Authoring loop (spec/plan)    | 5 rounds (`AUTHORING_MAX_ROUNDS`)                                           | Severity threshold per round: 1 all, 2 P1+P2, from 3 P1 only; at the cap P1 → escalation, otherwise accept + known limitations                         |
| Codex code review             | 5 rounds (`MAX_REVIEW_ROUNDS`)                                              | Same severity threshold; previous rounds' findings + dispositions as Codex context, below-threshold findings → `followups.md`                          |
| Fix cycles after final review | 3 per Lane (`MAX_FIX_CYCLES`)                                               |                                                                                                                                                        |
| CI re-entry                   | 1 (`MAX_CI_REENTRIES`)                                                      |                                                                                                                                                        |
| Gate/Codex/glab subprocesses  | Timeout per Gate from the config; Codex 900 s; glab 120 s; CI budget 2700 s | Process-group kill (`start_new_session` + `killpg`) on all exit paths — no zombie processes                                                            |

## 7. Security Model

| Layer               | Mechanism                                                                                                                                                                                                                                                                                                                                                                      |
|---------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Agent tools         | `tools=` **restricts** the available tools (not just auto-approve); `allowed_tools` with artifact-exact path rules; reviewers strictly read-only; build Bash sandboxed and limited to the Worktree.                                                                                                                                                                            |
| Process environment | `safe_env()` whitelist for **all** subprocesses (PATH, HOME, LANG, … — no API keys/cloud creds). The SDK merges `os.environ`; non-whitelisted variables are blanked via `""` override. `SSH_AUTH_SOCK` only for the push subprocess, `CODEX_HOME` only for the CodexRunner.                                                                                                    |
| Auth & billing      | Only the stored Claude CLI login (`_require_stored_login()`, fail fast before tokens flow) — consumption thus runs against the Claude plan, never token-by-token against the API: `ANTHROPIC_API_KEY` & co. are blanked via `""` override. Codex with isolated `CODEX_HOME` (only `auth.json`, no user-configured MCP servers), token rotation is synced back via CAS + flock. |
| Git integrity       | Orchestrator git always with `core.hooksPath=/dev/null` + env whitelist. Commits only by the orchestrator; agent commits, branch switches (symbolic-ref check), and HEAD movements in the crash window are detected and escalate.                                                                                                                                              |
| Artifact integrity  | Spec/plan/contract/config are effectively immutable for agents: byte-exact restore after every agent run or before Gates/commit; symlink/directory replacements are detected and replaced, never followed (no writing outside the Lane); an injected config is restored from the pinned fork point or — positively verified — deleted.                                         |
| Lane isolation      | Own Worktree per Lane; neighboring Lanes unreadable via `deny_read_paths`; ports deterministic from the run_id with a bind check.                                                                                                                                                                                                                                              |

## 8. Design Decisions (with Rationale)

<div class="decision">

**D1 — Control flow is code, not prompt.** Loops, limits, Dispatch, merges, polling as Python instead of agent instructions.

<div class="why">

**Why:** Repeatable work belongs in deterministic code — 0 tokens, always the same behavior, testable. Agents exist only where judgment is needed. (Principle from the video analysis "Forget Loop Engineering", validated in brainstorming.)

</div>

</div>

<div class="decision">

**D2 — Reviewer ≠ fixer; every fix takes the validated path.** Reviews deliver only Findings; fixes run without exception through build agents + all Gates (+ E2E in parallel mode).

<div class="why">

**Why:** "Trivial" direct fixes bypass exactly the checks that are supposed to catch errors. A reviewer's fix plan is a recommendation — the build agent may deviate with justification, because it knows the spec and the conventions.

</div>

</div>

<div class="decision">

**D3 — Strict parser contract instead of tolerance heuristics.** Only whole-JSON or the last ```` ```json ```` fence; everything else is an error.

<div class="why">

**Why:** Tolerance heuristics cannot be sealed against adversarial/noisy outputs — when in doubt they accept a stale or fabricated "ok". A parse error is safe (retry/escalation), a false "ok" is not. The decision was made after a tolerant prose extraction oscillated in the review loop.

</div>

</div>

<div class="decision">

**D4 — v1 = v2 with one Lane: one code path, one flag.** `--parallel` enables multi-lane operation; no separate scripts.

<div class="why">

**Why:** Two code paths inevitably diverge. Single-lane mode is parallel mode with n=1 — same tests, same guarantees.

</div>

</div>

<div class="decision">

**D5 — Config in the target repo, fail fast, no guessed defaults.** `.adw/config.yaml` belongs to the target project; the orchestrator is generic.

<div class="why">

**Why:** Gates/branches/E2E are project knowledge. Silent defaults produce silent misbehavior — the only defaults are the documented CI poll values.

</div>

</div>

<div class="decision">

**D6 — Session resume instead of context rebuild.** Fix tasks go to the same SDK session (`resume=session_id`); session IDs are part of the persisted state.

<div class="why">

**Why:** The agent that wrote the code fixes it faster and more consistently with full context — and it saves the tokens of a rebuild.

</div>

</div>

<div class="decision">

**D7 — Verify instead of trust: evidence with tree hashes.** `gates_passed` is valid only together with the `gates_tree` hash of the exact Worktree content; completed Lanes are revalidated on resume.

<div class="why">

**Why:** Commit messages or flags could be forged by the agent or go stale in crash windows. The content hash binds the evidence to exactly the verified state.

</div>

</div>

<div class="decision">

**D8 — Idempotent reconstruction instead of state interpretation.** Rebuild the integration Worktree freshly each round; review Worktrees restorable at any time.

<div class="why">

**Why:** Correctly *interpreting* a half-merged or manipulated state is error-prone; throwing it away and deterministically recreating it is cheap and provably correct.

</div>

</div>

<div class="decision">

**D9 — Escalation as a first-class result.** Every exhausted limit, every conflict, every unreadable reviewer response ends in a controlled way: report + `phase=escalated` + exit ≠ 0. Escalated runs are not resumable.

<div class="why">

**Why:** An orchestrator that "somehow keeps going" produces expensive garbage. The human receives the state reached and the concrete reason — and decides.

</div>

</div>

<div class="decision">

**D10 — Pinned base: `pinned_base_branch` (run) + `base_sha` (Lane).** The effective base branch is persisted at start; Lanes pin their fork SHA.

<div class="why">

**Why:** Base branches move on and configs change mid-run. Without pinning, continuations would integrate, diff, or push against a different history than the one the Lanes were forked from.

</div>

</div>

<div class="decision">

**D11 — Dry run as a product feature, not a test trick.** `--dry-run` injects the mocks into the same production code path, incl. canonical failure fixtures (a synthetic Gate that only the second run turns green; an E2E red that a triaged Lane fix resolves). The simulation stage is derived from the Worktree content, so that even a dry-run resume continues correctly.

<div class="why">

**Why:** Config, Gates, and the complete control flow can thus be accepted without tokens/network/push — and the acceptance tests (SPEC §8) drive exactly this path.

</div>

</div>

<div class="decision">

**D12 — Codex as CLI subprocess, not a second SDK.** `codex exec --sandbox read-only`, isolated `CODEX_HOME`, hard timeout.

<div class="why">

**Why:** An independent reviewer with a different model stack catches different error classes; the CLI boundary keeps the coupling minimal, and the read-only sandbox prevents mutations.

</div>

</div>

<div class="decision">

**D13 — Plan billing: Claude Code CLI under the hood, stored-login-only.** The Agent SDK spawns the Claude Code CLI as a headless subprocess; ADW enforces the stored CLI login (`_require_stored_login()`) and actively blanks API-key environment variables — no token-by-token API path exists. If an agent call fails (typically: subscription window exhausted), the CLI catches the `AgentRunError`, exits with exit 1 + a resume hint, and leaves the run at the checkpoint — deliberately NO escalation, because `phase=escalated` would be final and the condition is transient: `adw resume` continues after the limit reset.

<div class="why">

**Why:** Cost control structurally instead of by discipline — consumption counts against the plan quotas (5-hour window/weekly limit); an accidental API budget drain is technically impossible. The price is an availability risk that the crash-resume engineering covers anyway.

</div>

</div>

## 9. Packages, Frameworks & External Tools

### Python dependencies (pyproject.toml)

| Package                     | Version      | Role in ADW                                                                                                                                                                                                                                                                                                                                                                                                                   |
|-----------------------------|--------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `pydantic`                  | ≥ 2          | All data contracts: config schema, `RunState`/`LaneState`, Findings (`extra="forbid"`, cross-validators)                                                                                                                                                                                                                                                                                                                      |
| `typer`                     | ≥ 0.12       | CLI (`run/resume/approve/status`, Annotated style), test client `CliRunner`                                                                                                                                                                                                                                                                                                                                                   |
| `pyyaml`                    | ≥ 6          | Config parsing with a custom StrictLoader (duplicate keys → error)                                                                                                                                                                                                                                                                                                                                                            |
| `claude-agent-sdk`          | ≥ 0.2.118    | Claude agents headless: `query()` + `ClaudeAgentOptions` (model, cwd, resume, tools/allowed_tools, permission_mode, sandbox, setting_sources). **The SDK is a wrapper around the Claude Code CLI** — it spawns `claude` as a headless subprocess (verified in the SDK source code, `subprocess_cli.py`); auth and billing therefore run via the CLI login/plan. Lower bound because of `tools`/`strict_mcp_config`/`sandbox`. |
| `anyio`                     | ≥ 4          | Sync wrapper around the async SDK stream (`anyio.run`)                                                                                                                                                                                                                                                                                                                                                                        |
| `pytest` / `pytest-asyncio` | ≥ 8 / ≥ 0.24 | Test suite (331 tests)                                                                                                                                                                                                                                                                                                                                                                                                        |
| `ruff`                      | ≥ 0.6        | Lint (E,F,W,I,UP,B) + formatter, line-length 100, target py312                                                                                                                                                                                                                                                                                                                                                                |

### External tools (at runtime via subprocess)

| Tool                                   | Role                                                         | Safeguards                                                                               |
|----------------------------------------|--------------------------------------------------------------|------------------------------------------------------------------------------------------|
| **git**                                | Worktrees, branches, merges, tree hashes, push               | always `core.hooksPath=/dev/null` + env whitelist + timeouts                             |
| **codex** (CLI, verified with 0.144.0) | Independent reviews (spec/plan/code)                         | read-only sandbox, `mcp_servers={}`, isolated `CODEX_HOME`, 900 s timeout, strict parser |
| **glab** (verified with 1.53.0)        | GitLab: issue intake, pipeline/job status, job logs          | injectable (`run_glab` seam), 120 s timeout, SHA-bound pipeline query                    |
| **gh** (verified with 2.95.0)          | GitHub: issue intake, Actions runs/jobs, `--log-failed` logs | injectable (`run_gh` seam), 120 s timeout, head_sha-bound run query                      |
| **uv** (0.10.x)                        | Project/dependency management, entry point `adw`             | —                                                                                        |

## 10. Test Strategy

- **331 tests, developed TDD-first** (every task/bugfix began with a red test). No test needs network or tokens: SDK, Codex, and glab are mocked at their interfaces — **git is real** (throwaway repos in `tmp_path`).
- **Scriptable mocks as the test backbone:** `MockAgentRunner` (response queues per agent, simulated file outputs static or as `Callable(cwd)` for per-Lane behavior, complete call recording incl. `resume`/`deny_read_paths`) and `MockCodexRunner`.
- **Focus on crash windows:** A large part of the phase tests deliberately simulates aborts between two checkpoints (manipulate state, "let the process die", resume) as well as agent manipulation (foreign commits, symlink artifacts, rewritten configs, manipulated worktrees after the completed checkpoint).
- **Acceptance level:** `tests/test_e2e_dry_run.py` maps the DoD criteria 1–5 from `docs/SPEC.md` §8 — complete CLI dry runs (single + parallel), Gate fail→same-session fix, approval gate, Triage paths, crash resume.
- **Review gate in the development process:** per task `uv run pytest` + `ruff check/format` + `codex review --uncommitted`; across tasks 10–13 this found 8 P1 and 19 P2 Findings, each fixed with a regression test first.

## 11. Known Limitations (documented, deliberately accepted)

| Limitation                                                                                                                     | Assessment                                                                                                                                          |
|--------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| The git configuration of the **target repo** (clean filters, signing programs) is considered trustworthy.                      | It is controlled by the user and lies outside the agent write paths; configured filters run just as with any manual git invocation.                 |
| The Codex read-only sandbox prevents mutations, but not reads outside the cwd.                                                 | Same risk as with any manual `codex review`; mitigated by a secret-free env and disabled MCP servers.                                               |
| The Codex token back-sync does not hold the lock for the entire review duration.                                               | Deliberate — otherwise parallel reviews would be serialized for minutes. In the extreme case (two simultaneous rotations), a one-off `codex login`. |
| A crash between the CI re-entry checkpoint and the renewed poll can cause the resume to escalate without a second fix attempt. | Bounded and on the safe side: better one re-entry too few than an unbudgeted loop.                                                                  |
| The Circuit-Breaker compares exact error texts — varying outputs (timestamps, counters) bypass it.                             | Then the hard round limits take over.                                                                                                               |

## 12. Glossary

| Term                                  | Meaning                                                                                                                                                                |
|---------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **RunContext**                        | Dataclass with all dependencies of a phase (repo, config, state, runners, glab/sleep seams, lock) — the dependency injection root.                                     |
| **AgentRunner / CodexReviewer**       | The two protocols (PEP 544) behind which all agent calls live; production and mock implementations are interchangeable.                                                |
| **Gate evidence (`gates_tree`)**      | Tree hash of the complete Worktree content (incl. untracked) via a temporary Git index — binds "Gates green" to exactly the verified state.                            |
| **Circuit-Breaker**                   | `check_progress`: identical failure/Finding set as in the previous round → immediate escalation.                                                                       |
| **Dispatch (`_dispatch_lane_fixes`)** | Central fix-routing function: group Findings per Lane, stage Lane state atomically (optionally with budget increment via `mutate_staged`), then the regular Lane loop. |
| **Session resume**                    | SDK feature: follow-up task to an existing agent session (`resume=session_id`) — full context without a rebuild.                                                       |
| **Worktree**                          | Second checkout of the same Git repo (`git worktree add`); the basis of Lane isolation without repo clones.                                                            |
| **Escalation**                        | Controlled run ending: `escalation.md` + `phase=escalated` + exit ≠ 0.                                                                                                 |

[↑ back to top](#tldr)

</div>

ADW Technical Specification · generated on 2026-07-15 · Sources: repo `agentic-developer-workflow` (code under `adw/`, `docs/SPEC.md`, `docs/PLAN.md`, `pyproject.toml`, commit history up to main `9b89dd6`) · Usage: `docs/handbuch/ADW-USER-HANDBUCH.html`
