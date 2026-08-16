"""Behavioural test: the live-refresh region swap must keep the paged window.

The trace tree and the Tools list are windowed (Aufgabe A) — at most 200 entry
nodes per collection live in the DOM at any time. What makes that bound legitimate
is REACHABILITY: every entry is reachable through a moving window driven by the
``offset`` / ``tools_offset`` (+ ``focus``) query parameters.

The wholesale live region swap re-fetches the detail page and replaces
``main.detail``. If that GET drops the query string, the server renders its
DEFAULT (first) window and the swap throws the user's paged position away — on a
running run that happens every 200 ms, and even a completed run does it once (the
``run``/``end`` record triggers a final refresh). The bounded DOM would then be
bounded but no longer reachable.

Verified through the dependency-free JS harness against the SERVED ``app.js``
(no browser automation — the post-paint end mark hangs off
``requestAnimationFrame``, which does not fire in a hidden automation tab).
"""

from tests.gui_js_harness import run_scenario

WINDOW_QUERY = "?offset=1100&tools_offset=200&focus=42"


def test_live_refresh_keeps_the_paged_window(tmp_path):
    """With a window in effect, the refresh GET carries that exact query string —
    tree offset, tools offset and focus alike."""
    r = run_scenario(tmp_path, "refresh-window", WINDOW_QUERY)

    assert r["refresh_fetch_count"] == 1, "expected exactly one detail-page GET"
    assert r["refresh_url"].endswith(WINDOW_QUERY), (
        "the live refresh dropped the paged window; it fetched "
        f"{r['refresh_url']!r} instead of the current window {WINDOW_QUERY!r}"
    )


def test_live_refresh_without_a_window_fetches_the_plain_url(tmp_path):
    """No window in effect: the GET stays the plain detail URL — no stray ``?``."""
    r = run_scenario(tmp_path, "refresh-window", "")

    assert r["refresh_fetch_count"] == 1
    assert "?" not in r["refresh_url"], (
        f"expected the plain detail URL without a query, got {r['refresh_url']!r}"
    )
    assert r["refresh_url"].endswith("/runs/repo/aaaa1111")


def test_live_refresh_keeps_a_tools_only_window(tmp_path):
    """Paging the Tools list alone is preserved too — the two windows are
    independent, so a tools-only query must survive the swap unchanged."""
    r = run_scenario(tmp_path, "refresh-window", "?tools_offset=300")

    assert r["refresh_url"].endswith("?tools_offset=300")
