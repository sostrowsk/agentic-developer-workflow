"""RED tests for Aufgabe B — the CLI surface of ``adw runs list`` / ``adw runs
prune`` (Spec-Schritt 13, §4.5; AC B1–B3, C1, C7).

Derived from .adw/spec.md, .adw/contract.yaml (x-adw-cli) and .adw/plan.md §9.
Only the externally observable CLI surface is pinned here: the ``runs`` group and
its two subcommands, the flags, the exit codes and that a value renders visibly
rather than crashing. The exact column layout and message wording are NOT pinned.
Behavioural filesystem/git effects live in ``tests/test_retention.py``.

RED until the ``runs`` group exists (today ``adw --help`` knows only run/resume/
approve/status/gui).
"""

import re

from typer.testing import CliRunner

from adw.cli import app
from tests.conftest import git  # noqa: F401 — kept for parity with sibling tests
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
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


def test_runs_group_is_registered_with_list_and_prune(home):  # noqa: F811
    """B1: ``adw runs --help`` succeeds (the group exists) and advertises both
    subcommands. This is the observable proof the ``runs`` group is registered."""
    result, text = _help_text("runs")
    assert result.exit_code == 0, result.output
    assert "list" in text and "prune" in text


def test_prune_help_advertises_exactly_the_pinned_flags(home):  # noqa: F811
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


def test_list_unusable_repo_exits_one(home, tmp_path):  # noqa: F811
    """B3: a non-existent/unusable --repo yields a clear error and exit 1."""
    missing = tmp_path / "does-not-exist"
    result = cli.invoke(app, ["runs", "list", "--repo", str(missing)])
    assert result.exit_code == 1, result.output


def test_list_non_git_directory_exits_one(home, tmp_path):  # noqa: F811
    """B3: an existing but non-git directory is not a usable repo — clear error,
    exit 1, never a silent success."""
    plain = tmp_path / "plain"
    (plain / ".adw" / "runs").mkdir(parents=True)
    result = cli.invoke(app, ["runs", "list", "--repo", str(plain)])
    assert result.exit_code == 1, result.output


def test_prune_non_git_directory_exits_one(home, tmp_path):  # noqa: F811
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


def test_prune_rejects_negative_keep_with_exit_two(home, tmp_path):  # noqa: F811
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", _done_lines(), phase="done")
    result = cli.invoke(app, ["runs", "prune", "--repo", str(repo), "--keep", "-1"])
    assert result.exit_code == 2, result.output
    # No run data was touched by a rejected invocation.
    assert (repo / ".adw" / "runs" / "aaaa1111").is_dir()
