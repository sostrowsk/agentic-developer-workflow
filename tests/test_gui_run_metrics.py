"""RED tests for A1 — run metrics summed over the WHOLE run (AC 1–4, AC 14).

Today ``_summary`` (adw/gui/app.py) reports ``duration``/``cost`` from the LAST
``run`` span only; a gated run is several CLI commands (several ``run`` spans in
one log), so the numbers are the last section's, not the run's. This brief sums
``duration``, ``cost`` and ``tokens`` over ALL closed ``run`` spans while ``start``
stays the first span. An open (unfinished) span contributes nothing; without any
closed span the numbers stay ``null`` — never a fabricated ``0`` (E6) — but a real
``0`` from the payload is kept.

Addressed through the observable HTTP surface (``/api/runs``,
``/api/runs/{repo}/{run_id}`` and the rendered list ``/``) with FastAPI's
TestClient, exactly as the sibling run-list tests do; the contract pins the field
NAMES/TYPES, not the internal helper. Derived from .adw/spec.md (AC 1–4, 14),
.adw/contract.yaml (RunSummary, invariants) and .adw/plan.md (B1, B2).
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    ts,
    write_run,
)


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _by_id(entries, run_id):
    return next((e for e in entries if e.get("run_id") == run_id), None)


def multi_span_lines(specs, *, issue="Gated run", last_status="done"):
    """One run file holding several ``run`` spans, as a gated run really does. Each
    ``specs`` item is ``(duration, cost, tokens)`` for that span's ``totals``; every
    span but the last ends ``awaiting_approval``, the last ends ``last_status``. The
    first start's ts is the run's ``start``."""
    lines = []
    seq = 1
    sec = 0
    n = len(specs)
    for i, (duration, cost, tokens) in enumerate(specs):
        lines.append(rec(seq, "run", "start", f"R{i}", None, sec=sec,
                         payload=run_start_payload(issue)))
        seq += 1
        sec += 1
        status = last_status if i == n - 1 else "awaiting_approval"
        lines.append(rec(seq, "run", "end", f"R{i}", None, sec=sec,
                         payload=run_end_payload(status, duration, cost, tokens)))
        seq += 1
        sec += 1
    return lines


def _client(repo):
    return TestClient(create_app(repos=[str(repo)]))


# --- AC 1: sum over all closed spans -------------------------------------------


def test_metrics_summed_over_all_closed_run_spans(home, tmp_path):  # noqa: F811
    """AC 1: three closed spans with totals 100/200/300 s and 1/2/3 $ report
    ``duration`` 600 and ``cost`` 6 (not 300 / 3); tokens 10/20/30 sum to 60;
    ``duration`` equals ``work_seconds``; ``start`` stays the FIRST span."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111",
              multi_span_lines([(100, 1, 10), (200, 2, 20), (300, 3, 30)]),
              phase="done", issue="Gated run")

    entry = _by_id(_client(repo).get("/api/runs").json(), "aaaa1111")
    assert entry is not None
    assert entry["duration"] == 600
    assert entry["cost"] == 6
    assert entry["work_seconds"] == 600  # numerically identical to duration
    assert entry["tokens"] == 60
    assert entry["start"] == ts(0)  # the first span's start, unchanged


# --- AC 2: an open last span contributes nothing -------------------------------


def test_open_last_span_contributes_nothing(home, tmp_path):  # noqa: F811
    """AC 2: a run whose LAST span is still open counts only the closed spans; the
    open span adds nothing and raises no exception."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [
        rec(1, "run", "start", "R0", None, sec=0, payload=run_start_payload("Open tail")),
        rec(2, "run", "end", "R0", None, sec=1,
            payload=run_end_payload("awaiting_approval", 100, 1.0, 10)),
        rec(3, "run", "start", "R1", None, sec=2, payload=run_start_payload("Open tail")),
        # R1 never ends → still open, contributes no totals.
    ]
    write_run(repo, "bbbb2222", lines, phase="build", issue="Open tail")

    resp = _client(repo).get("/api/runs")
    assert resp.status_code == 200
    entry = _by_id(resp.json(), "bbbb2222")
    assert entry["duration"] == 100  # only the one closed span
    assert entry["cost"] == 1.0
    assert entry["work_seconds"] == 100
    assert entry["tokens"] == 10


# --- AC 3: no closed span → null, but a real 0 is kept -------------------------


def test_no_closed_span_is_null_while_a_real_zero_is_kept(home, tmp_path):  # noqa: F811
    """AC 3 / E6: with no closed span the metrics are ``null`` (never a fabricated
    0); a span whose payload carries a genuine 0 keeps the 0."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # A run with only an open span: no totals at all.
    write_run(repo, "cccc3333",
              [rec(1, "run", "start", "R", None, sec=0,
                   payload=run_start_payload("Never finished"))],
              phase="build", issue="Never finished")
    # A finished run whose payload totals are a genuine 0 (e.g. a mock runner).
    write_run(repo, "dddd4444",
              [rec(1, "run", "start", "R", None, sec=0,
                   payload=run_start_payload("Zero totals")),
               rec(2, "run", "end", "R", None, sec=1,
                   payload=run_end_payload("done", 0, 0, 0))],
              phase="done", issue="Zero totals")

    data = _client(repo).get("/api/runs").json()
    empty = _by_id(data, "cccc3333")
    assert empty["duration"] is None
    assert empty["cost"] is None
    assert empty["work_seconds"] is None
    assert empty["tokens"] is None

    zero = _by_id(data, "dddd4444")
    assert zero["duration"] == 0
    assert zero["cost"] == 0
    assert zero["work_seconds"] == 0
    assert zero["tokens"] == 0


