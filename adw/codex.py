"""Codex reviewer: independent review via `codex exec` (read-only sandbox).

Known Limitations:
- The read-only sandbox of the Codex CLI prevents mutations, but not reads
  outside the cwd. A review input with prompt injection could therefore make
  the reviewer read files — the same risk as with any manual
  `codex review`. Mitigated: no secret env (safe_env), no
  user-configured MCP servers (isolated CODEX_HOME with auth.json only).
- The token back-sync is CAS + flock over check+write, but deliberately does
  NOT hold the lock across the entire review execution (that would serialize
  all parallel reviews for minutes). If two processes rotate simultaneously,
  a fresh token may be discarded in the extreme case — rare and
  recoverable (one-time `codex login`).
"""

import collections
import contextlib
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Literal, Protocol

from adw.env import safe_env
from adw.findings import SCHEMA_INSTRUCTION, ReviewResult, extract_review_result

CODEX_TIMEOUT = 900

ReviewKind = Literal["spec", "plan", "code"]

_KIND_INSTRUCTIONS: dict[ReviewKind, str] = {
    "spec": (
        "Review the specification critically, in BOTH directions. "
        "Gaps: Is the goal clear, are the acceptance criteria testable, are "
        "edge cases with real damage missing? "
        "Excess: Flag over-engineering as findings too — any mechanism, state, "
        "or test defending a scenario that cannot realistically occur in this "
        "system's deployment profile, or whose damage is already bounded by an "
        "existing backstop (TTL, webhook, log, retry). Proportionality to the "
        "issue is an acceptance criterion: every requirement must trace back "
        "to the issue or to a concrete failure with real damage. Respect "
        "explicit non-goals and scope ceilings in the issue; never demand "
        "mechanisms they exclude — such hardening belongs in the spec's "
        "'Deferred' section, not in acceptance criteria."
    ),
    "plan": (
        "Review the implementation plan AND the interface contract together, "
        "in BOTH directions. Gaps: Are the Workstreams consistent with the "
        "contract, is the contract complete enough for the Lanes to build "
        "independently? Excess: Flag over-engineering — plan steps, tests, or "
        "contract clauses exceeding the spec's scope or defending scenarios "
        "without real damage. With a single Workstream the contract must pin "
        "only externally observable surfaces (HTTP routes, events, template "
        "behavior), never internal helper signatures."
    ),
    "code": (
        "Review the code changes for correctness, silent data loss, "
        "concurrency problems and deviations from spec/plan/contract."
    ),
}

_SCHEMA_INSTRUCTION = SCHEMA_INSTRUCTION


class CodexError(Exception):
    """codex exec failed (exit code, timeout, not installed)."""


class CodexReviewer(Protocol):
    def review(self, kind: ReviewKind, content_refs: list[str], cwd: Path) -> ReviewResult: ...


def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


_CHUNK_BYTES = 8192
_STDOUT_TAIL_CHUNKS = 640  # × 8 KiB = max. 5 MiB — reicht für jedes finale JSON
_STDERR_TAIL_CHUNKS = 32  # × 8 KiB = max. 256 KiB Fehlerkontext


def _pump(stream, tail: collections.deque) -> None:
    while chunk := stream.read(_CHUNK_BYTES):
        tail.append(chunk)
    stream.close()


def _isolated_codex_home() -> tuple[Path, Path, bytes | None]:
    """Fresh CODEX_HOME with only auth.json from the real home ($CODEX_HOME
    or ~/.codex) — configuration (incl. MCP registrations) stays out.
    Additionally returns the original snapshot of auth.json for
    conflict detection during the back-sync."""
    source = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    isolated = Path(tempfile.mkdtemp(prefix="adw-codex-home-"))
    original: bytes | None = None
    try:
        auth = source / "auth.json"
        if auth.is_file():
            original = auth.read_bytes()
            (isolated / "auth.json").write_bytes(original)
    except BaseException:
        # Halbe Token-Kopie nie liegen lassen (Disk-voll, Metadaten-Fehler …).
        shutil.rmtree(isolated, ignore_errors=True)
        raise
    return isolated, source, original


