"""RED tests for GUI-Redesign 5 — the live payload (A1 partial response, A2 panes
on demand).

Derived from .adw/spec.md (AC 1–5, 10, 13), .adw/contract.yaml
(x-adw-template-behavior, x-adw-api-invariance, x-adw-response-size) and
.adw/plan.md (B2 rows 1–5, B3, B4). Markup wording and CSS/data-* attribute
NAMES are not contractual (contract-boundary); these tests therefore assert
observable structure through two agreed markers the implementation must emit:

  * the two live regions ``header.run-header`` and ``main.detail`` (already in the
    template, the same nodes ``swapRegions`` swaps), and
  * ``data-pane-unloaded`` on an un-rendered span-pane SHELL — the machine-readable
    "not yet loaded" marker of B4. A ``.pane`` carrying ``data-seq`` but NOT this
    marker is a rendered span-pane BODY; the shared generic point-node pane has no
    ``data-seq`` and never counts. This marker name is this suite's contract with
    the B4 implementation (the concrete name is a free choice of .adw/contract.yaml,
    pinned here so the pane-count acceptance is testable).

RED until app.py evaluates the ``X-Requested-With: fetch`` header (A1) and renders
only the focused span-pane's body (A2).
"""

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    build_diff_run,
    comprehensive_lines,
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

_FETCH = {"X-Requested-With": "fetch"}


