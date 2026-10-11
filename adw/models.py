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
# A failed probe is retried after this back-off, not on every start.
FAILURE_BACKOFF = timedelta(hours=1)
CLAUDE_ALIASES = ("opus", "fable", "sonnet")
_CACHE_PATH_ENV = "ADW_MODEL_CHECK_PATH"
_CODEX_SLUG_RE = re.compile(r"gpt-(\d+(?:\.\d+)*)-([a-z]+)")
# claude-<line>-<major>[-<minor>], optionally -<8-digit date> and/or a [..] suffix.
_CLAUDE_ID_RE = re.compile(r"claude-([a-z]+)-(\d{1,3}(?:-\d{1,3})?)(?:-\d{8})?(?:\[[^\]]*\])?")
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


def _read_cache(path: Path, now: datetime) -> dict[str, dict]:
    """Still-valid per-role entries. A success is good for CHECK_TTL, a failed probe
    for FAILURE_BACKOFF (so a dead network does not cost every start a new round).
    Entries taken against other pins, or unusable entries, count as missing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        roles = data["roles"]
        if data["pinned"] != pinned_models() or not isinstance(roles, dict):
            return {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    valid: dict[str, dict] = {}
    for role, entry in roles.items():
        try:
            checked_at = datetime.fromisoformat(entry["checked_at"])
            model = entry["model"]
        except (KeyError, TypeError, ValueError):
            continue
        error = entry.get("error")
        if checked_at.tzinfo is None:
            continue
        # A success needs a model id, a failure needs its reason — anything else
        # is unusable and probed afresh rather than crashing the preflight.
        success = isinstance(model, str) and bool(model)
        failure = model is None and isinstance(error, str)
        if not (success or failure):
            continue
        ttl = CHECK_TTL if model else FAILURE_BACKOFF
        if timedelta(0) <= now - checked_at <= ttl:
            valid[role] = entry
    return valid


def _write_cache(path: Path, roles: dict[str, dict]) -> None:
    """Atomic via a writer-unique temp file — concurrent ADW commands share the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pinned": pinned_models(), "roles": roles}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _catalog_age(catalog: dict, now: datetime) -> timedelta | None:
    """None when the catalog carries no usable (timezone-aware) fetch time."""
    try:
        fetched = datetime.fromisoformat(catalog["fetched_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if fetched.tzinfo is None:
        return None
    return now - fetched


def _probe_codex(catalog: Callable[[], dict | None], now: datetime) -> str:
    """Newest Codex model, or raises with the reason the catalog proves nothing."""
    codex_catalog = catalog()
    if codex_catalog is None:
        raise LookupError("kein models_cache.json der codex-CLI gefunden")
    if not _well_formed(codex_catalog):
        raise LookupError("Katalog models_cache.json hat ein unerwartetes Format")
    age = _catalog_age(codex_catalog, now)
    if age is None:
        raise LookupError("Katalog ohne gültigen Zeitstempel (fetched_at) — Alter unklar")
    if age > CATALOG_MAX_AGE:
        raise LookupError(
            f"Katalog veraltet ({age.days} Tage) — einmal `codex` direkt starten, "
            "damit er sich aktualisiert"
        )
    return latest_codex_model(codex_catalog, codex.CODEX_MODEL)


def latest_models(
    cache_path: Path,
    now: datetime,
    resolve: Callable[[str], str],
    catalog: Callable[[], dict | None],
) -> tuple[dict[str, str], list[str]]:
    """Newest model per role plus warnings for roles that could not be checked.
    Every role is cached on its own: successes for CHECK_TTL, failures for
    FAILURE_BACKOFF — only expired roles are probed again."""
    cached = _read_cache(cache_path, now)
    probes: dict[str, Callable[[], str]] = {
        alias: (lambda alias=alias: resolve(alias)) for alias in CLAUDE_ALIASES
    }
    probes["codex"] = lambda: _probe_codex(catalog, now)
    roles: dict[str, dict] = {}
    probed = False
    for role, probe in probes.items():
        if role in cached:
            roles[role] = cached[role]
            continue
        probed = True
        try:
            roles[role] = {"model": probe(), "checked_at": now.isoformat(), "error": None}
        except Exception as exc:  # noqa: BLE001 — a failed probe must never block a run
            roles[role] = {"model": None, "checked_at": now.isoformat(), "error": str(exc)}
    latest = {role: entry["model"] for role, entry in roles.items() if entry["model"]}
    warnings = [
        f"Modell-Check {'Codex' if role == 'codex' else role}: nicht prüfbar ({entry['error']})"
        for role, entry in roles.items()
        if not entry["model"]
    ]
    if probed:
        try:
            _write_cache(cache_path, roles)
        except OSError as exc:
            warnings.append(
                f"Modell-Check: Cache nicht schreibbar ({exc}) — nächster Start prüft neu"
            )
    return latest, warnings


def _claude_version(model_id: str) -> tuple[str, tuple[int, ...]] | None:
    """(line, version) of a Claude id — date snapshots and [..] suffixes ignored."""
    match = _CLAUDE_ID_RE.fullmatch(model_id)
    if not match:
        return None
    version = tuple(int(part) for part in match.group(2).split("-"))
    return match.group(1), version


def _is_newer(role: str, pinned: str, latest: str) -> bool:
    """Only a PROVEN newer model counts — older, equal, other-line or unparsable
    ids never block a run."""
    if role == "codex":
        # latest_codex_model only ever returns the pin, a higher version of its
        # tier, or an upgrade the catalog announces for exactly this pin.
        return latest != pinned
    ours, theirs = _claude_version(pinned), _claude_version(latest)
    if ours is None or theirs is None or ours[0] != theirs[0]:
        return False
    return theirs[1] > ours[1]


def find_outdated(latest: dict[str, str]) -> list[Outdated]:
    return [
        Outdated(role, pinned, latest[role])
        for role, pinned in pinned_models().items()
        if role in latest and _is_newer(role, pinned, latest[role])
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
