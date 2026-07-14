"""Tests der Phasen-Orchestrierung — komplett mit Mocks, 0 Tokens, echtes git."""

import pytest

from adw.config import AdwConfig
from adw.findings import Finding, ReviewResult
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import (
    AwaitingApproval,
    EscalationError,
    RunContext,
    run_build_phase,
    run_spec_and_plan,
)
from adw.state import RunState
from tests.conftest import write_config

OK = ReviewResult(verdict="ok", findings=[])


def needs_fixes(issue: str = "unklar", lane: str = "backend") -> ReviewResult:
    return ReviewResult(
        verdict="needs_fixes",
        findings=[
            Finding(
                severity="P2",
                lane=lane,
                file=".adw/spec.md",
                issue=issue,
                remediation_plan=["nachschärfen"],
            )
        ],
    )


@pytest.fixture
def ctx(target_repo):
    agents = MockAgentRunner()
    agents.script_files("spec_agent", {".adw/spec.md": "# Spec\nZiel: Demo\n"})
    agents.script_files(
        "plan_agent",
        {
            ".adw/plan.md": "# Plan\nWorkstream backend\n",
            ".adw/contract.yaml": "openapi: 3.1.0\n",
        },
    )
    codex = MockCodexRunner()
    state = RunState.new(issue="ISSUE-1: Demo-Feature", parallel=False)
    return RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=agents,
        codex=codex,
        skip_approval=False,
    )


def test_spec_and_plan_happy_path_pauses_for_approval(ctx):
    ctx.agents.script("spec_agent", "Spec geschrieben")
    ctx.agents.script("plan_agent", "Plan geschrieben")
    ctx.codex.script(OK, OK)  # Spec-Review ok, Plan-Review ok
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "awaiting_approval"
    assert (ctx.run_dir / "spec.md").is_file()
    assert (ctx.run_dir / "plan.md").is_file()
    assert (ctx.run_dir / "contract.yaml").is_file()


def test_spec_findings_go_back_to_same_session(ctx):
    ctx.agents.script("spec_agent", "v1", "v2 nachgeschärft")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(needs_fixes("Akzeptanzkriterien fehlen"), OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    spec_calls = [c for c in ctx.agents.calls if c.agent == "spec_agent"]
    assert len(spec_calls) == 2
    assert spec_calls[0].resume is None
    assert spec_calls[1].resume == "mock-session-spec_agent-1"
    assert "Akzeptanzkriterien fehlen" in spec_calls[1].task


def test_no_approval_flag_skips_the_pause(ctx):
    ctx.skip_approval = True
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    run_spec_and_plan(ctx)  # kein AwaitingApproval
    assert ctx.state.phase == "build"


def test_identical_spec_findings_twice_escalate(ctx):
    ctx.agents.script("spec_agent", "v1", "v2", "v3")
    ctx.codex.script(
        needs_fixes("immer dasselbe"),
        needs_fixes("immer dasselbe"),
    )
    with pytest.raises(EscalationError):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "escalated"
    assert (ctx.run_dir / "escalation.md").is_file()


def test_stale_artifacts_from_previous_run_are_cleared(ctx):
    """Regression: alte .adw/spec.md darf einen untätigen Agenten nicht adeln."""
    (ctx.repo / ".adw" / "spec.md").write_text("# ALTE Spec vom letzten Run\n")
    ctx.agents.file_writes.pop("spec_agent")  # Agent schreibt diesmal nichts
    ctx.agents.script("spec_agent", "behauptet fertig zu sein")
    with pytest.raises(EscalationError, match="spec.md"):
        run_spec_and_plan(ctx)


def test_tracked_artifacts_from_merged_adw_run_do_not_block_the_next_run(ctx):
    """Regression: nach Merge eines ADW-Branches sind Artefakte getrackt — der
    nächste Run muss trotzdem laufen und den Checkout sauber hinterlassen."""
    from tests.conftest import git

    spec_path = ctx.repo / ".adw" / "spec.md"
    spec_path.write_text("# Spec des VORHERIGEN Runs (gemergt)\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Spec/Plan/Kontrakt")
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    # Neue Spec ist archiviert, der Checkout trägt wieder die gemergte Version:
    assert (ctx.run_dir / "spec.md").read_text() == "# Spec\nZiel: Demo\n"
    assert spec_path.read_text() == "# Spec des VORHERIGEN Runs (gemergt)\n"
    assert git(ctx.repo, "status", "--porcelain") == ""


def test_plan_agent_cannot_silently_rewrite_reviewed_spec(ctx):
    """Regression: Spec nach Spec-Review ist fix — der Plan-Agent plant nur."""
    plan_files = dict(ctx.agents.file_writes["plan_agent"])
    plan_files[".adw/spec.md"] = "# Umgeschriebene Spec\n"
    ctx.agents.script_files("plan_agent", plan_files)
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan (und Spec umgeschrieben)")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    archived = (ctx.run_dir / "spec.md").read_text()
    assert "Umgeschrieben" not in archived
    assert archived == "# Spec\nZiel: Demo\n"


def test_run_artifacts_are_gitignored_from_the_start(ctx):
    from tests.conftest import git

    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    status = git(ctx.repo, "status", "--porcelain")
    assert ".adw/runs" not in status


def test_authoring_agents_cannot_mutate_target_config(ctx):
    """Regression: Write(.adw/**) erlaubt technisch auch config.yaml — wird restauriert."""
    original_config = (ctx.repo / ".adw" / "config.yaml").read_text()
    spec_files = dict(ctx.agents.file_writes["spec_agent"])
    spec_files[".adw/config.yaml"] = "base_branch: main  # sabotiert\n"
    ctx.agents.script_files("spec_agent", spec_files)
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert (ctx.repo / ".adw" / "config.yaml").read_text() == original_config


def test_config_is_restored_even_when_the_run_escalates(ctx):
    """Regression: Restore muss exception-sicher sein — auch bei Eskalation."""
    original_config = (ctx.repo / ".adw" / "config.yaml").read_text()
    spec_files = dict(ctx.agents.file_writes["spec_agent"])
    spec_files[".adw/config.yaml"] = "base_branch: main  # sabotiert\n"
    ctx.agents.script_files("spec_agent", spec_files)
    ctx.agents.script("spec_agent", "v1", "v2", "v3")
    ctx.codex.script(needs_fixes("dasselbe"), needs_fixes("dasselbe"))
    with pytest.raises(EscalationError):
        run_spec_and_plan(ctx)
    assert (ctx.repo / ".adw" / "config.yaml").read_text() == original_config


def test_plan_review_always_sees_the_reviewed_spec(ctx):
    """Regression: Codex darf Plan+Kontrakt nie gegen eine umgeschriebene Spec reviewen."""
    seen_specs = []

    class SpyCodex(MockCodexRunner):
        def review(self, kind, content_refs, cwd):
            seen_specs.append((kind, (cwd / ".adw" / "spec.md").read_text()))
            return super().review(kind, content_refs, cwd)

    ctx.codex = SpyCodex()
    plan_files = dict(ctx.agents.file_writes["plan_agent"])
    plan_files[".adw/spec.md"] = "# Umgeschriebene Spec\n"
    ctx.agents.script_files("plan_agent", plan_files)
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    plan_reviews = [spec for kind, spec in seen_specs if kind == "plan"]
    assert plan_reviews and all("Umgeschrieben" not in spec for spec in plan_reviews)


def test_missing_spec_artifact_escalates(ctx):
    ctx.agents.file_writes.pop("spec_agent")  # Agent "vergisst" die Datei
    ctx.agents.script("spec_agent", "behauptet fertig zu sein")
    ctx.codex.script(OK)
    with pytest.raises(EscalationError, match="spec.md"):
        run_spec_and_plan(ctx)


def test_plan_review_covers_plan_and_contract(ctx):
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    plan_review = ctx.codex.calls[1]
    assert plan_review.kind == "plan"
    assert any("plan.md" in ref for ref in plan_review.content_refs)
    assert any("contract.yaml" in ref for ref in plan_review.content_refs)
    # Die Spec gehört in den Plan-Review — sonst kann Codex Plan/Kontrakt
    # nicht gegen die Akzeptanzkriterien prüfen.
    assert any("spec.md" in ref for ref in plan_review.content_refs)


def test_resume_after_approval_continues_at_build(ctx):
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    ctx.state = RunState.load(ctx.repo, ctx.state.run_id)
    ctx.state.approval_granted = True
    run_spec_and_plan(ctx)  # setzt fort statt neu zu spec-en
    assert ctx.state.phase == "build"
    assert len([c for c in ctx.agents.calls if c.agent == "spec_agent"]) == 1


def test_parallel_run_requires_parallel_capable_config(target_repo):
    write_config(target_repo)  # nur backend-Lane
    agents = MockAgentRunner()
    codex = MockCodexRunner()
    state = RunState.new(issue="x", parallel=True)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=agents,
        codex=codex,
    )
    with pytest.raises(EscalationError, match="frontend"):
        run_spec_and_plan(ctx)


