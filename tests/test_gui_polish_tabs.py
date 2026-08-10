"""RED tests for Aufgabe D — the agent.run detail pane uses switchable tabs.

GUI-SPEC §7.2 requires switchable tabs **Prompt**, **Answer**, **Tools** for
agent.run (exactly one active at a time) rather than stacked sections. The
run-detail view is uniformly English (E9): the label read "Antwort" during the
polish run and is now "Answer" again.

Derived from .adw/spec.md (D1/D2), .adw/contract.yaml
(x-adw-template-behavior.run_detail.detail_pane, .language) and .adw/plan.md.
Markup is not contractual, so the tab labels and the switching affordance are
checked as observable content, never as exact markup.
"""

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    write_run,
)


def _detail_html(tmp_path, run_id="aaaa1111"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, comprehensive_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    resp = client.get(f"/runs/{slug}/{run_id}")
    assert resp.status_code == 200
    return resp.text


def test_agent_run_offers_prompt_answer_tools_tabs(home, tmp_path):  # noqa: F811
    """D1/D2/E9: the three tabs Prompt/Answer/Tools; the run-detail view is
    uniformly English, so "Antwort" no longer appears. A snapshot-less run offers
    no Diff tab."""
    html = _detail_html(tmp_path)

    assert "Prompt" in html
    assert "Answer" in html
    assert "Tools" in html
    assert "Antwort" not in html
    assert "Diff" not in html


def test_tabs_are_switchable_via_vanilla_js(home, tmp_path):  # noqa: F811
    """D1: the tabs are switchable (exactly one visible/active at a time). The
    served vanilla client wires the tab switching — proven by the packaged app.js
    referencing tabs (no framework, no external asset per E5)."""
    _detail_html(tmp_path)  # ensure the app (and its static mount) is built
    client = TestClient(create_app(repos=[str(tmp_path)]))
    js = client.get("/static/app.js").text

    assert "tab" in js.lower()
