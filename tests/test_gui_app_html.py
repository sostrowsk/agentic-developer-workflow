"""RED tests for Aufgabe B — the server-rendered run-detail HTML and detail pane.

Derived from .adw/spec.md (AC 12–17), .adw/contract.yaml (x-adw-template-behavior)
and GUI-SPEC §7.2 B. Markup and CSS/JS structure are NOT contractual, so the
assertions check observable rendered content (phase names, tab labels, node data,
visible reader problems, generic unknown nodes, no external asset references) via
substring, never exact markup. RED until ``adw.gui.app.create_app`` exists.
"""

from adw.gui.app import create_app
from fastapi.testclient import TestClient

from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    CODEX_STDOUT,
    FINAL_ANSWER,
    GATE_CMD,
    GATE_OUTPUT,
    PHASE_ORDER,
    PROMPT,
    comprehensive_lines,
    home,
    problems_lines,
    write_run,
)


def _detail_html(tmp_path, run_id, lines, phase="done"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    resp = client.get(f"/runs/{slug}/{run_id}")
    assert resp.status_code == 200
    return resp.text


def test_detail_html_renders_phase_bar_and_agent_tabs(home, tmp_path):  # noqa: F811
    """AC 12/14: the header shows the seven phases; the agent.run pane shows
    exactly the Prompt/Answer/Tools tabs with their content — and NO Diff tab this
    run."""
    html = _detail_html(tmp_path, "aaaa1111", comprehensive_lines())

    for phase in PHASE_ORDER:
        assert phase in html
    assert "Prompt" in html and "Answer" in html and "Tools" in html
    assert "Diff" not in html  # the Diff tab is a later run (E-scope)
    assert PROMPT in html and FINAL_ANSWER in html
    assert "Read" in html  # the tool call appears in the Tools list


def test_detail_html_renders_gate_and_codex_panes(home, tmp_path):  # noqa: F811
    """AC 14: the gate pane shows command/exit-code/full output; the codex.review
    pane shows the findings (severity, file, message) and the raw stdout."""
    html = _detail_html(tmp_path, "aaaa1111", comprehensive_lines())

    assert GATE_CMD in html and GATE_OUTPUT in html
    assert "P2" in html and "parser.py" in html and "Missing null check" in html
    assert CODEX_STDOUT in html


def test_detail_html_shows_problems_unknown_nodes_and_no_external_refs(home, tmp_path):  # noqa: F811
    """AC 15/16/17: reader problems are visibly shown, unknown event types render
    generically (type label + raw payload), and the page references no external
    resource."""
    html = _detail_html(tmp_path, "aaaa1111", problems_lines())
    assert "problem" in html.lower()  # reader problems surfaced to the user

    generic = _detail_html(tmp_path, "bbbb2222", comprehensive_lines())
    assert "weird.unknown" in generic  # unknown type label kept
    assert "bar" in generic            # raw payload kept

    for forbidden in ("http://", "https://", "//cdn", "integrity="):
        assert forbidden not in generic