# --- 10c: Build-Lane-Loop -------------------------------------------------

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
  staging_job: deploy-staging
"""


def prepare_approved(ctx):
    """Bringt den Context in den Zustand 'build' (Spec/Plan fertig, approved)."""
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    ctx.skip_approval = True
    run_spec_and_plan(ctx)
    assert ctx.state.phase == "build"


def test_build_lane_commits_agent_changes(ctx):
    from tests.conftest import git

    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"src_neu.py": "print('hallo')\n"})
    ctx.agents.script("build_agent", "Backend gebaut")
    run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    assert (worktree / "src_neu.py").is_file()
    log = git(worktree, "log", "--oneline")
    assert ctx.state.run_id in log or "adw" in log.lower()
    status = git(worktree, "status", "--porcelain")
    assert status == ""  # alles committet
    assert ctx.state.phase == "codex_review"  # v1: keine Integration nötig


def test_build_worktree_contains_committed_artifacts(ctx):
    from tests.conftest import git

    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"x.py": "pass\n"})
    ctx.agents.script("build_agent", "ok")
    run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    assert (worktree / ".adw" / "spec.md").is_file()
    tracked = git(worktree, "ls-files", ".adw")
    assert ".adw/spec.md" in tracked and ".adw/contract.yaml" in tracked


def test_gate_failure_feeds_output_back_into_same_session(ctx, target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - name: marker-gate
        cmd: "sh -c 'test -f fixed || { echo GATE-KAPUTT; exit 1; }'"
        timeout: 10
""",
    )
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)
    ctx.agents.script("build_agent", "erster Versuch", "gefixt")

    original_script_files = ctx.agents.file_writes

    class FixOnSecondRun(dict):
        def get(self, key, default=None):
            if key != "build_agent":
                return super().get(key, default)
            # run() zeichnet den Call VOR dem file_writes-Lookup auf — beim
            # ersten Lauf steht der Zähler also schon auf 1.
            build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
            if len(build_calls) >= 2:
                return {"fixed": "jetzt ist es gut\n", "src.py": "pass\n"}
            return {"src.py": "pass\n"}

    ctx.agents.file_writes = FixOnSecondRun(original_script_files)
    run_build_phase(ctx)
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2
    assert build_calls[1].resume == build_calls[0].resume or build_calls[1].resume
    assert "GATE-KAPUTT" in build_calls[1].task
    assert build_calls[1].resume is not None


