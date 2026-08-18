"""Run retention: enumerate runs, prune old ones, compress event logs (§4.5).

``adw runs list`` shows per run when pruning is due; ``adw runs prune`` frees the
dominating storage (the git worktrees in the run directories) without discarding
uncommitted work or leaving orphaned worktree registrations, or — with ``--gzip``
— compresses the event log while keeping the run whole. The same enumeration,
canonical dating and candidate selection drive manual and automatic pruning.

Nothing here weakens the frozen event schema or ``adw/snapshots.py``; pruning only
removes/compresses whole run directories and their git artefacts.
"""

import gzip
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adw.env import safe_env
from adw.state import RUN_ID_RE, RUNS_RELPATH, RunState, StateNotFoundError
from adw.worktrees import WorktreeError, remove_registered_worktree

_GIT_TIMEOUT = 60
# The phases whose runs may be pruned — a run's state is its resume basis, so a
# non-terminal run is never touched (C2).
_TERMINAL_PHASES = ("done", "escalated")


@dataclass
class RunRecord:
    """One run's retention-relevant facts, all derived from the run directory."""

    run_id: str
    run_dir: Path
    phase: str | None
    date: datetime  # canonical UTC date (B4)
    event_count: int | None  # None = not determinable (legacy run, no log)
    log_size: int | None  # bytes of events.jsonl / events.jsonl.gz, or None
    log_format: str | None  # "jsonl" | "gz" | None


@dataclass
class PruneResult:
    """The outcome for one processed candidate (C7)."""

    run_id: str
    action: str  # "deleted" | "compressed" | "already_compressed" | "skipped"
    reason: str | None = None


class RetentionError(Exception):
    """A prune step could not be completed safely.

    Carries the per-candidate progress made BEFORE the failure so the CLI can report
    the achieved state (which runs were fully processed, and the partial state of the
    failing one) instead of discarding it (C7)."""

    def __init__(self, message: str, *, results=None, failed=None):
        super().__init__(message)
        self.results: list[PruneResult] = results or []
        self.failed: PruneResult | None = failed


# --- git helpers ---------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        env=safe_env(),
    )


# --- containment: never read/replace/delete anything outside the runs tree ------


def _runs_root(repo: Path) -> Path | None:
    """The resolved ``.adw/runs`` directory — but ONLY if it stays within the
    resolved repository (mirrors adw/gui/app.py::_runs_root). A ``.adw/runs`` that is
    itself a symlink to somewhere OUTSIDE the repo would otherwise become the
    containment root and expose arbitrary external directories to deletion/gzip.
    Returns None when it escapes or collapses onto the repo root."""
    repo_root = repo.resolve()
    runs_root = (repo_root / RUNS_RELPATH).resolve()
    if runs_root == repo_root or not runs_root.is_relative_to(repo_root):
        return None
    return runs_root


