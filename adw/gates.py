"""Deterministic Gates: lint/test/E2E commands with real timeouts."""

import collections
import contextlib
import os
import shlex
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from adw.config import Gate
from adw.env import safe_env

MAX_OUTPUT_LINES = 200


@dataclass(frozen=True)
class GateFailure:
    gate: str
    exit_code: int | None
    output: str
    timed_out: bool = False


@dataclass(frozen=True)
class GateReport:
    passed: bool
    failures: list[GateFailure] = field(default_factory=list)


class _LineTail:
    """Line-based rolling tail: exactly the last MAX_OUTPUT_LINES lines,
    each capped at _MAX_LINE_BYTES — RAM-bounded regardless of line length
    and total volume."""

    def __init__(self) -> None:
        self.lines: collections.deque[bytes] = collections.deque(maxlen=MAX_OUTPUT_LINES)
        self.dropped = 0

    def append(self, line: bytes) -> None:
        if len(self.lines) == MAX_OUTPUT_LINES:
            self.dropped += 1
        self.lines.append(line[:_MAX_LINE_BYTES])

    def text(self) -> str:
        body = b"\n".join(self.lines).decode("utf-8", errors="replace").strip("\n")
        if self.dropped:
            return f"… ({self.dropped} Zeilen gekappt)\n{body}"
        return body


def run_gates(
    gates: list[Gate],
    cwd: Path,
    extra_env: dict[str, str] | None = None,
    emitter=None,
) -> GateReport:
    """Runs Gates in order, stops at the first fail (fail fast).

    Every invocation runs with the env whitelist and the timeout required
    by the config — there is no Gate run without a time limit.

    ``emitter`` is additive and optional (default: no-op): each actually started
    gate is wrapped in a ``gate`` span. The end payload is initialized to the
    deterministic defaults right after the span opens and overwritten on a real
    outcome — so an unexpected exception leaves those defaults in place and
    propagates unchanged.
    """
    if emitter is None:
        # Lazy import: adw.events depends on adw.worktrees → adw.gates, so a
        # top-level import would be circular.
        from adw.events import NoOpEmitter

        emitter = NoOpEmitter()
    env = safe_env(extra_env)
    for gate in gates:
        with emitter.span(
            "gate", {"name": gate.name, "cmd": gate.cmd, "timeout": gate.timeout, "cwd": str(cwd)}
        ) as handle:
            handle.end_payload = {
                "passed": False, "exit_code": None, "timed_out": False, "output": ""
            }
            failure = _execute_gate(gate, cwd, env)
            if failure is None:
                handle.end_payload = {
                    "passed": True, "exit_code": 0, "timed_out": False, "output": ""
                }
                continue
            handle.end_payload = {
                "passed": False,
                "exit_code": failure.exit_code,
                "timed_out": failure.timed_out,
                "output": failure.output,
            }
        return GateReport(passed=False, failures=[failure])
    return GateReport(passed=True)


def _execute_gate(gate: Gate, cwd: Path, env: dict[str, str]) -> GateFailure | None:
    """Run one gate; return a GateFailure on fail/timeout, None on pass.

    Behaviour is identical to the pre-instrumentation inline loop body — only
    the return shape (GateFailure|None instead of GateReport) changed, so the
    caller can wrap it in a span."""
    try:
        argv = shlex.split(gate.cmd)
    except ValueError as exc:
        return GateFailure(gate=gate.name, exit_code=None, output=f"{gate.cmd}: {exc}")
    try:
        # Eigene Session: bei Timeout/Interrupt wird die GANZE
        # Prozessgruppe gekillt, nicht nur das direkte Kind — sonst
        # überleben npm-/pytest-Worker und blockieren Worktree/Ports.
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        # FileNotFoundError/PermissionError/embedded-NUL etc.: kaputte
        # Gate-Config oder fehlendes Tool — normaler Fix-/Eskalationspfad.
        return GateFailure(gate=gate.name, exit_code=None, output=f"{gate.cmd}: {exc}")
    # Rolling-Tail im Speicher: eine Firehose-Gate kann weder RAM noch
    # Platte fluten — es bleiben immer nur die letzten Zeilen erhalten.
    tail = _LineTail()
    pump = threading.Thread(target=_pump_stream, args=(proc.stdout, tail), daemon=True)
    pump.start()
    timed_out = False
    returncode = 0
    try:
        returncode = proc.wait(timeout=gate.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # IMMER die ganze Prozessgruppe aufräumen — egal ob Pass, Fail,
        # Timeout oder Ctrl-C: kein Gate darf Hintergrund-Kinder
        # hinterlassen, die Ports/Worktree für den nächsten Loop blockieren.
        _kill_group(proc)
        pump.join(timeout=5)
    if timed_out:
        return GateFailure(
            gate=gate.name,
            exit_code=None,
            output=tail.text() or f"Timeout nach {gate.timeout}s",
            timed_out=True,
        )
    if returncode != 0:
        return GateFailure(gate=gate.name, exit_code=returncode, output=tail.text())
    return None


def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


_CHUNK_BYTES = 8192
_MAX_LINE_BYTES = 4096


def _pump_stream(stream, tail: "_LineTail") -> None:
    partial = b""
    discarding = False
    while chunk := stream.read(_CHUNK_BYTES):
        if discarding:
            # Rest einer bereits gekappten Riesenzeile: bis zum nächsten
            # Newline verwerfen, statt Phantom-Zeilen zu erzeugen.
            if b"\n" not in chunk:
                continue
            _, chunk = chunk.split(b"\n", 1)
            discarding = False
        partial += chunk
        *complete, partial = partial.split(b"\n")
        for line in complete:
            tail.append(line)
        if len(partial) > _MAX_LINE_BYTES:
            tail.append(partial)  # _LineTail kappt auf _MAX_LINE_BYTES
            partial = b""
            discarding = True
    if partial and not discarding:
        tail.append(partial)
    stream.close()
