"""Tests of the phase orchestration — entirely with mocks, 0 tokens, real git."""

import json
import logging
import re
import threading

import pytest

from adw.codex import CodexError
from adw.config import AdwConfig
from adw.findings import Finding, ReviewResult
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import (
    AwaitingApproval,
    EscalationError,
    RunContext,
    _draft_stage,
    run_build_phase,
    run_ci_phase,
    run_codex_review_phase,
    run_final_review_phase,
    run_integration_phase,
    run_spec_and_plan,
)
from adw.state import RunState
from tests.conftest import DEFAULT_CONFIG, write_config

OK = ReviewResult(verdict="ok", findings=[])

# Die Synthese schreibt Best-of-Artefakt UND Summary — bewusst anderer Inhalt
# als die Entwürfe, damit Tests Entwurf und Endartefakt unterscheiden können.
SPEC_SYNTHESIS_FILES = {
    ".adw/spec.md": "# Spec (Synthese)\nZiel: Demo\n",
    ".adw/spec-summary.md": "# Spec-Zusammenfassung\nBest-of beider Entwürfe.\n",
}
PLAN_SYNTHESIS_FILES = {
    ".adw/plan.md": "# Plan (Synthese)\nWorkstream backend\n",
    ".adw/contract.yaml": "openapi: 3.1.0\ninfo:\n  title: Synthese\n",
    ".adw/plan-summary.md": "# Plan-Zusammenfassung\nBest-of beider Entwürfe.\n",
}


def script_authoring_agents(agents) -> None:
    """Entwurfs-Autoren und Synthese-Agents beider Authoring-Phasen verdrahten.

    Die Entwuerfe tragen keine Testaussage — sie werden grosszuegig gescriptet,
    damit ein Test nur noch die Synthese (den eigentlichen Loop-Agenten) scripten
    muss. Restantworten in der Queue sind unschaedlich, eine LEERE Queue wuerde
    einen legitimen Lauf abbrechen."""
    agents.script_files("spec_agent", {".adw/spec.md": "# Spec\nZiel: Demo\n"})
    agents.script_files(
        "plan_agent",
        {
            ".adw/plan.md": "# Plan\nWorkstream backend\n",
            ".adw/contract.yaml": "openapi: 3.1.0\n",
        },
    )
    agents.script_files("spec_synthesis", dict(SPEC_SYNTHESIS_FILES))
    agents.script_files("plan_synthesis", dict(PLAN_SYNTHESIS_FILES))
    agents.script("spec_agent", *["Spec-Entwurf"] * 4)
    agents.script("plan_agent", *["Plan-Entwurf"] * 4)


def script_draft_artifacts(codex, count: int = 4) -> None:
    """Codex-Entwürfe beider Authoring-Phasen — Ersatz-Mocks (Spies) brauchen sie
    wie die Fixtures. Restantworten in der Queue sind unschädlich."""
    codex.script_artifacts("spec", *[{"spec.md": "# Codex-Spec\n"}] * count)
    codex.script_artifacts(
        "plan", *[{"plan.md": "# Codex-Plan\n", "contract.yaml": "openapi: 3.1.0\n"}] * count
    )


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
    script_authoring_agents(agents)
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
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
    ctx.agents.script("spec_synthesis", "Spec geschrieben")
    ctx.agents.script("plan_synthesis", "Plan geschrieben")
    ctx.codex.script(OK, OK)  # Spec-Review ok, Plan-Review ok
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "awaiting_approval"
    assert (ctx.run_dir / "spec.md").is_file()
    assert (ctx.run_dir / "plan.md").is_file()
    assert (ctx.run_dir / "contract.yaml").is_file()


def test_spec_findings_go_back_to_same_session(ctx):
    ctx.agents.script("spec_synthesis", "v1", "v2 nachgeschärft")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(needs_fixes("Akzeptanzkriterien fehlen"), OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    spec_calls = [c for c in ctx.agents.calls if c.agent == "spec_synthesis"]
    assert len(spec_calls) == 2
    assert spec_calls[0].resume is None
    assert spec_calls[1].resume == "mock-session-spec_synthesis-2"
    assert "Akzeptanzkriterien fehlen" in spec_calls[1].task


def test_no_approval_flag_skips_the_pause(ctx):
    ctx.skip_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    run_spec_and_plan(ctx)  # kein AwaitingApproval
    assert ctx.state.phase == "build"


def test_identical_spec_findings_twice_escalate(ctx):
    ctx.agents.script("spec_synthesis", "v1", "v2", "v3")
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
    """Regression: an old .adw/spec.md must not dignify an idle agent."""
    (ctx.repo / ".adw" / "spec.md").write_text("# ALTE Spec vom letzten Run\n")
    ctx.agents.file_writes.pop("spec_agent")  # Agent schreibt diesmal nichts
    ctx.agents.script("spec_synthesis", "behauptet fertig zu sein")
    with pytest.raises(EscalationError, match="spec.md"):
        run_spec_and_plan(ctx)


def test_tracked_artifacts_from_merged_adw_run_do_not_block_the_next_run(ctx):
    """Regression: after merging an ADW branch the artifacts are tracked — the
    next run must still work and leave the checkout clean."""
    from tests.conftest import git

    spec_path = ctx.repo / ".adw" / "spec.md"
    spec_path.write_text("# Spec des VORHERIGEN Runs (gemergt)\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Spec/Plan/Kontrakt")
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    # Neue Spec ist archiviert, der Checkout trägt wieder die gemergte Version:
    assert (ctx.run_dir / "spec.md").read_text() == SPEC_SYNTHESIS_FILES[".adw/spec.md"]
    assert spec_path.read_text() == "# Spec des VORHERIGEN Runs (gemergt)\n"
    assert git(ctx.repo, "status", "--porcelain") == ""


def test_plan_synthesis_cannot_silently_rewrite_reviewed_spec(ctx):
    """Regression: the spec is fixed after the spec review — the plan phase only plans."""
    plan_files = dict(ctx.agents.file_writes["plan_synthesis"])
    plan_files[".adw/spec.md"] = "# Umgeschriebene Spec\n"
    ctx.agents.script_files("plan_synthesis", plan_files)
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan (und Spec umgeschrieben)")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    archived = (ctx.run_dir / "spec.md").read_text()
    assert "Umgeschrieben" not in archived
    assert archived == SPEC_SYNTHESIS_FILES[".adw/spec.md"]


def test_run_artifacts_are_gitignored_from_the_start(ctx):
    from tests.conftest import git

    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    status = git(ctx.repo, "status", "--porcelain")
    assert ".adw/runs" not in status


def test_authoring_agents_cannot_mutate_target_config(ctx):
    """Regression: Write(.adw/**) technically also allows config.yaml — it gets restored."""
    original_config = (ctx.repo / ".adw" / "config.yaml").read_text()
    spec_files = dict(ctx.agents.file_writes["spec_agent"])
    spec_files[".adw/config.yaml"] = "base_branch: main  # sabotiert\n"
    ctx.agents.script_files("spec_agent", spec_files)
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert (ctx.repo / ".adw" / "config.yaml").read_text() == original_config


def test_config_is_restored_even_when_the_run_escalates(ctx):
    """Regression: the restore must be exception-safe — even on escalation."""
    original_config = (ctx.repo / ".adw" / "config.yaml").read_text()
    spec_files = dict(ctx.agents.file_writes["spec_agent"])
    spec_files[".adw/config.yaml"] = "base_branch: main  # sabotiert\n"
    ctx.agents.script_files("spec_agent", spec_files)
    ctx.agents.script("spec_synthesis", "v1", "v2", "v3")
    ctx.codex.script(needs_fixes("dasselbe"), needs_fixes("dasselbe"))
    with pytest.raises(EscalationError):
        run_spec_and_plan(ctx)
    assert (ctx.repo / ".adw" / "config.yaml").read_text() == original_config


def test_plan_review_always_sees_the_reviewed_spec(ctx):
    """Regression: Codex must never review plan+contract against a rewritten spec."""
    seen_specs = []

    class SpyCodex(MockCodexRunner):
        def review(self, kind, content_refs, cwd, context=None):
            seen_specs.append((kind, (cwd / ".adw" / "spec.md").read_text()))
            return super().review(kind, content_refs, cwd, context)

    ctx.codex = SpyCodex()
    script_draft_artifacts(ctx.codex)
    plan_files = dict(ctx.agents.file_writes["plan_synthesis"])
    plan_files[".adw/spec.md"] = "# Umgeschriebene Spec\n"
    ctx.agents.script_files("plan_synthesis", plan_files)
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    plan_reviews = [spec for kind, spec in seen_specs if kind == "plan"]
    assert plan_reviews and all("Umgeschrieben" not in spec for spec in plan_reviews)


def test_missing_spec_artifact_escalates(ctx):
    ctx.agents.file_writes.pop("spec_agent")  # Agent "vergisst" die Datei
    ctx.agents.script("spec_synthesis", "behauptet fertig zu sein")
    ctx.codex.script(OK)
    with pytest.raises(EscalationError, match="spec.md"):
        run_spec_and_plan(ctx)


def test_plan_review_covers_plan_and_contract(ctx):
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
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
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
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


# --- Draft-Stage: zwei unabhängige Entwürfe je Authoring-Phase ---------------

SPEC_DRAFT_TASK = "Erstelle die Spezifikation zu ISSUE-1 als .adw/spec.md."
PLAN_DRAFT_TASK = "Erstelle aus .adw/spec.md den Plan .adw/plan.md und .adw/contract.yaml."


def spec_draft(ctx, protected=None):
    return _draft_stage(
        ctx,
        kind="spec",
        agent_name="spec_agent",
        task=SPEC_DRAFT_TASK,
        artifacts=("spec.md",),
        protected=protected,
    )


class BarrierCodex(MockCodexRunner):
    """Codex-Mock, der an einer Barriere wartet — belegt die Überlappung
    beider Autoren ohne Timing-Sleeps."""

    def __init__(self, barrier):
        super().__init__()
        self.barrier = barrier

    def author(self, kind, task, cwd):
        self.barrier.wait()
        return super().author(kind, task, cwd)


def test_draft_stage_writes_both_drafts(ctx):
    ctx.agents.script("spec_agent", "Entwurf geschrieben")
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    result = spec_draft(ctx)
    drafts = ctx.run_dir / "drafts"
    assert (drafts / "spec.claude.md").read_text() == "# Spec\nZiel: Demo\n"
    assert (drafts / "spec.codex.md").read_text() == "# Codex-Spec\n"
    run_id = ctx.state.run_id
    assert result.claude == (f".adw/runs/{run_id}/drafts/spec.claude.md",)
    assert result.codex == (f".adw/runs/{run_id}/drafts/spec.codex.md",)
    # Der Entwurf lebt nur im Run-Ordner — der Checkout bleibt sauber.
    assert not (ctx.repo / ".adw" / "spec.md").exists()
    assert ctx.codex.author_calls[0].task == SPEC_DRAFT_TASK


def test_draft_stage_covers_plan_and_contract(ctx):
    ctx.agents.script("plan_agent", "Entwurf geschrieben")
    ctx.codex.script_artifacts(
        "plan", {"plan.md": "# Codex-Plan\n", "contract.yaml": "openapi: 3.1.0\n"}
    )
    result = _draft_stage(
        ctx,
        kind="plan",
        agent_name="plan_agent",
        task=PLAN_DRAFT_TASK,
        artifacts=("plan.md", "contract.yaml"),
    )
    drafts = ctx.run_dir / "drafts"
    assert (drafts / "plan.claude.md").read_text() == "# Plan\nWorkstream backend\n"
    assert (drafts / "contract.claude.yaml").read_text() == "openapi: 3.1.0\n"
    assert (drafts / "plan.codex.md").read_text() == "# Codex-Plan\n"
    assert (drafts / "contract.codex.yaml").read_text() == "openapi: 3.1.0\n"
    assert len(result.claude) == 2 and len(result.codex) == 2


def test_both_authors_run_at_the_same_time(ctx):
    barrier = threading.Barrier(2, timeout=10)

    def writes(_cwd):
        barrier.wait()  # löst nur auf, wenn Codex gleichzeitig läuft
        return {".adw/spec.md": "# Spec\nZiel: Demo\n"}

    ctx.agents.file_writes["spec_agent"] = writes
    ctx.agents.script("spec_agent", "Entwurf geschrieben")
    ctx.codex = BarrierCodex(barrier)
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    result = spec_draft(ctx)
    assert result.claude and result.codex


def test_existing_drafts_skip_both_authors(ctx):
    drafts = ctx.run_dir / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "spec.claude.md").write_text("# Entwurf aus der Vorrunde\n")
    (drafts / "spec.codex.md").write_text("# Codex-Entwurf aus der Vorrunde\n")
    result = spec_draft(ctx)
    assert ctx.agents.calls == []
    assert ctx.codex.author_calls == []
    assert (drafts / "spec.claude.md").read_text() == "# Entwurf aus der Vorrunde\n"
    assert result.claude and result.codex


def test_existing_drafts_still_clean_the_checkout(ctx):
    """Crash zwischen Draft-Kopie und Aufräumen: der Resume überspringt die
    Autoren, muss den Rest im Checkout aber trotzdem wegräumen."""
    drafts = ctx.run_dir / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "spec.claude.md").write_text("# Entwurf\n")
    (drafts / "spec.codex.md").write_text("# Codex-Entwurf\n")
    (ctx.repo / ".adw" / "spec.md").write_text("# Entwurf\n")
    spec_draft(ctx)
    assert ctx.agents.calls == []
    assert not (ctx.repo / ".adw" / "spec.md").exists()


def test_empty_draft_files_are_written_again(ctx):
    """Abgebrochener Draft-Write: eine leere Datei ist keine Entwurfsquelle."""
    drafts = ctx.run_dir / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "spec.claude.md").write_text("")
    (drafts / "spec.codex.md").write_text("  \n")
    ctx.agents.script("spec_agent", "Entwurf geschrieben")
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    result = spec_draft(ctx)
    assert (drafts / "spec.claude.md").read_text() == "# Spec\nZiel: Demo\n"
    assert (drafts / "spec.codex.md").read_text() == "# Codex-Spec\n"
    assert result.claude and result.codex


