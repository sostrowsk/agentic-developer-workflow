"""Forge detection: GitLab or GitHub project?

Explicit config (`ci.provider`) always wins. Without an override, the
hostname of the origin remote URL is checked — only the UNAMBIGUOUS cases
("github" or "gitlab" in the hostname, covering gitlab.com/github.com and
self-hosted instances with a descriptive hostname) are recognized.
Everything else is fail fast: better a clear request for
`ci.provider` than a poll against the wrong API.
"""

import re
import subprocess
from pathlib import Path
from typing import Literal

from adw.env import safe_env

Forge = Literal["gitlab", "github"]

_GIT_TIMEOUT = 30


class ForgeError(Exception):
    """Hosting cannot be determined — set ci.provider in .adw/config.yaml."""


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
    """Extract the hostname from https-, ssh- and scp-style Git URLs."""
    match = re.match(r"^[a-z+]+://(?:[^/@]+@)?([^/:]+)", url)  # https://, ssh://
    if match:
        return match.group(1).lower()
    match = re.match(r"^(?:[^@]+@)?([^:/]+):", url)  # scp-Syntax: git@host:pfad
    if match:
        return match.group(1).lower()
    return url.lower()
