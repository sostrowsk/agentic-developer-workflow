"""RED tests for Aufgabe B3 — the repo registry (``adw.gui.registry``).

Derived from .adw/spec.md (AC 21–26), .adw/contract.yaml (``registry_api`` /
``registry_file_format``) and docs/GUI-SPEC.md §7.4 (URL slug). The module does
not exist yet, so these tests are RED until it is built. Only the public surface
and the on-disk ``~/.adw/repos.json`` format are asserted.
"""

import json
import shutil
from datetime import datetime

import pytest
from typer.testing import CliRunner

from adw.cli import app
from adw.gui.registry import load_registry, register_repo
from adw.state import RunState

cli = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Hermetic HOME so the registry writes under a throwaway ~/.adw."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def registry_file(home):
    return home / ".adw" / "repos.json"


def test_register_repo_writes_version1_format(home, tmp_path):
    """AC 21: the registry lives only in ~/.adw/repos.json in the pinned version-1
    format; ``exists`` is derived, not persisted."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    entry = register_repo(repo)
    assert entry.path == str(repo.resolve())
    assert entry.exists is True

    data = json.loads(registry_file(home).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert isinstance(data["repos"], list) and len(data["repos"]) == 1
    persisted = data["repos"][0]
    assert set(persisted) == {"path", "slug", "last_seen"}  # exists NOT persisted
    assert persisted["path"] == entry.path
    assert persisted["slug"] == entry.slug
    parsed = datetime.fromisoformat(persisted["last_seen"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None  # UTC / ISO-8601


def test_register_repo_is_idempotent_and_slug_stable(home, tmp_path):
    """AC 21/23: at most one entry per canonical path; re-registering updates
    last_seen and keeps the same slug."""
    repo = tmp_path / "r"
    repo.mkdir()
    first = register_repo(repo)
    second = register_repo(repo)

    assert first.slug == second.slug
    assert second.last_seen >= first.last_seen
    reg = load_registry()
    assert len([e for e in reg.repos if e.path == first.path]) == 1


def test_slugs_differ_for_same_basename_and_resolve_back(home, tmp_path):
    """AC 23: different canonical paths yield different slugs even with the same
    directory name; a slug never contains a raw filesystem path and resolves to
    its entry; an unknown slug resolves to None."""
    a = tmp_path / "x" / "proj"
    a.mkdir(parents=True)
    b = tmp_path / "y" / "proj"
    b.mkdir(parents=True)
    ea = register_repo(a)
    eb = register_repo(b)

    assert ea.slug != eb.slug
    for slug in (ea.slug, eb.slug):
        assert "/" not in slug
        assert str(tmp_path) not in slug  # no raw fs path in the URL part

    reg = load_registry()
    assert reg.resolve(ea.slug).path == ea.path
    assert reg.resolve(eb.slug).path == eb.path
    assert reg.resolve("kein-solcher-slug") is None


def test_missing_repo_is_flagged_exists_false_but_stays_resolvable(home, tmp_path):
    """AC 24: a vanished repo path is flagged exists=false yet stays listable and
    resolvable, and never raises."""
    repo = tmp_path / "gone"
    repo.mkdir()
    entry = register_repo(repo)
    slug = entry.slug
    shutil.rmtree(repo)

    reg = load_registry()
    resolved = reg.resolve(slug)
    assert resolved is not None
    assert resolved.exists is False
    assert slug in [e.slug for e in reg.repos]  # still listable


def test_missing_or_unreadable_registry_yields_empty_usable_registry(home, tmp_path):
    """AC 25: a missing OR unreadable registry file gives an empty, usable
    registry (no uncaught error); the next successful register writes valid v1."""
    empty = load_registry()  # file does not exist yet
    assert empty.repos == []
    assert empty.resolve("anything") is None

    registry_file(home).parent.mkdir(parents=True, exist_ok=True)
    registry_file(home).write_text("{ das ist : kein json ]", encoding="utf-8")
    still_empty = load_registry()
    assert still_empty.repos == []

    repo = tmp_path / "r"
    repo.mkdir()
    register_repo(repo)
    data = json.loads(registry_file(home).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["repos"]) == 1


def test_aborted_write_leaves_previous_registry_intact(home, tmp_path, monkeypatch):
    """AC 26: a write that fails before the atomic replace leaves the previous
    registry content untouched."""
    repo1 = tmp_path / "one"
    repo1.mkdir()
    register_repo(repo1)
    before = registry_file(home).read_bytes()

    def boom(src, dst):
        raise OSError("replace kaputt")

    monkeypatch.setattr("adw.gui.registry.os.replace", boom)
    repo2 = tmp_path / "two"
    repo2.mkdir()
    with pytest.raises(OSError):
        register_repo(repo2)

    assert registry_file(home).read_bytes() == before  # previous content intact


def test_adw_run_registers_its_resolved_target_repo(home, target_repo):
    """AC 22: a dry run registers its resolved target repo in ~/.adw/repos.json."""
    result = cli.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Reg", "--dry-run", "--no-approval"],
    )
    assert result.exit_code == 0, result.output
    reg = load_registry()
    assert str(target_repo.resolve()) in [e.path for e in reg.repos]


def test_registry_error_does_not_block_the_run(home, target_repo):
    """AC 22: a registry failure (here: ~/.adw is a file, so the write cannot
    create it) is fail-open — the run completes normally."""
    (home / ".adw").write_text("bin kein Verzeichnis", encoding="utf-8")
    result = cli.invoke(
        app,
        ["run", "--repo", str(target_repo), "--issue", "Reg", "--dry-run", "--no-approval"],
    )
    assert result.exit_code == 0, result.output
    assert RunState.find_latest(target_repo).phase == "done"
