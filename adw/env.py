"""Env-Whitelist für alle Subprozesse — kein Secret-Leakage in Gates/Agents."""

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
    "CLAUDE_CONFIG_DIR",
}
_ALLOWED_PREFIXES = ("LC_",)


def safe_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Gefilterte Umgebung: Whitelist + explizit übergebene Zusatzvariablen."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _ALLOWED or key.startswith(_ALLOWED_PREFIXES)
    }
    if extra:
        env.update(extra)
    return env
