"""RED tests for Aufgabe B — the run-detail JSON, path-traversal guard, the raw
``/events`` endpoint and the read-only guarantee.

Derived from .adw/spec.md (AC 6/7/8, 12/13/15/16), .adw/contract.yaml (RunDetail,
PhaseStatus, TraceNode, EventRecord, x-adw-scope.invariants) and GUI-SPEC §7.4.
Tested against fixture logs with FastAPI's TestClient. RED until
``adw.gui.app.create_app`` exists.
"""

import json

from adw.gui.app import create_app
from fastapi.testclient import TestClient

from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    CODEX_STDOUT,
    FINAL_ANSWER,
    GATE_CMD,
    GATE_OUTPUT,
    PHASE_ORDER,
    PROMPT,
    SYSTEM_APPEND,
    comprehensive_lines,
    find_node,
    home,
    phases_lines,
    problems_lines,
    write_run,
)


def _client_with(tmp_path, run_id, lines, phase=None):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def test_detail_phase_bar_has_seven_phases_in_order_with_states(home, tmp_path):  # noqa: F811
    """AC 12: the header is exactly the seven phases in workflow order, each with
    a status (completed/active/pending/failed) and a duration where
    determinable."""
    client, slug = _client_with(tmp_path, "aaaa1111", phases_lines(), phase="build")
    detail = client.get(f"/api/runs/{slug}/aaaa1111").json()

    phases = detail["phases"]
    assert [p["name"] for p in phases] == PHASE_ORDER
    by_name = {p["name"]: p for p in phases}
    assert by_name["spec"]["status"] == "completed" and by_name["spec"]["duration"] == 2.0
    assert by_name["plan"]["status"] == "completed" and by_name["plan"]["duration"] == 3.0
    assert by_name["build"]["status"] == "active"
    for later in ("integration", "codex_review", "final_review", "ci"):
        assert by_name[later]["status"] == "pending"
        assert by_name[later]["duration"] is None


def test_detail_tree_comes_from_model_and_keeps_unknown_types(home, tmp_path):  # noqa: F811
    """AC 13/16: the tree is the span tree from adw.gui.model (run root, nested
    phase/lane/round, agent.run with its tool points) and unknown event types are
    retained generically with their raw payload, never dropped."""
    client, slug = _client_with(tmp_path, "aaaa1111", comprehensive_lines(), phase="done")
    detail = client.get(f"/api/runs/{slug}/aaaa1111").json()

    tree = detail["tree"]
    assert len(tree) == 1 and tree[0]["type"] == "run"

    for node_type in ("phase", "lane", "round", "agent.run", "gate", "codex.review"):
        assert find_node(tree, node_type) is not None, node_type

    unknown = find_node(tree, "weird.unknown")
    assert unknown is not None
    assert json.dumps(unknown).find("bar") != -1  # raw payload carried through

    rnd = find_node(tree, "round")
    assert rnd.get("n") == 2 and rnd.get("cap") == 10  # loop nodes carry n/cap


def test_detail_pane_data_present_per_node_type(home, tmp_path):  # noqa: F811
    """AC 14: agent.run carries the full prompt (incl. system append), the answer
    and the tool calls (full input + result); gate carries command/exit/output;
    codex.review carries the findings and the raw stdout."""
    client, slug = _client_with(tmp_path, "aaaa1111", comprehensive_lines(), phase="done")
    tree = client.get(f"/api/runs/{slug}/aaaa1111").json()["tree"]

    agent = json.dumps(find_node(tree, "agent.run"))
    assert PROMPT in agent and SYSTEM_APPEND in agent  # Prompt tab material
    assert FINAL_ANSWER in agent                        # Answer tab material
    assert "agent.tool.call" in agent and "parser.py" in agent  # Tools tab material

    gate = json.dumps(find_node(tree, "gate"))
    assert GATE_CMD in gate and GATE_OUTPUT in gate and "\"exit_code\"" in gate

    review = json.dumps(find_node(tree, "codex.review"))
    assert "Missing null check" in review and "P2" in review and CODEX_STDOUT in review


