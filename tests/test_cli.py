"""CLI-Tests (typer CliRunner) — Dry-Run-Modus, 0 Tokens, echtes git."""

import json

from typer.testing import CliRunner

from adw.cli import app
from adw.state import RunState
from tests.conftest import write_config

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
    """Regression (Codex P1): Crasht der allererste Agent-Lauf, muss die
    angezeigte run_id trotzdem per `adw resume` auffindbar sein."""
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
    """Regression (Codex P1): --base-branch beim `run` muss auch nach der
    Approval-Pause gelten, ohne dass `approve` das Flag wiederholt."""
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
    """Regression (Codex P2): Config-Validierung VOR dem glab-Netzaufruf."""
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
    """PLAN Task 11: Der Dry-Run enthält einen simulierten Gate-Fail, dessen
    Fix als Folge-Task an dieselbe Session geht (2 Gate-Iterationen)."""
    result = runner.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", "--no-approval"],
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    assert state.lanes["backend"].gate_iterations == 2  # Fail → Fix → Pass


def test_parallel_dry_run_exercises_e2e_triage_path(target_repo):
    """SPEC DoD 1: --dry-run --parallel durchläuft den E2E-Triage-Pfad."""
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
    """Dry-Run-Runner, deren Build-Agent beim ZWEITEN Lauf crasht (nur 1 Antwort
    gescriptet) — hinterlässt einen Gate-Fail-Checkpoint im State."""
    import adw.cli as cli_mod

    real = cli_mod._dry_run_runners

    def limited():
        agents, codex = real()
        agents.scripts["build_agent"].clear()
        agents.script("build_agent", "einziger Lauf")
        return agents, codex

    monkeypatch.setattr(cli_mod, "_dry_run_runners", limited)


def test_dry_run_resume_after_gate_fail_checkpoint(target_repo, monkeypatch):
    """Regression (Codex P2): Die Dry-Run-Simulation muss ihren Stand aus dem
    Worktree ableiten — ein Resume mit frischem Mock darf den checkpointeten
    Gate-Fix nicht wiederholen und in den Circuit-Breaker laufen."""
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
    """Regression (Codex P2): Lanes sind vom alten Base geforkt — ein
    nachträglicher --base-branch-Wechsel würde inkonsistente Diffs erzeugen."""
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
    """Regression (Codex P2): Ein abgelehnter Override darf den State nicht
    vergiften — der nächste `resume` ohne Flag muss normal funktionieren."""
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
    """Regression (Codex P1): Ändert sich base_branch in der config.yaml mitten
    im Run, darf ein Resume die bestehenden Lanes nicht gegen die neue Basis
    integrieren — der effektive Base-Branch ist ab Run-Start gepinnt."""
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
    write_config(
        target_repo, PARALLEL_CLI_CONFIG.replace("base_branch: staging", "base_branch: develop")
    )
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
    """Plan-Betrieb: Ein fehlgeschlagener SDK-Call (z. B. Abo-Fenster erschöpft)
    beendet den Run kontrolliert mit Resume-Hinweis — er eskaliert NICHT
    (phase=escalated wäre nicht fortsetzbar) und crasht nicht mit Traceback."""
    import adw.cli as cli_mod
    from adw.agents import AgentRunError

    real = cli_mod._dry_run_runners

    def limit_exhausted_on_build():
        agents, codex = real()

        original_run = agents.run

        def run(agent, task, cwd, resume=None, deny_read_paths=None):
            if agent.name == "build_agent":
                raise AgentRunError("Claude-CLI: usage limit reached — resets 14:00")
            return original_run(agent, task, cwd, resume, deny_read_paths)

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
