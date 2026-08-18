"""RED tests for Aufgabe B — the transparent gzip reader (AC C6).

Every event-log-reading route of the Run Inspector must read ``events.jsonl.gz``
with the SAME business output as ``events.jsonl``: same events in the same order,
same event count, same bad-line / seq-gap handling. When both files are present
the plain ``events.jsonl`` is authoritative (consistent with --gzip, C5).

Exercised through the HTTP surface (``adw.gui.app.create_app``) so no internal
reader signature is pinned. RED until the read paths learn ``.gz`` (today
``adw/gui/reader.py`` and ``app.py`` contain no gzip handling at all).
"""

import gzip

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    problems_lines,
    write_run,
)


def _gzip_only(run_dir):
    """Replace a run's events.jsonl with an equivalent events.jsonl.gz."""
    src = run_dir / "events.jsonl"
    data = src.read_bytes()
    with gzip.open(run_dir / "events.jsonl.gz", "wb") as fh:
        fh.write(data)
    src.unlink()


def test_gz_only_run_is_listed_and_readable(home, tmp_path):  # noqa: F811
    """C6: a run whose ONLY log is events.jsonl.gz stays discoverable and fully
    readable — its event count and detail tree match a plain-log twin exactly."""
    repo = tmp_path / "repo"
    repo.mkdir()
    plain_dir = write_run(repo, "aaaa1111", comprehensive_lines(), phase="done")  # noqa: F841
    gz_dir = write_run(repo, "bbbb2222", comprehensive_lines(), phase="done")
    _gzip_only(gz_dir)

    client = TestClient(create_app(repos=[str(repo)]))
    listing = {e["run_id"]: e for e in client.get("/api/runs").json()}
    assert "bbbb2222" in listing, "gz-only run not discovered"
    assert listing["bbbb2222"]["event_count"] == listing["aaaa1111"]["event_count"]
    assert listing["bbbb2222"]["event_count"] > 0

    slug = listing["bbbb2222"]["repo"]
    gz_events = client.get(f"/api/runs/{slug}/bbbb2222/events").json()
    plain_events = client.get(f"/api/runs/{slug}/aaaa1111/events").json()
    assert gz_events == plain_events

    gz_detail = client.get(f"/api/runs/{slug}/bbbb2222").json()
    plain_detail = client.get(f"/api/runs/{slug}/aaaa1111").json()
    assert gz_detail["tree"] == plain_detail["tree"]


def test_both_present_prefers_plain_events_jsonl(home, tmp_path):  # noqa: F811
    """C6/C5: when both events.jsonl and events.jsonl.gz exist the plain file is
    authoritative — a stale/divergent .gz is ignored."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = write_run(repo, "aaaa1111", comprehensive_lines(), phase="done")
    plain = (run_dir / "events.jsonl").read_bytes()
    # A .gz whose content differs from the plain file — it must NOT win.
    with gzip.open(run_dir / "events.jsonl.gz", "wb") as fh:
        fh.write(b'{"seq": 1, "type": "run", "kind": "start", "span": "X", "payload": {}}\n')

    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    events = client.get(f"/api/runs/{slug}/aaaa1111/events").json()
    # The authoritative plain log has the full comprehensive event set, not the
    # single stale record from the .gz.
    assert len(events) == plain.decode().count("\n")
    assert len(events) > 1


def test_gz_run_reports_same_bad_line_and_seq_gap(home, tmp_path):  # noqa: F811
    """C6: reader problems (bad_line, seq_gap) are reported identically whether the
    log is plain or gzipped."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", problems_lines(), phase="done")
    gz_dir = write_run(repo, "bbbb2222", problems_lines(), phase="done")
    _gzip_only(gz_dir)

    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    plain_problems = client.get(f"/api/runs/{slug}/aaaa1111").json()["problems"]
    gz_problems = client.get(f"/api/runs/{slug}/bbbb2222").json()["problems"]
    kinds = {p["kind"] for p in plain_problems}
    assert {"bad_line", "seq_gap"} <= kinds
    assert gz_problems == plain_problems
