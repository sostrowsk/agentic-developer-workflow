"""Die sieben ADW-Phasen als Funktionen über einem RunContext (SPEC §4).

Kontrollfluss ist Code: Loops, Limits, Dispatch, Triage und State-Übergänge
leben hier — die Agenten liefern nur Urteilsvermögen (Texte, Findings).

Vertrauensgrenze: Die Konfiguration des ZIEL-Repos (.git/config, z. B.
clean-Filter, Signing-Programme) gilt als vertrauenswürdig — sie wird vom
Nutzer kontrolliert und liegt außerhalb der Schreibpfade der Agents (.git
ist nicht im Worktree-cwd). Orchestrator-Git läuft mit Env-Whitelist und
deaktivierten Repo-Hooks; konfigurierte Filter/Signer laufen wie bei jedem
manuellen git-Aufruf des Nutzers.
"""

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from adw import ci
from adw.agents import REGISTRY, AgentRunner
from adw.codex import CodexReviewer
from adw.config import AdwConfig, Gate
from adw.env import safe_env
from adw.findings import Finding, FindingsParseError, ReviewResult, extract_review_result
from adw.gates import GateReport, run_gates
from adw.state import LaneState, RunState
from adw.triage import (
    LimitExceededError,
    NoProgressError,
    check_fix_cycles,
    check_gate_iterations,
    check_progress,
    triage_final_review,
)
from adw.worktrees import (
    create_lane_worktree,
    ensure_runs_gitignored,
    lane_branch,
    lane_worktree_path,
    ports_for,
    remove_lane_worktree,
)

ARTIFACTS = ("spec.md", "plan.md", "contract.yaml")


class EscalationError(Exception):
    """Der Run kann nicht autonom weiter — Mensch übernimmt (Exit ≠ 0)."""


class AwaitingApproval(Exception):
    """Plan-Approval-Gate: Run pausiert, Fortsetzung via `adw approve`."""


@dataclass
class RunContext:
    repo: Path
    config: AdwConfig
    state: RunState
    agents: AgentRunner
    codex: CodexReviewer
    run_glab: ci.RunGlab = ci.run_glab
    sleep: Callable[[float], None] = time.sleep
    skip_approval: bool = False
    git_env: dict[str, str] = field(default_factory=dict)
    # Serialisiert State-Mutationen + Snapshot-Saves über parallele Lane-Threads —
    # sonst kann ein Save einen halb mutierten Zustand persistieren. RLock:
    # escalate() läuft teils unter bereits gehaltenem Lock.
    state_lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def run_dir(self) -> Path:
        return self.state.run_dir(self.repo)

    def save(self) -> None:
        self.state.save(self.repo)


def escalate(ctx: RunContext, reason: str) -> EscalationError:
    """Eskalations-Report schreiben, State markieren, Fehler zum Werfen liefern."""
    with ctx.state_lock:  # RLock: auch unter bereits gehaltenem Lock aufrufbar
        ctx.run_dir.mkdir(parents=True, exist_ok=True)
        report = ctx.run_dir / "escalation.md"
        report.write_text(
            f"# Eskalation — Run {ctx.state.run_id}\n\n"
            f"- Issue: {ctx.state.issue}\n"
            f"- Phase: {ctx.state.phase}\n"
            f"- Lanes: {', '.join(ctx.state.lanes) or '—'}\n\n"
            f"## Grund\n\n{reason}\n",
            encoding="utf-8",
        )
        ctx.state.phase = "escalated"
        ctx.save()
    return EscalationError(reason)


