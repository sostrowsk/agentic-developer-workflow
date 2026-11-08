import json

import pytest

from adw.state import LaneState, RunState, StateNotFoundError


def make_state(**overrides) -> RunState:
    defaults = dict(
        run_id="ab12cd34",
        issue="ISSUE-1: Demo",
        phase="build",
        parallel=False,
        lanes={
            "backend": LaneState(
                worktree=".adw/runs/ab12cd34/trees/backend",
                branch="adw/ab12cd34/backend",
                session_id="sess-1",
                ports={"backend": 9101},
                gate_iterations=2,
                fix_cycles=1,
            )
        },
    )
    defaults.update(overrides)
    return RunState(**defaults)


def test_save_load_round_trip_is_identical(target_repo):
    state = make_state()
    state.save(target_repo)
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded == state


def test_prior_review_context_survives_the_round_trip(target_repo):
    """Review-Policy v2: der Findings-Verlauf beider Loops überlebt einen Crash."""
    line = "- [P2] a.py: kaputt — disposition: fix dispatched (round 1)"
    state = make_state(review_prior_context=[line], authoring_prior_context=[line])
    state.save(target_repo)
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded.review_prior_context == [line]
    assert loaded.authoring_prior_context == [line]


def test_state_file_without_prior_context_fields_still_loads(target_repo):
    """Rückwärtskompatibel: State-Files aus der Zeit vor der Review-Policy v2."""
    data = json.loads(make_state().model_dump_json())
    del data["review_prior_context"]
    del data["authoring_prior_context"]
    path = target_repo / ".adw" / "runs" / "ab12cd34" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded.review_prior_context == []
    assert loaded.authoring_prior_context == []


def test_state_file_without_pushed_shas_still_loads(target_repo):
    """Rückwärtskompatibel: State-Files aus der Zeit vor der Push-Lease (C2)."""
    data = json.loads(make_state(pushed_shas={"adw/ab12cd34/backend": "abc"}).model_dump_json())
    del data["pushed_shas"]
    path = target_repo / ".adw" / "runs" / "ab12cd34" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert RunState.load(target_repo, "ab12cd34").pushed_shas == {}


def test_red_confirmed_survives_the_round_trip(target_repo):
    """RED-Gate: der orchestrator-eigene RED-Beweis überlebt einen Crash."""
    state = make_state()
    state.lanes["backend"].red_confirmed = True
    state.save(target_repo)
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded.lanes["backend"].red_confirmed is True


def test_red_test_paths_survive_the_round_trip(target_repo):
    """Die RED-Tests binden den Beweis — ihre Pfade müssen den Crash überleben."""
    state = make_state()
    state.lanes["backend"].red_test_paths = ["tests/test_feature.py"]
    state.save(target_repo)
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded.lanes["backend"].red_test_paths == ["tests/test_feature.py"]


def test_state_file_without_red_confirmed_still_loads(target_repo):
    """Rückwärtskompatibel: State-Files aus der Zeit vor dem RED-Gate."""
    data = json.loads(make_state().model_dump_json())
    del data["lanes"]["backend"]["red_confirmed"]
    del data["lanes"]["backend"]["red_test_paths"]
    path = target_repo / ".adw" / "runs" / "ab12cd34" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded.lanes["backend"].red_confirmed is False


def test_load_unknown_run_id_raises(target_repo):
    with pytest.raises(StateNotFoundError, match="deadbeef"):
        RunState.load(target_repo, "deadbeef")


def test_save_creates_run_directory(target_repo):
    make_state().save(target_repo)
    assert (target_repo / ".adw" / "runs" / "ab12cd34" / "state.json").is_file()


def test_find_latest_returns_most_recently_saved(target_repo):
    make_state(run_id="aaaa1111").save(target_repo)
    make_state(run_id="bbbb2222").save(target_repo)
    make_state(run_id="aaaa1111", phase="ci").save(target_repo)  # erneut gespeichert
    assert RunState.find_latest(target_repo).run_id == "aaaa1111"


def test_find_latest_without_runs_raises(target_repo):
    with pytest.raises(StateNotFoundError):
        RunState.find_latest(target_repo)


def test_new_run_generates_unique_ids(target_repo):
    a = RunState.new(issue="x", parallel=False)
    b = RunState.new(issue="x", parallel=False)
    assert a.run_id != b.run_id
    assert len(a.run_id) == 8
    assert a.phase == "spec"


def test_run_id_with_path_traversal_is_rejected():
    with pytest.raises(Exception, match="run_id|pattern"):
        make_state(run_id="../../evil")


def test_load_with_malformed_run_id_raises_state_not_found(target_repo):
    with pytest.raises(StateNotFoundError):
        RunState.load(target_repo, "../../evil")


def test_state_file_with_mismatched_embedded_run_id_is_rejected(target_repo):
    make_state(run_id="aaaa1111").save(target_repo)
    src = target_repo / ".adw" / "runs" / "aaaa1111"
    dst = target_repo / ".adw" / "runs" / "bbbb2222"
    dst.mkdir(parents=True)
    (dst / "state.json").write_text((src / "state.json").read_text())
    with pytest.raises(StateNotFoundError, match="bbbb2222"):
        RunState.load(target_repo, "bbbb2222")


