"""End-to-end dry-run acceptance tests — SPEC §8, DoD criteria 1–5.

Everything via the real CLI or the phase functions with mocks: 0 tokens,
no network, real git in tmp_path. Finer-grained behavior tests live in the
module test files; here each DoD criterion gets its acceptance path.
"""

import json

import pytest
from typer.testing import CliRunner

from adw.cli import app
from adw.config import AdwConfig
from adw.findings import ReviewResult
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import (
    EscalationError,
    RunContext,
    run_build_phase,
    run_codex_review_phase,
    run_final_review_phase,
    run_spec_and_plan,
)
from adw.state import RunState
from tests.conftest import git, write_config

runner = CliRunner()

PARALLEL_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
  frontend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
e2e:
  cmd: "true"
  timeout: 60
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


def cli_run(repo, *extra):
    return runner.invoke(
        app, ["run", "--repo", str(repo), "--issue", "Akzeptanz-Demo", "--dry-run", *extra]
    )


# --- DoD 1: kompletter Dry-Run, single und parallel ---------------------------


def test_dod1_single_lane_dry_run_passes_all_seven_phases(target_repo):
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    # Artefakte der Phasen 1–2 sind archiviert, der Haupt-Checkout bleibt sauber.
    run_dir = state.run_dir(target_repo)
    for artifact in ("spec.md", "plan.md", "contract.yaml"):
        assert (run_dir / artifact).is_file()
    assert git(target_repo, "status", "--porcelain") == ""
    # Phase 3 hat gebaut und committet (inkl. simuliertem Gate-Fail → Fix).
    lane = state.lanes["backend"]
    assert lane.completed and lane.gates_passed
    assert lane.gate_iterations == 2
    worktree = target_repo / ".adw" / "runs" / state.run_id / "trees" / "backend"
    assert git(worktree, "status", "--porcelain") == ""


def test_dod1_parallel_dry_run_covers_both_lanes_and_e2e_triage(target_repo):
    write_config(target_repo, PARALLEL_CONFIG)
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "parallel config")
    result = cli_run(target_repo, "--no-approval", "--parallel")
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    assert set(state.lanes) == {"backend", "frontend"}
    assert all(lane.completed for lane in state.lanes.values())
    assert state.integration_rounds == 1  # E2E-Rot → Triage → Lane-Fix → Grün
    integration = target_repo / ".adw" / "runs" / state.run_id / "trees" / "integration"
    assert (integration / "adw_dry_run_backend.md").is_file()
    assert (integration / "adw_dry_run_frontend.md").is_file()


# --- DoD 2: Gate-Fail → Fix an dieselbe Session; Limits → Eskalation ----------


def test_dod2_gate_fail_fix_goes_to_same_session(target_repo):
    """The simulated Gate fail of the dry run creates a fix task that reuses
    the Lane's session (SDK resume instead of rebuilding context)."""
    agents = MockAgentRunner()
    script_authoring(agents)
    agents.script("build_agent", "Versuch 1", "Fix")

    def writes(cwd):
        first = not (cwd / "demo.txt").exists()
        files = {"demo.txt": "v\n"}
        if not first:
            files["gate-fixed"] = "ok\n"
        return files

    agents.file_writes["build_agent"] = writes
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: marker, cmd: "sh -c 'test -f gate-fixed || { echo ROT; exit 1; }'", timeout: 10}
""",
    )
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
    codex.script(OK, OK)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=RunState.new(issue="DoD2", parallel=False),
        agents=agents,
        codex=codex,
        skip_approval=True,
    )
    run_spec_and_plan(ctx)
    run_build_phase(ctx)
    build_calls = [c for c in agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2
    assert build_calls[0].resume is None
    assert build_calls[1].resume is not None  # dieselbe Session, kein Neuaufbau
    assert "ROT" in build_calls[1].task  # Gate-Output ist der Fix-Task


def test_dod2_hopeless_gate_escalates_with_report_and_exit_1(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: immer-rot, cmd: "sh -c 'echo KAPUTT; exit 1'", timeout: 10}
""",
    )
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "rotes gate")
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 1
    state = RunState.find_latest(target_repo)
    assert state.phase == "escalated"
    report = state.run_dir(target_repo) / "escalation.md"
    assert report.is_file()
    assert "KAPUTT" in report.read_text() or "Circuit" in report.read_text()


# --- DoD 3: Plan-Approval-Gate ------------------------------------------------


def test_dod3_run_pauses_and_approve_continues(target_repo):
    paused = cli_run(target_repo)
    assert paused.exit_code == 2, paused.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_approval"
    # Plan + Kontrakt liegen zum Lesen bereit, bevor irgendetwas gebaut wird.
    assert (state.run_dir(target_repo) / "plan.md").is_file()
    assert (state.run_dir(target_repo) / "contract.yaml").is_file()
    assert state.lanes == {}
    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 0, approved.output
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_dod3_no_approval_skips_the_pause(target_repo):
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0
    assert RunState.find_latest(target_repo).phase == "done"


# --- DoD 4: Triage nach dem finalen Review ------------------------------------


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