def test_empty_claude_draft_escalates(ctx):
    ctx.agents.script_files("spec_agent", {".adw/spec.md": "   \n"})
    ctx.agents.script("spec_agent", "behauptet fertig zu sein")
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    with pytest.raises(EscalationError, match="spec.md"):
        spec_draft(ctx)
    assert not (ctx.run_dir / "drafts" / "spec.claude.md").exists()


def test_incomplete_draft_set_reruns_its_author(ctx):
    """Crash zwischen plan.md und contract.yaml: der Autor muss erneut laufen."""
    drafts = ctx.run_dir / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "plan.codex.md").write_text("# halber Codex-Entwurf\n")
    ctx.agents.script("plan_agent", "Entwurf geschrieben")
    ctx.codex.script_artifacts(
        "plan", {"plan.md": "# Codex-Plan\n", "contract.yaml": "openapi: 3.1.0\n"}
    )
    _draft_stage(
        ctx,
        kind="plan",
        agent_name="plan_agent",
        task=PLAN_DRAFT_TASK,
        artifacts=("plan.md", "contract.yaml"),
    )
    assert len(ctx.codex.author_calls) == 1
    assert (drafts / "plan.codex.md").read_text() == "# Codex-Plan\n"
    assert (drafts / "contract.codex.yaml").is_file()


def test_codex_draft_failure_degrades_instead_of_escalating(ctx, caplog):
    ctx.agents.script("spec_agent", "Entwurf geschrieben")
    ctx.codex = MockCodexRunner()  # ohne die Vorrats-Entwürfe der Fixture
    ctx.codex.script_author_error("spec", CodexError("codex exec: Exit 1 — kaputt"))
    with caplog.at_level(logging.WARNING):
        result = spec_draft(ctx)
    marker = ctx.run_dir / "drafts" / "spec.codex.FAILED"
    assert "Exit 1 — kaputt" in marker.read_text()
    assert result.codex == ()
    assert result.claude  # der Claude-Entwurf trägt die Synthese allein
    assert "Codex" in caplog.text


def test_failed_codex_draft_is_not_retried_on_resume(ctx):
    drafts = ctx.run_dir / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "spec.codex.FAILED").write_text("codex exec: Exit 1\n")
    ctx.agents.script("spec_agent", "Entwurf geschrieben")
    result = spec_draft(ctx)
    assert ctx.codex.author_calls == []
    assert result.codex == ()
    assert result.claude


def test_missing_claude_draft_escalates(ctx):
    ctx.agents.file_writes.pop("spec_agent")  # Agent schreibt nichts
    ctx.agents.script("spec_agent", "behauptet fertig zu sein")
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    with pytest.raises(EscalationError, match="spec.md"):
        spec_draft(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).phase == "escalated"
    assert not (ctx.run_dir / "drafts" / "spec.claude.md").exists()


def test_stale_tracked_artifact_does_not_become_a_draft(ctx):
    """Altbestand eines gemergten Runs darf einen untätigen Autor nicht adeln."""
    from tests.conftest import git

    (ctx.repo / ".adw" / "spec.md").write_text("# Spec des VORHERIGEN Runs\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Spec")
    ctx.agents.file_writes.pop("spec_agent")
    ctx.agents.script("spec_agent", "behauptet fertig zu sein")
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    with pytest.raises(EscalationError, match="spec.md"):
        spec_draft(ctx)
    assert not (ctx.run_dir / "drafts" / "spec.claude.md").exists()


def test_crash_leftovers_in_the_checkout_do_not_block(ctx):
    """.adw/spec.md eines gecrashten Draft-Laufs ohne Draft-Datei im Run-Ordner."""
    (ctx.repo / ".adw" / "spec.md").write_text("# Rest eines gecrashten Laufs\n")
    ctx.agents.script("spec_agent", "Entwurf geschrieben")
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    result = spec_draft(ctx)
    assert (ctx.run_dir / "drafts" / "spec.claude.md").read_text() == "# Spec\nZiel: Demo\n"
    assert not (ctx.repo / ".adw" / "spec.md").exists()
    assert result.claude and result.codex


def test_protected_files_survive_the_draft_stage(ctx):
    config = ctx.repo / ".adw" / "config.yaml"
    issue = ctx.repo / ".adw" / "issue.md"
    issue.write_text("# Issue\nISSUE-1\n")
    protected = {config: config.read_bytes(), issue: issue.read_bytes()}
    ctx.agents.script_files(
        "spec_agent",
        {
            ".adw/spec.md": "# Spec\nZiel: Demo\n",
            ".adw/config.yaml": "base_branch: gekapert\n",
            ".adw/issue.md": "# gefälschtes Issue\n",
        },
    )
    ctx.agents.script("spec_agent", "Entwurf geschrieben")
    ctx.codex.script_artifacts("spec", {"spec.md": "# Codex-Spec\n"})
    spec_draft(ctx, protected=protected)
    assert config.read_bytes() == protected[config]
    assert issue.read_bytes() == protected[issue]


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
  provider: gitlab
  staging_job: deploy-staging
"""


def prepare_approved(ctx):
    """Brings the context into the 'build' state (spec/plan done, approved)."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
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
    """Regression: a resume with committed artifacts + dirty non-.adw files
    must not fail on an empty seeding commit."""
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
    """Regression: the build agent must not silently change the approved contract."""
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
    """Regression: session ID/iteration must be on disk BEFORE the (long) Gates."""
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
    """Regression: Gates must see the approved contract, not the agent's version."""
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
    """Regression: a resume iteration without any implementation change must not
    pass as a success."""
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
    """Regression: a crash at iteration 10 must not allow an 11th attempt after resume."""
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
    """Regression: --no-approval must live in the state — a resume must not pause."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
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
    """Regression: --no-approval must be on disk BEFORE the first agent run."""
    ctx.skip_approval = True
    with pytest.raises(AssertionError):  # kein Script = simulierter Crash im Spec-Agent
        run_spec_and_plan(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).skip_approval is True


def test_completed_lane_is_not_rebuilt_on_resume(ctx):
    """Regression: a crash before the phase transition must not rebuild a finished Lane."""
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
    """Regression: a crash between the commit and the completed flag must not rebuild."""
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
    """Regression: spec/plan agents must not be able to write into .adw/runs/ (state!)."""
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
    """Regression: only the orchestrator commit (sentinel) proves green Gates."""
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
    """Regression: after a crash between Gate fail and fix run, the resume must
    still know the Gate feedback and the Circuit-Breaker baseline."""
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
    """Regression: commit messages are forgeable — only persisted gates_passed counts."""
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
    """Regression: the build agent must not commit the Gate configuration along."""
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
    """Regression: no persisted intermediate state after a Gate fail without pending_task."""
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


# --- 10d: RED-Gate im Initial-Build ---------------------------------------

# Beide Gates sind rot, solange impl.py fehlt — nur das zweite ist als
# RED-Beweis markiert. Damit zeigt der Gate-Output, WELCHE Gates im RED-Check
# gelaufen sind (fail fast würde sonst zuerst das lint-Gate melden).
TDD_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - name: lint
        cmd: "sh -c 'test -f impl.py || { echo LINT-ROT; exit 1; }'"
        timeout: 10
      - name: pytest
        cmd: "sh -c 'test -f impl.py || { echo TESTS-ROT; exit 1; }'"
        timeout: 10
        tdd: true
"""

GREEN_TDD_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pytest, cmd: "true", timeout: 10, tdd: true}
"""

TEST_FILES = {"test_feature.py": "def test_feature(): assert impl\n"}
IMPL_FILES = {"impl.py": "impl = 1\n"}


def prepare_tdd_lane(ctx, target_repo, config: str = TDD_CONFIG) -> None:
    write_config(target_repo, config)
    ctx.config = AdwConfig.load(target_repo)
    prepare_approved(ctx)


def files_per_build_call(ctx, *file_sets):
    """Je Build-Agent-Lauf ein eigener Datei-Satz (Test-Lauf, dann Implementierung).

    run() zeichnet den Call VOR dem file_writes-Lookup auf — beim ersten Lauf
    steht der Zähler also schon auf 1."""

    def choose(_cwd):
        calls = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
        return file_sets[min(calls, len(file_sets)) - 1]

    return choose


def build_calls(ctx):
    return [c for c in ctx.agents.calls if c.agent == "build_agent"]


def test_initial_build_proves_red_before_implementing(ctx, target_repo):
    prepare_tdd_lane(ctx, target_repo)
    ctx.agents.file_writes["build_agent"] = files_per_build_call(ctx, TEST_FILES, IMPL_FILES)
    ctx.agents.script("build_agent", "Tests geschrieben", "implementiert")
    run_build_phase(ctx)
    calls = build_calls(ctx)
    assert len(calls) == 2
    assert "ONLY the tests" in calls[0].task
    assert calls[0].resume is None
    # Implementierung setzt DIESELBE Session fort und kennt den roten Gate-Output:
    assert "RED confirmed" in calls[1].task
    assert "TESTS-ROT" in calls[1].task
    assert "LINT-ROT" not in calls[1].task  # im RED-Check laufen nur tdd-Gates
    assert calls[1].resume == ctx.state.lanes["backend"].session_id
    lane = ctx.state.lanes["backend"]
    assert lane.red_confirmed is True
    assert lane.gate_iterations == 1  # der RED-Check verbraucht keine Iteration
    assert ctx.state.phase == "codex_review"
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    assert (worktree / "test_feature.py").is_file()
    assert (worktree / "impl.py").is_file()


def test_green_gates_after_the_test_run_escalate(ctx, target_repo):
    """Grün nach reinem Test-Lauf = kein RED-Beweis — kein Retry, Eskalation."""
    prepare_tdd_lane(ctx, target_repo, GREEN_TDD_CONFIG)
    ctx.agents.script_files("build_agent", dict(TEST_FILES))
    ctx.agents.script("build_agent", "Tests geschrieben")
    with pytest.raises(EscalationError, match="RED"):
        run_build_phase(ctx)
    assert len(build_calls(ctx)) == 1
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "escalated"
    assert saved.lanes["backend"].red_confirmed is False


def test_test_only_run_without_changes_escalates(ctx, target_repo):
    prepare_tdd_lane(ctx, target_repo)
    ctx.agents.file_writes.pop("build_agent", None)
    ctx.agents.script("build_agent", "behauptet Tests geschrieben zu haben")
    with pytest.raises(EscalationError, match="Test-Lauf"):
        run_build_phase(ctx)
    assert len(build_calls(ctx)) == 1


def test_deleted_files_in_the_test_run_are_no_red_proof(ctx, target_repo):
    """Rote Gates durch gelöschte Dateien wären ein gefälschter RED-Beweis."""
    from pathlib import Path

    class DeletingAgentRunner(MockAgentRunner):
        def run(self, agent, task, cwd, resume=None, deny_read_paths=None):
            result = super().run(agent, task, cwd, resume, deny_read_paths)
            if agent.name == "build_agent":
                (Path(cwd) / "README.md").unlink()  # getrackte Datei aus dem Base-Branch
            return result

    deleting = DeletingAgentRunner()
    deleting.scripts = ctx.agents.scripts
    deleting.file_writes = ctx.agents.file_writes
    ctx.agents = deleting
    prepare_tdd_lane(ctx, target_repo)
    ctx.agents.script_files("build_agent", dict(TEST_FILES))
    ctx.agents.script("build_agent", "Tests geschrieben (und aufgeräumt)")
    with pytest.raises(EscalationError, match="gelöscht"):
        run_build_phase(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.lanes["backend"].red_confirmed is False


def test_implementation_deleting_the_red_tests_escalates(ctx, target_repo):
    """Grüne Gates ohne die Tests, die RED bewiesen haben, sind kein Ergebnis."""
    from pathlib import Path

    class DeletingImplRunner(MockAgentRunner):
        def run(self, agent, task, cwd, resume=None, deny_read_paths=None):
            result = super().run(agent, task, cwd, resume, deny_read_paths)
            if agent.name == "build_agent" and "RED confirmed" in task:
                (Path(cwd) / "test_feature.py").unlink()
            return result

    deleting = DeletingImplRunner()
    deleting.scripts = ctx.agents.scripts
    deleting.file_writes = ctx.agents.file_writes
    ctx.agents = deleting
    prepare_tdd_lane(ctx, target_repo)
    ctx.agents.file_writes["build_agent"] = files_per_build_call(ctx, TEST_FILES, IMPL_FILES)
    ctx.agents.script("build_agent", "Tests geschrieben", "implementiert (und Tests entsorgt)")
    with pytest.raises(EscalationError, match="RED-Tests"):
        run_build_phase(ctx)


def test_resume_without_any_test_changes_does_not_prove_red(ctx, target_repo):
    """Crash zwischen Session-Checkpoint und Idle-Check: ein unveränderter
    Worktree darf auch nach dem Resume kein RED beweisen."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for

    prepare_tdd_lane(ctx, target_repo)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        session_id="mock-session-build_agent-1",  # Checkpoint, aber keine Tests
    )
    ctx.agents.script("build_agent", "darf nicht laufen")
    with pytest.raises(EscalationError, match="Test-Lauf"):
        run_build_phase(ctx)
    assert build_calls(ctx) == []
    assert RunState.load(ctx.repo, ctx.state.run_id).lanes["backend"].red_confirmed is False


def test_lane_without_tdd_gate_builds_in_a_single_pass(ctx):
    """Ohne markiertes Gate bleibt der Build genau wie vor dem RED-Gate."""
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    calls = build_calls(ctx)
    assert len(calls) == 1
    assert "Implement the Workstream" in calls[0].task
    assert ctx.state.lanes["backend"].red_confirmed is False