def _sync_rotated_auth(isolated: Path, source: Path, original: bytes | None) -> bool:
    """codex may refresh auth.json — mirror the rotation atomically into the real home.

    Compare-and-swap semantics: writing happens only if the source still matches
    the original snapshot. If a PARALLEL process has already rotated the source,
    the source wins (our copy would be the stale one). Returns
    False only if a required sync failed — the caller then keeps the
    token copy."""
    rotated = isolated / "auth.json"
    if not rotated.is_file():
        return True
    target = source / "auth.json"
    try:
        new_content = rotated.read_bytes()
        if new_content == original:
            return True  # nichts rotiert
        source.mkdir(parents=True, exist_ok=True)
        # flock serialisiert Check+Write über Prozesse hinweg — sonst können
        # zwei Workflows beide "current == original" sehen und sich gegenseitig
        # frisch rotierte Tokens überschreiben.
        with open(source / ".auth.sync.lock", "a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            current = target.read_bytes() if target.is_file() else None
            if current == new_content:
                return True
            if current != original:
                return True  # externer Prozess hat rotiert — Quelle gewinnt
            fd, tmp_name = tempfile.mkstemp(dir=source, prefix=".auth.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(new_content)
                os.replace(tmp_name, target)
            except BaseException:
                # Nie eine Token-Kopie als tmp-File im echten Home hinterlassen.
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
        return True
    except OSError:
        return False


class CodexRunner:
    """Invokes the Codex CLI as a read-only subprocess and parses strictly."""

    def review(self, kind: ReviewKind, content_refs: list[str], cwd: Path) -> ReviewResult:
        prompt = self._build_prompt(kind, content_refs)
        argv = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            # User-konfigurierte MCP-Server abschalten: --sandbox read-only
            # beschränkt nur Shell-Kommandos, nicht MCP-Prozesse.
            "-c",
            "mcp_servers={}",
            "-C",
            str(cwd),
            prompt,
        ]
        env = safe_env()
        # Isoliertes CODEX_HOME: nur auth.json, KEINE config.toml — sonst
        # starten user-konfigurierte MCP-Server außerhalb der read-only-
        # Sandbox. -c mcp_servers={} allein MERGED nur und entfernt nichts.
        isolated_home, source_home, original_auth = _isolated_codex_home()
        env["CODEX_HOME"] = str(isolated_home)
        try:
            return self._execute(argv, env)
        finally:
            # Rotierte Tokens zurücksyncen (codex refresht auth.json ggf.),
            # dann die Token-Kopie aufräumen. Schlägt der Sync fehl, bleibt
            # das Temp-Home als letzte gültige Token-Kopie erhalten.
            if _sync_rotated_auth(isolated_home, source_home, original_auth):
                shutil.rmtree(isolated_home, ignore_errors=True)
            else:
                print(
                    f"WARNUNG: Rotiertes Codex-Token konnte nicht nach "
                    f"{source_home / 'auth.json'} geschrieben werden — Kopie "
                    f"liegt unter {isolated_home / 'auth.json'}",
                    file=sys.stderr,
                )

    @staticmethod
    def _execute(argv: list[str], env: dict[str, str]) -> ReviewResult:
        try:
            # Eigene Session: Bei Timeout/Interrupt stirbt die GANZE
            # Prozessgruppe (Codex spawnt Shell-Kommandos/MCP-Server).
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise CodexError(f"codex exec: {exc}") from exc
        # RAM-bounded Draining statt communicate(): Ein riesiges Tool-Transkript
        # darf den Orchestrator nicht fluten — geparst wird ohnehin nur das
        # finale JSON am Ende des Outputs.
        out_tail: collections.deque[bytes] = collections.deque(maxlen=_STDOUT_TAIL_CHUNKS)
        err_tail: collections.deque[bytes] = collections.deque(maxlen=_STDERR_TAIL_CHUNKS)
        pumps = [
            threading.Thread(target=_pump, args=(proc.stdout, out_tail), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, err_tail), daemon=True),
        ]
        for pump in pumps:
            pump.start()
        try:
            returncode = proc.wait(timeout=CODEX_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise CodexError(f"codex exec: Timeout nach {CODEX_TIMEOUT}s") from exc
        finally:
            # IMMER die Prozessgruppe aufräumen — auch nach normalem Exit:
            # detachte Kinder mit umgeleiteten Streams könnten weiterlaufen.
            _kill_group(proc)
            for pump in pumps:
                pump.join(timeout=5)
        stdout = b"".join(out_tail).decode("utf-8", errors="replace")
        stderr = b"".join(err_tail).decode("utf-8", errors="replace")
        if returncode != 0:
            raise CodexError(f"codex exec: Exit {returncode} — {stderr.strip()[:2000]}")
        return extract_review_result(stdout)

    @staticmethod
    def _build_prompt(kind: ReviewKind, content_refs: list[str]) -> str:
        refs = "\n".join(f"- {ref}" for ref in content_refs)
        return (
            f"You are an independent reviewer in an Agentic Developer Workflow "
            f"(review kind: {kind}).\n{_KIND_INSTRUCTIONS[kind]}\n\n"
            f"To review:\n{refs}\n\n{_SCHEMA_INSTRUCTION}"
        )
