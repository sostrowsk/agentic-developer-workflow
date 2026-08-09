"""Regression tests for the Codex-review findings on the read-only GUI.

Covers: symlink containment (P1), script-embed XSS neutralisation (P1),
reconnect-at/after-end stream closure (P2), the ``failed`` phase status (P2) and
the live client wiring that incorporates streamed records into the view (P2).
Server behaviour is tested with FastAPI's TestClient; the client-JS finding is
guarded by asserting the served asset actually consumes stream records into the
DOM (a headless browser is out of scope — E5 forbids a node/JS toolchain).
"""

import json
import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    CODEX_STDOUT,
    FINAL_ANSWER,
    GATE_OUTPUT,
    XSS_BREAKOUT,
    comprehensive_lines,
    escalated_lines,
    home,
    parse_sse,
    write_run,
    xss_lines,
)


def _client_with(tmp_path, run_id, lines, phase=None):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug, repo


def _slug_for(repo):
    """The stable slug the app assigns to an ad-hoc ``--repo`` path — computed the
    same way as the resolver, so the detail routes can be addressed even when the
    repo has no listable runs (and thus is absent from ``/api/runs``)."""
    return _slug(os.path.normpath(str(repo.resolve())))


# --- P1: symlink containment ----------------------------------------------------


def test_symlinked_run_directory_escaping_the_tree_is_rejected(home, tmp_path):  # noqa: F811
    """A run directory that is a symlink pointing outside ``.adw/runs`` must not be
    read: the routes reject it (404) and the list silently skips it."""
    repo = tmp_path / "repo"
    (repo / ".adw" / "runs").mkdir(parents=True)
    # A secret store outside the repo, exposed through a symlinked run directory.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "events.jsonl").write_text(
        json.dumps({"seq": 1, "type": "run", "kind": "start", "payload": {"issue": "SECRET"}})
        + "\n",
        encoding="utf-8",
    )
    (repo / ".adw" / "runs" / "abcdef12").symlink_to(outside, target_is_directory=True)

    client = TestClient(create_app(repos=[str(repo)]))
    listing = client.get("/api/runs").json()
    # The escaping run is not listed and does not crash the list.
    assert all(e.get("run_id") != "abcdef12" for e in listing if "run_id" in e)

    # Addressed with the REAL slug so require_run's containment is exercised (a
    # bogus slug would only prove the unknown-slug 404).
    repo_slug = _slug_for(repo)
    for path in (
        "/api/runs/{}/abcdef12",
        "/runs/{}/abcdef12",
        "/api/runs/{}/abcdef12/events",
        "/api/runs/{}/abcdef12/stream",
    ):
        resp = client.get(path.format(repo_slug))
        assert resp.status_code == 404, path
        assert "SECRET" not in resp.text  # the outside file was never read


def test_symlinked_events_file_escaping_the_tree_is_not_read(home, tmp_path):  # noqa: F811
    """A run whose ``events.jsonl`` is a symlink to a file outside the runs tree is
    treated as unreadable (404) — the outside content is never served."""
    repo = tmp_path / "repo"
    run_dir = repo / ".adw" / "runs" / "abcdef12"
    run_dir.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP_SECRET_TOKEN", encoding="utf-8")
    (run_dir / "events.jsonl").symlink_to(secret)

    client = TestClient(create_app(repos=[str(repo)]))
    repo_slug = _slug_for(repo)

    for path in (
        f"/api/runs/{repo_slug}/abcdef12",
        f"/api/runs/{repo_slug}/abcdef12/events",
        f"/runs/{repo_slug}/abcdef12",
    ):
        resp = client.get(path)
        assert resp.status_code == 404, path
        assert "TOP_SECRET_TOKEN" not in resp.text


def test_symlinked_runs_root_escaping_the_repo_is_rejected(home, tmp_path):  # noqa: F811
    """P1: ``.adw/runs`` itself symlinked to an external directory must not turn
    that external tree into the containment root — its runs are neither listed nor
    readable, and no external content is served."""
    repo = tmp_path / "repo"
    (repo / ".adw").mkdir(parents=True)
    external = tmp_path / "external_runs"
    (external / "abcdef12").mkdir(parents=True)
    (external / "abcdef12" / "events.jsonl").write_text(
        json.dumps({"seq": 1, "type": "run", "kind": "start",
                    "payload": {"issue": "EXTERNAL_SECRET"}}) + "\n",
        encoding="utf-8",
    )
    (repo / ".adw" / "runs").symlink_to(external, target_is_directory=True)

    client = TestClient(create_app(repos=[str(repo)]))
    listing = client.get("/api/runs").json()
    assert all(e.get("run_id") != "abcdef12" for e in listing if "run_id" in e)
    assert "EXTERNAL_SECRET" not in json.dumps(listing)

    repo_slug = _slug_for(repo)
    for path in (
        "/api/runs/{}/abcdef12",
        "/runs/{}/abcdef12",
        "/api/runs/{}/abcdef12/events",
        "/api/runs/{}/abcdef12/stream",
    ):
        resp = client.get(path.format(repo_slug))
        assert resp.status_code == 404, path
        assert "EXTERNAL_SECRET" not in resp.text