def test_fix_dispatch_skips_the_red_stage(ctx, target_repo):
    """Review-/E2E-Fix-Dispatches (pending_task gesetzt) bekommen keine RED-Stufe."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for

    prepare_tdd_lane(ctx, target_repo)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        pending_task="Review Findings for your Lane. Fix the root causes.",
    )
    ctx.agents.script_files("build_agent", dict(IMPL_FILES))
    ctx.agents.script("build_agent", "gefixt")
    run_build_phase(ctx)
    calls = build_calls(ctx)
    assert len(calls) == 1
    assert calls[0].task.startswith("Review Findings")
    assert ctx.state.lanes["backend"].red_confirmed is False


def test_resume_after_the_test_run_repeats_only_the_red_check(ctx, target_repo):
    """Crash zwischen Test-Lauf und RED-Check: kein zweiter Test-Lauf."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for

    prepare_tdd_lane(ctx, target_repo)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    for name, content in TEST_FILES.items():
        (worktree / name).write_text(content)
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        session_id="mock-session-build_agent-1",  # Checkpoint nach dem Test-Lauf
    )
    ctx.agents.script_files("build_agent", dict(IMPL_FILES))
    ctx.agents.script("build_agent", "implementiert")
    run_build_phase(ctx)
    calls = build_calls(ctx)
    assert len(calls) == 1
    assert "RED confirmed" in calls[0].task
    assert calls[0].resume == "mock-session-build_agent-1"
    assert ctx.state.lanes["backend"].red_confirmed is True


def test_resume_after_red_confirmed_goes_straight_to_the_implementation(ctx, target_repo):
    """Crash nach dem RED-Beweis: der Resume implementiert nur noch."""
    from adw.state import LaneState
    from adw.worktrees import create_lane_worktree, ports_for

    prepare_tdd_lane(ctx, target_repo)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, "backend", "staging")
    for name, content in TEST_FILES.items():
        (worktree / name).write_text(content)
    ctx.state.lanes["backend"] = LaneState(
        worktree=str(worktree),
        branch=f"adw/{ctx.state.run_id}/backend",
        ports=ports_for(ctx.state.run_id, "backend"),
        session_id="mock-session-build_agent-1",
        red_confirmed=True,
        pending_task="RED confirmed — implementiere jetzt minimal.",
    )
    ctx.agents.script_files("build_agent", dict(IMPL_FILES))
    ctx.agents.script("build_agent", "implementiert")
    run_build_phase(ctx)
    calls = build_calls(ctx)
    assert len(calls) == 1
    assert calls[0].task == "RED confirmed — implementiere jetzt minimal."
    assert ctx.state.phase == "codex_review"


def test_red_check_does_not_consume_a_gate_iteration(ctx, target_repo, monkeypatch):
    prepare_tdd_lane(ctx, target_repo)
    ctx.agents.file_writes["build_agent"] = files_per_build_call(ctx, TEST_FILES, IMPL_FILES)
    ctx.agents.script("build_agent", "Tests geschrieben", "implementiert")

    saves = []
    original_save = RunState.save

    def spy(self, repo):
        lane = self.lanes.get("backend")
        if lane is not None:
            saves.append((lane.red_confirmed, lane.gate_iterations))
        return original_save(self, repo)

    monkeypatch.setattr(RunState, "save", spy)
    run_build_phase(ctx)
    after_red = [iterations for confirmed, iterations in saves if confirmed]
    assert after_red and after_red[0] == 0  # RED-Beweis VOR der ersten Iteration
    assert ctx.state.lanes["backend"].gate_iterations == 1


def test_untracked_config_created_by_agent_is_not_committed(tmp_path):
    """Regression: if config.yaml is untracked, the agent must not smuggle in its own."""
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
    script_authoring_agents(agents)
    agents.script("spec_synthesis", "Spec")
    agents.script("plan_synthesis", "Plan")
    agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/config.yaml": "base_branch: main  # eingeschleust\n"},
    )
    agents.script("build_agent", "gebaut")
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
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
    """Regression: a git error during config restore must not count as 'untracked'."""
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
    """Regression: gates_passed proves the tree AT THAT TIME — a changed tree
    must go through agent/Gates again instead of being committed blindly."""
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
    """Regression: a git error during the absence check must not count as 'missing'."""
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
    """Regression: a crash during the plan phase must not strand the run in 'plan'."""
    ctx.agents.script("spec_synthesis", "Spec")  # Plan-Synthese NICHT gescriptet → Crash
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
    ctx.agents.script("plan_synthesis", "Plan (nach Resume)")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert resumed.state.phase == "awaiting_approval"
    assert (resumed.run_dir / "plan.md").is_file()


def test_restore_survives_deleted_adw_directory(ctx):
    """Regression: a deleted .adw in the Worktree must not crash the restoration."""
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
    """Regression: spec already archived, phase still 'plan' — resume must work."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
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
    ctx.agents.script("plan_synthesis", "Plan (Resume)")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert resumed.state.phase == "awaiting_approval"


def test_authoring_resume_keeps_session_and_review_feedback(ctx):
    """Regression: a crash in the spec fix cycle must not lose session + Codex feedback."""
    ctx.agents.script("spec_synthesis", "v1")  # Fix-Lauf NICHT gescriptet → Crash dort
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
    ctx.agents.script("spec_synthesis", "v2 nachgeschärft")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    spec_calls = [c for c in ctx.agents.calls if c.agent == "spec_synthesis"]
    # v1, gecrashter Fix-Versuch (vom Mock vor dem Raise aufgezeichnet), Resume-Fix
    assert len(spec_calls) == 3
    resumed_fix = spec_calls[2]
    # Der Resume-Lauf setzt die ALTE Session fort und trägt das Codex-Feedback:
    assert resumed_fix.resume == "mock-session-spec_synthesis-2"
    assert "Akzeptanzkriterien fehlen" in resumed_fix.task


def test_config_restore_checkout_runs_without_repo_hooks(ctx, tmp_path):
    """Regression: post-checkout hooks must not fire during the config restore."""
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
    """Regression: if the agent commits itself (Bash), the orchestrator must escalate."""
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
    """Regression: if an archived artifact is missing, no Lane may build against it."""
    prepare_approved(ctx)
    (ctx.run_dir / "contract.yaml").unlink()
    ctx.agents.script_files("build_agent", {"neu.py": "pass\n"})
    ctx.agents.script("build_agent", "gebaut")
    with pytest.raises(EscalationError, match="contract"):
        run_build_phase(ctx)


def test_completed_lane_with_tampered_tree_is_not_skipped(ctx):
    """Regression: completed + a changed tree must not be passed on unchecked."""
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
    """Regression: unsaved user edits to tracked artifacts must not be
    discarded by the archiving."""
    from tests.conftest import git

    spec_path = ctx.repo / ".adw" / "spec.md"
    spec_path.write_text("# Gemergte Spec\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Spec")
    spec_path.write_text("# Gemergte Spec\n\nUNGESPEICHERTE NOTIZ DES NUTZERS\n")
    ctx.agents.script("spec_synthesis", "egal")
    with pytest.raises(EscalationError, match="uncommittete|ungespeichert|Änderungen"):
        run_spec_and_plan(ctx)
    assert "UNGESPEICHERTE NOTIZ" in spec_path.read_text()


def test_uncommitted_edits_on_a_tracked_summary_fail_fast(ctx):
    """Die Summary wird wie ein Artefakt archiviert (git checkout --) — ein
    getrackter Nutzer-Edit darf dabei nicht verloren gehen."""
    from tests.conftest import git

    summary = ctx.repo / ".adw" / "spec-summary.md"
    summary.write_text("# Zusammenfassung\n")
    git(ctx.repo, "add", ".adw/spec-summary.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Zusammenfassung")
    summary.write_text("# Zusammenfassung\n\nUNGESPEICHERTE NOTIZ DES NUTZERS\n")
    ctx.agents.script("spec_synthesis", "egal")
    with pytest.raises(EscalationError, match="spec-summary.md"):
        run_spec_and_plan(ctx)
    assert "UNGESPEICHERTE NOTIZ" in summary.read_text()


def test_directory_shaped_injected_config_is_removed_safely(tmp_path):
    """Regression: a directory instead of config.yaml must not crash the run."""
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
    script_authoring_agents(agents)
    agents.script("spec_synthesis", "Spec")
    agents.script("plan_synthesis", "Plan")
    agents.script_files(
        "build_agent",
        {"neu.py": "pass\n", ".adw/config.yaml/eingeschleust.txt": "böse\n"},
    )
    agents.script("build_agent", "gebaut")
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
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
    """Regression: crash in the middle of archiving — the reviewed (archived)
    spec beats an old tracked spec restored via git."""
    seen_specs = []

    class SpyCodex(MockCodexRunner):
        def review(self, kind, content_refs, cwd, context=None):
            seen_specs.append((kind, (cwd / ".adw" / "spec.md").read_text()))
            return super().review(kind, content_refs, cwd, context)

    ctx.codex = SpyCodex()
    script_draft_artifacts(ctx.codex)
    # Crash-Fenster nachbauen: Phase 'plan', archivierte reviewte Spec im
    # Run-Ordner, aber .adw/spec.md trägt die ALTE (getrackte) Version.
    ctx.state.phase = "plan"
    ctx.state.save(ctx.repo)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    (ctx.run_dir / "spec.md").write_text("# REVIEWTE Spec\n")
    (ctx.run_dir / "spec-summary.md").write_text("# Zusammenfassung\n")
    (ctx.repo / ".adw" / "spec.md").write_text("# ALTE getrackte Spec\n")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    plan_reviews = [spec for kind, spec in seen_specs if kind == "plan"]
    assert plan_reviews and all("REVIEWTE" in spec for spec in plan_reviews)
    assert (ctx.run_dir / "spec.md").read_text() == "# REVIEWTE Spec\n"


def test_uncommitted_edits_guard_also_runs_on_plan_resume(ctx):
    """Regression: the data-loss guard must also apply on a resume in 'plan'."""
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
    ctx.agents.script("plan_synthesis", "egal")
    with pytest.raises(EscalationError, match="uncommittete|Änderungen"):
        run_spec_and_plan(ctx)
    assert "UNGESPEICHERTE NOTIZ" in plan_path.read_text()


def test_config_restore_uses_pinned_fork_point_not_moving_base(ctx):
    """Regression: if the base branch advances, the Lane must not import its NEW
    config — restoration happens from the Lane's fork point."""
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
    """Regression: a symlink instead of an artifact must neither overwrite foreign
    files nor bypass the restoration."""
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
    """Regression: if the agent commits and the orchestrator crashes before that,
    the resume must detect the foreign HEAD."""
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
    """Regression: git switch --detach at the same commit must not go unnoticed."""
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
    """Regression: .adw as a symlink must not let the orchestrator write outside the Lane."""
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
    """Regression: config.yaml as a symlink to a directory must not crash rmtree."""
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
    script_authoring_agents(agents)
    agents.script("spec_synthesis", "Spec")
    agents.script("plan_synthesis", "Plan")

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
    script_draft_artifacts(codex)
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
    """Regression: our OWN new spec over an old tracked one is not a user edit."""
    from tests.conftest import git

    spec_path = ctx.repo / ".adw" / "spec.md"
    spec_path.write_text("# Spec des VORHERIGEN Runs (gemergt)\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "adw(alt): Spec")
    ctx.agents.script("spec_synthesis", "Spec")  # Plan-Synthese unscripted → Crash dort
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
    ctx.agents.script("plan_synthesis", "Plan (Resume)")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):  # NICHT EscalationError durch den Guard
        run_spec_and_plan(resumed)
    assert (resumed.run_dir / "spec.md").read_text() == SPEC_SYNTHESIS_FILES[".adw/spec.md"]


def test_file_shaped_adw_directory_is_replaced(ctx):
    """Regression: .adw as a FILE must not crash mkdir with FileExistsError."""
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
    script_authoring_agents(agents)
    agents.script("spec_synthesis", "Spec")
    agents.script("plan_synthesis", "Plan")
    agents.script_files("build_agent", {"gebaut.py": "pass\n"})
    agents.script("build_agent", "Lane 1", "Lane 2")
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
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


# --- 10d: Integration + E2E -------------------------------------------------

E2E_MARKER_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
  frontend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
e2e:
  cmd: "sh -c 'test -f e2e-fixed || { echo E2E-KAPUTT; exit 1; }'"
  timeout: 60
ci:
  provider: gitlab
  staging_job: deploy-staging
