"""Phases 1–2: spec and plan — dual-author drafts, synthesis, the reviewed
authoring loop with its round cap, approval gates and artifact archival."""

import contextlib
import os
import secrets
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from adw.agents import REGISTRY, AgentSpec
from adw.codex import CodexAuthorError, CodexError
from adw.context import (
    ARTIFACTS,
    ISSUE_FILE,
    PLAN_SUMMARY,
    SPEC_SUMMARY,
    SUMMARIES,
    AwaitingApproval,
    RunContext,
    WorktreeRefusedError,
    _active_lanes,
    _agent_run,
    _artifact_bytes,
    _checkpoint_authoring_session,
    _codex_review,
    _emit_artifact,
    _git,
    _log_warning,
    _repo_relative,
    _require_progress,
    _reset_checkout_artifact,
    _restore_all,
    _validate_lanes,
    escalate,
)
from adw.findings import (
    FindingsParseError,
    ReviewResult,
)
from adw.review_policy import (
    _actionable_severities,
    _below_threshold_disposition,
    _dispatched_disposition,
    _disposition_line,
    _extend_context,
    _finding_keys,
    _findings_text,
    _severity_context,
    _split_by_severity,
    _write_followups,
)
from adw.worktrees import (
    ensure_runs_gitignored,
)

# Runden-Cap des Authoring-Loops: eine Runde = ein Agent-Lauf + ein
# Codex-Review. Nach so vielen erfolglosen Runden wird abgebrochen (P1 offen)
# bzw. das Artefakt mit dokumentierten Known Limitations akzeptiert (nur P2/P3).
AUTHORING_MAX_ROUNDS = 5


