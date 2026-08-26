"""RED tests for the change-scope projection — the additive, purely DERIVED
``change_scope`` object on ``GET /api/runs/{repo}/{run_id}`` and its read-only
rendering on the HTML run-detail page.

``change_scope`` places two facts side by side WITHOUT any judgement (S5): the
files each observed lane actually changed (diff between its lowest- and
highest-seq snapshot, +/- counts, via the EXISTING snapshot/diff/numstat logic —
no new git path, E5) and the contract's declared scope as readable YAML text (all
top-level ``x-adw-*`` blocks, or ``null``). It never marks a file "in scope" or
"out of scope" (E1) and never causes a 5xx on an otherwise successful detail
request (S3).

Derived from .adw/spec.md (AC-1…AC-9), .adw/contract.yaml
(ChangeScope/ChangeScopeLane/ChangeScopeFile, S1…S7) and .adw/plan.md (B1…B6).
Git-touching cases run against temp repos like tests/test_gui_diff_endpoint.py /
tests/test_gui_diff_pairing.py. RED until ``_run_detail`` hangs ``change_scope`` on
its result and the template renders the block.
"""

import os
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.i18n import CATALOG
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

# The chrome-label keys this feature adds to BOTH language blocks (B6). Scope text
# and file paths are CONTENT and are never translated (S6).
CHANGE_SCOPE_LABEL_KEYS = [
    "change_scope_title",
    "change_scope_col_add",
    "change_scope_col_del",
    "change_scope_binary",
    "change_scope_no_diff",
    "change_scope_no_files",
    "change_scope_no_run_diff",
    "change_scope_declared",
    "change_scope_no_declared",
]


# --- helpers -------------------------------------------------------------------


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.strip()


def _init_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "adw-test@example.com")
    _git(repo, "config", "user.name", "ADW Test")
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")


def _commit_ref(repo, files, ref, *, allow_empty=False):
    """Apply ``files`` (path -> str/bytes content, or None to delete), commit, and
    point ``ref`` at HEAD — the same shape of real snapshot ref the orchestrator
    records."""
    for rel, content in files.items():
        p = repo / rel
        if content is None:
            if p.exists():
                p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (bytes, bytearray)):
            p.write_bytes(bytes(content))
        else:
            p.write_text(content)
    _git(repo, "add", "-A")
    if allow_empty:
        _git(repo, "commit", "--allow-empty", "-m", f"snap {ref}")
    else:
        _git(repo, "commit", "-m", f"snap {ref}")
    _git(repo, "update-ref", ref, "HEAD")
    return ref


def _lane_start(seq, lane, parent="PB"):
    return rec(seq, "lane", "start", f"L{lane}", parent, sec=seq, lane=lane, payload={
        "name": lane, "branch": f"adw/{lane}", "worktree": "wt",
        "base_sha": None, "ports": {}})


def _snap(seq, lane, ref, label="s"):
    return rec(seq, "snapshot", "point", "L", sec=seq, lane=lane,
               payload={"lane": lane, "tree": "t", "ref": ref, "label": label})


def _ref(seq):
    return f"refs/adw/{RUN_ID}/{seq}"


