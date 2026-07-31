"""CLI tests (typer CliRunner) — dry-run mode, 0 tokens, real git."""

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


# --- B2: Spec-Approval-Stopp (CLI) ------------------------------------------


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
