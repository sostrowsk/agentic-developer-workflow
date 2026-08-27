<div>

<div class="wrap">

<span class="badge">USER HANDBOOK · As of 2026-07-15 · adw 0.1.0</span>

# Agentic Developer Workflow (ADW)

An issue goes in, a reviewed feature branch with green CI comes out — fully automated through seven phases.

</div>

</div>

<div role="main">

<div id="tldr" class="section tldr">

## ⚡ At a glance

- **What:** `adw run` takes an issue through **Spec → Plan+Contract → Build → Integration/E2E → Codex review → final review → Push/CI** — agents provide judgment, the control flow is deterministic code.
- **Getting started:** `uv sync`, then create `.adw/config.yaml` in the target repo, then `uv run adw run --repo <path> --issue "…"`.
- **Test safely:** `--dry-run` runs the complete flow with mocks — 0 tokens, no network, no push.
- **Stay in control:** After the plan, the run pauses (exit 2). Read the plan, then `adw approve <run_id>`. Can be disabled with `--no-approval`.
- **If something goes wrong:** Exit 1 + report under `.adw/runs/<run_id>/escalation.md`. After a crash: `adw resume <run_id>` continues exactly where it left off.

</div>

<div id="kernaussagen" class="section">

## Key points

<div class="kern">

<div class="card">

**You provide an issue, ADW delivers a branch.** The end result is a pushed feature branch whose pipeline, including the staging deploy, is green.

</div>

<div class="card">

**Reviewers never fix.** Every fix goes through the build agents and through all Gates again — there is no shortcut for "trivial" fixes.

</div>

<div class="card">

**Everything is bounded.** Fixed limits (10 Gate iterations, 3 fix cycles, 1 CI re-entry) and a circuit breaker prevent endless loops; after that, a human takes over.

</div>

<div class="card">

**Every state survives a crash.** The run state is persisted continuously; `adw resume` continues in the same phase without losing results.

</div>

</div>

</div>