def run_spec_and_plan(ctx: RunContext) -> None:
    """Phases 1–2: spec and plan agent with Codex review loops + approval Gate."""
    # Ignore-Regel VOR dem ersten State-/Artefakt-Write — der Haupt-Checkout
    # bleibt auch während der Approval-Pause sauber.
    ensure_runs_gitignored(ctx.repo)
    _validate_lanes(ctx)
    skip = ctx.state.skip_approval
    spec_approval = ctx.state.spec_approval
    # Die Ziel-Repo-Config ist für Agents tabu — Write(.adw/**) würde sie
    # technisch erlauben, deshalb Snapshot + Restore um die Authoring-Loops.
    config_path = ctx.repo / ".adw" / "config.yaml"
    config_snapshot = config_path.read_bytes() if config_path.is_file() else None
    if ctx.state.phase == "awaiting_approval":
        if ctx.state.pending_breakpoint is not None:
            # An einem Breakpoint wartend (NICHT am Plan-Gate): approval_granted
            # ist hier bereits True (der Plan wurde früher freigegeben) — deshalb
            # NICHT die Plan-Gate-Deutung ausführen und NICHT nach build übergehen.
            # Der Lauf bleibt wartend, bis `adw approve` den Breakpoint freigibt
            # und die Phase über die Grenze fortschaltet (B6/AC9). Das erneute
            # awaited-Event entscheidet log-geprüft cli._execute (B8/AC12).
            raise AwaitingApproval(ctx.state.run_id)
        if ctx.state.approval_granted or skip:
            ctx.state.phase = "build"
            ctx.save()
            return
        ctx.save()
        raise AwaitingApproval(ctx.state.run_id)
    if ctx.state.phase == "awaiting_spec_approval":
        # Archivierung idempotent nachholen: crasht ein Run zwischen dem
        # awaiting_spec_approval-Save und dem _archive_artifacts, lägen die
        # generierten Artefakte (spec.md, issue.md) sonst die ganze Pause über
        # dirty im Checkout. Cleanup-Modus: die bereits archivierte (reviewte)
        # Spec NICHT mit einer alten getrackten Checkout-Version überschreiben.
        _archive_artifacts(ctx, overwrite_existing_archive=False)
        if not ctx.state.spec_approval_granted:
            ctx.save()
            raise AwaitingApproval(ctx.state.run_id)
        # Approval erteilt: in die Plan-Phase übergehen und normal fortfahren.
        ctx.state.phase = "plan"
        ctx.save()
    if ctx.state.phase not in ("spec", "plan"):
        return  # Resume in einer späteren Phase — hier nichts zu tun

    # protected: nach JEDEM Agent-Lauf (vor dem Review) und exception-sicher
    # am Ende restauriert — Agents dürfen diese Dateien nie effektiv ändern.
    protected: dict[Path, bytes | None] = {config_path: config_snapshot}
    paused_for_spec_approval = False
    try:
        # Fail fast (in Phase spec UND plan): uncommittete Nutzer-Edits an
        # getrackten Artefakten würde die Archivierung (git checkout --)
        # verwerfen. Ausnahme: die vom ADW selbst restaurierte Spec (siehe
        # Plan-Resume) ist kein Nutzer-Edit.
        for name in (*ARTIFACTS, *SUMMARIES):
            if ctx.state.phase == "plan" and (ctx.run_dir / name).is_file():
                continue  # gehört diesem Run, wird gleich ohnehin überschrieben
            if (
                ctx.state.phase == "plan"
                and ctx.state.authoring_session
                and name in ("plan.md", "contract.yaml", PLAN_SUMMARY)
            ):
                continue  # eigener, gecrashter Plan-Loop — kein User-Edit
            if not _git(ctx, ctx.repo, "ls-files", "--", f".adw/{name}").strip():
                continue
            if _git(ctx, ctx.repo, "status", "--porcelain", "--", f".adw/{name}").strip():
                # Letzte Instanz, falls der Checkout zwischen CLI-Vorflug und
                # Phasenlauf dirty wird: VERWEIGERN statt eskalieren (B1) — der
                # Run bleibt unverändert und resumierbar, nichts wird verworfen.
                raise WorktreeRefusedError(
                    f".adw/{name} ist getrackt und hat uncommittete Änderungen — "
                    f"bitte committen oder stashen (ADW verwirft sie nicht und "
                    f"eskaliert nicht)"
                )
        # Issue-Text als reviewbares Artefakt (B3) — idempotent, damit ein
        # Resume in der Spec- ODER Plan-Phase es wieder herstellt; protected,
        # damit ein Agent es nicht effektiv verändern kann (wie config.yaml).
        protected[ctx.repo / ".adw" / ISSUE_FILE] = _write_issue(ctx)
        if ctx.state.phase == "spec":
            with ctx.emitter.span("phase", {"name": "spec", "from_phase": "spec"}) as _spec_phase:
                _spec_phase.end_payload = {"name": "spec", "to_phase": None}
                draft_task = _spec_draft_task(ctx)
                drafts = _draft_stage(
                    ctx,
                    kind="spec",
                    agent_name="spec_agent",
                    task=draft_task,
                    artifacts=("spec.md",),
                    protected=protected,
                    summary=SPEC_SUMMARY,
                )
                _reviewed_authoring_loop(
                    ctx,
                    agent_name="spec_synthesis",
                    initial_task=_synthesis_task(
                        drafts, artifacts=("spec.md",), summary=SPEC_SUMMARY, goal=draft_task
                    ),
                    review_kind="spec",
                    artifacts=("spec.md",),
                    summary=SPEC_SUMMARY,
                    # Issue mitgeben: Codex prüft die Spec auf Proportionalität
                    # gegen die tatsächliche Anforderung.
                    review_refs=(ISSUE_FILE, "spec.md"),
                    protected=protected,
                )
                with ctx.state_lock:
                    # Reviewte Spec SOFORT ins Run-Verzeichnis sichern: markiert sie
                    # als eigenen Output (Guard-Ausnahme beim Resume) und schützt
                    # sie vor dem Crash-Fenster bis zur Archivierung. Die Summary
                    # gehört zum Gate-Ergebnis und wird genauso gesichert.
                    ctx.run_dir.mkdir(parents=True, exist_ok=True)
                    for name in ("spec.md", SPEC_SUMMARY):
                        shutil.copy2(ctx.repo / ".adw" / name, ctx.run_dir / name)
                    # Spec-Gate: Phase + geleerter Checkpoint atomar. Ein Crash im
                    # Fenster darf nie phase="plan" hinterlassen, sonst würde der
                    # Stopp übersprungen (Runden-Zähler startet für den Plan bei 0).
                    if spec_approval and not ctx.state.spec_approval_granted:
                        ctx.state.phase = "awaiting_spec_approval"
                        paused_for_spec_approval = True
                    else:
                        ctx.state.phase = "plan"
                    ctx.save()  # Phase + geleerter Authoring-Checkpoint in EINEM Save
                _spec_phase.end_payload["to_phase"] = ctx.state.phase

        if not paused_for_spec_approval:
            with ctx.emitter.span("phase", {"name": "plan", "from_phase": "plan"}) as _plan_phase:
                _plan_phase.end_payload = {"name": "plan", "to_phase": None}
                # Phase "plan" — frisch erreicht ODER Resume nach Crash in der Plan-Phase.
                # Spec UND Spec-Summary sind das Ergebnis der Vorphase: sie werden
                # wiederhergestellt und sind ab hier fix — die Plan-Phase darf sie
                # lesen, aber nicht umschreiben (Write(.adw/**) würde es erlauben).
                for name in ("spec.md", SPEC_SUMMARY):
                    path = ctx.repo / ".adw" / name
                    archived = ctx.run_dir / name
                    if archived.is_file():
                        # Die ARCHIVIERTE (reviewte) Fassung hat immer Vorrang: Ein
                        # Crash mitten in der Archivierung kann die Checkout-Datei
                        # bereits auf eine alte getrackte Version zurückgesetzt haben.
                        shutil.copy2(archived, path)
                    elif not path.is_file():
                        raise escalate(
                            ctx,
                            f"Resume in Phase 'plan', aber .adw/{name} fehlt — "
                            "Spec-Ergebnis verloren, bitte Run neu starten",
                        )
                    protected[path] = path.read_bytes()

                draft_task = _plan_draft_task(ctx)
                plan_artifacts = ("plan.md", "contract.yaml")
                drafts = _draft_stage(
                    ctx,
                    kind="plan",
                    agent_name="plan_agent",
                    task=draft_task,
                    artifacts=plan_artifacts,
                    protected=protected,
                    summary=PLAN_SUMMARY,
                )
                _reviewed_authoring_loop(
                    ctx,
                    agent_name="plan_synthesis",
                    initial_task=_synthesis_task(
                        drafts, artifacts=plan_artifacts, summary=PLAN_SUMMARY, goal=draft_task
                    ),
                    review_kind="plan",
                    artifacts=plan_artifacts,
                    summary=PLAN_SUMMARY,
                    # Issue + Spec mitgeben: Codex prüft Plan/Kontrakt GEGEN die
                    # Akzeptanzkriterien und die tatsächliche Anforderung.
                    review_refs=(ISSUE_FILE, "spec.md", "plan.md", "contract.yaml"),
                    protected=protected,
                )
                # Archival + the plan→next transition happen INSIDE the plan
                # span, so its to_phase records the phase that was ACTUALLY
                # reached and stays null if archival/persistence/approval raises.
                # _restore_all is idempotent (also runs in the finally below).
                _restore_all(protected)
                _archive_artifacts(ctx)
                # Archival already removed the ADW artifacts from the checkout;
                # drop them from the restore set so the finally's safety-net
                # restore does not recreate them (it keeps handling config.yaml).
                for _name in (*ARTIFACTS, ISSUE_FILE, *SUMMARIES):
                    protected.pop(ctx.repo / ".adw" / _name, None)
                ctx.state.phase = "awaiting_approval"
                ctx.save()  # Phase + geleerter Authoring-Checkpoint in EINEM Save
                if skip or ctx.state.approval_granted:
                    ctx.state.phase = "build"
                    ctx.save()
                # Only NOW, after the transition succeeded, is the resulting
                # phase known.
                _plan_phase.end_payload["to_phase"] = ctx.state.phase
                if not (skip or ctx.state.approval_granted):
                    raise AwaitingApproval(ctx.state.run_id)
    finally:
        _restore_all(protected)
    # Spec-gate pause path: the plan span never opened. Archive the generated
    # artifacts (issue.md, spec.md) so they do not sit dirty in the checkout
    # during the approval pause, then signal the pause.
    if paused_for_spec_approval:
        _archive_artifacts(ctx)
        raise AwaitingApproval(ctx.state.run_id)
    # Non-paused path: archival + transition already happened inside the plan
    # span (build path falls through here; awaiting_approval raised above).
    return


