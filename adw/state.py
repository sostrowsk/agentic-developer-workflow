"""RunState: persistierter Zustand eines ADW-Runs — Grundlage für Resume."""

import contextlib
import fcntl
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

RUN_ID_RE = re.compile(r"^[0-9a-f]{8}$")

RUNS_RELPATH = Path(".adw") / "runs"

Phase = Literal[
    "spec",
    "plan",
    "awaiting_approval",
    "build",
    "integration",
    "codex_review",
    "final_review",
    "ci",
    "done",
    "escalated",
]


class StateNotFoundError(Exception):
    """Kein (lesbarer) State für diese run_id."""


class LaneState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worktree: str
    branch: str
    session_id: str | None = None
    ports: dict[str, int] = Field(default_factory=dict)
    # Fork-Point der Lane (SHA bei Worktree-Erstellung) — Restaurationen
    # nutzen diesen unbeweglichen Stand, nicht den weiterrückenden Base-Branch.
    base_sha: str | None = None
    gate_iterations: int = 0
    fix_cycles: int = 0
    # Fertige Lanes werden beim Resume übersprungen — sonst würde ein Crash
    # vor dem Phasenübergang ein akzeptiertes Ergebnis neu bauen.
    completed: bool = False
    # Vom ORCHESTRATOR persistierter Beweis, dass die Gates grün waren —
    # Commit-Messages wären agent-fälschbar, dieser Flag nicht. gates_tree
    # bindet den Beweis an den EXAKTEN Baum-Inhalt (inkl. untracked Files).
    gates_passed: bool = False
    gates_tree: str | None = None
    # Offenes Gate-Feedback für den nächsten Fix-Lauf + Circuit-Breaker-Basis —
    # überlebt einen Crash zwischen Gate-Fail und Fix-Iteration.
    pending_task: str | None = None
    last_failures: list[str] = Field(default_factory=list)
    # HEAD vor dem Agent-Lauf: erkennt Agent-Commits auch über ein Crash-
    # Fenster hinweg (Orchestrator-only-Commit-Invariante).
    expected_head: str | None = None


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=RUN_ID_RE.pattern)
    issue: str
    phase: Phase
    parallel: bool
    lanes: dict[str, LaneState] = Field(default_factory=dict)
    approval_granted: bool = False
    # --no-approval muss den Crash überleben — der Resume-Aufruf kennt das
    # CLI-Flag nicht mehr.
    skip_approval: bool = False
    # Runden-Zähler der Integration/E2E-Phase — persistiert, damit ein Crash
    # das 10-Runden-Limit nicht zurücksetzt. last_failures ist die
    # Circuit-Breaker-Basis (wird erst NACH dem Fix-Dispatch fortgeschrieben).
    integration_rounds: int = 0
    integration_last_failures: list[str] = Field(default_factory=list)
    # Analog für den Codex-Code-Review-Loop (Phase 5) und den finalen
    # Review (Phase 6, nur Circuit-Breaker — das Limit sind die fix_cycles).
    review_rounds: int = 0
    review_last_failures: list[str] = Field(default_factory=list)
    final_review_last_failures: list[str] = Field(default_factory=list)
    # Checkpoint des Spec-/Plan-Authoring-Loops: Session, offener Fix-Task und
    # Findings-Basis überleben so einen Crash mitten im Review-Zyklus.
    authoring_session: str | None = None
    authoring_pending_task: str | None = None
    authoring_last_findings: list[str] = Field(default_factory=list)
    # Monotone Speicher-Sequenz für find_latest — Datei-mtimes haben nur
    # Kernel-Tick-Granularität und produzieren Gleichstände.
    seq: int = 0

    @classmethod
    def new(cls, issue: str, parallel: bool) -> "RunState":
        return cls(
            run_id=secrets.token_hex(4),
            issue=issue,
            phase="spec",
            parallel=parallel,
        )

    def run_dir(self, repo: Path) -> Path:
        return repo / RUNS_RELPATH / self.run_id

    def save(self, repo: Path) -> None:
        """Vollen Snapshot atomar speichern (tmp + rename, repo-weiter flock).

        Für konkurrierende Mutationen desselben Runs (parallele Lanes)
        stattdessen :meth:`update` benutzen — save() überschreibt den ganzen
        Snapshot und würde fremde Zwischenstände verlieren.
        """
        with _repo_lock(repo) as seq_fh:
            self.seq = _next_seq_locked(seq_fh, repo / RUNS_RELPATH)
            self._write_snapshot(repo)

    @classmethod
    def update(cls, repo: Path, run_id: str, mutate) -> "RunState":
        """Load → mutate → write als EINE Transaktion unter dem Repo-Lock.

        Der einzige verlustfreie Weg, einen Run aus mehreren Threads/Prozessen
        zu verändern (z. B. Lane-Zähler in parallelen Build-Lanes).
        """
        with _repo_lock(repo) as seq_fh:
            state = cls.load(repo, run_id)
            mutate(state)
            state.seq = _next_seq_locked(seq_fh, repo / RUNS_RELPATH)
            state._write_snapshot(repo)
            return state

    def _write_snapshot(self, repo: Path) -> None:
        run_dir = self.run_dir(repo)
        run_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=run_dir, prefix=".state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.model_dump_json(indent=2))
            os.replace(tmp_name, run_dir / "state.json")
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, repo: Path, run_id: str) -> "RunState":
        if not RUN_ID_RE.fullmatch(run_id):
            raise StateNotFoundError(f"Ungültige run_id: {run_id!r}")
        path = repo / RUNS_RELPATH / run_id / "state.json"
        if not path.is_file():
            raise StateNotFoundError(f"Kein State für Run {run_id} unter {path}")
        try:
            state = cls.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError, OSError) as exc:
            raise StateNotFoundError(f"State für Run {run_id} ist ungültig: {exc}") from exc
        if state.run_id != run_id:
            raise StateNotFoundError(
                f"State unter {run_id} gehört zu Run {state.run_id} — Datei kopiert/korrupt?"
            )
        return state

    @classmethod
    def find_latest(cls, repo: Path) -> "RunState":
        runs_dir = repo / RUNS_RELPATH
        states = []
        if runs_dir.is_dir():
            for path in runs_dir.glob("*/state.json"):
                try:
                    states.append(cls.load(repo, path.parent.name))
                except StateNotFoundError:
                    continue
        if not states:
            raise StateNotFoundError(f"Keine Runs unter {runs_dir}")
        return max(states, key=lambda s: s.seq)