"""


def triage_json(lane: str, issue: str = "Button kaputt") -> str:
    return json.dumps(
        {
            "verdict": "needs_fixes",
            "findings": [
                {
                    "severity": "P1",
                    "lane": lane,
                    "file": "irgendwo.js",
                    "issue": issue,
                    "remediation_plan": ["Ursache fixen"],
                }
            ],
        }
    )


@pytest.fixture
def pctx(target_repo):
    """Parallel context: two Lanes + E2E config, authoring agents scripted."""
    write_config(target_repo, PARALLEL_CONFIG)
    agents = MockAgentRunner()
    script_authoring_agents(agents)
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
    state = RunState.new(issue="ISSUE-2: parallel", parallel=True)
    return RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=agents,
        codex=codex,
        skip_approval=True,
    )


def prepare_built_parallel(pctx, lane_files=None, build_responses=2):
    """Brings the parallel context up to phase='integration' (Lanes built)."""
    pctx.agents.script("spec_synthesis", "Spec")
    pctx.agents.script("plan_synthesis", "Plan")
    pctx.codex.script(OK, OK)
    pctx.agents.file_writes["build_agent"] = lane_files or (
        lambda cwd: {f"{cwd.name}.py": f"# {cwd.name}\n"}
    )
    pctx.agents.script("build_agent", *["gebaut"] * build_responses)
    run_spec_and_plan(pctx)
    run_build_phase(pctx)
    assert pctx.state.phase == "integration"


def test_integration_merges_lane_branches(pctx):
    prepare_built_parallel(pctx)
    run_integration_phase(pctx)
    worktree = pctx.repo / ".adw" / "runs" / pctx.state.run_id / "trees" / "integration"
    assert (worktree / "backend.py").is_file()
    assert (worktree / "frontend.py").is_file()
    assert pctx.state.phase == "codex_review"
    saved = RunState.load(pctx.repo, pctx.state.run_id)
    assert saved.phase == "codex_review"


def test_merge_conflict_escalates(pctx):
    prepare_built_parallel(pctx, lane_files=lambda cwd: {"same.txt": f"Version {cwd.name}\n"})
    with pytest.raises(EscalationError, match="[Mm]erge"):
        run_integration_phase(pctx)
    assert (pctx.run_dir / "escalation.md").is_file()
    assert RunState.load(pctx.repo, pctx.state.run_id).phase == "escalated"


def test_e2e_failure_routes_triage_findings_to_the_named_lane(pctx, target_repo):
    write_config(target_repo, E2E_MARKER_CONFIG)
    pctx.config = AdwConfig.load(target_repo)

    def writes(cwd):
        if cwd.name != "frontend":
            return {"backend.py": "pass\n"}
        fix_runs = [c for c in pctx.agents.calls if c.agent == "build_agent" and c.cwd == cwd]
        if len(fix_runs) >= 2:  # zweiter Lauf in der frontend-Lane = E2E-Fix
            return {"e2e-fixed": "ok\n"}
        return {"frontend.js": "v1\n"}

    prepare_built_parallel(pctx, lane_files=writes, build_responses=3)
    pctx.agents.script("e2e_triage", triage_json("frontend"))
    run_integration_phase(pctx)

    triage_calls = [c for c in pctx.agents.calls if c.agent == "e2e_triage"]
    assert len(triage_calls) == 1
    assert "E2E-KAPUTT" in triage_calls[0].task
    frontend_calls = [
        c for c in pctx.agents.calls if c.agent == "build_agent" and c.cwd.name == "frontend"
    ]
    backend_calls = [
        c for c in pctx.agents.calls if c.agent == "build_agent" and c.cwd.name == "backend"
    ]
    assert len(frontend_calls) == 2  # Build + E2E-Fix
    assert len(backend_calls) == 1  # Backend bleibt unangetastet
    assert "Button kaputt" in frontend_calls[1].task
    assert frontend_calls[1].resume == frontend_calls[0].resume or frontend_calls[1].resume
    worktree = pctx.repo / ".adw" / "runs" / pctx.state.run_id / "trees" / "integration"
    assert (worktree / "e2e-fixed").is_file()
    assert pctx.state.phase == "codex_review"


def test_identical_e2e_failures_trigger_circuit_breaker(pctx, target_repo):
    write_config(
        target_repo,
        PARALLEL_CONFIG.replace(
            'cmd: "true"\n  timeout: 60',
            "cmd: \"sh -c 'echo IMMER-GLEICH; exit 1'\"\n  timeout: 60",
        ),
    )
    pctx.config = AdwConfig.load(target_repo)

    def writes(cwd):
        return {f"datei-{len(pctx.agents.calls)}.txt": "x\n"}

    prepare_built_parallel(pctx, lane_files=writes, build_responses=6)
    pctx.agents.script("e2e_triage", triage_json("backend"), triage_json("backend"))
    with pytest.raises(EscalationError, match="Circuit|unverändert"):
        run_integration_phase(pctx)
    triage_calls = [c for c in pctx.agents.calls if c.agent == "e2e_triage"]
    assert len(triage_calls) == 1  # zweite identische Runde eskaliert VOR der Triage


def test_e2e_round_limit_escalates(pctx, target_repo):
    # Zähler lebt AUSSERHALB des Integration-Worktrees (der wird je Runde neu
    # aufgebaut) — so variiert der Output und der Circuit-Breaker greift nicht.
    write_config(
        target_repo,
        PARALLEL_CONFIG.replace(
            'cmd: "true"\n  timeout: 60',
            "cmd: \"sh -c 'cat ../../zaehler 2>/dev/null | wc -l; "
            "echo x >> ../../zaehler; exit 1'\"\n  timeout: 60",
        ),
    )
    pctx.config = AdwConfig.load(target_repo)

    def writes(cwd):
        return {f"datei-{len(pctx.agents.calls)}.txt": "x\n"}

    prepare_built_parallel(pctx, lane_files=writes, build_responses=30)
    pctx.agents.script("e2e_triage", *[triage_json("backend", f"Runde {i}") for i in range(12)])
    with pytest.raises(EscalationError, match="Runden|[Ll]imit"):
        run_integration_phase(pctx)
    triage_calls = [c for c in pctx.agents.calls if c.agent == "e2e_triage"]
    assert len(triage_calls) == 9  # 10 E2E-Läufe, nach dem 10. keine Triage mehr


def test_unparseable_e2e_triage_output_escalates(pctx, target_repo):
    write_config(target_repo, E2E_MARKER_CONFIG)
    pctx.config = AdwConfig.load(target_repo)
    prepare_built_parallel(pctx)
    pctx.agents.script("e2e_triage", "Ich glaube das Frontend ist kaputt (kein JSON)")
    with pytest.raises(EscalationError, match="[Tt]riage"):
        run_integration_phase(pctx)


def test_integration_round_counter_survives_resume(pctx, target_repo):
    write_config(target_repo, E2E_MARKER_CONFIG)
    pctx.config = AdwConfig.load(target_repo)
    prepare_built_parallel(pctx)
    pctx.state.integration_rounds = 9  # persistierter Stand eines gecrashten Runs
    pctx.save()
    with pytest.raises(EscalationError, match="Runden|[Ll]imit"):
        run_integration_phase(pctx)
    triage_calls = [c for c in pctx.agents.calls if c.agent == "e2e_triage"]
    assert triage_calls == []  # Limit war schon erreicht — kein 11. Fix-Versuch


def test_exhausted_round_limit_escalates_before_any_new_e2e_run(pctx, target_repo):
    """Regression (Codex): a crash between the round save and the limit check must
    not start an 11th round on resume — limit check BEFORE merge/E2E."""
    write_config(target_repo, E2E_MARKER_CONFIG)
    pctx.config = AdwConfig.load(target_repo)
    prepare_built_parallel(pctx)
    calls_before = len(pctx.agents.calls)
    pctx.state.integration_rounds = 10
    pctx.save()
    with pytest.raises(EscalationError, match="Runden|[Ll]imit"):
        run_integration_phase(pctx)
    assert len(pctx.agents.calls) == calls_before  # kein Agent mehr gelaufen
    worktree = pctx.repo / ".adw" / "runs" / pctx.state.run_id / "trees" / "integration"
    assert not worktree.exists()  # nicht mal der Merge wurde neu aufgebaut


def test_merge_timeout_escalates_instead_of_crashing(pctx, monkeypatch):
    """Regression (Codex): TimeoutExpired during the merge must end up in the
    escalation path (report + phase=escalated), not as a raw traceback."""
    import subprocess as sp

    prepare_built_parallel(pctx)
    real_run = sp.run

    def slow_merge(argv, *args, **kwargs):
        if isinstance(argv, list) and "merge" in argv and "--abort" not in argv:
            raise sp.TimeoutExpired(cmd=argv, timeout=300)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr("adw.phases.subprocess.run", slow_merge)
    with pytest.raises(EscalationError, match="[Mm]erge"):
        run_integration_phase(pctx)
    assert (pctx.run_dir / "escalation.md").is_file()
    assert RunState.load(pctx.repo, pctx.state.run_id).phase == "escalated"


# --- 10e: Codex-Code-Review + finaler Review + Triage ------------------------


def code_finding(lane: str, issue: str, category: str | None = None) -> ReviewResult:
    return ReviewResult(
        verdict="needs_fixes",
        findings=[
            Finding(
                severity="P1",
                lane=lane,
                file="src_neu.py",
                issue=issue,
                remediation_plan=["Ursache beheben"],
                category=category,
            )
        ],
    )


def final_review_json(verdict: str = "ok", findings: list[dict] | None = None) -> str:
    return json.dumps({"verdict": verdict, "findings": findings or []})


def finding_dict(lane: str, issue: str, category: str, severity: str = "P2") -> dict:
    return {
        "severity": severity,
        "lane": lane,
        "file": "src_neu.py",
        "issue": issue,
        "remediation_plan": ["fixen"],
        "category": category,
    }


def prepare_reviewable(ctx):
    """Bring the single Lane up to phase='codex_review' (build fully committed)."""
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"src_neu.py": "print('v1')\n"})
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    assert ctx.state.phase == "codex_review"


def test_codex_code_review_ok_advances_to_final_review(ctx):
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    assert ctx.state.phase == "final_review"
    code_calls = [c for c in ctx.codex.calls if c.kind == "code"]
    assert len(code_calls) == 1
    assert code_calls[0].cwd.name == "backend"  # Single-Lane: Review im Lane-Worktree
    assert "src_neu.py" in code_calls[0].content_refs


def test_codex_code_findings_route_to_lane_and_rereview(ctx):
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "gefixt")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2, race gefixt')\n"}
    ctx.codex.script(code_finding("backend", "Race Condition beim Speichern"), OK)
    run_codex_review_phase(ctx)
    assert ctx.state.phase == "final_review"
    fix_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(fix_calls) == 2  # Build + Codex-Fix
    assert "Race Condition" in fix_calls[1].task
    code_calls = [c for c in ctx.codex.calls if c.kind == "code"]
    assert len(code_calls) == 2  # Re-Review nach dem Fix


def test_identical_codex_code_findings_escalate(ctx):
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "angeblich gefixt")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.codex.script(
        code_finding("backend", "immer dasselbe"),
        code_finding("backend", "immer dasselbe"),
    )
    with pytest.raises(EscalationError, match="Circuit|unverändert"):
        run_codex_review_phase(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).phase == "escalated"


def code_findings(*items: tuple[str, str]) -> ReviewResult:
    """Code-Review-Ergebnis aus (severity, issue)-Paaren."""
    return ReviewResult(
        verdict="needs_fixes",
        findings=[
            Finding(
                severity=severity,
                lane="backend",
                file="src_neu.py",
                issue=issue,
                remediation_plan=["Ursache beheben"],
            )
            for severity, issue in items
        ],
    )


def test_code_review_drops_p3_from_round_two_into_followups(ctx):
    """Review-Policy v2: ab Runde 2 sind P3-Findings nicht mehr actionable —
    sie werden dokumentiert statt gefixt und beenden den Loop."""
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "gefixt")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.codex.script(
        code_findings(("P1", "Race Condition")),
        code_findings(("P3", "Namensdetail")),
    )
    run_codex_review_phase(ctx)
    assert ctx.state.phase == "final_review"
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2  # Build + EIN Fix — für das P3 kein Fix-Lauf
    assert "Namensdetail" in (ctx.run_dir / "followups.md").read_text()


def test_code_review_drops_p2_from_round_three(ctx):
    """Ab Runde 3 ist nur noch P1 actionable."""
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "fix 1", "fix 2")
    ctx.agents.file_writes["build_agent"] = lambda cwd: {
        "src_neu.py": f"print('v{len(ctx.agents.calls)}')\n"
    }
    ctx.codex.script(
        code_findings(("P1", "Race Condition")),
        code_findings(("P1", "Nullpointer")),
        code_findings(("P2", "unsauberer Name")),
    )
    run_codex_review_phase(ctx)
    assert ctx.state.phase == "final_review"
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 3  # Build + 2 Fixes, das P2 der Runde 3 nicht mehr
    assert "unsauberer Name" in (ctx.run_dir / "followups.md").read_text()


def test_code_review_passes_prior_findings_with_dispositions_as_context(ctx):
    """Ab Runde 2 kennt Codex die Findings der Vorrunden inkl. Disposition."""
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "gefixt")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.codex.script(code_findings(("P1", "Race Condition")), OK)
    run_codex_review_phase(ctx)
    code_calls = [c for c in ctx.codex.calls if c.kind == "code"]
    assert code_calls[0].context is None  # Runde 1: Prompt unverändert
    context = code_calls[1].context
    assert "round 2 of max 5" in context
    assert "Race Condition" in context
    assert "fix dispatched (round 1)" in context


def test_review_context_survives_a_crash_and_is_cleared_on_transition(ctx):
    """Der Verlauf wird im selben Save wie review_rounds persistiert und beim
    Phasenübergang geleert."""
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "gefixt")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.codex.script(code_findings(("P1", "Race Condition")))  # Runde 2: Queue leer
    with pytest.raises(AssertionError):
        run_codex_review_phase(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.review_rounds == 1
    assert any("fix dispatched (round 1)" in line for line in saved.review_prior_context)

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=saved,
        agents=ctx.agents,
        codex=ctx.codex,
    )
    ctx.codex.script(OK)
    run_codex_review_phase(resumed)
    assert resumed.state.phase == "final_review"
    assert RunState.load(ctx.repo, ctx.state.run_id).review_prior_context == []


def test_review_round_increment_is_persisted_with_the_staged_fix(ctx, monkeypatch):
    """Regression (Codex P1): Runden-Zähler und Findings-Verlauf dürfen erst mit
    dem gestagten Fix-Task in EINEM Save landen. Sonst überspringt ein Crash in
    diesem Fenster die Runde samt Fix — die Severity-Schwelle steigt weiter und
    das Finding wäre still verloren."""
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "Fix")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.codex.script(code_findings(("P1", "Race Condition")), OK)
    snapshots = []
    real_save = RunState.save

    def spy(self, repo):
        real_save(self, repo)
        snapshots.append(self.model_dump())

    monkeypatch.setattr(RunState, "save", spy)
    run_codex_review_phase(ctx)
    first = next(s for s in snapshots if s["review_rounds"] == 1)
    assert first["lanes"]["backend"]["pending_task"]  # im selben Save gestaged
    assert first["review_prior_context"]  # Verlauf ebenfalls im selben Save
    # Und die Circuit-Breaker-Basis: ein Crash im Dispatch-Fenster darf ein
    # unverändertes Finding nicht eine Runde länger überleben lassen.
    assert first["review_last_failures"]


def test_circuit_breaker_ignores_findings_below_the_threshold(ctx):
    """Regression: gedroppte P2/P3 dürfen die Circuit-Breaker-Basis nicht
    auffüllen — sonst kaschiert ein neues P3 ein stehengebliebenes P1."""
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", "angeblich gefixt")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.codex.script(
        code_findings(("P1", "immer dasselbe")),
        code_findings(("P1", "immer dasselbe"), ("P3", "neue Kleinigkeit")),
    )
    with pytest.raises(EscalationError, match="Circuit|unverändert"):
        run_codex_review_phase(ctx)


def test_parallel_codex_review_runs_on_integration_worktree(pctx):
    prepare_built_parallel(pctx)
    run_integration_phase(pctx)
    pctx.codex.script(OK)
    run_codex_review_phase(pctx)
    code_calls = [c for c in pctx.codex.calls if c.kind == "code"]
    assert len(code_calls) == 1
    assert code_calls[0].cwd.name == "integration"
    assert pctx.state.phase == "final_review"


def test_final_review_ok_advances_to_ci(ctx):
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("final_reviewer", final_review_json("ok"))
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    reviewer_calls = [c for c in ctx.agents.calls if c.agent == "final_reviewer"]
    assert len(reviewer_calls) == 1
    assert "spec" in reviewer_calls[0].task.lower()


def test_scope_gap_creates_followup_report_instead_of_fix_cycle(ctx):
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script(
        "final_reviewer",
        final_review_json(
            "needs_fixes",
            [finding_dict("backend", "Reporting-Export fehlt komplett", "scope_gap")],
        ),
    )
    build_calls_before = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"  # kein Fix-Zyklus, Run läuft weiter
    followups = ctx.run_dir / "followups.md"
    assert followups.is_file()
    assert "Reporting-Export" in followups.read_text()
    build_calls_after = len([c for c in ctx.agents.calls if c.agent == "build_agent"])
    assert build_calls_after == build_calls_before  # kein Build-Agent gelaufen


def test_implementation_finding_triggers_lane_fix_cycle(ctx):
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "nachgebessert")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v3, validiert')\n"}
    ctx.agents.script(
        "final_reviewer",
        final_review_json(
            "needs_fixes",
            [finding_dict("backend", "Input wird nicht validiert", "implementation")],
        ),
        final_review_json("ok"),
    )
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    fix_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert "nicht validiert" in fix_calls[-1].task
    assert ctx.state.lanes["backend"].fix_cycles == 1
    reviewer_calls = [c for c in ctx.agents.calls if c.agent == "final_reviewer"]
    assert len(reviewer_calls) == 2  # Re-Review nach dem Fix


def test_three_fix_cycles_escalate(ctx):
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", *["Fix-Versuch"] * 5)

    def writes(cwd):
        return {f"fix-{len(ctx.agents.calls)}.py": "pass\n"}

    ctx.agents.file_writes["build_agent"] = writes
    ctx.agents.script(
        "final_reviewer",
        *[
            final_review_json(
                "needs_fixes",
                [finding_dict("backend", f"Problem Nummer {i}", "implementation")],
            )
            for i in range(5)
        ],
    )
    with pytest.raises(EscalationError, match="Fix-Zyklen|[Ll]imit"):
        run_final_review_phase(ctx)
    assert ctx.state.lanes["backend"].fix_cycles == 3  # 3 Zyklen, der 4. eskaliert


def test_unparseable_final_review_output_escalates(ctx):
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("final_reviewer", "Sieht insgesamt ganz gut aus! (kein JSON)")
    with pytest.raises(EscalationError, match="unlesbar|[Rr]eview"):
        run_final_review_phase(ctx)


def test_pending_lane_fix_is_resumed_before_codex_review(ctx):
    """Regression (Codex P1): a crash between fix Dispatch and Gates/commit must
    not hand an unchecked, uncommitted state to the review on resume."""
    from pathlib import Path

    from tests.conftest import git

    prepare_reviewable(ctx)
    lane = ctx.state.lanes["backend"]
    worktree = Path(lane.worktree)
    # Crash-Fenster simulieren: Fix dispatcht, Agent hat geschrieben,
    # Gates + Commit sind nie gelaufen.
    (worktree / "halbfertig.py").write_text("print('unvalidiert')\n")
    lane.completed = False
    lane.gates_passed = False
    lane.gates_tree = None
    lane.pending_task = "Offenes Codex-Finding: Race fixen"
    lane.gate_iterations = 0
    ctx.save()
    ctx.agents.script("build_agent", "Fix abgeschlossen")
    ctx.agents.file_writes["build_agent"] = {"halbfertig.py": "print('validiert')\n"}
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    fix_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert "Offenes Codex-Finding" in fix_calls[-1].task
    assert git(worktree, "status", "--porcelain") == ""  # committet VOR dem Review
    assert ctx.state.phase == "final_review"


def test_review_round_limit_escalates_without_terminal_fix_dispatch(ctx):
    """Regression (Codex P2): the review of the last round (cap 5) must not
    dispatch a fix that no review can ever check again."""
    prepare_reviewable(ctx)
    ctx.agents.script("build_agent", *["Fix"] * 12)

    def writes(cwd):
        return {f"fix-{len(ctx.agents.calls)}.py": "pass\n"}

    ctx.agents.file_writes["build_agent"] = writes
    ctx.codex.script(*[code_finding("backend", f"Problem {i}") for i in range(12)])
    with pytest.raises(EscalationError, match="Limit 5"):
        run_codex_review_phase(ctx)
    code_calls = [c for c in ctx.codex.calls if c.kind == "code"]
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(code_calls) == 5
    assert len(build_calls) == 1 + 4  # Build + 4 Fixes — kein 5. Terminal-Fix


def test_final_review_finding_for_inactive_lane_routes_to_active_lanes(ctx):
    """lane='frontend' in a single-Lane run is treated like 'unknown' and routed
    into the active Lanes — no KeyError, no escalation, no discarded
    Finding."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "Fix")
    ctx.agents.file_writes["build_agent"] = {"fix.py": "pass\n"}
    ctx.agents.script(
        "final_reviewer",
        final_review_json(
            "needs_fixes", [finding_dict("frontend", "FE-Teil fehlt", "implementation")]
        ),
        final_review_json("ok"),
    )
    run_final_review_phase(ctx)
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2  # Fix lief über die backend-Lane
    assert "FE-Teil fehlt" in build_calls[-1].task
    assert RunState.load(ctx.repo, ctx.state.run_id).phase != "escalated"


