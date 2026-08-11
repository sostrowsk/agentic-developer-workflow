"""RED tests for Aufgabe C — measurable response time.

The served vanilla client instruments the TWO interactions the issue names — node
selection and tab switch — with the ``performance`` API only (no new dependency,
no browser automation). Each sets a start ``performance.mark()`` at the triggering
input event and an end mark only once the interaction is observably complete: the
content is in the DOM AND the browser has painted it. Because a
``requestAnimationFrame`` callback runs BEFORE the paint, the end mark is recorded
in a task scheduled from WITHIN a rAF callback (rAF → ``setTimeout(0)``), which
runs after that paint. A named ``performance.measure()`` between the marks is
readable via ``performance.getEntriesByName(...)``. Pinned names:

  node selection: adw:select:start / adw:select:end, measure adw:select
  tab switch:     adw:tab:start / adw:tab:end,       measure adw:tab

Per the Codex-round-1 descope carried in .adw/plan.md §5, exactly these two
interactions are instrumented — no third measure and no supersession protocol.

Derived from .adw/spec.md (C1–C4, as descoped), .adw/contract.yaml
(x-adw-performance, x-adw-guide) and .adw/plan.md §5/§6. The tests assert the
served client-script TEXT — wiring and stable names only, not runtime behavior.
RED until the instrumentation and the guide exist.
"""

import pathlib

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import home  # noqa: F401 — used as a fixture


def _client_script():
    return TestClient(create_app(repos=[])).get("/static/app.js").text


def test_client_script_contains_the_pinned_mark_and_measure_names(home):  # noqa: F811
    """C2: the served client script carries the pinned mark and measure names for
    both instrumented interactions, and no third measure name is introduced."""
    js = _client_script()

    for name in (
        "adw:select:start", "adw:select:end", "adw:select",
        "adw:tab:start", "adw:tab:end", "adw:tab",
    ):
        assert name in js, name
    # Review descope: no third measure (artifact opening) and no generation
    # protocol are built.
    assert "adw:artifact" not in js


def test_both_interactions_use_the_performance_api(home):  # noqa: F811
    """C1/C4: both interactions are instrumented with the vanilla ``performance``
    API — start marks and a named measure between them — and no browser-automation
    tool is pulled in."""
    js = _client_script()

    assert "performance.mark" in js
    assert "performance.measure" in js
    # No browser-automation dependency.
    for banned in ("playwright", "selenium", "puppeteer"):
        assert banned not in js.lower(), banned


def test_end_mark_is_scheduled_from_a_raf_callback_after_paint(home):  # noqa: F811
    """C1: the end mark is recorded in a task scheduled from WITHIN a
    ``requestAnimationFrame`` callback (rAF → ``setTimeout``), which runs AFTER the
    paint — a rAF callback alone runs before the paint and is insufficient. Both
    primitives of the post-paint sequence appear in the client script."""
    js = _client_script()

    assert "requestAnimationFrame" in js
    assert "setTimeout" in js
    # The post-paint task records an end mark (proximity: setTimeout precedes an
    # end-mark call in the script text).
    tail = js[js.find("requestAnimationFrame"):]
    assert "setTimeout" in tail and ":end" in tail


def test_measurement_guide_document_is_present_and_complete():
    """C3 / DoD: a short guide document in the repo names the same two measures,
    how to read them (``getEntriesByName``), the exact post-paint completion
    sequence, the passing value (≤ 2000 ms), the ``agent.run``-with-large-payload
    node to pick, and the manual checklist (active/waiting A3, bar-click A5)."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    candidates = list(repo_root.glob("*.md")) + list(repo_root.glob("docs/**/*.md"))
    guides = [
        p for p in candidates
        if "adw:select" in p.read_text(encoding="utf-8", errors="ignore")
        and "adw:tab" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert guides, "no guide document naming both adw:select and adw:tab measures"

    text = guides[0].read_text(encoding="utf-8", errors="ignore").lower()
    for token in (
        "getentriesbyname",       # how to read the measure
        "requestanimationframe",  # the post-paint completion sequence
        "settimeout",
        "2000",                   # the passing value (<= 2000 ms)
        "agent.run",              # which node to pick (large payload)
        "checklist",              # the documented-manual checklist
        "waiting",                # A3 active/waiting distinction
        "bar",                    # A5 bar-click navigation
    ):
        assert token in text, token
