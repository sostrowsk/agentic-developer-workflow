"""Repo registry the later GUI reads (GUI-SPEC §7.4).

A single JSON file ``~/.adw/repos.json`` (version 1) records, per canonical repo
path, a stable URL-safe ``slug`` and a UTC ``last_seen`` timestamp. ``adw run``
registers its resolved target repo automatically (fail-open — a registry error
never blocks the run). The slug is a pure function of the canonical path, so it
is stable across updates and differs for different paths even with the same
directory name, and it never contains a raw filesystem path. A missing or
unreadable file yields an empty, usable registry; every write is atomic
(temp file in the same dir + ``os.replace``), so an aborted write leaves the
previous content intact. ``exists`` is derived on load, never persisted.
"""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REGISTRY_VERSION = 1


@dataclass
class RepoEntry:
    path: str
    slug: str
    last_seen: str
    exists: bool = False


@dataclass
class Registry:
    repos: list

    def resolve(self, slug: str):
        for entry in self.repos:
            if entry.slug == slug:
                return entry
        return None


# A single overridable path source (Aufgabe A): outside tests the resolved path
# stays exactly ``~/.adw/repos.json`` in the pinned format; a test harness can
# point the registry at a throwaway location via ``ADW_REGISTRY_PATH`` so a run's
# auto-registration never writes into the developer's real home. This is a plain
# environment override, not a test-only branch — the runtime behaviour of
# ``adw run`` is unchanged when the variable is unset.
_REGISTRY_PATH_ENV = "ADW_REGISTRY_PATH"


def _registry_path() -> Path:
    override = os.environ.get(_REGISTRY_PATH_ENV)
    if override:
        return Path(override)
    return Path.home() / ".adw" / "repos.json"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slug(canonical_path: str) -> str:
    """Stable, URL-safe slug: a readable basename plus a short hash of the FULL
    canonical path. Same path → same slug; different paths → different slugs (the
    hash disambiguates equal basenames); never contains a raw filesystem path."""
    base = os.path.basename(canonical_path.rstrip("/")) or "repo"
    readable = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower() or "repo"
    digest = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


# A URL-safe slug: no path separators, spaces or other reserved characters — so a
# raw filesystem path (which contains "/") can never masquerade as a slug (§7.4).
_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")


def _is_valid_slug(slug) -> bool:
    return isinstance(slug, str) and _SLUG_RE.fullmatch(slug) is not None


def _unique_slug(canonical_path: str, used: set) -> str:
    """A deterministic slug for ``canonical_path`` that is not already in ``used``
    (a numeric suffix disambiguates the astronomically rare hash collision)."""
    base = _slug(canonical_path)
    if base not in used:
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    return f"{base}-{i}"


def _normalize_entry(entry) -> dict | None:
    """A structurally valid repo entry with all required string fields: the path
    is normalized (so duplicates collapse), the slug is repaired from the path if
    missing or not URL-safe, and ``last_seen`` is coerced to a string — or None if
    unrecoverable (not a mapping, or without a usable path from which nothing,
    including the slug, can be derived)."""
    if not isinstance(entry, dict):
        return None
    path = entry.get("path")
    if not isinstance(path, str) or not path:
        return None
    path = os.path.normpath(path)
    slug = entry.get("slug")
    if not _is_valid_slug(slug):
        slug = _slug(path)
    last_seen = entry.get("last_seen")
    if not isinstance(last_seen, str):
        last_seen = ""
    return {"path": path, "slug": slug, "last_seen": last_seen}


def _load_raw() -> dict:
    """The parsed registry as a normalized version-1 structure. A missing,
    unreadable, wrong-version or otherwise structurally corrupt file yields an
    empty registry. Recoverable entries are repaired; unrecoverable ones are
    discarded. The result is deduplicated by canonical path (first occurrence
    wins) with a unique, URL-safe slug per path — the invariants of AC 21/23.
    Never raises."""
    try:
        with open(_registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": REGISTRY_VERSION, "repos": []}
    if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
        return {"version": REGISTRY_VERSION, "repos": []}
    raw_repos = data.get("repos")
    if not isinstance(raw_repos, list):
        return {"version": REGISTRY_VERSION, "repos": []}

    repos: list[dict] = []
    seen_paths: set[str] = set()
    used_slugs: set[str] = set()
    for raw in raw_repos:
        entry = _normalize_entry(raw)
        if entry is None or entry["path"] in seen_paths:
            continue  # discard unrecoverable and duplicate-path entries
        seen_paths.add(entry["path"])
        if entry["slug"] in used_slugs:  # a slug must be unique across paths
            entry["slug"] = _unique_slug(entry["path"], used_slugs)
        used_slugs.add(entry["slug"])
        repos.append(entry)
    return {"version": REGISTRY_VERSION, "repos": repos}


def _write_atomic(data: dict) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".repos.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)  # atomic; a failure before this keeps the old file
    except BaseException:
        # Never leave a temp file behind on a failed/aborted write.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def register_repo(path) -> RepoEntry:
    """Create or update the entry for ``path`` (refreshing ``last_seen``). At most
    one entry per canonical path; a pre-existing entry keeps its slug."""
    canonical = os.path.normpath(str(Path(path).resolve()))
    now = _utc_now_iso()
    # _load_raw already deduplicated by path and made slugs unique & URL-safe.
    repos = _load_raw()["repos"]

    for entry in repos:
        if entry["path"] == canonical:
            entry["last_seen"] = now
            slug = entry["slug"]  # keep the existing (already valid, unique) slug
            break
    else:
        slug = _unique_slug(canonical, {e["slug"] for e in repos})
        repos.append({"path": canonical, "slug": slug, "last_seen": now})

    _write_atomic({"version": REGISTRY_VERSION, "repos": repos})
    return RepoEntry(path=canonical, slug=slug, last_seen=now, exists=os.path.isdir(canonical))


def load_registry() -> Registry:
    """The current registry with ``exists`` derived from the filesystem. A missing
    or unreadable file yields an empty, usable registry."""
    data = _load_raw()
    repos = []
    for entry in data.get("repos", []):
        p = entry.get("path")
        repos.append(
            RepoEntry(
                path=p,
                slug=entry.get("slug"),
                last_seen=entry.get("last_seen"),
                exists=bool(p) and os.path.isdir(p),
            )
        )
    return Registry(repos=repos)
