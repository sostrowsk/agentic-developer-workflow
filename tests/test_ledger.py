"""W13: run record (build_run_record), ledger export (export_run), the export after
run/resume/approve and ``adw ledger export``. Every export goes into a throwaway
ledger clone with a local bare ``origin`` (``ledger`` fixture); nothing touches
~/.adw/ledger or the network (E10)."""

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import adw.cli as cli_mod
from adw import ledger as ledger_mod
from adw import retention
from adw.cli import app
from adw.state import RunState
from adw.worktrees import ensure_runs_gitignored
from tests.conftest import git
from tests.gui_app_helpers import rec, run_end_payload, run_start_payload, write_run

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures" / "ledger"
RUN_ID = "a1b2c3d4"
SLUG = "acme/widget"
GOLDEN = json.loads((FIXTURES / "expected-run-record.json").read_text(encoding="utf-8"))
BUNDLE = {
    "run-record.json",
    "events.jsonl.gz",
    "issue.md",
    "spec.md",
    "plan.md",
    "contract.yaml",
    "spec-summary.md",
    "plan-summary.md",
    "followups.md",
    "escalation.md",
    "authoring-spec-known-findings.md",
    "state.json",
}


# --- builders / observers -------------------------------------------------------


@pytest.fixture
def repo(target_repo):
    """The target repo with a fixed origin, so repo_slug is ``acme/widget``."""
    git(target_repo, "remote", "add", "origin", "git@example.com:acme/widget.git")
    return target_repo


def install_fixture_run(repo: Path) -> Path:
    """Install the fixture run like a real run leaves it — including the
    self-ignoring ``.adw/runs/.gitignore`` every run writes with its first event."""
    run_dir = repo / ".adw" / "runs" / RUN_ID
    shutil.copytree(FIXTURES / RUN_ID, run_dir)
    ensure_runs_gitignored(repo)
    return run_dir


def edit_state(run_dir: Path, **changes) -> None:
    path = run_dir / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def gzip_log(run_dir: Path) -> bytes:
    plain = run_dir / "events.jsonl"
    data = plain.read_bytes()
    (run_dir / "events.jsonl.gz").write_bytes(gzip.compress(data))
    plain.unlink()
    return data


def ledger_dir(ledger: Path, run_id: str = RUN_ID, slug: str = SLUG) -> Path:
    return ledger / "runs" / slug.replace("/", "__") / run_id


def commit_count(repo: Path) -> int:
    return int(git(repo, "rev-list", "--count", "HEAD"))


def bare_head(ledger: Path) -> str:
    """HEAD of main in the ledger fixture's bare origin (independent of the
    clone's current remote URL)."""
    return git(ledger.parent / "ledger-origin.git", "rev-parse", "main")


def export_events(run_dir: Path) -> list[dict]:
    return [r for r in retention._iter_events(run_dir) if r.get("type") == "ledger.export"]


def break_push(ledger: Path, tmp_path: Path) -> None:
    git(ledger, "remote", "set-url", "origin", str(tmp_path / "unreachable.git"))


# --- AC 1/2: build_run_record ---------------------------------------------------


def test_build_run_record_matches_golden_file(repo):
    """AC 1: two run spans, one approval, review rounds, triage decisions, agent
    spans and one truncated line → exactly the golden record."""
    install_fixture_run(repo)
    assert ledger_mod.build_run_record(repo, RUN_ID) == GOLDEN