def test_dod4_scope_gap_becomes_followup_not_fix(target_repo):
    ctx = prepare_final_review(target_repo)
    ctx.agents.script("final_reviewer", review_json([finding("Export fehlt", "scope_gap")]))
    builds_before = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"  # Report statt Auto-Restart
    followups = ctx.run_dir / "followups.md"
    assert followups.is_file() and "Export fehlt" in followups.read_text()
    assert len([c for c in ctx.agents.calls if c.agent == "build_agent"]) == builds_before


def test_dod4_implementation_finding_routes_into_the_lane(target_repo):
    ctx = prepare_final_review(target_repo)
    ctx.agents.file_writes["build_agent"] = {"src.py": "validiert = True\n"}
    ctx.agents.script(
        "final_reviewer",
        review_json([finding("Validierung fehlt", "implementation")]),
        review_json([]),
    )
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    fix_call = [c for c in ctx.agents.calls if c.agent == "build_agent"][-1]
    assert "Validierung fehlt" in fix_call.task
    assert fix_call.cwd.name == "backend"
    assert ctx.state.lanes["backend"].fix_cycles == 1


def test_dod4_three_fix_cycles_escalate(target_repo):
    ctx = prepare_final_review(target_repo)

    def writes(cwd):
        return {f"fix{len(ctx.agents.calls)}.py": "pass\n"}

    ctx.agents.file_writes["build_agent"] = writes
    ctx.agents.script(
        "final_reviewer",
        *[review_json([finding(f"Problem {i}", "implementation")]) for i in range(4)],
    )
    with pytest.raises(EscalationError, match="Fix-Zyklen|[Ll]imit"):
        run_final_review_phase(ctx)
    assert ctx.state.lanes["backend"].fix_cycles == 3
    assert (ctx.run_dir / "escalation.md").is_file()


# --- DoD 7: Dual-Authoring im Dry-Run -----------------------------------------

DRAFT_PAIRS = (
    ("spec.claude.md", "spec.codex.md"),
    ("plan.claude.md", "plan.codex.md"),
    ("contract.claude.yaml", "contract.codex.yaml"),
)


def test_dod7_dry_run_produces_both_drafts_and_both_summaries(target_repo):
    """The dry run walks the full dual-authoring flow: two drafts per authoring
    phase plus the summary the human reads at the gate — 0 tokens, exit 0."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    run_dir = RunState.find_latest(target_repo).run_dir(target_repo)
    drafts = run_dir / "drafts"
    for claude_name, codex_name in DRAFT_PAIRS:
        for name in (claude_name, codex_name):
            assert (drafts / name).read_text().strip(), f"{name} fehlt oder ist leer"
    # Kein degradierter Codex-Entwurf: der Dry-Run fährt den vollen Pfad, sonst
    # bliebe die zweite Entwurfsquelle im Smoke-Test ungetestet.
    assert list(drafts.glob("*.FAILED")) == []
    for summary in ("spec-summary.md", "plan-summary.md"):
        assert (run_dir / summary).read_text().strip(), f"{summary} fehlt oder ist leer"


def test_dod7_dry_run_drafts_are_distinguishable_per_author(target_repo):
    """Identical fixtures would hide it if both draft files came from the same
    author — the dry run must show two independent sources."""
    cli_run(target_repo, "--no-approval")
    drafts = RunState.find_latest(target_repo).run_dir(target_repo) / "drafts"
    for claude_name, codex_name in DRAFT_PAIRS:
        assert (drafts / claude_name).read_text() != (drafts / codex_name).read_text()


def test_dod7_summaries_stay_out_of_the_lane_worktree(target_repo):
    """SPEC acceptance criterion 6: the summaries are gate material, no Lane
    builds against them — only spec/plan/contract are seeded."""
    cli_run(target_repo, "--no-approval")
    state = RunState.find_latest(target_repo)
    lane_adw = target_repo / ".adw" / "runs" / state.run_id / "trees" / "backend" / ".adw"
    assert (lane_adw / "spec.md").is_file()
    assert not (lane_adw / "spec-summary.md").exists()
    assert not (lane_adw / "plan-summary.md").exists()
    assert not (lane_adw / "drafts").exists()


# --- DoD 5: Resume setzt in derselben Phase fort -------------------------------


def test_dod5_state_round_trip_is_lossless(target_repo):
    cli_run(target_repo, "--no-approval")
    state = RunState.find_latest(target_repo)
    reloaded = RunState.load(target_repo, state.run_id)
    assert reloaded.model_dump() == state.model_dump()


def test_dod5_resume_continues_in_the_same_phase(target_repo, monkeypatch):
    """Crash in the middle of the build phase (mock responses exhausted) →
    `adw resume` continues exactly there and brings the run to completion."""
    import adw.cli as cli_mod

    real = cli_mod._dry_run_runners

    def crashing():
        agents, codex = real()
        agents.scripts["build_agent"].clear()
        agents.script("build_agent", "nur ein Lauf")  # zweiter Lauf crasht
        return agents, codex

    with monkeypatch.context() as m:
        m.setattr(cli_mod, "_dry_run_runners", crashing)
        crashed = cli_run(target_repo, "--no-approval")
        assert crashed.exit_code != 0
    state = RunState.find_latest(target_repo)
    assert state.phase == "build"  # mitten in Phase 3 gestorben
    assert state.lanes["backend"].pending_task  # Gate-Feedback checkpointed
    resumed = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 0, resumed.output
    final = RunState.load(target_repo, state.run_id)
    assert final.phase == "done"
    assert final.lanes["backend"].completed