def run_spec_and_plan(ctx: RunContext) -> None:
    """Phasen 1–2: Spec- und Plan-Agent mit Codex-Review-Loops + Approval-Gate."""
    # Ignore-Regel VOR dem ersten State-/Artefakt-Write — der Haupt-Checkout
    # bleibt auch während der Approval-Pause sauber.
    ensure_runs_gitignored(ctx.repo)
    _validate_lanes(ctx)
    if ctx.skip_approval and not ctx.state.skip_approval:
        ctx.state.skip_approval = True
        ctx.save()  # sofort persistieren — der Resume-Aufruf kennt das Flag nicht
    skip = ctx.skip_approval or ctx.state.skip_approval
    # Die Ziel-Repo-Config ist für Agents tabu — Write(.adw/**) würde sie
    # technisch erlauben, deshalb Snapshot + Restore um die Authoring-Loops.
    config_path = ctx.repo / ".adw" / "config.yaml"
    config_snapshot = config_path.read_bytes() if config_path.is_file() else None
    if ctx.state.phase == "awaiting_approval":
        if ctx.state.approval_granted or skip:
            ctx.state.phase = "build"
            ctx.save()
            return
        ctx.save()
        raise AwaitingApproval(ctx.state.run_id)
    if ctx.state.phase not in ("spec", "plan"):
        return  # Resume in einer späteren Phase — hier nichts zu tun

    # protected: nach JEDEM Agent-Lauf (vor dem Review) und exception-sicher
    # am Ende restauriert — Agents dürfen diese Dateien nie effektiv ändern.
    protected: dict[Path, bytes | None] = {config_path: config_snapshot}
    try:
        # Fail fast (in Phase spec UND plan): uncommittete Nutzer-Edits an
        # getrackten Artefakten würde die Archivierung (git checkout --)
        # verwerfen. Ausnahme: die vom ADW selbst restaurierte Spec (siehe
        # Plan-Resume) ist kein Nutzer-Edit.
        for name in ARTIFACTS:
            if ctx.state.phase == "plan" and (ctx.run_dir / name).is_file():
                continue  # gehört diesem Run, wird gleich ohnehin überschrieben
            if (
                ctx.state.phase == "plan"
                and ctx.state.authoring_session
                and name in ("plan.md", "contract.yaml")
            ):
                continue  # eigener, gecrashter Plan-Loop — kein User-Edit
            if not _git(ctx, ctx.repo, "ls-files", "--", f".adw/{name}").strip():
                continue
            if _git(ctx, ctx.repo, "status", "--porcelain", "--", f".adw/{name}").strip():
                raise escalate(
                    ctx,
                    f".adw/{name} ist getrackt und hat uncommittete Änderungen — "
                    f"bitte committen oder stashen, der ADW würde sie verwerfen",
                )
        if ctx.state.phase == "spec":
            _reviewed_authoring_loop(
                ctx,
                agent_name="spec_agent",
                initial_task=(
                    f"Erstelle die Spezifikation für dieses Issue nach fester Vorlage "
                    f"(Ziel, Scope, Nicht-Ziele, Akzeptanzkriterien, Definition of Done) "
                    f"als .adw/spec.md.\n\nIssue:\n{ctx.state.issue}"
                ),
                review_kind="spec",
                artifacts=("spec.md",),
                protected=protected,
            )
            with ctx.state_lock:
                # Reviewte Spec SOFORT ins Run-Verzeichnis sichern: markiert sie
                # als eigenen Output (Guard-Ausnahme beim Resume) und schützt
                # sie vor dem Crash-Fenster bis zur Archivierung.
                ctx.run_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ctx.repo / ".adw" / "spec.md", ctx.run_dir / "spec.md")
                ctx.state.phase = "plan"
                ctx.save()  # Phase + geleerter Authoring-Checkpoint in EINEM Save

        # Phase "plan" — frisch erreicht ODER Resume nach Crash in der Plan-Phase.
        spec_path = ctx.repo / ".adw" / "spec.md"
        archived = ctx.run_dir / "spec.md"
        if archived.is_file():
            # Die ARCHIVIERTE (reviewte) Spec hat immer Vorrang: Ein Crash
            # mitten in der Archivierung kann .adw/spec.md bereits auf eine
            # alte getrackte Version zurückgesetzt haben.
            shutil.copy2(archived, spec_path)
        elif not spec_path.is_file():
            raise escalate(
                ctx,
                "Resume in Phase 'plan', aber .adw/spec.md fehlt — Spec-Ergebnis "
                "verloren, bitte Run neu starten",
            )
        # Die reviewte Spec ist ab hier fix — der Plan-Agent darf sie lesen,
        # aber nicht umschreiben (Write(.adw/**) würde es technisch erlauben).
        protected[spec_path] = spec_path.read_bytes()

        lanes = ", ".join(_active_lanes(ctx))
        _reviewed_authoring_loop(
            ctx,
            agent_name="plan_agent",
            initial_task=(
                f"Erstelle aus .adw/spec.md den Implementierungsplan .adw/plan.md mit "
                f"den Workstreams ({lanes}) und den Schnittstellen-Kontrakt "
                f".adw/contract.yaml."
            ),
            review_kind="plan",
            artifacts=("plan.md", "contract.yaml"),
            # Spec mitgeben: Codex prüft Plan/Kontrakt GEGEN die Akzeptanzkriterien.
            review_refs=("spec.md", "plan.md", "contract.yaml"),
            protected=protected,
        )
    finally:
        _restore_all(protected)
    _archive_artifacts(ctx)

    ctx.state.phase = "awaiting_approval"
    ctx.save()  # Phase + geleerter Authoring-Checkpoint in EINEM Save
    if skip or ctx.state.approval_granted:
        ctx.state.phase = "build"
        ctx.save()
        return
    raise AwaitingApproval(ctx.state.run_id)


def _restore_snapshot(path: Path, snapshot: bytes | None) -> None:
    """Datei exakt auf den Snapshot zurücksetzen (None = existierte nicht)."""
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    if not path.is_file() or path.read_bytes() != snapshot:
        path.write_bytes(snapshot)


def _restore_all(protected: dict[Path, bytes | None]) -> None:
    for path, snapshot in protected.items():
        _restore_snapshot(path, snapshot)


def _validate_lanes(ctx: RunContext) -> None:
    if ctx.state.parallel and not ctx.config.is_parallel_capable:
        raise escalate(
            ctx,
            "--parallel verlangt eine frontend- UND backend-Lane in .adw/config.yaml "
            f"— konfiguriert: {', '.join(ctx.config.lanes)}",
        )


def _active_lanes(ctx: RunContext) -> list[str]:
    if ctx.state.parallel:
        return list(ctx.config.lanes)
    return ["backend"] if "backend" in ctx.config.lanes else list(ctx.config.lanes)[:1]


def _reviewed_authoring_loop(
    ctx: RunContext,
    agent_name: str,
    initial_task: str,
    review_kind: str,
    artifacts: tuple[str, ...],
    review_refs: tuple[str, ...] | None = None,
    protected: dict[Path, bytes | None] | None = None,
) -> None:
    """Agent schreibt Artefakt(e), Codex reviewt, Findings gehen an DIESELBE
    Session zurück — bis Verdict ok. Circuit-Breaker bei identischen Findings.
    ``protected`` wird nach JEDEM Agent-Lauf restauriert — auch der Reviewer
    sieht nie eine vom Agenten umgeschriebene geschützte Datei."""
    spec = REGISTRY[agent_name]
    # Resume mitten im Authoring: Session, offener Fix-Task und Findings-Basis
    # aus dem State — sonst startet der Loop kontextlos neu und der
    # Prior-Content-Check würde das vorhandene Artefakt fälschlich eskalieren.
    resuming = ctx.state.authoring_session is not None
    session: str | None = ctx.state.authoring_session
    task = ctx.state.authoring_pending_task or initial_task
    previous_failures: list[str] | None = ctx.state.authoring_last_findings or None
    # Prior-Snapshot: Altbestand (z. B. gemergte Artefakte früherer Runs) darf
    # einen untätigen Agenten nicht adeln — das Artefakt muss sich ÄNDERN.
    prior: dict[str, bytes | None] = {}
    for name in artifacts:
        path = ctx.repo / ".adw" / name
        prior[name] = path.read_bytes() if path.is_file() else None
    first_iteration = not resuming
    while True:
        result = ctx.agents.run(spec, task, cwd=ctx.repo, resume=session)
        session = result.session_id or session
        with ctx.state_lock:
            ctx.state.authoring_session = session
            ctx.save()
        if protected:
            _restore_all(protected)
        missing = [name for name in artifacts if not (ctx.repo / ".adw" / name).is_file()]
        if missing:
            raise escalate(
                ctx,
                f"{agent_name} hat {', '.join(f'.adw/{m}' for m in missing)} "
                "nicht erzeugt — Lauf abgebrochen statt mit leerem Artefakt weiterzumachen",
            )
        if first_iteration:
            unchanged = [
                name
                for name in artifacts
                if prior[name] is not None
                and (ctx.repo / ".adw" / name).read_bytes() == prior[name]
            ]
            if unchanged:
                raise escalate(
                    ctx,
                    f"{agent_name} hat {', '.join(f'.adw/{u}' for u in unchanged)} "
                    "nicht verändert — Altbestand eines früheren Runs würde sonst "
                    "als neues Artefakt durchgehen",
                )
            first_iteration = False
        try:
            review = ctx.codex.review(
                review_kind,
                [f".adw/{name}" for name in (review_refs or artifacts)],
                cwd=ctx.repo,
            )
        except (FindingsParseError, ValidationError) as exc:
            raise escalate(ctx, f"Codex-{review_kind}-Review unlesbar: {exc}") from exc
        if review.verdict == "ok":
            # Bewusst KEIN Save hier: Das Leeren des Authoring-Checkpoints
            # passiert atomar mit dem Phasenübergang beim Aufrufer — sonst
            # gäbe es ein Crash-Fenster "Phase alt, Checkpoint weg".
            _clear_authoring_checkpoint(ctx)
            return
        failures = _finding_keys(review)
        try:
            check_progress(previous_failures, failures)
        except NoProgressError as exc:
            raise escalate(
                ctx,
                f"Codex-{review_kind}-Review meldet unverändert dieselben Findings — "
                f"Circuit-Breaker.\n\n{_findings_text(review)}",
            ) from exc
        previous_failures = failures
        task = (
            f"Der Codex-Review zu {', '.join(f'.adw/{a}' for a in artifacts)} hat "
            f"Findings. Arbeite sie ein und aktualisiere die Artefakte:\n\n"
            f"{_findings_text(review)}"
        )
        with ctx.state_lock:
            # Checkpoint des Fix-Zyklus — überlebt einen Crash vor dem Fix-Lauf.
            ctx.state.authoring_pending_task = task
            ctx.state.authoring_last_findings = failures
            ctx.save()


