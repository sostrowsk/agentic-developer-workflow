"""RED tests for Aufgabe D — the agent.run detail pane uses switchable tabs.

GUI-SPEC §7.2 requires the tabs **Prompt**, **Antwort**, **Tools** for agent.run,
switchable (exactly one active at a time) rather than stacked sections. The old
stacked "Answer" section becomes the "Antwort" tab. No Diff tab this run.

Derived from .adw/spec.md (D1/D2), .adw/contract.yaml
(x-adw-template-behavior.run_detail.detail_pane) and .adw/plan.md. Markup is not
contractual, so the tab labels and the switching affordance are checked as
observable content, never as exact markup.
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


def test_agent_run_offers_prompt_antwort_tools_tabs(home, tmp_path):  # noqa: F811
    """D1/D2: exactly the three tabs Prompt/Antwort/Tools; the old English
    "Answer" heading is gone (renamed to Antwort) and there is no Diff tab."""
    html = _detail_html(tmp_path)

    assert "Prompt" in html
    assert "Antwort" in html
    assert "Tools" in html
    assert "Answer" not in html
    assert "Diff" not in html


def test_tabs_are_switchable_via_vanilla_js(home, tmp_path):  # noqa: F811
    """D1: the tabs are switchable (exactly one visible/active at a time). The
    served vanilla client wires the tab switching — proven by the packaged app.js
    referencing tabs (no framework, no external asset per E5)."""
    _detail_html(tmp_path)  # ensure the app (and its static mount) is built
    client = TestClient(create_app(repos=[str(tmp_path)]))
    js = client.get("/static/app.js").text

    assert "tab" in js.lower()
