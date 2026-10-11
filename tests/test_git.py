"""run_git: the one hardened git invocation (hooks off, env whitelist, timeout)."""

import subprocess

from adw.git import run_git


def test_run_git_disables_hooks_whitelists_env_and_passes_timeout(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setenv("SOME_SECRET_TOKEN", "s3cr3t")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_git(tmp_path, "status", "--porcelain", timeout=7)
    assert seen["argv"] == [
        "git", "-C", str(tmp_path), "-c", "core.hooksPath=/dev/null", "status", "--porcelain"
    ]
    assert seen["kwargs"]["timeout"] == 7
    assert seen["kwargs"]["text"] is True and seen["kwargs"]["capture_output"] is True
    assert "SOME_SECRET_TOKEN" not in seen["kwargs"]["env"]
    assert result.stdout == "ok"


def test_run_git_uses_the_given_env_verbatim(monkeypatch, tmp_path):
    seen = {}
    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_git(tmp_path, "write-tree", timeout=1, env={"GIT_INDEX_FILE": "/x"})
    assert seen["env"] == {"GIT_INDEX_FILE": "/x"}