def _clear_authoring_checkpoint(ctx: RunContext) -> None:
    """Nur in-memory leeren — persistiert wird atomar mit dem Phasenübergang."""
    ctx.state.authoring_session = None
    ctx.state.authoring_pending_task = None
    ctx.state.authoring_last_findings = []


def _finding_keys(review: ReviewResult) -> list[str]:
    return sorted(f"{f.file}|{f.issue}" for f in review.findings)


def _findings_text(review: ReviewResult) -> str:
    lines = []
    for item in review.findings:
        plan = "; ".join(item.remediation_plan)
        lines.append(f"- [{item.severity}] {item.file}: {item.issue} (Empfehlung: {plan})")
    return "\n".join(lines)


def _archive_artifacts(ctx: RunContext) -> None:
    """Spec/Plan/Kontrakt in den Run-Ordner sichern; das Haupt-Repo bleibt sauber.

    Getrackte Artefakte (gemergter früherer ADW-Run) werden per git auf den
    eingecheckten Stand zurückgesetzt statt gelöscht — sonst bliebe eine
    tracked deletion im Checkout. Untracked Artefakte werden entfernt."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        source = ctx.repo / ".adw" / name
        if not source.is_file():
            continue
        shutil.copy2(source, ctx.run_dir / name)
        if _git(ctx, ctx.repo, "ls-files", "--", f".adw/{name}").strip():
            _git(ctx, ctx.repo, "checkout", "--", f".adw/{name}")
        else:
            source.unlink()


# --- Phase 3: Build-Lanes ---------------------------------------------------


def run_build_phase(ctx: RunContext) -> None:
    """Phase 3: Build-Agent(en) je Lane in isolierten Worktrees, Gate-Loop bis
    grün (max. 10 Iterationen, Circuit-Breaker), Commit durch den Orchestrator."""
    if ctx.state.phase != "build":
        return
    lanes = _active_lanes(ctx)
    if ctx.state.parallel and len(lanes) > 1:
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            futures = {lane: pool.submit(_run_lane, ctx, lane, lanes) for lane in lanes}
            for future in futures.values():
                future.result()  # Eskalationen propagieren
    else:
        for lane in lanes:
            _run_lane(ctx, lane, lanes)
    ctx.state.phase = "integration" if ctx.state.parallel else "codex_review"
    ctx.save()


def _run_lane(ctx: RunContext, lane: str, all_lanes: list[str]) -> None:
    existing = ctx.state.lanes.get(lane)
    worktree = create_lane_worktree(ctx.repo, ctx.state.run_id, lane, ctx.config.base_branch)
    if existing is not None and existing.completed:
        # Auch 'completed' wird revalidiert: Stimmt der Baum nicht mehr mit dem
        # persistierten Gate-Beweis überein (Manipulation im Crash-Fenster),
        # geht die Lane zurück in den Loop statt blind weitergereicht zu werden.
        if existing.gates_tree and existing.gates_tree == _worktree_tree_hash(ctx, worktree):
            return
        with ctx.state_lock:
            existing.completed = False
            existing.gates_passed = False
            existing.gates_tree = None
            ctx.save()
        existing = ctx.state.lanes.get(lane)
    if (
        existing is not None
        and existing.gates_passed
        and existing.gates_tree
        and existing.gates_tree == _worktree_tree_hash(ctx, worktree)
    ):
        # Crash nach dem persistierten Gates-grün-Beweis: nur noch abschließen —
        # aber NUR wenn der Baum noch EXAKT dem geprüften Stand entspricht
        # (Tree-Hash). Sonst zurück in den Loop: erneut Agent + Gates.
        _finalize_lane(ctx, worktree, lane, existing)
        return
    if existing is not None and existing.gates_passed:
        with ctx.state_lock:
            existing.gates_passed = False
            existing.gates_tree = None
            ctx.save()
    with ctx.state_lock:
        # Insertion unter dem Lock: parallele Lane-Threads mutieren dieselbe
        # lanes-Map, während save() sie serialisiert.
        lane_state = ctx.state.lanes.get(lane) or LaneState(
            worktree=str(worktree),
            branch=f"adw/{ctx.state.run_id}/{lane}",
            ports=ports_for(ctx.state.run_id, lane),
        )
        ctx.state.lanes[lane] = lane_state
        if lane_state.base_sha is None:
            # Fork-Point pinnen: der Base-Branch kann während des Runs
            # weiterrücken — Restaurationen brauchen den Stand der Lane.
            lane_state.base_sha = _git(
                ctx, worktree, "merge-base", ctx.config.base_branch, "HEAD"
            ).strip()
        ctx.save()
    _seed_artifacts(ctx, worktree)
    deny = [
        str(lane_worktree_path(ctx.repo, ctx.state.run_id, other))
        for other in all_lanes
        if other != lane
    ]
    spec = REGISTRY["build_agent"]
    # Resume: offenes Gate-Feedback und Circuit-Breaker-Basis aus dem State —
    # sonst verliert ein Crash zwischen Gate-Fail und Fix-Lauf den Kontext.
    task = lane_state.pending_task or (
        f"Implementiere den Workstream '{lane}' aus .adw/plan.md strikt gegen "
        f".adw/contract.yaml (Spec: .adw/spec.md). TDD: Test zuerst, RED "
        f"bestätigen, dann minimal implementieren. Du committest nicht.\n\n"
        f"Issue:\n{ctx.state.issue}"
    )
    previous_failures: list[str] | None = lane_state.last_failures or None
    while True:
        current_head = _git(ctx, worktree, "rev-parse", "HEAD").strip()
        with ctx.state_lock:
            # Agent-Commit aus einem Crash-Fenster erkennen: expected_head
            # ist der persistierte HEAD VOR dem letzten Agent-Lauf.
            if lane_state.expected_head and lane_state.expected_head != current_head:
                raise escalate(
                    ctx,
                    f"Lane {lane}: HEAD hat sich außerhalb des Orchestrators bewegt "
                    f"(Agent-Commit im Crash-Fenster?) — Commits sind Sache des "
                    f"Orchestrators",
                )
            # Limit VOR dem Versuch prüfen — nach Crash+Resume bei Iteration 10
            # darf kein 11. Versuch starten.
            try:
                check_gate_iterations(lane_state)
            except LimitExceededError as exc:
                raise escalate(ctx, f"Lane {lane}: {exc}") from exc
            lane_state.gate_iterations += 1
            lane_state.expected_head = current_head
            resume = lane_state.session_id
            ctx.save()  # Checkpoint VOR dem Agent-Lauf
        result = ctx.agents.run(spec, task, cwd=worktree, resume=resume, deny_read_paths=deny)
        if _git(ctx, worktree, "rev-parse", "HEAD").strip() != current_head:
            raise escalate(
                ctx,
                f"Lane {lane}: der Build-Agent hat selbst committet — Commits "
                f"sind Sache des Orchestrators (Reviewer-/Gate-Invariante verletzt)",
            )
        _require_lane_branch(ctx, worktree, lane_state)
        with ctx.state_lock:
            lane_state.session_id = result.session_id or lane_state.session_id
            ctx.save()  # Checkpoint VOR den (potenziell langen) Gates
        # VOR den Gates: approvte Artefakte restaurieren — Gates und Commit
        # müssen exakt denselben, approvten Stand sehen.
        _restore_approved_artifacts(ctx, worktree, lane_state.base_sha or ctx.config.base_branch)
        if lane_state.gate_iterations == 1 and not _worktree_dirty(ctx, worktree):
            raise escalate(
                ctx,
                f"Build-Agent (Lane {lane}) hat keine Änderungen im Worktree "
                f"hinterlassen — nichts zu bauen oder Agent-Lauf wirkungslos",
            )
        # Kein Save zwischen Gates und Feedback-Persistenz — jeder Checkpoint
        # nach einem Gate-Fail muss pending_task/last_failures bereits tragen.
        report = _run_lane_gates(ctx, lane, worktree, lane_state)
        if report.passed:
            break
        failures = [f"{f.gate}|{f.exit_code}|{f.output}" for f in report.failures]
        try:
            check_progress(previous_failures, failures)
        except NoProgressError as exc:
            raise escalate(
                ctx,
                f"Lane {lane}: Gate-Fix-Iteration hat nichts verändert — "
                f"Circuit-Breaker.\n\n{_gate_failure_text(report)}",
            ) from exc
        previous_failures = failures
        task = (
            f"Die Gates sind fehlgeschlagen. Analysiere die Ausgabe und fixe "
            f"die Ursache (kein Workaround an den Gates vorbei):\n\n"
            f"{_gate_failure_text(report)}"
        )
        with ctx.state_lock:
            lane_state.pending_task = task
            lane_state.last_failures = failures
            ctx.save()
    with ctx.state_lock:
        lane_state.pending_task = None
        lane_state.last_failures = []
        lane_state.expected_head = None  # ab hier committet der Orchestrator selbst
        lane_state.gates_passed = True  # persistierter Beweis VOR dem Commit,
        lane_state.gates_tree = _worktree_tree_hash(ctx, worktree)  # an den Baum gebunden
        ctx.save()
    _finalize_lane(ctx, worktree, lane, lane_state)


def _require_lane_branch(ctx: RunContext, worktree: Path, lane_state: LaneState) -> None:
    """Ein SHA-gleicher Branch-Wechsel (git switch --detach) würde den
    HEAD-Check passieren — der symbolische Ref muss der Lane-Branch bleiben."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        timeout=120,
        env=safe_env(ctx.git_env),
    )
    current_ref = result.stdout.strip()
    if result.returncode != 0 or current_ref != lane_state.branch:
        raise escalate(
            ctx,
            f"Worktree steht nicht mehr auf dem Lane-Branch {lane_state.branch!r} "
            f"(aktuell: {current_ref or 'detached HEAD'}) — Branch-Wechsel durch "
            f"den Agenten?",
        )