def test_identical_final_review_findings_trigger_circuit_breaker(ctx):
    """Regression (Codex P2): an identical Finding after one fix cycle must trip
    the Circuit-Breaker instead of exhausting all 3 cycles."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "Fix")

    def writes(cwd):
        return {f"fix-{len(ctx.agents.calls)}.py": "pass\n"}

    ctx.agents.file_writes["build_agent"] = writes
    same = finding_dict("backend", "genau dasselbe Problem", "implementation")
    ctx.agents.script(
        "final_reviewer",
        final_review_json("needs_fixes", [same]),
        final_review_json("needs_fixes", [same]),
    )
    with pytest.raises(EscalationError, match="Circuit|unverändert"):
        run_final_review_phase(ctx)
    assert ctx.state.lanes["backend"].fix_cycles == 1  # nicht bis 3 ausgereizt


def prepare_final_review_fix(ctx, findings: list[dict]) -> None:
    """Bring the run to phase='final_review' and script the reviewer with one
    round of ``findings``, then ok."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "Findings gesichtet")
    ctx.agents.script(
        "final_reviewer",
        final_review_json("needs_fixes", findings),
        final_review_json("ok"),
    )


def test_p3_only_fix_dispatch_without_changes_becomes_followup(ctx):
    """Regression (Run 8afec216): a P3 that demands no code change at all left
    the Worktree empty and escalated the whole run. It belongs in the
    follow-up report — the run continues."""
    prepare_final_review_fix(
        ctx, [finding_dict("backend", "Prozess-Hinweis, kein Code", "implementation", "P3")]
    )
    ctx.agents.file_writes.pop("build_agent", None)  # Agent ändert nichts
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    assert "Prozess-Hinweis" in (ctx.run_dir / "followups.md").read_text()


def test_deferred_p3_is_not_dispatched_again_on_re_review(ctx):
    """Regression (Codex P2): ein deterministischer Reviewer meldet das
    vertagte P3 erneut — es darf weder einen zweiten Fix-Lauf noch den
    Circuit-Breaker auslösen."""
    same = finding_dict("backend", "Prozess-Hinweis, kein Code", "implementation", "P3")
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "Findings gesichtet")
    ctx.agents.file_writes.pop("build_agent", None)
    ctx.agents.script(
        "final_reviewer",
        final_review_json("needs_fixes", [same]),
        final_review_json("needs_fixes", [same]),
    )
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2  # Build + EIN wirkungsloser Fix-Lauf, kein zweiter


def test_deferred_finding_returning_as_p2_is_fixed_after_all(ctx):
    """Regression (Codex P1): Vertagt wird die P3-BEWERTUNG, nicht die Datei.
    Stuft ein späterer Review dasselbe Problem hoch, muss es gefixt werden."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "gesichtet", "gefixt")
    fix_runs = []

    def writes(cwd):
        fix_runs.append(cwd)
        return {} if len(fix_runs) == 1 else {"src_neu.py": "print('v2')\n"}

    ctx.agents.file_writes["build_agent"] = writes
    ctx.agents.script(
        "final_reviewer",
        final_review_json(
            "needs_fixes", [finding_dict("backend", "dasselbe", "implementation", "P3")]
        ),
        final_review_json(
            "needs_fixes", [finding_dict("backend", "dasselbe", "implementation", "P2")]
        ),
        final_review_json("ok"),
    )
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    assert ctx.state.lanes["backend"].fix_cycles == 2  # P3 vertagt, P2 gefixt
    assert len(fix_runs) == 2


def test_fix_dispatch_without_changes_still_escalates_on_p2(ctx):
    """Bei P1/P2 ist Untätigkeit des Build-Agenten ein echtes Problem."""
    prepare_final_review_fix(
        ctx, [finding_dict("backend", "Race Condition", "implementation", "P2")]
    )
    ctx.agents.file_writes.pop("build_agent", None)
    with pytest.raises(EscalationError, match="keine Änderungen"):
        run_final_review_phase(ctx)


def test_p3_fix_dispatch_with_changes_takes_the_regular_path(ctx):
    prepare_final_review_fix(ctx, [finding_dict("backend", "Kleinigkeit", "implementation", "P3")])
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2, P3 erledigt')\n"}
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    assert not (ctx.run_dir / "followups.md").is_file()  # gefixt statt vertagt
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2  # Build + Fix-Lauf, Gates regulär gelaufen


def test_tampered_completed_lane_is_revalidated_before_review(ctx):
    """Regression (Codex P1): even 'completed' Lanes must pass the tree-hash
    revalidation before the review — otherwise the review consumes ungated changes."""
    from pathlib import Path

    from tests.conftest import git

    prepare_reviewable(ctx)
    lane = ctx.state.lanes["backend"]
    worktree = Path(lane.worktree)
    # Manipulation NACH dem completed-Checkpoint (z. B. Crash-Fenster/Fremdprozess)
    (worktree / "eingeschleust.py").write_text("print('ungated')\n")
    ctx.agents.script("build_agent", "neu gebaut")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    build_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert len(build_calls) == 2  # Lane ging zurück in den Loop (Gates + Commit)
    assert git(worktree, "status", "--porcelain") == ""  # nichts Uncommittetes im Review


def test_fix_cycle_increment_is_persisted_with_the_staged_fix(ctx, monkeypatch):
    """Regression (Codex P2): the counter increment and the staged fix task must
    land in the SAME save — otherwise a crash in between burns a fix cycle."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "Fix")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.agents.script(
        "final_reviewer",
        final_review_json("needs_fixes", [finding_dict("backend", "Problem", "implementation")]),
        final_review_json("ok"),
    )
    snapshots = []
    real_save = RunState.save

    def spy(self, repo):
        real_save(self, repo)
        snapshots.append(self.model_dump())

    monkeypatch.setattr(RunState, "save", spy)
    run_final_review_phase(ctx)
    first = next(s for s in snapshots if s["lanes"]["backend"]["fix_cycles"] == 1)
    assert first["lanes"]["backend"]["pending_task"]  # im selben Save gestaged
    assert first["lanes"]["backend"]["completed"] is False


def test_followups_are_not_duplicated_across_review_rounds(ctx):
    """Regression (Codex P2): a scope_gap that is deliberately not fixed must not
    reappear in the follow-up report on every review round."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("build_agent", "Fix")

    def writes(cwd):
        return {f"fix-{len(ctx.agents.calls)}.py": "pass\n"}

    ctx.agents.file_writes["build_agent"] = writes
    gap = finding_dict("backend", "Export-Feature fehlt", "scope_gap")
    ctx.agents.script(
        "final_reviewer",
        final_review_json("needs_fixes", [gap, finding_dict("backend", "Bug A", "implementation")]),
        final_review_json("needs_fixes", [gap]),
    )
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"
    text = (ctx.run_dir / "followups.md").read_text()
    assert text.count("Export-Feature fehlt") == 1


def test_review_fix_in_parallel_run_goes_back_through_e2e(pctx, target_repo):
    """Regression (Codex P1): a review fix in a parallel run must go through the
    E2E gate again — not just through the Lane Gates + re-merge."""
    write_config(
        target_repo,
        PARALLEL_CONFIG.replace(
            'cmd: "true"\n  timeout: 60',
            "cmd: \"sh -c 'test ! -f kaputt || { echo E2E-BRICHT; exit 1; }'\"\n  timeout: 60",
        ),
    )
    pctx.config = AdwConfig.load(target_repo)
    prepare_built_parallel(pctx, build_responses=6)
    run_integration_phase(pctx)  # E2E grün: 'kaputt' existiert noch nicht
    assert pctx.state.phase == "codex_review"
    # Codex-Fix bricht das Cross-Lane-Verhalten: die Lane-Gates bleiben grün,
    # aber E2E würde rot — das MUSS auffallen.
    pctx.agents.file_writes["build_agent"] = lambda cwd: {
        "kaputt": "bricht e2e\n",
        f"fix-{len(pctx.agents.calls)}.py": "pass\n",
    }
    pctx.codex.script(code_finding("backend", "Race beim Speichern"))
    pctx.agents.script("e2e_triage", triage_json("backend", "kaputt eingebaut"))
    with pytest.raises(EscalationError):
        run_codex_review_phase(pctx)
    triage_calls = [c for c in pctx.agents.calls if c.agent == "e2e_triage"]
    assert len(triage_calls) >= 1  # E2E lief nach dem Review-Fix und schlug an
    assert "E2E-BRICHT" in triage_calls[0].task


def test_final_review_finding_without_category_escalates(ctx):
    """Regression (Codex P2): a missing category field must not silently pass as
    'implementation' — the reviewer must answer in a triageable way."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    incomplete = {
        "severity": "P2",
        "lane": "backend",
        "file": "src_neu.py",
        "issue": "irgendwas",
        "remediation_plan": ["fixen"],
    }
    ctx.agents.script("final_reviewer", final_review_json("needs_fixes", [incomplete]))
    with pytest.raises(EscalationError, match="category"):
        run_final_review_phase(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).phase == "escalated"


