"""RED tests for Aufgabe B — ``adw runs prune`` behaviour on the real filesystem
and git (Spec-Schritt 13, §4.5; AC C1–C8, B4, E9/E10).

Everything is exercised through the CLI against real temp git repos: run
directories, snapshot refs ``refs/adw/<run_id>/*``, registered git worktrees
under ``.adw/runs/<run_id>/trees/<lane>`` and lane branches ``adw/<run_id>/*``.
Only observable effects are asserted (which run dirs / refs / worktrees / branches
remain, what the output names), never internal helper signatures.

RED until the ``runs prune`` command and the retention core exist.
"""

import gzip
import subprocess
from datetime import UTC, datetime, timedelta, timezone

from typer.testing import CliRunner

from adw.cli import app
from adw.worktrees import lane_branch, lane_worktree_path
from tests.conftest import git, write_config
from tests.gui_app_helpers import (
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
    write_state_only_run,
)

cli = CliRunner()


# --- builders / observers -------------------------------------------------------


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if dt.utcoffset() == timedelta(0):
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # A non-UTC offset kept in the notation, so the normalise-to-UTC path is exercised.
    return dt.isoformat(timespec="milliseconds")


def make_run(repo, run_id, ts, *, phase="done", status="done", issue="Issue"):
    lines = [
        rec(1, "run", "start", "R", None, ts=ts, payload=run_start_payload(issue)),
        rec(2, "run", "end", "R", None, ts=ts, payload=run_end_payload(status)),
    ]
    return write_run(repo, run_id, lines, phase=phase)


def run_dir(repo, run_id):
    return repo / ".adw" / "runs" / run_id


def add_ref(repo, run_id, n=1):
    git(repo, "update-ref", f"refs/adw/{run_id}/{n}", "HEAD")


def refs_of(repo, run_id):
    out = git(repo, "for-each-ref", "--format=%(refname)", f"refs/adw/{run_id}/")
    return [line for line in out.splitlines() if line.strip()]


def worktree_list(repo):
    return git(repo, "worktree", "list")


def prune_dry(repo):
    return git(repo, "worktree", "prune", "--dry-run")


def branch_exists(repo, branch):
    result = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
    )
    return result.returncode == 0


def fresh_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-b", "staging")
    git(path, "config", "user.email", "adw-test@example.com")
    git(path, "config", "user.name", "ADW Test")
    (path / "README.md").write_text("# r\n")
    write_config(path)
    git(path, "add", ".")
    git(path, "commit", "-m", "init")
    return path


def prune(repo, *extra):
    return cli.invoke(app, ["runs", "prune", "--repo", str(repo), *extra])


# --- C8/C3: exact retention + snapshot refs ------------------------------------


def test_prune_keeps_exactly_n_and_drops_refs_of_removed(target_repo):
    """C8: with everything terminal and safe, --keep N leaves exactly the newest N
    run dirs; every removed run loses its snapshot refs, every kept run keeps
    them."""
    ids = ["aaaa0001", "aaaa0002", "aaaa0003", "aaaa0004", "aaaa0005"]
    base = datetime(2026, 8, 1, 12, 0, 0)
    for i, rid in enumerate(ids):
        make_run(target_repo, rid, _iso(base + timedelta(hours=i)))
        add_ref(target_repo, rid, 1)

    result = prune(target_repo, "--keep", "2")
    assert result.exit_code == 0, result.output

    remaining = [rid for rid in ids if run_dir(target_repo, rid).is_dir()]
    assert remaining == ids[-2:]  # the newest two, by canonical date
    for rid in ids[:3]:
        assert refs_of(target_repo, rid) == []
    for rid in ids[-2:]:
        assert refs_of(target_repo, rid) != []


# --- C3: deleting a run WITH a registered worktree, no orphan, branch kept -------


def test_prune_removes_registered_worktree_without_orphan(target_repo):
    rid = "aaaa1111"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    add_ref(target_repo, rid, 1)
    from adw.worktrees import create_lane_worktree

    create_lane_worktree(target_repo, rid, "backend", "staging")
    assert rid in worktree_list(target_repo)

    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output

    assert not run_dir(target_repo, rid).is_dir()
    assert rid not in worktree_list(target_repo)
    # No orphaned registration: a rmtree would leave one and dry-run would report it.
    assert prune_dry(target_repo).strip() == ""
    assert refs_of(target_repo, rid) == []
    # The lane branch survives (Deferred: branch deletion).
    assert branch_exists(target_repo, lane_branch(rid, "backend"))