def _finalize_lane(ctx: RunContext, worktree: Path, lane: str, lane_state: LaneState) -> None:
    """Nach nachweislich grünen Gates: committen, Implementierung prüfen, abschließen."""
    _require_lane_branch(ctx, worktree, lane_state)
    _commit_lane(ctx, worktree, lane, lane_state.base_sha or ctx.config.base_branch)
    if not _has_implementation_changes(ctx, worktree):
        raise escalate(
            ctx,
            f"Lane {lane}: Gates grün, aber keine Implementierungs-Änderungen "
            f"gegenüber {ctx.config.base_branch} (nur Artefakte) — Agent hat "
            f"nichts gebaut oder alles zurückgebaut",
        )
    with ctx.state_lock:
        lane_state.completed = True
        ctx.save()


def _worktree_tree_hash(ctx: RunContext, worktree: Path) -> str:
    """Inhalts-Hash des kompletten Worktree-Baums (inkl. untracked, ohne Ignoriertes).

    Läuft über einen TEMPORÄREN Git-Index — der echte Index bleibt unberührt.
    Bindet den gates_passed-Beweis an exakt diesen Stand."""
    tmp_index = ctx.run_dir / f".treehash-index-{os.getpid()}-{threading.get_ident()}"
    env = safe_env(ctx.git_env)
    env["GIT_INDEX_FILE"] = str(tmp_index)
    try:
        # Index aus HEAD seeden: sonst fehlen getrackte Dateien, die zugleich
        # einem Ignore-Pattern entsprechen, im Hash (add -A überspränge sie).
        for args in (["read-tree", "HEAD"], ["add", "-A"], ["write-tree"]):
            result = subprocess.run(
                ["git", "-C", str(worktree), "-c", "core.hooksPath=/dev/null", *args],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if result.returncode != 0:
                raise escalate(
                    ctx, f"Tree-Hash fehlgeschlagen (git {args[0]}): {result.stderr.strip()}"
                )
        return result.stdout.strip()
    finally:
        tmp_index.unlink(missing_ok=True)


def _seed_artifacts(ctx: RunContext, worktree: Path) -> None:
    """Spec/Plan/Kontrakt aus dem Run-Ordner in den Lane-Worktree kopieren und
    committen — der Build-Agent arbeitet gegen eingecheckte Artefakte."""
    target_dir = worktree / ".adw"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        source = ctx.run_dir / name
        if not source.is_file():
            raise escalate(
                ctx,
                f"Archiviertes Artefakt {name} fehlt im Run-Ordner — keine Lane "
                f"darf gegen unvollständige/unapprovte Artefakte bauen",
            )
        shutil.copy2(source, target_dir / name)
    # Nur committen, wenn sich die ARTEFAKTE selbst geändert haben — beim
    # Resume kann der Worktree außerhalb von .adw dirty sein (Crash-Reste),
    # und ein leerer Commit würde den Lauf fälschlich eskalieren.
    if _git(ctx, worktree, "status", "--porcelain", "--", ".adw").strip():
        _git(ctx, worktree, "add", ".adw")
        _git(ctx, worktree, "commit", "-m", f"adw({ctx.state.run_id}): Spec/Plan/Kontrakt")


def _run_lane_gates(ctx: RunContext, lane: str, worktree: Path, lane_state: LaneState):
    gates = ctx.config.lanes[lane].gates
    extra_env = {f"{name.upper()}_PORT": str(port) for name, port in lane_state.ports.items()}
    return run_gates(gates, cwd=worktree, extra_env=extra_env)


def _commit_lane(ctx: RunContext, worktree: Path, lane: str, base_ref: str) -> None:
    _restore_approved_artifacts(ctx, worktree, base_ref)
    if not _worktree_dirty(ctx, worktree):
        return
    _git(ctx, worktree, "add", "-A")
    _git(
        ctx,
        worktree,
        "commit",
        "-m",
        f"adw({ctx.state.run_id}/{lane}): Build-Ergebnis nach grünen Gates",
    )


def _has_implementation_changes(ctx: RunContext, worktree: Path) -> bool:
    """Gibt es gegenüber dem Base-Branch Änderungen JENSEITS der .adw-Artefakte?"""
    changed = _git(
        ctx, worktree, "diff", "--name-only", f"{ctx.config.base_branch}...HEAD"
    ).splitlines()
    return any(name and not name.startswith(".adw/") for name in changed)


def _restore_approved_artifacts(ctx: RunContext, worktree: Path, base_ref: str) -> None:
    """Der Build-Agent baut GEGEN Spec/Plan/Kontrakt — er ändert sie nicht.

    Abweichungen von den approvten Versionen im Run-Ordner werden vor dem
    Commit zurückgesetzt; sonst könnte eine Lane den Kontrakt still umschreiben
    und parallele Lanes bauten gegen unterschiedliche Artefakte."""
    adw_dir = worktree / ".adw"
    if adw_dir.is_symlink():
        # .adw selbst als Symlink: NIE folgen — sonst schreibt der
        # unsandboxte Orchestrator außerhalb der Lane.
        adw_dir.unlink()
    elif adw_dir.exists() and not adw_dir.is_dir():
        adw_dir.unlink()  # .adw als Datei: ersetzen statt FileExistsError
    adw_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        approved = ctx.run_dir / name
        current = worktree / ".adw" / name
        if not approved.is_file():
            continue
        if current.is_symlink():
            # Symlink NIE folgen: is_dir/copy2 würden sonst den Referenten
            # treffen (Fremddatei-Korruption) statt das Artefakt zu ersetzen.
            current.unlink()
        elif current.is_dir():
            # Agent hat das Artefakt durch ein Verzeichnis ersetzt — wegräumen.
            shutil.rmtree(current)
        if not current.is_file() or current.read_bytes() != approved.read_bytes():
            shutil.copy2(approved, current)
    # Auch die Workflow-Config ist tabu: auf den Stand des Base-Branch
    # zurücksetzen. Schlägt das fehl (Config im Ziel-Repo untracked, also
    # nicht im Base-Branch), wird eine evtl. vom Agenten eingeschleuste
    # Config gelöscht statt still committet.
    restored = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            base_ref,
            "--",
            ".adw/config.yaml",
        ],
        capture_output=True,
        timeout=120,
        env=safe_env(ctx.git_env),
    )
    if restored.returncode != 0:
        # Nur der POSITIV verifizierte Nicht-im-Base-Branch-Fall rechtfertigt
        # das Löschen: ls-tree rc==0 mit LEEREM Output. rc!=0 (kaputter Ref,
        # Repo-Fehler) oder nicht-leerer Output → eskalieren, nichts löschen.
        on_base = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "core.hooksPath=/dev/null",
                "ls-tree",
                base_ref,
                "--",
                ".adw/config.yaml",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=safe_env(ctx.git_env),
        )
        if on_base.returncode != 0 or on_base.stdout.strip():
            raise escalate(
                ctx,
                f"Config-Restore im Worktree fehlgeschlagen: "
                f"{restored.stderr.decode(errors='replace').strip()} / "
                f"{on_base.stderr.strip()}",
            )
        injected = worktree / ".adw" / "config.yaml"
        if injected.is_symlink():
            injected.unlink()  # Symlink NIE folgen (is_dir/rmtree träfen den Referenten)
        elif injected.is_dir():
            shutil.rmtree(injected)  # Agent hat ein Verzeichnis eingeschleust
        else:
            injected.unlink(missing_ok=True)


