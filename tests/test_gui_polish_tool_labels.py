"""RED tests for Aufgabe C — readable tool-call/-result entries in the trace tree.

Each ``agent.tool.call`` node exposes a ``label`` with the tool and its main
argument (Read → file path, Bash → command, Grep → pattern; other tools → tool +
identifiable main argument, else the tool alone). Each ``agent.tool.result`` node
shows its outcome compactly (error/success; the Bash exit code when present).
Nothing is invented: a missing tool name, a missing argument or missing outcome
fields fall back to the bare type name / the tool name alone.

Derived from .adw/spec.md (C1–C3), .adw/contract.yaml
(x-adw-template-behavior.trace_tree_tool_entries) and .adw/plan.md. The label is
observed on the serialized tree node (which also feeds the HTML tree row).
"""

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    iter_nodes,
    tool_label_lines,
    write_run,
)

RUN_ID = "aaaa1111"


def _labels_by_use_id(tmp_path):
    """Map tool call/result labels keyed by ``tool_use_id``. A call and its result
    share the same ``tool_use_id``, so they are kept in separate dicts; a single
    merged view exposes calls, with results only where no call shares the id."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, tool_label_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    tree = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"]
    calls, results = {}, {}
    for node in iter_nodes(tree):
        use_id = (node.get("payload") or {}).get("tool_use_id")
        if node.get("type") == "agent.tool.call":
            calls[use_id] = node["label"]
        elif node.get("type") == "agent.tool.result":
            results[use_id] = node["label"]
    # Calls win the shared view; every result is also exposed under a ``result:``
    # prefix so a call and its result (same tool_use_id) can both be asserted.
    merged = dict(calls)
    for use_id, label in results.items():
        merged[f"result:{use_id}"] = label
    return merged


def test_read_bash_grep_calls_show_tool_and_main_argument(home, tmp_path):  # noqa: F811
    """C1: the named cases carry tool + main argument per the field priority."""
    labels = _labels_by_use_id(tmp_path)

    assert "Read" in labels["read1"] and "models.py" in labels["read1"]
    assert "Bash" in labels["bash1"] and "pytest -x -q" in labels["bash1"]
    assert "Grep" in labels["grep1"] and "RUN_ID_RE" in labels["grep1"]


def test_other_tool_shows_tool_and_identifiable_argument(home, tmp_path):  # noqa: F811
    """C1: for a tool beyond the named cases (Write), the tool name plus an
    unambiguously identifiable main argument (the file path)."""
    labels = _labels_by_use_id(tmp_path)

    assert "Write" in labels["write1"] and "new.py" in labels["write1"]


def test_call_fallbacks_invent_nothing(home, tmp_path):  # noqa: F811
    """C3: no tool name → the bare type name; a tool with no identifiable argument
    → the tool name alone (no substitute value)."""
    labels = _labels_by_use_id(tmp_path)

    assert labels["noname1"] == "agent.tool.call"
    assert labels["noarg1"] == "Read"


def test_result_shows_outcome_and_bash_exit_code(home, tmp_path):  # noqa: F811
    """C2: a result shows error vs. success compactly, and the exit code when the
    payload carries one."""
    labels = _labels_by_use_id(tmp_path)

    err = labels["result:bash1"]
    ok = labels["result:read1"]
    assert "error" in err.lower()  # the failing result is marked as an error
    assert "1" in err              # exit code surfaced (payload carried exit_code=1)

    assert ok != "agent.tool.result"  # the success result is summarised, not bare
    assert ok != err                  # success and error are visibly distinct
    assert "error" not in ok.lower()


def test_result_without_outcome_fields_keeps_type_name(home, tmp_path):  # noqa: F811
    """C3: a result payload with no outcome fields keeps the bare type name."""
    labels = _labels_by_use_id(tmp_path)

    assert labels["result:bare1"] == "agent.tool.result"


def test_content_only_result_keeps_type_name(home, tmp_path):  # noqa: F811
    """C3 (regression): a result with a resolvable tool but only a ``content`` body
    (no ``is_error``/exit code) keeps the type name — content is not an outcome, so
    success is never invented."""
    labels = _labels_by_use_id(tmp_path)

    assert labels["result:content1"] == "agent.tool.result"


def test_unresolved_tool_name_result_keeps_type_name(home, tmp_path):  # noqa: F811
    """C3 (regression): a result whose ``tool_use_id`` matches no call has no
    resolvable tool name and keeps the type name, even though it carries an
    outcome field."""
    labels = _labels_by_use_id(tmp_path)

    assert labels["result:orphan1"] == "agent.tool.result"
