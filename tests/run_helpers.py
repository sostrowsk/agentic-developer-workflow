"""Shared helpers for run-level tests (CLI dry runs, event logs) — not a test module."""

import json

from typer.testing import CliRunner

from adw.cli import app
from adw.config import AdwConfig
from adw.findings import ReviewResult
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import (
    RunContext,
    run_build_phase,
    run_codex_review_phase,
    run_spec_and_plan,
)
from adw.state import RunState

runner = CliRunner()


RUN_ID = "run01a2"


def read_events(repo, run_id: str = RUN_ID) -> list[dict]:
    """Every complete line of the run's events.jsonl (empty list if absent)."""
    path = repo.resolve() / ".adw" / "runs" / run_id / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def of_type(records: list[dict], type_: str, kind: str | None = None) -> list[dict]:
    return [
        r for r in records if r["type"] == type_ and (kind is None or r["kind"] == kind)
    ]


def cli_run(repo, *extra):
    return runner.invoke(
        app, ["run", "--repo", str(repo), "--issue", "Instrumentierung", "--dry-run", *extra]
    )


def read_run_events(repo) -> tuple[RunState, list[dict]]:
    state = RunState.find_latest(repo)
    return state, read_events(repo, state.run_id)


TDD_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pytest, cmd: "test -f .adw-dry-run-fixed", timeout: 10, tdd: true}
ci:
  provider: gitlab
  staging_job: deploy-staging
"""


OK = ReviewResult(verdict="ok", findings=[])


def script_authoring(agents: MockAgentRunner) -> None:
    """Draft authors and synthesis agents of both authoring phases."""
    agents.script_files("spec_agent", {".adw/spec.md": "# Spec-Entwurf\n"})
    agents.script_files(
        "plan_agent",
        {".adw/plan.md": "# Plan-Entwurf\n", ".adw/contract.yaml": "openapi: 3.1.0\n"},
    )
    agents.script_files(
        "spec_synthesis", {".adw/spec.md": "# Spec\n", ".adw/spec-summary.md": "# Spec-Summary\n"}
    )
    agents.script_files(
        "plan_synthesis",
        {
            ".adw/plan.md": "# Plan\n",
            ".adw/contract.yaml": "openapi: 3.1.0\ninfo:\n  title: Synthese\n",
            ".adw/plan-summary.md": "# Plan-Summary\n",
        },
    )
    agents.script("spec_agent", "Spec-Entwurf")
    agents.script("plan_agent", "Plan-Entwurf")
    agents.script("spec_synthesis", "Spec")
    agents.script("plan_synthesis", "Plan")


def script_draft_artifacts(codex: MockCodexRunner) -> None:
    codex.script_artifacts("spec", {"spec.md": "# Codex-Spec-Entwurf\n"})
    codex.script_artifacts(
        "plan", {"plan.md": "# Codex-Plan-Entwurf\n", "contract.yaml": "openapi: 3.1.0\n"}
    )


def prepare_final_review(target_repo):
    agents = MockAgentRunner()
    script_authoring(agents)
    agents.script_files("build_agent", {"src.py": "pass\n"})
    agents.script("build_agent", *["gebaut"] * 8)
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
    codex.script(*[OK] * 8)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=RunState.new(issue="DoD4", parallel=False),
        agents=agents,
        codex=codex,
        skip_approval=True,
    )
    run_spec_and_plan(ctx)
    run_build_phase(ctx)
    run_codex_review_phase(ctx)
    assert ctx.state.phase == "final_review"
    return ctx


def review_json(findings):
    return json.dumps({"verdict": "needs_fixes" if findings else "ok", "findings": findings})


def finding(issue, category, lane="backend"):
    return {
        "severity": "P2",
        "lane": lane,
        "file": "src.py",
        "issue": issue,
        "remediation_plan": ["fixen"],
        "category": category,
    }