def test_schema_invalid_final_review_output_escalates(ctx):
    """Regression (Codex P2): valid JSON with a schema violation (ValidationError)
    must escalate just like broken JSON — no raw traceback."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    broken = {
        "severity": "P9",  # ungültig
        "lane": "backend",
        "file": "src_neu.py",
        "issue": "x",
        "remediation_plan": ["y"],
        "category": "implementation",
    }
    ctx.agents.script("final_reviewer", final_review_json("needs_fixes", [broken]))
    with pytest.raises(EscalationError, match="unlesbar|[Rr]eview"):
        run_final_review_phase(ctx)
    assert (ctx.run_dir / "escalation.md").is_file()


def test_schema_invalid_e2e_triage_output_escalates(pctx, target_repo):
    write_config(target_repo, E2E_MARKER_CONFIG)
    pctx.config = AdwConfig.load(target_repo)
    prepare_built_parallel(pctx)
    broken = json.dumps({"verdict": "needs_fixes", "findings": [{"severity": "P9"}]})
    pctx.agents.script("e2e_triage", broken)
    with pytest.raises(EscalationError, match="[Tt]riage"):
        run_integration_phase(pctx)


# --- 10f: Push + CI + Log-Analyst ---------------------------------------------


class FakeGlab:
    """Scriptable glab responses: one pipeline outcome sequence, one per poll.

    Outcome "stale:<status>" simulates a terminal pipeline of an EARLIER
    push (wrong SHA); all other outcomes carry the real branch SHA."""

    def __init__(self, *outcomes: str, staging_job: str = "deploy-staging"):
        self.outcomes = list(outcomes)
        self.polls = 0
        self.staging_job = staging_job
        self.calls: list[list[str]] = []
        self.current = outcomes[0]

    def __call__(self, argv: list[str], cwd) -> str:
        self.calls.append(list(argv))
        if argv[0] == "api" and "/pipelines?" in argv[1]:
            outcome = self.outcomes[min(self.polls, len(self.outcomes) - 1)]
            self.polls += 1
            if outcome.startswith("stale:"):
                # Server-seitiger sha-Filter: für die frische SHA existiert
                # (noch) keine Pipeline — die alte wird gar nicht geliefert.
                self.current = outcome.removeprefix("stale:")
                return json.dumps([])
            self.current = outcome
            sha = re.search(r"sha=([0-9a-f]+)", argv[1]).group(1)
            return json.dumps([{"id": self.polls, "status": self.current, "sha": sha}])
        if argv[0] == "api":
            if self.current == "failed":
                return json.dumps([{"id": 5, "name": "pytest", "status": "failed"}])
            return json.dumps([{"id": 6, "name": self.staging_job, "status": "success"}])
        if argv[:2] == ["ci", "trace"]:
            return "CI-LOG: ImportError in backend/app.py"
        raise AssertionError(f"Unerwarteter glab-Aufruf: {argv}")


def add_bare_origin(repo):
    """Bare remote as origin — push target for tests."""
    from tests.conftest import git

    bare = repo.parent / "origin.git"
    subprocess_run = __import__("subprocess").run
    subprocess_run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(bare))
    return bare


def analyst_json(lane: str, issue: str = "ImportError in app.py") -> str:
    return triage_json(lane, issue)


def prepare_ci_ready(ctx):
    """Bring the single Lane up to phase='ci'."""
    prepare_reviewable(ctx)
    ctx.codex.script(OK)
    run_codex_review_phase(ctx)
    ctx.agents.script("final_reviewer", final_review_json("ok"))
    run_final_review_phase(ctx)
    assert ctx.state.phase == "ci"


def test_ci_green_completes_run(ctx, target_repo):
    from tests.conftest import git

    bare = add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.run_glab = FakeGlab("success")
    ctx.sleep = lambda s: None
    run_ci_phase(ctx)
    assert ctx.state.phase == "done"
    branches = git(bare, "branch", "--list")
    assert f"adw/{ctx.state.run_id}/backend" in branches  # Branch wurde gepusht


def test_ci_red_triggers_log_analyst_and_one_reentry(ctx, target_repo):
    bare = add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.agents.script("build_agent", "CI-Fix")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('ci-fix')\n"}
    ctx.agents.script("log_analyst", analyst_json("backend"))
    ctx.run_glab = FakeGlab("failed", "success")
    ctx.sleep = lambda s: None
    run_ci_phase(ctx)
    assert ctx.state.phase == "done"
    assert ctx.state.ci_reentries == 1
    analyst_calls = [c for c in ctx.agents.calls if c.agent == "log_analyst"]
    assert len(analyst_calls) == 1
    assert "CI-LOG" in analyst_calls[0].task  # Log-Excerpt ging an den Analyst
    fix_calls = [c for c in ctx.agents.calls if c.agent == "build_agent"]
    assert "ImportError" in fix_calls[-1].task
    assert bare.exists()


def test_second_ci_failure_escalates(ctx, target_repo):
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.agents.script("build_agent", "CI-Fix")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('v2')\n"}
    ctx.agents.script("log_analyst", analyst_json("backend"))
    ctx.run_glab = FakeGlab("failed", "failed")
    ctx.sleep = lambda s: None
    with pytest.raises(EscalationError, match="CI|Pipeline"):
        run_ci_phase(ctx)
    analyst_calls = [c for c in ctx.agents.calls if c.agent == "log_analyst"]
    assert len(analyst_calls) == 1  # genau EIN Re-Entry, dann Mensch
    assert (ctx.run_dir / "escalation.md").is_file()


def test_push_failure_escalates(ctx):
    prepare_ci_ready(ctx)  # KEIN origin-Remote angelegt
    ctx.run_glab = FakeGlab("success")
    ctx.sleep = lambda s: None
    with pytest.raises(EscalationError, match="[Pp]ush"):
        run_ci_phase(ctx)


def test_ci_timeout_escalates(ctx, target_repo):
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.run_glab = FakeGlab("running")
    ctx.sleep = lambda s: None
    with pytest.raises(EscalationError, match="[Tt]imeout|nicht abgeschlossen"):
        run_ci_phase(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).phase == "escalated"


def test_parallel_ci_pushes_integration_branch(pctx, target_repo):
    from tests.conftest import git

    bare = add_bare_origin(target_repo)
    prepare_built_parallel(pctx)
    run_integration_phase(pctx)
    pctx.codex.script(OK)
    run_codex_review_phase(pctx)
    pctx.agents.script("final_reviewer", final_review_json("ok"))
    run_final_review_phase(pctx)
    assert pctx.state.phase == "ci"
    pctx.run_glab = FakeGlab("success")
    pctx.sleep = lambda s: None
    run_ci_phase(pctx)
    assert pctx.state.phase == "done"
    branches = git(bare, "branch", "--list")
    assert f"adw/{pctx.state.run_id}/integration" in branches


def test_unparseable_log_analyst_output_escalates(ctx, target_repo):
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.agents.script("log_analyst", "Die Pipeline sieht kaputt aus (kein JSON)")
    ctx.run_glab = FakeGlab("failed", "failed")
    ctx.sleep = lambda s: None
    with pytest.raises(EscalationError, match="Log-Analyst"):
        run_ci_phase(ctx)


def test_ci_poll_ignores_stale_pipeline_from_previous_push(ctx, target_repo):
    """Regression (Codex P1): a terminal pipeline of the previous push (different
    SHA) must not judge the result of the fresh push."""
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.run_glab = FakeGlab("stale:failed", "success")
    ctx.sleep = lambda s: None
    run_ci_phase(ctx)
    assert ctx.state.phase == "done"
    analyst_calls = [c for c in ctx.agents.calls if c.agent == "log_analyst"]
    assert analyst_calls == []  # stale Fail wurde ignoriert, kein Re-Entry verbrannt
    assert ctx.state.ci_reentries == 0


def test_ci_reentry_counter_is_persisted_with_the_staged_fix(ctx, target_repo, monkeypatch):
    """Regression (Codex P2): the ci_reentries increment and the staged CI fix must
    land in the SAME save — otherwise a crash burns the only re-entry."""
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.agents.script("build_agent", "CI-Fix")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('ci-fix')\n"}
    ctx.agents.script("log_analyst", analyst_json("backend"))
    ctx.run_glab = FakeGlab("failed", "success")
    ctx.sleep = lambda s: None
    snapshots = []
    real_save = RunState.save

    def spy(self, repo):
        real_save(self, repo)
        snapshots.append(self.model_dump())

    monkeypatch.setattr(RunState, "save", spy)
    run_ci_phase(ctx)
    first = next(s for s in snapshots if s["ci_reentries"] == 1)
    assert first["lanes"]["backend"]["pending_task"]  # im selben Save gestaged
    assert first["lanes"]["backend"]["completed"] is False


def test_log_analyst_gets_schema_and_pushed_worktree(ctx, target_repo):
    """Regression (Codex P1+P2): the log analyst needs the exact
    Findings schema in the prompt and the PUSHED Worktree as cwd — not the
    base checkout of the main repo."""
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.agents.script("build_agent", "CI-Fix")
    ctx.agents.file_writes["build_agent"] = {"src_neu.py": "print('ci-fix')\n"}
    ctx.agents.script("log_analyst", analyst_json("backend"))
    ctx.run_glab = FakeGlab("failed", "success")
    ctx.sleep = lambda s: None
    run_ci_phase(ctx)
    analyst_call = next(c for c in ctx.agents.calls if c.agent == "log_analyst")
    assert '"remediation_plan"' in analyst_call.task  # exaktes Schema im Prompt
    assert '"verdict"' in analyst_call.task
    assert analyst_call.cwd.name == "backend"  # der gepushte Lane-Worktree


def test_triage_and_final_review_prompts_contain_schema(pctx, target_repo):
    write_config(target_repo, E2E_MARKER_CONFIG)
    pctx.config = AdwConfig.load(target_repo)

    def writes(cwd):
        if cwd.name != "frontend":
            return {"backend.py": "pass\n"}
        fix_runs = [c for c in pctx.agents.calls if c.agent == "build_agent" and c.cwd == cwd]
        if len(fix_runs) >= 2:
            return {"e2e-fixed": "ok\n"}
        return {"frontend.js": "v1\n"}

    prepare_built_parallel(pctx, lane_files=writes, build_responses=3)
    pctx.agents.script("e2e_triage", triage_json("frontend"))
    run_integration_phase(pctx)
    triage_call = next(c for c in pctx.agents.calls if c.agent == "e2e_triage")
    assert '"remediation_plan"' in triage_call.task
    pctx.codex.script(OK)
    run_codex_review_phase(pctx)
    pctx.agents.script("final_reviewer", final_review_json("ok"))
    run_final_review_phase(pctx)
    reviewer_call = next(c for c in pctx.agents.calls if c.agent == "final_reviewer")
    assert '"remediation_plan"' in reviewer_call.task
    assert "category" in reviewer_call.task


def test_ci_failure_without_logs_escalates_directly(ctx, target_repo):
    """Regression (Codex P2): a red pipeline WITHOUT usable logs (canceled,
    YAML error) must not trigger an analyst run on zero evidence."""
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.run_glab = FakeGlab("canceled")
    ctx.sleep = lambda s: None
    with pytest.raises(EscalationError, match="[Ll]og|rot"):
        run_ci_phase(ctx)
    analyst_calls = [c for c in ctx.agents.calls if c.agent == "log_analyst"]
    assert analyst_calls == []  # kein Fix auf Basis von nichts
    assert ctx.state.ci_reentries == 0  # Re-Entry-Budget nicht verbrannt


# --- Forge-Dispatch: GitHub-Projekte ------------------------------------------


class FakeGhPhases:
    """gh fake at the phase level: green workflow runs + staging job."""

    def __init__(self, staging_job: str = "deploy-staging"):
        self.staging_job = staging_job
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], cwd) -> str:
        self.calls.append(list(argv))
        if argv[0] == "api" and "/actions/runs?" in argv[1]:
            return json.dumps(
                {
                    "total_count": 1,
                    "workflow_runs": [{"id": 1, "status": "completed", "conclusion": "success"}],
                }
            )
        if argv[0] == "api" and "/jobs" in argv[1]:
            return json.dumps(
                {"jobs": [{"id": 2, "name": self.staging_job, "conclusion": "success"}]}
            )
        raise AssertionError(f"Unerwarteter gh-Aufruf: {argv}")


def test_ci_phase_uses_github_actions_when_provider_is_github(ctx, target_repo):
    write_config(
        target_repo,
        DEFAULT_CONFIG.replace("provider: gitlab", "provider: github"),
    )
    from tests.conftest import git

    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "github provider")
    ctx.config = AdwConfig.load(target_repo)
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    gh = FakeGhPhases()
    ctx.run_gh = gh
    ctx.sleep = lambda s: None
    run_ci_phase(ctx)
    assert ctx.state.phase == "done"
    runs_call = next(c for c in gh.calls if "/actions/runs?" in c[1])
    assert "head_sha=" in runs_call[1]  # an den Push gebunden, wie beim GitLab-Poll


def test_ci_phase_escalates_when_forge_is_undetectable(ctx, target_repo):
    """Local bare origin without ci.provider: neither GitLab nor GitHub detectable —
    clear escalation with an action instruction instead of polling the wrong API."""
    write_config(
        target_repo,
        DEFAULT_CONFIG.replace("  provider: gitlab\n", ""),  # KEIN Override
    )
    from tests.conftest import git

    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "ohne provider")
    ctx.config = AdwConfig.load(target_repo)
    add_bare_origin(target_repo)
    prepare_ci_ready(ctx)
    ctx.run_glab = FakeGlab("success")
    ctx.sleep = lambda s: None
    with pytest.raises(EscalationError, match="ci.provider"):
        run_ci_phase(ctx)


# --- Review-Policy v2: absteigende Severity-Schwelle -------------------------


def severity_finding(severity: str, issue: str = "x") -> Finding:
    return Finding(
        severity=severity,
        lane="backend",
        file="src_neu.py",
        issue=issue,
        remediation_plan=["fixen"],
    )


def test_actionable_severities_descend_per_round():
    from adw.phases import _actionable_severities

    assert _actionable_severities(1) == {"P1", "P2", "P3"}
    assert _actionable_severities(2) == {"P1", "P2"}
    assert _actionable_severities(3) == {"P1"}
    assert _actionable_severities(7) == {"P1"}


def test_split_by_severity_returns_actionable_and_dropped():
    from adw.phases import _split_by_severity

    findings = [severity_finding("P1"), severity_finding("P2"), severity_finding("P3")]
    actionable, dropped = _split_by_severity(findings, 1)
    assert [f.severity for f in actionable] == ["P1", "P2", "P3"]
    assert dropped == []
    actionable, dropped = _split_by_severity(findings, 2)
    assert [f.severity for f in actionable] == ["P1", "P2"]
    assert [f.severity for f in dropped] == ["P3"]
    actionable, dropped = _split_by_severity(findings, 3)
    assert [f.severity for f in actionable] == ["P1"]
    assert [f.severity for f in dropped] == ["P2", "P3"]


def test_severity_context_lists_prior_findings_and_threshold():
    from adw.phases import _severity_context

    prior = ["- [P2] a.py: kaputt — disposition: fix dispatched (round 1)"]
    context = _severity_context(2, 5, prior)
    assert "round 2 of max 5" in context
    assert "P1/P2" in context
    assert prior[0] in context
    assert "Do NOT report these findings again" in context


def test_severity_context_is_none_without_prior_findings():
    from adw.phases import _severity_context

    assert _severity_context(1, 5, []) is None


# --- B1: Runden-Cap + Severity-Schwelle im Authoring-Loop --------------------


def p1_finding(issue: str = "kritisch", lane: str = "backend") -> ReviewResult:
    return ReviewResult(
        verdict="needs_fixes",
        findings=[
            Finding(
                severity="P1",
                lane=lane,
                file=".adw/spec.md",
                issue=issue,
                remediation_plan=["dringend fixen"],
            )
        ],
    )


def spec_findings(*items: tuple[str, str]) -> ReviewResult:
    """Spec-Review-Ergebnis aus (severity, issue)-Paaren."""
    return ReviewResult(
        verdict="needs_fixes",
        findings=[
            Finding(
                severity=severity,
                lane="backend",
                file=".adw/spec.md",
                issue=issue,
                remediation_plan=["nachschärfen"],
            )
            for severity, issue in items
        ],
    )


def test_authoring_accepts_when_all_findings_are_below_the_threshold(ctx):
    """B1(a)/v2: In Runde 3 sind P2-Findings nicht mehr actionable → Artefakt
    akzeptiert, Known-Findings-Datei mit der Schwellen-Begründung."""
    ctx.agents.script("spec_synthesis", "v1", "v2", "v3")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(needs_fixes("A"), needs_fixes("B"), needs_fixes("C"), OK)
    ctx.skip_approval = True
    run_spec_and_plan(ctx)
    assert ctx.state.phase == "build"
    known = ctx.run_dir / "authoring-spec-known-findings.md"
    assert known.is_file()
    text = known.read_text()
    assert "C" in text
    assert "Severity-Schwelle" in text
    # Genau 3 Spec-Runden (kein 4. Agent-Lauf nach dem Schwellen-Accept):
    assert len([c for c in ctx.agents.calls if c.agent == "spec_synthesis"]) == 3


def test_authoring_escalates_after_round_cap_with_p1(ctx):
    """B1(b)/v2: 5 Runden mit offenem P1 → Eskalation, keine Known-Findings."""
    ctx.agents.script("spec_synthesis", "v1", "v2", "v3", "v4", "v5")
    ctx.codex.script(*[p1_finding(issue) for issue in "ABCDE"])
    with pytest.raises(EscalationError, match="[Rr]unden-Cap"):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "escalated"
    assert not (ctx.run_dir / "authoring-spec-known-findings.md").is_file()
    assert len([c for c in ctx.agents.calls if c.agent == "spec_synthesis"]) == 5


def test_authoring_fix_task_carries_only_actionable_findings(ctx):
    """v2: In Runde 2 gehen nur P1/P2 in den Fix-Task; das P3 wird als
    Follow-up dokumentiert."""
    ctx.agents.script("spec_synthesis", "v1", "v2", "v3")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(
        needs_fixes("Runde 1"),
        spec_findings(("P1", "kritische Lücke"), ("P3", "Formulierungsdetail")),
        OK,
        OK,
    )
    ctx.skip_approval = True
    run_spec_and_plan(ctx)
    spec_calls = [c for c in ctx.agents.calls if c.agent == "spec_synthesis"]
    assert "kritische Lücke" in spec_calls[2].task
    assert "Formulierungsdetail" not in spec_calls[2].task
    assert "Formulierungsdetail" in (ctx.run_dir / "followups.md").read_text()


def test_authoring_passes_prior_findings_with_dispositions_as_context(ctx):
    """v2: Ab Runde 2 kennt Codex die Findings der Vorrunde inkl. Disposition."""
    ctx.agents.script("spec_synthesis", "v1", "v2")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(needs_fixes("Akzeptanzkriterien fehlen"), OK, OK)
    ctx.skip_approval = True
    run_spec_and_plan(ctx)
    spec_reviews = [c for c in ctx.codex.calls if c.kind == "spec"]
    assert spec_reviews[0].context is None  # Runde 1: Prompt unverändert
    context = spec_reviews[1].context
    assert "round 2 of max 5" in context
    assert "Akzeptanzkriterien fehlen" in context
    assert "fix dispatched (round 1)" in context
    # Der Plan-Loop startet mit leerem Verlauf — Checkpoint wurde zurückgesetzt.
    assert [c for c in ctx.codex.calls if c.kind == "plan"][0].context is None


def test_authoring_context_survives_crash_and_resume(ctx):
    """v2: Der Findings-Verlauf liegt im selben Save wie der Checkpoint."""
    ctx.agents.script("spec_synthesis", "v1", "v2", "v3")
    ctx.codex.script(needs_fixes("A"))  # Runde 2: Codex-Queue leer → Crash
    with pytest.raises(AssertionError):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert any("fix dispatched (round 1)" in line for line in saved.authoring_prior_context)

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=saved,
        agents=ctx.agents,
        codex=ctx.codex,
        skip_approval=True,
    )
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    run_spec_and_plan(resumed)
    spec_reviews = [c for c in ctx.codex.calls if c.kind == "spec"]
    # Der Review nach dem Resume kennt den Verlauf aus dem State.
    assert "fix dispatched (round 1)" in spec_reviews[2].context
    assert resumed.state.authoring_prior_context == []  # beim Übergang geleert


def test_authoring_ok_before_cap_writes_no_known_findings(ctx):
    """B1(c): Verdict ok vor dem Cap → unverändert, keine Known-Findings-Datei."""
    ctx.agents.script("spec_synthesis", "v1", "v2")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(needs_fixes("A"), OK, OK)  # Spec: 1 Fix, dann ok; Plan ok
    ctx.skip_approval = True
    run_spec_and_plan(ctx)
    assert ctx.state.phase == "build"
    assert not (ctx.run_dir / "authoring-spec-known-findings.md").is_file()


def test_authoring_round_counter_survives_resume(ctx):
    """B1(d): Der Runden-Zähler überlebt Crash+Resume und läuft korrekt weiter."""
    ctx.agents.script("spec_synthesis", "v1", "v2", "v3", "v4")
    ctx.codex.script(needs_fixes("A"))  # Runde 2: Codex-Queue leer → Crash
    with pytest.raises(AssertionError):
        run_spec_and_plan(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).authoring_rounds == 1

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
        skip_approval=True,
    )
    ctx.agents.script("plan_synthesis", "Plan")
    # Runde 2 fixt, Runde 3 liegt unter der Schwelle → Accept; Plan ok
    ctx.codex.script(needs_fixes("B"), needs_fixes("C"), OK)
    run_spec_and_plan(resumed)
    assert resumed.state.phase == "build"
    assert (resumed.run_dir / "authoring-spec-known-findings.md").is_file()


# --- B2: Optionaler Spec-Approval-Stopp -------------------------------------


def test_spec_approval_pauses_after_spec_phase(ctx):
    """B2(a): --spec-approval pausiert nach der Spec, vor dem Plan."""
    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)  # nur Spec-Review; Plan wird nicht erreicht
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "awaiting_spec_approval"
    assert (ctx.run_dir / "spec.md").is_file()  # Spec archiviert
    assert [c for c in ctx.agents.calls if c.agent == "plan_agent"] == []


def test_granted_spec_approval_continues_into_plan(ctx):
    """B2(b): nach erteiltem Spec-Approval läuft der Run in die Plan-Phase."""
    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    # Approve simulieren:
    state = RunState.load(ctx.repo, ctx.state.run_id)
    state.spec_approval_granted = True
    state.phase = "plan"
    state.save(ctx.repo)
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
        skip_approval=True,
    )
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK)
    run_spec_and_plan(resumed)
    assert resumed.state.phase == "build"


def test_no_spec_approval_flag_does_not_pause_after_spec(ctx):
    """B2(d): ohne Flag unverändertes Verhalten — Stopp erst beim Plan-Approval."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).phase == "awaiting_approval"