# --- Draft-Stage: zwei unabhängige Entwürfe je Authoring-Phase ---------------

DRAFTS_DIR = "drafts"


@dataclass(frozen=True)
class DraftSet:
    """Draft paths of one authoring phase, relative to the repo root.

    ``codex`` is empty when the Codex draft failed — the synthesis then works
    from a single source. The Claude draft never degrades: it escalates."""

    claude: tuple[str, ...]
    codex: tuple[str, ...]


def _draft_name(artifact: str, author: str) -> str:
    """spec.md + claude -> spec.claude.md"""
    path = Path(artifact)
    return f"{path.stem}.{author}{path.suffix}"


def _write_draft(target: Path, data: bytes) -> None:
    """Publish a draft atomically (tmp + rename).

    A crash mid-write must never leave a truncated draft behind — the resume
    would take it for a finished one and skip its author."""
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _draft_present(path: Path) -> bool:
    """A draft counts only with content — an empty file is no synthesis source."""
    return path.is_file() and bool(path.read_bytes().strip())


def _draft_stage(
    ctx: RunContext,
    kind: str,
    agent_name: str,
    task: str,
    artifacts: tuple[str, ...],
    protected: dict[Path, bytes | None] | None = None,
    summary: str | None = None,
) -> DraftSet:
    """Two independent drafts for one authoring phase, written in PARALLEL.

    Claude (agent, writes into the checkout, the orchestrator copies the result
    into the run folder) and Codex (read-only, returns the content) run at the
    same time. Idempotent over FILES, not over state: an existing draft skips
    its author, a `<kind>.codex.FAILED` marker skips the failed Codex attempt
    for good.

    ``summary`` is not drafted — it is only cleaned out of the checkout with
    the artifacts, so no stale summary can pose as the one of this run."""
    drafts = ctx.run_dir / DRAFTS_DIR
    drafts.mkdir(parents=True, exist_ok=True)
    claude_paths = [drafts / _draft_name(name, "claude") for name in artifacts]
    codex_paths = [drafts / _draft_name(name, "codex") for name in artifacts]
    marker = drafts / f"{kind}.codex.FAILED"
    # Crash-Reste dieser Phase im Hauptthread wegräumen, BEVOR ein Autor läuft:
    # ein Rest darf weder als frischer Entwurf durchgehen noch dem parallel
    # lesenden Codex als Zwischenstand erscheinen. Die Summary gehört dazu —
    # sonst ginge eine fremde/alte Zusammenfassung als die dieses Runs durch.
    # AUSNAHME: Ist der Authoring-Loop bereits gecheckpointet, stammen die
    # Dateien im Checkout von der Synthese — das ist die reviewte
    # Zwischenfassung, an der die Fix-Runde weiterarbeitet.
    if ctx.state.authoring_session is None:
        for name in (*artifacts, summary) if summary else artifacts:
            _reset_checkout_artifact(ctx, name)
    prior = {name: _artifact_bytes(ctx, name) for name in artifacts}
    # Vollständigkeit je Autor: ein Crash zwischen zwei Draft-Writes (plan.md
    # geschrieben, contract.yaml nicht) lässt den Autor erneut laufen.
    need_claude = not all(_draft_present(path) for path in claude_paths)
    need_codex = not all(_draft_present(path) for path in codex_paths) and not marker.is_file()
    # Captured on the MAIN thread (inside the phase span): the draft authors run
    # in pool workers whose span stack is empty, so any point they emit (log on
    # Codex degradation, escalation on a missing Claude draft) must be given
    # this enclosing span explicitly to stay non-null (GUI-SPEC §4.2).
    span_id = ctx.emitter.current_span_id()
    with ThreadPoolExecutor(max_workers=2) as pool:
        codex_future = (
            pool.submit(_codex_draft, ctx, kind, task, artifacts, codex_paths, marker, span_id)
            if need_codex
            else None
        )
        claude_future = (
            pool.submit(
                _claude_draft,
                ctx, agent_name, task, artifacts, claude_paths, prior, protected, span_id,
            )
            if need_claude
            else None
        )
        if codex_future:
            codex_future.result()  # degradiert selbst, wirft nur Unerwartetes
        if claude_future:
            claude_future.result()  # Eskalation propagieren
    # Draft artifacts individually (B4). Emitted HERE on the main thread — the
    # authors ran in pool workers whose span stack is empty, so an emit inside
    # them would carry a null span.
    for path in (*claude_paths, *codex_paths):
        if _draft_present(path):
            _emit_artifact(ctx, path)
    return DraftSet(
        claude=tuple(_repo_relative(ctx, path) for path in claude_paths),
        # Fehlender Codex-Entwurf (degradiert oder Marker aus einem Vorlauf):
        # die Synthese darf keinen Pfad genannt bekommen, den es nicht gibt.
        codex=(
            tuple(_repo_relative(ctx, path) for path in codex_paths)
            if all(_draft_present(path) for path in codex_paths)
            else ()
        ),
    )


