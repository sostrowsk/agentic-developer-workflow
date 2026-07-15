"""Forge-Erkennung: GitLab- oder GitHub-Projekt?

Explizite Config (`ci.provider`) gewinnt immer. Ohne Override wird der
Hostname der origin-Remote-URL geprüft — nur die EINDEUTIGEN Fälle
("github" bzw. "gitlab" im Hostnamen, deckt gitlab.com/github.com und
Self-Hosted-Instanzen mit sprechendem Hostnamen ab) werden erkannt.
Alles andere ist fail fast: lieber eine klare Rückfrage nach
`ci.provider` als ein Poll gegen die falsche API.
"""

import re
import subprocess
from pathlib import Path
from typing import Literal

from adw.env import safe_env

Forge = Literal["gitlab", "github"]

_GIT_TIMEOUT = 30


class ForgeError(Exception):
    """Hosting nicht bestimmbar — ci.provider in .adw/config.yaml setzen."""


def detect_forge(repo: Path, override: Forge | None) -> Forge:
    if override is not None:
        return override
    url = _origin_url(repo)
    if url is None:
        raise ForgeError(
            f"{repo}: kein origin-Remote — Hosting nicht erkennbar, bitte "
            f"ci.provider (gitlab | github) in .adw/config.yaml setzen"
        )
    host = _hostname(url)
    if "github" in host:
        return "github"
    if "gitlab" in host:
        return "gitlab"
    raise ForgeError(
        f"{repo}: Host {host!r} (origin: {url}) ist weder als GitLab noch als "
        f"GitHub erkennbar — bitte ci.provider (gitlab | github) in "
        f".adw/config.yaml setzen"
    )


def _origin_url(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "remote",
                "get-url",
                "origin",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=safe_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ForgeError(f"{repo}: origin-Remote nicht lesbar — {exc}") from exc
    if result.returncode != 0:
        return None  # kein origin konfiguriert
    url = result.stdout.strip()
    return url or None


def _hostname(url: str) -> str:
    """Hostname aus https-, ssh- und scp-artigen Git-URLs ziehen."""
    match = re.match(r"^[a-z+]+://(?:[^/@]+@)?([^/:]+)", url)  # https://, ssh://
    if match:
        return match.group(1).lower()
    match = re.match(r"^(?:[^@]+@)?([^:/]+):", url)  # scp-Syntax: git@host:pfad
    if match:
        return match.group(1).lower()
    return url.lower()
