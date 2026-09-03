"""The GUI's own assets must always be revalidated.

`adw gui` is a local inspection tool whose stylesheet and script change whenever the
orchestrator is worked on. `StaticFiles` sends `etag` and `last-modified` but no
`Cache-Control`, and without one a browser applies HEURISTIC freshness: it reuses the
cached copy WITHOUT asking the server. Observed in Chrome after 0.20.1: the served
`app.css` carried the label-wrap fix, yet the page's stylesheet arrived with
`transferSize: 0` and the fix stayed invisible — a restarted server did not help, and
a hard reload in one tab did not help a newly opened one.

`no-cache` does not forbid caching; it forbids using a cached copy without
revalidating. With the ETag already present that costs one 304, so the fix is free.
"""

from fastapi.testclient import TestClient

from adw.gui.app import create_app

ASSETS = ("/static/app.css", "/static/app.js")


def _client():
    return TestClient(create_app(repos=[]))


def test_assets_are_served_with_no_cache():
    client = _client()

    for path in ASSETS:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "no-cache" in resp.headers.get("cache-control", ""), path


def test_revalidation_stays_cheap_via_the_etag():
    """The point of `no-cache` over `no-store`: the browser still keeps the copy and
    a conditional request answers 304 with no body."""
    client = _client()

    for path in ASSETS:
        etag = client.get(path).headers["etag"]
        again = client.get(path, headers={"if-none-match": etag})
        assert again.status_code == 304, path
        assert not again.content, path


def test_the_asset_body_is_still_served():
    """A cache header must not break delivery itself."""
    client = _client()

    assert "trace-summary" in client.get("/static/app.css").text
    assert "prettyPayload" in client.get("/static/app.js").text