# --- C2: non-terminal runs are never pruned, only skipped and named -------------


def test_prune_skips_non_terminal_run_and_names_it(target_repo):
    rid = "aaaa2222"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)), phase="build", status="running")
    add_ref(target_repo, rid, 1)

    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output  # a safety skip is not an error

    assert run_dir(target_repo, rid).is_dir()  # fully preserved
    assert refs_of(target_repo, rid) != []
    assert rid in result.output
    assert "build" in result.output  # named with its phase


# --- C4: uncommitted worktree protection ---------------------------------------


def test_prune_skips_entire_run_when_a_worktree_is_dirty(target_repo):
    """C4: a dirty worktree in the NOT-first-found lane (frontend > backend) skips
    the WHOLE run — no worktree removed, nothing else deleted, other candidates
    unaffected. ``prune`` never discards foreign work."""
    from adw.worktrees import create_lane_worktree

    rid = "aaaa3333"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    add_ref(target_repo, rid, 1)
    create_lane_worktree(target_repo, rid, "backend", "staging")
    create_lane_worktree(target_repo, rid, "frontend", "staging")
    (lane_worktree_path(target_repo, rid, "frontend") / "dirty.txt").write_text("x\n")

    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output

    assert rid in result.output
    assert run_dir(target_repo, rid).is_dir()
    assert refs_of(target_repo, rid) != []
    assert rid in worktree_list(target_repo)  # neither worktree forcibly removed


def test_prune_removes_all_clean_worktrees_orphan_free(target_repo):
    """C4 (clean case): every lane worktree of a safe run is removed via git, so no
    orphan registration remains and both lane branches survive."""
    from adw.worktrees import create_lane_worktree

    rid = "aaaa4444"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    create_lane_worktree(target_repo, rid, "backend", "staging")
    create_lane_worktree(target_repo, rid, "frontend", "staging")

    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output

    assert not run_dir(target_repo, rid).is_dir()
    assert rid not in worktree_list(target_repo)
    assert prune_dry(target_repo).strip() == ""
    assert branch_exists(target_repo, lane_branch(rid, "backend"))
    assert branch_exists(target_repo, lane_branch(rid, "frontend"))


# --- C1/B4: --older-than boundary and UTC-offset normalisation ------------------


def test_prune_older_than_prunes_old_keeps_young_across_offsets(target_repo):
    """C1/B4: --older-than DAYS uses ``run_time <= now_utc - DAYS*24h`` over the
    UTC-normalised canonical date. A run older than the boundary is pruned even
    when its start ts carried a non-UTC offset; a younger run is kept."""
    now = datetime.now(UTC)
    old = "aaaa5555"
    young = "aaaa6666"
    offset_old = "aaaa7777"
    make_run(target_repo, old, _iso(now - timedelta(days=7, minutes=1)))
    make_run(target_repo, young, _iso(now - timedelta(days=1)))
    # Same 8-days-old instant, expressed with a +05:00 offset (must normalise).
    instant = (now - timedelta(days=8)).astimezone(timezone(timedelta(hours=5)))
    make_run(target_repo, offset_old, _iso(instant))

    result = prune(target_repo, "--keep", "0", "--older-than", "7")
    assert result.exit_code == 0, result.output

    assert not run_dir(target_repo, old).is_dir()
    assert not run_dir(target_repo, offset_old).is_dir()
    assert run_dir(target_repo, young).is_dir()


# --- B4: deterministic tie-break + legacy fallback date -------------------------


