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
import os
import shutil
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


def test_prune_reports_partial_failure_when_a_later_worktree_removal_refuses(
    target_repo, monkeypatch
):
    """C4 (race window): the inventory finds every worktree clean, but a LATER
    worktree turns dirty before its removal and git refuses it — after an earlier
    worktree was already removed.

    "skipped" means "the whole run is intact" (C4). Once a worktree is gone that
    is no longer true, so the run must be reported as a PARTIAL FAILURE with the
    achieved state and a nonzero exit — never as an intact safety skip.
    """
    from adw import retention
    from adw.worktrees import WorktreeError, create_lane_worktree

    rid = "aaaa7777"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    add_ref(target_repo, rid, 1)
    create_lane_worktree(target_repo, rid, "backend", "staging")
    create_lane_worktree(target_repo, rid, "frontend", "staging")

    # First removal succeeds, the second refuses — exactly the race the inventory
    # cannot rule out (an external process dirties a worktree in between).
    real = retention.remove_registered_worktree
    calls = []

    def flaky(repo, path):
        calls.append(path)
        if len(calls) == 1:
            return real(repo, path)
        raise WorktreeError(f"worktree enthält uncommittete Änderungen: {path}")

    monkeypatch.setattr(retention, "remove_registered_worktree", flaky)

    result = prune(target_repo, "--keep", "0")

    assert len(calls) == 2, "expected a second removal attempt"
    assert result.exit_code != 0, (
        "a run that lost a worktree must not end with the success exit code:\n"
        + result.output
    )
    assert "skipped" not in result.output.lower(), (
        "the run is NOT an intact safety skip — one worktree is already gone:\n"
        + result.output
    )
    assert rid in result.output
    # The achieved state is preserved and reported: refs and run dir untouched,
    # so a later prune can safely continue.
    assert run_dir(target_repo, rid).is_dir()
    assert refs_of(target_repo, rid) != []


def test_partial_failure_does_not_stop_the_remaining_candidates(target_repo, monkeypatch):
    """C4: a partial failure on one run must not abort the sweep — every other safe
    candidate is still processed, and the command still ends nonzero."""
    from adw import retention
    from adw.worktrees import WorktreeError, create_lane_worktree

    broken, safe = "aaaa8888", "aaaa9999"
    make_run(target_repo, broken, _iso(datetime(2026, 8, 1)))
    create_lane_worktree(target_repo, broken, "backend", "staging")
    create_lane_worktree(target_repo, broken, "frontend", "staging")
    make_run(target_repo, safe, _iso(datetime(2026, 8, 2)))
    add_ref(target_repo, safe, 1)

    real = retention.remove_registered_worktree
    calls = []

    def flaky(repo, path):
        calls.append(path)
        if len(calls) == 1:
            return real(repo, path)
        raise WorktreeError(f"worktree enthält uncommittete Änderungen: {path}")

    monkeypatch.setattr(retention, "remove_registered_worktree", flaky)

    result = prune(target_repo, "--keep", "0")

    assert result.exit_code != 0, result.output
    # The failing run is reported ...
    assert broken in result.output
    # ... and the later safe candidate was processed anyway.
    assert not run_dir(target_repo, safe).exists(), (
        "the safe candidate after the failing one was not processed:\n" + result.output
    )
    assert refs_of(target_repo, safe) == []


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


def test_gzip_run_without_any_log_is_skipped(target_repo):
    """C5: a terminal run with NEITHER events.jsonl NOR events.jsonl.gz is skipped
    (named), never falsely reported as already compressed, and fully preserved."""
    rid = "aaaacccc"
    write_state_only_run(target_repo, rid, phase="done")
    result = prune(target_repo, "--keep", "0", "--gzip")
    assert result.exit_code == 0, result.output
    assert rid in result.output and "skipped" in result.output
    assert (run_dir(target_repo, rid) / "state.json").is_file()


def test_gzip_compression_failure_exits_one_and_keeps_plain(target_repo):
    """C5/C7: a compression that cannot be completed safely is exit 1, and the
    authoritative plain log is left untouched (never unlinked on failure)."""
    rid = "aaaadddd"
    rd = make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    original = (rd / "events.jsonl").read_bytes()
    # A non-empty directory at the .gz target name makes os.replace fail.
    gzdir = rd / "events.jsonl.gz"
    gzdir.mkdir()
    (gzdir / "keep").write_text("x\n")

    result = prune(target_repo, "--keep", "0", "--gzip")
    assert result.exit_code == 1, result.output
    assert (rd / "events.jsonl").read_bytes() == original  # authoritative log kept


# --- P1: symlinked run directory / event log must never be touched outside the repo