@contextlib.contextmanager
def _repo_lock(repo: Path):
    """Exklusiver flock auf .adw/runs/.seq — serialisiert alle State-Writes."""
    runs_dir = repo / RUNS_RELPATH
    runs_dir.mkdir(parents=True, exist_ok=True)
    with open(runs_dir / ".seq", "a+", encoding="utf-8") as seq_fh:
        fcntl.flock(seq_fh, fcntl.LOCK_EX)
        yield seq_fh


def _next_seq_locked(seq_fh, runs_dir: Path) -> int:
    """Nächste monotone Sequenz — Aufrufer hält den flock auf .seq.

    Ist .seq leer/korrupt (z. B. Crash zwischen truncate und flush), wird die
    Sequenz aus den persistierten States rekonstruiert statt auf 0 zu fallen.
    """
    seq_fh.seek(0)
    raw = seq_fh.read().strip()
    try:
        counted = int(raw) if raw.isascii() else 0
    except ValueError:
        counted = 0
    # Immer gegen die persistierten States abgleichen: eine veraltete oder
    # kaputte .seq darf nie hinter existierende Runs zurückfallen.
    current = max(counted, _max_seq_from_states(runs_dir))
    seq_fh.seek(0)
    seq_fh.truncate()
    seq_fh.write(str(current + 1))
    seq_fh.flush()
    return current + 1


def _max_seq_from_states(runs_dir: Path) -> int:
    max_seq = 0
    for path in runs_dir.glob("*/state.json"):
        try:
            state = RunState.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError, OSError):
            continue
        max_seq = max(max_seq, state.seq)
    return max_seq