def test_identical_gate_failures_trigger_circuit_breaker(ctx, target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: immer-kaputt, cmd: "sh -c 'echo IMMER-GLEICH; exit 1'", timeout: 10}
""",
    )
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"src.py": "pass\n"})
    ctx.agents.script("build_agent", *["Versuch"] * 12)
    with pytest.raises(EscalationError, match="Circuit|unverändert"):
        run_build_phase(ctx)
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2  # nicht bis 10 ausgereizt


def test_gate_iteration_limit_escalates(ctx, target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - name: variabel-kaputt
        cmd: "sh -c 'cat zaehler 2>/dev/null | wc -l; echo x >> zaehler; exit 1'"
        timeout: 10
""",
    )
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"src.py": "pass\n"})
    ctx.agents.script("build_agent", *["Versuch"] * 12)
    with pytest.raises(EscalationError, match="[Ii]teration"):
        run_build_phase(ctx)
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 10


def test_build_agent_without_changes_escalates(ctx):
    prepare_approved(ctx)
    ctx.agents.script("build_agent", "behauptet gebaut zu haben")  # keine file_writes
    ctx.agents.file_writes.pop("build_agent", None)
    with pytest.raises(EscalationError, match="keine Änderungen"):
        run_build_phase(ctx)


def test_resume_build_with_committed_artifacts_and_dirty_rest(ctx):
    """Regression: Resume mit committeten Artefakten + dirty Nicht-.adw-Files
    darf nicht an einem leeren Seeding-Commit scheitern."""
    import shutil

    from adw.worktrees import create_lane_worktree
    from tests.conftest import git

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    (worktree / ".adw").mkdir(exist_ok=True)
    for name in ("spec.md", "plan.md", "contract.yaml"):
        source = ctx.run_dir / name
        if source.is_file():
            shutil.copy2(source, worktree / ".adw" / name)
    git(worktree, "add", ".adw")
    git(worktree, "commit", "-m", "adw: Artefakte (vorheriger Lauf)")
    (worktree / "halbfertig.py").write_text("x = 1\n")  # Crash-Rest des Agents

    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "weitergebaut")
    run_build_phase(ctx)
    assert ctx.state.phase == "codex_review"


def test_agent_tampering_with_approved_artifacts_is_reverted(ctx):
    """Regression: der Build-Agent darf den approvten Kontrakt nicht still ändern."""
    from tests.conftest import git

    prepare_approved(ctx)
    ctx.agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/contract.yaml": "openapi: 9.9.9  # manipuliert\n"},
    )
    ctx.agents.script("build_agent", "gebaut (und am Kontrakt gedreht)")
    run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    committed = git(worktree, "show", "HEAD:.adw/contract.yaml")
    assert "manipuliert" not in committed
    assert (worktree / ".adw" / "contract.yaml").read_text() == (
        ctx.run_dir / "contract.yaml"
    ).read_text()


def test_lane_checkpoint_is_persisted_before_gates(ctx, target_repo):
    """Regression: Session-ID/Iteration müssen VOR den (langen) Gates auf Platte sein."""
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - name: checkpoint-gate
        cmd: "sh -c 'grep -q mock-session ../../state.json'"
        timeout: 10
""",
    )
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"x.py": "pass\n"})
    ctx.agents.script("build_agent", "ok")
    run_build_phase(ctx)  # Gate ist nur grün, wenn der Checkpoint schon persistiert war
    assert ctx.state.phase == "codex_review"


def test_gates_run_against_restored_artifacts(ctx, target_repo):
    """Regression: Gates müssen den approvten Kontrakt sehen, nicht die Agent-Version."""
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - name: contract-gate
        cmd: "grep -q 'openapi: 3.1.0' .adw/contract.yaml"
        timeout: 10
""",
    )
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)
    ctx.agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/contract.yaml": "openapi: 9.9.9  # manipuliert\n"},
    )
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)  # Gate ist nur grün, wenn vor den Gates restauriert wurde
    assert ctx.state.phase == "codex_review"


def test_reverted_implementation_escalates_at_commit(ctx):
    """Regression: Resume-Iteration ohne jede Implementierungs-Änderung darf nicht
    als Erfolg durchgehen."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        gate_iterations=3,  # Resume: first-iteration-Guard greift nicht mehr
    )
    ctx.agents.file_writes.pop("build_agent", None)
    ctx.agents.script("build_agent", "alles zurückgebaut, nichts geändert")
    with pytest.raises(EscalationError, match="keine.*Änderung|Implementierung"):
        run_build_phase(ctx)


def test_resume_at_iteration_limit_escalates_without_new_agent_call(ctx):
    """Regression: Crash bei Iteration 10 darf nach Resume keinen 11. Versuch erlauben."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        gate_iterations=10,
    )
    with pytest.raises(EscalationError, match="[Ii]teration"):
        run_build_phase(ctx)
    assert [c for c in ctx.agents.calls if c.agent == "build_agent"] == []


def test_no_approval_choice_survives_resume(ctx):
    """Regression: --no-approval muss im State liegen — Resume darf nicht pausieren."""
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    ctx.skip_approval = True
    run_spec_and_plan(ctx)
    # Simulierter Crash direkt nach der awaiting_approval-Persistenz:
    ctx.state.phase = "awaiting_approval"
    ctx.state.save(ctx.repo)
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
        skip_approval=False,  # der Resume-Aufruf kennt das Flag nicht mehr
    )
    run_spec_and_plan(resumed)  # darf NICHT AwaitingApproval werfen
    assert resumed.state.phase == "build"


def test_no_approval_is_persisted_before_spec_starts(ctx):
    """Regression: --no-approval muss VOR dem ersten Agent-Lauf auf Platte sein."""
    ctx.skip_approval = True
    with pytest.raises(AssertionError):  # kein Script = simulierter Crash im Spec-Agent
        run_spec_and_plan(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).skip_approval is True


