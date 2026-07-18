# SPEC — Agentic Developer Workflow (ADW)

**English** | [Deutsch](SPEC.de.md)

Status: 2026-07-14 · validated in brainstorming (Stefan + Fable 5) · Reference: `Handout_AgenticDeveloperWorkflow.html` (v2)

## 1. Goal

A Python orchestrator that takes an issue fully automatically through seven phases —
Spec → Plan + API contract → Build → Integration/E2E → Codex review → final review → Push/CI/Staging —
as a combination of **agents** (Claude Agent SDK + Codex CLI) and **deterministic code**
(Gates, Dispatch, Triage, merges, CI polling).

Guiding principle (from the video analysis "Forget Loop Engineering"): Control flow is code, not prompt.
Agents only where judgment is needed; everything repeatable runs as code —
0 tokens, deterministic, always the same.

## 2. Scope

**v1 (one build lane):** Phases 1–2 (spec/plan agent with Codex review loop), plan approval gate,
one build lane with Gates + fix loop, Codex code review with fix plan, final review (read-only)
with Triage, push + GitLab CI monitoring until staging.

**v2 (`--parallel`):** Dispatch into parallel FE/BE lanes (own Worktrees, sessions, ports, Gates),
integration merge, Playwright E2E gate, E2E Triage agent, log analyst for red CI pipelines.

v1 = v2 with one lane: **one** code path, one CLI (`adw run`), the `--parallel` flag enables
multi-lane operation. No separate scripts.

**Non-goals (v1/v2):** Kanban/webhook intake, workflow router (chore/bug/feature/hotfix),
ZTE (auto-merge to production), multi-repo runs, own web UI, agent sandboxes beyond
Git Worktrees.

## 3. Actors & Agent Registry

| Agent | Model | Tools | Mission |
|---|---|---|---|
| Spec agent | Fable 5 | Read/Grep/Glob + Write (only `.adw/`) | `.adw/spec.md` per template; never implements |
| Plan agent | Fable 5 | Read/Grep/Glob + Write (only `.adw/`) | `.adw/plan.md` (workstreams FE/BE) + `.adw/contract.yaml` |
| Build agent (per lane) | Opus 4.8 | Read/Write/Edit/Bash, `cwd` = lane Worktree | Workstream strictly against the contract; fix plans = recommendation |
| Codex reviewer | Codex (CLI) | `codex exec --sandbox read-only` | Reviews of spec, plan, code; Findings + `remediation_plan` as JSON |
| E2E Triage | Sonnet 5 (workhorse) | Read | Assign Playwright failures to a lane; fixes nothing |
| Log analyst | Sonnet 5 (workhorse) | Read | CI logs → structured Findings with lane assignment |
| Final reviewer | Fable 5 | Read/Grep/Glob (strictly read-only) | Check implementation against spec; Findings only |

Ground rules: **Reviewers never fix.** Every fix goes through the build agents and again through
all Gates — no shortcut for "trivial" fixes.

## 4. The Seven Phases (target behavior)

1. **Spec:** Issue (CLI text, GitLab issue via `glab` or GitHub issue via `gh`) → spec agent writes `.adw/spec.md`
   (goal, scope, non-goals, acceptance criteria, definition of done). Codex reviews; Findings
   go back to the spec agent (session resume) until verdict `ok`.
2. **Plan + contract:** Plan agent produces `.adw/plan.md` + `.adw/contract.yaml`
   (OpenAPI/types/events). Codex reviews both together until `ok`.
   **Plan approval gate:** Workflow pauses (state persisted, exit); Stefan reads plan +
   contract and continues via `adw resume <run_id>` or `adw approve <run_id>`.
   Can be disabled with `--no-approval`.
3. **Build:** Dispatch (code) splits the plan into workstreams. Per lane: own Git Worktree
   (branch off base branch), own SDK session, own ports. Lane loop: build agent works →
   Gates run (commands from target-repo config) → on fail the error outputs go back as a
   follow-up task to **the same session**. Max. 10 iterations.
4. **Integration + E2E** (only `--parallel`): Code merges lane branches onto an
   integration branch; E2E command (Playwright) runs. On red, the E2E Triage agent
   assigns each failure to a lane → fix in the lane → integrate again. Max. 10 rounds.
5. **Codex code review:** Codex reviews the integrated diff, delivers Findings with
   `remediation_plan`. Findings are routed via the `lane` field; build agents check the
   fix plan against spec/conventions and may deviate with justification. Runs until verdict `ok`.
6. **Final review + Triage:** Fable 5 checks read-only against `.adw/spec.md`. Triage (code):
   `scope_gap` → follow-up issue (report, no auto-restart); `implementation`/`trivial` →
   fix cycle into the lane. Max. 3 fix cycles.
7. **Push + CI:** Merge/push of the feature branch, then CI polling (60-s interval,
   45-min timeout) until pipeline + staging deploy are green — GitLab via `glab`, GitHub
   Actions via `gh` (forge from `ci.provider` or origin URL). On a red pipeline:
   log analyst reads logs → Findings → back to phase 3/4.

**Escalation:** Every exhausted limit and the circuit breaker (a fix iteration resolves
**nothing** → abort immediately) ends the run with exit code ≠ 0 and an
escalation report (`.adw/runs/<run_id>/escalation.md`): what was achieved, what is open, why.

## 5. Interfaces

### CLI

```
adw run --repo <pfad> (--issue "Text" | --gitlab-issue <id> | --github-issue <nr>)
        [--parallel] [--dry-run] [--no-approval] [--base-branch <name>]
adw resume <run_id> [--repo <pfad>]      # after a crash or approval pause
adw approve <run_id>                     # grant plan approval + continue
adw status [<run_id>]                    # show runs + phase
```