def _claude_draft(
    ctx: RunContext,
    agent_name: str,
    task: str,
    artifacts: tuple[str, ...],
    targets: list[Path],
    prior: dict[str, bytes | None],
    protected: dict[Path, bytes | None] | None,
    span_id: str | None = None,
) -> None:
    """Claude draft: the agent writes into the checkout, the orchestrator copies
    the result into the run folder and cleans the checkout again.

    ``prior`` is the checkout snapshot taken by the caller BEFORE both authors
    started — an idle agent run must not pass off leftovers as a draft."""
    _agent_run(ctx, REGISTRY[agent_name], task, cwd=ctx.repo)
    if protected:
        _restore_all(protected)
    # Leer wie fehlend behandeln: ein weißraum-Artefakt ist keine Entwurfsquelle,
    # die Synthese hätte daraus nichts zu mergen.
    missing = _missing_loop_artifacts(ctx, artifacts)
    if missing:
        raise escalate(
            ctx,
            f"{agent_name} hat {', '.join(f'.adw/{m}' for m in missing)} nicht "
            "erzeugt (oder leer gelassen) — ohne Entwurf gibt es nichts zu synthetisieren",
            span_id=span_id,
        )
    # Getrackter Altbestand (gemergter Vorlauf) überlebt das Aufräumen — er darf
    # einen untätigen Agent-Lauf nicht als Entwurf adeln.
    unchanged = _unchanged_artifacts(ctx, artifacts, prior)
    if unchanged:
        raise escalate(
            ctx,
            f"{agent_name} hat {', '.join(f'.adw/{u}' for u in unchanged)} nicht "
            "verändert — Altbestand eines früheren Runs würde sonst als Entwurf "
            "durchgehen",
            span_id=span_id,
        )
    for name, target in zip(artifacts, targets, strict=True):
        _write_draft(target, (ctx.repo / ".adw" / name).read_bytes())
    # Der Entwurf lebt ab jetzt im Run-Ordner; die Synthese schreibt das
    # Artefakt selbst — der Checkout darf keinen Entwurf behalten.
    for name in artifacts:
        _reset_checkout_artifact(ctx, name)


