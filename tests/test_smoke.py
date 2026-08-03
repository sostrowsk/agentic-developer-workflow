import tomllib
from pathlib import Path

import adw


def test_package_importable():
    assert adw.__version__


def test_version_matches_pyproject():
    # Regressionstest zum Release-Prozess: __version__ kommt aus den
    # Package-Metadaten — ein Bump in pyproject.toml muss durchschlagen.
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert adw.__version__ == expected


def test_target_repo_fixture_is_valid_git_repo(target_repo):
    assert (target_repo / ".git").is_dir()
    assert (target_repo / ".adw" / "config.yaml").is_file()
