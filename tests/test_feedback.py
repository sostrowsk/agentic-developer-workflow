"""W14: ``adw rate`` and ``adw feedback`` — the human verdict as JSON lines in the
ledger's run folder. Every test uses a throwaway ledger clone (``ledger`` fixture)
or the absent default ledger; nothing touches ~/.adw/ledger (E10)."""

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adw import ledger as ledger_mod
from adw.cli import app
from adw.worktrees import ensure_runs_gitignored
from tests.conftest import git

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures" / "ledger"
RUN_ID = "a1b2c3d4"
SLUG = "acme/widget"
# finding_keys of the fixture run (tests/fixtures/ledger/expected-run-record.json)
KEYS = ["spec.md|Spec lässt Exit-Pfad offen", "spec.md|AC 3 nicht testbar", "x.py|Null-Check fehlt"]


@pytest.fixture
def repo(target_repo):
    """The target repo with a fixed origin, so repo_slug is ``acme/widget``."""
    git(target_repo, "remote", "add", "origin", "git@example.com:acme/widget.git")
    return target_repo


def install_fixture_run(repo: Path) -> Path:
    run_dir = repo / ".adw" / "runs" / RUN_ID
    shutil.copytree(FIXTURES / RUN_ID, run_dir)
    ensure_runs_gitignored(repo)
    return run_dir


@pytest.fixture
def exported(repo, ledger) -> Path:
    """The fixture run, exported into the ledger; returns the run's ledger folder."""
    install_fixture_run(repo)
    assert ledger_mod.export_run(repo, RUN_ID) == "exported"
    return ledger / "runs" / SLUG.replace("/", "__") / RUN_ID


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def commit_count(ledger: Path) -> int:
    return int(git(ledger, "rev-list", "--count", "HEAD"))


def assert_utc(ts: str) -> None:
    assert datetime.fromisoformat(ts).utcoffset() == timedelta(0)


# --- AC 7: adw rate --------------------------------------------------------------------


def test_rate_appends_commits_and_survives_a_re_export(repo, ledger, exported):
    res = runner.invoke(app, ["rate", RUN_ID, "bad", "--note", "x", "--repo", str(repo)])

    assert res.exit_code == 0, res.output
    feedback = exported / "feedback.jsonl"
    [entry] = lines(feedback)
    assert set(entry) == {"ts", "kind", "value", "note"}
    assert (entry["kind"], entry["value"], entry["note"]) == ("rate", "bad", "x")
    assert_utc(entry["ts"])
    assert git(ledger, "log", "-1", "--format=%s") == f"feedback {RUN_ID} rate"
    assert git(ledger, "status", "--porcelain") == ""
    pushed = git(ledger.parent / "ledger-origin.git", "rev-parse", "main")
    assert pushed == git(ledger, "rev-parse", "HEAD")

    before = feedback.read_bytes()
    run_dir = repo / ".adw" / "runs" / RUN_ID
    (run_dir / "retro.md").write_text("# Retro\n", encoding="utf-8")  # forces a rewrite
    assert ledger_mod.export_run(repo, RUN_ID) == "exported"
    assert feedback.read_bytes() == before
    assert git(ledger, "status", "--porcelain") == ""

    res = runner.invoke(app, ["rate", RUN_ID, "good", "--repo", str(repo)])
    assert res.exit_code == 0, res.output
    first, second = lines(feedback)
    assert first == entry
    assert (second["kind"], second["value"], second["note"]) == ("rate", "good", None)


# --- AC 8: adw feedback ----------------------------------------------------------------


def test_feedback_with_unique_prefix_stores_the_full_finding_key(repo, ledger, exported):
    res = runner.invoke(
        app, ["feedback", RUN_ID, "--finding", "x.py", "noise", "--repo", str(repo)]
    )

    assert res.exit_code == 0, res.output
    [entry] = lines(exported / "feedback.jsonl")
    assert entry["finding_key"] == "x.py|Null-Check fehlt"
    assert set(entry) == {"ts", "kind", "value", "note", "finding_key"}
    assert (entry["kind"], entry["value"], entry["note"]) == ("finding", "noise", None)
    assert_utc(entry["ts"])
    assert git(ledger, "log", "-1", "--format=%s") == f"feedback {RUN_ID} finding"


@pytest.mark.parametrize("prefix", ["spec.md|", "gibt-es-nicht"], ids=["ambiguous", "unknown"])
def test_feedback_with_ambiguous_or_unknown_prefix_writes_nothing(
    repo, ledger, exported, prefix
):
    before = commit_count(ledger)

    res = runner.invoke(
        app, ["feedback", RUN_ID, "--finding", prefix, "valid", "--repo", str(repo)]
    )

    assert res.exit_code == 1, res.output
    assert res.stderr.strip()
    if prefix == "spec.md|":
        assert KEYS[0] in res.stderr and KEYS[1] in res.stderr
    else:  # no hit: all keys of the run are the candidates
        assert all(key in res.stderr for key in KEYS)
    assert not (exported / "feedback.jsonl").exists()
    assert commit_count(ledger) == before


# --- AC 9: no exported run / no ledger / invalid value ---------------------------------


@pytest.mark.parametrize("case", ["not-exported", "no-ledger", "invalid-value"])
def test_rate_without_exported_run_or_with_invalid_value_writes_nothing(repo, request, case):
    """``ledger`` is requested lazily: the no-ledger case keeps the absent default."""
    install_fixture_run(repo)
    value = "meh" if case == "invalid-value" else "bad"
    ledger = None if case == "no-ledger" else request.getfixturevalue("ledger")
    if case == "invalid-value":
        assert ledger_mod.export_run(repo, RUN_ID) == "exported"
    before = commit_count(ledger) if ledger else None

    res = runner.invoke(app, ["rate", RUN_ID, value, "--repo", str(repo)])

    assert res.exit_code != 0, res.output
    if case != "invalid-value":
        assert res.exit_code == 1, res.output
        assert res.stderr.strip()
    if ledger is None:
        assert not Path(os.environ["ADW_LEDGER"]).exists()
        return
    run_folder = ledger / "runs" / SLUG.replace("/", "__") / RUN_ID
    if case == "not-exported":
        assert not (ledger / "runs").exists()
    else:
        assert not (run_folder / "feedback.jsonl").exists()
    assert commit_count(ledger) == before