def _worktree_dirty(ctx: RunContext, worktree: Path) -> bool:
    return bool(_git(ctx, worktree, "status", "--porcelain").strip())


def _git(ctx: RunContext, cwd: Path, *args: str) -> str:
    """Git durch den Orchestrator — Env-Whitelist, Repo-Hooks deaktiviert."""
    result = subprocess.run(
        ["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=safe_env(ctx.git_env),
    )
    if result.returncode != 0:
        raise escalate(
            ctx, f"git {' '.join(args)} in {cwd} fehlgeschlagen: {result.stderr.strip()}"
        )
    return result.stdout


# --- Phase 4: Integration + E2E (nur --parallel) -----------------------------

MAX_E2E_ROUNDS = 10
INTEGRATION_LANE = "integration"


def run_integration_phase(ctx: RunContext) -> None:
    """Phase 4: Lane-Branches auf einen Integrations-Branch mergen, E2E-Gate
    fahren; bei Rot triagiert der E2E-Agent die Fehler in die Lanes zurück.
    Max. 10 Runden, Circuit-Breaker bei identischem E2E-Output."""
    if ctx.state.phase != "integration":
        return
    lanes = _active_lanes(ctx)
    _resume_pending_lanes(ctx, lanes)
    _integration_loop(ctx, lanes)
    with ctx.state_lock:
        ctx.state.phase = "codex_review"
        ctx.save()


def _integration_loop(ctx: RunContext, lanes: list[str]) -> Path:
    """Merge + E2E bis grün (Kern von Phase 4) — liefert den grünen
    Integrations-Worktree. Auch die Review-Phasen laufen hier durch: JEDER
    Review-Fix muss wieder durchs E2E-Gate, nicht nur durch die Lane-Gates.
    Das Runden-Budget (integration_rounds) gilt run-weit."""
    previous: list[str] | None = ctx.state.integration_last_failures or None
    while True:
        # Limit VOR Merge/E2E prüfen: Ein Crash zwischen Runden-Save und
        # Eskalation darf beim Resume keine weitere Runde starten.
        if ctx.state.integration_rounds >= MAX_E2E_ROUNDS:
            raise escalate(
                ctx,
                f"Integration/E2E: {ctx.state.integration_rounds} Runden erreicht "
                f"(Limit {MAX_E2E_ROUNDS}) — Eskalation",
            )
        worktree = _fresh_integration_worktree(ctx, lanes)
        report = _run_e2e_gate(ctx, worktree, lanes)
        if report is None or report.passed:
            break
        with ctx.state_lock:
            ctx.state.integration_rounds += 1
            ctx.save()  # Runden-Limit überlebt jeden Crash ab hier
        if ctx.state.integration_rounds >= MAX_E2E_ROUNDS:
            raise escalate(
                ctx,
                f"Integration/E2E: {ctx.state.integration_rounds} Runden erreicht "
                f"(Limit {MAX_E2E_ROUNDS}) — Eskalation.\n\n{_gate_failure_text(report)}",
            )
        failures = [f"{f.gate}|{f.exit_code}|{f.output}" for f in report.failures]
        try:
            check_progress(previous, failures)
        except NoProgressError as exc:
            raise escalate(
                ctx,
                f"E2E-Fix-Runde hat nichts verändert — Circuit-Breaker.\n\n"
                f"{_gate_failure_text(report)}",
            ) from exc
        review = _triage_e2e(ctx, worktree, report)
        _dispatch_lane_fixes(ctx, review.findings, lanes, source="E2E")
        with ctx.state_lock:
            # Erst NACH dem Fix-Dispatch fortschreiben: ein Crash davor darf
            # beim Resume nicht als "identische Runde" fehl-eskalieren.
            ctx.state.integration_last_failures = failures
            ctx.save()
        previous = failures
    if ctx.state.integration_last_failures:
        with ctx.state_lock:
            ctx.state.integration_last_failures = []
            ctx.save()
    return worktree


def _fresh_integration_worktree(ctx: RunContext, lanes: list[str]) -> Path:
    """Integrations-Branch je Runde frisch ab Base-Branch aufbauen und alle
    Lane-Branches hineinmergen — idempotent und damit crash-sicher."""
    remove_lane_worktree(ctx.repo, ctx.state.run_id, INTEGRATION_LANE)
    worktree = create_lane_worktree(
        ctx.repo, ctx.state.run_id, INTEGRATION_LANE, ctx.config.base_branch
    )
    for lane in lanes:
        branch = lane_branch(ctx.state.run_id, lane)
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(worktree),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "merge",
                    "--no-edit",
                    branch,
                ],
                capture_output=True,
                text=True,
                timeout=300,
                env=safe_env(ctx.git_env),
            )
        except subprocess.TimeoutExpired as exc:
            _abort_merge(ctx, worktree)
            raise escalate(
                ctx,
                f"Integrations-Merge der Lane {lane} ({branch}) hat das Timeout "
                f"überschritten — Merge abgebrochen, Mensch übernimmt",
            ) from exc
        if result.returncode != 0:
            _abort_merge(ctx, worktree)
            raise escalate(
                ctx,
                f"Integrations-Merge der Lane {lane} ({branch}) fehlgeschlagen — "
                f"Konflikt braucht menschliche Auflösung:\n"
                f"{result.stdout.strip()}\n{result.stderr.strip()}",
            )
    return worktree


