"""RED tests for task C — snapshots as the step-diff basis (GUI-SPEC §5, ACs 5–11).

``adw/snapshots.py`` does not exist yet, so the unit tests import ``capture``
locally (an ImportError makes exactly those tests RED without breaking module
collection). The run-wide tests drive the CLI / build phase and assert on the
``snapshot`` events that ``phases.py`` will emit at the four §5 boundaries — RED
today because no snapshot points are wired.

Every assertion is over the externally observable surface pinned by the contract:
the ``refs/adw/<run_id>/<seq>`` refs and their commits, and the ``snapshot``
events with payload ``lane``/``tree``/``ref``/``label``. The mechanic (index
handling, git command order, how <seq> is derived) is deliberately never asserted.
"""

import logging
import re

import pytest

from adw.config import AdwConfig
from adw.events import EventEmitter
from adw.phases import RunContext, run_build_phase, run_spec_and_plan
from adw.state import LaneState, RunState
from adw.worktrees import create_lane_worktree, lane_branch
from tests.conftest import git, write_config
from tests.test_instrumentation import TDD_CONFIG, cli_run, of_type, read_events, read_run_events

# --- helpers ----------------------------------------------------------------


def make_ctx(target_repo) -> RunContext:
    """A minimal RunContext with a real emitter and a persisted run dir; the
    agents/codex are never called by ``capture``."""
    state = RunState.new(issue="snapshot", parallel=False)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=object(),
        codex=object(),
        emitter=EventEmitter(target_repo, state.run_id),
    )
    ctx.state.save(target_repo)
    return ctx


def make_lane(ctx: RunContext, lane: str = "backend", base_sha: str | None = None):
    """Create the lane worktree and pin its LaneState (with base_sha) the way the
    build phase does, so ``capture`` can derive lane and fork point from ctx."""
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, lane, ctx.config.base_branch)
    if base_sha is None:
        base_sha = git(worktree, "rev-parse", "HEAD")
    ctx.state.lanes[lane] = LaneState(
        worktree=str(worktree),
        branch=lane_branch(ctx.state.run_id, lane),
        base_sha=base_sha,
    )
    ctx.state.save(ctx.repo)
    return worktree, base_sha


def snapshot_events(ctx: RunContext) -> list[dict]:
    return of_type(read_events(ctx.repo, ctx.state.run_id), "snapshot")


# --- AC 5 / AC 6: tree + commit + ref, real index untouched -----------------


def test_capture_creates_ref_to_commit_with_the_tree_and_base_parent(target_repo):
    """AC 6: a successful snapshot leaves a ref refs/adw/<run_id>/<seq> pointing
    at a commit whose tree is the captured tree and whose parent is base_sha; the
    commit message is ``adw snapshot <label>``."""
    from adw.snapshots import capture

    ctx = make_ctx(target_repo)
    worktree, base_sha = make_lane(ctx)
    (worktree / "new.py").write_text("x = 1\n")  # untracked change to capture
    capture(ctx, worktree, "before_agent")

    (snap,) = snapshot_events(ctx)
    ref = snap["payload"]["ref"]
    tree = snap["payload"]["tree"]
    assert re.fullmatch(r"refs/adw/[^/]+/[^/]+", ref)
    assert ref.split("/")[2] == ctx.state.run_id
    commit = git(target_repo, "rev-parse", "--verify", ref)  # ref resolves to a commit
    assert git(target_repo, "cat-file", "-t", commit) == "commit"
    assert git(target_repo, "rev-parse", f"{ref}^{{tree}}") == tree
    assert git(target_repo, "rev-parse", f"{ref}^1") == base_sha  # parent = pinned fork point
    assert git(target_repo, "log", "-1", "--format=%s", ref) == "adw snapshot before_agent"


def test_capture_leaves_real_index_worktree_branch_and_head_unchanged(target_repo):
    """AC 5: the capture runs over a TEMPORARY index — the real index, the
    worktree files, the current branch and HEAD stay byte-for-byte unchanged."""
    from adw.snapshots import capture

    ctx = make_ctx(target_repo)
    worktree, _base = make_lane(ctx)
    (worktree / "untracked.py").write_text("u = 1\n")
    (worktree / "README.md").write_text("# changed in the worktree\n")  # modify a tracked file

    head_before = git(worktree, "rev-parse", "HEAD")
    branch_before = git(worktree, "symbolic-ref", "HEAD")
    status_before = git(worktree, "status", "--porcelain")

    capture(ctx, worktree, "after_agent")

    assert git(worktree, "rev-parse", "HEAD") == head_before
    assert git(worktree, "symbolic-ref", "HEAD") == branch_before
    # A real-index add would flip "??"/" M" to staged "A "/"M " here.
    assert git(worktree, "status", "--porcelain") == status_before
    assert (worktree / "README.md").read_text() == "# changed in the worktree\n"


