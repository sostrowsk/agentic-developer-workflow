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
_disabled_runs: set[tuple[str, str]] = set()
_registry_lock = threading.Lock()


def _utc_millis() -> str:
    """Current UTC time as YYYY-MM-DDTHH:MM:SS.mmmZ (millisecond precision)."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _new_span_id() -> str:
    return uuid.uuid4().hex


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
        self._io_lock = threading.Lock()
        self._local = threading.local()
        try:
            self._repo = Path(repo).resolve()
        except Exception:  # construction is fail-open
            self._repo = Path(repo)
        self._path = self._repo / RUNS_RELPATH / run_id / "events.jsonl"
        self._key = (str(self._repo), run_id)

    # -- fail-open registry --------------------------------------------------

    def _is_disabled(self) -> bool:
        with _registry_lock:
            return self._key in _disabled_runs

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

    def _next_seq_locked(self, fh) -> int:
        """Highest seq of the complete existing records + 1 (1 when empty).

        The caller holds the exclusive flock. Partial/corrupt trailing lines
        are ignored, which also covers the resume scenario without any extra
        persistent sequence state.
        """
        fh.seek(0)
        max_seq = 0
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
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
        # file; flock serializes seq assignment + append across threads and
        # processes (GUI-SPEC §4.3). fsync is deliberately not called.
        with self._io_lock:
            fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
            fh = os.fdopen(fd, "r+", encoding="utf-8")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
                record["seq"] = self._next_seq_locked(fh)
                line = json.dumps(record, ensure_ascii=False)
                fh.seek(0, os.SEEK_END)
                fh.write(line + "\n")
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()

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
        if self._is_disabled():
            return None
        try:
            self._append(
                kind="point", type=type, payload=payload, span=span,
                parent=parent, phase=phase, lane=lane, round=round,
            )
        except Exception as exc:  # fail-open: never reaches the caller
            self._disable_with_warning(exc)
        return None

    def _stack(self) -> list:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

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
        handle = SpanHandle(_new_span_id())

        if self._is_disabled():
            # Functional handle, no events, body exceptions propagate.
            yield handle
            return

        stack = self._stack()
        parent = stack[-1] if stack else None
        try:
            self._append(
                kind="start", type=type, payload=payload, span=handle.id,
                parent=parent, phase=phase, lane=lane, round=round,
            )
        except Exception as exc:  # start write failed: disable, still yield
            self._disable_with_warning(exc)
            yield handle
            return

        stack.append(handle.id)
        try:
            yield handle
        finally:
            if stack and stack[-1] == handle.id:
                stack.pop()
            # The end event is written even on a body exception; an emitter
            # error here stays fail-open and never replaces that exception.
            if not self._is_disabled():
                try:
                    self._append(
                        kind="end", type=type, payload=handle.end_payload,
                        span=handle.id, parent=parent, phase=phase,
                        lane=lane, round=round,
                    )
                except Exception as exc:
                    self._disable_with_warning(exc)


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
        yield SpanHandle(_new_span_id())
