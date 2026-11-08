"""Tests for the ``trace:`` config block and automatic retention (GUI-SPEC §4.5)."""

import re

import pytest
from typer.testing import CliRunner

from adw.cli import app
from adw.config import AdwConfig, ConfigError
from adw.state import RunState
from adw.worktrees import ensure_runs_gitignored
from tests.conftest import git, write_config
from tests.gui_app_helpers import (
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
    write_state_only_run,
)

cli = CliRunner()

_BASE = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
ci:
  provider: gitlab
  staging_job: deploy-staging
"""


def _write(repo, trace_block):
    write_config(repo, _BASE + trace_block)
    git(repo, "add", ".adw/config.yaml")
    git(repo, "commit", "-m", "trace config")


def _seed_done_run(repo, run_id, ts):
    lines = [
        rec(1, "run", "start", "R", None, ts=ts, payload=run_start_payload("seed")),
        rec(2, "run", "end", "R", None, ts=ts, payload=run_end_payload("done")),
    ]
    write_run(repo, run_id, lines, phase="done")
    # A real ADW repo always has .adw/runs gitignored (created on first run); seed
    # it so the pre-existing run dirs don't read as an uncommitted worktree.
    ensure_runs_gitignored(repo)


def _prune_as_real_run(monkeypatch):
    """The CLI runs below are dry-runs (0 tokens), but a dry-run never auto-prunes
    (C11). Hand the auto-prune the finished state as a REAL run, so the D3 rules
    are still exercised through the real `run`/`approve`/`resume` call sites."""
    import adw.cli as cli_mod

    real = cli_mod._maybe_auto_prune

    def as_real_run(repo, config, state):
        return real(repo, config, state.model_copy(update={"dry_run": False}))

    monkeypatch.setattr(cli_mod, "_maybe_auto_prune", as_real_run)


# --- D1: config contract --------------------------------------------------------


def test_trace_defaults_when_absent(target_repo):
    """D1: a missing trace block yields enabled=true, keep_runs=20."""
    cfg = AdwConfig.load(target_repo)
    assert cfg.trace.enabled is True
    assert cfg.trace.keep_runs == 20


def test_trace_enabled_must_be_a_real_boolean(target_repo):
    write_config(target_repo, _BASE + "trace:\n  enabled: yes-please\n")
    with pytest.raises(ConfigError, match="enabled|trace"):
        AdwConfig.load(target_repo)


def test_trace_keep_runs_rejects_negative_and_boolean(target_repo):
    write_config(target_repo, _BASE + "trace:\n  keep_runs: -1\n")
    with pytest.raises(ConfigError, match="keep_runs|trace"):
        AdwConfig.load(target_repo)
    # A YAML boolean does NOT count as a valid integer (strict).
    write_config(target_repo, _BASE + "trace:\n  keep_runs: true\n")
    with pytest.raises(ConfigError, match="keep_runs|trace"):
        AdwConfig.load(target_repo)


# --- D2: configurable event logging --------------------------------------------


def _cli_run(repo, *extra):
    argv = ["run", "--repo", str(repo), "--issue", "Trace-Demo", "--dry-run", "--no-approval"]
    return cli.invoke(app, [*argv, *extra])


def test_disabled_trace_writes_no_event_log_but_run_completes(target_repo):
    """D2: with trace.enabled=false no events.jsonl is created or appended; the run
    still completes and its state persists."""
    _write(target_repo, "trace:\n  enabled: false\n")
    result = _cli_run(target_repo)
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "done"
    assert not (state.run_dir(target_repo) / "events.jsonl").exists()


# --- D3: automatic pruning after a successful run -------------------------------


def test_auto_prune_after_success_keeps_newest_and_removes_older(target_repo, monkeypatch):
    """D3: after a run finishes ``done`` with keep_runs=N, deleting-prune keeps the
    newest N runs (the just-finished one plus older ones) and removes the rest."""
    _prune_as_real_run(monkeypatch)
    _write(target_repo, "trace:\n  keep_runs: 2\n")
    for i, rid in enumerate(["cccc0001", "cccc0002", "cccc0003"]):
        _seed_done_run(target_repo, rid, f"2026-08-0{i + 1}T12:00:00.000Z")

    result = _cli_run(target_repo)
    assert result.exit_code == 0, result.output

    remaining = [
        rid for rid in ["cccc0001", "cccc0002", "cccc0003"]
        if (target_repo / ".adw" / "runs" / rid).is_dir()
    ]
    # keep_runs=2: the just-finished run + the newest seed survive; two oldest go.
    assert remaining == ["cccc0003"], remaining


def test_keep_runs_zero_disables_auto_prune(target_repo):
    """D3: keep_runs=0 disables automatic pruning entirely — every seeded run
    survives a successful run."""
    _write(target_repo, "trace:\n  keep_runs: 0\n")
    seeds = ["cccc0001", "cccc0002", "cccc0003"]
    for i, rid in enumerate(seeds):
        _seed_done_run(target_repo, rid, f"2026-08-0{i + 1}T12:00:00.000Z")

    result = _cli_run(target_repo)
    assert result.exit_code == 0, result.output
    for rid in seeds:
        assert (target_repo / ".adw" / "runs" / rid).is_dir()


def test_auto_prune_never_removes_a_non_terminal_run(target_repo, monkeypatch):
    """D3/C2: automatic pruning obeys the safety rule — a non-terminal seeded run
    is skipped, and the just-finished run stays consistently done + exit 0."""
    _prune_as_real_run(monkeypatch)
    _write(target_repo, "trace:\n  keep_runs: 1\n")
    # An old, non-terminal run that a keep_runs=1 prune would otherwise target.
    write_state_only_run(target_repo, "cccc0009", phase="build")
    ensure_runs_gitignored(target_repo)

    result = _cli_run(target_repo)
    assert result.exit_code == 0, result.output
    assert (target_repo / ".adw" / "runs" / "cccc0009").is_dir()  # preserved
    assert RunState.find_latest(target_repo).phase == "done"


def _remaining_seeds(repo, seeds):
    return [rid for rid in seeds if (repo / ".adw" / "runs" / rid).is_dir()]


def test_auto_prune_runs_after_approve_reaches_done(target_repo, monkeypatch):
    """D3: a run that reaches done through ``approve`` triggers auto-pruning too."""
    _prune_as_real_run(monkeypatch)
    _write(target_repo, "trace:\n  keep_runs: 2\n")
    seeds = ["cccc0001", "cccc0002", "cccc0003"]
    for i, rid in enumerate(seeds):
        _seed_done_run(target_repo, rid, f"2026-08-0{i + 1}T12:00:00.000Z")

    # No --no-approval → the run pauses at the plan gate (exit 2).
    paused = cli.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Approve-Demo", "--dry-run"]
    )
    assert paused.exit_code == 2, paused.output
    run_id = RunState.find_latest(target_repo).run_id

    approved = cli.invoke(app, ["approve", run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 0, approved.output
    assert RunState.load(target_repo, run_id).phase == "done"
    # keep_runs=2: the just-approved run + newest seed survive; two oldest go.
    assert _remaining_seeds(target_repo, seeds) == ["cccc0003"]


def test_auto_prune_runs_after_resume_reaches_done(target_repo, monkeypatch):
    """D3: a run that reaches done through ``resume`` triggers auto-pruning too."""
    import adw.cli as cli_mod

    _prune_as_real_run(monkeypatch)
    _write(target_repo, "trace:\n  keep_runs: 2\n")
    seeds = ["cccc0001", "cccc0002", "cccc0003"]
    for i, rid in enumerate(seeds):
        _seed_done_run(target_repo, rid, f"2026-08-0{i + 1}T12:00:00.000Z")

    real = cli_mod._dry_run_runners

    def crashing():
        agents, codex = real()
        agents.scripts["build_agent"].clear()
        agents.script("build_agent", "nur ein Lauf")  # the second build run crashes
        return agents, codex

    with monkeypatch.context() as m:
        m.setattr(cli_mod, "_dry_run_runners", crashing)
        crashed = _cli_run(target_repo)
        assert crashed.exit_code != 0, crashed.output
    run_id = RunState.find_latest(target_repo).run_id
    assert RunState.load(target_repo, run_id).phase == "build"  # died mid-build
    # A crashed run does NOT auto-prune (it never reached done).
    assert _remaining_seeds(target_repo, seeds) == seeds

    resumed = cli.invoke(app, ["resume", run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 0, resumed.output
    assert RunState.load(target_repo, run_id).phase == "done"
    assert _remaining_seeds(target_repo, seeds) == ["cccc0003"]


def test_auto_prune_reports_a_returned_partial_failure(target_repo, monkeypatch, capsys):
    """D3: auto-pruning is fail-open — the finished run keeps `done` and the success
    exit code — but a RETURNED partial failure (not an exception) must still be
    reported visibly, not swallowed."""
    from adw import cli as cli_mod
    from adw.retention import PruneResult

    def fake_prune(repo, **kwargs):
        return [PruneResult("dead0001", "failed", reason="1 worktree(s) removed; frontend stuck")]

    monkeypatch.setattr(cli_mod.retention, "prune", fake_prune)

    state = type("S", (), {"phase": "done", "dry_run": False, "run_id": "beef0001"})()
    config = type("C", (), {"trace": type("T", (), {"keep_runs": 5})()})()
    cli_mod._maybe_auto_prune(target_repo, config, state)

    err = capsys.readouterr().err
    assert "dead0001" in err, f"partial failure not reported: {err!r}"
    assert "unvollst" in err.lower() or "failed" in err.lower()



# --- Follow-up of run d1c9de00 [P3]: the fail-open path itself is now covered ---


def test_failing_auto_prune_leaves_the_run_done_and_exit_zero(target_repo, monkeypatch):
    """D3 fail-open: the existing test only covered a safety SKIP. This provokes a
    real auto-prune ERROR and pins all three promises — exit 0, the finished run
    stays `done`, and the failure is REPORTED rather than swallowed."""
    import adw.cli as cli_mod

    _write(target_repo, "trace:\n  keep_runs: 1\n")
    write_state_only_run(target_repo, "cccc0011", phase="done")
    ensure_runs_gitignored(target_repo)
    _prune_as_real_run(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError("Platte voll")

    monkeypatch.setattr(cli_mod.retention, "prune", boom)

    result = _cli_run(target_repo)
    assert result.exit_code == 0, result.output
    assert RunState.find_latest(target_repo).phase == "done"
    assert "Auto-Pruning fehlgeschlagen" in result.output
    assert "Platte voll" in result.output
    # Der Seed-Lauf wurde nicht angefasst — der Fehler kam vor jeder Entfernung.
    assert (target_repo / ".adw" / "runs" / "cccc0011").is_dir()


# --- C11: no auto-prune after a dry-run, the own run is never a candidate ------


def test_dry_run_reaching_done_never_auto_prunes_real_runs(target_repo):
    """C11: a dry-run that reaches done leaves every real run alone — even with
    keep_runs=1 and an older, terminal run that a real run would prune."""
    _write(target_repo, "trace:\n  keep_runs: 1\n")
    _seed_done_run(target_repo, "cccc0001", "2026-08-01T12:00:00.000Z")

    result = _cli_run(target_repo)
    assert result.exit_code == 0, result.output
    assert RunState.find_latest(target_repo).phase == "done"
    assert (target_repo / ".adw" / "runs" / "cccc0001").is_dir()
    assert "Auto-Pruning" not in result.output


def test_auto_prune_never_removes_the_run_that_just_finished(target_repo, monkeypatch):
    """C11: a run finishes done while a NEWER run (by start time) takes the single
    keep_runs slot — like a run that waited at the plan gate for a week. Auto-prune
    removes older runs but never the just-finished run, nor names it."""
    _prune_as_real_run(monkeypatch)
    _write(target_repo, "trace:\n  keep_runs: 1\n")
    _seed_done_run(target_repo, "cccc0001", "2026-08-01T12:00:00.000Z")
    _seed_done_run(target_repo, "cccc0099", "2099-01-01T12:00:00.000Z")  # newer than ours

    result = _cli_run(target_repo)
    assert result.exit_code == 0, result.output
    own_id = re.search(r"Run (\w+) gestartet", result.output).group(1)
    own_dir = target_repo / ".adw" / "runs" / own_id
    assert own_dir.is_dir()  # own run survives …
    assert RunState.load(target_repo, own_id).phase == "done"
    assert own_id not in result.output.split("Auto-Pruning", 1)[-1]  # … and is not named
    assert _remaining_seeds(target_repo, ["cccc0001", "cccc0099"]) == ["cccc0099"]
