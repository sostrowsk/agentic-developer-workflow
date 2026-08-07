"""Event emitter for the ADW run event log (GUI-SPEC §4.1–§4.4).

Appends events as JSON Lines to ``.adw/runs/<run_id>/events.jsonl`` in the
target repo. The single most important invariant is **fail-open**: no
emitter-internal error (disk full, permissions, unserializable payload) ever
reaches the caller or aborts a run. On the first internal error for a run the
emitter warns once and then silently disables itself for that run (per
process). ``state.json`` remains the resume authority, so ``fsync`` is
deliberately not issued.
"""

import contextlib
import fcntl
import itertools
import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from adw.state import RUNS_RELPATH
from adw.worktrees import ensure_runs_gitignored

logger = logging.getLogger(__name__)

# Run-scoped, process-wide disable registry (GUI-SPEC §4.3 "once per run").
# Keyed by (resolved repo, run_id); no persistent/sidecar state (forbidden).
# _run_locks holds one write lock per run, shared by ALL emitter instances of
# that run so the disabled-check and the append are coordinated atomically
# (a disabled run never gets one more record from a racing instance). Both are
# guarded by _registry_lock, which is only ever held briefly and never across
# a run lock, file lock or I/O.
_disabled_runs: set[tuple[str, str]] = set()
_run_locks: dict[tuple[str, str], threading.Lock] = {}
_registry_lock = threading.Lock()


def _reset_registries_after_fork() -> None:
    """After os.fork() a child inherits copies of the parent's registries and
    locks — a run could stay wrongly disabled, or a run lock held by another
    (now absent) thread at fork time would deadlock the child. A forked child
    is a fresh process (GUI-SPEC §4.3), so recreate the mutex and clear the
    inherited disabled-run and run-lock state."""
    global _registry_lock
    _registry_lock = threading.Lock()
    _disabled_runs.clear()
    _run_locks.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_registries_after_fork)


def _utc_millis() -> str:
    """Current UTC time as YYYY-MM-DDTHH:MM:SS.mmmZ (millisecond precision)."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _new_span_id() -> str:
    return uuid.uuid4().hex


# Non-raising fallback id, used only when _new_span_id() itself fails. A plain
# process-global counter (next() on itertools.count is atomic under the GIL)
# cannot raise, keeping span-id generation inside the fail-open boundary.
_fallback_span_counter = itertools.count(1)


def _fallback_span_id() -> str:
    return f"span-fallback-{next(_fallback_span_counter)}"


class SpanHandle:
    """Yielded by ``span(...)``: carries the generated span ``id`` and a
    writable ``end_payload`` whose contents become the end event's payload."""

    def __init__(self, span_id: str):
        self.id = span_id
        self.end_payload: dict = {}