def _codex_draft(
    ctx: RunContext,
    kind: str,
    task: str,
    artifacts: tuple[str, ...],
    targets: list[Path],
    marker: Path,
    span_id: str | None = None,
) -> None:
    """Codex draft — degrades instead of escalating.

    Codex is the SECOND opinion: without it the synthesis still has the Claude
    draft. A failure is therefore logged and marked; the marker keeps a resume
    from burning another Codex run on the same broken input."""
    try:
        # codex.author span at the call site (GUI-SPEC §4.4, E4): the argv is
        # built (with the per-call marker_id) and complete BEFORE the span opens;
        # the runner fills artifacts/raw_stdout/parse_ok into the handle. Opened
        # in this pool worker, its span stack is empty, so parent stays null (the
        # documented orphan case, repaired on read by the model).
        marker_id = secrets.token_hex(8)
        author_argv = ctx.codex.effective_author_argv(kind, task, ctx.repo, marker_id)
        start_payload = {
            "kind": kind, "argv": author_argv, "cwd": str(ctx.repo), "task": task,
        }
        with ctx.emitter.span("codex.author", start_payload) as handle:
            # Deterministic defaults so a failed/empty draft still writes a
            # complete end record (the runner overwrites them on success).
            handle.end_payload = {"artifacts": [], "raw_stdout": "", "parse_ok": False}
            files = ctx.codex.author(
                kind, task, cwd=ctx.repo, marker_id=marker_id,
                emitter=ctx.emitter, span=handle,
            )
            empty = [name for name in artifacts if not files.get(name, "").strip()]
            if empty:
                raise CodexAuthorError(f"Codex-Entwurf ohne Inhalt für {', '.join(empty)}")
    except CodexError as exc:
        marker.write_text(
            f"# Codex-Entwurf ({kind}) fehlgeschlagen — Run {ctx.state.run_id}\n\n{exc}\n",
            encoding="utf-8",
        )
        _log_warning(
            ctx,
            f"Codex-Entwurf ({kind}) fehlgeschlagen — die Synthese arbeitet "
            f"einquellig: {exc}",
            span_id=span_id,
        )
        return
    for name, target in zip(artifacts, targets, strict=True):
        _write_draft(target, files[name].encode())


def _spec_draft_task(ctx: RunContext) -> str:
    return (
        f"Create the specification for this issue following the fixed template "
        f"(goal, scope, non-goals, acceptance criteria, Definition of Done) as "
        f".adw/spec.md.\n\nIssue:\n{ctx.state.issue}"
    )


def _plan_draft_task(ctx: RunContext) -> str:
    lanes = ", ".join(_active_lanes(ctx))
    return (
        f"From .adw/spec.md, create the implementation plan .adw/plan.md with "
        f"the Workstreams ({lanes}) and the interface contract .adw/contract.yaml."
    )


def _synthesis_task(drafts: DraftSet, artifacts: tuple[str, ...], summary: str, goal: str) -> str:
    """Task of the synthesis agent — the FIRST run of the authoring loop.

    It merges both drafts into the best-of artifact and writes the summary the
    human reads at the approval gate."""
    targets = ", ".join(f".adw/{name}" for name in artifacts)
    lines = [
        goal,
        f"Read .adw/{ISSUE_FILE} and the independent drafts listed below, then "
        f"write the best-of merge to {targets} and the summary .adw/{summary}.",
        f"Claude draft: {', '.join(drafts.claude)}",
    ]
    if drafts.codex:
        lines.append(f"Codex draft: {', '.join(drafts.codex)}")
    else:
        lines.append(
            "There is no Codex draft — its author failed. Work from the Claude "
            "draft alone and state that single-source basis in the summary."
        )
    return "\n\n".join(lines)


def _missing_loop_artifacts(ctx: RunContext, loop_artifacts: tuple[str, ...]) -> list[str]:
    """Required artifacts that are absent or empty (whitespace counts as empty)."""
    return [name for name in loop_artifacts if not (_artifact_bytes(ctx, name) or b"").strip()]


def _unchanged_artifacts(
    ctx: RunContext, names: tuple[str, ...], prior: dict[str, bytes | None]
) -> list[str]:
    """Artifacts that existed before the agent run and are byte-identical now —
    leftovers an idle agent run must not pass off as its own work."""
    return [
        name
        for name in names
        if prior[name] is not None and _artifact_bytes(ctx, name) == prior[name]
    ]


