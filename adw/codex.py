"""Codex-Reviewer: unabhängiges Review via `codex exec` (read-only Sandbox).

Known Limitations:
- Die read-only-Sandbox der Codex-CLI verhindert Mutationen, aber keine Reads
  außerhalb des cwd. Ein Review-Input mit Prompt-Injection könnte den Reviewer
  also Dateien lesen lassen — dasselbe Risiko wie bei jedem manuellen
  `codex review`. Mitigiert: kein Secret-Env (safe_env), keine
  user-konfigurierten MCP-Server (isoliertes CODEX_HOME nur mit auth.json).
- Der Token-Rücksync ist CAS + flock über Check+Write, hält den Lock aber
  bewusst NICHT über die gesamte Review-Ausführung (das würde alle parallelen
  Reviews auf Minuten serialisieren). Rotieren zwei Prozesse gleichzeitig,
  kann im Extremfall ein frisches Token verworfen werden — selten und
  recoverbar (einmalig `codex login`).
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
from adw.findings import ReviewResult, extract_review_result

CODEX_TIMEOUT = 900

ReviewKind = Literal["spec", "plan", "code"]

_KIND_INSTRUCTIONS: dict[ReviewKind, str] = {
    "spec": (
        "Reviewe die Spezifikation kritisch: Ist das Ziel klar, der Scope "
        "vollständig, sind Akzeptanzkriterien testbar, fehlen Randfälle?"
    ),
    "plan": (
        "Reviewe Implementierungsplan UND Schnittstellen-Kontrakt gemeinsam: "
        "Sind die Workstreams konsistent zum Kontrakt, ist der Kontrakt "
        "vollständig genug, dass beide Lanes unabhängig bauen können?"
    ),
    "code": (
        "Reviewe die Code-Änderungen auf Korrektheit, stille Datenverluste, "
        "Concurrency-Probleme und Abweichungen von Spec/Plan/Kontrakt."
    ),
}

_SCHEMA_INSTRUCTION = """\
Antworte AUSSCHLIESSLICH mit einem JSON-Objekt nach exakt diesem Schema
(keine Prosa davor oder danach, optional in einem ```json-Fence):
{
  "verdict": "ok | needs_fixes",
  "findings": [{
    "severity": "P1 | P2 | P3",
    "lane": "frontend | backend | unknown",
    "file": "pfad/relativ/zum/repo",
    "issue": "Beschreibung des Problems",
    "remediation_plan": ["Schritt 1", "Schritt 2"]
  }]
}
Regeln: verdict "ok" nur mit leerem findings-Array; "needs_fixes" braucht
mindestens ein Finding; alle Felder sind Pflicht."""


class CodexError(Exception):
    """codex exec ist fehlgeschlagen (Exit-Code, Timeout, nicht installiert)."""


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
    """Frisches CODEX_HOME nur mit auth.json aus dem echten Home ($CODEX_HOME
    oder ~/.codex) — Konfiguration (inkl. MCP-Registrierungen) bleibt draußen.
    Liefert zusätzlich den Original-Snapshot der auth.json für die
    Konflikt-Erkennung beim Rücksync."""
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
    """codex kann auth.json refreshen — Rotation atomar ins echte Home spiegeln.

    Compare-and-swap-Semantik: Geschrieben wird nur, wenn die Quelle noch dem
    Original-Snapshot entspricht. Hat ein PARALLELER Prozess die Quelle bereits
    rotiert, gewinnt die Quelle (unsere Kopie wäre die veraltete). Rückgabe
    False nur, wenn ein nötiger Sync fehlschlug — der Aufrufer behält dann die
    Token-Kopie."""
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
    """Ruft die Codex-CLI als read-only Subprocess auf und parst strikt."""

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
            f"Du bist unabhängiger Reviewer in einem Agentic Developer Workflow "
            f"(Review-Art: {kind}).\n{_KIND_INSTRUCTIONS[kind]}\n\n"
            f"Zu reviewen:\n{refs}\n\n{_SCHEMA_INSTRUCTION}"
        )
