"""Tests for the server-rendered run-detail HTML and detail pane."""

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (
    CODEX_STDOUT,
    FINAL_ANSWER,
    GATE_CMD,
    GATE_OUTPUT,
    PHASE_ORDER,
    PROMPT,
    comprehensive_lines,
    problems_lines,
    write_run,
)


def _detail_html(tmp_path, run_id, lines, phase="done", focus=None):
    # GUI-Redesign 5 (A2): a span pane's BODY is delivered only for the ``?focus``
    # node; without it the pane is an unloaded shell the client fetches on selection.
    # Tests that assert a specific pane's body content pass ``focus=<seq>``.
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    resp = client.get(f"/runs/{slug}/{run_id}", params={"focus": focus} if focus else None)
    assert resp.status_code == 200
    return resp.text


def test_detail_html_renders_phase_bar_and_agent_tabs(home, tmp_path):
    """AC 12/14/E9: the header shows the seven phases; the agent.run pane shows the
    Prompt/Answer/Tools tabs with their content — and NO Diff tab for this
    snapshot-less run (a Diff tab only appears for bracketed nodes)."""
    html = _detail_html(tmp_path, "aaaa1111", comprehensive_lines(), focus=5)  # agent.run pane

    for phase in PHASE_ORDER:
        assert phase in html
    assert "Prompt" in html and "Answer" in html and "Tools" in html
    assert "Diff" not in html  # no bracketing snapshot -> no Diff tab
    assert PROMPT in html and FINAL_ANSWER in html
    assert "Read" in html  # the tool call appears in the Tools list


def test_detail_html_renders_gate_and_codex_panes(home, tmp_path):
    """AC 14: the gate pane shows command/exit-code/full output; the codex.review
    pane shows the findings (severity, file, message) and the raw stdout."""
    html = _detail_html(tmp_path, "aaaa1111", comprehensive_lines())

    assert GATE_CMD in html and GATE_OUTPUT in html
    assert "P2" in html and "parser.py" in html and "Missing null check" in html
    assert CODEX_STDOUT in html


def test_detail_html_shows_problems_unknown_nodes_and_no_external_refs(home, tmp_path):
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


def test_foreign_host_is_rejected_and_loopback_hosts_are_served(home, tmp_path):
    """R3/AK6 (E4): DNS-rebinding guard — a foreign ``Host`` gets 400 on the GUI
    and the API; the loopback names (with or without a port) are served."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", comprehensive_lines())
    client = TestClient(create_app(repos=[str(repo)]))
    for path in ("/", "/api/runs"):
        assert client.get(path, headers={"Host": "evil.example"}).status_code == 400
        for host in ("127.0.0.1:8765", "localhost", "[::1]:8765"):
            assert client.get(path, headers={"Host": host}).status_code == 200, (path, host)


def test_detail_body_carries_run_status_and_last_seq(home, tmp_path):
    """R3/E3: <body> names the rendered run status and the highest seq of the loaded
    log (independent of the Raw/Tools window), so the client can skip the stream
    for a finished run and otherwise resume after that seq."""
    import re

    html = _detail_html(tmp_path, "aaaa1111", comprehensive_lines())
    body = re.search(r"<body[^>]*>", html).group(0)
    assert 'data-run-status="done"' in body
    assert 'data-last-seq="18"' in body
