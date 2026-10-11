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

## Documentation

| Document | For | Content |
| --- | --- | --- |
| [`docs/ADW-HANDBOOK.md`](docs/ADW-HANDBOOK.md) | Users (developers) | Setup, config, CLI, GUI, run artifacts, troubleshooting |
| [`docs/ADW-WORKFLOW.md`](docs/ADW-WORKFLOW.md) | Users + contributors | Deep dive: the seven phases, loops, limits, resume |
| [`docs/ADW-SPEC.md`](docs/ADW-SPEC.md) | Contributors | Philosophy, architecture, invariants, extension points |
| [`docs/SPEC.md`](docs/SPEC.md) / [`docs/PLAN.md`](docs/PLAN.md) | Contributors | Original target spec (DoD) and implementation plan |

Exports with interactive diagrams (HTML) and EMF diagrams (DOCX): [`docs/export/`](docs/export/) —
rebuilt with `python scripts/build_docs.py` (needs pandoc + Inkscape).

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
uv run adw run --repo /path/to/repo --issue "Demo feature" --dry-run --gates none
uv run adw run --repo /path/to/repo --issue "Demo feature" --dry-run --parallel --gates none

# Real run (tokens!): issue text directly, GitLab issue (glab) or GitHub issue (gh)
uv run adw run --repo /path/to/repo --issue "Bug: login aborts when ..."
uv run adw run --repo /path/to/repo --gitlab-issue 42 --parallel
uv run adw run --repo /path/to/repo --github-issue 42 --parallel
```

### CLI

```
adw run     --repo PATH (--issue TEXT | --gitlab-issue ID | --github-issue NR)
            [--parallel] [--dry-run] [--gates none|spec|plan|both]
            [--base-branch NAME]   # --no-approval/--spec-approval: deprecated
adw resume  RUN_ID [--repo PATH] [--base-branch NAME]   # after a crash / plan limit
adw approve RUN_ID [--repo PATH] [--base-branch NAME]   # grant a pending approval + continue
adw status  [RUN_ID] [--repo PATH]                      # show runs + phase
adw gui     [--repo PATH]... [--host 127.0.0.1] [--port 8765] [--open]   # read-only Run Inspector
adw runs list  [--repo PATH]
adw runs prune [--repo PATH] [--keep N] [--older-than DAYS] [--gzip]
```

Exit codes: `0` done · `2` paused at an approval gate or breakpoint · `1` escalation/error.

**Approval gates:** `--gates` picks where the run pauses (exit 2): `plan`
(default, after plan + contract), `spec`, `both` or `none`. Read the summary
under `.adw/runs/<run_id>/` (`plan-summary.md`, `plan.md`, `contract.yaml`),
then `adw approve <run_id>`. `--no-approval`/`--spec-approval` are deprecated: they
still work but print a hint to use `--gates`. Full reference: [`docs/ADW-HANDBOOK.md`](docs/ADW-HANDBOOK.md).

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
| `ci.provider` | optional | `gitlab` or `github`; if unset, auto-detection from the origin URL (unknown host: a real run is refused in the preflight — set the key for self-hosted forges) |
| `codex.timeout` | optional (900) | Time limit in seconds per `codex exec` call |
| `trace.enabled` | optional (true) | Write the event log `events.jsonl` (feeds the GUI) |
| `trace.keep_runs` | optional (20) | Auto-prune after a successful run keeps the newest N runs; `0` = never |
| `breakpoints[]` | optional | Extra holds: `before_integration`, `before_push` |

A missing or broken config aborts immediately with a clear message (fail fast).
Allowed Lane names are `backend` and `frontend`; `--parallel` requires both.

## Architecture in 60 seconds

```
adw/
  cli.py        typer entry point: run/resume/approve/status/gui/runs
  preflight.py  checks before run/resume/approve: forge, clean checkout, models
  dry_run.py    dry-run fixtures and mock wiring (0 tokens, no network)
  phases.py     phases 4–7 + facade for all seven phase entry points
  context.py    RunContext, escalation, spans, escalating git wrapper
  authoring.py  phases 1–2: dual drafts, synthesis, reviewed authoring loop
  lanes.py      phase 3 + fix dispatch: lane worktrees, RED stage, gate loop
  review_policy.py  severity floor, findings memory, follow-ups
  git.py        the one hardened git invocation
  agents.py     agent registry (Fable 5.1 / Opus 5.5 / Sonnet 5.5) + SDK runner
  codex.py      Codex CLI (gpt-6-astra) as a read-only subprocess, strict findings parsing
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
  models.py     model freshness check: refuses runs while newer models exist
  env.py        env whitelist for agents, gates and subprocesses
  events.py     append-only event log (events.jsonl) per run
  snapshots.py  artifact snapshots per phase for the Run Inspector
  retention.py  `adw runs list|prune`, automatic pruning
  gui/          read-only Run Inspector (FastAPI, optional extra `gui`): app.py routes +
                trace, fmt, runs, serialize, timeline, artifacts, diffs, projections
```

- **Lanes:** one dedicated git worktree per workstream (`.adw/runs/<id>/trees/<lane>`),
  its own SDK session, its own ports. Commits are made exclusively by the orchestrator.
- **Limits:** 10 Gate iterations per task, 10 E2E rounds, 5 Codex review rounds, 3 fix cycles,
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
  `adw approve <run_id>` — or start future runs with `--gates none`.
- **`scope_gap` Findings:** end up in `.adw/runs/<run_id>/followups.md` as
  follow-up issues (no auto-restart) — the run continues normally.
- **CI red despite fix:** exactly one automatic log-analyst re-entry is
  provided; after that the run escalates with the job logs in the report.
- **Dry run to verify the config:** `--dry-run` drives the complete
  control flow including a simulated Gate fail and (with `--parallel`)
  the E2E triage path — without tokens, without push, without GitLab.

## Development

```bash
uv run pytest          # complete suite (~1,280 tests, mocks-only, real git)
uv run ruff check .    # lint
```

## License

MIT — see [LICENSE](LICENSE).
