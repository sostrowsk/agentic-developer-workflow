# Agentic Developer Workflow (ADW)

**English** | [Deutsch](README.de.md)

A Python orchestrator that takes an issue fully automatically through **seven phases**:

> Spec → Plan + Contract → Build (Lanes) → Integration/E2E → Codex Review → final Review + Triage → Push/CI/Staging

Guiding principle: **Control flow is code, not prompt.** Loops, Gates, merges, dispatch,
triage, limits, and CI polling are deterministic Python code (0 tokens).
Agents (Claude Agent SDK + Codex CLI) run only where judgment is
needed — and reviewers never fix: every fix goes through the build agents
and again through all Gates.

**Billing:** ADW talks to Claude exclusively via the **Claude Code CLI**
(the Agent SDK spawns it headless) with stored login — usage runs
against the Claude plan, never token-by-token against the API (API key env variables
are actively blanked). An exhausted plan limit stops the run at the checkpoint;
`adw resume` continues after the reset.

References: [`docs/SPEC.md`](docs/SPEC.md) (target behavior, interfaces, DoD)
and [`docs/PLAN.md`](docs/PLAN.md) (implementation plan).

**Claude skill:** [agentic-developer-workflow-skill](https://github.com/sostrowsk/agentic-developer-workflow-skill)
— packages the operation of this orchestrator as an installable Claude skill
(preflight check, workflow guide, config template, troubleshooting).

## Quickstart

```bash
# Dependencies (uv, Python >= 3.12)
uv sync

# Prepare the target repo: create .adw/config.yaml (see examples/config.yaml)
mkdir -p /path/to/repo/.adw
cp examples/config.yaml /path/to/repo/.adw/config.yaml   # and adjust

# Dry run: complete control flow with mocks — 0 tokens, no network
uv run adw run --repo /path/to/repo --issue "Demo feature" --dry-run --no-approval
uv run adw run --repo /path/to/repo --issue "Demo feature" --dry-run --parallel --no-approval

# Real run (tokens!): issue text directly, GitLab issue (glab) or GitHub issue (gh)
uv run adw run --repo /path/to/repo --issue "Bug: login aborts when ..."
uv run adw run --repo /path/to/repo --gitlab-issue 42 --parallel
uv run adw run --repo /path/to/repo --github-issue 42 --parallel
```

### CLI

```
adw run --repo <path> (--issue "Text" | --gitlab-issue <id> | --github-issue <nr>)
        [--parallel] [--dry-run] [--no-approval] [--base-branch <name>]
adw resume <run_id> [--repo <path>]      # after a crash; on approval pause → approve
adw approve <run_id> [--repo <path>]     # grant plan approval + continue
adw status [<run_id>] [--repo <path>]    # show runs + phase
```

Exit codes: `0` done · `2` awaiting_approval (plan approval pause) · `1` escalation/error.

**Plan approval gate:** After phase 2 the run pauses (exit 2). Plan and
contract are located at `.adw/runs/<run_id>/plan.md` and `contract.yaml` —
read them, then `adw approve <run_id>`. With `--no-approval` the pause is skipped.

## Config reference (`.adw/config.yaml` in the target repo)

Complete example: [`examples/config.yaml`](examples/config.yaml).

| Key | Required | Meaning |
| --- | --- | --- |
| `base_branch` | yes | Branch that Lanes fork from and that diffs run against |
| `lanes.<name>.gates[]` | yes (>= 1 Lane) | Gate list per Lane: `name`, `cmd`, `timeout` (seconds). Order = execution order, fail fast |
| `…gates[].tdd` | optional (false) | Marks a Gate as a RED proof: in the initial build at least one marked Gate has to be red after the test-only agent pass, before the implementation run |
| `e2e.cmd` / `e2e.timeout` | optional | E2E command (Playwright or similar) — only relevant with `--parallel` |
| `ci.poll_interval` | optional (60) | Seconds between pipeline polls |
| `ci.timeout` | optional (2700) | Total budget for waiting on CI |
| `ci.staging_job` | optional | Job name that must additionally be green |
| `ci.provider` | optional | `gitlab` or `github`; if unset, auto-detection from the origin URL (fail fast on unknown host) |

A missing or broken config aborts immediately with a clear message (fail fast).
`--parallel` requires a `backend` **and** a `frontend` Lane.

## Architecture in 60 seconds

```
adw/
  cli.py        typer entry point: run/resume/approve/status, dry-run wiring
  phases.py     the 7 phases over a RunContext — loops, limits, dispatch
  agents.py     agent registry (Fable 5 / Opus 4.8 / Sonnet 5) + SDK runner
  codex.py      Codex CLI as a read-only subprocess, strict findings parsing
  findings.py   Findings schema (pydantic) + strict parser contract
  config.py     .adw/config.yaml loader (fail fast)
  state.py      RunState: atomically persisted, basis for `adw resume`
  gates.py      Gate runner: subprocess with real timeout, env whitelist
  worktrees.py  Lane worktrees + deterministic ports
  triage.py     triage rules, iteration limits, circuit breaker
  ci.py         GitLab polling (glab) until staging is green, log retrieval
  github.py     GitHub Actions polling (gh) — same interface as ci.py
  forge.py      GitLab-or-GitHub detection (origin URL, ci.provider override)
  mock.py       scriptable mock runners for --dry-run and tests
```

- **Lanes:** one dedicated git worktree per workstream (`.adw/runs/<id>/trees/<lane>`),
  its own SDK session, its own ports. Commits are made exclusively by the orchestrator.
- **Limits:** 10 Gate iterations per task, 10 E2E/review rounds, 3 fix cycles,
  1 CI re-entry — plus circuit breaker (identical errors twice → immediate stop).
- **Resume:** every transition and every open feedback is checkpointed in
  `.adw/runs/<run_id>/state.json`; `adw resume` continues exactly there.
- **Safety:** agents work with tool whitelists and path rules; subprocesses
  run with an env whitelist (no secrets); spec/plan/contract and the config are
  effectively immutable for agents (the orchestrator restores them).

## Troubleshooting

- **Run aborts (exit 1):** read `.adw/runs/<run_id>/escalation.md` — it
  contains the state reached, the phase, and the concrete reason (Gate output,
  merge conflict, limit, circuit breaker). After manual clarification, start a new
  run; escalated runs are deliberately not resumable.
- **Run stuck in `awaiting_approval`:** `adw status`, then
  `adw approve <run_id>` — or in the future `--no-approval`.
- **`scope_gap` Findings:** end up in `.adw/runs/<run_id>/followups.md` as
  follow-up issues (no auto-restart) — the run continues normally.
- **CI red despite fix:** exactly one automatic log-analyst re-entry is
  provided; after that the run escalates with the job logs in the report.
- **Dry run to verify the config:** `--dry-run` drives the complete
  control flow including a simulated Gate fail and (with `--parallel`)
  the E2E triage path — without tokens, without push, without GitLab.

## Development

```bash
uv run pytest          # complete suite (~330 tests, mocks-only, real git)
uv run ruff check .    # lint
uv run ruff format .   # formatting
```

## License

MIT — see [LICENSE](LICENSE).
