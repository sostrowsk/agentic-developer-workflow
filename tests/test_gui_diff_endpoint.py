"""RED tests for Aufgabe B — the read-only snapshot-diff endpoint.

``GET /api/runs/{repo}/{run_id}/diff?from=REF&to=REF`` computes the diff between
two snapshot refs of THIS run with ``git diff`` and returns the per-file +/-
counts and the unified patch (AC-B1). Refs are accepted ONLY if their exact name
appears in a ``snapshot`` event of exactly this run (AC-B2) — a pattern match
alone is insufficient. Rejections are 400 or 404, never 5xx, and never execute
git (AC-B2/B3). Allowed execution mirrors the orchestrator: no shell, disabled
hooks, ``safe_env()``, a timeout, reads only (AC-B4).

Derived from .adw/spec.md (AC-B1..B4, B7), .adw/contract.yaml
(getRunSnapshotDiff, DiffResponse, x-adw-validation) and .adw/plan.md §3. Tested
against temp git repos via FastAPI's TestClient. RED until the diff route exists.
"""

import os
import subprocess

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    build_diff_run,
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _diff_client(tmp_path, *, large=False):
    info = build_diff_run(tmp_path / "repo", RUN_ID, large=large)
    client = TestClient(create_app(repos=[str(info["repo"])]))
    info["slug"] = _slug_for(info["repo"])
    info["url"] = f"/api/runs/{info['slug']}/{RUN_ID}/diff"
    return client, info


def test_diff_returns_exact_schema_counts_binary_and_git_order(home, tmp_path):  # noqa: F811
    """AC-B1: two real snapshot refs yield HTTP 200 with the exact shape
    ``{"files":[{path,additions,deletions}],"patch":str}`` — per-file +/- counts,
    a binary file as null/null, and the files in the order git reports them."""
    client, info = _diff_client(tmp_path)

    resp = client.get(info["url"], params={"from": info["ref1"], "to": info["ref2"]})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {"files", "patch"}
    assert isinstance(body["patch"], str) and "example.py" in body["patch"]
    for f in body["files"]:
        assert set(f) == {"path", "additions", "deletions"}

    by_path = {f["path"]: f for f in body["files"]}
    assert by_path["src/example.py"]["additions"] == 3
    assert by_path["src/example.py"]["deletions"] == 1
    assert by_path["assets/logo.bin"]["additions"] is None   # binary -> null
    assert by_path["assets/logo.bin"]["deletions"] is None
    # git reports the changed files in tree order (assets/ before src/).
    assert [f["path"] for f in body["files"]] == ["assets/logo.bin", "src/example.py"]


def test_diff_of_a_ref_against_itself_is_the_empty_diff(home, tmp_path):  # noqa: F811
    """AC-B1: an empty diff (no changes between the two refs) is HTTP 200 with
    exactly ``{"files": [], "patch": ""}`` — not a 204, not an error."""
    client, info = _diff_client(tmp_path)

    resp = client.get(info["url"], params={"from": info["ref1"], "to": info["ref1"]})
    assert resp.status_code == 200
    assert resp.json() == {"files": [], "patch": ""}


def test_missing_or_malformed_params_are_client_errors(home, tmp_path):  # noqa: F811
    """AC-B3: a missing or malformed ``from``/``to`` yields 400 or 404, never 5xx
    (and never a 422 from an unvalidated required query param)."""
    client, info = _diff_client(tmp_path)

    for params in (
        {},                                        # both missing
        {"from": info["ref1"]},                    # to missing
        {"to": info["ref2"]},                      # from missing
        {"from": "", "to": ""},                    # both empty
        {"from": info["ref1"], "to": ""},          # to empty
    ):
        resp = client.get(info["url"], params=params)
        assert resp.status_code in (400, 404), (params, resp.status_code)


