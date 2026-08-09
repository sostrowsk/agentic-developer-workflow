"""Incremental, fault-tolerant reader for ``.adw/runs/<run_id>/events.jsonl``
(GUI-SPEC §4.2).

Tail-capable over a byte offset that lives only in memory (no offset/index/cache
files): each ``read()`` parses only what was appended since the last confirmed
offset. Only complete, ``\\n``-terminated lines are parsed; an unterminated last
line is left for a later read. A gap in the gap-free ``seq`` sequence is reported
as a ``seq_gap`` problem (a truncated log must not pass as complete), and a
single broken line is reported as ``bad_line`` without making the rest
unreadable. ``snapshot`` events and records of unknown type pass through
unchanged — the reader computes no diffs.
"""

import json
import os
from dataclasses import dataclass, field


@dataclass
class ReadProblem:
    """A problem encountered while reading. ``kind`` is ``"bad_line"`` or
    ``"seq_gap"``; the affected position is given by ``line_no`` and/or
    ``byte_offset``; ``expected``/``found`` are set only for ``seq_gap``."""

    kind: str
    line_no: int | None = None
    byte_offset: int | None = None
    expected: int | None = None
    found: int | None = None


@dataclass
class ReadResult:
    """The outcome of one ``read()``: parsed records (in file order, as mappings
    with all original fields) and the problems found — cleanly separated."""

    events: list[dict] = field(default_factory=list)
    problems: list[ReadProblem] = field(default_factory=list)


class EventReader:
    """Reads new, complete records from an ``events.jsonl`` since the last call.

    The confirmed byte ``offset`` and the seq-continuity cursor live only in
    memory; reading never modifies the log or creates files.
    """

    def __init__(self, path):
        self._path = os.fspath(path)
        self.offset: int = 0
        # Seq continuity is carried across reads (in-memory) so a gap is detected
        # even incrementally; None until the first valid record establishes it.
        self._last_seq: int | None = None
        # Physical line counter across reads, for line_no in reported problems.
        self._line_no: int = 0

    def read(self) -> ReadResult:
        result = ReadResult()
        if not os.path.exists(self._path):
            return result
        with open(self._path, "rb") as fh:
            fh.seek(self.offset)
            data = fh.read()

        read_start = self.offset
        consumed = 0
        while True:
            nl = data.find(b"\n", consumed)
            if nl == -1:
                break  # the remainder is an unterminated (partial) last line
            raw = data[consumed:nl]
            line_start_abs = read_start + consumed
            consumed = nl + 1
            self._line_no += 1
            self._handle_line(raw, self._line_no, line_start_abs, result)

        # Confirm only up to the end of the last complete line; the partial tail
        # stays unconfirmed and is retried (once) when it is completed.
        self.offset = read_start + consumed
        return result

    def _handle_line(self, raw: bytes, line_no: int, byte_offset: int, result: ReadResult) -> None:
        if raw.strip() == b"":
            return  # blank line: neither an event nor a problem
        try:
            record = json.loads(raw)
        except ValueError:
            result.problems.append(
                ReadProblem(kind="bad_line", line_no=line_no, byte_offset=byte_offset)
            )
            return
        seq = record.get("seq")
        if isinstance(seq, int):
            if self._last_seq is not None and seq > self._last_seq + 1:
                result.problems.append(
                    ReadProblem(
                        kind="seq_gap",
                        line_no=line_no,
                        byte_offset=byte_offset,
                        expected=self._last_seq + 1,
                        found=seq,
                    )
                )
            if self._last_seq is None or seq > self._last_seq:
                self._last_seq = seq
        result.events.append(record)