def test_capture_tree_reflects_worktree_state_without_ignored_files(target_repo):
    """AC 5/6: the captured tree mirrors the worktree — modified content, new
    untracked files, no deleted file — but excludes .gitignore-ignored files."""
    from adw.snapshots import capture

    ctx = make_ctx(target_repo)
    # Seed tracked files into the base branch, then fork the lane from it.
    (target_repo / "keep.py").write_text("keep\n")
    (target_repo / "gone.py").write_text("gone\n")
    git(target_repo, "add", "-A")
    git(target_repo, "commit", "-m", "seed tracked files")
    worktree, _base = make_lane(ctx)

    (worktree / "README.md").write_text("# modified\n")  # tracked modification
    (worktree / "gone.py").unlink()  # tracked deletion
    (worktree / "untracked.py").write_text("u = 1\n")  # new untracked file
    (worktree / ".gitignore").write_text("*.log\n")
    (worktree / "debug.log").write_text("secret\n")  # ignored, must NOT be captured

    capture(ctx, worktree, "before_agent")
    (snap,) = snapshot_events(ctx)
    tree = snap["payload"]["tree"]
    listed = git(target_repo, "ls-tree", "-r", "--name-only", tree).splitlines()

    assert "keep.py" in listed
    assert "untracked.py" in listed
    assert ".gitignore" in listed
    assert "gone.py" not in listed  # deletion reflected
    assert "debug.log" not in listed  # ignored file excluded
    assert git(target_repo, "show", f"{tree}:README.md") == "# modified"


# --- AC 8: exactly one snapshot event, consistent payload -------------------


def test_capture_emits_exactly_one_snapshot_event_with_full_payload(target_repo):
    """AC 8: a successful capture emits exactly one snapshot event whose payload
    carries lane/tree/ref/label, with tree/ref matching the created git objects."""
    from adw.snapshots import capture

    ctx = make_ctx(target_repo)
    worktree, _base = make_lane(ctx, "backend")
    (worktree / "f.py").write_text("f = 1\n")
    capture(ctx, worktree, "after_gates")

    snaps = snapshot_events(ctx)
    assert len(snaps) == 1
    payload = snaps[0]["payload"]
    assert set(payload) == {"lane", "tree", "ref", "label"}
    assert payload["lane"] == "backend"
    assert payload["label"] == "after_gates"
    assert git(target_repo, "rev-parse", f"{payload['ref']}^{{tree}}") == payload["tree"]


# --- AC 7: resume in a new process — fresh seq, never overwrite --------------


def test_resume_assigns_a_fresh_seq_and_never_overwrites_an_existing_ref(target_repo):
    """AC 7: a resume of the same run in a new process (fresh ctx + emitter)
    derives the next <seq> from the existing refs — a fresh, distinct ref — and
    never overwrites a ref already set for that run."""
    from adw.snapshots import capture

    ctx = make_ctx(target_repo)
    worktree, _base = make_lane(ctx)
    (worktree / "a.py").write_text("a = 1\n")
    capture(ctx, worktree, "before_agent")
    (snap1,) = snapshot_events(ctx)
    ref1 = snap1["payload"]["ref"]
    commit1 = git(target_repo, "rev-parse", "--verify", ref1)

    # A resume in a new process: fresh state/ctx/emitter for the SAME run.
    resumed = RunContext(
        repo=target_repo,
        config=ctx.config,
        state=RunState.load(target_repo, ctx.state.run_id),
        agents=object(),
        codex=object(),
        emitter=EventEmitter(target_repo, ctx.state.run_id),
    )
    (worktree / "b.py").write_text("b = 1\n")
    capture(resumed, worktree, "after_agent")

    refs = [s["payload"]["ref"] for s in snapshot_events(ctx)]
    assert len(refs) == 2
    assert len(set(refs)) == 2  # a fresh, distinct <seq>
    assert git(target_repo, "rev-parse", "--verify", ref1) == commit1  # ref1 untouched


# --- AC 11: fail-open ------------------------------------------------------