def test_completed_lane_is_not_rebuilt_on_resume(ctx):
    """Regression: Crash vor dem Phasenübergang darf eine fertige Lane nicht neu bauen."""
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    calls_after_first = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
    # Simulierter Crash VOR dem Übergang zu codex_review:
    ctx.state.phase = "build"
    ctx.state.save(ctx.repo)
    run_build_phase(ctx)
    calls_after_resume = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
    assert calls_after_resume == calls_after_first  # kein neuer Agent-Lauf
    assert ctx.state.phase == "codex_review"


def test_committed_lane_without_completed_flag_is_recovered(ctx):
    """Regression: Crash zwischen Commit und completed-Flag darf nicht neu bauen."""
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    calls_first = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
    # Simulierter Crash NACH dem Commit, VOR der completed-Persistenz:
    ctx.state.phase = "build"
    ctx.state.lanes["backend"].completed = False
    ctx.state.save(ctx.repo)
    run_build_phase(ctx)
    assert len([c for c in ctx.agents.calls if c.agent == "build_agent"]) == calls_first
    assert ctx.state.phase == "codex_review"
    assert ctx.state.lanes["backend"].completed is True


def test_authoring_write_rules_are_artifact_exact():
    """Regression: Spec-/Plan-Agent dürfen nicht in .adw/runs/ (State!) schreiben können."""
    from adw.agents import REGISTRY

    spec_rules = set(REGISTRY["spec_agent"].allowed_tools)
    plan_rules = set(REGISTRY["plan_agent"].allowed_tools)
    assert not any(".adw/**" in rule for rule in spec_rules | plan_rules)
    assert "Write(.adw/spec.md)" in spec_rules
    assert "Write(.adw/plan.md)" in plan_rules
    assert "Write(.adw/contract.yaml)" in plan_rules
    assert "Write(.adw/spec.md)" not in plan_rules  # Plan-Agent schreibt keine Spec


def test_run_dir_writes_are_deny_listed_for_all_agents(tmp_path):
    from adw.agents import _deny_rules

    denied = set(_deny_rules())
    assert "Write(.adw/runs/**)" in denied
    assert "Edit(.adw/runs/**)" in denied


def test_agent_side_commit_is_not_recovered_as_completed(ctx):
    """Regression: nur der Orchestrator-Commit (Sentinel) beweist grüne Gates."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for
    from tests.conftest import git

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    (worktree / "ungeprueft.py").write_text("x = 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "wip vom Agenten (keine Gates gelaufen)")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        gate_iterations=1,
    )
    ctx.agents.script_files("build_agent", {"echt.py": "pass\n"})
    ctx.agents.script("build_agent", "jetzt richtig")
    run_build_phase(ctx)
    assert len([c for c in ctx.agents.calls if c.agent == "build_agent"]) == 1
    assert ctx.state.phase == "codex_review"


def test_failed_gate_feedback_survives_resume(ctx, target_repo):
    """Regression: Nach Crash zwischen Gate-Fail und Fix-Lauf muss der Resume
    das Gate-Feedback und die Circuit-Breaker-Basis noch kennen."""
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - name: immer-kaputt
        cmd: "sh -c 'echo IMMER-GLEICH; exit 1'"
        timeout: 10
""",
    )
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"src.py": "pass\n"})
    ctx.agents.script("build_agent", *["Versuch"] * 12)
    with pytest.raises(EscalationError):
        run_build_phase(ctx)
    # Crash-Simulation: Der persistierte State muss das Feedback tragen.
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    lane = saved.lanes["backend"]
    assert lane.pending_task and "IMMER-GLEICH" in lane.pending_task
    assert lane.last_failures


def test_forged_sentinel_commit_is_not_recovered(ctx):
    """Regression: Commit-Messages sind fälschbar — nur persistierte gates_passed zählen."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for
    from tests.conftest import git

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    (worktree / "ungeprueft.py").write_text("x = 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", f"adw({ctx.state.run_id}/backend): gefälschter Sentinel")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        gate_iterations=1,
    )
    ctx.agents.script_files("build_agent", {"echt.py": "pass\n"})
    ctx.agents.script("build_agent", "jetzt mit Gates")
    run_build_phase(ctx)
    # Der Agent MUSS erneut laufen — der gefälschte Commit beweist keine Gates.
    assert len([c for c in ctx.agents.calls if c.agent == "build_agent"]) == 1


def test_build_agent_config_tampering_is_not_committed(ctx):
    """Regression: der Build-Agent darf die Gate-Konfiguration nicht mitcommitten."""
    from tests.conftest import git

    prepare_approved(ctx)
    ctx.agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/config.yaml": "base_branch: main  # sabotiert\n"},
    )
    ctx.agents.script("build_agent", "gebaut (Config angefasst)")
    run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    committed = git(worktree, "show", "HEAD:.adw/config.yaml")
    assert "sabotiert" not in committed


def test_every_post_gate_checkpoint_carries_the_feedback(ctx, target_repo, monkeypatch):
    """Regression: kein persistierter Zwischenstand nach Gate-Fail ohne pending_task."""
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - name: immer-kaputt
        cmd: "sh -c 'echo IMMER-GLEICH; exit 1'"
        timeout: 10
""",
    )
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"src.py": "pass\n"})
    ctx.agents.script("build_agent", *["Versuch"] * 3)

    saves = []
    original_save = RunState.save

    def spy(self, repo):
        lane = self.lanes.get("backend")
        calls = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
        saves.append((calls, lane.pending_task if lane else None))
        return original_save(self, repo)

    monkeypatch.setattr(RunState, "save", spy)
    with pytest.raises(EscalationError):
        run_build_phase(ctx)
    after_first_agent = [pending for calls, pending in saves if calls == 1]
    assert after_first_agent, "keine Checkpoints nach dem ersten Agent-Lauf"
    # Erster Save = Session-Checkpoint (noch kein Gate gelaufen) — jeder
    # weitere Save mit calls==1 MUSS das Gate-Feedback tragen.
    assert all(pending is not None for pending in after_first_agent[1:])