def _abort_merge(ctx: RunContext, worktree: Path) -> None:
    """Best effort: halb gemergten Zustand nicht liegen lassen."""
    try:
        subprocess.run(
            ["git", "-C", str(worktree), "-c", "core.hooksPath=/dev/null", "merge", "--abort"],
            capture_output=True,
            timeout=120,
            env=safe_env(ctx.git_env),
        )
    except (subprocess.TimeoutExpired, OSError):
        pass  # der Worktree wird ohnehin je Runde neu aufgebaut


def _run_e2e_gate(ctx: RunContext, worktree: Path, lanes: list[str]) -> GateReport | None:
    if ctx.config.e2e is None:
        return None  # keine E2E-Config: Integration ist reiner Merge
    gate = Gate(name="e2e", cmd=ctx.config.e2e.cmd, timeout=ctx.config.e2e.timeout)
    extra_env: dict[str, str] = {}
    for lane in lanes:
        for name, port in ctx.state.lanes[lane].ports.items():
            extra_env[f"{name.upper()}_PORT"] = str(port)
    return run_gates([gate], cwd=worktree, extra_env=extra_env)


def _triage_e2e(ctx: RunContext, worktree: Path, report: GateReport) -> ReviewResult:
    spec = REGISTRY["e2e_triage"]
    task = (
        "Die E2E-Tests auf dem Integrations-Branch sind rot. Ordne jeden Fehler "
        "einer Lane zu (frontend/backend/unknown) und antworte AUSSCHLIESSLICH "
        "mit dem Findings-JSON (verdict/findings, siehe Schema-Konvention):\n\n"
        f"{_gate_failure_text(report)}"
    )
    result = ctx.agents.run(spec, task, cwd=worktree, resume=None)
    try:
        return extract_review_result(result.text)
    except (FindingsParseError, ValidationError) as exc:
        raise escalate(ctx, f"E2E-Triage-Output unlesbar: {exc}") from exc