def _contained(path: Path, root: Path) -> Path | None:
    """The real path if it stays within ``root`` after resolving EVERY symlink, else
    None — the same discipline the GUI reader applies. Pruning must never follow a
    symlinked run directory or event log out of the runs tree and delete/replace a
    file elsewhere (scope + data-loss guard)."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(root) else None


def _is_valid_gzip(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as fh:
            while fh.read(1 << 16):
                pass
        return True
    except (OSError, EOFError):
        return False


# --- log reading (plain OR gzip) -----------------------------------------------


def _log_file(run_dir: Path) -> tuple[Path | None, str | None]:
    plain = run_dir / "events.jsonl"
    if plain.is_file():
        return plain, "jsonl"
    gz = run_dir / "events.jsonl.gz"
    if gz.is_file():
        return gz, "gz"
    return None, None


def _iter_events(run_dir: Path):
    path, fmt = _log_file(run_dir)
    if path is None:
        return
    opener = gzip.open if fmt == "gz" else open
    try:
        with opener(path, "rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except (OSError, EOFError):
        return


def _parse_ts(ts) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:  # naive timestamps are interpreted as UTC (B4)
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def canonical_date(run_dir: Path) -> datetime:
    """The deterministic UTC date of a run (B4): the start event's timestamp, else
    the persisted state's mtime, else the run directory's mtime — always
    determinable."""
    for record in _iter_events(run_dir):
        if record.get("type") == "run" and record.get("kind") == "start":
            dt = _parse_ts(record.get("ts"))
            if dt is not None:
                return dt
            break  # a start event without a usable ts → fall back
    state = run_dir / "state.json"
    try:
        if state.is_file():
            return datetime.fromtimestamp(state.stat().st_mtime, UTC)
        return datetime.fromtimestamp(run_dir.stat().st_mtime, UTC)
    except OSError:
        return datetime.fromtimestamp(0, UTC)


def _event_count(run_dir: Path) -> int | None:
    path, _fmt = _log_file(run_dir)
    if path is None:
        return None
    return sum(1 for _ in _iter_events(run_dir))


def _log_size(run_dir: Path) -> int | None:
    path, _fmt = _log_file(run_dir)
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _phase_of(repo: Path, run_id: str) -> str | None:
    try:
        return RunState.load(repo, run_id).phase
    except StateNotFoundError:
        return None


def scan_runs(repo: Path) -> list[RunRecord]:
    """Every run directory under the repo, sorted oldest → newest by canonical date
    with a lexicographic run-id tie-break (deterministic, B4)."""
    runs_dir = repo / RUNS_RELPATH
    runs_root = _runs_root(repo)
    records: list[RunRecord] = []
    if runs_root is None or not runs_dir.is_dir():
        # An escaping/invalid runs root contributes nothing — listing never reaches
        # outside the repo. Deleting/gzip pruning rejects it outright (see prune()).
        return records
    for child in sorted(runs_dir.iterdir()):
        if not RUN_ID_RE.fullmatch(child.name):
            continue
        # A symlinked or escaping run directory is never a candidate — pruning it
        # could reach a directory OUTSIDE the repository (P1).
        if child.is_symlink() or _contained(child, runs_root) is None or not child.is_dir():
            continue
        path, fmt = _log_file(child)
        records.append(
            RunRecord(
                run_id=child.name,
                run_dir=child,
                phase=_phase_of(repo, child.name),
                date=canonical_date(child),
                event_count=_event_count(child),
                log_size=_log_size(child),
                log_format=fmt,
            )
        )
    records.sort(key=lambda r: (r.date, r.run_id))
    return records


# --- candidate selection -------------------------------------------------------


def _candidates(records: list[RunRecord], keep: int) -> list[RunRecord]:
    """The pruning candidates (oldest first): all but the newest ``keep`` runs.
    ``keep == 0`` protects none."""
    if keep <= 0:
        return list(records)
    if keep >= len(records):
        return []
    return records[: len(records) - keep]


# --- worktree inventory / dirty check ------------------------------------------


def _run_worktrees(repo: Path, run_id: str) -> list[Path]:
    """Every registered git worktree whose path lies under the run's ``trees/``
    directory (one per lane)."""
    trees = (repo.resolve() / RUNS_RELPATH / run_id / "trees").resolve()
    result = _git(repo, "worktree", "list", "--porcelain", "-z")
    if result.returncode != 0:
        raise RetentionError(f"git worktree list: {result.stderr.strip()}")
    paths: list[Path] = []
    for token in result.stdout.split("\0"):
        if not token.startswith("worktree "):
            continue
        candidate = Path(token[len("worktree "):])
        try:
            if candidate.resolve().is_relative_to(trees):
                paths.append(candidate)
        except OSError:
            continue
    return paths


def _worktree_dirty(path: Path) -> bool:
    """Whether a worktree has uncommitted changes. A failing status is treated as
    dirty — cleanliness could not be established, so the run is never force-removed
    (C4)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "-c", "core.hooksPath=/dev/null", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=safe_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def _snapshot_refs(repo: Path, run_id: str) -> list[str]:
    result = _git(repo, "for-each-ref", "--format=%(refname)", f"refs/adw/{run_id}/")
    if result.returncode != 0:
        raise RetentionError(f"git for-each-ref: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# --- deleting prune ------------------------------------------------------------


def _delete_run(repo: Path, rec: RunRecord, runs_root: Path) -> PruneResult:
    """Delete one prune-able run in a fixed, resumable order (C3/C7): all worktree
    registrations first (via git, keeping the lane branch), then the snapshot refs,
    then the run directory. The directory is never removed while a worktree
    registration remains, so an interruption never orphans one."""
    if rec.run_dir.is_symlink() or _contained(rec.run_dir, runs_root) is None:
        raise RetentionError(f"{rec.run_id}: Laufordner liegt außerhalb der Runs — nicht gelöscht")
    worktrees = _run_worktrees(repo, rec.run_id)
    # C4: never force-remove a worktree with uncommitted work — skip the WHOLE run.
    # A registered path whose directory is MISSING is a stale registration, not a
    # dirty worktree: it is pruned below (never a permanent skip, P2).
    for wt in worktrees:
        if wt.exists() and _worktree_dirty(wt):
            return PruneResult(rec.run_id, "skipped", reason="uncommitted changes in worktree")
    # Remove WITHOUT --force: a worktree that turned dirty between the inventory
    # check and here (a race) is rejected by git's own removal-time check rather
    # than force-discarded. Such a refusal is a safety skip — no ref or run
    # directory is deleted (they are removed only AFTER every worktree is gone).
    for wt in worktrees:
        try:
            remove_registered_worktree(repo, wt)  # keeps the lane branch, no --force
        except WorktreeError as exc:
            return PruneResult(
                rec.run_id, "skipped", reason=f"worktree not safely removable: {exc}"
            )
    for ref in _snapshot_refs(repo, rec.run_id):
        res = _git(repo, "update-ref", "-d", ref)
        if res.returncode != 0 and _ref_exists(repo, ref):
            raise RetentionError(
                f"{rec.run_id}: Worktrees entfernt, Ref-Löschung fehlgeschlagen "
                f"({ref}): {res.stderr.strip()}",
                failed=PruneResult(
                    rec.run_id, "failed", reason=f"worktrees removed; ref {ref} remains"
                ),
            )
    try:
        shutil.rmtree(rec.run_dir, ignore_errors=False)
    except OSError as exc:
        raise RetentionError(
            f"{rec.run_id}: Worktrees und Refs entfernt, Laufordner-Löschung "
            f"fehlgeschlagen: {exc}",
            failed=PruneResult(
                rec.run_id, "failed", reason="worktrees and refs removed; run dir remains"
            ),
        ) from exc
    return PruneResult(rec.run_id, "deleted")


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "show-ref", "--verify", "--quiet", ref).returncode == 0


