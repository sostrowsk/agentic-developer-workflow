import socket
import subprocess
import threading
import time

import adw.worktrees as worktrees
from adw.worktrees import create_lane_worktree, ports_for, remove_lane_worktree
from tests.conftest import git

RUN_ID = "ab12cd34"


def test_concurrent_lane_worktree_creation_is_serialized(target_repo, monkeypatch):
    """Regression (CI flake): run_build_phase fans out create_lane_worktree across
    lanes via a ThreadPoolExecutor. Concurrent ``git worktree add`` on the same
    repo races on ``.git/worktrees`` metadata (e.g. 'failed to read
    .git/worktrees/frontend/commondir'), so the worktree mutations must be
    serialized. Two lanes created from two threads must not run their
    ``git worktree add`` intervals overlapping, and both must succeed."""
    intervals = []
    intervals_lock = threading.Lock()
    real_effect = worktrees._git_effect

    def timed_effect(repo, *args):
        is_add = len(args) >= 2 and args[0] == "worktree" and args[1] == "add"
        t_in = time.monotonic()
        if is_add:
            time.sleep(0.2)  # widen the window so an UNserialized add overlaps
        result = real_effect(repo, *args)
        if is_add:
            with intervals_lock:
                intervals.append((t_in, time.monotonic()))
        return result

    monkeypatch.setattr(worktrees, "_git_effect", timed_effect)

    errors = []
    start = threading.Barrier(2)

    def make(lane):
        try:
            start.wait()
            create_lane_worktree(target_repo, RUN_ID, lane, "staging")
        except Exception as exc:  # noqa: BLE001 — surface any worktree error to the assert
            errors.append((lane, exc))

    threads = [threading.Thread(target=make, args=(lane,)) for lane in ("backend", "frontend")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(intervals) == 2, intervals
    (first_in, first_out), (second_in, second_out) = sorted(intervals)
    # Serialized: the second `git worktree add` starts only after the first ends.
    assert second_in >= first_out, f"worktree add intervals overlapped: {intervals}"
    for lane in ("backend", "frontend"):
        assert worktrees.lane_worktree_path(target_repo, RUN_ID, lane).is_dir()


def test_worktree_is_created_on_lane_branch_from_base(target_repo):
    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert path.is_dir()
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == f"adw/{RUN_ID}/backend"
    base_head = git(target_repo, "rev-parse", "staging")
    lane_head = git(path, "rev-parse", "HEAD")
    assert lane_head == base_head


def test_create_is_idempotent(target_repo):
    first = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    second = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert first == second
    worktrees = git(target_repo, "worktree", "list")
    assert worktrees.count(f"adw/{RUN_ID}/backend") == 1


def test_two_lanes_get_separate_worktrees(target_repo):
    be = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    fe = create_lane_worktree(target_repo, RUN_ID, "frontend", "staging")
    assert be != fe
    assert be.is_dir() and fe.is_dir()


def test_remove_lane_worktree(target_repo):
    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    remove_lane_worktree(target_repo, RUN_ID, "backend")
    assert not path.exists()
    assert f"adw/{RUN_ID}/backend" not in git(target_repo, "worktree", "list")


def test_relative_repo_path_creates_worktree_in_right_place(target_repo, monkeypatch):
    from pathlib import Path

    monkeypatch.chdir(target_repo.parent)
    path = create_lane_worktree(Path(target_repo.name), RUN_ID, "backend", "staging")
    assert path.is_absolute()
    assert (target_repo / ".adw" / "runs" / RUN_ID / "trees" / "backend").is_dir()
    assert not (target_repo / target_repo.name).exists()


def test_non_ascii_repo_path_is_idempotent(tmp_path):
    from tests.conftest import write_config

    repo = tmp_path / "tärget-ünïcode"
    repo.mkdir()
    git(repo, "init", "-b", "staging")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    write_config(repo)
    (repo / "README.md").write_text("x")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")

    first = create_lane_worktree(repo, RUN_ID, "backend", "staging")
    second = create_lane_worktree(repo, RUN_ID, "backend", "staging")
    assert first == second


def test_repo_hooks_never_run_during_worktree_ops(target_repo, tmp_path):
    """Repo hooks do NOT run at all during orchestrator git ops (core.hooksPath=/dev/null)."""
    marker = tmp_path / "hook-lief.txt"
    hook = target_repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    hook.chmod(0o755)
    create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    remove_lane_worktree(target_repo, RUN_ID, "backend")
    assert not marker.exists()


def test_locked_branch_deletion_raises_instead_of_silent_success(target_repo):
    import pytest

    from adw.worktrees import WorktreeError

    create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    lock = target_repo / ".git" / "refs" / "heads" / "adw" / RUN_ID / "backend.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")
    try:
        with pytest.raises(WorktreeError):
            remove_lane_worktree(target_repo, RUN_ID, "backend")
    finally:
        lock.unlink()