# --- P1: script-embed XSS -------------------------------------------------------


def test_detail_html_neutralises_script_breakout_in_payloads(home, tmp_path):  # noqa: F811
    """AC/P1: an event payload containing ``</script><script>…`` must not be able
    to close the embedded JSON script element or inject executable markup."""
    client, slug, _ = _client_with(tmp_path, "abcdef12", xss_lines(), phase="done")
    html = client.get(f"/runs/{slug}/abcdef12").text

    # The literal breakout sequence must never appear verbatim in the document.
    assert XSS_BREAKOUT not in html
    assert "</script><script>" not in html
    assert "<script>alert(document.cookie)" not in html
    # The data is still present (safely escaped), so nothing was silently dropped.
    assert "alert(document.cookie)" in html


# --- P2: reconnect at/after the final seq closes the stream ---------------------


def test_stream_reconnect_at_or_after_final_seq_closes_empty(home, tmp_path):  # noqa: F811
    """A reconnect whose Last-Event-ID is >= the run's final (end) seq must close
    promptly with no further accepted-event frames, not poll forever."""
    client, slug, _ = _client_with(tmp_path, "abcdef12", comprehensive_lines())
    final_seq = 18

    for last_id in (final_seq, final_seq + 5):
        resp = client.get(
            f"/api/runs/{slug}/abcdef12/stream", headers={"Last-Event-ID": str(last_id)}
        )
        assert resp.status_code == 200
        events = [f for f in parse_sse(resp.text) if f["event"] is None]
        assert events == []  # nothing new, and the stream closed (get returned)


# --- P2: failed phase status ----------------------------------------------------


def test_detail_phase_bar_marks_escalated_phase_failed(home, tmp_path):  # noqa: F811
    """AC 12/P2: a phase the run escalated from is reported ``failed``, not
    ``completed``."""
    client, slug, _ = _client_with(tmp_path, "abcdef12", escalated_lines(), phase="escalated")
    detail = client.get(f"/api/runs/{slug}/abcdef12").json()
    by_name = {p["name"]: p for p in detail["phases"]}
    assert by_name["build"]["status"] == "failed"

    html = client.get(f"/runs/{slug}/abcdef12").text
    assert "failed" in html  # the failed phase is visibly marked


# --- P2: the live client actually incorporates streamed records -----------------


def test_client_renders_live_through_the_shared_server_snapshot_path(home, tmp_path):  # noqa: F811
    """P2/AC 14/17/20: a live-observed run must reach the completed-snapshot
    representation. The client must use ONE shared rendering path — re-fetch the
    server-rendered detail snapshot and swap it in without a page reload, driven
    by the SSE stream — rather than a divergent JS re-implementation of the tree/
    panes. (A headless-browser comparison is out of scope: E5 forbids a JS/node
    toolchain; the guarantee is structural — live and snapshot share one
    endpoint and thus one rendering path.)"""
    client, slug, _ = _client_with(tmp_path, "abcdef12", comprehensive_lines(), phase="done")
    js = client.get("/static/app.js").text

    # Driven by the stream ...
    assert "EventSource" in js and "onmessage" in js
    # ... but rendered by re-fetching the server snapshot and swapping it in place
    # (no page reload) — the SAME path that produced the initial page.
    assert "fetch(" in js
    assert "replaceWith" in js
    assert "run-header" in js and "detail" in js  # phase bar + tree/panes/problems
    # It must NOT re-implement node/detail rendering in JS (the divergent path the
    # previous defect used): building tree nodes/panes client-side is forbidden.
    assert "createElement" not in js

    # The snapshot the client re-fetches is COMPLETE, so once refreshed the live
    # view matches a freshly opened completed run: final answer, gate output,
    # review findings and raw stdout are all present in that one server page.
    html = client.get(f"/runs/{slug}/abcdef12").text
    for expected in (FINAL_ANSWER, GATE_OUTPUT, "Missing null check", CODEX_STDOUT):
        assert expected in html