def _dispatch_lane_fixes(
    ctx: RunContext,
    findings: list[Finding],
    lanes: list[str],
    source: str,
    mutate_staged: Callable[[list[str]], None] | None = None,
) -> None:
    """Findings je Lane als Fix-Task in den regulären Lane-Loop geben —
    jeder Fix nimmt den validierten Pfad (Gates, Commit, kein Sonderweg).

    ``mutate_staged`` läuft unter demselben Lock und wird mit demselben Save
    persistiert wie das Staging — Zähler (z. B. fix_cycles) und gestagter
    Fix-Task sind damit atomar; ein Crash kann keinen Zyklus verbrennen,
    ohne dass der zugehörige Fix beim Resume nachgeholt wird."""
    decision = triage_final_review(
        ReviewResult(verdict="needs_fixes", findings=findings)
        if findings
        else ReviewResult(verdict="ok", findings=[]),
        active_lanes=lanes,
    )
    with ctx.state_lock:
        # Erst ALLE Lanes validieren, dann mutieren — escalate() persistiert
        # den State und darf keinen halb gestagten Zustand mitschreiben.
        for lane in decision.fix_tasks:
            if ctx.state.lanes.get(lane) is None:
                raise escalate(
                    ctx,
                    f"{source}-Finding für unbekannte Lane {lane!r} — "
                    f"aktive Lanes: {', '.join(lanes)}",
                )
        if mutate_staged is not None:
            mutate_staged(list(decision.fix_tasks))
        for lane, items in decision.fix_tasks.items():
            lane_state = ctx.state.lanes[lane]
            text = _findings_text(ReviewResult(verdict="needs_fixes", findings=items))
            # Lane geht zurück in den Loop: neuer Fix-Task, frisches
            # Iterations-Budget (das Limit gilt pro Task, nicht pro Run).
            lane_state.completed = False
            lane_state.gates_passed = False
            lane_state.gates_tree = None
            lane_state.pending_task = (
                f"{source}-Findings für deine Lane. Fixe die Ursachen (kein Workaround "
                f"an den Tests vorbei), TDD wo sinnvoll. Du committest nicht.\n\n{text}"
            )
            lane_state.last_failures = []
            lane_state.gate_iterations = 0
        ctx.save()  # EIN Save: Staging aller Lanes + mutate_staged atomar
    for lane in decision.fix_tasks:
        _run_lane(ctx, lane, lanes)


# --- Phase 5+6: Codex-Code-Review, finaler Review + Triage --------------------

MAX_REVIEW_ROUNDS = 10


def _resume_pending_lanes(ctx: RunContext, lanes: list[str]) -> None:
    """Vor Merge/Review JEDE Lane durch _run_lane schicken: Unfertige Lanes
    (Crash nach Fix-Dispatch) holen Gates + Commit nach; fertige Lanes laufen
    durch die Tree-Hash-Revalidierung und gehen bei Manipulation zurück in den
    Loop statt ungegatete Änderungen ins Review zu geben."""
    for lane in lanes:
        if ctx.state.lanes.get(lane) is not None:
            _run_lane(ctx, lane, lanes)


def _review_worktree(ctx: RunContext, lanes: list[str]) -> Path:
    """Worktree, gegen den Reviews laufen: parallel der Integrations-Branch
    NACH grünem E2E-Gate (jeder Review-Fix muss wieder durch die komplette
    Integrationsschleife), Single-Lane der Lane-Worktree — beides idempotent
    wiederherstellbar, ein Crash-Resume braucht keinen alten Zustand."""
    if ctx.state.parallel:
        return _integration_loop(ctx, lanes)
    return create_lane_worktree(ctx.repo, ctx.state.run_id, lanes[0], ctx.config.base_branch)


def _changed_files(ctx: RunContext, worktree: Path) -> list[str]:
    changed = _git(
        ctx, worktree, "diff", "--name-only", f"{ctx.config.base_branch}...HEAD"
    ).splitlines()
    return [name for name in changed if name]