def test_remove_twice_is_idempotent_even_with_localized_git(target_repo, monkeypatch):
    monkeypatch.setenv("LANGUAGE", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    remove_lane_worktree(target_repo, RUN_ID, "backend")
    remove_lane_worktree(target_repo, RUN_ID, "backend")  # darf nicht raisen


def test_registered_but_deleted_worktree_is_recreated(target_repo):
    import shutil

    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    shutil.rmtree(path)
    recreated = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert recreated == path
    assert recreated.is_dir()
    assert git(recreated, "rev-parse", "--abbrev-ref", "HEAD") == f"adw/{RUN_ID}/backend"


def test_worktree_on_wrong_branch_raises(target_repo):
    import pytest

    from adw.worktrees import WorktreeError

    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    git(path, "checkout", "-b", "fremder-branch")
    with pytest.raises(WorktreeError, match="fremder-branch"):
        create_lane_worktree(target_repo, RUN_ID, "backend", "staging")


def test_existing_gitignore_without_catchall_gets_amended(target_repo):
    runs_dir = target_repo / ".adw" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / ".gitignore").write_text("nur-etwas-anderes\n")
    create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert "*" in (runs_dir / ".gitignore").read_text().splitlines()
    status = git(target_repo, "status", "--porcelain")
    assert ".adw/runs/" not in status or ".gitignore" in status


def test_partially_created_worktree_is_not_accepted_on_retry(target_repo):
    """Regression: a registered Worktree WITHOUT a ready marker (aborted add)
    must be freshly recreated on retry."""
    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    (path / "rest-des-abbruchs.txt").write_text("halbfertig")
    marker = target_repo / ".adw" / "runs" / RUN_ID / "trees" / "backend.ready"
    marker.unlink()  # Partial-Signatur: registriert + Dir + KEIN Marker
    recreated = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert recreated.is_dir()
    assert not (recreated / "rest-des-abbruchs.txt").exists()  # frisch neu erstellt
    assert marker.exists()


def test_stale_ready_marker_does_not_bless_missing_worktree(target_repo):
    """Regression: an old .ready marker + missing dir must not count as done."""
    import shutil

    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    shutil.rmtree(path)  # Marker bleibt liegen
    recreated = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert recreated.is_dir()
    assert git(recreated, "rev-parse", "--abbrev-ref", "HEAD") == f"adw/{RUN_ID}/backend"


def test_stale_marker_on_unregistered_worktree_is_cleared_before_add(target_repo):
    """Regression: externally removed Worktree + old marker → recreate cleanly."""
    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    git(target_repo, "worktree", "remove", "--force", str(path))  # Marker bleibt
    recreated = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert recreated.is_dir()
    assert git(recreated, "rev-parse", "--abbrev-ref", "HEAD") == f"adw/{RUN_ID}/backend"


def test_locked_partial_worktree_is_recovered(target_repo):
    """Regression: a locked partial (aborted add) must be cleaned up on retry."""
    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    git(target_repo, "worktree", "lock", str(path))
    (target_repo / ".adw" / "runs" / RUN_ID / "trees" / "backend.ready").unlink()
    recreated = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert recreated.is_dir()
    assert git(recreated, "rev-parse", "--abbrev-ref", "HEAD") == f"adw/{RUN_ID}/backend"


def test_ports_are_deterministic():
    assert ports_for(RUN_ID, "backend") == ports_for(RUN_ID, "backend")


def test_ports_differ_between_lanes():
    assert ports_for(RUN_ID, "backend") != ports_for(RUN_ID, "frontend")


def test_ports_have_lane_specific_ranges():
    be = ports_for(RUN_ID, "backend")["backend"]
    fe = ports_for(RUN_ID, "frontend")["frontend"]
    assert 9100 <= be < 9200
    assert 9200 <= fe < 9300


def test_occupied_port_gets_evaded():
    wanted = ports_for(RUN_ID, "backend")["backend"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", wanted))
        sock.listen(1)
        evaded = ports_for(RUN_ID, "backend")["backend"]
    assert evaded != wanted
    assert 9100 <= evaded < 9200


def test_worktree_of_dirty_base_still_starts_from_committed_state(target_repo):
    (target_repo / "uncommitted.txt").write_text("nicht committet")
    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert not (path / "uncommitted.txt").exists()


def test_create_with_unknown_base_branch_raises(target_repo):
    import pytest

    from adw.worktrees import WorktreeError

    with pytest.raises(WorktreeError, match="gibtsnicht"):
        create_lane_worktree(target_repo, RUN_ID, "backend", "gibtsnicht")


def test_worktree_lives_under_run_dir_and_is_gitignored(target_repo):
    path = create_lane_worktree(target_repo, RUN_ID, "backend", "staging")
    assert (target_repo / ".adw" / "runs" / RUN_ID / "trees" / "backend") == path
    status = subprocess.run(
        ["git", "-C", str(target_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    assert ".adw/runs/" not in status