def _app(tmp_path, run_id, lines, phase="done"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def _region(html, start, end):
    """The substring of ``html`` from the opening tag beginning ``start`` to the end
    of the first closing ``end`` tag — one live region, extracted by its stable
    outer tag so the comparison never pins the inner markup."""
    i = html.index(start)
    j = html.index(end) + len(end)
    return html[i:j]


def _strip_latest(s):
    """Remove the ``data-latest-context`` attribute wherever it sits, so the region
    equivalence check is blind to WHICH element carries the no-selection context
    (the full page keeps it on <body>, a fragment carries it inside a region)."""
    return re.sub(r"\s*data-latest-context='[^']*'", "", s)


def _span_pane_bodies(html):
    """The number of RENDERED span-pane bodies: a ``.pane`` with a ``data-seq`` that
    is not marked ``data-pane-unloaded``. Empty shells (marked) and the shared
    generic point-node pane (no ``data-seq``) do not count (AC 4)."""
    bodies = 0
    for attrs in re.findall(r'<div class="pane[^"]*"([^>]*)>', html):
        if "data-seq=" in attrs and "data-pane-unloaded" not in attrs:
            bodies += 1
    return bodies


# --- AC 1 / AC 3 (structural): the fetch-header partial response --------------------


def test_fetch_header_returns_only_the_two_regions_as_a_fragment(home, tmp_path):  # noqa: F811
    """AC 1: with ``X-Requested-With: fetch`` the route returns a bare HTML fragment —
    no ``<!DOCTYPE>``, ``<html>`` or ``<head>`` document wrapper — carrying exactly the
    two live regions ``header.run-header`` and ``main.detail`` in document order. At
    equal data and parameters each region is byte-identical to the full response's
    (same render path, A1). AC 3: the no-selection context stays reachable inside the
    fragment (``data-latest-context``)."""
    client, slug = _app(tmp_path, "aaaa1111", comprehensive_lines(issue="Payload issue"))
    url = f"/runs/{slug}/aaaa1111"

    full = client.get(url).text
    frag = client.get(url, headers=_FETCH).text

    low = frag.lower()
    assert "<!doctype" not in low
    assert "<html" not in low
    assert "<head>" not in low
    stripped = frag.strip()
    assert stripped.startswith('<header class="run-header"')
    assert stripped.endswith("</main>")

    # Exactly the two regions, in document order.
    assert '<header class="run-header"' in frag
    assert '<main class="detail"' in frag

    # Region equivalence to the full page (blind to where latest_context is carried).
    head_full = _region(full, '<header class="run-header"', "</header>")
    head_frag = _region(frag, '<header class="run-header"', "</header>")
    main_full = _region(full, '<main class="detail"', "</main>")
    main_frag = _region(frag, '<main class="detail"', "</main>")
    assert _strip_latest(head_frag) == _strip_latest(head_full)
    assert _strip_latest(main_frag) == _strip_latest(main_full)

    # AC 3: the no-selection context value travels with the fragment.
    assert "data-latest-context" in frag


def test_without_the_header_the_response_is_a_full_document(home, tmp_path):  # noqa: F811
    """AC 2: the same request WITHOUT the header stays the full document mode —
    ``<!DOCTYPE>``, ``<html>``, ``<head>`` and ``<body>`` — unchanged product
    behaviour. Only the header selects the fragment (E3)."""
    client, slug = _app(tmp_path, "aaaa1111", comprehensive_lines(issue="Payload issue"))
    full = client.get(f"/runs/{slug}/aaaa1111").text

    low = full.lower()
    assert "<!doctype html>" in low
    assert "<html" in low
    assert "<head>" in low
    assert "<body" in low


# --- AC 4 / AC 10: panes on demand, focus resolution --------------------------------


def test_span_pane_bodies_reduced_to_at_most_one(home, tmp_path):  # noqa: F811
    """AC 4: without ``?focus`` the delivered page (full OR fragment) carries ZERO
    span-pane bodies; with a span focus exactly ONE — the targeted node's — while
    every other span keeps an (empty, marked) shell. The shared point-node pane never
    counts. AC 10: the deep-link's existing focus resolution decides which pane."""
    client, slug = _app(tmp_path, "aaaa1111", comprehensive_lines(issue="Payload issue"))
    url = f"/runs/{slug}/aaaa1111"

    # No focus: no span-pane body in EITHER mode.
    assert _span_pane_bodies(client.get(url).text) == 0
    assert _span_pane_bodies(client.get(url, headers=_FETCH).text) == 0

    # Focus on the agent.run span (seq 5): exactly ONE span-pane body — its own — with
    # every other span left an (unloaded, marked) shell. The one-body count is the
    # acceptance; the loaded pane is seq 5 and carries the agent.run body. (Content
    # strings like the gate command or codex stdout are NOT a "no other body" signal:
    # the Raw tab previews every event's payload, so they appear regardless of which
    # pane body is rendered — the marker count, not the text, decides.)
    focused = client.get(url, params={"focus": 5}).text
    assert _span_pane_bodies(focused) == 1
    assert re.search(r'<div class="pane[^"]*" data-seq="5"(?![^>]*data-pane-unloaded)', focused)
    assert 'data-tabs' in focused  # the agent.run body (its tab group) is present

    # The fragment mode obeys the same one-body rule under focus.
    assert _span_pane_bodies(client.get(url, params={"focus": 5}, headers=_FETCH).text) == 1


def test_focus_on_a_foldable_result_is_redirected_to_its_call(home, tmp_path):  # noqa: F811
    """AC 10: the existing focus resolution is untouched — a ``?focus`` landing on an
    A1-foldable tool RESULT (Read result seq 8, tool_use_id ``t1``) is redirected to
    its call (seq 7), whose seq then carries the selection. The redirect holds with and
    without the fetch header. Neither seq is a span node, so no span-pane body is
    rendered."""
    client, slug = _app(tmp_path, "aaaa1111", comprehensive_lines(issue="Payload issue"))
    url = f"/runs/{slug}/aaaa1111"

    full = client.get(url, params={"focus": 8}).text
    assert 'data-focus="7"' in full  # the result (8) redirected to its call (7)
    assert _span_pane_bodies(full) == 0

    frag = client.get(url, params={"focus": 8}, headers=_FETCH).text
    assert _span_pane_bodies(frag) == 0


# --- AC 5: the delivered page is at least 35% smaller --------------------------------

# The contractual reference measurement (.adw/contract.yaml x-adw-response-size),
# pinned so the acceptance check is exact. The reference run 16f39431 is a REAL run
# measured on 2026-09-15; it is NOT checked in (runs fall under retention, plan B1/B2),
# so it is supplied out of band through $ADW_GUI_REFERENCE_REPO — a repo whose
# ``.adw/runs/16f39431`` reproduces that run. Absent it, AC 5's reference measurement is
# recorded as UNVERIFIED (below), never faked and never replaced by a synthetic fixture.
REFERENCE_RUN = "16f39431"
REFERENCE_DATE = "2026-09-15"
REFERENCE_BYTES = 847791          # x-adw-response-size.reference_bytes
MAXIMUM_BYTES = 551064            # x-adw-response-size.maximum_bytes (>= 35% reduction)
REFERENCE_REPO_ENV = "ADW_GUI_REFERENCE_REPO"


def _reference_repo():
    """The repo root containing the reference run, or None when it is not available."""
    root = os.environ.get(REFERENCE_REPO_ENV)
    if not root:
        return None
    run_dir = Path(root) / ".adw" / "runs" / REFERENCE_RUN
    reproducible = (run_dir / "events.jsonl").is_file() or (run_dir / "events.jsonl.gz").is_file()
    return Path(root) if reproducible else None


def test_reference_run_16f39431_meets_the_contractual_byte_maximum(home, tmp_path):  # noqa: F811
    """AC 5 (contractual reference): the page delivered for run ``16f39431`` WITHOUT
    ``?focus`` and WITHOUT the fetch header is at most ``551064`` uncompressed bytes —
    at least 35% below the ``847791``-byte reference of 2026-09-15. The reference run is
    supplied out of band via ``$ADW_GUI_REFERENCE_REPO``; when it is not reproducible
    this acceptance is EXPLICITLY RECORDED AS UNVERIFIED (a skip carrying the run,
    request parameters and the fixed maximum) rather than faked, invented or replaced by
    a synthetic fixture — the contract's ``baseline_rule`` forbids all three. The
    synthetic reduction test below is only supplementary structural coverage."""
    repo = _reference_repo()
    if repo is None:
        pytest.skip(
            f"AC 5 UNVERIFIED — reference run {REFERENCE_RUN} ({REFERENCE_DATE}) is not "
            f"reproducible in-suite (runs fall under retention, plan B1/B2). Set "
            f"${REFERENCE_REPO_ENV} to a repo whose .adw/runs/{REFERENCE_RUN} reproduces "
            f"it. Unmeasured here: GET /runs/<repo>/{REFERENCE_RUN} (no focus, no fetch "
            f"header) <= {MAXIMUM_BYTES} uncompressed bytes (reference {REFERENCE_BYTES}). "
            f"The synthetic reduction test is supplementary coverage only."
        )
    client = TestClient(create_app(repos=[str(repo)]))
    slug = next(e["repo"] for e in client.get("/api/runs").json()
                if e.get("run_id") == REFERENCE_RUN)
    resp = client.get(f"/runs/{slug}/{REFERENCE_RUN}")  # no ?focus, no fetch header
    assert resp.status_code == 200
    size = len(resp.content)
    assert size <= MAXIMUM_BYTES, (
        f"run {REFERENCE_RUN}, GET /runs/{slug}/{REFERENCE_RUN} (no focus, no fetch "
        f"header): {size} uncompressed bytes exceeds the contractual maximum "
        f"{MAXIMUM_BYTES} (reference {REFERENCE_BYTES} on {REFERENCE_DATE})"
    )


def _many_span_panes_lines(count, body_chars, *, issue="Many span panes"):
    """A run of ``count`` ``agent.run`` spans, each with a large unique prompt so its
    detail pane BODY is heavy. These bodies are the bulk that A2 stops shipping without
    ``?focus`` — the structure that produces the >=35% reduction the 62-pane reference
    run showed. (The literal reference run ``16f39431`` is a MANUAL measurement, not
    reproducible in-suite; a synthetic fixture never replaces it, so it is not renamed
    to impersonate it — see .adw/plan.md B1.)"""
    lines = [rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue))]
    seq = 2
    starts = []
    for i in range(count):
        prompt = f"agent-{i:03d} " + ("payload line for this pane\n" * (body_chars // 26))
        starts.append(seq)
        lines.append(rec(seq, "agent.run", "start", f"A{i}", "R", sec=1,
                         payload={"agent": f"agent_{i}", "prompt": prompt, "system_append": ""}))
        seq += 1
        lines.append(rec(seq, "agent.run", "end", f"A{i}", "R", sec=2,
                         payload={"result_text": f"answer {i}", "is_error": False}))
        seq += 1
    lines.append(rec(seq, "run", "end", "R", None, sec=3, payload=run_end_payload("done")))
    return lines, starts


def test_delivered_page_without_focus_is_at_least_35_percent_smaller(home, tmp_path):  # noqa: F811
    """AC 5 (SUPPLEMENTARY structural coverage — NOT the contractual reference
    measurement; that is
    ``test_reference_run_16f39431_meets_the_contractual_byte_maximum``): for a run whose
    span-pane bodies dominate the document, the page delivered WITHOUT ``?focus``
    (unencoded bytes) is at least 35% smaller than a page that inlined those bodies. The
    reference size is derived from the ACTUAL server-rendered body of each span (the
    per-focus byte delta), never fabricated; the request parameters (no focus, no fetch
    header) and the measured byte counts ride in the assertion message. This proves the
    mechanism generally; it does not stand in for the 16f39431 / 551064-byte acceptance."""
    lines, span_seqs = _many_span_panes_lines(30, 6000)
    client, slug = _app(tmp_path, "cccc3333", lines)
    url = f"/runs/{slug}/cccc3333"

    without = client.get(url).content
    base = len(without)
    # Each ?focus adds exactly one span-pane body; the sum of those deltas is the byte
    # mass A2 keeps off the no-focus page.
    bodies = sum(len(client.get(url, params={"focus": s}).content) - base for s in span_seqs)
    full_estimate = base + bodies
    reduction = bodies / full_estimate if full_estimate else 0.0

    assert _span_pane_bodies(without.decode()) == 0
    assert reduction >= 0.35, (
        f"run cccc3333, no ?focus, no fetch header: delivered {base} bytes, "
        f"excluded span-pane bodies {bodies} bytes -> reduction {reduction:.1%} (< 35%)"
    )


# --- AC 13: the /api routes stay unchanged across the new HTML behaviour --------------


def test_api_routes_unchanged_across_full_and_fragment_html_fetches(home, tmp_path):  # noqa: F811
    """AC 13: every ``/api`` route answers identically before and after an intervening
    full-document AND fragment HTML fetch — the deliberate HTML change touches no API
    field, type or value, and adds no route or query parameter. Uses the existing
    route fixtures rather than a new API matrix."""
    info = build_diff_run(tmp_path / "repo", run_id="aaaa1111")
    run_id = info["run_id"]
    (info["repo"] / ".adw" / "runs" / run_id / "issue.md").write_text("# issue\nbody\n")
    client = TestClient(create_app(repos=[str(info["repo"])]))
    slug = client.get("/api/runs").json()[0]["repo"]
    base = f"/api/runs/{slug}/{run_id}"

    def snapshot():
        return {
            "runs": client.get("/api/runs").json(),
            "detail": client.get(base).json(),
            "events": client.get(f"{base}/events").json(),
            "diff": client.get(
                f"{base}/diff", params={"from": info["ref1"], "to": info["ref2"]}
            ).json(),
            "artifact": client.get(f"{base}/artifacts/issue.md").text,
            "stream": client.get(f"{base}/stream").text,
        }

    before = snapshot()

    detail_url = f"/runs/{slug}/{run_id}"
    assert client.get(detail_url).status_code == 200          # full document
    assert client.get(detail_url, headers=_FETCH).status_code == 200  # fragment

    assert snapshot() == before, "an /api response changed across an HTML render"