def test_detail_surfaces_reader_problems(home, tmp_path):  # noqa: F811
    """AC 15: reader-reported problems (broken lines, seq gaps) are made visible
    in the detail JSON with their position/sequence information."""
    client, slug = _client_with(tmp_path, "aaaa1111", problems_lines(), phase="done")
    detail = client.get(f"/api/runs/{slug}/aaaa1111").json()

    kinds = {p["kind"] for p in detail["problems"]}
    assert "bad_line" in kinds and "seq_gap" in kinds
    gap = next(p for p in detail["problems"] if p["kind"] == "seq_gap")
    assert gap["expected"] == 2 and gap["found"] == 3


def test_path_traversal_guard_slug_and_run_id(home, tmp_path):  # noqa: F811
    """AC 7/8: unknown slug -> 404; a run_id that violates RUN_ID_RE -> 400; a
    valid-format but absent run_id -> 404. Never a 5xx."""
    client, slug = _client_with(tmp_path, "abcdef12", comprehensive_lines(), phase="done")

    assert client.get("/api/runs/no-such-slug/abcdef12").status_code == 404
    assert client.get(f"/api/runs/{slug}/zzzzzzzz").status_code == 400  # 8 chars, not hex
    assert client.get(f"/api/runs/{slug}/1234567").status_code == 400   # too short
    assert client.get(f"/api/runs/{slug}/deadbeef").status_code == 404  # valid form, absent
    # The HTML detail route enforces the same contract.
    assert client.get(f"/runs/{slug}/zzzzzzzz").status_code == 400
    assert client.get("/runs/no-such-slug/abcdef12").status_code == 404


def test_events_endpoint_from_seq_and_unknown_types(home, tmp_path):  # noqa: F811
    """AC / contract: /events returns reader-accepted records with all original
    fields in file order, from seq >= N (default: the beginning), unknown types
    NOT filtered; an invalid from_seq is a controlled client error (never 5xx)."""
    client, slug = _client_with(tmp_path, "aaaa1111", comprehensive_lines(), phase="done")

    allrecs = client.get(f"/api/runs/{slug}/aaaa1111/events").json()
    assert [r["seq"] for r in allrecs] == list(range(1, 19))  # default: from the start
    first = allrecs[0]
    for field in ("seq", "ts", "type", "kind", "span", "parent",
                  "phase", "lane", "round", "payload"):
        assert field in first
    assert any(r["type"] == "weird.unknown" for r in allrecs)  # unknown types kept

    frm = client.get(f"/api/runs/{slug}/aaaa1111/events", params={"from_seq": 10}).json()
    assert [r["seq"] for r in frm] == list(range(10, 19))

    bad = client.get(f"/api/runs/{slug}/aaaa1111/events", params={"from_seq": "not-int"})
    assert 400 <= bad.status_code < 500  # controlled client error, not a server error


def test_gui_is_strictly_read_only(home, tmp_path):  # noqa: F811
    """AC 6: no route mutates a run's files and there is no write/control route —
    a full read pass leaves every file under the run directory byte-identical."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = write_run(repo, "aaaa1111", comprehensive_lines(), phase="done")
    app = create_app(repos=[str(repo)])

    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        assert not (methods & {"POST", "PUT", "PATCH", "DELETE"})

    def snapshot():
        return {p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()}

    before = snapshot()
    client = TestClient(app)
    slug = client.get("/api/runs").json()[0]["repo"]
    client.get("/")
    client.get(f"/runs/{slug}/aaaa1111")
    client.get(f"/api/runs/{slug}/aaaa1111")
    client.get(f"/api/runs/{slug}/aaaa1111/events")
    client.get(f"/api/runs/{slug}/aaaa1111/stream")  # finished run: closes on its own
    assert snapshot() == before
