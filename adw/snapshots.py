"""Per-step worktree snapshots as git refs — the step-diff basis (GUI-SPEC §5).

At each step boundary of a build lane the worktree state is pinned so the GUI
can later answer "what did this step change?" via ``git diff <ref_before>
<ref_after>`` — the diff is computed lazily from the refs, never written into the
event log.

``capture(ctx, worktree, label)``:

1. builds a tree object over a TEMPORARY git index (``read-tree HEAD`` →
   ``add -A`` → ``write-tree``) — exactly like ``phases.py:_worktree_tree_hash``,
   the real index stays untouched;
2. ``git commit-tree <tree> -p <base_sha> -m "adw snapshot <label>"`` and
   ``git update-ref refs/adw/<run_id>/<seq> <commit> ""`` — created only if the
   ref does not exist yet (parallel lanes may pick the same seq; the loser retries
   with the next one), never overwritten; the ref keeps the objects alive against
   ``git gc``;
3. emits one ``snapshot`` point event (payload ``lane``/``tree``/``ref``/``label``).

Fail-open like the emitter: any failure before/at setting the ref leaves no ref
and no event, warns once, and never raises, aborts a run or replaces a business
exception. A failing event append AFTER the ref is set stays fail-open in the
emitter — the ref remains a usable snapshot, no rollback, no recovery mechanic.
"""

import logging
import os
import threading
from pathlib import Path

from adw.env import safe_env
from adw.git import run_git

logger = logging.getLogger(__name__)

# git command timeout (seconds) — write-tree/commit-tree/update-ref are cheap.
_GIT_TIMEOUT = 120
# Attempts to create a fresh ref when concurrent captures keep taking the seq.
_REF_ATTEMPTS = 10


def capture(ctx, worktree, label: str) -> None:
    """Pin the ``worktree`` state as a git ref and emit a ``snapshot`` event.

    Never raises: every failure path is caught, warned once and dropped so a
    snapshot can never abort a run or change its behaviour (GUI-SPEC §5)."""
    try:
        _capture(ctx, worktree, label)
    except Exception as exc:  # fail-open: never reaches the caller
        logger.warning("adw snapshot (%s) failed: %s", label, exc)


def _capture(ctx, worktree, label: str) -> None:
    worktree = Path(worktree)
    lane = worktree.name
    run_id = ctx.state.run_id
    lane_state = ctx.state.lanes.get(lane)
    base_sha = lane_state.base_sha if lane_state is not None else None
    if not base_sha:
        raise RuntimeError(f"lane {lane!r} has no pinned base_sha for the snapshot parent")
    env = safe_env(ctx.git_env)
    tree = _write_tree(ctx, worktree, env)
    commit = _run_git(
        worktree, env, "commit-tree", tree, "-p", base_sha, "-m", f"adw snapshot {label}"
    ).strip()
    ref = _create_ref(worktree, env, run_id, commit)
    # Only AFTER the ref is set (contract: no ref → no event). The emit itself is
    # fail-open — a failing append leaves the ref standing, no rollback.
    ctx.emitter.emit(
        "snapshot",
        {"lane": lane, "tree": tree, "ref": ref, "label": label},
        span=ctx.emitter.current_span_id(),
    )


def _write_tree(ctx, worktree: Path, env: dict[str, str]) -> str:
    """Tree object of the full worktree (incl. untracked, without ignored files)
    over a TEMPORARY index — the real index is never touched."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    tmp_index = ctx.run_dir / f".snapshot-index-{os.getpid()}-{threading.get_ident()}"
    env = dict(env)
    env["GIT_INDEX_FILE"] = str(tmp_index)
    try:
        # Seed from HEAD so tracked files that also match an ignore pattern are
        # not dropped by ``add -A``.
        _run_git(worktree, env, "read-tree", "HEAD")
        _run_git(worktree, env, "add", "-A")
        return _run_git(worktree, env, "write-tree").strip()
    finally:
        tmp_index.unlink(missing_ok=True)


def _create_ref(worktree: Path, env: dict[str, str], run_id: str, commit: str) -> str:
    """Create ``refs/adw/<run_id>/<seq>`` for ``commit`` with a fresh sequence
    derived from the existing refs of THIS run (the refs themselves are the source
    of truth, no sidecar state). The empty old value makes ``update-ref`` create
    only — a ref already set (a parallel lane, an earlier process) is never
    overwritten; a lost race retries with the next seq."""
    for _attempt in range(_REF_ATTEMPTS):
        ref = f"refs/adw/{run_id}/{_next_ref_seq(worktree, env, run_id)}"
        result = run_git(worktree, "update-ref", ref, commit, "", timeout=_GIT_TIMEOUT, env=env)
        if result.returncode == 0:
            return ref
        exists = run_git(
            worktree, "show-ref", "--verify", "--quiet", ref, timeout=_GIT_TIMEOUT, env=env
        )
        if exists.returncode != 0:
            raise RuntimeError(f"git update-ref failed: {result.stderr.strip()}")
    raise RuntimeError(f"no free snapshot ref after {_REF_ATTEMPTS} attempts")


def _next_ref_seq(worktree: Path, env: dict[str, str], run_id: str) -> int:
    """Highest existing ``<seq>`` of this run's refs + 1 (1 when none exist)."""
    out = _run_git(
        worktree, env, "for-each-ref", "--format=%(refname)", f"refs/adw/{run_id}/"
    )
    max_seq = 0
    for line in out.splitlines():
        tail = line.strip().rsplit("/", 1)[-1]
        try:
            max_seq = max(max_seq, int(tail))
        except ValueError:
            continue
    return max_seq + 1


def _run_git(cwd: Path, env: dict[str, str], *args: str) -> str:
    result = run_git(cwd, *args, timeout=_GIT_TIMEOUT, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout
