import adw


def test_package_importable():
    assert adw.__version__


def test_target_repo_fixture_is_valid_git_repo(target_repo):
    assert (target_repo / ".git").is_dir()
    assert (target_repo / ".adw" / "config.yaml").is_file()