def test_concurrent_saves_allocate_unique_seqs(target_repo):
    """Concurrent saves (parallel Lanes) must not duplicate sequence numbers."""
    from concurrent.futures import ThreadPoolExecutor

    states = [make_state(run_id=f"{i:08x}") for i in range(32)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda s: s.save(target_repo), states))
    seqs = [RunState.load(target_repo, s.run_id).seq for s in states]
    assert len(set(seqs)) == len(states)


def test_seq_recovers_from_corrupted_seq_file(target_repo):
    """Regression: an empty/broken .seq must not reset the sequence to 0."""
    make_state(run_id="aaaa1111").save(target_repo)
    make_state(run_id="bbbb2222").save(target_repo)
    (target_repo / ".adw" / "runs" / ".seq").write_text("")
    make_state(run_id="aaaa1111", phase="ci").save(target_repo)
    assert RunState.find_latest(target_repo).run_id == "aaaa1111"


def test_seq_reconciles_with_persisted_states_when_stale(target_repo):
    """Regression: a stale lower .seq must not distort find_latest."""
    make_state(run_id="aaaa1111").save(target_repo)
    make_state(run_id="bbbb2222").save(target_repo)
    (target_repo / ".adw" / "runs" / ".seq").write_text("1")
    make_state(run_id="aaaa1111", phase="ci").save(target_repo)
    assert RunState.find_latest(target_repo).run_id == "aaaa1111"


def test_seq_with_unicode_digit_does_not_crash(target_repo):
    (target_repo / ".adw" / "runs").mkdir(parents=True, exist_ok=True)
    (target_repo / ".adw" / "runs" / ".seq").write_text("²")
    make_state().save(target_repo)
    assert RunState.load(target_repo, "ab12cd34").seq >= 1


def test_corrupt_state_file_raises(target_repo):
    state = make_state()
    state.save(target_repo)
    path = target_repo / ".adw" / "runs" / "ab12cd34" / "state.json"
    path.write_text("{kaputt")
    with pytest.raises(StateNotFoundError, match="kaputt|ungültig|invalid"):
        RunState.load(target_repo, "ab12cd34")


def test_save_is_atomic_no_partial_file_on_existing_state(target_repo):
    state = make_state()
    state.save(target_repo)
    state.phase = "ci"
    state.save(target_repo)
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded.phase == "ci"
    run_dir = target_repo / ".adw" / "runs" / "ab12cd34"
    assert [p.name for p in run_dir.glob("*.json")] == ["state.json"]


def test_state_file_without_test_pass_done_loads_as_false(target_repo):
    """Rückwärtskompatibel (C6): alte States ohne Flag laden mit False, keine Migration."""
    data = json.loads(make_state().model_dump_json())
    data["lanes"]["backend"].pop("test_pass_done", None)
    path = target_repo / ".adw" / "runs" / "ab12cd34" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = RunState.load(target_repo, "ab12cd34")
    assert loaded.lanes["backend"].test_pass_done is False


def test_write_snapshot_fsyncs_the_file_before_replace_and_then_the_run_dir(
    target_repo, monkeypatch
):
    """C10: state.json übersteht Stromausfall — Datei-fsync vor os.replace,
    danach fsync des Run-Verzeichnisses, dessen Deskriptor wieder geschlossen wird."""
    import os
    import stat

    calls = []
    real_fsync, real_replace, real_close = os.fsync, os.replace, os.close

    def is_dir_fd(fd):
        return stat.S_ISDIR(os.fstat(fd).st_mode)

    def spy_fsync(fd):
        calls.append(("fsync_dir" if is_dir_fd(fd) else "fsync_file", fd))
        return real_fsync(fd)

    def spy_replace(src, dst):
        calls.append(("replace", str(dst)))
        return real_replace(src, dst)

    def spy_close(fd):
        if is_dir_fd(fd):
            calls.append(("close_dir", fd))
        return real_close(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr(os, "close", spy_close)
    make_state().save(target_repo)
    monkeypatch.undo()

    kinds = [kind for kind, _ in calls]
    assert "fsync_file" in kinds and "fsync_dir" in kinds, calls
    replace_at = kinds.index("replace")
    assert calls[replace_at][1].endswith("state.json")
    assert kinds.index("fsync_file") < replace_at  # Datei dauerhaft VOR dem Umbenennen
    dir_fsync_at = kinds.index("fsync_dir")
    assert dir_fsync_at > replace_at  # Verzeichniseintrag dauerhaft NACH dem Umbenennen
    dir_fd = calls[dir_fsync_at][1]
    assert ("close_dir", dir_fd) in calls[dir_fsync_at:]  # Deskriptor geschlossen
    assert RunState.load(target_repo, "ab12cd34").issue == "ISSUE-1: Demo"
