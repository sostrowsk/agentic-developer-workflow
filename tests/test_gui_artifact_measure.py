"""Tests for the third measure ``adw:artifact``."""

import os
import pathlib

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (
    HUGE_ARTIFACT_HEAD,
    HUGE_ARTIFACT_TAIL,
    RUN_ID,
    huge_artifact_body,
    write_artifacts_run,
)
from tests.gui_js_harness import run_scenario


def _client_script():
    return TestClient(create_app(repos=[])).get("/static/app.js").text


def _guide_text():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    return (repo_root / "docs" / "gui-response-time.md").read_text(encoding="utf-8").lower()


# --- C1: completion semantics (behavioural) -------------------------------------


def test_artifact_measure_completes_only_after_post_paint(tmp_path):
    """C1: opening an artifact sets ``adw:artifact:start`` at the opening event, and
    the ``adw:artifact`` measure completes ONLY after the post-paint rAF -> task
    sequence — never at request dispatch, response arrival, or the rAF callback
    alone (which runs before the paint)."""
    r = run_scenario(tmp_path, "artifact")

    # Start mark is set at the opening event; nothing is complete yet.
    assert r["afterDispatch"]["start"] >= 1
    assert r["afterDispatch"]["measure"] == 0

    # Response arrived and the slice is in the DOM — but the paint task has not run.
    assert r["afterResponse"]["measure"] == 0
    # A rAF callback alone runs BEFORE the paint: still not complete.
    assert r["afterRaf"]["measure"] == 0

    # Only after the task scheduled from within the rAF (after the paint) does the
    # measure complete — exactly once, with its end mark.
    assert r["afterTimer"]["measure"] == 1
    assert r["afterTimer"]["end"] == 1


def test_artifact_measure_counts_the_async_rendered_content(tmp_path):
    """C1 (root cause) / E8 / E10: the measure covers the asynchronously fetched
    content — the bounded initial slice is inserted as faithful monospace text (the
    head is present) and, being bounded, the tail is not inlined initially while the
    full content stays reachable through the artifacts route."""
    r = run_scenario(tmp_path, "artifact")

    assert r["afterResponse"]["has_head"] is True       # fetched content is rendered
    assert r["afterResponse"]["has_tail"] is False      # the render is bounded
    assert r["afterResponse"]["pre_text_len"] > r["afterDispatch"]["pre_text_len"]


# --- C1: served-script text (pinned names + shared construction) ----------------


def test_client_script_carries_the_artifact_marks_and_measure(home):
    """C1: the served client script carries the pinned ``adw:artifact`` mark and
    measure names and completes the measure through the SAME promise-gated,
    post-paint helper as node selection and tab switch."""
    js = _client_script()

    for name in ("adw:artifact:start", "adw:artifact:end", "adw:artifact"):
        assert name in js, name
    # Same construction as adw:select / adw:tab: the async load promise gates the
    # post-paint end mark.
    assert 'perfEndAfterContent("adw:artifact:start"' in js


# --- C3 / A1: the guide documents the measure, its threshold and the selectors ---


def test_guide_documents_the_artifact_measure_and_entry_selectors():
    """C3 / A1: ``docs/gui-response-time.md`` documents ``adw:artifact`` alongside
    the other two measures (how to read it, the ≤ 2000 ms threshold, the ≥ 2 MB C2
    reference) AND the A1 entry-node selectors and the 200-per-collection cap the
    automated tests count."""
    text = _guide_text()

    for token in (
        "adw:artifact",       # the third measure, documented
        "getentriesbyname",   # how to read it
        "2000",               # the ≤ 2000 ms threshold
        "mb",                 # the >= 2 MB C2 reference measurement
        "data-tree-entry",    # the A1 trace-tree entry selector
        "data-tool-entry",    # the A1 Tools entry selector
    ):
        assert token in text, token


def test_huge_artifact_reference_is_at_least_2mb_and_fully_reachable(home, tmp_path):
    """C2 shape guard: the manual measurement's reference artifact is at least 2 MB
    and its COMPLETE content (head + tail) stays reachable through the read-only
    artifacts route, while the initial detail render does not inline the tail — the
    bounded display keeps the full content reachable (E8/E10)."""
    body = huge_artifact_body()
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_artifacts_run(repo, RUN_ID, large_name="plan.md", large_body=body)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug(os.path.normpath(str(repo.resolve())))

    served = client.get(f"/api/runs/{slug}/{RUN_ID}/artifacts/plan.md").text
    assert len(served.encode("utf-8")) >= 2 * 1024 * 1024       # >= 2 MB, node/byte shape
    assert HUGE_ARTIFACT_HEAD in served and HUGE_ARTIFACT_TAIL in served  # full content

    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    assert HUGE_ARTIFACT_TAIL not in html                       # bounded initial render