def test_untracked_config_created_by_agent_is_not_committed(tmp_path):
    """Regression: ist config.yaml untracked, darf der Agent keine eigene einschleusen."""
    from tests.conftest import git, write_config

    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "staging")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")  # Config bleibt UNTRACKED
    write_config(repo)

    agents = MockAgentRunner()
    agents.script_files("spec_agent", {".adw/spec.md": "# Spec\n"})
    agents.script_files(
        "plan_agent", {".adw/plan.md": "# Plan\n", ".adw/contract.yaml": "openapi: 3.1.0\n"}
    )
    agents.script("spec_agent", "Spec")
    agents.script("plan_agent", "Plan")
    agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/config.yaml": "base_branch: main  # eingeschleust\n"},
    )
    agents.script("build_agent", "gebaut")
    codex = MockCodexRunner()
    codex.script(OK, OK)
    state = RunState.new(issue="x", parallel=False)
    ctx = RunContext(
        repo=repo,
        config=AdwConfig.load(repo),
        state=state,
        agents=agents,
        codex=codex,
        skip_approval=True,
    )
    run_spec_and_plan(ctx)
    run_build_phase(ctx)
    worktree = repo / ".adw" / "runs" / state.run_id / "trees" / "backend"
    tracked = git(worktree, "ls-files", ".adw/config.yaml")
    assert tracked == ""  # die eingeschleuste Config ist NICHT committet


def test_failed_config_restore_of_tracked_config_escalates(ctx, monkeypatch):
    """Regression: Git-Fehler beim Config-Restore darf nicht als 'untracked' gelten."""
    import subprocess as real_subprocess

    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    original_run = real_subprocess.run

    def failing_checkout(argv, **kwargs):
        if isinstance(argv, list) and "checkout" in argv and ".adw/config.yaml" in argv:
            return real_subprocess.CompletedProcess(argv, 128, stdout=b"", stderr=b"fatal: kaputt")
        return original_run(argv, **kwargs)

    monkeypatch.setattr("adw.phases.subprocess.run", failing_checkout)
    with pytest.raises(EscalationError, match="[Cc]onfig"):
        run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    assert (worktree / ".adw" / "config.yaml").is_file()  # NICHT gelöscht