def run_codex_review_phase(ctx: RunContext) -> None:
    """Phase 5: Codex reviewt den integrierten Diff; Findings werden per
    lane-Feld in die Build-Lanes geroutet, bis Verdict ok."""
    if ctx.state.phase != "codex_review":
        return
    lanes = _active_lanes(ctx)
    _resume_pending_lanes(ctx, lanes)
    previous: list[str] | None = ctx.state.review_last_failures or None
    while True:
        # Limit VOR dem Review: Crash zwischen Runden-Save und Eskalation darf
        # beim Resume keine weitere Runde starten.
        if ctx.state.review_rounds >= MAX_REVIEW_ROUNDS:
            raise escalate(
                ctx,
                f"Codex-Code-Review: {ctx.state.review_rounds} Runden erreicht "
                f"(Limit {MAX_REVIEW_ROUNDS}) — Eskalation",
            )
        worktree = _review_worktree(ctx, lanes)
        try:
            review = ctx.codex.review("code", _changed_files(ctx, worktree), cwd=worktree)
        except (FindingsParseError, ValidationError) as exc:
            raise escalate(ctx, f"Codex-Code-Review unlesbar: {exc}") from exc
        if review.verdict == "ok":
            break
        with ctx.state_lock:
            ctx.state.review_rounds += 1
            ctx.save()
        if ctx.state.review_rounds >= MAX_REVIEW_ROUNDS:
            # VOR dem Dispatch eskalieren: ein Fix, den nie wieder ein Review
            # prüfen kann, ist verschwendete (teure) Arbeit.
            raise escalate(
                ctx,
                f"Codex-Code-Review: {ctx.state.review_rounds} Runden erreicht "
                f"(Limit {MAX_REVIEW_ROUNDS}) — Eskalation.\n\n{_findings_text(review)}",
            )
        failures = _finding_keys(review)
        try:
            check_progress(previous, failures)
        except NoProgressError as exc:
            raise escalate(
                ctx,
                f"Codex-Code-Review meldet unverändert dieselben Findings — "
                f"Circuit-Breaker.\n\n{_findings_text(review)}",
            ) from exc
        _dispatch_lane_fixes(ctx, review.findings, lanes, source="Codex-Code-Review")
        with ctx.state_lock:
            # Erst NACH dem Fix-Dispatch fortschreiben — wie in Phase 4.
            ctx.state.review_last_failures = failures
            ctx.save()
        previous = failures
    with ctx.state_lock:
        ctx.state.review_last_failures = []
        ctx.state.phase = "final_review"
        ctx.save()


def run_final_review_phase(ctx: RunContext) -> None:
    """Phase 6: Finaler Reviewer (read-only) prüft gegen die Spec; Triage in
    Code: scope_gap → Follow-up-Report, Rest → Fix-Zyklus (max. 3 je Lane)."""
    if ctx.state.phase != "final_review":
        return
    lanes = _active_lanes(ctx)
    _resume_pending_lanes(ctx, lanes)
    previous: list[str] | None = ctx.state.final_review_last_failures or None
    while True:
        worktree = _review_worktree(ctx, lanes)
        spec = REGISTRY["final_reviewer"]
        task = (
            "Prüfe die Implementierung in diesem Worktree read-only gegen "
            ".adw/spec.md (Akzeptanzkriterien, Definition of Done) und "
            ".adw/contract.yaml. Antworte AUSSCHLIESSLICH mit dem Findings-JSON; "
            "setze bei jedem Finding das category-Feld "
            "(scope_gap | implementation | trivial)."
        )
        result = ctx.agents.run(spec, task, cwd=worktree, resume=None)
        try:
            review = extract_review_result(result.text)
        except (FindingsParseError, ValidationError) as exc:
            raise escalate(ctx, f"Finaler Review unlesbar: {exc}") from exc
        if review.verdict == "ok":
            break
        uncategorized = [f.file for f in review.findings if f.category is None]
        if uncategorized:
            # Ohne category ist keine Triage möglich — ein scope_gap würde
            # sonst still als Implementierungs-Fix durchgehen.
            raise escalate(
                ctx,
                f"Finaler Review ohne category-Feld bei: {', '.join(uncategorized)} — "
                f"Findings sind nicht triagierbar",
            )
        decision = triage_final_review(review, active_lanes=lanes)
        if decision.followups:
            _write_followups(ctx, decision.followups)
        if not decision.fix_tasks:
            break  # nur scope_gaps: Report statt Auto-Restart (SPEC §4 Phase 6)
        fixable = [f for f in review.findings if f.category != "scope_gap"]
        failures = _finding_keys(ReviewResult(verdict="needs_fixes", findings=fixable))
        try:
            check_progress(previous, failures)
        except NoProgressError as exc:
            raise escalate(
                ctx,
                f"Finaler Review meldet unverändert dieselben Findings — "
                f"Circuit-Breaker.\n\n{_findings_text(review)}",
            ) from exc

        def _bump_fix_cycles(fix_lanes: list[str]) -> None:
            # Läuft im Dispatch unter dem State-Lock und wird mit dem Staging
            # in EINEM Save persistiert — Zähler und Fix-Task sind atomar.
            for lane in fix_lanes:
                try:
                    check_fix_cycles(ctx.state.lanes[lane])
                except LimitExceededError as exc:
                    raise escalate(ctx, f"Lane {lane}: {exc}") from exc
            for lane in fix_lanes:
                ctx.state.lanes[lane].fix_cycles += 1

        _dispatch_lane_fixes(
            ctx,
            review.findings,
            lanes,
            source="Finaler-Review",
            mutate_staged=_bump_fix_cycles,
        )
        with ctx.state_lock:
            # Erst NACH dem Fix-Dispatch fortschreiben — wie in Phase 4/5.
            ctx.state.final_review_last_failures = failures
            ctx.save()
        previous = failures
    with ctx.state_lock:
        ctx.state.final_review_last_failures = []
        ctx.state.phase = "ci"
        ctx.save()


def _write_followups(ctx: RunContext, followups: list[Finding]) -> None:
    """scope_gap-Findings als Follow-up-Report sammeln (kein Auto-Restart).

    Dedupe über file+issue: ein bewusst nicht gefixtes scope_gap taucht in
    jeder weiteren Review-Runde erneut auf und darf den Report nicht fluten."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.run_dir / "followups.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    entries = []
    for item in followups:
        if f"{item.file}: {item.issue}" in existing:
            continue
        plan = "; ".join(item.remediation_plan)
        entries.append(f"- [{item.severity}] {item.file}: {item.issue}\n  - Plan: {plan}")
    if not entries:
        return
    header = "" if existing else f"# Follow-ups — Run {ctx.state.run_id}\n\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(header + "\n".join(entries) + "\n")


def _gate_failure_text(report: GateReport) -> str:
    lines = []
    for failure in report.failures:
        status = "Timeout" if failure.timed_out else f"Exit {failure.exit_code}"
        lines.append(f"### Gate {failure.gate} ({status})\n{failure.output}")
    return "\n\n".join(lines)
