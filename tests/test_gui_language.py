"""RED test for Aufgabe D — the run-detail view is uniformly English (E9).

The tab previously labeled "Antwort" (introduced in the polish run) reads
"Answer" again; no mixed-language label remains in the run-detail view.

Derived from .adw/spec.md (AC-D1), .adw/contract.yaml
(x-adw-template-behavior.language) and .adw/plan.md §7. Labels are checked as
observable content, never as exact markup.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    write_run,
    write_state_only_run,
)


def test_agent_run_answer_tab_is_english(home, tmp_path):  # noqa: F811
    """AC-D1: the agent.run answer tab is labeled "Answer" and "Antwort" no longer
    appears in the run-detail view."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, "aaaa1111", comprehensive_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]

    html = client.get(f"/runs/{slug}/aaaa1111").text
    assert "Prompt" in html and "Tools" in html
    assert "Answer" in html
    assert "Antwort" not in html


def test_no_trace_hint_is_english_only(home, tmp_path):  # noqa: F811
    """AC-D1/E9: no mixed-language label remains — the polish run's bilingual
    'no trace' hint keeps its English part and drops the German fragment."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_state_only_run(repo, "8f8dc4ff", phase="done", issue="Legacy run")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug(os.path.normpath(str(repo.resolve())))

    html = client.get(f"/runs/{slug}/8f8dc4ff").text
    assert "no trace" in html.lower()      # the hint stays (in English)
    assert "kein Event-Log" not in html    # ... without the German fragment