def test_tampered_tree_after_gates_passed_is_not_finalized_blindly(ctx):
    """Regression: gates_passed beweist den DAMALIGEN Baum — ein veränderter
    Baum muss erneut durch Agent/Gates statt blind committet zu werden."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    (worktree / "nach_dem_crash.py").write_text("tamper = True\n")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        gate_iterations=1,
        gates_passed=True,
        gates_tree="deadbeef00000000000000000000000000000000",  # passt nicht mehr
    )
    ctx.agents.script_files("build_agent", {"echt.py": "pass\n"})
    ctx.agents.script("build_agent", "neu verifiziert")
    run_build_phase(ctx)
    # Der Agent + die Gates MÜSSEN erneut gelaufen sein:
    assert len([c for c in ctx.agents.calls if c.agent == "build_agent"]) == 1
    assert ctx.state.phase == "codex_review"


def test_git_error_during_config_absence_check_escalates(ctx, monkeypatch):
    """Regression: Git-Fehler bei der Absenz-Prüfung darf nicht als 'fehlt' gelten."""
    import subprocess as real_subprocess

    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    original_run = real_subprocess.run

    def failing_git(argv, **kwargs):
        if isinstance(argv, list) and any(".adw/config.yaml" in str(a) for a in argv):
            if any(cmd in argv for cmd in ("checkout", "ls-tree", "cat-file")):
                return real_subprocess.CompletedProcess(
                    argv, 128, stdout=b"", stderr=b"fatal: repo kaputt"
                )
        return original_run(argv, **kwargs)

    monkeypatch.setattr("adw.phases.subprocess.run", failing_git)
    with pytest.raises(EscalationError):
        run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    assert (worktree / ".adw" / "config.yaml").is_file()  # NICHT gelöscht


def test_resume_in_plan_phase_reruns_the_plan_loop(ctx):
    """Regression: Crash während der Plan-Phase darf den Run nicht in 'plan' stranden."""
    ctx.agents.script("spec_agent", "Spec")  # Plan-Agent NICHT gescriptet → Crash
    ctx.codex.script(OK)
    with pytest.raises(AssertionError):
        run_spec_and_plan(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).phase == "plan"

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
    )
    ctx.agents.script("plan_agent", "Plan (nach Resume)")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert resumed.state.phase == "awaiting_approval"
    assert (resumed.run_dir / "plan.md").is_file()


def test_restore_survives_deleted_adw_directory(ctx):
    """Regression: gelöschtes .adw im Worktree darf die Restauration nicht crashen."""
    import shutil

    from adw.phases import _restore_approved_artifacts
    from adw.worktrees import create_lane_worktree

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    shutil.rmtree(worktree / ".adw", ignore_errors=True)
    (worktree / ".adw").mkdir()
    (worktree / ".adw" / "spec.md").mkdir()  # Verzeichnis statt Datei
    _restore_approved_artifacts(ctx, worktree, "staging")
    assert (worktree / ".adw" / "spec.md").is_file()


def test_crash_between_archive_and_phase_save_is_recoverable(ctx):
    """Regression: Spec schon archiviert, Phase noch 'plan' — Resume muss laufen."""
    ctx.agents.script("spec_agent", "Spec")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    # Crash-Fenster simulieren: Artefakte sind archiviert, Phase zurück auf 'plan'.
    ctx.state.phase = "plan"
    ctx.state.save(ctx.repo)
    assert not (ctx.repo / ".adw" / "spec.md").is_file()  # archiviert = weg aus .adw

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
    )
    ctx.agents.script("plan_agent", "Plan (Resume)")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert resumed.state.phase == "awaiting_approval"


def test_authoring_resume_keeps_session_and_review_feedback(ctx):
    """Regression: Crash im Spec-Fix-Zyklus darf Session + Codex-Feedback nicht verlieren."""
    ctx.agents.script("spec_agent", "v1")  # Fix-Lauf NICHT gescriptet → Crash dort
    ctx.codex.script(needs_fixes("Akzeptanzkriterien fehlen"))
    with pytest.raises(AssertionError):
        run_spec_and_plan(ctx)

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
    )
    ctx.agents.script("spec_agent", "v2 nachgeschärft")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    spec_calls = [c for c in ctx.agents.calls if c.agent == "spec_agent"]
    # v1, gecrashter Fix-Versuch (vom Mock vor dem Raise aufgezeichnet), Resume-Fix
    assert len(spec_calls) == 3
    resumed_fix = spec_calls[2]
    # Der Resume-Lauf setzt die ALTE Session fort und trägt das Codex-Feedback:
    assert resumed_fix.resume == "mock-session-spec_agent-1"
    assert "Akzeptanzkriterien fehlen" in resumed_fix.task


def test_config_restore_checkout_runs_without_repo_hooks(ctx, tmp_path):
    """Regression: post-checkout-Hooks dürfen beim Config-Restore nicht feuern."""
    from tests.conftest import git

    marker = tmp_path / "hook-lief"
    hooks_dir = ctx.repo / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "post-checkout").write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    (hooks_dir / "post-checkout").chmod(0o755)
    git(ctx.repo, "config", "core.hooksPath", "hooks")
    prepare_approved(ctx)
    ctx.agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/config.yaml": "base_branch: main  # angefasst\n"},
    )
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    assert not marker.exists(), "Repo-Hook lief außerhalb der Sandbox"


def test_agent_side_commits_during_build_escalate(ctx):
    """Regression: committet der Agent selbst (Bash), muss der Orchestrator eskalieren."""
    from tests.conftest import git

    class CommittingAgentRunner(MockAgentRunner):
        def run(self, agent, task, cwd, resume=None, deny_read_paths=None):
            result = super().run(agent, task, cwd, resume, deny_read_paths)
            if agent.name == "build_agent":
                git(cwd, "add", "-A")
                git(cwd, "commit", "-m", "heimlicher Agent-Commit")
            return result

    committing = CommittingAgentRunner()
    committing.scripts = ctx.agents.scripts
    committing.file_writes = ctx.agents.file_writes
    ctx.agents = committing
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut (und selbst committet)")
    with pytest.raises(EscalationError, match="committ"):
        run_build_phase(ctx)


def test_missing_archived_artifact_escalates_before_build(ctx):
    """Regression: fehlt ein archiviertes Artefakt, darf keine Lane dagegen bauen."""
    prepare_approved(ctx)
    (ctx.run_dir / "contract.yaml").unlink()
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    with pytest.raises(EscalationError, match="contract"):
        run_build_phase(ctx)


def test_completed_lane_with_tampered_tree_is_not_skipped(ctx):
    """Regression: completed + verändertem Baum darf nicht ungeprüft weitergereicht werden."""
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    # Crash-Fenster + Manipulation: Phase zurück, Baum verändert, completed bleibt.
    ctx.state.phase = "build"
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    (worktree / "nachtraeglich.py").write_text("tamper = True\n")
    ctx.state.save(ctx.repo)
    ctx.agents.script("build_agent", "neu verifiziert")
    run_build_phase(ctx)
    # Agent/Gates MÜSSEN erneut gelaufen sein statt die Lane blind zu skippen:
    assert len([c for c in ctx.agents.calls if c.agent == "build_agent"]) == 2
    assert ctx.state.phase == "codex_review"


def test_uncommitted_edits_on_tracked_artifacts_fail_fast(ctx):
    """Regression: ungespeicherte Nutzer-Edits an getrackten Artefakten dürfen
    nicht durch die Archivierung verworfen werden."""
    from tests.conftest import git

    spec_path = ctx.repo / ".adw" / "spec.md"
    spec_path.write_text("# Gemergte Spec\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Spec")
    spec_path.write_text("# Gemergte Spec\n\nUNGESPEICHERTE NOTIZ DES NUTZERS\n")
    ctx.agents.script("spec_agent", "egal")
    with pytest.raises(EscalationError, match="uncommittete|ungespeichert|Änderungen"):
        run_spec_and_plan(ctx)
    assert "UNGESPEICHERTE NOTIZ" in spec_path.read_text()


def test_directory_shaped_injected_config_is_removed_safely(tmp_path):
    """Regression: Verzeichnis statt config.yaml darf den Lauf nicht crashen."""
    from tests.conftest import git, write_config

    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "staging")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")  # Config bleibt UNTRACKED
    write_config(repo)

    agents = MockAgentRunner()
    agents.script_files("spec_agent", {".adw/spec.md": "# Spec\n"})
    agents.script_files(
        "plan_agent", {".adw/plan.md": "# Plan\n", ".adw/contract.yaml": "openapi: 3.1.0\n"}
    )
    agents.script("spec_agent", "Spec")
    agents.script("plan_agent", "Plan")
    agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/config.yaml/eingeschleust.txt": "böse\n"},
    )
    agents.script("build_agent", "gebaut")
    codex = MockCodexRunner()
    codex.script(OK, OK)
    state = RunState.new(issue="x", parallel=False)
    ctx = RunContext(
        repo=repo,
        config=AdwConfig.load(repo),
        state=state,
        agents=agents,
        codex=codex,
        skip_approval=True,
    )
    run_spec_and_plan(ctx)
    run_build_phase(ctx)  # darf nicht mit IsADirectoryError crashen
    worktree = repo / ".adw" / "runs" / state.run_id / "trees" / "backend"
    assert git(worktree, "ls-files", ".adw/config.yaml") == ""


def test_archived_spec_takes_precedence_on_plan_resume(ctx):
    """Regression: Crash mitten in der Archivierung — die reviewte (archivierte)
    Spec schlägt eine per git restaurierte alte getrackte Spec."""
    seen_specs = []

    class SpyCodex(MockCodexRunner):
        def review(self, kind, content_refs, cwd):
            seen_specs.append((kind, (cwd / ".adw" / "spec.md").read_text()))
            return super().review(kind, content_refs, cwd)

    ctx.codex = SpyCodex()
    # Crash-Fenster nachbauen: Phase 'plan', archivierte reviewte Spec im
    # Run-Ordner, aber .adw/spec.md trägt die ALTE (getrackte) Version.
    ctx.state.phase = "plan"
    ctx.state.save(ctx.repo)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    (ctx.run_dir / "spec.md").write_text("# REVIEWTE Spec\n")
    (ctx.repo / ".adw" / "spec.md").write_text("# ALTE getrackte Spec\n")
    ctx.agents.script("plan_agent", "Plan")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    plan_reviews = [spec for kind, spec in seen_specs if kind == "plan"]
    assert plan_reviews and all("REVIEWTE" in spec for spec in plan_reviews)
    assert (ctx.run_dir / "spec.md").read_text() == "# REVIEWTE Spec\n"


def test_uncommitted_edits_guard_also_runs_on_plan_resume(ctx):
    """Regression: der Datenverlust-Guard muss auch beim Resume in 'plan' greifen."""
    from tests.conftest import git

    plan_path = ctx.repo / ".adw" / "plan.md"
    plan_path.write_text("# Gemergter Plan\n")
    git(ctx.repo, "add", ".adw/plan.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Plan")
    plan_path.write_text("# Gemergter Plan\n\nUNGESPEICHERTE NOTIZ\n")
    ctx.state.phase = "plan"
    ctx.state.save(ctx.repo)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    (ctx.run_dir / "spec.md").write_text("# Spec\n")
    ctx.agents.script("plan_agent", "egal")
    with pytest.raises(EscalationError, match="uncommittete|Änderungen"):
        run_spec_and_plan(ctx)
    assert "UNGESPEICHERTE NOTIZ" in plan_path.read_text()


def test_config_restore_uses_pinned_fork_point_not_moving_base(ctx):
    """Regression: rückt der Base-Branch vor, darf die Lane nicht dessen NEUE
    Config importieren — restauriert wird vom Fork-Point der Lane."""
    from tests.conftest import git

    original_config = (ctx.repo / ".adw" / "config.yaml").read_text()  # bereits getrackt
    prepare_approved(ctx)
    # Lane-Worktree entsteht jetzt (Fork-Point = aktueller staging-Stand):
    from adw.worktrees import create_lane_worktree

    create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    # Base-Branch rückt vor — mit GEÄNDERTER Config:
    (ctx.repo / ".adw" / "config.yaml").write_text(original_config + "# neuer stand\n")
    git(ctx.repo, "add", ".adw/config.yaml")
    git(ctx.repo, "commit", "-m", "config weiterentwickelt")

    ctx.agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/config.yaml": "base_branch: main  # tamper\n"},
    )
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    committed = git(worktree, "show", "HEAD:.adw/config.yaml")
    assert "neuer stand" not in committed  # nicht die vorgerückte Base-Version
    assert "tamper" not in committed
    assert committed.strip() == original_config.strip()


def test_symlinked_artifact_is_replaced_not_followed(ctx):
    """Regression: Symlink statt Artefakt darf weder Fremddateien überschreiben
    noch die Restauration umgehen."""
    from adw.phases import _restore_approved_artifacts
    from adw.worktrees import create_lane_worktree

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    (worktree / ".adw").mkdir(exist_ok=True)
    victim = worktree / "opfer.txt"
    victim.write_text("unantastbar\n")
    contract = worktree / ".adw" / "contract.yaml"
    contract.unlink(missing_ok=True)
    contract.symlink_to(victim)
    _restore_approved_artifacts(ctx, worktree, "staging")
    assert not contract.is_symlink()
    assert contract.read_text() == (ctx.run_dir / "contract.yaml").read_text()
    assert victim.read_text() == "unantastbar\n"  # Referent unangetastet


def test_agent_commit_in_crash_window_is_detected_on_resume(ctx):
    """Regression: committet der Agent und crasht der Orchestrator davor,
    muss der Resume den fremden HEAD erkennen."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for
    from tests.conftest import git

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    fork_head = git(worktree, "rev-parse", "HEAD")
    (worktree / "heimlich.py").write_text("x = 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "heimlicher Agent-Commit im Crash-Fenster")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        gate_iterations=1,
        expected_head=fork_head,
    )
    ctx.agents.script("build_agent", "egal")
    with pytest.raises(EscalationError, match="committ|HEAD"):
        run_build_phase(ctx)