def test_resume_at_spec_approval_pauses_again(ctx):
    """B2(e): Resume bei awaiting_spec_approval pausiert erneut."""
    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,  # spec_approval NICHT gesetzt — kommt aus dem State
    )
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert resumed.state.phase == "awaiting_spec_approval"


def test_spec_approval_flag_read_from_state_on_resume(ctx):
    """B2(f): Crash nach Spec-Archivierung → Resume landet wieder im Spec-Stopp
    (Flag aus State, nicht aus dem Resume-Kontext); Spec liegt archiviert."""
    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "awaiting_spec_approval"
    assert saved.spec_approval is True
    assert (ctx.run_dir / "spec.md").is_file()
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=saved,
        agents=ctx.agents,
        codex=ctx.codex,
    )
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert resumed.state.phase == "awaiting_spec_approval"


def test_authoring_rounds_reset_across_spec_approval_gate(ctx):
    """B1/B2-Wechselwirkung: der Plan-Loop startet mit authoring_rounds=0,
    auch wenn der Spec-Loop Fix-Runden hatte."""
    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "v1", "v2")
    ctx.codex.script(needs_fixes("A"), OK)  # Spec: 1 Fix-Runde, dann ok
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.phase == "awaiting_spec_approval"
    assert saved.authoring_rounds == 0


# --- B3: Issue-Text als Review-Referenz -------------------------------------


def test_spec_review_includes_issue_ref(ctx):
    """B3(a): Spec-Review erhält issue.md UND spec.md als Refs."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    spec_review = ctx.codex.calls[0]
    assert spec_review.kind == "spec"
    assert any("issue.md" in r for r in spec_review.content_refs)
    assert any("spec.md" in r for r in spec_review.content_refs)


def test_plan_review_includes_issue_ref(ctx):
    """B3(b): Plan-Review erhält issue.md zusätzlich zu spec/plan/contract."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    plan_review = ctx.codex.calls[1]
    assert plan_review.kind == "plan"
    assert any("issue.md" in r for r in plan_review.content_refs)
    assert any("spec.md" in r for r in plan_review.content_refs)


def test_issue_md_archived_and_removed_from_checkout(ctx):
    """B3(c): issue.md landet archiviert im run_dir und verschwindet aus dem Checkout."""
    from tests.conftest import git

    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    archived = ctx.run_dir / "issue.md"
    assert archived.is_file()
    assert "ISSUE-1" in archived.read_text()
    assert not (ctx.repo / ".adw" / "issue.md").is_file()
    assert git(ctx.repo, "status", "--porcelain") == ""


def test_issue_md_agent_tampering_is_restored(ctx):
    """B3(d): eine Agent-seitige Änderung an issue.md wird nach dem Lauf restauriert."""
    spec_files = dict(ctx.agents.file_writes["spec_agent"])
    spec_files[".adw/issue.md"] = "# sabotiert\n"
    ctx.agents.script_files("spec_agent", spec_files)
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    archived = (ctx.run_dir / "issue.md").read_text()
    assert "sabotiert" not in archived
    assert "ISSUE-1" in archived


def test_resume_in_plan_phase_regenerates_issue_md(ctx):
    """B3(e): Resume in der Plan-Phase ohne vorhandene issue.md schreibt sie neu."""
    seen = {}

    class SpyCodex(MockCodexRunner):
        def review(self, kind, content_refs, cwd, context=None):
            if kind == "plan":
                path = cwd / ".adw" / "issue.md"
                seen["exists"] = path.is_file()
                seen["content"] = path.read_text() if path.is_file() else ""
            return super().review(kind, content_refs, cwd, context)

    ctx.agents.script("spec_synthesis", "Spec")  # Plan-Synthese NICHT gescriptet → Crash
    ctx.codex.script(OK)
    with pytest.raises(AssertionError):
        run_spec_and_plan(ctx)
    assert RunState.load(ctx.repo, ctx.state.run_id).phase == "plan"
    (ctx.repo / ".adw" / "issue.md").unlink(missing_ok=True)

    spy = SpyCodex()
    script_draft_artifacts(spy)
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=spy,
    )
    ctx.agents.script("plan_synthesis", "Plan")
    spy.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert seen["exists"] is True
    assert "ISSUE-1" in seen["content"]


# --- Codex-Review-Fixes (B1/B2/B3) ------------------------------------------


def test_preexisting_foreign_issue_md_is_not_silently_destroyed(ctx):
    """P1-Fix: eine fremde .adw/issue.md wird nicht überschrieben/verworfen,
    sondern eskaliert — der Pfad gehört dem ADW."""
    foreign = ctx.repo / ".adw" / "issue.md"
    foreign.write_text("# WICHTIGE User-Notizen\n")
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(EscalationError, match="issue.md"):
        run_spec_and_plan(ctx)
    # User-Inhalt unangetastet:
    assert foreign.read_text() == "# WICHTIGE User-Notizen\n"