class EventEmitter:
    """Active emitter bound to a target repo and a run."""

    def __init__(self, repo: Path, run_id: str):
        self._run_id = run_id
        self._local = threading.local()
        # A single fail-open boundary around ALL path/key initialization: any
        # failure (including an unconvertible repo) permanently disables this
        # instance instead of escaping to the caller. The failing conversion is
        # never retried, so it cannot escape a second time.
        self._broken = False
        self._repo = None
        self._path = None
        self._key = None
        # In-memory seq cache (no sidecar files — additional_persistent_state is
        # forbidden): the seq this instance last wrote and the file size right
        # after that write. Lets _append skip re-scanning the whole log when no
        # other writer has appended since. Both are only touched under the flock.
        self._cached_seq: int | None = None
        self._cached_offset: int | None = None
        try:
            repo_path = Path(repo)
            try:
                repo_path = repo_path.resolve()
            except Exception:
                pass  # best effort — an unresolved path is still usable
            self._repo = repo_path
            self._path = repo_path / RUNS_RELPATH / run_id / "events.jsonl"
            self._key = (str(repo_path), run_id)
        except Exception as exc:  # construction stays fail-open
            self._broken = True
            logger.warning(
                "event log disabled: emitter construction failed: %s", exc
            )

    # -- fail-open registry --------------------------------------------------

    def _is_disabled(self) -> bool:
        if self._broken:
            return True
        with _registry_lock:
            return self._key in _disabled_runs

    def _run_lock(self) -> threading.Lock:
        """The process-wide write lock for this run, shared across instances."""
        with _registry_lock:
            lock = _run_locks.get(self._key)
            if lock is None:
                lock = threading.Lock()
                _run_locks[self._key] = lock
            return lock

    def _disable_with_warning(self, exc: BaseException) -> None:
        with _registry_lock:
            first = self._key not in _disabled_runs
            _disabled_runs.add(self._key)
        if first:
            logger.warning(
                "event log disabled for run %s: %s", self._run_id, exc
            )

    # -- writing -------------------------------------------------------------

    def _prepare(self) -> None:
        ensure_runs_gitignored(self._repo)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _next_seq_locked(self, fh, size: int) -> int:
        """Highest seq of the complete existing records + 1 (1 when empty).

        The caller holds the exclusive flock and passes the current file
        ``size``. To keep emit O(1) instead of O(log-size) (GUI-SPEC §9), only
        the region the instance has not yet accounted for is scanned:

        - if nothing was appended since our last write (size unchanged), return
          the cached seq + 1 without reading anything;
        - if the file only grew, scan just the newly appended lines starting
          from the cached offset, seeding the max with the cached seq;
        - otherwise (cold cache, or the file unexpectedly shrank) fall back to a
          full scan — this preserves the resume behavior of criterion 12.

        Partial/corrupt lines are ignored (their seq stays unreadable), so a
        crash-truncated tail never inflates the sequence.
        """
        if self._cached_offset is not None and size == self._cached_offset:
            return self._cached_seq + 1
        if self._cached_offset is not None and 0 < self._cached_offset < size:
            start, max_seq = self._cached_offset, self._cached_seq
        else:
            start, max_seq = 0, 0
        fh.seek(start)
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            seq = record.get("seq")
            if isinstance(seq, int) and seq > max_seq:
                max_seq = seq
        return max_seq + 1

    def _append(self, *, kind, type, payload, span, parent, phase, lane, round) -> None:
        self._prepare()
        record = {
            "seq": None,  # assigned under the lock
            "ts": _utc_millis(),
            "type": type,
            "kind": kind,
            "span": span,
            "parent": parent,
            "phase": phase,
            "lane": lane,
            "round": round,
            "payload": payload,
        }
        # os.open with mode 0600 sets the permissions on the *newly* created
        # file; the caller already holds the shared per-run lock (threads), and
        # flock serializes seq assignment + append across processes
        # (GUI-SPEC §4.3). Binary mode makes offset math and the trailing-byte
        # check exact. fsync is deliberately not called.
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        fh = os.fdopen(fd, "r+b")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            size = os.fstat(fd).st_size
            record["seq"] = self._next_seq_locked(fh, size)
            data = json.dumps(record, ensure_ascii=False).encode("utf-8")
            # Heal a crash-truncated trailing fragment: if the last byte is not
            # a newline, start the new record on a fresh line so it stays "one
            # JSON object per line" (criterion 1). Only adds bytes — append-only
            # is preserved; the fragment remains an isolated, ignorable line.
            prefix = b""
            if size > 0:
                fh.seek(size - 1)
                if fh.read(1) != b"\n":
                    prefix = b"\n"
            chunk = prefix + data + b"\n"
            fh.seek(0, os.SEEK_END)
            fh.write(chunk)
            fh.flush()
            self._cached_seq = record["seq"]
            self._cached_offset = size + len(chunk)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def _guarded_append(self, **record_fields) -> bool:
        """Fail-open write: coordinates the disabled-check and the append under
        the shared per-run lock, so a run disabled by any instance never gets a
        further record from a racing one. Returns True iff a record was written.
        """
        if self._is_disabled():  # fast path, no lock needed
            return False
        with self._run_lock():
            # Authoritative re-check under the lock: if another instance
            # disabled the run while we waited here, we must not append.
            if self._is_disabled():
                return False
            try:
                self._append(**record_fields)
                return True
            except Exception as exc:  # fail-open: never reaches the caller
                self._disable_with_warning(exc)
                return False

    # -- public API ----------------------------------------------------------

    def emit(
        self,
        type: str,
        payload: dict | None = None,
        *,
        phase: str | None = None,
        lane: str | None = None,
        round: int | None = None,
        span: str | None = None,
        parent: str | None = None,
    ) -> None:
        self._guarded_append(
            kind="point", type=type, payload=payload, span=span,
            parent=parent, phase=phase, lane=lane, round=round,
        )
        return None

    def _stack(self) -> list:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def _safe_span_id(self) -> str:
        """Fail-open span-id generation: on any failure disable the run (warning
        once) and fall back to a non-raising identifier, so the caller always
        gets a functional handle."""
        try:
            return _new_span_id()
        except Exception as exc:
            self._disable_with_warning(exc)
            return _fallback_span_id()

    @contextlib.contextmanager
    def span(
        self,
        type: str,
        payload: dict | None = None,
        *,
        phase: str | None = None,
        lane: str | None = None,
        round: int | None = None,
    ):
        handle = SpanHandle(self._safe_span_id())

        if self._is_disabled():
            # Functional handle, no events, body exceptions propagate.
            yield handle
            return

        stack = self._stack()
        parent = stack[-1] if stack else None
        started = self._guarded_append(
            kind="start", type=type, payload=payload, span=handle.id,
            parent=parent, phase=phase, lane=lane, round=round,
        )
        if not started:  # disabled or start write failed: still yield a handle
            yield handle
            return

        stack.append(handle.id)
        try:
            yield handle
        finally:
            if stack and stack[-1] == handle.id:
                stack.pop()
            # The end event is written even on a body exception; an emitter
            # error here stays fail-open (handled inside _guarded_append) and
            # never replaces that exception.
            self._guarded_append(
                kind="end", type=type, payload=handle.end_payload,
                span=handle.id, parent=parent, phase=phase,
                lane=lane, round=round,
            )


class NoOpEmitter:
    """No-op emitter with EXACTLY the same surface as EventEmitter: writes
    nothing, creates no file, and never raises an emitter-internal error.
    Body exceptions from its spans propagate unchanged (like the active one)."""

    def emit(
        self,
        type: str,
        payload: dict | None = None,
        *,
        phase: str | None = None,
        lane: str | None = None,
        round: int | None = None,
        span: str | None = None,
        parent: str | None = None,
    ) -> None:
        return None

    @contextlib.contextmanager
    def span(
        self,
        type: str,
        payload: dict | None = None,
        *,
        phase: str | None = None,
        lane: str | None = None,
        round: int | None = None,
    ):
        try:
            span_id = _new_span_id()
        except Exception:  # stay non-raising, like the active emitter
            span_id = _fallback_span_id()
        yield SpanHandle(span_id)