1.  [Prerequisites & installation](#voraussetzungen)
2.  [Preparing the target repo](#zielrepo)
3.  [The first run (dry run)](#erster-lauf)
4.  [CLI reference](#cli)
5.  [What happens in the seven phases?](#ablauf)
6.  [The plan approval Gate](#approval)
7.  [Artifacts, reports & run directory](#artefakte)
8.  [Crash, pause, resume](#resume)
9.  [Understanding escalations](#eskalation)
10. [Troubleshooting & FAQ](#troubleshooting)
11. [Glossary](#glossar)

## 1. Prerequisites & installation

| Tool                    | Purpose                                                                                                            | Check                                  |
|-------------------------|--------------------------------------------------------------------------------------------------------------------|----------------------------------------|
| Python ≥ 3.12 + **uv**  | Runtime environment of the orchestrator                                                                            | `uv --version`                         |
| **Claude CLI login**    | The Claude agents (Spec, Plan, Build, reviews) run via the Agent SDK with your stored login                        | Sign in to `claude` interactively once |
| **codex** (CLI)         | Independent reviewer for spec, plan and code                                                                       | `codex login` once                     |
| **glab** / **gh** (CLI) | Read the issue and monitor CI — glab for GitLab projects, gh for GitHub projects (only the matching one is needed) | `glab auth status` or `gh auth status` |
| **git**                 | Worktrees, branches, merges, push                                                                                  | —                                      |

    # Clone the repo and install dependencies
    git clone git@gitlab.com:addvendo/agentic-developer-workflow.git
    cd agentic-developer-workflow
    uv sync

<div class="hint">

Without a stored Claude login, ADW aborts **before** the first agent run with a clear message (fail fast) — an API key from the environment is never used silently.

</div>

### Billing: runs on your Claude plan, not on API tokens

ADW talks to Claude **exclusively via the Claude Code CLI** — the Claude Agent SDK launches it under the hood as a headless subprocess. There is no separate API path:

- **Stored-login-only, enforced:** ADW requires the stored CLI login (`~/.claude/.credentials.json` or macOS keychain). Environment variables like `ANTHROPIC_API_KEY` are actively cleared for all agent processes — an API key is never used even if one is set. Accidental token-by-token payment is thus ruled out.
- **Usage = subscription limits:** Runs count against the plan quotas (5-hour window + weekly limits), not against a dollar budget. Zero cost risk, but availability risk instead. The registry uses Fable 5, Opus 4.8 and Sonnet 5 — the plan must provide these models (in practice: Max plan).
- **Limit exhausted ≠ data loss:** If an agent call fails (typically: window empty), the run stops in a controlled way with exit 1 and a resume hint — it does *not* escalate and stays at the persisted checkpoint. After the window resets: `adw resume <run_id>` continues exactly where it left off (sessions, open fix tasks and counters are saved). There is deliberately no automatic "wait until reset".
- **`--parallel` consumes faster:** Two concurrent Opus build sessions plus reviews drain a 5-hour window considerably more quickly — in plan-based operation, single-lane is the more relaxed profile.
- **Codex is a separate subscription:** The Codex reviews run via your ChatGPT/Codex login (isolated `CODEX_HOME`) — likewise no token payment, but a separate quota.

## 2. Preparing the target repo

ADW works **against any Git repo** ("target repo"). All project-specific configuration lives there in **one file**: `.adw/config.yaml`. A template is available under `examples/config.yaml`.

    mkdir -p /path/to/repo/.adw
    cp examples/config.yaml /path/to/repo/.adw/config.yaml   # and adapt it

    # .adw/config.yaml — minimal example (one Lane)
    base_branch: staging
    lanes:
      backend:
        gates:                      # order = execution order, fail fast
          - {name: black,  cmd: "black --check .",      timeout: 120}
          - {name: isort,  cmd: "isort --check-only .", timeout: 120}
          # tdd: true = at least one marked Gate has to be RED before implementing
          - {name: pytest, cmd: "pytest -x -q",         timeout: 1800, tdd: true}

Complete config reference (all keys)

<div class="inner">

| Key                       | Required        | Meaning                                                                                                                                                                     |
|---------------------------|-----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `base_branch`             | yes             | Branch the Lanes fork from and against which diffs are computed.                                                                                                            |
| `lanes.<name>.gates[]`    | yes (≥ 1 Lane)  | Gate list per Lane. Each Gate needs `name`, `cmd` and `timeout` (seconds). Gates run in order; the first red Gate stops the pass.                                           |
| `…gates[].tdd`            | optional (false) | Marks a Gate (typically the test Gate) as a RED proof: in the initial build at least one marked Gate has to be red after the test-only pass, **before** the implementation run (section 5, phase 3).                 |
| `lanes.frontend`          | optional        | If the Lane is missing, ADW runs in single-lane mode. `--parallel` requires `backend` **and** `frontend`.                                                                   |
| `e2e.cmd` / `e2e.timeout` | optional        | E2E command (e.g. `npx playwright test`) — runs only with `--parallel` on the integration branch.                                                                           |
| `ci.poll_interval`        | optional (60)   | Seconds between two pipeline queries.                                                                                                                                       |
| `ci.timeout`              | optional (2700) | Total budget for waiting on CI (45 min default).                                                                                                                            |
| `ci.staging_job`          | optional        | Name of a job (e.g. `deploy-staging`) that additionally has to be green.                                                                                                    |
| `ci.provider`             | optional        | `gitlab` or `github`. If not set, ADW detects the hosting from the origin remote URL; for an unknown host (e.g. self-hosted with a custom domain name) the key is required. |
| `breakpoints[]`           | optional ([])   | Extra approval holds before the expensive, hard-to-reverse steps. A list of `before_integration` (after all build Lanes are green, before integration/review) and/or `before_push` (after the final review, before push/CI). Any other value is a config error. Empty/absent = no extra holds (section 6). |

A missing or broken config (unknown keys, Lane without Gates, Gate without timeout, duplicate keys, an unknown `breakpoints` value) aborts **immediately** with a clear message — no defaults are guessed, apart from the two documented ones (`poll_interval`, `timeout`).

</div>

<div class="warnbox">

**Gates are your quality boundary.** ADW accepts build results only when *all* Gates are green. Whatever the Gates don't check, nobody checks in phase 3 — so configure at least formatter/linter and the test suite.

</div>

## 3. The first run (dry run)

Before any tokens flow: the dry run exercises **the complete control flow** with scripted mock agents — 0 tokens, no network, no push. It verifies your config, the Gates and the entire flow:

    uv run adw run --repo /path/to/repo --issue "Demo feature" --dry-run --no-approval
    uv run adw run --repo /path/to/repo --issue "Demo feature" --dry-run --no-approval --parallel

The dry run is deliberately not a pure fair-weather run: it simulates **one Gate failure** (the fix goes to the same agent session as a follow-up task) and, in `--parallel` mode, **one red E2E run** that the Triage agent routes back into a Lane. So you see exactly the loops a real run would take.

Then the real run:

    # Issue text directly …
    uv run adw run --repo /path/to/repo --issue "Bug: login aborts when …"
    # … or pull it from GitLab/GitHub (title + description via glab/gh)
    uv run adw run --repo /path/to/repo --gitlab-issue 42 --parallel
    uv run adw run --repo /path/to/repo --github-issue 42 --parallel

## 4. CLI reference

    adw run --repo <path> (--issue "Text" | --gitlab-issue <id> | --github-issue <nr>)
            [--parallel] [--dry-run] [--no-approval] [--base-branch <name>]
    adw resume  <run_id> [--repo <path>]     # after a crash; if paused for approval → approve
    adw approve <run_id> [--repo <path>]     # grant plan approval + continue
    adw status  [<run_id>] [--repo <path>]   # show runs + phase

| Option                 | Meaning                                                                                                                                                                                     |
|------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--issue "Text"`       | Issue description directly on the command line. Exactly **one** issue source is required.                                                                                                   |
| `--gitlab-issue <id>`  | Fetches title + description of the issue via `glab issue view` from the target repo's GitLab project.                                                                                       |
| `--github-issue <nr>`  | Fetches title + description of the issue via `gh issue view` from the target repo's GitHub project.                                                                                         |
| `--parallel`           | Builds the frontend and backend workstreams in **two parallel Lanes** (own Worktrees, sessions, ports) and activates integration + E2E Gate. Requires both Lanes in the config.             |
| `--dry-run`            | Mocks instead of agents, fake CI instead of glab, no push. The mode is stored in the run state — even a `resume`/`approve` of a dry run remains token-free.                                 |
| `--no-approval`        | Skips the plan approval pause. Applies to the whole run (survives crash + resume).                                                                                                          |
| `--base-branch <name>` | Overrides `base_branch` from the config. **Pinned** at run start: later changes to the config do not move a running run; switching via flag is only possible as long as no Lanes exist yet. |

### Exit codes

| Code                                | Meaning                                                           | Next step                                   |
|-------------------------------------|-------------------------------------------------------------------|---------------------------------------------|
| <span class="exitcode ec0">0</span> | Run completed (`done`) — branch pushed, pipeline + staging green. | Open a merge request / merge.               |
| <span class="exitcode ec2">2</span> | `awaiting_approval` — the run is waiting for your plan approval.  | Read the plan, then `adw approve <run_id>`. |
| <span class="exitcode ec1">1</span> | Escalation or error.                                              | Read `.adw/runs/<run_id>/escalation.md`.    |

## 5. What happens in the seven phases?

<div class="phase-flow">

<span class="ph">1 Spec<span class="small">2 drafts + synthesis + Codex</span></span><span class="arrow">→</span> <span class="ph">2 Plan + Contract<span class="small">2 drafts + synthesis · Approval</span></span><span class="arrow">→</span> <span class="ph">3 Build<span class="small">Opus 4.8 per Lane + Gates</span></span><span class="arrow">→</span> <span class="ph">4 Integration + E2E<span class="small">--parallel only</span></span><span class="arrow">→</span> <span class="ph">5 Codex review<span class="small">code diff</span></span><span class="arrow">→</span> <span class="ph">6 Final review<span class="small">Fable 5 + Triage</span></span><span class="arrow">→</span> <span class="ph">7 Push + CI<span class="small">glab/gh polling</span></span>

</div>

Phases 1–2: Spec and plan are created — and independently reviewed

<div class="inner">

Each of the two artifacts is written **twice, independently**: the **Spec agent** (Opus 4.8) and **Codex** each produce their own draft of the specification following a fixed template (goal, scope, non-goals, acceptance criteria, definition of done) — in parallel, both drafts land in `.adw/runs/<run_id>/drafts/`. The **Spec synthesis** (Fable 5) then merges them into ONE best-of `.adw/spec.md` (per section the stronger formulation wins, never a union) and writes the short summary `.adw/spec-summary.md` — your decision basis at the approval Gate. **Codex** reviews the merged artifact; Findings go back **to the same synthesis session** as a follow-up task until the verdict is `ok` — at most 5 rounds. The severity threshold descends per round (round 1: all findings, round 2: P1+P2, from round 3: P1 only), and from round 2 on Codex receives the previous rounds' findings including their disposition, so it does not re-report settled or deliberately rejected points. Then the **Plan agent** + Codex + **Plan synthesis** analogously produce `.adw/plan.md` (workstreams), `.adw/contract.yaml` (interface contract: OpenAPI/types/events) and `.adw/plan-summary.md` — Codex checks plan and contract **together against the spec**. If Codex fails as an *author*, the run does not abort: the synthesis works from the Claude draft alone and states that in the summary. The run then pauses for your approval (section 6).

</div>

Phase 3: Build in isolated Lanes with a Gate loop

<div class="inner">

Each Lane gets its own **Git Worktree** under `.adw/runs/<run_id>/trees/<lane>` with its own branch `adw/<run_id>/<lane>`, its own agent session and its own ports (injected into the Gates as `BACKEND_PORT`/`FRONTEND_PORT`). If you marked at least one Gate with `tdd: true`, the initial build starts with the **RED stage**: the Build agent first writes **only the tests** (no production code), and then the orchestrator itself runs exactly the marked Gates. At least one red = RED proven, and the same agent session continues with the implementation, with the (shortened) red Gate output as its task. All green means the tests do not cover the required behavior — that escalates instead of building on a proof nobody has. The **Build agent (Opus 4.8)** implements its workstream strictly against the contract. Then your **Gates** run. Red? The error output goes to the same session as a follow-up task — at most 10 iterations (the RED check consumes none of them); on two identical failures the circuit breaker aborts immediately. Green? **The orchestrator commits** (never the agent) — but only while the tests that proved RED are still in place.

</div>

Phase 4: Integration + E2E (--parallel only)

<div class="inner">

The orchestrator merges both Lane branches onto a fresh integration branch `adw/<run_id>/integration` and runs your E2E command. On red, the **E2E Triage agent** (Sonnet 5, read-only) assigns each failure to a Lane; the fix goes through the regular Lane loop (Gates!, commit) and integration is redone. At most 10 rounds. A merge conflict escalates to you immediately — no agent resolves conflicts.

</div>

Phases 5–6: Two independent reviews + Triage

<div class="inner">

**Codex** reviews the integrated diff (Findings with a fix plan, routed per Lane, until `ok` — same review-loop policy as in phases 1–2: max. 5 rounds, descending severity threshold, findings memory; P2/P3 below the threshold go to `followups.md`). Then the **final reviewer** (Fable 5, strictly read-only) checks the implementation against the spec. The **Triage is code**: Findings of category `scope_gap` ("was never part of the plan") end up as follow-ups in `followups.md` — they trigger *no* rework. `implementation`/`trivial` Findings go into the responsible Lane as a fix cycle (max. 3 cycles per Lane), including a fresh Gate run and re-review. Important: Build agents may **deviate from fix plans with justification** — the reviewer describes the problem, the builder decides the solution. If a fix cycle leaves the worktree untouched and the triggering Findings were **P3 only**, that is not a failure: the finding is deferred to `followups.md` as well and the run continues (idleness on P1/P2 still escalates).

</div>

Phase 7: Push + CI monitoring

<div class="inner">

The feature branch (single-lane: the Lane branch; parallel: the integration branch) is pushed. ADW polls the CI **of this specific push** — GitLab pipelines via glab or GitHub Actions via gh, depending on the target repo's hosting (SHA-bound — an old or foreign pipeline cannot distort the result) until the pipeline and the configured staging job are green. On red, the **Log analyst** (Sonnet 5) reads the job logs and routes Findings into the Lanes — **exactly one** automatic re-entry, after which the run escalates with the logs in the report.

</div>

## 6. The plan approval Gate

After phase 2, the run pauses by default (<span class="exitcode ec2">exit 2</span>) — **before** anything is built and before any significant tokens flow into the implementation:

    $ uv run adw run --repo ~/projekte/shop --issue "Warenkorb-Rabatte"
    Run 3f9a12c4 gestartet (Phase: spec)
    Plan-Approval ausstehend: .adw/runs/3f9a12c4/plan-summary.md und .adw/runs/3f9a12c4/plan.md lesen, dann `adw approve 3f9a12c4`

    $ less ~/projekte/shop/.adw/runs/3f9a12c4/plan-summary.md # the synthesis summary first
    $ less ~/projekte/shop/.adw/runs/3f9a12c4/plan.md      # check the plan
    $ less ~/projekte/shop/.adw/runs/3f9a12c4/contract.yaml # check the contract
    $ uv run adw approve 3f9a12c4 --repo ~/projekte/shop    # continue with phases 3–7

<div class="hint">

Start with the summary: it states in a few lines what is to be built and why, which decisions were made, what was deliberately left out, and where the two drafts disagreed — plan and contract are the detail level behind it.

The approval Gate is the cheapest place to stop wrong directions: a corrected assumption at plan level costs nothing; at code level it costs build, review and fix cycles. For small, low-risk tasks: `--no-approval`.

</div>

**Configurable breakpoints (optional).** Beyond the plan approval you can add up to two holds before the expensive, hard-to-reverse steps — set `breakpoints:` in `.adw/config.yaml`:

    breakpoints:
      - before_integration   # after all build Lanes are green, before integration/review
      - before_push          # after the final review, before push/CI

At an active breakpoint the run pauses exactly like the plan Gate (exit 2), and you continue with `adw approve <run_id> --repo <path>` — the CLI names the waiting breakpoint. A granted breakpoint never holds again (even after crash + `resume`); `adw approve` on a run that is not waiting is a clean error. `--no-approval` (or `--gates none`) skips the breakpoints too. Default (no key): today's behavior, no extra holds.

## 7. Artifacts, reports & run directory

| Path (in the target repo)                            | Contents                                                                                                            | Git status                                                   |
|------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| `.adw/spec.md`, `.adw/plan.md`, `.adw/contract.yaml` | Spec, plan, contract — copied into the Lane Worktrees and **committed along on the feature branch** (traceability). | tracked on the feature branch; the main checkout stays clean |
| `.adw/runs/<run_id>/state.json`                      | Complete run state (phase, Lanes, sessions, counters) — the basis for `resume`.                                     | gitignored (ADW creates the ignore rule itself)              |
| `.adw/runs/<run_id>/spec.md` etc.                    | Archived, reviewed artifact states of this run.                                                                     |                                                              |
| `.adw/runs/<run_id>/spec-summary.md`, `plan-summary.md` | The synthesis summary per authoring phase — your decision basis at the approval Gate (what/why, key decisions, deferred items, which draft contributed what, open questions). |                                                              |
| `.adw/runs/<run_id>/drafts/`                         | The two independent drafts per authoring phase (`spec.claude.md` / `spec.codex.md`, `plan.*`, `contract.*`), plus a `<kind>.codex.FAILED` marker if the Codex draft failed. |                                                              |
| `.adw/runs/<run_id>/escalation.md`                   | Escalation report: state reached, phase, concrete reason.                                                           |                                                              |
| `.adw/runs/<run_id>/followups.md`                    | Follow-up issues from `scope_gap` Findings and deferred P3 Findings (deduplicated).                                                          |                                                              |
| `.adw/runs/<run_id>/trees/<lane>`                    | Lane Worktrees (+ `trees/integration` with `--parallel`).                                                           |                                                              |

## 8. Crash, pause, resume

ADW checkpoints every phase transition **and** every open intermediate result (Gate feedback, review session, counters) atomically into `state.json`. If the process dies — power outage, `Ctrl-C`, network loss — the following holds:

    $ uv run adw status --repo ~/projekte/shop
    3f9a12c4  build              single    Warenkorb-Rabatte

    $ uv run adw resume 3f9a12c4 --repo ~/projekte/shop
    Run 3f9a12c4 wird fortgesetzt (Phase: build)

- Finished Lanes are **not rebuilt** — their result is verified via a tree hash and taken over.
- An open fix (Gate feedback existed, the fix had not run yet) is caught up with **the same agent session**.
- Limits (iterations, rounds, cycles) survive the crash — a restart does not grant the run additional attempts.
- A dry run stays a dry run: the mode is part of the state; a resume never accidentally wires up real agents.

### The working-tree check before every run/resume/approve

Before `adw run`, `adw resume` and `adw approve` enter the phases, ADW checks the **main checkout** for uncommitted changes. This check **never escalates** a run — it only proceeds, self-heals, or refuses:

- **Six ADW-owned artifacts** are written and archived by ADW itself and are **not meant to be edited by hand**: `.adw/issue.md`, `.adw/spec.md`, `.adw/plan.md`, `.adw/contract.yaml`, `.adw/spec-summary.md`, `.adw/plan-summary.md`. If the *only* uncommitted changes are within this exact list (e.g. crash leftovers), ADW resets them itself (tracked → `git checkout`, untracked → delete) and continues.
- **Any other uncommitted change** — a foreign file, or a mix of a foreign file *and* an ADW artifact — makes ADW **refuse** to run with a clear message and a non-zero exit. Nothing is discarded, the run state stays unchanged and resumable; commit or stash your changes, then try again. ADW never resets foreign files.

<div class="warnbox">

Escalated runs (`phase: escalated`) are deliberately **not** resumable — first clarify the cause (read the report), then start a new run.

</div>

## 9. Understanding escalations

ADW gives up **before** it causes damage or burns budget. Every escalation ends the run with exit 1 and writes `escalation.md` with the state reached and the concrete reason:

| Trigger                      | Limit                                   | Typical cause                                                                   |
|------------------------------|-----------------------------------------|---------------------------------------------------------------------------------|
| Gate loop of a Lane          | 10 iterations per task                  | Requirement and Gates contradict each other; flaky tests                        |
| Circuit breaker              | 2× exactly the same failure             | The agent is going in circles — immediate abort instead of exhausting the limit |
| Integration/E2E              | 10 rounds per run                       | Cross-Lane incompatibility, contract gap                                        |
| Merge conflict               | immediately                             | Lanes changed the same files in contradictory ways                              |
| Review loops                 | 10 Codex rounds / 3 fix cycles per Lane | Fundamental problem that reviews cannot "fix away"                              |
| CI                           | 1 re-entry, 45 min budget               | Infrastructure/pipeline problem, environment difference                         |
| Unreadable reviewer response | immediately                             | Agent/Codex did not comply with the Findings JSON schema                        |

## 10. Troubleshooting & FAQ

`Fehler: .adw/config.yaml fehlt …`

<div class="inner">

The target repo has no (valid) workflow config. Copy `examples/config.yaml` and adapt it — see [section 2](#zielrepo). ADW deliberately guesses no defaults.

</div>

Run is at `awaiting_approval` — `resume` "does nothing"

<div class="inner">

That is the approval Gate: `resume` pauses again with exit 2 because the approval is missing. `adw approve <run_id>` grants it and continues. `resume` is meant for crash continuation.

</div>

`--parallel` is rejected

<div class="inner">

`--parallel verlangt eine frontend- UND backend-Lane`: the target repo's config defines only one Lane. Either add the second Lane (with its own Gates) or run without `--parallel`.

</div>

`.adw/spec.md ist getrackt und hat uncommittete Änderungen …`

<div class="inner">

The target repo contains unsaved changes to an earlier ADW artifact. ADW never silently discards your edits: commit or stash first, then start again.

</div>

Escalation "HEAD moved outside the orchestrator" / "Build agent committed on its own"

<div class="inner">

Safety mechanism: only the orchestrator makes commits — after demonstrably green Gates. If foreign commits appear in the Lane Worktree (manually worked in? parallel tooling?), ADW aborts instead of passing on unchecked changes. Clarify the Worktree state, start a new run.

</div>

Escalation "Tests green after the test-only pass — RED not confirmed"

<div class="inner">

The Lane has a `tdd: true` Gate, but the marked Gates were green right after the test-only pass. Then the new tests prove nothing: either they do not cover the required behavior, or that behavior already exists. ADW does not loop on this — it hands over to you. Sharpen the plan/contract (or drop the requirement), then start a new run. Related escalations from the same stage: the test-only pass left the Worktree untouched (no tests = no proof), it deleted files (red Gates through deletions are no proof), or the implementation removed the tests that proved RED.

</div>

Pipeline red "without usable job logs"

<div class="inner">

The pipeline was `canceled`/`skipped` or failed without any failed jobs (e.g. a YAML error in the CI config). Without logs, the Log analyst would only be guessing — so this goes directly to you.

</div>

Why do I see `adw_dry_run_*.md` files on dry-run branches?

<div class="inner">

Those are the demo artifacts of the mock build agents. Dry-run branches (`adw/<run_id>/…`) are purely local and are never pushed — they can be deleted safely.

</div>

What does a run cost?

<div class="inner">

Dry run: nothing (0 tokens). Real run: **no API costs** — everything runs on your Claude plan (see [Billing](#abrechnung)), distributed model-economically: Fable 5 only for spec/plan/final review, Opus 4.8 builds, Sonnet 5 triages, Codex runs on your Codex subscription. The limits (section 9) structurally cap the worst case.

</div>

`Agent-Lauf abgebrochen (z. B. Plan-Limit erschöpft) …`

<div class="inner">

The subscription window is empty or the Claude CLI could not respond. No action needed on the run itself: it sits at the last checkpoint (phase unchanged, no `escalation.md`). After the limit resets, simply `adw resume <run_id>` — the run continues with the same agent sessions.

</div>

## 11. Glossary

| Term                | Meaning                                                                                                                                       |
|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| **Lane**            | A workstream (backend/frontend) with its own Git Worktree, its own branch, its own agent session and its own ports.                           |
| **Draft stage**     | Phases 1–2: Claude agent and Codex write their own draft of the artifact in parallel, into `.adw/runs/<run_id>/drafts/`.                       |
| **Synthesis**       | The agent that merges both drafts into ONE best-of artifact and writes the summary for the approval Gate.                                     |
| **Gate**            | A configured check command (linter, tests, …) with a hard timeout. All Gates green = condition for every commit.                              |
| **RED stage**       | Initial build of a Lane with a `tdd: true` Gate: the agent is told to write tests only, then the orchestrator proves the marked Gates are red — before the implementation run.                 |
| **Contract**        | `.adw/contract.yaml` — the agreed interface (OpenAPI/types/events) against which both Lanes build independently.                              |
| **Finding**         | Structured review result (JSON): severity P1–P3, Lane, file, problem, fix recommendation, possibly category.                                  |
| **scope_gap**       | Finding category "missing, but was never in the plan" → becomes a follow-up issue, no auto-rework.                                            |
| **Circuit breaker** | Abort rule: if a fix iteration produces exactly the same failure picture as before, it escalates immediately instead of exhausting the limit. |
| **Escalation**      | Controlled handover to the human: exit ≠ 0 + `escalation.md` with state and reason.                                                           |
| **Session resume**  | Fix tasks go to the *existing* agent session (full context) instead of a fresh agent.                                                         |
| **Run ID**          | 8-character hex ID of a run; all artifacts live under `.adw/runs/<run_id>/`.                                                                  |

[↑ back to top](#tldr)

</div>

ADW User Handbook · generated on 2026-07-15 · Source: repo `agentic-developer-workflow` (README.md, docs/SPEC.md, docs/PLAN.md, as of main `9b89dd6`) · Technical details: `docs/spec/ADW-TECHNISCHE-SPEC.html`
