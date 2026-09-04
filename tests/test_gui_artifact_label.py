"""An artifact row names its file.

Every `artifact` node used to render as the bare word `artifact`. With the repeat
counter of 0.19.0 that got worse, not better: `5× Artefakte` collapses correctly, but
expanded it showed five identical, uninformative rows — the name only ever existed
inside the payload.

The label now follows the tool-call form (`Read <path>` → `artifact <name>`), and the
full path travels in the `title` exactly as a tool call's path argument does (A4), so
the row is as informative as a tool row without getting longer.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import _display_label, _node_label, create_app
from adw.gui.model import build_tree
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"
REPO_ROOT = "/repo/root"


def _artifact(seq, payload):
    lines = [rec(seq, "artifact", "point", "A", sec=seq, payload=payload)]
    return build_tree([x for x in lines if isinstance(x, dict)])[0]


def test_the_label_names_the_artifact():
    node = _artifact(3, {"name": "spec.claude.md", "path": "/repo/root/.adw/spec.claude.md",
                         "bytes": 12, "sha256": "x"})

    assert _node_label(node) == "artifact spec.claude.md"


def test_a_nameless_artifact_keeps_the_bare_type():
    """Invent nothing: without a usable name the row stays what it was."""
    for payload in ({}, {"name": ""}, {"name": 7}, {"path": "/x/y.md"}, None):
        assert _node_label(_artifact(3, payload)) == "artifact"


def test_the_full_path_travels_in_the_title():
    """A4: the visible text stays short, the complete path stays reachable — the same
    contract a tool call's path argument has."""
    node = {"type": "artifact", "label": "artifact spec.md",
            "payload": {"name": "spec.md", "path": f"{REPO_ROOT}/.adw/spec.md"}}

    text, title = _display_label(node, REPO_ROOT)

    assert text == "artifact spec.md"
    assert title == f"{REPO_ROOT}/.adw/spec.md"


def test_an_artifact_without_a_path_gets_no_title():
    node = {"type": "artifact", "label": "artifact spec.md", "payload": {"name": "spec.md"}}

    assert _display_label(node, REPO_ROOT) == ("artifact spec.md", None)


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _artifact_run_lines(names):
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("art")),
        rec(2, "agent.run", "start", "A", "R", sec=1,
            payload={"agent": "spec_agent", "prompt": "p", "system_append": ""}),
    ]
    seq = 3
    for name in names:
        lines.append(rec(seq, "artifact", "point", "A", sec=seq,
                         payload={"name": name, "path": f"/x/{name}", "bytes": 1}))
        seq += 1
    lines.append(rec(seq, "agent.run", "end", "A", "R", sec=seq,
                     payload={"result_text": "d", "is_error": False}))
    seq += 1
    lines.append(rec(seq, "run", "end", "R", None, sec=seq,
                     payload={"status": "done", "totals": {"duration": 1.0}}))
    return lines


def test_a_collapsed_artifact_run_expands_to_distinct_names(home, tmp_path):  # noqa: F811
    """The whole point of the change: the counted collector stays, and opening it now
    tells the reader WHICH artifacts they were."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    names = ["spec.claude.md", "spec.codex.md", "spec.md", "contract.yaml"]
    write_run(repo, RUN_ID, _artifact_run_lines(names), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    html = client.get(f"/runs/{_slug_for(repo)}/{RUN_ID}").text
    i, j = html.find('class="trace"'), html.find('class="panes"')
    trace = html[i:j]

    # still one counted collector ...
    assert "4&times;" in trace or "4×" in trace
    # ... whose members are now distinguishable
    for name in names:
        assert f"artifact {name}" in trace


def test_the_api_tree_label_names_the_artifact_too(home, tmp_path):  # noqa: F811
    """The label is built once in `_node_label`, so the JSON tree carries the same
    text the column shows — no second source of truth."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, _artifact_run_lines(["plan.md"]), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    tree = client.get(f"/api/runs/{_slug_for(repo)}/{RUN_ID}").json()["tree"]
    labels = []

    def walk(nodes):
        for n in nodes:
            labels.append(n["label"])
            walk(n.get("children") or [])

    walk(tree)
    assert "artifact plan.md" in labels


def test_a_corrupt_artifact_payload_never_crashes_the_page():
    """A hand-written or corrupt record may carry a truthy NON-mapping payload.
    `_artifact_label` already tolerates that; the path extraction must too, or the
    whole run-detail page answers 500 while annotating."""
    for payload in ("just a string", ["a", "b"], 42, True):
        node = {"type": "artifact", "label": "artifact", "payload": payload}
        assert _display_label(node, REPO_ROOT) == ("artifact", None)


def test_a_run_with_a_corrupt_artifact_record_still_renders(home, tmp_path):  # noqa: F811
    """End to end: the page stays a 200 and keeps rendering the healthy nodes."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("art")),
        rec(2, "artifact", "point", "R", sec=1, payload="corrupt-not-a-mapping"),
        rec(3, "artifact", "point", "R", sec=2, payload={"name": "ok.md", "path": "/x/ok.md"}),
        rec(4, "run", "end", "R", None, sec=3,
            payload={"status": "done", "totals": {"duration": 1.0}}),
    ]
    write_run(repo, RUN_ID, lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    resp = client.get(f"/runs/{_slug_for(repo)}/{RUN_ID}")

    assert resp.status_code == 200
    assert "artifact ok.md" in resp.text