def _wrap(inner):
    """A minimal run scaffold (start/phase around ``inner`` events, then end)."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Change scope")),
        rec(2, "phase", "start", "PB", "R", sec=2,
            payload={"name": "build", "from_phase": "build"}),
        *inner,
        rec(97, "phase", "end", "PB", "R", sec=97, payload={"name": "build", "to_phase": "done"}),
        rec(98, "run", "end", "R", None, sec=98, payload=run_end_payload("done")),
    ]


def _client(repo):
    return TestClient(create_app(repos=[str(repo)]))


def _detail(client, slug, run_id=RUN_ID):
    r = client.get(f"/api/runs/{slug}/{run_id}")
    assert r.status_code == 200, r.status_code
    return r.json()


def _lanes(client, slug, run_id=RUN_ID):
    return _detail(client, slug, run_id)["change_scope"]["lanes"]


def _by_lane(lanes):
    return {ln["lane"]: ln for ln in lanes}


def _write_contract(repo, text, run_id=RUN_ID):
    (repo / ".adw" / "runs" / run_id / "contract.yaml").write_text(text, encoding="utf-8")


def _page(client, slug, run_id=RUN_ID, lang="en"):
    r = client.get(f"/runs/{slug}/{run_id}", params={"lang": lang})
    assert r.status_code == 200, r.status_code
    return r.text


# --- AC-9 / AC-5: additive, exactly the pinned keys, no judgement ---------------


def test_change_scope_is_present_additive_and_carries_only_pinned_keys(home, tmp_path):  # noqa: F811
    """AC-9/AC-5/S7: ``change_scope`` is ALWAYS present with exactly
    ``{lanes, declared_scope}``; every lane object has exactly
    ``{lane, diff_available, files}`` and every file exactly
    ``{path, additions, deletions}`` — no in-scope/out-of-scope/violation field
    anywhere (additionalProperties:false). All pre-existing detail fields remain."""
    info = build_diff_run(tmp_path / "repo", RUN_ID)
    client = _client(info["repo"])
    detail = _detail(client, _slug_for(info["repo"]))

    cs = detail["change_scope"]
    assert set(cs) == {"lanes", "declared_scope"}
    assert cs["lanes"], "the single backend lane must be observed"
    for ln in cs["lanes"]:
        assert set(ln) == {"lane", "diff_available", "files"}
        if ln["files"] is not None:
            for f in ln["files"]:
                assert set(f) == {"path", "additions", "deletions"}

    for key in ("run", "phases", "tree", "latest_context", "problems", "raw"):
        assert key in detail, key


# --- AC-1 / AC-2: the per-lane file list from the existing diff logic ------------


def test_lane_diff_lists_files_with_counts_binary_null_in_git_order(home, tmp_path):  # noqa: F811
    """AC-1/AC-2: a lane with two snapshots carries the diff of its first vs last
    snapshot — per file ``path``/``additions``/``deletions`` from the existing
    numstat logic, a binary file as null/null, in git's report order."""
    info = build_diff_run(tmp_path / "repo", RUN_ID)  # backend: +3/-1 text + binary
    client = _client(info["repo"])
    lanes = _lanes(client, _slug_for(info["repo"]))

    assert len(lanes) == 1
    backend = lanes[0]
    assert backend["lane"] == "backend"
    assert backend["diff_available"] is True

    files = backend["files"]
    assert [f["path"] for f in files] == ["assets/logo.bin", "src/example.py"]
    by_path = {f["path"]: f for f in files}
    assert by_path["src/example.py"]["additions"] == 3
    assert by_path["src/example.py"]["deletions"] == 1
    assert by_path["assets/logo.bin"]["additions"] is None   # binary -> null
    assert by_path["assets/logo.bin"]["deletions"] is None


def test_diff_pairs_the_lowest_and_highest_seq_snapshot_only(home, tmp_path):  # noqa: F811
    """AC-1/S2: with THREE snapshots the diff is strictly first (lowest seq) vs last
    (highest seq); the middle snapshot is never an endpoint — a file that exists
    only in the middle tree never appears in the reported diff."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_ref(repo, {"keep.py": "a\nb\nc\nd\ne\n"}, _ref(1))                     # low
    _commit_ref(repo, {"keep.py": "a\nb\nc\nd\ne\n", "only_mid.py": "x\n"}, _ref(2))  # mid
    _commit_ref(repo, {"keep.py": "a\nb\nX\nd\ne\nf\ng\n", "only_mid.py": None}, _ref(3))  # high

    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", _ref(1)),
        _snap(6, "backend", _ref(2)),   # middle by seq -> never an endpoint
        _snap(8, "backend", _ref(3)),
    ]), phase="done")
    lanes = _lanes(_client(repo), _slug_for(repo))

    backend = _by_lane(lanes)["backend"]
    assert backend["diff_available"] is True
    paths = [f["path"] for f in backend["files"]]
    assert paths == ["keep.py"]                     # low vs high only
    assert "only_mid.py" not in paths               # the middle tree is not compared
    keep = backend["files"][0]
    assert keep["additions"] == 3 and keep["deletions"] == 1


def test_snapshots_of_other_lanes_are_never_mixed_into_a_lane(home, tmp_path):  # noqa: F811
    """AC-1/S2: each lane diffs only its OWN snapshots — a two-lane run keeps the
    backend and frontend file lists strictly separate."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_ref(repo, {"backend/a.py": "a\n"}, _ref(1))
    _commit_ref(repo, {"backend/a.py": "a\nb\n"}, _ref(2))
    _commit_ref(repo, {"frontend/x.js": "1\n"}, _ref(3))
    _commit_ref(repo, {"frontend/x.js": "1\n2\n"}, _ref(4))

    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", _ref(1)),
        _snap(6, "backend", _ref(2)),
        _lane_start(7, "frontend"),
        _snap(8, "frontend", _ref(3)),
        _snap(10, "frontend", _ref(4)),
    ]), phase="done")
    by = _by_lane(_lanes(_client(repo), _slug_for(repo)))

    assert [f["path"] for f in by["backend"]["files"]] == ["backend/a.py"]
    assert [f["path"] for f in by["frontend"]["files"]] == ["frontend/x.js"]


