"""Tests for the CLI surface of ``adw runs list`` / ``adw runs prune``."""

import re
import subprocess

import pytest
from typer.testing import CliRunner

from adw.cli import app
from tests.conftest import git  # noqa: F401 — kept for parity with sibling tests
from tests.gui_app_helpers import (
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
    write_state_only_run,
)

cli = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help_text(*args):
    """The ``--help`` output as a stable, normalized string. Typer/Rich colours and
    WIDTH-WRAPS (even truncating long option names with an ellipsis) at the ambient
    terminal width, which differs between a local TTY and a non-TTY CI shell. Pinning
    a wide COLUMNS for the invocation and stripping ANSI + collapsing wrap-whitespace
    makes the rendered surface deterministic across environments."""
    result = cli.invoke(app, [*args, "--help"], env={"COLUMNS": "200"})
    text = " ".join(_ANSI.sub("", result.output).split())
    return result, text


def _done_lines(issue="Issue text", ts="2026-08-05T14:00:00.000Z"):
    return [
        rec(1, "run", "start", "R", None, ts=ts, payload=run_start_payload(issue)),
        rec(2, "run", "end", "R", None, ts=ts, payload=run_end_payload("done")),
    ]


# --- B1: the command group exists with exactly list + prune ---------------------


def test_runs_group_is_registered_with_list_and_prune(home):
    """B1: ``adw runs --help`` succeeds (the group exists) and advertises both
    subcommands. This is the observable proof the ``runs`` group is registered."""
    result, text = _help_text("runs")
    assert result.exit_code == 0, result.output
    assert "list" in text and "prune" in text


def test_prune_help_advertises_exactly_the_pinned_flags(home):
    """C1/E5: prune's surface is exactly --repo/--keep/--older-than/--gzip."""
    result, text = _help_text("runs", "prune")
    assert result.exit_code == 0, result.output
    for flag in ("--repo", "--keep", "--older-than", "--gzip"):
        assert flag in text, flag


# --- B3: list exit codes --------------------------------------------------------


def test_list_empty_repo_exits_zero(target_repo):
    """B3: a valid (git) repo with no runs still succeeds (exit 0)."""
    (target_repo / ".adw" / "runs").mkdir(parents=True, exist_ok=True)
    result = cli.invoke(app, ["runs", "list", "--repo", str(target_repo)])
    assert result.exit_code == 0, result.output


def test_list_unusable_repo_exits_one(home, tmp_path):
    """B3: a non-existent/unusable --repo yields a clear error and exit 1."""
    missing = tmp_path / "does-not-exist"
    result = cli.invoke(app, ["runs", "list", "--repo", str(missing)])
    assert result.exit_code == 1, result.output


def test_list_non_git_directory_exits_one(home, tmp_path):
    """B3: an existing but non-git directory is not a usable repo — clear error,
    exit 1, never a silent success."""
    plain = tmp_path / "plain"
    (plain / ".adw" / "runs").mkdir(parents=True)
    result = cli.invoke(app, ["runs", "list", "--repo", str(plain)])
    assert result.exit_code == 1, result.output


def test_prune_non_git_directory_exits_one(home, tmp_path):
    """B3/C7: prune on an existing but non-git directory is exit 1 (unusable repo)."""
    plain = tmp_path / "plain"
    (plain / ".adw" / "runs").mkdir(parents=True)
    result = cli.invoke(app, ["runs", "prune", "--repo", str(plain)])
    assert result.exit_code == 1, result.output


# --- B2: visible per-run fields, including legacy unknowns -----------------------


def test_list_shows_run_id_and_phase(target_repo):
    """B2: each recognised run shows at least its run-id and phase."""
    write_run(target_repo, "aaaa1111", _done_lines(), phase="done")
    result = cli.invoke(app, ["runs", "list", "--repo", str(target_repo)])
    assert result.exit_code == 0, result.output
    assert "aaaa1111" in result.output
    assert "done" in result.output


def test_list_legacy_run_without_log_does_not_crash(target_repo):
    """B2: a legacy run whose event count/log size cannot be determined (no
    events.jsonl) is still listed — a value renders visibly instead of aborting."""
    write_state_only_run(target_repo, "8f8dc4ff", phase="done")
    result = cli.invoke(app, ["runs", "list", "--repo", str(target_repo)])
    assert result.exit_code == 0, result.output
    assert "8f8dc4ff" in result.output


# --- C1: invalid flag values are a plain CLI error (exit 2), no data change ------


def test_prune_rejects_negative_keep_with_exit_two(home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", _done_lines(), phase="done")
    result = cli.invoke(app, ["runs", "prune", "--repo", str(repo), "--keep", "-1"])
    assert result.exit_code == 2, result.output
    # No run data was touched by a rejected invocation.
    assert (repo / ".adw" / "runs" / "aaaa1111").is_dir()


def test_prune_rejects_keep_zero_with_exit_two(home, tmp_path):
    """`trace.keep_runs: 0` means "never prune" — a manual `--keep 0` that wiped every
    run would invert that meaning, so it is rejected; at least one run stays."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", _done_lines(), phase="done")
    result = cli.invoke(app, ["runs", "prune", "--repo", str(repo), "--keep", "0"])
    assert result.exit_code == 2, result.output
    assert "--keep" in result.output
    assert (repo / ".adw" / "runs" / "aaaa1111").is_dir()


# --- C12: git failures inside the retention are reported, not a traceback --------


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["git", "worktree", "list"], 60),
        OSError("git nicht ausführbar"),
    ],
    ids=["timeout", "oserror"],
)
def test_prune_reports_a_git_failure_with_the_runs_already_processed(
    target_repo, monkeypatch, error
):
    """C12: git times out (or cannot be executed) after the first candidate was
    deleted. ``adw runs prune`` exits 1 with a message naming the git command and
    the cause — no traceback — and still reports the run already deleted."""
    import adw.retention as retention_mod

    write_run(target_repo, "aaaa0001", _done_lines(ts="2026-08-01T12:00:00.000Z"), phase="done")
    write_run(target_repo, "aaaa0002", _done_lines(ts="2026-08-02T12:00:00.000Z"), phase="done")
    write_run(target_repo, "ffff9999", _done_lines(ts="2099-01-01T12:00:00.000Z"), phase="done")
    real_run_git = retention_mod.run_git
    listings = []

    def failing_second_listing(repo, *args, **kwargs):
        if args[:2] == ("worktree", "list"):
            listings.append(args)
            if len(listings) == 2:  # the second candidate's worktree inventory
                raise error
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(retention_mod, "run_git", failing_second_listing)
    result = cli.invoke(app, ["runs", "prune", "--repo", str(target_repo), "--keep", "1"])

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), repr(result.exception)  # no traceback
    assert "aaaa0001: deleted" in result.output  # the achieved state is reported
    assert "aaaa0002" in result.output  # the failing candidate is named
    assert "git worktree list" in result.output  # the command …
    assert str(error) in result.output  # … and the cause
    assert not (target_repo / ".adw" / "runs" / "aaaa0001").exists()  # no rollback
    assert (target_repo / ".adw" / "runs" / "aaaa0002").is_dir()


def test_prune_rejects_negative_older_than_with_a_message(home, tmp_path):
    """R5/AK 6: --older-than < 0 → exit 2 WITH a stderr message naming the option."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", _done_lines(), phase="done")
    result = cli.invoke(app, ["runs", "prune", "--repo", str(repo), "--older-than", "-1"])
    assert result.exit_code == 2, result.output
    assert "--older-than" in result.stderr
    assert "0" in result.stderr
    assert (repo / ".adw" / "runs" / "aaaa1111").is_dir()
