"""Geteilte Fixtures: echtes Mini-Git-Repo als ADW-Ziel-Repo."""

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


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    """Echtes Git-Repo mit staging-Branch, einem Commit und gültiger .adw/config.yaml."""
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