### Config in the target repo: `.adw/config.yaml`

```yaml
base_branch: staging
lanes:
  backend:
    gates:                     # order = execution order; each: name + command + timeout
      - {name: black,  cmd: "black --check .", timeout: 120}
      - {name: isort,  cmd: "isort --check-only .", timeout: 120}
      - {name: pytest, cmd: "pytest -x -q", timeout: 1800}
  frontend:                    # optional; if the lane is missing, v1 single-lane runs
    gates:
      - {name: eslint, cmd: "npm run lint", timeout: 300}
e2e:                           # optional; only relevant with --parallel
  cmd: "npx playwright test"
  timeout: 1800
ci:
  poll_interval: 60
  timeout: 2700
  staging_job: deploy-staging  # job name that must be green
  provider: gitlab             # optional: gitlab | github; otherwise auto-detection via origin URL
```

Missing/broken config → immediate, clear error (fail fast), no guessing of defaults
except the documented ones (`poll_interval`, `timeout`).

### Findings schema (JSON, identical everywhere)

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

`category` is filled only by the final reviewer (Triage basis). Codex is pinned to this
schema via prompt + an `--output-schema`-like instruction.

**Parser contract (strict, deliberate design decision):** Accepted is exclusively
(a) output that is a JSON object as a whole, or (b) the content of the last
```` ```json ````-fence. Everything else (prose around bare JSON, drafts, truncated or
wrapped answers, unclosed fences) → `FindingsParseError` = retry/escalation case.
Tolerance heuristics cannot be sealed against adversarial outputs (stale-ok risk);
a parse error is safe, a false "ok" is not. Validation strictly via Pydantic
(`extra="forbid"`, verdict-findings consistency, required fields except `category`).

### Artifacts & State

- `.adw/spec.md`, `.adw/plan.md`, `.adw/contract.yaml` — in the target repo, are
  **committed along** on the feature branch (traceability).
- `.adw/runs/<run_id>/` — gitignored: `state.json` (RunState), agent transcripts,
  Gate outputs, `escalation.md`.
- `RunState` (Pydantic): run_id, issue, phase, lanes (worktree, branch, session_id, ports,
  iterations), approval status, ci status. Persisted after every phase transition →
  `adw resume` continues exactly there.

## 6. Design Principles (binding)

1. **Three actors, clearly distributed** — Gates/merges/polling/Dispatch/Triage are code
   (`subprocess` with a **real `timeout`** parameter, always).
2. **Reviewer ≠ fixer.**
3. **Every fix takes the validated path** (all Gates, no exception).
4. **Structured handovers:** JSON/Pydantic between all nodes, no free-text parsing.
5. **Model economy:** Fable 5 only spec/plan/final review; Opus builds; Sonnet 5 triages.
6. **Security:** `allowed_tools` per agent from the registry; build agents limited to their
   Worktree via `cwd`; env whitelist for all subprocesses (no secret leakage);
   never blanket permission skipping.
7. **Session resume instead of context rebuild** in all fix cycles (SDK `resume=session_id`).

## 7. Technology

- Python ≥ 3.12, **uv** (pyproject.toml + uv.lock), package `adw/`, entry point `adw` (typer).
- `claude-agent-sdk` (query + ClaudeAgentOptions: model, cwd, resume, allowed_tools,
  system_prompt preset `claude_code` + append, permission_mode). The SDK spawns the
  **Claude Code CLI** as a headless subprocess — auth/billing run via the
  stored CLI login (plan quotas), enforced via stored-login-only +
  blanking the API key env variables; no token-by-token API path. Failed
  agent calls (e.g. limit exhausted) end the run in a controlled way WITHOUT escalation
  — `adw resume` continues at the checkpoint after the reset.
- Codex as a CLI subprocess (`codex exec --sandbox read-only`), no second SDK.
- `glab` for GitLab or `gh` for GitHub (read issue, pipeline/Actions status),
  `git worktree` for lanes,
  ports deterministic from run_id (base port + hash offset, socket bind check as fallback).
- Agent and Codex calls each behind an interface (`AgentRunner`, `CodexRunner`);
  `--dry-run` injects mocks with canonical fixtures (simulated Gate fails,
  review Findings) — complete v1/v2 control flow testable without tokens.

## 8. Acceptance Criteria (Definition of Done)

1. `adw run --repo <test-repo> --issue "…" --dry-run` passes through all 7 phases (single lane)
   without token consumption; `--dry-run --parallel` passes through both lanes incl. the E2E Triage path.
2. A simulated Gate fail leads to a fix task to the same session; after 10 unsuccessful
   iterations or on zero progress (circuit breaker), `escalation.md` is created and
   exit code ≠ 0.
3. Plan approval gate: run pauses after phase 2, `adw approve` continues; `--no-approval`
   skips.
4. Triage: a `scope_gap` Finding creates a follow-up report instead of a fix cycle;
   an `implementation` Finding routes into the right lane; after 3 fix cycles, escalation.
5. `adw resume <run_id>` continues an aborted run in the same phase
   (state round-trip test).
6. All deterministic modules (config, state, findings, triage, gates, worktrees/ports,
   dispatch, ci-polling) have pytest tests (TDD, test-first); lint (`ruff` or
   flake8+isort+black) green.
7. A real (token) run against a small test repo with a real issue is planned only **after**
   acceptance of the dry-run scaffold (not part of this DoD).