def test_lanes_ordered_by_first_observation_one_entry_per_name(home, tmp_path):  # noqa: F811
    """AC-1/S1: the lane list is ordered by first observation (smallest seq across
    lane spans and snapshot events); a lane observed several times (span twice plus
    snapshots) yields exactly one entry. Here frontend (seq 3) precedes backend
    (seq 5)."""
    repo = tmp_path / "repo"  # plain dir: diff availability is irrelevant to order
    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "frontend"),
        _lane_start(5, "backend"),
        _snap(6, "backend", _ref(1)),
        _snap(7, "frontend", _ref(2)),
        _snap(8, "backend", _ref(3)),
        _snap(9, "frontend", _ref(4)),
        _lane_start(10, "backend"),   # a repeated observation of the same name
    ]), phase="done")
    lanes = _lanes(_client(repo), _slug_for(repo))

    assert [ln["lane"] for ln in lanes] == ["frontend", "backend"]


def test_snapshot_only_lane_appears_and_invalid_snapshot_events_are_ignored(home, tmp_path):  # noqa: F811,E501
    """AC-1/S1: a lane observed ONLY via valid snapshots (no lane span) appears with
    its diff; snapshot events whose ref fails the structural validation
    (option-like, foreign run, range-like) contribute neither a lane nor a pair — so
    a lane whose only snapshots are malformed has no diff, and a lane declared only
    by a malformed snapshot never appears at all."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_ref(repo, {"solo/a.py": "a\nb\nc\n"}, _ref(1))
    _commit_ref(repo, {"solo/a.py": "a\nX\nc\nd\n"}, _ref(2))

    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", "--output=/tmp/pwned"),               # option-like -> ignored
        _snap(5, "backend", "refs/adw/zzzzzzzz/1"),               # foreign run -> ignored
        _snap(6, "solo", _ref(1)),                                # valid, lane has no span
        _snap(7, "ghost", f"{_ref(1)}..{_ref(2)}"),               # range-like -> ignored
        _snap(8, "solo", _ref(2)),
    ]), phase="done")
    by = _by_lane(_lanes(_client(repo), _slug_for(repo)))

    assert set(by) == {"backend", "solo"}                          # ghost never appears
    assert by["backend"]["diff_available"] is False               # only malformed snaps
    assert by["backend"]["files"] is None
    assert by["solo"]["diff_available"] is True
    assert [f["path"] for f in by["solo"]["files"]] == ["solo/a.py"]


# --- AC-6 / AC-7: canonical shapes for unusable and empty diffs -----------------


def test_zero_and_one_snapshot_lanes_have_no_diff_and_no_run_diff(home, tmp_path):  # noqa: F811
    """AC-6/AC-7: an observed lane with no snapshot (span only) and one with a
    single snapshot both carry the canonical unavailable shape
    ``diff_available: false`` / ``files: null`` (never ``[]``, never an omitted
    field); when no lane has a usable pair, every entry is unavailable — the API
    condition behind the "no run diff" view."""
    repo = tmp_path / "repo"  # no git needed: no diff is ever attempted
    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "backend"),                # span only -> 0 snapshots
        _lane_start(5, "frontend"),
        _snap(6, "frontend", _ref(1)),            # exactly one snapshot
    ]), phase="done")
    by = _by_lane(_lanes(_client(repo), _slug_for(repo)))

    for name in ("backend", "frontend"):
        assert by[name]["diff_available"] is False
        assert by[name]["files"] is None
    assert all(ln["diff_available"] is False for ln in by.values())


def test_available_diff_without_changes_is_empty_list_not_null(home, tmp_path):  # noqa: F811
    """AC-7/S3: a produced diff with no changed files is ``diff_available: true``
    with ``files: []`` — distinguishable from the unavailable ``files: null`` — so
    the view can say clearly "no changed files were found"."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_ref(repo, {"x.py": "a\n"}, _ref(1))
    _commit_ref(repo, {}, _ref(2), allow_empty=True)   # identical tree -> empty diff

    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", _ref(1)),
        _snap(6, "backend", _ref(2)),
    ]), phase="done")
    backend = _by_lane(_lanes(_client(repo), _slug_for(repo)))["backend"]

    assert backend["diff_available"] is True
    assert backend["files"] == []