def test_agent_branch_switch_is_detected(ctx):
    """Regression: git switch --detach am selben Commit darf nicht unbemerkt bleiben."""
    from tests.conftest import git

    class BranchSwitchingAgent(MockAgentRunner):
        def run(self, agent, task, cwd, resume=None, deny_read_paths=None):
            result = super().run(agent, task, cwd, resume, deny_read_paths)
            if agent.name == "build_agent":
                git(cwd, "switch", "--detach", "HEAD")
            return result

    switching = BranchSwitchingAgent()
    switching.scripts = ctx.agents.scripts
    switching.file_writes = ctx.agents.file_writes
    ctx.agents = switching
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut (und Branch gewechselt)")
    with pytest.raises(EscalationError, match="[Bb]ranch|ref"):
        run_build_phase(ctx)


def test_symlinked_adw_directory_is_replaced_not_followed(ctx):
    """Regression: .adw als Symlink darf den Orchestrator nicht aus der Lane schreiben lassen."""
    import shutil

    from adw.phases import _restore_approved_artifacts
    from adw.worktrees import create_lane_worktree

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    victim_dir = ctx.repo / "opfer-verzeichnis"
    victim_dir.mkdir()
    (victim_dir / "wichtig.txt").write_text("unantastbar\n")
    shutil.rmtree(worktree / ".adw", ignore_errors=True)
    (worktree / ".adw").symlink_to(victim_dir)
    _restore_approved_artifacts(ctx, worktree, "staging")
    assert not (worktree / ".adw").is_symlink()
    assert (worktree / ".adw" / "spec.md").is_file()
    assert (victim_dir / "wichtig.txt").read_text() == "unantastbar\n"
    assert not (victim_dir / "spec.md").exists()  # nichts in den Referenten geschrieben