def test_findings_and_review_counts_without_round_or_phase_ancestors_are_null(repo):
    """W3 A0: phase/round/loop come only from the span hierarchy — a decision on
    the run span, a review whose parent has no start event and a cyclic parent
    chain yield null (no hang), even with top-level phase/round set."""
    run_dir = install_fixture_run(repo)
    run_span = "5b1c2e7a90d34f1e8a6b2c4d1e0f9a87"
    top = {"phase": "codex_review", "lane": None, "round": 9}
    extra = [
        {"type": "lane", "kind": "start", "span": "c1", "parent": "c2", "payload": {}},
        {"type": "lane", "kind": "start", "span": "c2", "parent": "c1", "payload": {}},
        {"type": "triage.decision", "kind": "point", "span": run_span, "parent": None,
         "payload": {"finding_key": "a|run", "severity": "P2", "action": "fix", "reason": "r"}},
        {"type": "triage.decision", "kind": "point", "span": "c1", "parent": "c2",
         "payload": {"finding_key": "b|cycle", "severity": "P2", "action": "fix", "reason": "r"}},
        {"type": "codex.review", "kind": "start", "span": "v1", "parent": "missing", "payload": {}},
        {"type": "codex.review", "kind": "end", "span": "v1", "parent": "missing",
         "payload": {"findings": []}},
        {"type": "codex.review", "kind": "start", "span": "v2", "parent": "c2", "payload": {}},
        {"type": "codex.review", "kind": "end", "span": "v2", "parent": "c2",
         "payload": {"findings": [{}]}},
    ]
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        for i, event in enumerate(extra, 100):
            line = {"seq": i, "ts": "2026-09-15T09:29:30.000Z", **top, **event}
            fh.write(json.dumps(line) + "\n")

    record = ledger_mod.build_run_record(repo, RUN_ID)

    unknown = {"phase": None, "round": None, "loop": None}
    assert record["findings"][:3] == GOLDEN["findings"]
    assert record["findings"][3:] == [
        {"finding_key": "a|run", "severity": "P2", "action": "fix", "reason": "r", **unknown},
        {"finding_key": "b|cycle", "severity": "P2", "action": "fix", "reason": "r", **unknown},
    ]
    assert record["review_counts"][:3] == GOLDEN["review_counts"]
    assert record["review_counts"][3:] == [{**unknown, "n": 0}, {**unknown, "n": 1}]


def test_build_run_record_tolerates_missing_files_and_gzip_log(repo):
    """AC 2: no escalation.md/followups.md → null/0; the same log as .gz only
    yields the identical record."""
    run_dir = install_fixture_run(repo)
    (run_dir / "escalation.md").unlink()
    (run_dir / "followups.md").unlink()
    plain_record = ledger_mod.build_run_record(repo, RUN_ID)
    assert plain_record == {**GOLDEN, "escalation_reason": None, "followups": 0}

    gzip_log(run_dir)
    assert ledger_mod.build_run_record(repo, RUN_ID) == plain_record


# --- AC 3/4/5/8/9: export_run ---------------------------------------------------


