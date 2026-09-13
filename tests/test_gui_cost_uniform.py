"""RED tests for A7 / AC 11 — one cost format everywhere.

Cost appears in the run list, the Timeline header and the run-context panel in a
single format (the ``_fmt_cost`` convention, e.g. ``$56.88``). Today the run-context
entry runs ``cost_usd`` through ``ctx_num`` (``round(6)`` → ``56.87666``) both
server-side and — after selection / live refresh — in ``app.js``
(``Math.round(value*1e6)/1e6``). The other context number fields keep ``ctx_num``;
the raw payload the panel PROJECTS FROM (a data attribute) is not a formatted
display and is untouched (E10).

Derived from .adw/spec.md (AC 11), .adw/contract.yaml
(x-adw-template-behavior.costs) and .adw/plan.md (B2/B3). RED until the server
template and the client projection adopt the shared cost format.
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)
from tests.gui_js_harness import run_scenario

RUN_ID = "aaaa1111"
# A cost whose six-decimal rounding (56.87666) and two-decimal format ($56.88)
# differ visibly — the exact value the issue cites from the run-context panel.
COST = 56.87666


def _cost_run_lines():
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Cost run")),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "agent.run", "start", "A", "PB", sec=2,
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        rec(4, "agent.run", "end", "A", "PB", sec=3,
            payload={"result_text": "done", "cost_usd": COST, "is_error": False}),
        rec(5, "phase", "end", "PB", "R", sec=4, payload={"name": "build", "to_phase": "done"}),
        rec(6, "run", "end", "R", None, sec=5, payload=run_end_payload("done", 5.0, COST)),
    ]


def _clients(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, RUN_ID, _cost_run_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    return client, _slug(os.path.normpath(str(repo.resolve())))


def _ctx_cost_cell(html: str):
    """The rendered TEXT of the run-context panel's ``cost_usd`` field (not the
    projection payload it carries in a data attribute)."""
    m = re.search(r'data-context-field="cost_usd"[^>]*>([^<]*)<', html)
    return m.group(1).strip() if m else None


def test_cost_uses_the_shared_format_in_list_timeline_and_context(home, tmp_path):  # noqa: F811
    """AC 11: the run list, the Timeline header and the run-context panel all show
    ``$56.88`` — one format. The panel's rendered field no longer shows the raw
    six-decimal ``56.87666``."""
    client, slug = _clients(tmp_path)

    list_html = client.get("/").text
    detail_html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert "$56.88" in list_html                               # run list (already $-formatted)

    header = detail_html[detail_html.find('data-tab-panel="timeline"'):]
    assert "$56.88" in header, "the Timeline header cost is not in the shared format"

    cell = _ctx_cost_cell(detail_html)
    assert cell == "$56.88", f"run-context cost cell is {cell!r}, not the shared format"
    assert "56.87666" != cell  # the six-decimal form is gone from the formatted display


def test_a_missing_cost_stays_empty_in_the_context_panel(home, tmp_path):  # noqa: F811
    """AC 11 / the "never as 0" rule: a run with no cost renders the context cost
    field EMPTY — never ``$0.00`` and never ``0``."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("No cost")),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "phase", "end", "PB", "R", sec=2, payload={"name": "build", "to_phase": "done"}),
        rec(4, "run", "end", "R", None, sec=3,
            payload={"status": "done", "totals": {"duration": 3.0}}),
    ]
    write_run(repo, "bbbb2222", lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug(os.path.normpath(str(repo.resolve())))

    cell = _ctx_cost_cell(client.get(f"/runs/{slug}/bbbb2222").text)
    assert cell == "", f"a missing cost rendered {cell!r} instead of empty"


def test_client_projection_uses_the_shared_cost_format(tmp_path):
    """AC 11 (client): selecting a node projects the node's ``cost_usd`` onto the
    panel in the SAME ``$x.xx`` format the server renders — the projection no longer
    falls back to the six-decimal display. The shared ``context-panel`` scenario's
    node A carries cost 0.4, so the projected field reads ``$0.40``; node B's null
    cost stays empty."""
    r = run_scenario(tmp_path, "context-panel")

    assert r["afterA"]["cost_usd"] == "$0.40", r["afterA"]["cost_usd"]
    assert r["afterB"]["cost_usd"] == ""       # null cost stays empty, never $0.00