def test_refs_absent_from_this_runs_snapshot_events_are_rejected(home, tmp_path):  # noqa: F811
    """AC-B2: any ref not drawn from THIS run's snapshot events is rejected with
    400/404 — arbitrary refs, a real-but-unlisted snapshot ref of this run, a
    foreign run's ref, a bare sha, revision ranges and option-like strings. Never
    5xx. A ref matching the ``refs/adw/<run_id>/<seq>`` shape but absent from the
    event log (``unlisted``) proves a pattern match alone is insufficient."""
    client, info = _diff_client(tmp_path)
    good = info["ref2"]

    bad_values = [
        "refs/heads/main",                                   # arbitrary real ref
        info["unlisted"],                                    # right shape, not in log
        info["foreign"],                                     # another run's ref
        info["head_sha"],                                    # a bare commit sha
        "HEAD",                                              # a revision
        f"{info['ref1']}..{info['ref2']}",                   # a range expression
        f"{info['ref1']}...{info['ref2']}",                  # a symmetric range
        "--output=/tmp/x",                                   # option-like
        "-p",                                                # option-like
        f"{info['ref1']} {info['ref2']}",                    # two refs in one value
    ]
    def status(frm, to):
        return client.get(info["url"], params={"from": frm, "to": to}).status_code

    for bad in bad_values:
        # A rejected ref in either position must be refused.
        assert status(bad, good) in (400, 404), bad
        assert status(good, bad) in (400, 404), bad


def test_rejected_refs_never_execute_git(home, tmp_path, monkeypatch):  # noqa: F811
    """AC-B2: a ref outside the allowlist is refused BEFORE git runs — a git spy
    records no invocation on rejection, but does record one for an allowed pair."""
    client, info = _diff_client(tmp_path)

    real_run = subprocess.run
    calls = []

    def spy(cmd, *a, **k):
        calls.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)

    rejected = client.get(info["url"], params={"from": info["unlisted"], "to": info["ref2"]})
    assert rejected.status_code in (400, 404)
    assert not any(isinstance(c, (list, tuple)) and c and c[0] == "git" for c in calls)

    calls.clear()
    allowed = client.get(info["url"], params={"from": info["ref1"], "to": info["ref2"]})
    assert allowed.status_code == 200
    assert any(isinstance(c, (list, tuple)) and c and c[0] == "git" for c in calls)


def _subcommand(cmd):
    """The git subcommand of a ``["git", "-C", cwd, "-c", opt, <sub>, ...]`` argv,
    skipping the leading options — so the read-only-ness is checked structurally."""
    it = iter(cmd[1:])
    for tok in it:
        if tok in ("-C", "-c"):
            next(it, None)
            continue
        if tok.startswith("-"):
            continue
        return tok
    return None


# git subcommands that create/update/delete refs, move HEAD or touch the worktree —
# none may appear when the endpoint merely computes a diff (AC-B4, read-only).
_MUTATING_SUBCOMMANDS = {
    "update-ref", "update-index", "symbolic-ref", "commit", "commit-tree",
    "write-tree", "checkout", "switch", "restore", "reset", "add", "rm", "mv",
    "branch", "tag", "merge", "rebase", "cherry-pick", "revert", "stash", "clean",
    "apply", "am", "push", "fetch", "pull", "clone", "init", "config", "gc",
    "repack", "prune", "worktree", "fast-import", "filter-branch",
}