def test_export_writes_bundle_commits_and_pushes(repo, ledger):
    """AC 3: bundle per §4 (no drafts/, no trees/), final.diff from the lane,
    exactly one commit with the pinned message, pushed to the bare origin."""
    run_dir = install_fixture_run(repo)
    (run_dir / "trees" / "backend").mkdir(parents=True)
    (run_dir / "trees" / "backend" / "work.py").write_text("x = 1\n")
    base_sha = git(repo, "rev-parse", "HEAD")
    branch = f"adw/{RUN_ID}/backend"
    git(repo, "checkout", "-q", "-b", branch)
    (repo / "README.md").write_text("# target\n\nchanged by the lane\n")
    git(repo, "commit", "-q", "-am", "lane work")
    git(repo, "checkout", "-q", "staging")
    lanes = json.loads((run_dir / "state.json").read_text())["lanes"]
    lanes["backend"]["base_sha"] = base_sha
    edit_state(run_dir, lanes=lanes)
    log_bytes = (run_dir / "events.jsonl").read_bytes()
    state_bytes = (run_dir / "state.json").read_bytes()
    target_head = git(repo, "rev-parse", "HEAD")
    target_status = git(repo, "status", "--porcelain")
    before = commit_count(ledger)

    assert ledger_mod.export_run(repo, RUN_ID) == "exported"

    dest = ledger_dir(ledger)
    files = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert files == BUNDLE | {"final.diff"}
    record_text = (dest / "run-record.json").read_text(encoding="utf-8")
    assert record_text == json.dumps(GOLDEN, sort_keys=True, indent=2) + "\n"
    assert (dest / "state.json").read_bytes() == state_bytes
    assert (dest / "spec.md").read_bytes() == (run_dir / "spec.md").read_bytes()
    gz = (dest / "events.jsonl.gz").read_bytes()
    assert gz[4:8] == b"\x00\x00\x00\x00"  # mtime=0
    assert not gz[3] & 0x08  # no file name in the header
    assert gzip.decompress(gz) == log_bytes
    expected_diff = subprocess.run(
        ["git", "-C", str(repo), "diff", f"{base_sha}..{branch}"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert (dest / "final.diff").read_text() == expected_diff
    assert "changed by the lane" in expected_diff

    assert commit_count(ledger) == before + 1
    assert git(ledger, "log", "-1", "--format=%s") == f"run {RUN_ID} escalated ({SLUG})"
    assert bare_head(ledger) == git(ledger, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", "HEAD") == target_head
    assert git(repo, "status", "--porcelain") == target_status
    [event] = export_events(run_dir)
    assert event["kind"] == "point"
    assert event["payload"] == {"outcome": "exported", "pushed": True, "reason": None}


def test_second_export_without_change_is_unchanged(repo, ledger):
    """AC 4: the first export's own ledger.export event does not count as change."""
    run_dir = install_fixture_run(repo)
    assert ledger_mod.export_run(repo, RUN_ID) == "exported"
    after_first = commit_count(ledger)

    assert ledger_mod.export_run(repo, RUN_ID) == "unchanged"

    assert commit_count(ledger) == after_first
    payloads = [e["payload"] for e in export_events(run_dir)]
    assert payloads[-1] == {"outcome": "unchanged", "pushed": True, "reason": None}


def test_gzip_only_log_gets_event_appended_without_new_plain_log(repo, ledger):
    """Plan hint 2: with only events.jsonl.gz the event is appended to the .gz (a
    new events.jsonl would later overwrite it); a re-export stays unchanged."""
    run_dir = install_fixture_run(repo)
    original = gzip_log(run_dir)

    assert ledger_mod.export_run(repo, RUN_ID) == "exported"

    assert not (run_dir / "events.jsonl").exists()
    assert retention._is_valid_gzip(run_dir / "events.jsonl.gz")
    [event] = export_events(run_dir)
    assert event["payload"]["outcome"] == "exported"
    bundled = gzip.decompress((ledger_dir(ledger) / "events.jsonl.gz").read_bytes())
    assert bundled == original
    assert ledger_mod.export_run(repo, RUN_ID) == "unchanged"


def test_push_failure_keeps_exported_and_commits_locally(repo, ledger, tmp_path, capsys):
    """AC 5: unreachable origin → exported, local commit, warning, pushed false."""
    run_dir = install_fixture_run(repo)
    break_push(ledger, tmp_path)
    before = commit_count(ledger)

    assert ledger_mod.export_run(repo, RUN_ID) == "exported"

    assert commit_count(ledger) == before + 1
    assert capsys.readouterr().err.strip()
    [event] = export_events(run_dir)
    assert event["payload"] == {"outcome": "exported", "pushed": False, "reason": None}


def test_no_ledger_repo_skips_without_creating_anything(repo, tmp_path, monkeypatch, capsys):
    """AC 8: ADW_LEDGER on a plain directory → skipped, message, reason no_ledger."""
    run_dir = install_fixture_run(repo)
    state_bytes = (run_dir / "state.json").read_bytes()
    plain = tmp_path / "not-a-ledger"
    plain.mkdir()
    monkeypatch.setenv("ADW_LEDGER", str(plain))

    assert ledger_mod.export_run(repo, RUN_ID) == "skipped"

    assert list(plain.iterdir()) == []
    assert capsys.readouterr().err.strip()
    [event] = export_events(run_dir)
    assert event["payload"] == {"outcome": "skipped", "pushed": None, "reason": "no_ledger"}
    assert (run_dir / "state.json").read_bytes() == state_bytes


def test_dry_run_is_not_exported(repo, ledger):
    """AC 9: a dry run → skipped/dry_run, no files and no commit in the ledger."""
    run_dir = install_fixture_run(repo)
    edit_state(run_dir, dry_run=True)
    before = commit_count(ledger)

    assert ledger_mod.export_run(repo, RUN_ID) == "skipped"

    assert not (ledger / "runs").exists()
    assert commit_count(ledger) == before
    [event] = export_events(run_dir)
    assert event["payload"] == {"outcome": "skipped", "pushed": None, "reason": "dry_run"}


# --- AC 5/6/7: export after run / resume / approve -------------------------------


def _fake_execute(outcome: str):
    """Stand-in for cli._execute: sets the phase a real execution would reach and
    leaves the way the real one does (escalation exit 1, gate pause exit 2)."""
    phase, code = {"escalate": ("escalated", 1), "pause": ("awaiting_approval", 2),
                   "done": ("done", 0)}[outcome]

    def execute(ctx):
        ctx.state.phase = phase
        ctx.state.save(ctx.repo)
        if code:
            raise typer.Exit(code)

    return execute


def _start_run(repo, monkeypatch, outcome: str):
    monkeypatch.setattr(cli_mod, "_execute", _fake_execute(outcome))
    res = runner.invoke(app, ["run", "--repo", str(repo), "--issue", "Demo", "--gates", "none"])
    return res, RunState.find_latest(repo)


@pytest.fixture
def no_preflight(monkeypatch):
    monkeypatch.setattr(cli_mod, "_preflight", lambda *a, **k: None)


@pytest.mark.parametrize(
    ("command", "outcome", "exit_code", "phase", "push_broken"),
    [
        ("run", "escalate", 1, "escalated", False),
        ("resume", "pause", 2, "awaiting_approval", False),
        ("approve", "done", 0, "done", True),
    ],
)
def test_every_exit_path_is_exported_with_unchanged_exit_code(
    repo, ledger, no_preflight, monkeypatch, tmp_path, command, outcome, exit_code, phase,
    push_broken,
):
    """AC 7 (escalation, gate pause) and AC 5 via the CLI (unreachable origin):
    the run lands in the ledger, exit code and phase stay as without export."""
    if command == "run":
        res, state = _start_run(repo, monkeypatch, outcome)
    else:
        _, state = _start_run(repo, monkeypatch, "pause")
        if push_broken:
            break_push(ledger, tmp_path)
        monkeypatch.setattr(cli_mod, "_execute", _fake_execute(outcome))
        res = runner.invoke(app, [command, state.run_id, "--repo", str(repo)])

    assert res.exit_code == exit_code, res.output
    assert RunState.load(repo, state.run_id).phase == phase
    exported = ledger_dir(ledger, state.run_id)
    record = json.loads((exported / "run-record.json").read_text(encoding="utf-8"))
    assert record["phase"] == phase
    assert git(ledger, "log", "-1", "--format=%s") == f"run {state.run_id} {phase} ({SLUG})"
    pushed = bare_head(ledger) == git(ledger, "rev-parse", "HEAD")
    assert pushed is not push_broken


def test_export_exception_keeps_exit_code_and_phase(repo, ledger, no_preflight, monkeypatch):
    """AC 6: an exception inside the export → same exit/phase, stderr message,
    ledger.export event with outcome failed."""

    def boom(*args, **kwargs):
        raise RuntimeError("record kaputt")

    monkeypatch.setattr(ledger_mod, "build_run_record", boom)
    res, state = _start_run(repo, monkeypatch, "done")

    assert res.exit_code == 0, res.output
    assert RunState.load(repo, state.run_id).phase == "done"
    assert res.stderr.strip()
    [event] = export_events(state.run_dir(repo))
    assert event["payload"]["outcome"] == "failed"
    assert "record kaputt" in event["payload"]["reason"]


# --- AC 11: adw ledger export ---------------------------------------------------


def _done_run(repo, run_id, ts):
    lines = [
        rec(1, "run", "start", "R", None, ts=ts, payload=run_start_payload("Issue")),
        rec(2, "run", "end", "R", None, ts=ts, payload=run_end_payload("done")),
    ]
    write_run(repo, run_id, lines, phase="done")


def test_ledger_export_command_exports_all_runs_in_start_order(repo, ledger, monkeypatch):
    """AC 11: one line per run in start order; a failed run does not stop the
    others and makes the exit code 1; a clean re-run exits 0."""
    _done_run(repo, "cccc0001", "2026-08-01T10:00:00.000Z")
    _done_run(repo, "bbbb0002", "2026-08-02T10:00:00.000Z")
    _done_run(repo, "aaaa0003", "2026-08-03T10:00:00.000Z")
    real_build = ledger_mod.build_run_record

    def flaky(repo_, run_id):
        if run_id == "bbbb0002":
            raise RuntimeError("kaputt")
        return real_build(repo_, run_id)

    monkeypatch.setattr(ledger_mod, "build_run_record", flaky)
    res = runner.invoke(app, ["ledger", "export", "--repo", str(repo)])
    assert res.exit_code == 1, res.output
    assert res.stdout.splitlines() == [
        "cccc0001: exported",
        "bbbb0002: failed",
        "aaaa0003: exported",
    ]

    monkeypatch.setattr(ledger_mod, "build_run_record", real_build)
    res = runner.invoke(app, ["ledger", "export", "--repo", str(repo)])
    assert res.exit_code == 0, res.output
    assert res.stdout.splitlines() == [
        "cccc0001: unchanged",
        "bbbb0002: exported",
        "aaaa0003: unchanged",
    ]
    assert (ledger_dir(ledger, "bbbb0002") / "run-record.json").is_file()


def test_ledger_export_command_rejects_unknown_run_id(repo, ledger):
    res = runner.invoke(app, ["ledger", "export", "deadbeef", "--repo", str(repo)])
    assert res.exit_code == 1, res.output
    assert not (ledger / "runs").exists()


# --- review findings --------------------------------------------------------------


def test_backfill_export_holds_the_run_lock_against_a_concurrent_prune(repo, ledger, monkeypatch):
    """Review P2: while ``adw ledger export`` exports a run, a manual prune must not
    export and delete the same run — otherwise the backfill overwrites the ledger
    bundle with one missing state, events and artifacts."""
    install_fixture_run(repo)
    _done_run(repo, "ffff0001", "2099-01-01T00:00:00.000Z")  # newest: the kept guard
    real_build = ledger_mod.build_run_record
    prune_results = []
    pruned = []

    def build_then_prune(repo_, run_id):
        record = real_build(repo_, run_id)
        if run_id == RUN_ID and not pruned:
            pruned.append(True)  # once: the prune's own export re-enters here
            prune_results.append(retention.prune(repo_, keep=1))
        return record

    monkeypatch.setattr(ledger_mod, "build_run_record", build_then_prune)
    res = runner.invoke(app, ["ledger", "export", RUN_ID, "--repo", str(repo)])

    assert res.exit_code == 0, res.output
    [results] = prune_results
    assert [(r.run_id, r.action, r.reason) for r in results] == [
        (RUN_ID, "skipped", "läuft noch")
    ]
    assert (repo / ".adw" / "runs" / RUN_ID / "state.json").is_file()
    files = {p.name for p in ledger_dir(ledger).iterdir()}
    assert BUNDLE <= files


@pytest.mark.parametrize(
    "tail", [b"", b'{"seq": 23, "ts": "2026-09-15T09:3'], ids=["complete", "truncated"]
)
def test_gzip_event_append_starts_on_a_fresh_line(repo, ledger, tail):
    """Review P2: a .gz log without a final newline (complete last record or a
    crash-truncated fragment) must not swallow the appended ledger.export event;
    the bundle stays stable across re-exports."""
    run_dir = install_fixture_run(repo)
    plain = run_dir / "events.jsonl"
    data = plain.read_bytes().rstrip(b"\n") + (b"\n" + tail if tail else b"")
    (run_dir / "events.jsonl.gz").write_bytes(gzip.compress(data))
    plain.unlink()
    before = list(retention._iter_events(run_dir))

    assert ledger_mod.export_run(repo, RUN_ID) == "exported"

    after = list(retention._iter_events(run_dir))
    assert after[:-1] == before
    assert after[-1]["type"] == "ledger.export"
    assert after[-1]["payload"]["outcome"] == "exported"
    assert ledger_mod.export_run(repo, RUN_ID) == "unchanged"