# --- AC 4: a gated run reports the corrected, higher value ----------------------


def test_gated_run_costs_corrected_and_list_matches_detail(home, tmp_path):  # noqa: F811
    """AC 4: for a 16f39431-shaped run the list reports $56.88 (the SUM), not the
    last span's $47.16; for an e4e70373-shaped run whose last span cost $0.00 the
    reported cost is greater than zero. The run detail head reads the SAME value as
    the list (both feed from the one summary)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # 16f39431: costs 9.72 + 0.00 + 47.16 = 56.88 (last span 47.16 today).
    write_run(repo, "16f39431",
              multi_span_lines([(3000, 9.72, 100), (2341, 0.0, 0), (743, 47.16, 200)]),
              phase="done", issue="Gated run 16f39431")
    # e4e70373: costs 7.52 + 0.00 = 7.52 (last span 0.00 → today $0.00).
    write_run(repo, "e4e70373",
              multi_span_lines([(1061, 7.52, 300), (124, 0.0, 0)]),
              phase="done", issue="Gated run e4e70373")

    client = _client(repo)
    slug = _slug_for(repo)
    data = client.get("/api/runs").json()

    gated = _by_id(data, "16f39431")
    assert abs(gated["cost"] - 56.88) < 0.01  # the sum, not the last span (47.16)
    assert gated["cost"] > 47.16

    zero_tail = _by_id(data, "e4e70373")
    assert zero_tail["cost"] > 0  # not the last span's 0.00

    # The rendered list shows the corrected value, and the detail head agrees.
    list_html = client.get("/").text
    assert "$56.88" in list_html and "$47.16" not in list_html

    detail = client.get(f"/api/runs/{slug}/16f39431").json()["run"]
    assert detail["cost"] == gated["cost"]
    assert detail["duration"] == gated["duration"]


# --- AC 14: the contract fields survive; the new ones are additive --------------


def test_contract_fields_preserved_and_additive(home, tmp_path):  # noqa: F811
    """AC 14: every field of RunSummary keeps its name/type; ``issue`` stays the
    raw (truncated) issue value — NOT the derived display title; the four time-size
    fields and ``tokens`` are additionally present; ``tree`` stays a list."""
    repo = tmp_path / "repo"
    repo.mkdir()
    raw_issue = "# My Heading\n\nsome body text that is the real issue description"
    write_run(repo, "aaaa1111",
              [rec(1, "run", "start", "R", None, sec=0,
                   payload=run_start_payload(raw_issue)),
               rec(2, "phase", "start", "P", "R", sec=1,
                   payload={"name": "build", "from_phase": "build"}),
               rec(3, "phase", "end", "P", "R", sec=5,
                   payload={"name": "build", "to_phase": "done"}),
               rec(4, "run", "end", "R", None, sec=6,
                   payload=run_end_payload("done", 5, 1.0, 10))],
              phase="done", issue=raw_issue)

    client = _client(repo)
    slug = _slug_for(repo)
    entry = _by_id(client.get("/api/runs").json(), "aaaa1111")

    # The existing fields stay, with their types.
    assert isinstance(entry["run_id"], str)
    assert isinstance(entry["repo"], str)
    assert entry["repo_exists"] is True
    assert isinstance(entry["dry_run"], bool)
    assert entry["phase"] == "done"
    assert entry["status"] == "done"
    assert isinstance(entry["start"], str)
    assert isinstance(entry["event_count"], int)
    assert isinstance(entry["has_trace"], bool)

    # `issue` remains the RAW issue value (starts with the markdown heading), never
    # rewritten to the 90-char display title ("My Heading").
    assert entry["issue"].startswith("# My Heading")

    # The new additive fields are present.
    for key in ("work_seconds", "phase_seconds", "wait_seconds", "total_seconds", "tokens"):
        assert key in entry, key

    # The detail response carries the same run summary and an unchanged tree list.
    detail = client.get(f"/api/runs/{slug}/aaaa1111").json()
    assert detail["run"]["issue"].startswith("# My Heading")
    for key in ("work_seconds", "phase_seconds", "wait_seconds", "total_seconds", "tokens"):
        assert key in detail["run"], key
    assert isinstance(detail["tree"], list) and detail["tree"]
