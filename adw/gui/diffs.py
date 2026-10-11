"""Read-only snapshot diffs and the change-scope projection (git diff --numstat)."""

import re
import subprocess
from pathlib import Path

import yaml
from fastapi import HTTPException

from adw.git import run_git
from adw.gui.artifacts import (
    _resolve_artifact,
)
from adw.gui.fmt import (
    _mapping_payload,
)
from adw.state import contained as _contained

# git-diff timeout (seconds); the diff endpoint reads only — mirrors snapshots.py.
_GIT_DIFF_TIMEOUT = 30


# --- read-only snapshot diff (Aufgabe B, AC-B1/B2/B4) ---------------------------


def _is_snapshot_ref(value, run_id) -> bool:
    """Whether ``value`` has the EXACT snapshot-ref structure of this run:
    ``refs/adw/<run_id>/<seq>`` with a numeric seq. This rejects malformed,
    range-like (``a..b``) and option-like (``--output=…``, ``-p``) values and
    foreign-run refs BEFORE git is ever invoked — so a crafted or corrupt snapshot
    event whose ``ref`` is not a plain snapshot ref can never reach git as an
    option or revision (AC-B2/B3, strictly read-only). Membership in the event-log
    allowlist is still required in addition to this structural check."""
    return (
        isinstance(value, str)
        and re.fullmatch(rf"refs/adw/{re.escape(run_id)}/[0-9]+", value) is not None
    )


def _snapshot_refs(events) -> set:
    """The exact set of ref names appearing in ``snapshot`` events of this run —
    the allowlist the diff endpoint validates ``from``/``to`` against (AC-B2). A
    pattern match alone is never sufficient; membership in this set is required."""
    refs = set()
    for e in events:
        if e.get("type") == "snapshot":
            ref = _mapping_payload(e).get("ref")
            if isinstance(ref, str) and ref:
                refs.add(ref)
    return refs


def _parse_numstat(text: str) -> list:
    """Parse ``git diff --numstat`` output into per-file counts, in git's order.
    A binary file's ``-`` count becomes JSON ``null`` (mirroring numstat)."""
    files = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_s, del_s = parts[0], parts[1]
        files.append({
            "path": "\t".join(parts[2:]),
            "additions": None if add_s == "-" else int(add_s),
            "deletions": None if del_s == "-" else int(del_s),
        })
    return files


def _git_diff_output(repo_path, *args: str) -> str:
    """One read-only ``git diff`` between two already-allowlisted refs, run like the
    orchestrator (AC-B4): list argv (no shell), hooks off, ``safe_env()``, a timeout
    — no worktree switch, no ref created/updated/deleted. A failing git (a dangling
    or removed ref, a concurrent gc) is NOT a valid diff and must never be presented
    as an (empty) success: a controlled non-5xx error is raised instead."""
    result = run_git(repo_path, "diff", *args, timeout=_GIT_DIFF_TIMEOUT)
    if result.returncode != 0:
        # Allowlisted and well-formed, but git could not produce the diff (e.g. the
        # snapshot object is gone). Report it as unavailable, never a false empty
        # diff. Non-5xx, and never a partial/one-sided result.
        raise HTTPException(status_code=404, detail="Snapshot diff unavailable")
    return result.stdout


def _git_numstat(repo_path, frm: str, to: str) -> list:
    """Per-file change counts only — the change-scope view needs no patch text."""
    return _parse_numstat(_git_diff_output(repo_path, "--numstat", frm, to))


def _git_diff(repo_path, frm: str, to: str) -> dict:
    """Files and full patch; both git invocations must succeed (see above)."""
    files = _git_numstat(repo_path, frm, to)
    return {"files": files, "patch": _git_diff_output(repo_path, frm, to)}


# --- change scope projection (Änderungsumfang eines Laufs) ----------------------
# An additive, purely DERIVED view: the files each observed lane actually changed
# (its first vs last snapshot, via the EXISTING snapshot/diff/numstat logic) placed
# beside the contract's declared `x-adw-*` scope as readable YAML text. It makes NO
# judgement (no in-/out-of-scope marker, E1) and introduces no new git path, route,
# event or state (E5/E6).

# The narrow set of expected operational diff errors a per-lane comparison may raise
# via `_git_numstat`: its own HTTPException(404) on a missing snapshot
# object / one-sided failure, plus the SubprocessError (incl. TimeoutExpired) and
# OSError that propagate from `subprocess.run`. A programming error is NOT swallowed.
_CHANGE_SCOPE_DIFF_ERRORS = (HTTPException, subprocess.SubprocessError, OSError)