def test_failed_snapshot_is_fail_open_no_ref_no_event_one_warning(target_repo, caplog):
    """AC 11: a git command failing before/at the ref (here: an invalid base_sha
    makes commit-tree fail) leaves no ref and no snapshot event, warns, and
    ``capture`` neither raises nor changes behaviour."""
    from adw.snapshots import capture

    ctx = make_ctx(target_repo)
    worktree, _base = make_lane(ctx, base_sha="0" * 40)  # invalid parent → commit-tree fails
    (worktree / "x.py").write_text("x = 1\n")

    with caplog.at_level(logging.WARNING):
        capture(ctx, worktree, "before_agent")  # fail-open: must not raise

    assert snapshot_events(ctx) == []  # no snapshot event
    refs = git(target_repo, "for-each-ref", "--format=%(refname)", f"refs/adw/{ctx.state.run_id}")
    assert refs == ""  # no ref set
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]  # warned


# --- AC 9: the four snapshot points across a run ----------------------------


def test_dry_run_snapshots_cover_the_build_boundaries_and_skip_authoring(target_repo):
    """AC 9: a single-lane dry run (2 gate iterations: red then green) snapshots
    before/after each build agent run and after each gate iteration — after_gates
    regardless of the gate outcome. No snapshots for authoring/review agent runs:
    the build phase has exactly two agent runs, so before_agent/after_agent count
    exactly two, which would be higher if authoring runs leaked snapshots."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    snaps = of_type(records, "snapshot")
    assert snaps
    labels = [s["payload"]["label"] for s in snaps]

    assert labels.count("before_agent") == 2  # the two build agent runs only
    assert labels.count("after_agent") == 2
    assert labels.count("after_gates") == 2  # failed iteration + passed iteration
    assert "red" not in labels  # no tdd gate in the default config
    for s in snaps:
        assert set(s["payload"]) == {"lane", "tree", "ref", "label"}
        assert s["payload"]["lane"] == "backend"
        assert re.fullmatch(r"refs/adw/[^/]+/[^/]+", s["payload"]["ref"])


def test_tdd_dry_run_emits_a_red_snapshot_after_the_test_only_pass(target_repo):
    """AC 9: a lane with a tdd gate walks the RED path — before/after the
    test-only agent run, a ``red`` snapshot after it, then before/after and
    after_gates for the single implementation iteration."""
    write_config(target_repo, TDD_CONFIG)
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "tdd gate")
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    snaps = of_type(records, "snapshot")
    labels = [s["payload"]["label"] for s in snaps]

    assert labels.count("red") == 1
    assert labels.count("before_agent") == 2  # test-only pass + implementation run
    assert labels.count("after_agent") == 2
    assert labels.count("after_gates") == 1  # one green implementation iteration
    # the red snapshot follows the test-only pass (at least one before_agent before it).
    red_seq = next(s["seq"] for s in snaps if s["payload"]["label"] == "red")
    before_seqs = [s["seq"] for s in snaps if s["payload"]["label"] == "before_agent"]
    assert min(before_seqs) < red_seq


# --- AC 10: agent exception keeps before_agent, skips after_agent ------------


class _RaisingAgentRunner:
    def __init__(self, boom: BaseException):
        self._boom = boom

    def run(self, *args, **kwargs):
        raise self._boom


def _build_ready_ctx(target_repo) -> RunContext:
    """Drive spec+plan with mocks to reach the build phase, ready for _run_lane."""
    from adw.mock import MockAgentRunner, MockCodexRunner
    from tests.test_e2e_dry_run import OK, script_authoring, script_draft_artifacts

    agents = MockAgentRunner()
    script_authoring(agents)
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
    codex.script(OK, OK)
    state = RunState.new(issue="exc", parallel=False)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=agents,
        codex=codex,
        skip_approval=True,
        emitter=EventEmitter(target_repo, state.run_id),
    )
    run_spec_and_plan(ctx)
    assert ctx.state.phase == "build"
    return ctx


def test_agent_exception_keeps_before_agent_snapshot_and_skips_after_agent(target_repo):
    """AC 10: when a build agent run raises, the after_agent snapshot and its
    event are skipped, the exception propagates unchanged, and the before_agent
    snapshot taken just before the run survives as the diff basis."""
    ctx = _build_ready_ctx(target_repo)
    boom = RuntimeError("agent kaputt")
    ctx.agents = _RaisingAgentRunner(boom)

    with pytest.raises(RuntimeError) as excinfo:
        run_build_phase(ctx)
    assert excinfo.value is boom  # propagates unchanged

    snaps = snapshot_events(ctx)
    labels = [s["payload"]["label"] for s in snaps]
    assert labels.count("before_agent") == 1
    assert "after_agent" not in labels
    ref = next(s["payload"]["ref"] for s in snaps if s["payload"]["label"] == "before_agent")
    assert git(target_repo, "rev-parse", "--verify", ref)  # the before_agent snapshot survived
