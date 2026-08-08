"""Codex client: independent review AND artifact authoring via `codex exec`
(read-only sandbox).

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
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Literal, Protocol

from adw.agents import _PLAN_CONTENT_RULES, _SPEC_CONTENT_RULES
from adw.env import safe_env
from adw.events import NoOpEmitter
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
        "to the issue (provided as .adw/issue.md) or to a concrete failure "
        "with real damage. Respect "
        "explicit non-goals and scope ceilings in the issue; never demand "
        "mechanisms they exclude — such hardening belongs in the spec's "
        "'Deferred' section, not in acceptance criteria. Process requirements "
        "are a finding of the same category as over-engineering: acceptance "
        "criteria or Definition of Done items about commit messages, commit "
        "prefixes, branch topology or git history are unsatisfiable here — the "
        "orchestrator commits, not the implementation agent. A spec describes "
        "product behavior only; git behavior produced by the product's own code "
        "is such behavior and stays legitimate."
    ),
    "plan": (
        "Review the implementation plan AND the interface contract together, "
        "in BOTH directions. Gaps: Are the Workstreams consistent with the "
        "contract, is the contract complete enough for the Lanes to build "
        "independently? Excess: Flag over-engineering — plan steps, tests, or "
        "contract clauses exceeding the spec's scope or defending scenarios "
        "without real damage. With a single Workstream the contract must pin "
        "only externally observable surfaces (HTTP routes, events, template "
        "behavior), never internal helper signatures. Process requirements "
        "(commit messages, commit prefixes, branch topology, git history) are "
        "a finding too — the orchestrator commits, not the implementation agent."
    ),
    "code": (
        "Review the code changes for correctness, silent data loss, "
        "concurrency problems and deviations from spec/plan/contract."
    ),
}

_SCHEMA_INSTRUCTION = SCHEMA_INSTRUCTION

AuthorKind = Literal["spec", "plan"]

# Artefakte je Authoring-Auftrag — zugleich die Marker-Namen im Output.
_AUTHOR_BLOCKS: dict[AuthorKind, tuple[str, ...]] = {
    "spec": ("spec.md",),
    "plan": ("plan.md", "contract.yaml"),
}

# Dieselbe Quelle wie die Autor-System-Prompts der entsprechenden Agents in
# adw/agents.py — nur der Rollen-Rahmen ist codex-spezifisch. Beide Autoren
# müssen dieselbe Spec/Plan-Qualität liefern, sonst driften die Artefakte.
_AUTHOR_INSTRUCTIONS: dict[AuthorKind, str] = {
    "spec": (
        "You are the Spec author of an Agentic Developer Workflow. You write "
        "EXCLUSIVELY the specification following a fixed template (goal, scope, "
        "non-goals, acceptance criteria, Definition of Done). "
        f"{_SPEC_CONTENT_RULES}"
    ),
    "plan": (
        "You are the Plan author of an Agentic Developer Workflow. From "
        ".adw/spec.md you produce the implementation plan with the "
        "Workstreams 'backend' and 'frontend' (if the lane exists) as well as "
        "the interface contract (OpenAPI/types/events). "
        f"{_PLAN_CONTENT_RULES}"
    ),
}


class CodexError(Exception):
    """codex exec failed (exit code, timeout, not installed)."""


class CodexAuthorError(CodexError):
    """codex exec produced no usable marker block for a requested artifact."""


class CodexClient(Protocol):
    def review(
        self,
        kind: ReviewKind,
        content_refs: list[str],
        cwd: Path,
        context: str | None = None,
        emitter=None,
        span=None,
    ) -> ReviewResult: ...

    def author(self, kind: AuthorKind, task: str, cwd: Path) -> dict[str, str]: ...

    def effective_argv(
        self, kind: ReviewKind, content_refs: list[str], cwd: Path, context: str | None = None
    ) -> list[str]: ...


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


def _markers(name: str, marker_id: str) -> tuple[str, str]:
    """Begin/end marker of a block. The per-call nonce makes them
    collision-safe: an artifact may write ABOUT this protocol without its
    prose accidentally terminating its own block."""
    return f"===BEGIN {name} {marker_id}===", f"===END {name} {marker_id}==="


def _extract_blocks(stdout: str, names: tuple[str, ...], marker_id: str) -> dict[str, str]:
    """Contents between the begin/end markers, one block per name.

    Like the review JSON, only the LAST answer counts: the tool transcript may
    echo the prompt (incl. its empty marker template) before the actual answer.
    All blocks must therefore come from ONE coherent answer — parsing starts at
    the last block of the first name and then moves strictly FORWARD in the
    prompted order. Mixing an answer block with one from the echoed prompt
    would silently persist half a template as an artifact; the prompt's own
    blocks are empty and thus rejected."""
    first_begin, _ = _markers(names[0], marker_id)
    start = stdout.rfind(first_begin)
    if start == -1:
        raise CodexAuthorError(
            f"codex exec: Block {names[0]!r} fehlt im Output\n"
            f"--- Roh-Output (gekürzt) ---\n{stdout[-2000:]}"
        )
    blocks: dict[str, str] = {}
    cursor = start
    for name in names:
        begin, end = _markers(name, marker_id)
        begin_at = stdout.find(begin, cursor)
        end_at = stdout.find(end, begin_at + len(begin)) if begin_at != -1 else -1
        if begin_at == -1 or end_at == -1:
            raise CodexAuthorError(
                f"codex exec: Block {name!r} fehlt in der finalen Antwort\n"
                f"--- Roh-Output (gekürzt) ---\n{stdout[-2000:]}"
            )
        content = stdout[begin_at + len(begin) : end_at].strip("\n")
        if not content.strip():
            raise CodexAuthorError(f"codex exec: Block {name!r} ist leer")
        blocks[name] = content
        cursor = end_at + len(end)
    return blocks


class CodexRunner:
    """Invokes the Codex CLI as a read-only subprocess and parses strictly."""

    def __init__(self, emitter=None):
        # Optional run emitter injected by the orchestrator; the explicit
        # review(emitter=...) arg still wins (used by unit tests).
        self._emitter = emitter

    def effective_argv(
        self, kind: ReviewKind, content_refs: list[str], cwd: Path, context: str | None = None
    ) -> list[str]:
        """The exact argv this runner will execute for such a review — a static,
        side-effect-free builder so the call site can write the full argv into the
        codex.review span start event before the subprocess runs (GUI-SPEC §6)."""
        return self._argv(cwd, self._build_prompt(kind, content_refs, context))

    def review(
        self,
        kind: ReviewKind,
        content_refs: list[str],
        cwd: Path,
        context: str | None = None,
        emitter=None,
        span=None,
    ) -> ReviewResult:
        emitter = emitter or self._emitter or NoOpEmitter()
        prompt = self._build_prompt(kind, content_refs, context)
        # The codex.review SPAN lives at the call site in phases.py (GUI-SPEC §6):
        # with a handle the runner only fills its end payload and opens no span.
        # A direct call without a handle (unit tests) still gets a self-contained
        # span so the runner stays usable on its own.
        if span is not None:
            return self._review_into(prompt, cwd, span)
        argv = self._argv(cwd, prompt)
        with emitter.span(
            "codex.review",
            {"kind": kind, "argv": argv, "cwd": str(cwd), "custom_prompt": context},
        ) as handle:
            return self._review_into(prompt, cwd, handle)

    def _review_into(self, prompt: str, cwd: Path, handle) -> ReviewResult:
        # Deterministic defaults BEFORE the subprocess: a parse failure or an
        # unexpected exception still writes a complete end record.
        handle.end_payload = {"findings": [], "raw_stdout": "", "parse_ok": False}
        stdout = self._run(prompt, cwd)
        handle.end_payload["raw_stdout"] = stdout
        result = extract_review_result(stdout)
        handle.end_payload["findings"] = [f.model_dump() for f in result.findings]
        handle.end_payload["parse_ok"] = True
        return result

    def author(self, kind: AuthorKind, task: str, cwd: Path) -> dict[str, str]:
        """Have Codex author the artifacts of `kind`; returns file name -> content.

        Nothing is written: the read-only sandbox forbids it, so Codex returns the
        content in marker blocks and the caller persists it."""
        marker_id = secrets.token_hex(8)
        stdout = self._run(self._build_author_prompt(kind, task, marker_id), cwd)
        return _extract_blocks(stdout, _AUTHOR_BLOCKS[kind], marker_id)

    @staticmethod
    def _argv(cwd: Path, prompt: str) -> list[str]:
        return [
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

    def _run(self, prompt: str, cwd: Path) -> str:
        argv = self._argv(cwd, prompt)
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
    def _execute(argv: list[str], env: dict[str, str]) -> str:
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
        return stdout

    @staticmethod
    def _build_author_prompt(kind: AuthorKind, task: str, marker_id: str) -> str:
        # Vorlage bewusst OHNE Platzhalter-Inhalt: Echot codex nur den Prompt,
        # sind die Blöcke leer — und leere Blöcke sind ein CodexAuthorError
        # statt eines stillschweigend übernommenen Templates.
        blocks = "\n\n".join(
            "\n".join(_markers(name, marker_id)) for name in _AUTHOR_BLOCKS[kind]
        )
        return (
            f"{_AUTHOR_INSTRUCTIONS[kind]}\n\n"
            f"Task:\n{task.strip()}\n\n"
            f"You run in a read-only sandbox and write no files — read the repository "
            f"yourself and respond EXCLUSIVELY with the complete file content placed "
            f"between the marker lines below, each artifact in its own block, in this "
            f"order (no prose before, between or after the blocks, no markdown fence "
            f"around them, and repeat the markers verbatim including their id):\n{blocks}"
        )

    @staticmethod
    def _build_prompt(kind: ReviewKind, content_refs: list[str], context: str | None = None) -> str:
        refs = "\n".join(f"- {ref}" for ref in content_refs)
        # Kontextblock (Runde, Severity-Schwelle, Findings der Vorrunden) zwischen
        # Refs und Schema — die Antwortformat-Instruktion bleibt die letzte.
        block = f"{context.strip()}\n\n" if context and context.strip() else ""
        return (
            f"You are an independent reviewer in an Agentic Developer Workflow "
            f"(review kind: {kind}).\n{_KIND_INSTRUCTIONS[kind]}\n\n"
            f"To review:\n{refs}\n\n{block}{_SCHEMA_INSTRUCTION}"
        )
