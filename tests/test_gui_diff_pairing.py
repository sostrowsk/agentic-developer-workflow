"""RED tests for Aufgabe B — the snapshot bracketing rule and the Diff tab.

Each detail node's ``from``/``to`` pair is derived observably from THIS run's
event log (AC-B5): ``from`` is the same-lane ``snapshot`` with the highest ``seq``
at or before the node's first event; ``to`` is the same-lane ``snapshot`` with the
lowest ``seq`` at or after the node's last event. Snapshots of other lanes are
never used; because ``seq`` is unique the pair is deterministic. If either
boundary is absent the node is not bracketed and AC-B6 applies (no Diff tab, or a
clear "no snapshot" state — never an unexplained empty area or a synthetic pair).

The derived pair is exposed on each serialized tree node as ``diff_from`` /
``diff_to`` (both null when unbracketed) — the observable the Diff tab requests.
The concrete rendering/paging mechanism is not pinned.

Derived from .adw/spec.md (AC-B5/B6/B8, B7), .adw/contract.yaml
(x-adw-snapshot-pairing, x-adw-template-behavior.diff) and .adw/plan.md §2/§4.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    bracketed_lane_lines,
    build_diff_run,
    comprehensive_lines,
    find_node_by_label,
    home,
    write_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _detail(tmp_path, lines, run_id=RUN_ID):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    return client, slug


def test_each_bracketed_node_resolves_its_own_same_lane_pair(home, tmp_path):  # noqa: F811
    """AC-B5/B8: with two bracketed agent runs in one lane, each derives its OWN
    inclusive-seq pair, and the frontend-lane decoy snapshots (closer by seq to the
    first node's boundaries) are never chosen — proving other-lane exclusion on
    both the ``from`` and the ``to`` boundary."""
    client, slug = _detail(tmp_path, bracketed_lane_lines(RUN_ID))
    tree = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"]

    def ref(n):
        return f"refs/adw/{RUN_ID}/{n}"

    one = find_node_by_label(tree, "agent_one")
    assert one["diff_from"] == ref(1)   # highest backend snapshot at/before seq 6
    assert one["diff_to"] == ref(2)     # lowest backend snapshot at/after seq 8
    assert one["diff_from"] not in (ref(91), ref(92))  # frontend decoys rejected
    assert one["diff_to"] not in (ref(91), ref(92))

    two = find_node_by_label(tree, "agent_two")
    assert two["diff_from"] == ref(3)   # its own, distinct pair
    assert two["diff_to"] == ref(4)


def test_node_missing_a_same_lane_boundary_is_unbracketed(home, tmp_path):  # noqa: F811
    """AC-B8 -> AC-B6: agent_three has a same-lane snapshot before it but none at or
    after its last event, so it is not bracketed — no synthetic nearest pair, no
    error; the missing boundary yields the unbracketed state (``diff_to`` null)."""
    client, slug = _detail(tmp_path, bracketed_lane_lines(RUN_ID))
    tree = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"]

    three = find_node_by_label(tree, "agent_three")
    assert three.get("diff_to") is None
    assert not (three.get("diff_from") and three.get("diff_to"))  # not bracketed


def test_nodes_without_a_lane_or_snapshots_are_unbracketed(home, tmp_path):  # noqa: F811
    """AC-B6: nodes with no bracketing snapshots (a run with none at all) are never
    bracketed — every agent.run in the snapshot-less comprehensive fixture reports
    a null pair, so no Diff tab is offered for them."""
    client, slug = _detail(tmp_path, comprehensive_lines(), run_id="bbbb2222")
    tree = client.get(f"/api/runs/{slug}/bbbb2222").json()["tree"]

    agent = find_node_by_label(tree, "build_agent")
    assert agent.get("diff_from") is None and agent.get("diff_to") is None


def test_diff_tab_present_for_bracketed_and_absent_for_unbracketed(home, tmp_path):  # noqa: F811
    """AC-B5/B6: a run with bracketed nodes offers a Diff tab and the client wires
    the diff endpoint; a run without any bracketing snapshot offers no Diff tab."""
    client, slug = _detail(tmp_path, bracketed_lane_lines(RUN_ID))
    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    assert "Diff" in html

    js = client.get("/static/app.js").text
    assert "diff" in js.lower()  # the client requests the diff endpoint

    client2, slug2 = _detail(tmp_path, comprehensive_lines(), run_id="cccc3333")
    html2 = client2.get(f"/runs/{slug2}/cccc3333").text
    assert "Diff" not in html2  # no bracketing snapshot -> no Diff tab


def test_large_diff_patch_is_not_inlined_in_the_detail_html(home, tmp_path):  # noqa: F811
    """AC-B7: the big patch does not block the page — it is fetched on demand from
    the diff endpoint, not inlined in the server-rendered detail (which stays far
    smaller than the full patch), while the full patch stays reachable there."""
    info = build_diff_run(tmp_path / "repo", RUN_ID, large=True)
    client = TestClient(create_app(repos=[str(info["repo"])]))
    slug = _slug_for(info["repo"])

    patch = client.get(
        f"/api/runs/{slug}/{RUN_ID}/diff",
        params={"from": info["ref1"], "to": info["ref2"]},
    ).json()["patch"]
    assert len(patch) > 0

    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    assert len(html) < len(patch) // 2  # the patch is not materialised in the page
