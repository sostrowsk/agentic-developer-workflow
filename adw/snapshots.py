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
   ``git update-ref refs/adw/<run_id>/<seq> <commit>`` — the ref keeps the
   objects alive against ``git gc``;
3. emits one ``snapshot`` point event (payload ``lane``/``tree``/``ref``/``label``).

Fail-open like the emitter: any failure before/at setting the ref leaves no ref
and no event, warns once, and never raises, aborts a run or replaces a business
exception. A failing event append AFTER the ref is set stays fail-open in the
emitter — the ref remains a usable snapshot, no rollback, no recovery mechanic.
"""

import logging
import os
import subprocess
import threading
from pathlib import Path

from adw.env import safe_env

logger = logging.getLogger(__name__)

# git command timeout (seconds) — write-tree/commit-tree/update-ref are cheap.
_GIT_TIMEOUT = 120


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
    # A fresh sequence derived from the existing refs of THIS run — never
    # overwrites a ref already set, even after a resume in a new process
    # (the refs themselves are the source of truth, no sidecar state).
    seq = _next_ref_seq(worktree, env, run_id)
    ref = f"refs/adw/{run_id}/{seq}"
    _run_git(worktree, env, "update-ref", ref, commit)
    # Only AFTER the ref is set (contract: no ref → no event). The emit itself is
    # fail-open — a failing append leaves the ref standing, no rollback.
    ctx.emitter.emit(
        "snapshot",
        {"lane": lane, "tree": tree, "ref": ref, "label": label},
        span=_current_span_id(ctx.emitter),
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
    result = subprocess.run(
        ["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _current_span_id(emitter):
    """Innermost open span on the current thread, or None — so the snapshot point
    attributes to its enclosing lane/round span (GUI-SPEC §4.2). Read lazily to
    avoid an import cycle with ``phases``; mirrors ``phases._current_span_id``."""
    stack = getattr(emitter, "_stack", None)
    if stack is None:  # NoOpEmitter has no stack
        return None
    try:
        current = stack()
        return current[-1] if current else None
    except Exception:
        return None
