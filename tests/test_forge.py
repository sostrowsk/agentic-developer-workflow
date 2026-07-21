"""Forge detection: is the target repo a GitLab or GitHub project?"""

import pytest

from adw.forge import ForgeError, detect_forge
from tests.conftest import git


def test_config_override_wins_over_remote(target_repo):
    git(target_repo, "remote", "add", "origin", "git@github.com:acme/demo.git")
    assert detect_forge(target_repo, override="gitlab") == "gitlab"


def test_github_remote_is_detected(target_repo):
    git(target_repo, "remote", "add", "origin", "git@github.com:acme/demo.git")
    assert detect_forge(target_repo, override=None) == "github"


def test_gitlab_remote_is_detected(target_repo):
    git(target_repo, "remote", "add", "origin", "https://gitlab.com/acme/demo.git")
    assert detect_forge(target_repo, override=None) == "gitlab"


def test_self_hosted_gitlab_hostname_is_detected(target_repo):
    git(target_repo, "remote", "add", "origin", "git@gitlab.acme-corp.de:x/y.git")
    assert detect_forge(target_repo, override=None) == "gitlab"


def test_unknown_host_without_override_fails_fast(target_repo):
    git(target_repo, "remote", "add", "origin", "git@code.acme-corp.de:x/y.git")
    with pytest.raises(ForgeError, match="ci.provider"):
        detect_forge(target_repo, override=None)


def test_missing_remote_without_override_fails_fast(target_repo):
    with pytest.raises(ForgeError, match="origin|ci.provider"):
        detect_forge(target_repo, override=None)