def test_own_regenerated_issue_md_is_no_op_overwrite(ctx):
    """P1-Fix: identischer (eigener) Inhalt darf NICHT eskalieren — Resume-Fall."""
    from adw.phases import _write_issue

    data = _write_issue(ctx)  # erzeugt .adw/issue.md aus dem State
    # Zweiter Aufruf mit identischem Inhalt (wie beim Resume) ist ein No-op:
    assert _write_issue(ctx) == data


def test_spec_approval_pause_leaves_checkout_clean(ctx):
    """P2-Fix: der Spec-Gate-Stopp archiviert + säubert wie das Plan-Gate."""
    from tests.conftest import git

    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert git(ctx.repo, "status", "--porcelain") == ""
    assert (ctx.run_dir / "spec.md").is_file()
    assert (ctx.run_dir / "issue.md").is_file()
    assert not (ctx.repo / ".adw" / "spec.md").is_file()
    assert not (ctx.repo / ".adw" / "issue.md").is_file()


def test_preexisting_issue_md_symlink_is_not_followed_or_destroyed(ctx):
    """P1-Fix (Runde 2): ein vorhandener .adw/issue.md-Symlink wird nicht
    entfernt/ersetzt (die Archivierung würde ihn verlieren), sondern eskaliert;
    die Referenz bleibt unangetastet."""
    target = ctx.repo / "geheim.txt"
    target.write_text("SENSIBEL\n")
    link = ctx.repo / ".adw" / "issue.md"
    link.symlink_to(target)
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(EscalationError, match="issue.md"):
        run_spec_and_plan(ctx)
    assert link.is_symlink()  # nicht ersetzt
    assert target.read_text() == "SENSIBEL\n"  # Referenz unangetastet


def test_spec_approval_pause_retries_cleanup_on_resume(ctx):
    """P2-Fix (Runde 2): ein Crash zwischen awaiting_spec_approval-Save und
    Archivierung hinterlässt generierte Artefakte im Checkout — ein Resume holt
    die Archivierung nach und säubert den Checkout."""
    from tests.conftest import git

    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    # Crash-Fenster simulieren: generierte Artefakte liegen wieder dirty im Checkout.
    (ctx.repo / ".adw" / "spec.md").write_text((ctx.run_dir / "spec.md").read_text())
    (ctx.repo / ".adw" / "issue.md").write_text((ctx.run_dir / "issue.md").read_text())
    assert git(ctx.repo, "status", "--porcelain") != ""  # dirty vor dem Resume
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
    )
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert resumed.state.phase == "awaiting_spec_approval"
    assert git(ctx.repo, "status", "--porcelain") == ""  # Cleanup nachgeholt
    assert not (ctx.repo / ".adw" / "spec.md").is_file()
    assert not (ctx.repo / ".adw" / "issue.md").is_file()


def test_spec_approval_resume_preserves_reviewed_archive_when_spec_tracked(ctx):
    """P1-Fix (Runde 3): ein Resume während der Spec-Pause darf die reviewte
    Archiv-Spec NICHT mit einer alten getrackten Checkout-Version überschreiben."""
    from tests.conftest import git

    spec_path = ctx.repo / ".adw" / "spec.md"
    spec_path.write_text("# ALTE gemergte Spec\n")
    git(ctx.repo, "add", ".adw/spec.md")
    git(ctx.repo, "commit", "-m", "alt")
    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert (ctx.run_dir / "spec.md").read_text() == SPEC_SYNTHESIS_FILES[".adw/spec.md"]
    # Checkout wurde auf die alte getrackte Version zurückgesetzt:
    assert spec_path.read_text() == "# ALTE gemergte Spec\n"

    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
    )
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    # Reviewte Spec im Archiv bleibt erhalten (nicht durch die alte überschrieben):
    assert (resumed.run_dir / "spec.md").read_text() == SPEC_SYNTHESIS_FILES[".adw/spec.md"]


def test_ok_verdict_clears_stale_known_findings_report(ctx):
    """P2-Fix (Runde 3): ein terminaler ok-Verdict entfernt einen stale
    Known-Findings-Report (z. B. aus einem gecrashten Cap-Accept)."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    stale = ctx.run_dir / "authoring-spec-known-findings.md"
    stale.write_text("# alte Known Limitations\n")
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    ctx.skip_approval = True
    run_spec_and_plan(ctx)
    assert ctx.state.phase == "build"
    assert not stale.is_file()


# --- Synthese als Loop-Einstieg + Summary-Archivierung -----------------------


def test_synthesis_is_the_first_run_of_the_authoring_loop(ctx):
    """Je Phase: erst beide Entwürfe, dann die Synthese als Loop-Einstieg."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert [c.agent for c in ctx.agents.calls] == [
        "spec_agent",
        "spec_synthesis",
        "plan_agent",
        "plan_synthesis",
    ]
    assert [c.kind for c in ctx.codex.author_calls] == ["spec", "plan"]


def test_synthesis_task_names_issue_and_both_drafts(ctx):
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    run_id = ctx.state.run_id
    task = next(c.task for c in ctx.agents.calls if c.agent == "spec_synthesis")
    assert ".adw/issue.md" in task
    assert f".adw/runs/{run_id}/drafts/spec.claude.md" in task
    assert f".adw/runs/{run_id}/drafts/spec.codex.md" in task
    assert ".adw/spec.md" in task and ".adw/spec-summary.md" in task
    plan_task = next(c.task for c in ctx.agents.calls if c.agent == "plan_synthesis")
    assert f".adw/runs/{run_id}/drafts/contract.claude.yaml" in plan_task
    assert ".adw/plan-summary.md" in plan_task


def test_synthesis_task_flags_the_single_source_basis(ctx):
    """Degradierter Codex-Entwurf: die Synthese bekommt keinen toten Pfad,
    sondern den Hinweis auf die Ein-Quellen-Basis."""
    ctx.codex = MockCodexRunner()  # ohne die Vorrats-Entwürfe der Fixture
    ctx.codex.script_author_error("spec", CodexError("codex exec: Exit 1"))
    ctx.codex.script_artifacts(
        "plan", {"plan.md": "# Codex-Plan\n", "contract.yaml": "openapi: 3.1.0\n"}
    )
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    task = next(c.task for c in ctx.agents.calls if c.agent == "spec_synthesis")
    assert "spec.codex.md" not in task
    assert "single-source" in task
    plan_task = next(c.task for c in ctx.agents.calls if c.agent == "plan_synthesis")
    assert "single-source" not in plan_task  # der Plan-Entwurf kam durch


def test_summaries_are_archived_and_leave_the_checkout_clean(ctx):
    from tests.conftest import git

    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert (ctx.run_dir / "spec-summary.md").read_text() == (
        SPEC_SYNTHESIS_FILES[".adw/spec-summary.md"]
    )
    assert (ctx.run_dir / "plan-summary.md").read_text() == (
        PLAN_SYNTHESIS_FILES[".adw/plan-summary.md"]
    )
    assert not (ctx.repo / ".adw" / "spec-summary.md").exists()
    assert not (ctx.repo / ".adw" / "plan-summary.md").exists()
    assert git(ctx.repo, "status", "--porcelain") == ""


def test_summaries_are_not_seeded_into_lane_worktrees(ctx):
    """Die Summary ist ein Gate-Artefakt für den Menschen — keine Build-Lane
    baut dagegen, also gehört sie nicht in den Worktree."""
    prepare_approved(ctx)
    ctx.agents.script_files("build_agent", {"src_neu.py": "print('hallo')\n"})
    ctx.agents.script("build_agent", "gebaut")
    run_build_phase(ctx)
    worktree = ctx.repo / ".adw" / "runs" / ctx.state.run_id / "trees" / "backend"
    assert (worktree / ".adw" / "spec.md").is_file()
    assert not (worktree / ".adw" / "spec-summary.md").exists()
    assert not (worktree / ".adw" / "plan-summary.md").exists()


def test_summary_survives_a_crash_at_the_spec_gate(ctx):
    """Crash-Fenster zwischen Spec-Gate-Save und Archivierung: die Summary
    liegt schon im Run-Ordner und überlebt den Resume."""
    from tests.conftest import git

    ctx.spec_approval = True
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    summary = ctx.run_dir / "spec-summary.md"
    assert summary.is_file()
    # Crash-Fenster: das generierte Artefakt liegt wieder dirty im Checkout.
    (ctx.repo / ".adw" / "spec-summary.md").write_text(summary.read_text())
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=RunState.load(ctx.repo, ctx.state.run_id),
        agents=ctx.agents,
        codex=ctx.codex,
    )
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    assert summary.read_text() == SPEC_SYNTHESIS_FILES[".adw/spec-summary.md"]
    assert not (ctx.repo / ".adw" / "spec-summary.md").exists()
    assert git(ctx.repo, "status", "--porcelain") == ""


def test_fix_task_asks_to_keep_the_summary_current(ctx):
    ctx.agents.script("spec_synthesis", "v1", "v2")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(needs_fixes("Akzeptanzkriterien fehlen"), OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    fix_task = [c for c in ctx.agents.calls if c.agent == "spec_synthesis"][1].task
    assert "Akzeptanzkriterien fehlen" in fix_task
    assert ".adw/spec-summary.md" in fix_task


def test_summary_is_no_codex_review_reference(ctx):
    """review_refs bleiben unverändert — Codex reviewt Artefakte, keine Summary."""
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    for call in ctx.codex.calls:
        assert all("summary" not in ref for ref in call.content_refs)


def test_missing_summary_escalates_like_a_missing_artifact(ctx):
    ctx.agents.script_files("spec_synthesis", {".adw/spec.md": "# Spec (Synthese)\n"})
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK)
    with pytest.raises(EscalationError, match="spec-summary.md"):
        run_spec_and_plan(ctx)


def test_blank_summary_escalates_like_a_missing_one(ctx):
    """Eine leere Summary ist keine Entscheidungsgrundlage — und Codex reviewt
    sie nicht, der Fehler fiele sonst erst dem Menschen am Gate auf."""
    ctx.agents.script_files(
        "spec_synthesis", {".adw/spec.md": "# Spec (Synthese)\n", ".adw/spec-summary.md": "  \n"}
    )
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(EscalationError, match="spec-summary.md"):
        run_spec_and_plan(ctx)


def test_orphaned_summary_of_a_crashed_loop_does_not_escalate(ctx):
    """Crash zwischen Synthese-Lauf und Session-Save: die verwaiste Summary im
    Checkout darf einen frischen Lauf mit identischem Inhalt nicht eskalieren."""
    (ctx.repo / ".adw").mkdir(exist_ok=True)
    (ctx.repo / ".adw" / "spec-summary.md").write_text(SPEC_SYNTHESIS_FILES[".adw/spec-summary.md"])
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert (ctx.run_dir / "spec-summary.md").is_file()


def test_resume_keeps_the_checkpointed_synthesis_artifact(ctx):
    """Die Draft-Stage räumt Phasen-Reste weg — NICHT aber das Artefakt einer
    bereits gelaufenen Synthese: daran arbeitet die Fix-Runde weiter."""
    ctx.agents.script("spec_synthesis", "v1")  # Fix-Lauf fehlt → Crash dort
    ctx.codex.script(needs_fixes("Akzeptanzkriterien fehlen"))
    with pytest.raises(AssertionError):
        run_spec_and_plan(ctx)
    saved = RunState.load(ctx.repo, ctx.state.run_id)
    assert saved.authoring_session and saved.authoring_pending_task

    seen = {}

    class SpyAgents(MockAgentRunner):
        def run(self, agent, task, cwd, resume=None, deny_read_paths=None):
            if agent.name == "spec_synthesis":
                seen["spec"] = (cwd / ".adw" / "spec.md").read_text()
            return super().run(agent, task, cwd, resume, deny_read_paths)

    spy = SpyAgents()
    script_authoring_agents(spy)
    spy.script("spec_synthesis", "v2")
    spy.script("plan_synthesis", "Plan")
    resumed = RunContext(
        repo=ctx.repo,
        config=ctx.config,
        state=saved,
        agents=spy,
        codex=ctx.codex,
    )
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(resumed)
    # Der Fix-Lauf sieht die reviewte Zwischenfassung, nicht ein leeres Verzeichnis:
    assert seen["spec"] == SPEC_SYNTHESIS_FILES[".adw/spec.md"]


def test_foreign_summary_does_not_pass_as_this_runs_summary(ctx):
    """Eine fremde (getrackte) Summary darf keine Synthese-Zusammenfassung
    vortäuschen — Codex reviewt sie nicht, niemand fiele es sonst auf."""
    from tests.conftest import git

    summary = ctx.repo / ".adw" / "spec-summary.md"
    summary.write_text("# Fremde Zusammenfassung\n")
    git(ctx.repo, "add", ".adw/spec-summary.md")
    git(ctx.repo, "commit", "-m", "fremde Zusammenfassung")
    ctx.agents.script_files(
        "spec_synthesis",
        {
            ".adw/spec.md": SPEC_SYNTHESIS_FILES[".adw/spec.md"],
            ".adw/spec-summary.md": "# Fremde Zusammenfassung\n",  # unverändert
        },
    )
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.codex.script(OK)
    with pytest.raises(EscalationError, match="spec-summary.md"):
        run_spec_and_plan(ctx)


def test_plan_synthesis_cannot_rewrite_the_spec_summary(ctx):
    """Die Spec-Summary ist in der Plan-Phase protected wie die Spec selbst."""
    files = dict(PLAN_SYNTHESIS_FILES)
    files[".adw/spec-summary.md"] = "# gekaperte Spec-Zusammenfassung\n"
    ctx.agents.script_files("plan_synthesis", files)
    ctx.agents.script("spec_synthesis", "Spec")
    ctx.agents.script("plan_synthesis", "Plan")
    ctx.codex.script(OK, OK)
    with pytest.raises(AwaitingApproval):
        run_spec_and_plan(ctx)
    assert (ctx.run_dir / "spec-summary.md").read_text() == (
        SPEC_SYNTHESIS_FILES[".adw/spec-summary.md"]
    )