def _authoring_step(
    ctx: RunContext,
    spec: AgentSpec,
    task: str,
    session: str | None,
    protected: dict[Path, bytes | None] | None,
) -> str | None:
    """One agent run of the authoring loop: run over the session, checkpoint the
    (possibly new) session id, restore the protected files. Returns the session."""
    result = _agent_run(
        ctx, spec, task, cwd=ctx.repo, resume=session,
        on_session_id=lambda sid: _checkpoint_authoring_session(ctx, sid),
    )
    session = result.session_id or session
    with ctx.state_lock:
        ctx.state.authoring_session = session
        ctx.save()
    if protected:
        _restore_all(protected)
    return session


def _accept_with_known_findings(
    ctx: RunContext, review_kind: str, review: ReviewResult, report_reason: str, log_reason: str
) -> None:
    """Terminal 'accept' exit of the authoring loop: document the remaining findings
    as Known Limitations, warn, clear the checkpoint. Deliberately no save — the
    caller clears the checkpoint atomically with the phase transition."""
    known_path = _write_known_findings(
        ctx, review_kind, review, f"{report_reason} — Artefakt akzeptiert."
    )
    _log_warning(
        ctx,
        f"Authoring-{review_kind}: {log_reason} — Artefakt akzeptiert, Known Limitations "
        f"unter {known_path}",
    )
    _clear_authoring_checkpoint(ctx)