def _observed_lanes(events, snaps: dict) -> list:
    """The observed lanes in first-observation order (AC-1/S1). A lane is observed
    via a `lane` span with a non-empty `payload.name`, OR via a structurally valid
    snapshot (already filtered into `snaps`). The order key is the smallest seq
    across both sources; each name yields exactly one entry."""
    first_seq: dict = {}

    def observe(name, seq):
        # A lane name must be a non-empty STRING (contract `lane: string`): a numeric
        # or unhashable `payload.name` is ignored, never a non-string lane and never
        # a mixed-type sort key.
        if not isinstance(name, str) or not name or not isinstance(seq, int):
            return
        prev = first_seq.get(name)
        if prev is None or seq < prev:
            first_seq[name] = seq

    for e in events:
        if e.get("type") == "lane" and e.get("kind") == "start":
            observe(_mapping_payload(e).get("name"), e.get("seq"))
    for lane, pairs in snaps.items():
        for seq, _ref in pairs:
            observe(lane, seq)
    return sorted(first_seq, key=lambda name: (first_seq[name], name))


def _lane_change(repo_path, lane: str, snaps: dict) -> dict:
    """One lane's change entry (AC-1/AC-6/AC-7/AC-8/S2/S3). With >= 2 valid
    snapshots the diff between its lowest- and highest-seq snapshot is produced via
    `_git_numstat` (a produced diff with no changes is `files: []`).
    Otherwise — 0/1 snapshot, or any expected diff error despite a pair — the
    canonical unavailable shape `diff_available: false` / `files: null` (never `[]`,
    never omitted, never a false empty diff)."""
    pairs = snaps.get(lane) or []
    if len(pairs) >= 2:
        frm, to = pairs[0][1], pairs[-1][1]
        try:
            files = _git_numstat(repo_path, frm, to)
        except _CHANGE_SCOPE_DIFF_ERRORS:
            return {"lane": lane, "diff_available": False, "files": None}
        return {"lane": lane, "diff_available": True, "files": files}
    return {"lane": lane, "diff_available": False, "files": None}


def _declared_scope(run_dir: Path) -> str | None:
    """The contract's declared scope as readable YAML text, or None (AC-3/AC-4/S4).
    Reads `contract.yaml` ONLY through the existing whitelist/containment path,
    selects every top-level entry whose key is a STRING with the `x-adw-` prefix in
    document order, and re-dumps them (semantic equivalence, not text fidelity). A
    missing/unreadable/non-mapping/unserializable contract, or the absence of a
    matching key, yields None — never an error, never a judgement."""
    path = _resolve_artifact(run_dir, "contract.yaml")
    if path is None:
        return None
    contained = _contained(path, run_dir)
    if contained is None or not contained.is_file():
        return None
    try:
        loaded = yaml.safe_load(contained.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(loaded, dict):
        return None
    # A legally loaded mapping may carry non-string keys (numeric, bool, null, …);
    # they are ignored, never subjected to a prefix operation.
    selected = {
        key: value for key, value in loaded.items()
        if isinstance(key, str) and key.startswith("x-adw-")
    }
    if not selected:
        return None
    try:
        return yaml.safe_dump(selected, sort_keys=False, allow_unicode=True)
    except yaml.YAMLError:
        return None


def _change_scope(events, snaps: dict, repo_path, run_dir: Path) -> dict:
    """The additive `change_scope` object (B4): the per-lane changed files beside
    the declared contract scope. Always present; `lanes` may be `[]` and
    `declared_scope` may be `null`."""
    lanes = [_lane_change(repo_path, lane, snaps) for lane in _observed_lanes(events, snaps)]
    return {"lanes": lanes, "declared_scope": _declared_scope(run_dir)}


def _change_scope_summary(change_scope: dict) -> dict:
    """The collapsed "Change scope" summary (A2): over ALL observed lanes the number
    of changed files and the sums of added / removed lines. Binary files (null
    counts) count towards the file number but contribute nothing to the line sums.

    ``available`` is True as soon as ANY lane produced a usable diff — an available
    diff with no changed files then reads as ``files: 0`` (distinct from the
    unavailable case where no lane has a diff at all). Derived here as a pure
    RENDER-context value from the already-built ``change_scope`` object; it is NOT
    written into ``detail`` and so never joins the JSON API (AC 12)."""
    lanes = change_scope.get("lanes") or []
    available = any(lane.get("diff_available") for lane in lanes)
    files = additions = deletions = 0
    for lane in lanes:
        for f in lane.get("files") or []:
            files += 1
            if f.get("additions") is not None:
                additions += f["additions"]
            if f.get("deletions") is not None:
                deletions += f["deletions"]
    return {"available": available, "files": files,
            "additions": additions, "deletions": deletions}
