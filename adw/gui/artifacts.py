"""Run artifacts: the listing for the Artifacts tab and safe resolution of an
artifact name to a file inside the run directory."""

from pathlib import Path

from adw.state import contained as _contained

# --- Artifacts tab + content route (Aufgabe B) ----------------------------------
# The Artifacts tab lists the whitelisted artifacts of the run and the dual-author
# drafts; content is served by the read-only route below. `{name}` is a SINGLE path
# segment, resolved through a fixed whitelist — never treated as a filesystem path.

# The eight whitelisted top-level artifact names (GUI-SPEC §7.2 / contract).
_ARTIFACT_TOP_LEVEL = [
    "issue.md", "spec.md", "plan.md", "contract.yaml",
    "escalation.md", "followups.md", "spec-summary.md", "plan-summary.md",
]
_ARTIFACT_TOP_LEVEL_SET = set(_ARTIFACT_TOP_LEVEL)
# The artifacts produced through dual authoring — each has a claude/codex draft.
_ARTIFACT_DUAL = ["spec.md", "plan.md", "contract.yaml"]
_DRAFT_AUTHORS = ("claude", "codex")


def _draft_names(top_name: str) -> list[str]:
    stem, ext = top_name.rsplit(".", 1)
    return [f"{stem}.{author}.{ext}" for author in _DRAFT_AUTHORS]


def _resolve_artifact(run_dir: Path, name: str) -> Path | None:
    """Map a single-segment artifact ``name`` to a file path inside ``run_dir`` via
    the fixed whitelist, or None if the name is unknown. The name is NEVER treated
    as a filesystem path: any separator, ``..`` or absolute form makes it unknown
    without any filesystem access (B4/B5)."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    if name in _ARTIFACT_TOP_LEVEL_SET:
        return run_dir / name
    parts = name.split(".")
    if len(parts) >= 3:
        author = parts[-2]
        stem_ext = ".".join(parts[:-2]) + "." + parts[-1]
        if author in _DRAFT_AUTHORS and stem_ext in _ARTIFACT_TOP_LEVEL_SET:
            # Drafts are addressed by their FLAT filename → drafts/<name>.
            return run_dir / "drafts" / name
    return None


def _artifact_present(run_dir: Path, relpath: str) -> bool:
    # Contain against THIS run's directory (not the whole runs tree): a whitelisted
    # name symlinked to a SIBLING run's file resolves outside run_dir and must count
    # as missing, never present (B4/B5/B6).
    contained = _contained(run_dir / relpath, run_dir)
    return contained is not None and contained.is_file()


def _artifacts_listing(run_dir: Path) -> list[dict]:
    """The whitelisted artifacts of the run and, for the dual-authored ones, their
    two drafts — each marked present (a fetch anchor) or missing (B1/B2/B3). A
    missing artifact/draft is never an error."""
    items = []
    for name in _ARTIFACT_TOP_LEVEL:
        drafts = []
        if name in _ARTIFACT_DUAL:
            for dname in _draft_names(name):
                drafts.append({
                    "name": dname,
                    "present": _artifact_present(run_dir, f"drafts/{dname}"),
                })
        items.append({
            "name": name,
            "present": _artifact_present(run_dir, name),
            "drafts": drafts,
        })
    return items