def test_prune_tie_break_on_equal_dates_is_deterministic(tmp_path):
    """B4: with identical canonical dates the tie-break (run_id) is deterministic —
    two identical repos keep the SAME run under --keep 1 (direction not pinned)."""
    ts = "2026-08-01T12:00:00.000Z"
    ids = ["aaaa0001", "aaaa0002", "aaaa0003"]
    kept = []
    for name in ("r1", "r2"):
        repo = fresh_repo(tmp_path / name)
        for rid in ids:
            make_run(repo, rid, ts)
        result = prune(repo, "--keep", "1")
        assert result.exit_code == 0, result.output
        remaining = [rid for rid in ids if run_dir(repo, rid).is_dir()]
        assert len(remaining) == 1, remaining
        kept.append(remaining[0])
    assert kept[0] == kept[1]


def test_prune_handles_legacy_run_without_start_event(target_repo):
    """B4: a legacy run without a start event still gets a canonical date (state
    mtime fallback) and is prunable."""
    rid = "aaaa8888"
    write_state_only_run(target_repo, rid, phase="done")  # no events.jsonl
    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output
    assert not run_dir(target_repo, rid).is_dir()


# --- C7: resuming a partially-completed deleting prune --------------------------


def test_prune_resumes_after_partial_deletion(target_repo):
    """C7: a re-run continues safely — an already-removed snapshot ref is not an
    error, the remaining components are cleaned up."""
    rid = "aaaa9999"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    add_ref(target_repo, rid, 1)
    add_ref(target_repo, rid, 2)
    # Simulate an interrupted earlier prune: one ref already gone.
    git(target_repo, "update-ref", "-d", f"refs/adw/{rid}/1")

    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output
    assert not run_dir(target_repo, rid).is_dir()
    assert refs_of(target_repo, rid) == []


# --- C5/E10: --gzip is the KEEPING form ----------------------------------------


def test_gzip_preserves_run_refs_worktree_and_roundtrips(target_repo):
    from adw.worktrees import create_lane_worktree

    rid = "bbbb0001"
    rd = make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    add_ref(target_repo, rid, 1)
    create_lane_worktree(target_repo, rid, "backend", "staging")
    original = (rd / "events.jsonl").read_bytes()

    result = prune(target_repo, "--keep", "0", "--gzip")
    assert result.exit_code == 0, result.output
    assert rid in result.output

    # Kept form: everything preserved (E10).
    assert rd.is_dir() and (rd / "state.json").is_file()
    assert refs_of(target_repo, rid) != []
    assert rid in worktree_list(target_repo)
    assert branch_exists(target_repo, lane_branch(rid, "backend"))
    # Compressed: source gone, gz round-trips to the original bytes.
    gz = rd / "events.jsonl.gz"
    assert gz.is_file() and not (rd / "events.jsonl").exists()
    assert gzip.decompress(gz.read_bytes()) == original


def test_gzip_already_compressed_and_recompresses_interrupted(target_repo):
    """C5: 'already compressed' iff .gz exists and .jsonl is absent (left as-is).
    Both present means an interrupted attempt — .jsonl is authoritative and is
    recompressed, replacing the stale .gz."""
    # already compressed → untouched
    rid1 = "bbbb0002"
    rd1 = make_run(target_repo, rid1, _iso(datetime(2026, 8, 1)))
    data1 = (rd1 / "events.jsonl").read_bytes()
    with gzip.open(rd1 / "events.jsonl.gz", "wb") as fh:
        fh.write(data1)
    (rd1 / "events.jsonl").unlink()

    result = prune(target_repo, "--keep", "0", "--gzip")
    assert result.exit_code == 0, result.output
    assert (rd1 / "events.jsonl.gz").is_file() and not (rd1 / "events.jsonl").exists()
    assert gzip.decompress((rd1 / "events.jsonl.gz").read_bytes()) == data1

    # interrupted: both present, a STALE .gz — the .jsonl is authoritative
    rid2 = "bbbb0003"
    rd2 = make_run(target_repo, rid2, _iso(datetime(2026, 8, 2)))
    authoritative = (rd2 / "events.jsonl").read_bytes()
    with gzip.open(rd2 / "events.jsonl.gz", "wb") as fh:
        fh.write(b"stale bytes\n")

    result = prune(target_repo, "--keep", "0", "--gzip")
    assert result.exit_code == 0, result.output
    assert not (rd2 / "events.jsonl").exists()
    assert gzip.decompress((rd2 / "events.jsonl.gz").read_bytes()) == authoritative