def test_prune_ignores_symlinked_run_directory_outside_repo(target_repo, tmp_path):
    """A run directory that is a symlink to somewhere OUTSIDE the repo is never a
    candidate — neither --gzip nor deleting prune may read, replace or delete
    anything through it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    log = outside / "events.jsonl"
    log.write_text('{"seq": 1, "type": "run", "kind": "start", "payload": {}}\n')
    original = log.read_bytes()
    runs = target_repo / ".adw" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, runs / "aaaa1111")

    gz_result = prune(target_repo, "--keep", "0", "--gzip")
    assert gz_result.exit_code == 0, gz_result.output
    assert log.read_bytes() == original  # not compressed/replaced
    assert not (outside / "events.jsonl.gz").exists()

    del_result = prune(target_repo, "--keep", "0")
    assert del_result.exit_code == 0, del_result.output
    assert outside.is_dir() and log.exists()  # outside dir never removed


# --- P2: a stale worktree registration (dir already gone) must not skip forever ---


def test_prune_continues_with_stale_worktree_registration(target_repo):
    """A registered worktree whose directory is already missing is a stale
    registration, not a dirty worktree — prune clears it and completes, leaving no
    orphan and keeping the lane branch (safe continuation of a partial prune)."""
    from adw.worktrees import create_lane_worktree

    rid = "aaaabbbb"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    add_ref(target_repo, rid, 1)
    wt = create_lane_worktree(target_repo, rid, "backend", "staging")
    shutil.rmtree(wt)  # the worktree dir is gone; its registration remains

    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output
    assert not run_dir(target_repo, rid).is_dir()
    assert prune_dry(target_repo).strip() == ""  # no orphaned registration
    assert refs_of(target_repo, rid) == []
    assert branch_exists(target_repo, lane_branch(rid, "backend"))


# --- P1: a symlinked .adw/runs must never make pruning reach outside the repo -----


def test_prune_rejects_runs_root_symlinked_outside_repo(target_repo, tmp_path):
    """If ``.adw/runs`` itself is a symlink to an external directory, pruning must
    refuse (exit 1) instead of treating the external children as runs — a parent
    symlink must not recreate the out-of-scope data-loss path."""
    external = tmp_path / "external"
    ext_run = external / "aaaa1111"
    ext_run.mkdir(parents=True)
    log = ext_run / "events.jsonl"
    log.write_text('{"seq": 1, "type": "run", "kind": "start", "payload": {}}\n')
    original = log.read_bytes()
    adw = target_repo / ".adw"
    adw.mkdir(exist_ok=True)
    os.symlink(external, adw / "runs")

    gz = prune(target_repo, "--keep", "0", "--gzip")
    assert gz.exit_code == 1, gz.output
    assert log.read_bytes() == original and not (ext_run / "events.jsonl.gz").exists()

    deletion = prune(target_repo, "--keep", "0")
    assert deletion.exit_code == 1, deletion.output
    assert ext_run.is_dir() and log.exists()  # external files untouched


# --- P1: a worktree dirtied AFTER the inventory check is never force-discarded -----


def test_prune_never_force_removes_worktree_dirtied_after_inventory(target_repo, monkeypatch):
    """A change arriving between the cleanliness inventory and removal (a race) must
    not be silently discarded: git's own removal-time check refuses, and prune turns
    that into a safety skip that preserves the worktree and the whole run."""
    import adw.retention as retention_mod
    from adw.worktrees import create_lane_worktree

    rid = "aaaaeeee"
    make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    add_ref(target_repo, rid, 1)
    wt = create_lane_worktree(target_repo, rid, "backend", "staging")
    (wt / "late_change.txt").write_text("uncommitted work\n")
    # The inventory sees clean; the worktree is really dirty at removal time.
    monkeypatch.setattr(retention_mod, "_worktree_dirty", lambda p: False)

    result = prune(target_repo, "--keep", "0")
    assert result.exit_code == 0, result.output  # a safety skip, not a crash
    assert rid in result.output
    assert (wt / "late_change.txt").exists()  # never force-discarded
    assert run_dir(target_repo, rid).is_dir()
    assert refs_of(target_repo, rid) != []
    assert rid in worktree_list(target_repo)


# --- P2: a partial multi-candidate failure reports the achieved state -------------


def test_prune_reports_completed_and_failed_on_partial_failure(target_repo):
    """When a later candidate fails after an earlier one was already processed, the
    CLI reports BOTH the completed candidate and the failing run's state, and exits
    1 — earlier mutations remain applied and are named (C7)."""
    ok = "aaaa0001"
    bad = "aaaa0002"
    make_run(target_repo, ok, _iso(datetime(2026, 8, 1)))  # older → processed first
    rd_bad = make_run(target_repo, bad, _iso(datetime(2026, 8, 2)))
    gzdir = rd_bad / "events.jsonl.gz"  # a non-empty dir makes os.replace fail
    gzdir.mkdir()
    (gzdir / "x").write_text("x\n")

    result = prune(target_repo, "--keep", "0", "--gzip")
    assert result.exit_code == 1, result.output
    assert ok in result.output  # the completed candidate is reported
    assert bad in result.output  # the failing run's achieved state is reported
    assert (run_dir(target_repo, ok) / "events.jsonl.gz").is_file()  # really compressed
    assert (rd_bad / "events.jsonl").is_file()  # authoritative log kept


# --- P1: a planted temp-file symlink must not let --gzip write outside the repo ---


def test_gzip_temp_file_symlink_cannot_touch_external_target(target_repo, tmp_path):
    """The compression temp file is created with an unpredictable, O_EXCL/O_NOFOLLOW
    name — a symlink planted at the OLD predictable temp path can no longer redirect
    the gzip write onto an external file (which the old code would truncate)."""
    rid = "aaaaffff"
    rd = make_run(target_repo, rid, _iso(datetime(2026, 8, 1)))
    original_plain = (rd / "events.jsonl").read_bytes()
    external = tmp_path / "victim.txt"
    external.write_bytes(b"precious external data\n")
    victim_original = external.read_bytes()
    # The prune runs in-process, so os.getpid() matches the formerly predictable name.
    planted = rd / f".events.jsonl.{os.getpid()}.gz.tmp"
    os.symlink(external, planted)

    result = prune(target_repo, "--keep", "0", "--gzip")
    assert result.exit_code == 0, result.output
    assert external.read_bytes() == victim_original  # never opened for writing
    gz = rd / "events.jsonl.gz"
    assert gz.is_file() and gzip.decompress(gz.read_bytes()) == original_plain