def _reviewed_authoring_loop(
    ctx: RunContext,
    agent_name: str,
    initial_task: str,
    review_kind: str,
    artifacts: tuple[str, ...],
    review_refs: tuple[str, ...] | None = None,
    protected: dict[Path, bytes | None] | None = None,
    summary: str | None = None,
) -> None:
    """Agent writes artifact(s), Codex reviews, Findings go back to the SAME
    session — until the verdict is ok. Circuit-Breaker on identical Findings.
    ``protected`` is restored after EVERY agent run — even the reviewer
    never sees a protected file rewritten by the agent.

    ``summary`` is the phase summary: it must exist like an artifact and the fix
    tasks keep it current, but it is never a Codex review reference."""
    spec = REGISTRY[agent_name]
    review_targets = review_refs or artifacts
    # Die Summary muss wie ein Artefakt entstehen; für Fix-Task und Review
    # bleibt sie getrennt (Codex hat sie nicht reviewt).
    loop_artifacts = (*artifacts, summary) if summary else artifacts
    # Resume mitten im Authoring: Session, offener Fix-Task und Findings-Basis
    # aus dem State — sonst startet der Loop kontextlos neu und der
    # Prior-Content-Check würde das vorhandene Artefakt fälschlich eskalieren.
    resuming = ctx.state.authoring_session is not None
    session: str | None = ctx.state.authoring_session
    task = ctx.state.authoring_pending_task or initial_task
    previous_failures: list[str] | None = ctx.state.authoring_last_findings or None
    # Runden-Zähler aus dem State (überlebt Crash+Resume): eine Runde =
    # ein Agent-Lauf + ein Codex-Review, das mit Verdict ≠ ok endet.
    rounds = ctx.state.authoring_rounds
    # Findings-Verlauf der Vorrunden inkl. Disposition (Review-Policy v2).
    prior_context = list(ctx.state.authoring_prior_context)
    # Prior-Snapshot: Altbestand (z. B. gemergte Artefakte früherer Runs) darf
    # einen untätigen Agenten nicht adeln — das Artefakt muss sich ÄNDERN.
    prior = {name: _artifact_bytes(ctx, name) for name in loop_artifacts}
    first_iteration = not resuming
    # Report der Known Limitations: nur der Cap-Accept-Pfad schreibt ihn; jeder
    # andere terminale Ausgang (ok, Cap-Eskalation) entfernt einen etwaigen
    # STALE Report — z. B. ein Crash nach Cap-Accept, dessen Resume erneut die
    # letzte Runde fährt und "ok" bzw. andere Findings erhält.
    known_findings_path = ctx.run_dir / f"authoring-{review_kind}-known-findings.md"
    while True:
        with ctx.emitter.span(
            "round", {"loop": "authoring", "n": rounds + 1, "cap": AUTHORING_MAX_ROUNDS}
        ) as _round:
            _round.end_payload = {"outcome": "aborted"}
            session = _authoring_step(ctx, spec, task, session, protected)
            # Leer zählt wie fehlend: Die Summary reviewt niemand, eine weiße
            # Summary fiele sonst erst dem Menschen am Freigabe-Gate auf. Fehlt ein
            # Pflicht-Artefakt, wird GENAU EINMAL derselbe Schritt über die
            # vorhandene Session repariert (kein Review-Zyklus, keine Runde, E4).
            missing = _missing_loop_artifacts(ctx, loop_artifacts)
            if missing:
                repair_task = (
                    f"The synthesis run did not produce the required artifact(s) "
                    f"(missing or empty): {', '.join(f'.adw/{m}' for m in missing)}. "
                    f"Write exactly these files now over the same session and leave "
                    f"the already correct files unchanged."
                )
                session = _authoring_step(ctx, spec, repair_task, session, protected)
                missing = _missing_loop_artifacts(ctx, loop_artifacts)
            if missing:
                raise escalate(
                    ctx,
                    f"{agent_name} hat {', '.join(f'.adw/{m}' for m in missing)} "
                    "nicht erzeugt (oder leer gelassen) — Lauf abgebrochen statt mit "
                    "leerem Artefakt weiterzumachen",
                )
            if first_iteration:
                # Auch die Summary muss frisch sein: Reste eines fremden oder
                # gecrashten Laufs hat die Draft-Stage vorher entfernt, ein
                # unveränderter Altbestand wäre also eine untergeschobene
                # Zusammenfassung — und die reviewt niemand.
                unchanged = _unchanged_artifacts(ctx, loop_artifacts, prior)
                if unchanged:
                    raise escalate(
                        ctx,
                        f"{agent_name} hat {', '.join(f'.adw/{u}' for u in unchanged)} "
                        "nicht verändert — Altbestand eines früheren Runs würde sonst "
                        "als neues Artefakt durchgehen",
                    )
                first_iteration = False
            round_no = rounds + 1
            try:
                review = _codex_review(
                    ctx,
                    review_kind,
                    [f".adw/{name}" for name in review_targets],
                    cwd=ctx.repo,
                    context=_severity_context(round_no, AUTHORING_MAX_ROUNDS, prior_context),
                )
            except (FindingsParseError, ValidationError) as exc:
                raise escalate(ctx, f"Codex-{review_kind}-Review unlesbar: {exc}") from exc
            _round.end_payload["outcome"] = review.verdict
            if review.verdict == "ok":
                known_findings_path.unlink(missing_ok=True)  # kein stale Report
                # Bewusst KEIN Save hier: Das Leeren des Authoring-Checkpoints
                # passiert atomar mit dem Phasenübergang beim Aufrufer — sonst
                # gäbe es ein Crash-Fenster "Phase alt, Checkpoint weg".
                _clear_authoring_checkpoint(ctx)
                return
            # Eine Runde (Agent-Lauf + Review) endete mit Verdict ≠ ok.
            rounds += 1
            if rounds >= AUTHORING_MAX_ROUNDS:
                if any(f.severity == "P1" for f in review.findings):
                    known_findings_path.unlink(missing_ok=True)  # kein stale Report
                    raise escalate(
                        ctx,
                        f"Authoring-{review_kind}: Runden-Cap ({AUTHORING_MAX_ROUNDS}) "
                        f"erreicht, P1-Findings weiterhin offen — Eskalation an den "
                        f"Menschen.\n\n{_findings_text(review)}",
                    )
                # Nur P2/P3 offen: Artefakt akzeptieren, verbleibende Findings als
                # Known Limitations dokumentieren und wie ein ok-Verdict beenden.
                cap = f"Runden-Cap ({AUTHORING_MAX_ROUNDS}) erreicht, nur P2/P3 offen"
                _accept_with_known_findings(ctx, review_kind, review, cap, cap)
                return
            actionable, dropped = _split_by_severity(review.findings, round_no)
            if not actionable:
                # Alle Findings liegen unter der Schwelle dieser Runde: wie am Cap
                # das Artefakt akzeptieren und die Findings dokumentieren.
                severities = "/".join(sorted(_actionable_severities(round_no)))
                below = f"alle Findings unter der Severity-Schwelle ({severities})"
                _accept_with_known_findings(
                    ctx, review_kind, review, f"Runde {round_no}: {below}",
                    f"Runde {round_no}, {below}",
                )
                return
            if dropped:
                _write_followups(ctx, dropped)
            review = ReviewResult(verdict="needs_fixes", findings=actionable)
            failures = _finding_keys(review)
            _require_progress(
                ctx, previous_failures, failures, f"authoring:{review_kind}",
                f"Codex-{review_kind}-Review meldet unverändert dieselben Findings — "
                f"Circuit-Breaker.\n\n{_findings_text(review)}",
            )
            previous_failures = failures
            prior_context = _extend_context(
                prior_context,
                [
                    _disposition_line(item, _below_threshold_disposition(round_no))
                    for item in dropped
                ]
                + [
                    _disposition_line(item, _dispatched_disposition(round_no))
                    for item in actionable
                ],
            )
            task = (
                f"The Codex review of {', '.join(f'.adw/{a}' for a in artifacts)} has "
                f"Findings. Incorporate them and update the artifacts:\n\n"
                f"{_findings_text(review)}"
            )
            if summary:
                task += (
                    f"\n\nKeep .adw/{summary} current: when the fixes change the "
                    f"picture, update the summary accordingly."
                )
            with ctx.state_lock:
                # Checkpoint des Fix-Zyklus — überlebt einen Crash vor dem Fix-Lauf.
                # authoring_rounds und der Findings-Verlauf gehen im SELBEN Save wie
                # der Fix-Task raus.
                ctx.state.authoring_pending_task = task
                ctx.state.authoring_last_findings = failures
                ctx.state.authoring_rounds = rounds
                ctx.state.authoring_prior_context = prior_context
                ctx.save()


