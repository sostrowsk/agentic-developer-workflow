"""RED tests for the read-only run-context PANEL on the run-detail page.

Two halves, in the established GUI test style and without any browser automation:

* markup (``TestClient`` over ``GET /runs/{repo}/{run_id}``): the page renders the
  read-only six-field panel beside the detail pane, each rendered node carries its
  own six-field ``context`` in the markup (``data-context``), and the no-selection
  fallback (``data-latest-context``) travels with the page — including a run with
  no trace, whose panel fields are simply empty;
* behaviour (the minimal JS harness driving the SERVED ``app.js`` in ``node``):
  selecting a node projects THAT node's context onto the panel fields; changing the
  selection updates every field (time travel); a null field renders empty, never 0.

Derived from .adw/spec.md (AC 9, 3, 4), .adw/contract.yaml (P1_context_panel) and
.adw/plan.md (B6). RED until the template renders the panel/context data and
``app.js`` wires the selection-driven projection. No production code is written
here; the panel's data-attribute contract is asserted so the implementation has a
concrete observable to satisfy.
"""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
    write_state_only_run,
)
from tests.gui_js_harness import run_scenario

RUN_ID = "aaaa1111"
FIELDS = ("phase", "round", "limit_hits", "circuit_breakers", "cost_usd", "followups")


def _panel_run_lines():
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Panel run")),
        rec(2, "phase", "start", "P", "R", sec=2, payload={"name": "build", "from_phase": "build"}),
        rec(3, "agent.run", "start", "A", "P", sec=3,
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        rec(4, "limit.hit", "point", "A", sec=4, payload={"limit": "cost", "value": 1, "cap": 5}),
        rec(5, "agent.run", "end", "A", "P", sec=5,
            payload={"result_text": "ok", "cost_usd": 0.25, "is_error": False}),
        rec(6, "phase", "end", "P", "R", sec=6, payload={"name": "build", "to_phase": "done"}),
        rec(7, "run", "end", "R", None, sec=7, payload=run_end_payload("done")),
    ]


def _client(tmp_path, run_id=RUN_ID, lines=None, *, state_only=False):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    if state_only:
        write_state_only_run(repo, run_id)
    else:
        write_run(repo, run_id, lines or _panel_run_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def _trace_section(html: str) -> str:
    """The trace tree markup (between the ``trace`` list and the ``panes``), where
    the per-node ``data-context`` travels."""
    i = html.find('class="trace"')
    j = html.find('class="panes"')
    return html[i:j] if (i != -1 and j != -1 and i < j) else ""


def test_context_panel_is_a_read_only_six_field_list_on_the_page(home, tmp_path):  # noqa: F811
    """AC 9 / P1_context_panel: the page renders a read-only context panel with a
    fixed slot for each of the six fields, beside the existing detail pane. The
    existing detail-pane tabs remain present."""
    client, slug = _client(tmp_path)
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert "run-context" in html
    for field in FIELDS:
        assert f'data-context-field="{field}"' in html, field
    # The panel is read-only: no form control drives it. Scope to the panel region
    # itself (the ``run-context`` element up to its close) so the assertion targets
    # the panel and not, e.g., the Raw tab's filter form elsewhere on the page.
    start = html.find("run-context")
    panel = html[start: html.find("</aside>", start)]
    assert "<input" not in panel and "<select" not in panel
    # The existing detail-pane tabs are untouched.
    assert 'data-tab="trace"' in html and 'data-tab-panel="tools"' in html


def test_each_rendered_node_carries_its_own_context_in_the_markup(home, tmp_path):  # noqa: F811
    """AC 3 / plan B6: the panel data travels PER NODE in the render (no client-side
    re-derivation) — each rendered trace node carries a ``data-context`` — and the
    no-selection fallback ``data-latest-context`` is available in the markup."""
    client, slug = _client(tmp_path)
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert "data-latest-context=" in html                 # no-selection fallback
    # A per-node context attribute (distinct from data-latest-context) rides along
    # each rendered node.
    assert re.search(r"\sdata-context=", _trace_section(html))


def test_run_without_trace_still_renders_the_panel_with_empty_fields(home, tmp_path):  # noqa: F811
    """AC 10 / P1: a run without a trace renders the panel (fed by
    ``latest_context``) without error — the fields are simply empty, never 0."""
    client, slug = _client(tmp_path, run_id="beef0001", state_only=True)
    resp = client.get(f"/runs/{slug}/beef0001")

    assert resp.status_code == 200
    html = resp.text
    assert "run-context" in html
    for field in FIELDS:
        assert f'data-context-field="{field}"' in html, field
    assert "data-latest-context=" in html


def test_selecting_a_node_projects_its_context_onto_the_panel(tmp_path):
    """AC 3/4 / P1 (client): selecting a node writes that node's six-field context
    onto the panel; selecting another node updates every field (time travel); a
    null field renders empty, never as a numeric 0 or an inferred value."""
    r = run_scenario(tmp_path, "context-panel")

    after_a = r["afterA"]
    assert after_a["phase"] == "build"
    assert after_a["limit_hits"] == "1"
    assert after_a["circuit_breakers"] == ""      # null -> empty, not "0"
    assert after_a["followups"] == ""             # null -> empty
    assert "0.4" in after_a["cost_usd"]           # the node's cost is shown

    after_b = r["afterB"]                          # time travel to the second node
    assert after_b["phase"] == "plan"
    assert after_b["limit_hits"] == ""            # this node's null count stays empty
    assert after_b["cost_usd"] == ""
    assert after_b["followups"] == ""


def test_live_refresh_updates_the_unselected_panel(tmp_path):
    """P1 (client): a live-region swap must refresh the panel when no node is
    selected. `data-latest-context` lives on <body>, which is not swapped, so the
    swap has to copy it from the fetched document — otherwise the unselected panel
    would freeze at the value the page opened with."""
    r = run_scenario(tmp_path, "context-live-swap")

    assert r["before"]["phase"] == "spec"          # initial latest_context
    assert r["after"]["phase"] == "build"          # refreshed latest_context wins
    assert r["after"]["limit_hits"] == "2"
