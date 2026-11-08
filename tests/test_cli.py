"""CLI tests (typer CliRunner) — dry-run mode, 0 tokens, real git."""

import contextlib
import fcntl
import json
import subprocess

import pytest
from typer.testing import CliRunner

from adw.cli import app
from adw.state import RunState, StateNotFoundError
from tests.conftest import git, write_config

runner = CliRunner()

PARALLEL_CLI_CONFIG = """\
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


def test_run_requires_exactly_one_issue_source(target_repo):
    neither = runner.invoke(app, ["run", "--repo", str(target_repo), "--dry-run"])
    assert neither.exit_code == 1
    both = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--issue",
            "Demo",
            "--gitlab-issue",
            "7",
            "--dry-run",
        ],
    )
    assert both.exit_code == 1


def test_dry_run_happy_path_completes_all_phases(target_repo):
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--issue",
            "Demo-Feature",
            "--dry-run",
            "--no-approval",
        ],
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    assert state.run_id in result.output


def test_dry_run_parallel_completes_all_phases(target_repo):
    write_config(target_repo, PARALLEL_CLI_CONFIG)
    from tests.conftest import git

    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "parallel config")
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--issue",
            "Demo parallel",
            "--dry-run",
            "--no-approval",
            "--parallel",
        ],
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    assert set(state.lanes) >= {"backend", "frontend"}


def test_run_pauses_for_approval_and_approve_continues(target_repo):
    result = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    assert result.exit_code == 2, result.output  # awaiting_approval
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_approval"
    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 0, approved.output
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_resume_without_approval_pauses_again(target_repo):
    runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    state = RunState.find_latest(target_repo)
    result = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert result.exit_code == 2  # weiterhin awaiting_approval
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


def test_gitlab_issue_is_fetched_via_glab(target_repo, monkeypatch):
    import adw.cli as cli_mod

    def fake_glab(argv, cwd):
        assert argv[:2] == ["issue", "view"]
        assert "7" in argv
        return json.dumps({"iid": 7, "title": "Login bricht ab", "description": "Stacktrace ..."})

    monkeypatch.setattr(cli_mod, "_run_glab", fake_glab)
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--gitlab-issue",
            "7",
            "--dry-run",
            "--no-approval",
        ],
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert "Login bricht ab" in state.issue
    assert "Stacktrace" in state.issue


def test_status_lists_runs_with_phase(target_repo):
    runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
    )
    state = RunState.find_latest(target_repo)
    result = runner.invoke(app, ["status", "--repo", str(target_repo)])
    assert result.exit_code == 0
    assert state.run_id in result.output
    assert "done" in result.output


def test_status_without_runs_reports_cleanly(target_repo):
    result = runner.invoke(app, ["status", "--repo", str(target_repo)])
    assert result.exit_code == 0
    assert "Keine Runs" in result.output


def test_failing_gate_escalates_with_exit_1(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: immer-rot, cmd: "sh -c 'echo GATE-ROT; exit 1'", timeout: 10}
ci:
  provider: gitlab
  staging_job: deploy-staging
""",
    )
    from tests.conftest import git

    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "rotes gate")
    result = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
    )
    assert result.exit_code == 1
    state = RunState.find_latest(target_repo)
    assert state.phase == "escalated"
    assert (state.run_dir(target_repo) / "escalation.md").is_file()


def test_missing_config_exits_1_with_clear_error(tmp_path):
    from tests.conftest import git

    repo = tmp_path / "leer"
    repo.mkdir()
    git(repo, "init", "-b", "staging")
    result = runner.invoke(app, ["run", "--repo", str(repo), "--issue", "x", "--dry-run"])
    assert result.exit_code == 1
    assert "config.yaml" in result.output


def test_resume_unknown_run_id_exits_1(target_repo):
    result = runner.invoke(app, ["resume", "deadbeef", "--repo", str(target_repo)])
    assert result.exit_code == 1


def test_run_state_is_persisted_before_first_agent_call(target_repo, monkeypatch):
    """Regression (Codex P1): if the very first agent run crashes, the displayed
    run_id must still be findable via `adw resume`."""
    import adw.cli as cli_mod
    from adw.mock import MockAgentRunner, MockCodexRunner

    def broken_runners():
        return MockAgentRunner(), MockCodexRunner()  # nichts gescriptet → Crash

    monkeypatch.setattr(cli_mod, "_dry_run_runners", broken_runners)
    # OHNE --no-approval: der Default-Pfad hatte vor dem Fix keinen Save
    # zwischen Run-Erzeugung und erstem Agent-Lauf.
    result = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    assert result.exit_code != 0
    state = RunState.find_latest(target_repo)  # State existiert trotz Crash
    assert state.phase == "spec"


def test_base_branch_override_survives_approve(target_repo):
    """Regression (Codex P1): --base-branch on `run` must still apply after the
    approval pause, without `approve` repeating the flag."""
    from tests.conftest import git

    git(target_repo, "checkout", "-b", "develop")
    (target_repo / "nur_in_develop.txt").write_text("marker\n")
    git(target_repo, "add", ".")
    git(target_repo, "commit", "-m", "develop marker")
    git(target_repo, "checkout", "staging")
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--issue",
            "Demo",
            "--dry-run",
            "--base-branch",
            "develop",
        ],
    )
    assert result.exit_code == 2, result.output
    state = RunState.find_latest(target_repo)
    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 0, approved.output
    worktree = target_repo / ".adw" / "runs" / state.run_id / "trees" / "backend"
    assert (worktree / "nur_in_develop.txt").is_file()  # ab develop gebaut