def _write_issue(ctx: RunContext) -> bytes:
    """Write the original issue text as .adw/issue.md so Codex can review the
    spec/plan against the actual request (proportionality). Idempotent: it is
    regenerated from the state, so a resume in any authoring phase restores it.

    The path belongs to the ADW (it is derived deterministically from the run
    state). A pre-existing file with FOREIGN content is therefore never
    overwritten — it would later be deleted/reset by the archival, silently
    losing user data — but escalated instead. Our own regenerated content
    (identical bytes, e.g. after a crash+resume) is a no-op overwrite."""
    path = ctx.repo / ".adw" / ISSUE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        f"# Issue (ADW-generiert aus dem Run-State — nicht manuell editieren)\n\n"
        f"{ctx.state.issue}\n"
    ).encode()
    if path.is_symlink():
        # Ein Symlink an diesem Pfad ist NIE unser Artefakt (wir schreiben immer
        # eine reguläre Datei) — also fremd. Nicht folgen (Referenz-Korruption)
        # und nicht still entfernen (die Archivierung würde den Ersatz löschen
        # und den Symlink verlieren), sondern eskalieren.
        raise escalate(
            ctx,
            f".adw/{ISSUE_FILE} ist ein Symlink — dieser Pfad gehört dem ADW (wird "
            f"aus dem Issue-Text generiert); bitte entfernen oder umbenennen",
        )
    elif path.is_dir():
        raise escalate(
            ctx,
            f".adw/{ISSUE_FILE} ist ein Verzeichnis — dieser Pfad gehört dem ADW "
            f"(wird aus dem Issue-Text generiert); bitte entfernen",
        )
    elif path.is_file() and path.read_bytes() != data:
        raise escalate(
            ctx,
            f".adw/{ISSUE_FILE} existiert bereits mit fremdem Inhalt — dieser Pfad "
            f"gehört dem ADW (wird aus dem Issue-Text generiert); bitte entfernen "
            f"oder umbenennen, der ADW würde ihn sonst überschreiben und verwerfen",
        )
    path.write_bytes(data)
    return data


def _clear_authoring_checkpoint(ctx: RunContext) -> None:
    """Clear in-memory only — persisting happens atomically with the phase transition."""
    ctx.state.authoring_session = None
    ctx.state.authoring_pending_task = None
    ctx.state.authoring_last_findings = []
    ctx.state.authoring_rounds = 0
    ctx.state.authoring_prior_context = []


def _write_known_findings(
    ctx: RunContext, review_kind: str, review: ReviewResult, reason: str
) -> Path:
    """Persist the remaining findings as Known Limitations when the authoring
    loop accepts the artifact anyway (round cap or severity threshold) — the
    open findings stay documented."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.run_dir / f"authoring-{review_kind}-known-findings.md"
    path.write_text(
        f"# Known Limitations — Authoring-{review_kind} (Run {ctx.state.run_id})\n\n"
        f"{reason}\n\n"
        f"{_findings_text(review)}\n",
        encoding="utf-8",
    )
    return path


def _archive_artifacts(ctx: RunContext, overwrite_existing_archive: bool = True) -> None:
    """Archive spec/plan/contract into the run folder; the main repo stays clean.

    Tracked artifacts (merged earlier ADW run) are reset via git to the
    checked-in state instead of deleted — otherwise a
    tracked deletion would remain in the checkout. Untracked artifacts are removed.

    ``overwrite_existing_archive=False`` is the idempotent CLEANUP mode (spec-gate
    resume): an already-archived artifact is NOT re-copied from the checkout —
    the checkout copy of a tracked artifact was reset to the OLD merged version
    by an earlier archival and would clobber the reviewed archive. The stray
    checkout file is still cleaned up."""
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    # issue.md und die Summaries werden mitarchiviert — nicht in ARTIFACTS, weil
    # keine Build-Lane dagegen baut; hier aber wie die anderen aufgeräumt.
    for name in (*ARTIFACTS, ISSUE_FILE, *SUMMARIES):
        source = ctx.repo / ".adw" / name
        if not source.is_file():
            continue
        if overwrite_existing_archive or not (ctx.run_dir / name).is_file():
            shutil.copy2(source, ctx.run_dir / name)
            # AFTER the artifact was successfully written into the run folder.
            _emit_artifact(ctx, ctx.run_dir / name)
        _reset_checkout_artifact(ctx, name)