# --- gzip prune (the keeping form) ---------------------------------------------


def _compress_run(rec: RunRecord, runs_root: Path) -> PruneResult:
    """Compress ``events.jsonl`` → ``events.jsonl.gz`` atomically, keeping the run
    whole (C5/E10). 'Already compressed' iff a VALID .gz exists and the .jsonl is
    absent; if both exist the .jsonl is authoritative and is recompressed. A run
    with neither log is skipped (named), never falsely reported as compressed. A
    failure that could not be safely completed is a RetentionError (exit 1), and
    the authoritative plain log is left untouched."""
    if rec.run_dir.is_symlink() or _contained(rec.run_dir, runs_root) is None:
        raise RetentionError(
            f"{rec.run_id}: Laufordner liegt außerhalb der Runs — nicht komprimiert"
        )
    plain = rec.run_dir / "events.jsonl"
    gz = rec.run_dir / "events.jsonl.gz"
    if plain.exists():
        # The authoritative log must be a real regular file inside the runs tree —
        # never compress/delete through a symlink that escapes it (P1).
        if plain.is_symlink() or _contained(plain, runs_root) is None:
            raise RetentionError(f"{rec.run_id}: events.jsonl verweist aus den Runs heraus")
        data = plain.read_bytes()
        # Create the temp file with an UNPREDICTABLE name via O_CREAT|O_EXCL
        # (tempfile also adds O_NOFOLLOW where available) and gzip through the
        # secured fd — a pre-planted symlink at a predictable path can no longer
        # redirect the write onto an external file (P1). Same-directory placement
        # keeps the replacement atomic.
        fd, tmp_name = tempfile.mkstemp(dir=rec.run_dir, prefix=".events.", suffix=".gz.tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as fh:
                fh.write(data)
            with gzip.open(tmp, "rb") as fh:  # validate before it becomes the result
                if fh.read() != data:
                    raise RetentionError("komprimierte Ausgabe war nicht verlustfrei")
            os.replace(tmp, gz)
        except (OSError, RetentionError) as exc:
            tmp.unlink(missing_ok=True)
            # The plain log is still present and authoritative; surface the failure.
            raise RetentionError(
                f"{rec.run_id}: Kompression fehlgeschlagen: {exc}",
                failed=PruneResult(
                    rec.run_id, "failed", reason="events.jsonl unverändert erhalten"
                ),
            ) from exc
        plain.unlink()  # the source is removed only after a valid .gz is in place
        return PruneResult(rec.run_id, "compressed")
    # No plain source: already compressed ONLY if the .gz exists AND is valid.
    if gz.exists() and _is_valid_gzip(gz):
        return PruneResult(rec.run_id, "already_compressed")
    return PruneResult(rec.run_id, "skipped", reason="kein Ereignis-Log zum Komprimieren")


# --- public prune --------------------------------------------------------------


def prune(
    repo: Path,
    *,
    keep: int = 20,
    older_than: int | None = None,
    gzip_mode: bool = False,
    now: datetime | None = None,
) -> list[PruneResult]:
    """Prune the repo's runs. Protects the newest ``keep`` runs; among the rest
    processes only those at least ``older_than`` days old (if given). Non-terminal
    runs are skipped and named (C2); deleting mode also skips a run with a dirty
    worktree (C4). Returns one result per PROCESSED candidate (C7)."""
    now = now or datetime.now(UTC)
    runs_root = _runs_root(repo)
    if runs_root is None:
        # An escaping/invalid .adw/runs is unsafe to prune (could reach outside the
        # repo): a clear failure, never a silent empty run collection (P1).
        raise RetentionError(f"{repo}: .adw/runs liegt außerhalb des Repositorys — unsicher")
    records = scan_runs(repo)
    candidates = _candidates(records, keep)
    threshold = None if older_than is None else now - timedelta(days=older_than)
    results: list[PruneResult] = []
    for rec in candidates:
        if threshold is not None and not (rec.date <= threshold):
            continue  # younger than the age filter → kept, not reported
        if rec.phase not in _TERMINAL_PHASES:
            results.append(PruneResult(rec.run_id, "skipped", reason=f"phase {rec.phase}"))
            continue
        try:
            if gzip_mode:
                results.append(_compress_run(rec, runs_root))
            else:
                results.append(_delete_run(repo, rec, runs_root))
        except RetentionError as exc:
            # Preserve the progress made before the failure so the CLI can report the
            # achieved state (completed candidates + the failing run's partial state).
            exc.results = results
            if exc.failed is None:
                exc.failed = PruneResult(rec.run_id, "failed", reason=str(exc))
            raise
    return results
