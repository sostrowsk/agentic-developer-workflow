"""Env whitelist for all subprocesses — no secret leakage into Gates/Agents."""

import os

# Nur explizit Erlaubtes wird durchgereicht. Insbesondere NICHT: API-Keys,
# AWS-/Cloud-Credentials, Tokens.
_ALLOWED = {
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "TERM",
    "TMPDIR",
    "LANG",
    "LANGUAGE",
    "TZ",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "NODE_ENV",
    "CI",
    # Nicht-geheimer Pfad zur Claude-CLI-Konfiguration — nötig, damit
    # stored-login/Session-Resume auch mit custom Config-Dir funktioniert.
    # CODEX_HOME bewusst NICHT global: der Pfad wird nur dem CodexRunner
    # gereicht, damit Gates/Agents das Auth-Verzeichnis nicht orten können.
    "CLAUDE_CONFIG_DIR",
}
_ALLOWED_PREFIXES = ("LC_",)


def safe_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Filtered environment: whitelist + explicitly passed extra variables."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _ALLOWED or key.startswith(_ALLOWED_PREFIXES)
    }
    if extra:
        env.update(extra)
    return env
