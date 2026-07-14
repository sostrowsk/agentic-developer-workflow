"""Lane-Isolation: Git-Worktrees unter .adw/runs/<run_id>/trees/<lane> + Ports."""

import shlex
import socket
import subprocess
from pathlib import Path

from adw.config import Gate
from adw.env import safe_env
from adw.gates import run_gates
from adw.state import RUNS_RELPATH

_GIT_TIMEOUT = 60


class WorktreeError(Exception):
    """Git-Worktree-Operation fehlgeschlagen."""


def _git(repo: Path, *args: str) -> str:
    """Git-Query mit parsebarem stdout — nur für Kommandos OHNE Hook-Ausführung."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            # Whitelist-Env: keine Secrets in git-Subprozesse.
            env=safe_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"git {' '.join(args)}: Timeout") from exc
    if result.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def _git_effect(repo: Path, *args: str) -> None:
    """Git-Kommando mit Seiteneffekt (kann Hooks ausführen) — Output nur als
    RAM-bounded Tail für die Fehlermeldung, eine laute Hook kann den
    Orchestrator nicht fluten."""
    quoted = " ".join(shlex.quote(a) for a in ["git", "-C", str(repo), *args])
    report = run_gates(
        [Gate(name=f"git-{args[0]}", cmd=quoted, timeout=_GIT_TIMEOUT)], cwd=repo
    )
    if not report.passed:
        failure = report.failures[0]
        raise WorktreeError(f"git {' '.join(args)}: {failure.output}")


def _ensure_runs_gitignored(repo: Path) -> None:
    """Selbst-ignorierendes .gitignore — Run-Artefakte tauchen nie in git status auf."""
    runs_dir = repo / RUNS_RELPATH
    runs_dir.mkdir(parents=True, exist_ok=True)
    gitignore = runs_dir / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if "*" not in lines:
        lines.append("*")
        gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")


def lane_worktree_path(repo: Path, run_id: str, lane: str) -> Path:
    # resolve(): git -C <repo> interpretiert relative Pfad-Argumente relativ
    # zum Repo — ohne Absolutpfad entstünde <repo>/<repo>/.adw/…
    return repo.resolve() / RUNS_RELPATH / run_id / "trees" / lane


def lane_branch(run_id: str, lane: str) -> str:
    return f"adw/{run_id}/{lane}"


def create_lane_worktree(repo: Path, run_id: str, lane: str, base_branch: str) -> Path:
    """Worktree auf eigenem Lane-Branch ab base_branch; idempotent."""
    repo = repo.resolve()  # relative Pfade + `git -C` + cwd vertragen sich nicht
    _ensure_runs_gitignored(repo)
    path = lane_worktree_path(repo, run_id, lane)
    branch = lane_branch(run_id, lane)
    ready = _ready_marker(path)
    if _worktree_registered(repo, path):
        if path.is_dir() and ready.exists():
            current = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
            if current != branch:
                raise WorktreeError(
                    f"Worktree {path} steht auf '{current}' statt '{branch}' — "
                    "Lane-Isolation verletzt, bitte manuell klären"
                )
            return path
        # Ohne Ready-Marker ist der Worktree ein Rest eines fehlgeschlagenen
        # add (z. B. Hook-Fail) oder auf Platte gelöscht: wegräumen und neu.
        if path.is_dir():
            # Doppeltes --force: auch gelockte Worktrees (abgebrochenes add
            # hinterlässt "initializing"-Lock) sind unsere und müssen weg.
            _git_effect(repo, "worktree", "remove", "--force", "--force", str(path))
        _git_effect(repo, "worktree", "prune")
    # Vor JEDEM add-Versuch einen evtl. veralteten Marker entfernen — er darf
    # einen fehlschlagenden (Neu-)Aufbau nicht nachträglich adeln, auch wenn
    # der alte Worktree außerhalb von uns entfernt wurde.
    ready.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _branch_exists(repo, branch):
        # Resume-Fall: Lane-Branch existiert schon — auschecken statt -b.
        _git_effect(repo, "worktree", "add", str(path), branch)
    else:
        _git_effect(repo, "worktree", "add", "-b", branch, str(path), base_branch)
    # Marker erst NACH erfolgreichem add — nur vollständige Worktrees zählen.
    ready.touch()
    return path


def _ready_marker(path: Path) -> Path:
    # Liegt NEBEN dem Worktree (gitignorierter runs-Bereich), nie darin —
    # sonst würde er im Ziel-Repo als untracked File auftauchen.
    return path.with_name(path.name + ".ready")


def remove_lane_worktree(repo: Path, run_id: str, lane: str) -> None:
    repo = repo.resolve()
    path = lane_worktree_path(repo, run_id, lane)
    _ready_marker(path).unlink(missing_ok=True)
    if _worktree_registered(repo, path):
        _git_effect(repo, "worktree", "remove", "--force", str(path))
    # Existenz explizit prüfen statt lokalisierte stderr-Texte zu parsen —
    # nur der verifizierte Schon-weg-Fall wird übersprungen.
    if _branch_exists(repo, lane_branch(run_id, lane)):
        _git_effect(repo, "branch", "-D", lane_branch(run_id, lane))


def _branch_exists(repo: Path, branch: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=safe_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"show-ref {branch}: Timeout") from exc
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        # Der EINE dokumentierte Nicht-existiert-Fall — alles andere ist ein
        # echter Fehler und darf nicht als "Branch weg" durchgehen.
        return False
    raise WorktreeError(f"show-ref {branch}: rc={result.returncode} {result.stderr.strip()}")


def _worktree_registered(repo: Path, path: Path) -> bool:
    # -z: NUL-getrennt und ungequotet — C-Quoting bei Nicht-ASCII-Pfaden
    # (core.quotePath) würde den Vergleich sonst brechen.
    listing = _git(repo, "worktree", "list", "--porcelain", "-z")
    return any(
        record == f"worktree {path.resolve()}"
        for record in listing.split("\0")
    )


_LANE_PORT_BASE = {"backend": 9100, "frontend": 9200}
_PORT_RANGE = 50


def ports_for(run_id: str, lane: str) -> dict[str, int]:
    """Deterministischer Port aus run_id + Lane, mit Bind-Check-Ausweichen."""
    base = _LANE_PORT_BASE.get(lane, 9300)
    offset = int(run_id, 16) % _PORT_RANGE
    for attempt in range(_PORT_RANGE):
        port = base + (offset + attempt) % _PORT_RANGE
        if _port_is_free(port):
            return {lane: port}
    raise WorktreeError(f"Kein freier Port im Bereich {base}–{base + _PORT_RANGE - 1}")


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True
