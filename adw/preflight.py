"""Preflight for run/resume/approve: checks that run BEFORE any state mutation.

Every check either passes, self-heals (ADW's own authoring artifacts) or refuses
with ``PreflightRefused`` — never an escalation. The CLI turns a refusal into
exit 1 with the message; order (forge → working tree → models) is fixed in
``run_all``.
"""

import subprocess
from pathlib import Path

import typer

from adw import models
from adw.config import AdwConfig
from adw.forge import ForgeError, detect_forge
from adw.git import run_git


class PreflightRefused(Exception):
    """A preflight check refused the command — nothing was mutated or escalated."""


# Genau die sechs ADW-eigenen Authoring-Artefakte (Konvention, nicht
# konfigurierbar, kein Glob — E2/B3): nur wenn AUSSCHLIESSLICH diese im
# Haupt-Checkout dirty sind, heilt der Vorflug sie selbst.
SELF_HEAL_ARTIFACTS = (
    ".adw/issue.md",
    ".adw/spec.md",
    ".adw/plan.md",
    ".adw/contract.yaml",
    ".adw/spec-summary.md",
    ".adw/plan-summary.md",
)


def git_plain(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Git in the main checkout WITHOUT the escalating orchestrator wrapper — the
    worktree preflight must NEVER escalate a run (B1), only refuse or self-heal."""
    return run_git(repo, *args, timeout=120)


def worktree_dirty_paths(repo: Path) -> list[str]:
    """Full set of uncommitted paths in the main checkout (tracked + untracked).

    Determined up front from ``git status --porcelain -z`` so a decision is made
    over the COMPLETE set before anything is mutated. A failing ``git status``
    (nonzero exit, timeout, or launch failure) is NEVER treated as a clean tree —
    the preflight cannot then establish cleanliness, so it REFUSES (non-escalating)
    rather than silently proceeding to mutate state."""
    try:
        result = git_plain(repo, "status", "--porcelain", "-z")
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightRefused(
            "Arbeitsbaum-Prüfung fehlgeschlagen: `git status` im Haupt-Checkout "
            f"nicht ausführbar — {exc}"
        ) from exc
    if result.returncode != 0:
        raise PreflightRefused(
            "Arbeitsbaum-Prüfung fehlgeschlagen: `git status` im Haupt-Checkout "
            f"endete mit Exit {result.returncode} — {result.stderr.strip()[:500]}"
        )
    tokens = result.stdout.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        if not entry:
            i += 1
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        # Rename/Copy: "XY <neu>\0<orig>" — der Ursprungspfad ist ein eigenes Token.
        if "R" in status or "C" in status:
            i += 1
            if i < len(tokens) and tokens[i]:
                paths.append(tokens[i])
        i += 1
    return paths


def heal_adw_artifact(repo: Path, relpath: str) -> None:
    """Reset ONE ADW-owned artifact: tracked → git checkout, untracked → delete.

    A failing heal REFUSES (non-escalating) instead of running on with a
    half-healed tree — the caller must not proceed to mutate state."""
    # Maßgeblich ist HEAD, NICHT der Index: `git checkout -- <pfad>` restauriert aus
    # dem Index und ließe eine GESTAGETE Änderung/Löschung/Umbenennung eines
    # Artefakts stehen — die folgende Statusprüfung sähe den Baum weiter dirty und
    # verweigerte, statt zu heilen (Follow-up Lauf 72a042ad).
    try:
        in_head = git_plain(repo, "ls-tree", "--name-only", "HEAD", "--", relpath)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightRefused(
            f"Selbstheilung von {relpath} fehlgeschlagen: git ls-tree — {exc}"
        ) from exc
    if in_head.returncode != 0:
        raise PreflightRefused(
            f"Selbstheilung von {relpath} fehlgeschlagen: git ls-tree Exit "
            f"{in_head.returncode} — {in_head.stderr.strip()[:300]}"
        )
    if in_head.stdout.strip():
        # `checkout HEAD -- <pfad>` setzt Index UND Arbeitsbaum auf den Stand aus
        # HEAD zurück — pfadbezogen, fremde Dateien sind unerreichbar.
        try:
            result = git_plain(repo, "checkout", "HEAD", "--", relpath)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreflightRefused(
                f"Selbstheilung von {relpath} fehlgeschlagen: git checkout — {exc}"
            ) from exc
        if result.returncode != 0:
            raise PreflightRefused(
                f"Selbstheilung von {relpath} fehlgeschlagen: git checkout Exit "
                f"{result.returncode} — {result.stderr.strip()[:300]}"
            )
    else:
        # Nicht in HEAD: ein etwaiger Index-Eintrag (frisch gestagetes Artefakt)
        # muss mit weg, sonst bliebe der Baum nach dem Löschen dirty.
        try:
            staged = git_plain(
                repo, "rm", "--cached", "--force", "--quiet", "--ignore-unmatch",
                "--", relpath,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreflightRefused(
                f"Selbstheilung von {relpath} fehlgeschlagen: git rm — {exc}"
            ) from exc
        if staged.returncode != 0:
            raise PreflightRefused(
                f"Selbstheilung von {relpath} fehlgeschlagen: git rm Exit "
                f"{staged.returncode} — {staged.stderr.strip()[:300]}"
            )
        try:
            (repo / relpath).unlink(missing_ok=True)
        except OSError as exc:
            raise PreflightRefused(f"Selbstheilung von {relpath} fehlgeschlagen: {exc}") from exc


def check_worktree(repo: Path) -> None:
    """Guard the main checkout before run/resume/approve (B1–B4). It NEVER
    escalates: a clean tree proceeds; a dirty set consisting EXCLUSIVELY of the
    six ADW-owned authoring artifacts self-heals and proceeds; anything else
    (foreign or mixed) refuses with a clear message, discarding nothing. A git
    failure (status/heal) is also a refusal, never a silent proceed."""
    dirty = worktree_dirty_paths(repo)
    if not dirty:
        return
    if all(path in SELF_HEAL_ARTIFACTS for path in dirty):
        for path in dirty:
            heal_adw_artifact(repo, path)
        # Re-Check: die Heilung MUSS den Checkout sauber hinterlassen haben —
        # sonst liefe der Lauf mit einem halb geheilten Baum weiter.
        remaining = worktree_dirty_paths(repo)
        if remaining:
            raise PreflightRefused(
                "Selbstheilung ließ den Haupt-Checkout nicht sauber:\n  "
                + "\n  ".join(sorted(remaining))
            )
        return
    listing = "\n  ".join(sorted(dirty))
    raise PreflightRefused(
        "Der Haupt-Checkout hat uncommittete Änderungen außerhalb der ADW-eigenen "
        f"Authoring-Artefakte:\n  {listing}\n"
        "Bitte committen oder stashen, dann den Lauf erneut starten bzw. fortsetzen "
        "(der ADW verwirft fremde Änderungen nie)."
    )


def check_forge(repo: Path, config: AdwConfig, dry_run: bool) -> None:
    """Refuse a real run whose forge cannot be determined BEFORE any agent runs —
    otherwise phase 7 would only escalate after the whole build. Never escalates,
    mutates nothing. Dry-run keeps its GitLab fallback (CI is mocked there)."""
    if dry_run:
        return
    try:
        detect_forge(repo, config.ci.provider)
    except ForgeError as exc:
        raise PreflightRefused(str(exc)) from exc


def check_models(dry_run: bool) -> None:
    """Refuse a real run while ADW pins older models than the newest (checked at
    most once per 24 h). A probe that cannot run only warns. Dry run: 0 tokens."""
    if dry_run:
        return
    outdated, warnings = models.check()
    for warning in warnings:
        typer.echo(f"Warnung: {warning}", err=True)
    if outdated:
        raise PreflightRefused(models.outdated_message(outdated))


def run_all(repo: Path, config: AdwConfig, dry_run: bool) -> None:
    """The preflight order of run/resume/approve: the forge is known, the main
    checkout is clean (after self-heal), and only then — so a refused checkout
    costs no probe tokens — the model freshness check."""
    check_forge(repo, config, dry_run)
    check_worktree(repo)
    check_models(dry_run)
