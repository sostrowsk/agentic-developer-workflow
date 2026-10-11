"""Model freshness check: refuse to run while ADW pins older models than the newest.

Claude: the CLI aliases ``opus``/``fable``/``sonnet`` always resolve to the newest
model of their line — one tiny SDK call each tells which model that is.
Codex: the codex CLI keeps its model catalog in ``$CODEX_HOME/models_cache.json``;
the newest model of the pinned tier (e.g. ``*-astra``) or an announced upgrade wins.

The result is cached for 24 h (``~/.adw/model-check.json``), so the probe costs at
most one round of calls per day. A probe that fails (offline, plan limit, no
catalog) only warns — only a PROVEN newer model blocks.
"""

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adw import agents, codex

CHECK_TTL = timedelta(hours=24)
# The codex CLI refreshes its catalog only when run with the real CODEX_HOME — ADW's
# isolated home never updates it. An older catalog proves nothing about "newest".
CATALOG_MAX_AGE = timedelta(days=7)
CLAUDE_ALIASES = ("opus", "fable", "sonnet")
_CACHE_PATH_ENV = "ADW_MODEL_CHECK_PATH"
_CODEX_SLUG_RE = re.compile(r"gpt-(\d+(?:\.\d+)*)-([a-z]+)")
_WHERE = {
    "opus": "adw/agents.py: OPUS",
    "fable": "adw/agents.py: FABLE",
    "sonnet": "adw/agents.py: SONNET",
    "codex": "adw/codex.py: CODEX_MODEL",
}


@dataclass(frozen=True)
class Outdated:
    role: str
    pinned: str
    latest: str


def pinned_models() -> dict[str, str]:
    return {
        "opus": agents.OPUS,
        "fable": agents.FABLE,
        "sonnet": agents.SONNET,
        "codex": codex.CODEX_MODEL,
    }


def resolve_claude_alias(alias: str) -> str:
    return agents.resolve_model_alias(alias)


def load_codex_catalog() -> dict | None:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        return json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _version(slug: str) -> tuple[tuple[int, ...], str] | None:
    match = _CODEX_SLUG_RE.fullmatch(slug)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split(".")), match.group(2)


def _well_formed(catalog) -> bool:
    """The structure latest_codex_model relies on — anything else is unchecked."""
    if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
        return False
    for entry in catalog["models"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug", ""), str):
            return False
        upgrade = entry.get("upgrade")
        if upgrade is not None and not (
            isinstance(upgrade, dict) and isinstance(upgrade.get("model"), str)
        ):
            return False
    return True


def latest_codex_model(catalog: dict, pinned: str) -> str:
    """Newest listed model of the pinned tier; an announced upgrade of the pinned
    model wins outright."""
    entries = catalog.get("models", [])
    for entry in entries:
        if entry.get("slug") == pinned and entry.get("upgrade"):
            return entry["upgrade"]["model"]
    pinned_version = _version(pinned)
    if pinned_version is None:
        return pinned
    best_version, best = pinned_version[0], pinned
    for entry in entries:
        if entry.get("visibility") != "list":
            continue
        version = _version(entry.get("slug", ""))
        if version and version[1] == pinned_version[1] and version[0] > best_version:
            best_version, best = version[0], entry["slug"]
    return best


def _cache_path() -> Path:
    override = os.environ.get(_CACHE_PATH_ENV)
    return Path(override) if override else Path.home() / ".adw" / "model-check.json"


def _read_cache(path: Path, now: datetime) -> dict[str, str] | None:
    """A cached result counts only if it is younger than CHECK_TTL AND was taken
    against the current pins — after an ADW model update the old 'latest' would
    otherwise flag the new pins as outdated."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(data["checked_at"])
        latest, pinned = data["latest"], data["pinned"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if now - checked_at > CHECK_TTL or pinned != pinned_models() or not isinstance(latest, dict):
        return None
    return latest


def _write_cache(path: Path, now: datetime, latest: dict[str, str]) -> None:
    """Atomic via a writer-unique temp file — concurrent ADW commands share the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"checked_at": now.isoformat(), "pinned": pinned_models(), "latest": latest}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _catalog_age(catalog: dict, now: datetime) -> timedelta | None:
    try:
        return now - datetime.fromisoformat(catalog["fetched_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def latest_models(
    cache_path: Path,
    now: datetime,
    resolve: Callable[[str], str],
    catalog: Callable[[], dict | None],
) -> tuple[dict[str, str], list[str]]:
    """Newest model per role plus warnings for roles that could not be checked.
    Only a complete result is cached — a failed probe is retried next start."""
    cached = _read_cache(cache_path, now)
    if cached is not None:
        return cached, []
    latest: dict[str, str] = {}
    warnings: list[str] = []
    for alias in CLAUDE_ALIASES:
        try:
            latest[alias] = resolve(alias)
        except Exception as exc:  # noqa: BLE001 — a failed probe must never block a run
            warnings.append(f"Modell-Check {alias}: nicht prüfbar ({exc})")
    codex_catalog = catalog()
    if codex_catalog is not None and not _well_formed(codex_catalog):
        warnings.append("Modell-Check Codex: Katalog models_cache.json hat ein unerwartetes Format")
    elif codex_catalog is None:
        warnings.append("Modell-Check Codex: kein models_cache.json der codex-CLI gefunden")
    elif (age := _catalog_age(codex_catalog, now)) is not None and age > CATALOG_MAX_AGE:
        warnings.append(
            f"Modell-Check Codex: Katalog veraltet ({age.days} Tage) — einmal `codex` "
            "direkt starten, damit er sich aktualisiert"
        )
    else:
        latest["codex"] = latest_codex_model(codex_catalog, codex.CODEX_MODEL)
    if not warnings:
        try:
            _write_cache(cache_path, now, latest)
        except OSError as exc:
            warnings.append(
                f"Modell-Check: Cache nicht schreibbar ({exc}) — nächster Start prüft neu"
            )
    return latest, warnings


def find_outdated(latest: dict[str, str]) -> list[Outdated]:
    return [
        Outdated(role, pinned, latest[role])
        for role, pinned in pinned_models().items()
        if role in latest and latest[role] != pinned
    ]


def outdated_message(outdated: list[Outdated]) -> str:
    lines = [f"  {o.role}: {o.pinned} → {o.latest}   ({_WHERE[o.role]})" for o in outdated]
    return (
        "Es gibt neuere Modelle als die in ADW eingestellten — Lauf NICHT gestartet:\n"
        + "\n".join(lines)
        + "\nZuerst ADW selbst auf die neuesten Modelle aktualisieren (Konstanten, Tests, "
        "Doku, Release), dann den Lauf erneut starten."
    )


def check() -> tuple[list[Outdated], list[str]]:
    """Entry point for the CLI preflight."""
    latest, warnings = latest_models(
        _cache_path(), datetime.now(UTC), resolve_claude_alias, load_codex_catalog
    )
    return find_outdated(latest), warnings
