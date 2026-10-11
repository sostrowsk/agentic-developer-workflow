"""Shared fixtures: real mini Git repo as the ADW target repo."""

import subprocess
from pathlib import Path

import pytest

DEFAULT_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
ci:
  provider: gitlab
  staging_job: deploy-staging
"""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def write_config(repo: Path, content: str = DEFAULT_CONFIG) -> Path:
    cfg = repo / ".adw" / "config.yaml"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(content)
    return cfg


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path_factory, monkeypatch):
    """Aufgabe A: no test may pollute the real ~/.adw/repos.json.

    Point the registry at a throwaway file for EVERY test (not just the
    registry's own), so ``adw run`` / auto-registration writes there. Tests that
    need to inspect the registry (their own ``home`` fixture) override this with
    their chosen location. The override is a plain env var honoured by
    ``adw.gui.registry._registry_path``; the production default is untouched.
    """
    registry = tmp_path_factory.mktemp("adw-registry") / "repos.json"
    monkeypatch.setenv("ADW_REGISTRY_PATH", str(registry))


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    """Real Git repo with a staging branch, one commit, and a valid .adw/config.yaml."""
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "staging")
    git(repo, "config", "user.email", "adw-test@example.com")
    git(repo, "config", "user.name", "ADW Test")
    (repo / "README.md").write_text("# target\n")
    write_config(repo)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    return repo


@pytest.fixture(autouse=True)
def _no_live_model_probe(tmp_path_factory, monkeypatch):
    """The model freshness check must never call the real SDK or read the real
    ~/.codex catalog in tests: aliases resolve to the pinned models, no Codex
    catalog, and the cache lives in a throwaway file."""
    from adw import agents, codex, models

    pinned = {"opus": agents.OPUS, "fable": agents.FABLE, "sonnet": agents.SONNET}
    monkeypatch.setattr(models, "resolve_claude_alias", lambda alias: pinned[alias])
    monkeypatch.setattr(models, "load_codex_catalog", lambda: None)
    cache = tmp_path_factory.mktemp("adw-model-check") / "model-check.json"
    monkeypatch.setenv("ADW_MODEL_CHECK_PATH", str(cache))
    assert codex.CODEX_MODEL