# --- AC-8: robustness against git failure ---------------------------------------


def test_diff_failure_despite_a_pair_stays_200_and_isolates_the_lane(home, tmp_path):  # noqa: F811
    """AC-8/S3: when a lane's diff fails despite a snapshot pair (a removed ref
    object), that lane falls back to ``diff_available: false`` / ``files: null`` —
    never a false empty diff — while the response stays 200 and other lanes keep
    their available diffs."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_ref(repo, {"backend/a.py": "a\n"}, _ref(1))
    _commit_ref(repo, {"backend/a.py": "a\nb\n"}, _ref(2))
    _commit_ref(repo, {"frontend/x.js": "1\n"}, _ref(3))
    _commit_ref(repo, {"frontend/x.js": "1\n2\n"}, _ref(4))
    _git(repo, "update-ref", "-d", _ref(2))   # backend's high ref name is gone

    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", _ref(1)),
        _snap(6, "backend", _ref(2)),
        _lane_start(7, "frontend"),
        _snap(8, "frontend", _ref(3)),
        _snap(10, "frontend", _ref(4)),
    ]), phase="done")
    client = _client(repo)
    r = client.get(f"/api/runs/{_slug_for(repo)}/{RUN_ID}")
    assert r.status_code == 200          # never a 5xx, never a bubbled-up 404
    by = _by_lane(r.json()["change_scope"]["lanes"])

    assert by["backend"]["diff_available"] is False and by["backend"]["files"] is None
    assert by["frontend"]["diff_available"] is True
    assert [f["path"] for f in by["frontend"]["files"]] == ["frontend/x.js"]


@pytest.mark.parametrize("exc", [
    OSError("exec failed"),                                       # ENOENT/EACCES-style
    subprocess.TimeoutExpired(cmd=["git", "diff"], timeout=1),    # SubprocessError family
], ids=["oserror", "timeout"])
def test_subprocess_error_during_diff_is_unavailable_not_5xx(home, tmp_path, monkeypatch, exc):  # noqa: F811,E501
    """AC-8/S3: an OS-level execution error or a timeout while diffing is caught per
    lane — the lane becomes ``diff_available: false`` / ``files: null`` and the
    detail response stays 200 (never a 5xx, never an invented diff)."""
    info = build_diff_run(tmp_path / "repo", RUN_ID)
    client = _client(info["repo"])

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    r = client.get(f"/api/runs/{_slug_for(info['repo'])}/{RUN_ID}")
    assert r.status_code == 200
    lanes = r.json()["change_scope"]["lanes"]
    assert lanes
    assert all(ln["diff_available"] is False and ln["files"] is None for ln in lanes)


# --- AC-3 / AC-4: the declared contract scope as YAML text ----------------------


def test_declared_scope_serializes_only_x_adw_blocks_in_document_order(home, tmp_path):  # noqa: F811
    """AC-3: ``declared_scope`` is a semantically equivalent YAML serialization of
    all top-level ``x-adw-*`` entries in document order — keys, values and nesting
    unchanged, no rename/merge/normalization. Other top-level entries are dropped,
    and a non-string top-level key is ignored (never a prefix operation), not a
    crash."""
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _wrap([_lane_start(3, "backend")]), phase="done")
    _write_contract(repo, (
        "openapi: 3.1.0\n"
        "info:\n"
        "  title: Should not appear\n"
        "x-adw-surfaces:\n"
        "  - GET /api/runs/{repo}/{run_id}\n"
        "x-adw-invariants:\n"
        "  - read-only\n"
        "  - no write\n"
        "42: ignored non-string key\n"
        "paths:\n"
        "  /x: {}\n"
        "x-adw-added_events: []\n"
    ))
    declared = _detail(_client(repo), _slug_for(repo))["change_scope"]["declared_scope"]

    assert declared is not None
    loaded = yaml.safe_load(declared)
    assert list(loaded.keys()) == ["x-adw-surfaces", "x-adw-invariants", "x-adw-added_events"]
    assert loaded == {
        "x-adw-surfaces": ["GET /api/runs/{repo}/{run_id}"],
        "x-adw-invariants": ["read-only", "no write"],
        "x-adw-added_events": [],
    }
    # Non-x-adw top-level entries never leak into the text.
    assert "openapi" not in declared and "paths" not in declared
    assert "Should not appear" not in declared


@pytest.mark.parametrize("contract", [
    None,                                                    # contract.yaml missing
    "- just\n- a\n- sequence\n",                             # valid YAML but not a mapping
    "openapi: 3.1.0\ninfo:\n  title: X\n",                   # mapping without any x-adw-* key
    "1: one\ntrue: yes\nnull: none\n",                       # only non-string keys
], ids=["missing", "not_mapping", "no_x_adw_key", "non_string_keys"])
def test_declared_scope_is_null_when_no_readable_x_adw_scope(home, tmp_path, contract):  # noqa: F811
    """AC-4: a missing contract, a non-mapping document, a mapping without any
    ``x-adw-*`` key, and a mapping whose only keys are non-strings each yield
    ``declared_scope: null`` — no error, no 5xx, a neutral absence."""
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _wrap([_lane_start(3, "backend")]), phase="done")
    if contract is not None:
        _write_contract(repo, contract)

    detail = _detail(_client(repo), _slug_for(repo))     # 200, not 5xx
    assert detail["change_scope"]["declared_scope"] is None


def test_declared_scope_from_a_boundary_escaping_symlink_is_null(home, tmp_path):  # noqa: F811
    """AC-4/E5: a ``contract.yaml`` that is a symlink escaping the run directory is
    absent per the existing containment discipline — its target is never read and
    ``declared_scope`` is null."""
    outside = tmp_path / "outside_contract.yaml"
    outside.write_text("x-adw-surfaces:\n  - LEAKEDSURFACE\n", encoding="utf-8")
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _wrap([_lane_start(3, "backend")]), phase="done")
    link = repo / ".adw" / "runs" / RUN_ID / "contract.yaml"
    link.symlink_to(outside)

    detail = _detail(_client(repo), _slug_for(repo))
    assert detail["change_scope"]["declared_scope"] is None
    assert "LEAKEDSURFACE" not in str(detail)            # the target never reached


# --- S6: read-only rendering in the run detail ----------------------------------


def test_run_detail_renders_files_and_declared_scope(home, tmp_path):  # noqa: F811
    """S6/AC-3/AC-9: the run-detail page renders the change-scope block read-only —
    the changed file paths, the binary label for a null count, and the declared
    scope as its verbatim content — with no "no diff"/"no declared scope"
    fallbacks when both are present."""
    info = build_diff_run(tmp_path / "repo", RUN_ID)
    _write_contract(info["repo"], "x-adw-surfaces:\n  - CHANGESCOPETOKEN\n")
    html = _page(_client(info["repo"]), _slug_for(info["repo"]))

    assert "data-change-scope" in html
    assert "src/example.py" in html and "assets/logo.bin" in html
    assert CATALOG["en"]["change_scope_binary"] in html      # the binary file's null count
    assert "x-adw-surfaces" in html and "CHANGESCOPETOKEN" in html  # declared scope is content
    assert CATALOG["en"]["change_scope_no_declared"] not in html
    assert CATALOG["en"]["change_scope_no_run_diff"] not in html


def test_run_detail_states_no_run_diff_and_no_declared_scope(home, tmp_path):  # noqa: F811
    """S6/AC-4/AC-7: when no lane has a usable diff and no declared scope exists, the
    block still renders and says so clearly — "no run diff available" and "no
    declared scope" — never an unexplained empty table."""
    repo = tmp_path / "repo"
    write_run(repo, RUN_ID, _wrap([_lane_start(3, "backend")]), phase="done")  # 0 snapshots
    html = _page(_client(repo), _slug_for(repo))

    assert "data-change-scope" in html
    assert CATALOG["en"]["change_scope_no_run_diff"] in html
    assert CATALOG["en"]["change_scope_no_declared"] in html


def test_change_scope_chrome_labels_are_bilingual(home, tmp_path):  # noqa: F811
    """B6: every change-scope chrome label exists in BOTH language blocks, non-empty,
    with identical key sets; German values are not mere copies of the English."""
    for key in CHANGE_SCOPE_LABEL_KEYS:
        assert key in CATALOG["en"] and CATALOG["en"][key].strip(), key
        assert key in CATALOG["de"] and CATALOG["de"][key].strip(), key
    assert CATALOG["en"]["change_scope_title"] != CATALOG["de"]["change_scope_title"]
    assert CATALOG["en"]["change_scope_no_declared"] != CATALOG["de"]["change_scope_no_declared"]