def test_config_symlink_to_directory_is_removed_safely(tmp_path):
    """Regression: config.yaml als Symlink auf ein Verzeichnis darf rmtree nicht crashen."""
    from tests.conftest import git, write_config

    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "staging")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")  # Config bleibt UNTRACKED
    write_config(repo)

    agents = MockAgentRunner()
    agents.script_files("spec_agent", {".adw/spec.md": "# Spec\n"})
    agents.script_files(
        "plan_agent", {".adw/plan.md": "# Plan\n", ".adw/contract.yaml": "openapi: 3.1.0\n"}
    )
    agents.script("spec_agent", "Spec")
    agents.script("plan_agent", "Plan")

    class SymlinkingAgent(MockAgentRunner):
        def run(self, agent, task, cwd, resume=None, deny_read_paths=None):
            result = super().run(agent, task, cwd, resume, deny_read_paths)
            if agent.name == "build_agent":
                victim = repo / "opfer-verzeichnis"
                victim.mkdir(exist_ok=True)
                config = cwd / ".adw" / "config.yaml"
                if not config.is_symlink() and not config.exists():
                    config.parent.mkdir(parents=True, exist_ok=True)
                    config.symlink_to(victim)
            return result

    symlinking = SymlinkingAgent()
    symlinking.scripts = agents.scripts
    symlinking.file_writes = agents.file_writes
    symlinking.script_files("build_agent", {"neu.py": "pass\n"})
    symlinking.script("build_agent", "gebaut")
    codex = MockCodexRunner()
    codex.script(OK, OK)
    state = RunState.new(issue="x", parallel=False)
    ctx = RunContext(
        repo=repo,
        config=AdwConfig.load(repo),
        state=state,
        agents=symlinking,
        codex=codex,
        skip_approval=True,
    )
    run_spec_and_plan(ctx)
    run_build_phase(ctx)  # darf nicht an rmtree-auf-Symlink crashen
    assert (repo / "opfer-verzeichnis").is_dir()  # Referent unangetastet


def test_own_dirty_tracked_spec_does_not_trip_the_guard_on_plan_resume(ctx):
    """Regression: die EIGENE neue Spec über einer getrackten alten ist kein User-Edit."""
    from tests.conftest import git

    spec_path = ctx.repo / ".adw" / "spec.md"
    spec_path.write_text("# Spec des VORHERIGEN Runs (gemergt)\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Spec")
    ctx.agents.script("spec_agent", "Spec")  # Plan-Agent unscripted → Crash dort
    ctx.codex.script(OK)
    with pytest.raises(AssertionError):
        run_spec_and_plan(ctx)

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
    )
    ctx.agents.script("plan_agent", "Plan (Resume)")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):  # NICHT EscalationError durch den Guard
        run_spec_and_plan(resumed)
    assert (resumed.run_dir / "spec.md").read_text() == "# Spec\nZiel: Demo\n"


def test_file_shaped_adw_directory_is_replaced(ctx):
    """Regression: .adw als DATEI darf mkdir nicht mit FileExistsError crashen."""
    import shutil

    from adw.phases import _restore_approved_artifacts
    from adw.worktrees import create_lane_worktree

    prepare_approved(ctx)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    shutil.rmtree(worktree / ".adw", ignore_errors=True)
    (worktree / ".adw").write_text("ich bin eine Datei")
    _restore_approved_artifacts(ctx, worktree, "staging")
    assert (worktree / ".adw").is_dir()
    assert (worktree / ".adw" / "spec.md").is_file()


def test_parallel_lanes_build_isolated_worktrees(target_repo):
    from tests.conftest import git

    write_config(target_repo, PARALLEL_CONFIG)
    agents = MockAgentRunner()
    agents.script_files("spec_agent", {".adw/spec.md": "# Spec\n"})
    agents.script_files(
        "plan_agent", {".adw/plan.md": "# Plan\n", ".adw/contract.yaml": "openapi: 3.1.0\n"}
    )
    agents.script("spec_agent", "Spec")
    agents.script("plan_agent", "Plan")
    agents.script_files("build_agent", {"gebaut.py": "pass\n"})
    agents.script("build_agent", "Lane 1", "Lane 2")
    codex = MockCodexRunner()
    codex.script(OK, OK)
    state = RunState.new(issue="ISSUE-2: parallel", parallel=True)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=agents,
        codex=codex,
        skip_approval=True,
    )
    run_spec_and_plan(ctx)
    run_build_phase(ctx)
    for lane in ("backend", "frontend"):
        worktree = target_repo / ".adw" / "runs" / state.run_id / "trees" / lane
        assert (worktree / "gebaut.py").is_file()
        assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == f"adw/{state.run_id}/{lane}"
    build_calls = [c for c in agents.calls if c.agent == "build_agent"]
    deny_sets = [set(c.deny_read_paths) for c in build_calls]
    assert any(any("frontend" in p for p in paths) for paths in deny_sets)
    assert any(any("backend" in p for p in paths) for paths in deny_sets)
    assert ctx.state.phase == "integration"