def test_allowed_diff_uses_safe_read_only_git_invocation(home, tmp_path, monkeypatch):  # noqa: F811
    """AC-B4: an allowed diff runs git like the orchestrator — a list argv (no
    shell), ``-c core.hooksPath=/dev/null``, a ``timeout``, a filtered
    ``safe_env()`` (no leaked secret), and only the read-only ``diff`` subcommand
    (no ref- or worktree-mutating subcommand)."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-leak")
    client, info = _diff_client(tmp_path)

    real_run = subprocess.run
    recorded = []

    def spy(cmd, *a, **k):
        recorded.append((cmd, k))
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)

    resp = client.get(info["url"], params={"from": info["ref1"], "to": info["ref2"]})
    assert resp.status_code == 200

    git_calls = [(cmd, k) for cmd, k in recorded if isinstance(cmd, (list, tuple))
                 and cmd and cmd[0] == "git"]
    assert git_calls
    for cmd, k in git_calls:
        assert "-c" in cmd and "core.hooksPath=/dev/null" in cmd
        assert _subcommand(cmd) not in _MUTATING_SUBCOMMANDS  # read-only only
        assert k.get("shell") in (None, False)     # never a shell
        assert k.get("timeout") is not None        # bounded
        env = k.get("env")
        assert isinstance(env, dict)
        assert "AWS_SECRET_ACCESS_KEY" not in env  # safe_env filtered the secret
        assert "PATH" in env
    # The actual diff is computed with the read-only ``diff`` subcommand.
    assert any(_subcommand(cmd) == "diff" for cmd, _ in git_calls)


def test_diff_endpoint_does_not_mutate_refs_head_or_worktree(home, tmp_path):  # noqa: F811
    """AC-B4 / read-only: computing a diff creates, updates or deletes no ref, and
    leaves HEAD and the worktree status byte-identical."""
    client, info = _diff_client(tmp_path)
    repo = info["repo"]

    def snapshot():
        return (
            subprocess.run(["git", "-C", str(repo), "for-each-ref"],
                           capture_output=True, text=True, timeout=60).stdout,
            subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=60).stdout,
            subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=60).stdout,
        )

    before = snapshot()
    resp = client.get(info["url"], params={"from": info["ref1"], "to": info["ref2"]})
    assert resp.status_code == 200
    assert snapshot() == before


def test_diff_endpoint_keeps_containment_behavior(home, tmp_path):  # noqa: F811
    """AC-B2/B3 + existing containment: an unknown slug is 404, a run_id violating
    RUN_ID_RE is 400, a valid-format but absent run is 404 — never 5xx, even with
    otherwise well-formed refs."""
    client, info = _diff_client(tmp_path)
    good = {"from": info["ref1"], "to": info["ref2"]}

    assert client.get(f"/api/runs/no-such-slug/{RUN_ID}/diff", params=good).status_code == 404
    assert client.get(f"/api/runs/{info['slug']}/zzzzzzzz/diff", params=good).status_code == 400
    assert client.get(f"/api/runs/{info['slug']}/deadbeef/diff", params=good).status_code == 404


def test_malformed_ref_recorded_in_event_log_is_rejected_without_git(home, tmp_path, monkeypatch):  # noqa: F811,E501
    """AC-B2/B3 (P1): a value must have the exact ``refs/adw/<run_id>/<seq>``
    structure, not merely appear in a snapshot event. A crafted/corrupt snapshot
    event whose ``ref`` is option-like or range-like must be rejected (400/404)
    and NEVER reach git — even though it IS in the event-log allowlist — so it can
    never be interpreted as a git option/revision or cause a write."""
    repo = tmp_path / "repo"
    repo.mkdir()
    option_like = "--output=/tmp/pwned"
    range_like = "refs/adw/aaaa1111/1..refs/adw/aaaa1111/2"
    lines = [
        rec(1, "run", "start", "R", None, payload=run_start_payload("crafted")),
        rec(2, "snapshot", "point", "R",
            payload={"lane": "backend", "tree": "t", "ref": option_like, "label": "a"}),
        rec(3, "snapshot", "point", "R",
            payload={"lane": "backend", "tree": "t", "ref": range_like, "label": "b"}),
        rec(4, "run", "end", "R", None, payload=run_end_payload("done")),
    ]
    write_run(repo, "aaaa1111", lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    url = f"/api/runs/{slug}/aaaa1111/diff"

    real_run = subprocess.run
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, *a, **k: (calls.append(cmd), real_run(cmd, *a, **k))[1]
    )

    for bad in (option_like, range_like):
        # Present in the log, but not a structural snapshot ref -> rejected.
        assert client.get(url, params={"from": bad, "to": bad}).status_code in (400, 404), bad
    assert not any(isinstance(c, (list, tuple)) and c and c[0] == "git" for c in calls)


def test_large_diff_patch_and_counts_are_fully_reachable(home, tmp_path):  # noqa: F811
    """AC-B7 (server side): the endpoint returns the FULL large patch — at least
    100 changed files and at least 1500 changed lines in total — so the display's
    windowing always has the complete patch to draw on. No 5xx on a big diff."""
    client, info = _diff_client(tmp_path, large=True)

    resp = client.get(info["url"], params={"from": info["ref1"], "to": info["ref2"]})
    assert resp.status_code == 200
    body = resp.json()

    text_files = [f for f in body["files"] if f["additions"] is not None]
    assert len(body["files"]) >= 100
    total_lines = sum(f["additions"] + f["deletions"] for f in text_files)
    assert total_lines >= 1500
    assert body["patch"]  # the unified patch is present in full