def test_resume_escalated_run_exits_1(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: immer-rot, cmd: "sh -c 'echo ROT; exit 1'", timeout: 10}
""",
    )
    from tests.conftest import git

    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "rotes gate")
    runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
    )
    state = RunState.find_latest(target_repo)
    assert state.phase == "escalated"
    result = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert result.exit_code == 1  # kein "abgeschlossen" mit Exit 0
    assert "eskaliert" in (result.output or "").lower()


def test_resume_of_a_done_run_changes_nothing(target_repo, monkeypatch):
    """C9: `resume` eines fertigen Laufs mit bereits komprimiertem Log endet mit
    Exit 0 und Meldung — vor Vorflug, Emitter und Auto-Prune; kein neues
    events.jsonl, .gz und state.json byte-gleich."""
    import gzip

    import adw.cli as cli_mod

    done = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
    )
    assert done.exit_code == 0, done.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    run_dir = state.run_dir(target_repo)
    plain = run_dir / "events.jsonl"
    (run_dir / "events.jsonl.gz").write_bytes(gzip.compress(plain.read_bytes()))
    plain.unlink()
    gz_before = (run_dir / "events.jsonl.gz").read_bytes()
    state_before = (run_dir / "state.json").read_bytes()

    called = []
    monkeypatch.setattr(cli_mod, "_preflight", lambda *a, **k: called.append("preflight"))
    monkeypatch.setattr(
        cli_mod, "_maybe_auto_prune", lambda *a, **k: called.append("auto_prune")
    )
    result = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])

    assert result.exit_code == 0, result.output
    assert f"Run {state.run_id} ist bereits abgeschlossen" in result.output
    assert called == []
    assert not plain.exists()
    assert (run_dir / "events.jsonl.gz").read_bytes() == gz_before
    assert (run_dir / "state.json").read_bytes() == state_before


def test_blank_base_branch_override_is_rejected(target_repo):
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--issue",
            "x",
            "--dry-run",
            "--base-branch",
            "   ",
        ],
    )
    assert result.exit_code == 1
    assert "base-branch" in result.output.lower()


def test_gitlab_issue_is_not_fetched_when_config_is_broken(tmp_path, monkeypatch):
    """Regression (Codex P2): config validation BEFORE the glab network call."""
    import adw.cli as cli_mod
    from tests.conftest import git

    repo = tmp_path / "kaputt"
    repo.mkdir()
    git(repo, "init", "-b", "staging")
    calls = []

    def recording_glab(argv, cwd):
        calls.append(argv)
        return "{}"

    monkeypatch.setattr(cli_mod, "_run_glab", recording_glab)
    result = runner.invoke(app, ["run", "--repo", str(repo), "--gitlab-issue", "7", "--dry-run"])
    assert result.exit_code == 1
    assert "config.yaml" in result.output
    assert calls == []  # kein Netzaufruf für ein Repo ohne gültige Config


def test_dry_run_simulates_gate_fail_and_fix(target_repo):
    """PLAN Task 11: the dry run contains a simulated Gate fail whose fix goes
    to the same session as a follow-up task (2 Gate iterations)."""
    result = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    assert state.lanes["backend"].gate_iterations == 2  # Fail → Fix → Pass


def test_parallel_dry_run_exercises_e2e_triage_path(target_repo):
    """SPEC DoD 1: --dry-run --parallel exercises the E2E-Triage path."""
    write_config(target_repo, PARALLEL_CLI_CONFIG)
    from tests.conftest import git

    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "parallel config")
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--issue",
            "Demo",
            "--dry-run",
            "--no-approval",
            "--parallel",
        ],
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    assert state.integration_rounds == 1  # genau eine simulierte E2E-Fix-Runde


def crash_on_second_build(monkeypatch):
    """Dry-run runners whose build agent crashes on the SECOND run (only 1
    response scripted) — leaves a Gate-fail checkpoint in the state."""
    import adw.cli as cli_mod

    real = cli_mod._dry_run_runners

    def limited():
        agents, codex = real()
        agents.scripts["build_agent"].clear()
        agents.script("build_agent", "einziger Lauf")
        return agents, codex

    monkeypatch.setattr(cli_mod, "_dry_run_runners", limited)


def test_dry_run_resume_after_gate_fail_checkpoint(target_repo, monkeypatch):
    """Regression (Codex P2): the dry-run simulation must derive its progress
    from the Worktree — a resume with a fresh mock must not repeat the
    checkpointed Gate fix and run into the Circuit-Breaker."""
    with monkeypatch.context() as m:
        crash_on_second_build(m)
        crashed = runner.invoke(
            app,
            ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
        )
        assert crashed.exit_code != 0
    state = RunState.find_latest(target_repo)
    assert state.lanes["backend"].pending_task  # Gate-Fail-Checkpoint liegt vor
    resumed = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 0, resumed.output
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_base_branch_change_after_lane_creation_is_rejected(target_repo, monkeypatch):
    """Regression (Codex P2): Lanes are forked from the old base — a subsequent
    --base-branch change would produce inconsistent diffs."""
    from tests.conftest import git

    git(target_repo, "branch", "develop")
    with monkeypatch.context() as m:
        crash_on_second_build(m)
        runner.invoke(
            app,
            ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
        )
    state = RunState.find_latest(target_repo)
    assert state.lanes  # Lane existiert bereits
    result = runner.invoke(
        app,
        ["resume", state.run_id, "--repo", str(target_repo), "--base-branch", "develop"],
    )
    assert result.exit_code == 1
    assert "base-branch" in result.output.lower()
    assert RunState.load(target_repo, state.run_id).pinned_base_branch == "staging"


def test_invalid_resume_override_is_not_persisted(target_repo):
    """Regression (Codex P2): a rejected override must not poison the state —
    the next `resume` without the flag must work normally."""
    runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    state = RunState.find_latest(target_repo)
    rejected = runner.invoke(
        app,
        ["resume", state.run_id, "--repo", str(target_repo), "--base-branch", "   "],
    )
    assert rejected.exit_code == 1
    assert RunState.load(target_repo, state.run_id).pinned_base_branch == "staging"
    plain = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert plain.exit_code == 2  # weiterhin sauber awaiting_approval


def test_config_base_branch_change_mid_run_does_not_move_existing_lanes(target_repo, monkeypatch):
    """Regression (Codex P1): if base_branch changes in config.yaml mid-run, a
    resume must not integrate the existing Lanes against the new base — the
    effective base branch is pinned from run start."""
    from tests.conftest import git

    git(target_repo, "checkout", "-b", "develop")
    (target_repo / "develop_marker.txt").write_text("x\n")
    git(target_repo, "add", ".")
    git(target_repo, "commit", "-m", "develop divergiert")
    git(target_repo, "checkout", "staging")
    write_config(target_repo, PARALLEL_CLI_CONFIG)
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "parallel config")
    with monkeypatch.context() as m:
        crash_on_second_build(m)
        crashed = runner.invoke(
            app,
            [
                "run",
                "--repo",
                str(target_repo),
                "--issue",
                "Demo",
                "--dry-run",
                "--no-approval",
                "--parallel",
            ],
        )
        assert crashed.exit_code != 0
    state = RunState.find_latest(target_repo)
    assert state.lanes  # Lanes wurden bereits von staging geforkt
    assert state.pinned_base_branch == "staging"
    # Config wechselt mid-run auf develop — der laufende Run darf das ignorieren.
    # Committen, damit der Arbeitsbaum-Vorflug (F3) nicht wegen der fremden,
    # uncommitteten config.yaml verweigert — geprüft wird das Pinning, nicht
    # die Toleranz eines dirty Checkouts.
    write_config(
        target_repo, PARALLEL_CLI_CONFIG.replace("base_branch: staging", "base_branch: develop")
    )
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "config wechselt auf develop")
    resumed = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 0, resumed.output
    integration = target_repo / ".adw" / "runs" / state.run_id / "trees" / "integration"
    assert not (integration / "develop_marker.txt").exists()  # weiterhin ab staging


def test_github_issue_is_fetched_via_gh(target_repo, monkeypatch):
    import adw.cli as cli_mod

    def fake_gh(argv, cwd):
        assert argv[:2] == ["issue", "view"]
        assert "9" in argv
        return json.dumps({"number": 9, "title": "Checkout hängt", "body": "Repro: Warenkorb ..."})

    monkeypatch.setattr(cli_mod, "_run_gh", fake_gh)
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--github-issue",
            "9",
            "--dry-run",
            "--no-approval",
        ],
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert "Checkout hängt" in state.issue
    assert "Warenkorb" in state.issue


def test_issue_sources_are_mutually_exclusive_all_pairs(target_repo):
    pairs = [
        ["--issue", "x", "--github-issue", "9"],
        ["--gitlab-issue", "7", "--github-issue", "9"],
        ["--issue", "x", "--gitlab-issue", "7", "--github-issue", "9"],
    ]
    for extra in pairs:
        result = runner.invoke(app, ["run", "--repo", str(target_repo), "--dry-run", *extra])
        assert result.exit_code == 1, extra


def test_agent_run_error_stops_cleanly_and_stays_resumable(target_repo, monkeypatch):
    """Plan-based operation: a failed SDK call (e.g. subscription window
    exhausted) ends the run in a controlled way with a resume hint — it does
    NOT escalate (phase=escalated would not be resumable) and does not crash
    with a traceback."""
    import adw.cli as cli_mod
    from adw.agents import AgentRunError

    real = cli_mod._dry_run_runners

    def limit_exhausted_on_build():
        agents, codex = real()

        original_run = agents.run

        def run(agent, task, cwd, resume=None, deny_read_paths=None, **kwargs):
            if agent.name == "build_agent":
                raise AgentRunError("Claude-CLI: usage limit reached — resets 14:00")
            return original_run(agent, task, cwd, resume, deny_read_paths, **kwargs)

        agents.run = run
        return agents, codex

    with monkeypatch.context() as m:  # nur der ERSTE Lauf trifft das Limit
        m.setattr(cli_mod, "_dry_run_runners", limit_exhausted_on_build)
        result = runner.invoke(
            app,
            ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
        )
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)  # kein Traceback
    assert "resume" in result.output.lower()
    state = RunState.find_latest(target_repo)
    assert state.phase == "build"  # am Checkpoint stehen geblieben, NICHT escalated
    assert state.run_id in result.output
    assert not (state.run_dir(target_repo) / "escalation.md").exists()
    # Nach "Limit-Reset": derselbe Run läuft per resume bis zum Ende durch.
    resumed = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 0, resumed.output
    assert RunState.load(target_repo, state.run_id).phase == "done"


def _assert_stopped_resumable(result, target_repo, phase: str) -> None:
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)  # kein Traceback
    assert "resume" in result.output.lower()
    state = RunState.find_latest(target_repo)
    assert state.phase == phase  # am Checkpoint, NICHT escalated
    assert state.run_id in result.output
    assert not (state.run_dir(target_repo) / "escalation.md").exists()


def test_sdk_exception_in_query_stops_with_resume_hint(target_repo, monkeypatch, tmp_path):
    """W1/C26: an exception out of the SDK's query() (real runner) ends with the
    resume hint, exit 1 — no traceback, no escalation."""
    import adw.cli as cli_mod
    from adw.agents import SdkAgentRunner

    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    def fake_query(*, prompt, options):
        async def stream():
            raise RuntimeError("CLI-Prozess weg")
            yield  # pragma: no cover — makes this an async generator

        return stream()

    monkeypatch.setattr("adw.agents.query", fake_query)
    real = cli_mod._dry_run_runners

    def sdk_build_agent():
        agents, codex = real()
        original_run = agents.run

        def run(agent, task, cwd, resume=None, deny_read_paths=None, **kwargs):
            if agent.name == "build_agent":
                return SdkAgentRunner().run(agent, task, cwd, resume, deny_read_paths, **kwargs)
            return original_run(agent, task, cwd, resume, deny_read_paths, **kwargs)

        agents.run = run
        return agents, codex

    monkeypatch.setattr(cli_mod, "_dry_run_runners", sdk_build_agent)
    result = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"]
    )
    _assert_stopped_resumable(result, target_repo, "build")
    assert "RuntimeError: CLI-Prozess weg" in result.output


def test_codex_error_in_code_review_stops_with_resume_hint(target_repo, monkeypatch):
    """W1/C26: a CodexError (e.g. timeout) in the Codex code review is resumable
    — resume hint, exit 1, phase stays codex_review, no escalation.md."""
    import adw.cli as cli_mod
    from adw.codex import CodexError

    real = cli_mod._dry_run_runners

    def codex_times_out_on_code_review():
        agents, codex = real()
        original_review = codex.review

        def review(kind, content_refs, cwd, context=None, **kwargs):
            if kind == "code":
                raise CodexError("codex exec: Timeout nach 900s")
            return original_review(kind, content_refs, cwd, context, **kwargs)

        codex.review = review
        return agents, codex

    monkeypatch.setattr(cli_mod, "_dry_run_runners", codex_times_out_on_code_review)
    result = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"]
    )
    _assert_stopped_resumable(result, target_repo, "codex_review")
    assert "Timeout nach 900s" in result.output


# --- B2: Spec-Approval-Stopp (CLI)------------------------------------------


def test_run_with_spec_approval_pauses_after_spec(target_repo):
    result = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--spec-approval"],
    )
    assert result.exit_code == 2, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_spec_approval"
    assert (state.run_dir(target_repo) / "spec.md").is_file()


def test_gate_messages_point_to_the_summary(target_repo):
    """Die Zusammenfassung ist die Entscheidungsgrundlage am Gate — sie liegt im
    ignorierten Run-Ordner und muss deshalb im Hinweis stehen."""
    spec_gate = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--spec-approval"],
    )
    assert spec_gate.exit_code == 2, spec_gate.output
    assert "spec-summary.md" in spec_gate.output
    state = RunState.find_latest(target_repo)
    plan_gate = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert plan_gate.exit_code == 2, plan_gate.output
    assert "plan-summary.md" in plan_gate.output


def test_approve_spec_then_pauses_at_plan_approval(target_repo):
    runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--spec-approval"],
    )
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_spec_approval"
    # Spec approven → Lauf pausiert regulär beim Plan-Approval:
    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 2, approved.output
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"
    # Plan approven → Durchlauf bis done:
    done = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert done.exit_code == 0, done.output
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_spec_approval_with_no_approval_runs_through_after_spec(target_repo):
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            str(target_repo),
            "--issue",
            "Demo",
            "--dry-run",
            "--spec-approval",
            "--no-approval",
        ],
    )
    assert result.exit_code == 2, result.output  # Stopp nach Spec
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_spec_approval"
    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 0, approved.output  # danach Durchlauf ohne Plan-Stopp
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_resume_at_spec_approval_pauses_again(target_repo):
    runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--spec-approval"],
    )
    state = RunState.find_latest(target_repo)
    result = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert result.exit_code == 2
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_spec_approval"


def test_dry_run_authors_each_phase_exactly_once_across_invocations(target_repo, monkeypatch):
    """Every CLI invocation builds fresh mocks — the draft stage must stay
    idempotent over the FILES: after the spec gate, `approve` only authors the
    plan, no second Codex run against the already written spec draft."""
    import adw.cli as cli_mod

    real = cli_mod._dry_run_runners
    runners = []

    def recording():
        pair = real()
        runners.append(pair)
        return pair

    monkeypatch.setattr(cli_mod, "_dry_run_runners", recording)
    paused = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--spec-approval"],
    )
    assert paused.exit_code == 2, paused.output
    state = RunState.find_latest(target_repo)
    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 2, approved.output  # jetzt am Plan-Gate
    assert [call.kind for _, codex in runners for call in codex.author_calls] == ["spec", "plan"]


def test_dry_run_completes_when_the_codex_author_fails(target_repo, monkeypatch):
    """SPEC acceptance criterion 3 at CLI level: a broken Codex author degrades
    to a single source instead of killing the run."""
    import adw.cli as cli_mod
    from adw.codex import CodexAuthorError

    real = cli_mod._dry_run_runners

    def codex_spec_broken():
        agents, codex = real()
        codex.artifacts["spec"].clear()
        codex.script_author_error("spec", CodexAuthorError("Dry-Run: Codex-Entwurf kaputt"))
        return agents, codex

    monkeypatch.setattr(cli_mod, "_dry_run_runners", codex_spec_broken)
    result = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
    )
    assert result.exit_code == 0, result.output
    drafts = RunState.find_latest(target_repo).run_dir(target_repo) / "drafts"
    assert (drafts / "spec.codex.FAILED").is_file()
    assert not (drafts / "spec.codex.md").exists()
    assert (drafts / "spec.claude.md").read_text().strip()  # einquellig weitergelaufen


def test_status_shows_spec_approval_phase(target_repo):
    runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--spec-approval"],
    )
    result = runner.invoke(app, ["status", "--repo", str(target_repo)])
    assert result.exit_code == 0
    assert "awaiting_spec_approval" in result.output


# =====================================================================
# Aufgabe B / F3: Arbeitsbaum-Prüfung eskaliert NIE — sie verweigert
# höchstens (Run-State unverändert, resumierbar) oder heilt genau die
# sechs ADW-eigenen Authoring-Artefakte selbst.
# =====================================================================


def _paused_run(target_repo) -> RunState:
    """Startet einen Dry-Run, der regulär am Plan-Approval-Gate pausiert."""
    result = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    assert result.exit_code == 2, result.output
    return RunState.find_latest(target_repo)


def test_resume_self_heals_dirty_tracked_adw_artifact(target_repo):
    """B6(a): dirty getracktes .adw/spec.md + resume → Lauf läuft weiter, Datei
    zurückgesetzt (Selbstheilung), keine Eskalation."""
    state = _paused_run(target_repo)
    spec = target_repo / ".adw" / "spec.md"
    spec.write_text("# committete Spec\n")
    git(target_repo, "add", ".adw/spec.md")
    git(target_repo, "commit", "-m", "spec getrackt")
    spec.write_text("# committete Spec\n\nDIRTY REST\n")  # getrackt + dirty
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 2, res.output  # weiterhin awaiting_approval, kein Absturz
    assert spec.read_text() == "# committete Spec\n"  # per checkout zurückgesetzt
    saved = RunState.load(target_repo, state.run_id)
    assert saved.phase == "awaiting_approval"  # keine Eskalation
    assert not (saved.run_dir(target_repo) / "escalation.md").exists()


def test_resume_self_heals_untracked_adw_artifact(target_repo):
    """B: eine ungetrackte ADW-Artefakt-Datei allein wird per Löschen geheilt."""
    state = _paused_run(target_repo)
    summary = target_repo / ".adw" / "spec-summary.md"
    summary.write_text("# lose ADW-Datei\n")  # untracked ADW-Artefakt
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 2, res.output
    assert not summary.exists()  # per Löschen geheilt
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


def test_run_refuses_on_foreign_dirty_file_without_creating_a_run(target_repo):
    """B1/B4: dirty fremde Datei + `adw run` → Ausführung verweigert, kein neuer
    persistenter Run, fremde Datei bytegleich unangetastet."""
    (target_repo / "fremd_foo.py").write_text("dreckig\n")  # untracked fremd
    res = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"]
    )
    assert res.exit_code != 0  # verweigert
    assert res.exit_code != 2  # keine reguläre Approval-Pause
    with pytest.raises(StateNotFoundError):
        RunState.find_latest(target_repo)  # kein Run angelegt
    assert (target_repo / "fremd_foo.py").read_text() == "dreckig\n"  # nie verworfen


def test_resume_refuses_on_foreign_dirty_file_and_stays_resumable(target_repo):
    """B1/B4/B6(b): dirty fremde Datei + resume → verweigert, Run-State
    unverändert, fremde Datei bytegleich, ein späterer Resume bleibt möglich."""
    state = _paused_run(target_repo)
    readme = target_repo / "README.md"
    readme.write_text("# target\n\nFREMDER DIRTY EDIT\n")  # getrackt + dirty
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 1, res.output  # Verweigerung, keine Approval-Pause
    assert readme.read_text() == "# target\n\nFREMDER DIRTY EDIT\n"  # unangetastet
    saved = RunState.load(target_repo, state.run_id)
    assert saved.phase == "awaiting_approval"  # inhaltlich unverändert
    assert not (saved.run_dir(target_repo) / "escalation.md").exists()
    # Nach manueller Bereinigung ist der Resume möglich:
    readme.write_text("# target\n")
    res2 = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res2.exit_code == 2, res2.output  # wieder am Gate


def test_resume_refuses_mixed_dirty_set_without_discarding_anything(target_repo):
    """B4/B6(c): mind. ein ADW-Artefakt UND eine fremde Datei dirty → verweigert,
    NICHTS verworfen (keine Teilheilung), Run-State unverändert."""
    state = _paused_run(target_repo)
    spec = target_repo / ".adw" / "spec.md"
    spec.write_text("# S\n")
    git(target_repo, "add", ".adw/spec.md")
    git(target_repo, "commit", "-m", "spec")
    spec.write_text("# S\n\nADW DIRTY\n")  # ADW-Artefakt dirty
    readme = target_repo / "README.md"
    readme.write_text("# target\n\nFREMD DIRTY\n")  # fremd dirty
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 1, res.output
    assert spec.read_text() == "# S\n\nADW DIRTY\n"  # NICHT geheilt
    assert readme.read_text() == "# target\n\nFREMD DIRTY\n"  # NICHT verworfen
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


def test_approve_refuses_on_foreign_dirty_file_without_granting(target_repo):
    """B (approve-Pfad): dirty fremde Datei + `adw approve` → verweigert, fremde
    Datei bytegleich, KEINE Eskalation, die Zusage bleibt UNGESETZT, der Run
    steht unverändert am selben Gate."""
    state = _paused_run(target_repo)
    readme = target_repo / "README.md"
    readme.write_text("# target\n\nFREMD\n")
    res = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 1, res.output
    saved = RunState.load(target_repo, state.run_id)
    assert saved.phase == "awaiting_approval"  # unverändert am selben Gate
    assert saved.approval_granted is False  # Zusage NICHT gesetzt
    assert readme.read_text() == "# target\n\nFREMD\n"  # unangetastet
    assert not (saved.run_dir(target_repo) / "escalation.md").exists()


def test_approve_self_heals_adw_artifact_and_proceeds(target_repo):
    """B (approve-Pfad, Gegenprobe): ausschließlich dirty ADW-Artefakt → geheilt,
    `approve` läuft regulär durch."""
    state = _paused_run(target_repo)
    stray = target_repo / ".adw" / "spec.md"
    stray.write_text("# lose ADW-Datei\n")  # untracked ADW-Artefakt
    res = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 0, res.output
    assert RunState.load(target_repo, state.run_id).phase == "done"
    assert not stray.exists()  # geheilt


# --- Finding 1: der In-Phasen-Guard verweigert (keine Eskalation) -------------


def test_in_phase_worktree_refusal_maps_to_nonzero_exit_without_escalation(
    target_repo, monkeypatch
):
    """Finding 1: eine WorktreeRefusedError aus dem In-Phasen-Guard (Race: der
    Checkout wird nach dem Vorflug dirty) wird an der CLI-Grenze zu einem klaren
    Nichtnull-Exit — OHNE State-Save, OHNE escalation.md; der Run bleibt
    unverändert und resumierbar (B1)."""
    import adw.cli as cli_mod
    from adw.phases import WorktreeRefusedError

    state = _paused_run(target_repo)

    def refuse(ctx):
        raise WorktreeRefusedError(".adw/spec.md ist getrackt und hat uncommittete Änderungen")

    # Vorflug sah einen sauberen Baum; erst danach wird er dirty → der Guard greift.
    monkeypatch.setattr("adw.preflight.check_worktree", lambda repo: None)
    monkeypatch.setattr(cli_mod, "run_spec_and_plan", refuse)
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 1
    assert res.exit_code != 2  # keine Approval-Pause
    saved = RunState.load(target_repo, state.run_id)
    assert saved.phase == "awaiting_approval"  # unverändert, NICHT escalated
    assert not (saved.run_dir(target_repo) / "escalation.md").exists()


# --- Finding 2: fehlgeschlagenes git status / Heilung → Verweigerung ----------


def _git_plain_with_failure(monkeypatch, failing_subcommand: str):
    """Ersetzt preflight.git_plain so, dass genau ein Git-Subkommando fehlschlägt."""
    from adw import preflight

    real = preflight.git_plain

    def wrapper(repo, *args):
        if args[:1] == (failing_subcommand,):
            return subprocess.CompletedProcess(
                ["git", *args], 128, "", f"fatal: {failing_subcommand} kaputt"
            )
        return real(repo, *args)

    monkeypatch.setattr(preflight, "git_plain", wrapper)


def test_run_refuses_when_git_status_fails_without_creating_a_run(target_repo, monkeypatch):
    """Finding 2: ein fehlschlagendes `git status` gilt NICHT als sauberer Baum —
    der Vorflug verweigert, es wird kein Run angelegt."""
    _git_plain_with_failure(monkeypatch, "status")
    res = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"])
    assert res.exit_code != 0
    assert res.exit_code != 2
    with pytest.raises(StateNotFoundError):
        RunState.find_latest(target_repo)  # keine State-Mutation


def test_resume_refuses_when_git_status_fails_without_mutating_state(target_repo, monkeypatch):
    """Finding 2: fehlschlagendes `git status` beim Resume → Verweigerung, der
    Run-State bleibt unverändert und resumierbar."""
    state = _paused_run(target_repo)
    _git_plain_with_failure(monkeypatch, "status")
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code != 0
    assert res.exit_code != 2
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


def test_resume_refuses_when_self_heal_checkout_fails_without_discarding(target_repo, monkeypatch):
    """Finding 2: schlägt eine Heilungs-Operation (git checkout) fehl, verweigert
    der Vorflug, statt mit halb geheiltem Baum weiterzulaufen — nichts wird
    verworfen, der State bleibt unverändert."""
    state = _paused_run(target_repo)
    spec = target_repo / ".adw" / "spec.md"
    spec.write_text("# committete Spec\n")
    git(target_repo, "add", ".adw/spec.md")
    git(target_repo, "commit", "-m", "spec getrackt")
    spec.write_text("# committete Spec\n\nDIRTY REST\n")  # getracktes ADW-Artefakt, dirty
    _git_plain_with_failure(monkeypatch, "checkout")
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code != 0
    assert res.exit_code != 2
    assert spec.read_text() == "# committete Spec\n\nDIRTY REST\n"  # nicht verworfen
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


# --- Follow-up of run 72a042ad [P2]: self-heal must restore from HEAD ----------
#
# `git checkout -- <path>` restores from the INDEX, not from HEAD. A STAGED
# modification/deletion/rename of an ADW artifact therefore survives the heal,
# the following status check still sees a dirty tree, and the command refuses
# instead of healing and proceeding.


def test_resume_self_heals_a_staged_modification(target_repo):
    """A staged (not just unstaged) change to a tracked ADW artifact is healed —
    `git checkout --` alone would restore the staged content over itself."""
    state = _paused_run(target_repo)
    spec = target_repo / ".adw" / "spec.md"
    spec.write_text("# committete Spec\n")
    git(target_repo, "add", ".adw/spec.md")
    git(target_repo, "commit", "-m", "spec getrackt")

    spec.write_text("# committete Spec\n\nSTAGED REST\n")
    git(target_repo, "add", ".adw/spec.md")  # die Änderung liegt IM INDEX

    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 2, res.output  # geheilt und fortgesetzt, keine Verweigerung
    assert spec.read_text() == "# committete Spec\n"
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


def test_resume_self_heals_a_staged_deletion(target_repo):
    """A staged deletion of a tracked ADW artifact is healed back from HEAD."""
    state = _paused_run(target_repo)
    spec = target_repo / ".adw" / "spec.md"
    spec.write_text("# committete Spec\n")
    git(target_repo, "add", ".adw/spec.md")
    git(target_repo, "commit", "-m", "spec getrackt")

    git(target_repo, "rm", "--cached", "-q", ".adw/spec.md")  # aus dem Index entfernt
    spec.unlink()

    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 2, res.output
    assert spec.read_text() == "# committete Spec\n"
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"


# --- Forge-Erkennung im Vorflug -------------------------------------------------
# Ein unerkennbarer Host darf nicht erst in Phase 7 auffallen — nach dem kompletten
# Build. run/resume/approve verweigern VOR jeder Agent-Ausführung (Exit 1, keine
# Eskalation); der Dry-Run behält seinen GitLab-Rückfall.


def _drop_provider(target_repo) -> None:
    from tests.conftest import DEFAULT_CONFIG

    write_config(target_repo, DEFAULT_CONFIG.replace("  provider: gitlab\n", ""))
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "ohne provider")
    git(target_repo, "remote", "add", "origin", "git@git.example.org:team/app.git")


def _no_execution(monkeypatch) -> None:
    """Sicherheitsnetz: ohne Vorflug-Check liefe sonst ein echter (Token-)Lauf an."""
    import adw.cli as cli_mod

    def boom(ctx):
        raise AssertionError("_execute darf bei unerkennbarer Forge nicht laufen")

    monkeypatch.setattr(cli_mod, "_execute", boom)


def test_run_refuses_undetectable_forge_without_creating_a_run(target_repo, monkeypatch):
    _drop_provider(target_repo)
    _no_execution(monkeypatch)
    res = runner.invoke(app, ["run", "--repo", str(target_repo), "--issue", "Demo"])
    assert res.exit_code == 1, res.output
    assert "ci.provider" in res.output
    with pytest.raises(StateNotFoundError):
        RunState.find_latest(target_repo)


def test_dry_run_keeps_gitlab_fallback_for_undetectable_forge(target_repo):
    _drop_provider(target_repo)
    res = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"]
    )
    assert res.exit_code == 0, res.output
    assert RunState.find_latest(target_repo).phase == "done"


def _real_paused_run(target_repo) -> RunState:
    """Am Plan-Gate pausierter Lauf, als echter (nicht Dry-)Lauf markiert."""
    state = _paused_run(target_repo)
    state.dry_run = False
    state.save(target_repo)
    _drop_provider(target_repo)
    return state


@pytest.mark.parametrize("command", ["resume", "approve"])
def test_continuation_refuses_undetectable_forge_and_stays_at_the_gate(
    target_repo, monkeypatch, command
):
    state = _real_paused_run(target_repo)
    _no_execution(monkeypatch)
    res = runner.invoke(app, [command, state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 1, res.output
    assert "ci.provider" in res.output
    saved = RunState.load(target_repo, state.run_id)
    assert saved.phase == "awaiting_approval"
    assert saved.approval_granted is False
    assert not (saved.run_dir(target_repo) / "escalation.md").exists()


@pytest.mark.parametrize(
    "flags, mode",
    [(["--no-approval"], "none"), (["--spec-approval"], "both"),
     (["--no-approval", "--spec-approval"], "spec")],
)
def test_legacy_approval_flags_still_work_but_point_to_gates(target_repo, flags, mode):
    res = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", *flags]
    )
    assert res.exit_code in (0, 2), res.output
    assert "veraltet" in res.output
    assert f"--gates {mode}" in res.output


@pytest.mark.parametrize("mode", ["none", "spec", "plan", "both"])
def test_gates_flag_prints_no_deprecation_notice(target_repo, mode):
    res = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--gates", mode]
    )
    assert res.exit_code in (0, 2), res.output
    assert "veraltet" not in res.output


# =====================================================================
# C3: Repo-weiter Lock vor Preflight und Ausführung; Resume-Schutz mitten
# im Authoring.
# =====================================================================


@contextlib.contextmanager
def _held_repo_lock(target_repo):
    """Ein anderes ADW-Kommando hält den Repo-Lock (eigener Handle = eigener flock)."""
    from adw.worktrees import ensure_runs_gitignored

    ensure_runs_gitignored(target_repo)
    with open(target_repo / ".adw" / "runs" / ".repo.lock", "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _run_dirs(target_repo) -> list[str]:
    runs = target_repo / ".adw" / "runs"
    return sorted(p.name for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else []


@pytest.mark.parametrize("command", ["run", "resume", "approve"])
def test_busy_repo_lock_refuses_without_touching_anything(target_repo, command):
    state = None if command == "run" else _paused_run(target_repo)
    if state is None:
        args = ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run"]
    else:
        args = [command, state.run_id, "--repo", str(target_repo)]
        state_file = state.run_dir(target_repo) / "state.json"
        state_bytes = state_file.read_bytes()
    stray = target_repo / ".adw" / "spec.md"
    stray.write_text("# Zwischenstand eines anderen Laufs\n")  # sonst selbstheilbar
    with _held_repo_lock(target_repo):
        dirs_before = _run_dirs(target_repo)
        res = runner.invoke(app, args)
        assert res.exit_code == 1, res.output
        assert "bereits ein ADW-Kommando" in res.output
        assert _run_dirs(target_repo) == dirs_before  # kein neuer Run-Ordner
    assert stray.read_text() == "# Zwischenstand eines anderen Laufs\n"
    if state is not None:
        assert state_file.read_bytes() == state_bytes
        assert not (state.run_dir(target_repo) / "escalation.md").exists()


def test_first_run_creates_repo_lock_without_dirtying_the_checkout(target_repo):
    """Der Lock entsteht vor dem Preflight — die Lock-Datei darf den eigenen
    Arbeitsbaum-Check nicht scheitern lassen und bleibt danach liegen."""
    assert not (target_repo / ".adw" / "runs").exists()
    res = runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--gates", "none"]
    )
    assert res.exit_code == 0, res.output
    assert (target_repo / ".adw" / "runs" / ".repo.lock").is_file()
    assert (target_repo / ".adw" / "runs" / ".gitignore").is_file()
    assert ".adw/runs" not in git(target_repo, "status", "--porcelain", "--untracked-files=all")


def _mid_authoring_run(target_repo) -> RunState:
    """Lauf, der mitten in einer Fix-Runde der Plan-Synthese gecrasht ist."""
    state = _paused_run(target_repo)
    state.phase = "plan"
    state.authoring_session = "sess-plan-synthesis"
    state.save(target_repo)
    return state


def test_resume_mid_authoring_keeps_its_dirty_artifact(target_repo, monkeypatch):
    import adw.cli as cli_mod

    state = _mid_authoring_run(target_repo)
    plan = target_repo / ".adw" / "plan.md"
    plan.write_text("# Plan (reviewte Zwischenfassung)\n")
    seen = {}

    def execute(ctx):
        seen["plan"] = plan.read_text() if plan.exists() else None

    monkeypatch.setattr(cli_mod, "_execute", execute)
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 0, res.output  # Preflight verweigert nicht
    assert seen["plan"] == "# Plan (reviewte Zwischenfassung)\n"  # nach dem Preflight


def test_resume_mid_authoring_still_refuses_foreign_dirty_files(target_repo, monkeypatch):
    state = _mid_authoring_run(target_repo)
    plan = target_repo / ".adw" / "plan.md"
    plan.write_text("# Plan (reviewte Zwischenfassung)\n")
    readme = target_repo / "README.md"
    readme.write_text("# target\n\nFREMD DIRTY\n")
    _no_execution(monkeypatch)
    res = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert res.exit_code == 1, res.output
    assert plan.read_text() == "# Plan (reviewte Zwischenfassung)\n"
    assert readme.read_text() == "# target\n\nFREMD DIRTY\n"


# --- W2: effort per agent and codex.reasoning_effort ---------------------------


def _commit_config(repo, extra=""):
    """Write and COMMIT the config — the worktree preflight needs a clean checkout."""
    from tests.conftest import DEFAULT_CONFIG

    write_config(repo, DEFAULT_CONFIG + extra)
    git(repo, "add", ".adw/config.yaml")
    git(repo, "commit", "--allow-empty", "-m", "effort config")


def _agent_starts(repo, run_id, agent):
    path = repo.resolve() / ".adw" / "runs" / run_id / "events.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [
        r["payload"] for r in records
        if r["type"] == "agent.run" and r["kind"] == "start" and r["payload"]["agent"] == agent
    ]


def _dry_run(repo, *extra):
    return runner.invoke(
        app, ["run", "--repo", str(repo), "--issue", "Effort", "--dry-run", *extra]
    )


def test_unknown_effort_agent_fails_before_a_run_exists(target_repo):
    """AC 3: ConfigError at start — no run directory, no state."""
    _commit_config(target_repo, "effort:\n  bulid_agent: low\n")
    result = _dry_run(target_repo, "--no-approval")
    assert result.exit_code != 0
    runs = target_repo / ".adw" / "runs"
    assert not runs.exists() or not [p for p in runs.iterdir() if p.is_dir()]


def test_dry_run_start_payloads_carry_the_effective_effort(target_repo):
    """AC 4/E5: the mock runner sees the same start payload as the real one."""
    result = _dry_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    run_id = RunState.find_latest(target_repo).run_id
    build = _agent_starts(target_repo, run_id, "build_agent")
    review = _agent_starts(target_repo, run_id, "final_reviewer")
    assert build and {p["effort"] for p in build} == {"low"}
    assert review and all(p["effort"] is None for p in review)


@pytest.mark.parametrize(
    ("at_start", "later", "expected_build", "expected_codex"),
    [
        (
            "effort:\n  build_agent: medium\ncodex:\n  reasoning_effort: low\n",
            "effort:\n  build_agent: high\ncodex:\n  reasoning_effort: xhigh\n",
            "medium",
            "low",
        ),
        # an empty mapping is a real pin: entries added later do not apply
        ("", "effort:\n  build_agent: high\n", "low", "high"),
    ],
)
def test_effort_is_pinned_at_run_start(
    target_repo, at_start, later, expected_build, expected_codex
):
    """AC 5: changing the config after the run started does not change effort."""
    from adw.cli import _config_for_continuation

    _commit_config(target_repo, at_start)
    assert _dry_run(target_repo).exit_code == 2  # plan gate
    run_id = RunState.find_latest(target_repo).run_id
    _commit_config(target_repo, later)

    config, _ = _config_for_continuation(
        target_repo, RunState.load(target_repo, run_id), None
    )
    assert config.effort.get("build_agent", "low") == expected_build
    assert config.codex.reasoning_effort == expected_codex

    approved = runner.invoke(app, ["approve", run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 0, approved.output
    build = _agent_starts(target_repo, run_id, "build_agent")
    assert build and {p["effort"] for p in build} == {expected_build}
