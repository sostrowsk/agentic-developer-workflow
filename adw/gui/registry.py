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


def _registry_path() -> Path:
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


def _load_raw() -> dict:
    """The parsed registry file, or an empty version-1 structure if it is missing
    or unreadable/corrupt (never raises)."""
    try:
        with open(_registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": REGISTRY_VERSION, "repos": []}
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        return {"version": REGISTRY_VERSION, "repos": []}
    return data


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
    canonical = str(Path(path).resolve())
    now = _utc_now_iso()
    data = _load_raw()
    repos = data.get("repos", [])

    slug = _slug(canonical)
    for entry in repos:
        if entry.get("path") == canonical:
            entry["last_seen"] = now
            slug = entry.get("slug", slug)  # keep the existing slug
            break
    else:
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
