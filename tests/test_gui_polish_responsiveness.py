"""RED tests for Aufgabe B — selecting an agent.run node keeps the UI usable.

The reference case is the big fixture (>= 40 tool-call/-result pairs, >= 5 MB of
payload, one result >= 1 MB). Only the *observable effect* of the fix is asserted
here; the concrete mechanism (collapse/section-load/render-only-the-visible-slice)
is an implementation decision (spec B2, contract x-adw-detail-responsiveness). The
2-second bound itself is verified by the documented manual browser check (B4), not
automated — an automated browser/performance harness is deferred.

Derived from .adw/spec.md (B1–B4), .adw/contract.yaml and .adw/plan.md. Tested
against the deterministic big fixture with FastAPI's TestClient.
"""

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    BIG_RESULT_HEAD,
    BIG_RESULT_TAIL,
    big_agent_run_lines,
    home,
    write_run,
)

RUN_ID = "aaaa1111"


def _big_client(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, big_agent_run_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def _total_result_bytes(client, slug):
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()
    return sum(
        len(e["payload"].get("content", ""))
        for e in events
        if e.get("type") == "agent.tool.result"
    )


def test_fixture_meets_the_reference_case_thresholds(home, tmp_path):  # noqa: F811
    """B1: the fixture is a faithful reference case — >= 40 tool pairs, >= 5 MB of
    result payload in total, and one single result >= 1 MB."""
    client, slug = _big_client(tmp_path)
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()

    results = [e for e in events if e.get("type") == "agent.tool.result"]
    calls = [e for e in events if e.get("type") == "agent.tool.call"]
    assert len(calls) >= 40 and len(results) >= 40

    assert _total_result_bytes(client, slug) >= 5 * 1024 * 1024
    assert max(len(r["payload"].get("content", "")) for r in results) >= 1024 * 1024


def test_initial_detail_html_does_not_inline_all_payloads_at_once(home, tmp_path):  # noqa: F811
    """B1/B2: the initial detail delivery does not materialise every full payload
    at once — the served HTML is far smaller than the summed result payloads (the
    old code rendered all of it inline, causing the ~35 s freeze)."""
    client, slug = _big_client(tmp_path)
    total = _total_result_bytes(client, slug)

    resp = client.get(f"/runs/{slug}/{RUN_ID}")
    assert resp.status_code == 200
    assert len(resp.text) < total // 2


def test_full_tool_result_stays_reachable(home, tmp_path):  # noqa: F811
    """B2: the user can still reach every full content — the >= 1 MB tool result is
    delivered in full (head + body + tail) through the read-only events route the
    load mechanism draws on."""
    client, slug = _big_client(tmp_path)
    events = client.get(f"/api/runs/{slug}/{RUN_ID}/events").json()

    big = next(
        e for e in events
        if e.get("type") == "agent.tool.result"
        and e["payload"].get("content", "").startswith(BIG_RESULT_HEAD)
    )
    content = big["payload"]["content"]
    assert content.endswith(BIG_RESULT_TAIL)
    assert len(content) >= 1024 * 1024


def test_tool_node_pane_carries_load_anchor_and_loads_on_selection(home, tmp_path):  # noqa: F811
    """B2 (regression): selecting a tool node directly must not show a permanently
    empty box. A tool node is a POINT node and has no pane of its own any more — it
    is shown through the SHARED pane shell, which carries a standalone load anchor
    (data-load-seq, not inside <details>) that the client loads on selection."""
    client, slug = _big_client(tmp_path)

    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    assert "data-generic-pane" in html    # the shared pane for point nodes
    assert "data-load-seq" in html        # with a load anchor

    js = client.get("/static/app.js").text
    assert "loadToolBody" in js
    # The selection path re-points and loads the shared shell.
    assert "data-generic-pane" in js


def test_single_record_events_query_returns_only_that_record(home, tmp_path):  # noqa: F811
    """B (regression): the events route accepts an upper bound so the client can
    fetch a single record (from_seq == to_seq) instead of the whole tail — a hint
    query returns exactly the requested record, not megabytes of following log."""
    client, slug = _big_client(tmp_path)

    recs = client.get(
        f"/api/runs/{slug}/{RUN_ID}/events", params={"from_seq": 3, "to_seq": 3}
    ).json()
    assert [r["seq"] for r in recs] == [3]


def test_detail_endpoints_respond_without_server_error(home, tmp_path):  # noqa: F811
    """B3: opening the big run never yields a 5xx (HTML and JSON detail both 200),
    and the pinned error cases stay controlled client errors — whatever load
    mechanism (and any route it adds) must keep the read-only guarantees."""
    client, slug = _big_client(tmp_path)

    assert client.get(f"/runs/{slug}/{RUN_ID}").status_code == 200
    assert client.get(f"/api/runs/{slug}/{RUN_ID}").status_code == 200

    assert client.get("/api/runs/no-such-slug/aaaa1111").status_code == 404
    assert client.get(f"/api/runs/{slug}/zzzzzzzz").status_code == 400  # not hex
    assert client.get(f"/api/runs/{slug}/deadbeef").status_code == 404  # valid form, absent
