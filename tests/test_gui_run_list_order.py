"""RED tests for the run-list status ordering (AC4 / contract R4).

The run list groups runs by status: every ``awaiting_approval`` run precedes
every ``running`` run, which precedes every run in the remaining-status group.
Within each group the existing newest-first ordering is preserved (Python's sort
is stable). The ``dry_run`` value affects neither group membership nor ordering.

Derived from .adw/spec.md (AC4), .adw/contract.yaml (x-behavior R4_run_list_order)
and .adw/plan.md (B2). RED until the sort key groups ``awaiting_approval`` ahead
of ``running`` ahead of the rest — today only ``running`` is pulled to the front,
so an ``awaiting_approval`` run sinks below newer finished runs.
"""

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)


def _client(repos):
    return TestClient(create_app(repos=[str(p) for p in repos]))


def awaited(issue, sec):
    """An OPEN ``run`` span paused at the plan gate — derived status
    ``awaiting_approval`` — starting at second ``sec``."""
    return [
        rec(1, "run", "start", "R", None, sec=sec, payload=run_start_payload(issue)),
        rec(2, "approval", "point", "R", sec=sec + 1, payload={"gate": "plan", "event": "awaited"}),
    ]


def running(issue, sec):
    """An OPEN ``run`` span with no approval pause — derived status ``running``."""
    return [
        rec(1, "run", "start", "R", None, sec=sec, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "P", "R", sec=sec + 1,
            payload={"name": "build", "from_phase": "build"}),
    ]


def done(issue, sec, *, dry_run=False):
    """A CLOSED ``run`` span — status ``done`` (the remaining-status group) —
    optionally flagged as a dry run."""
    payload = run_start_payload(issue)
    payload["dry_run"] = dry_run
    return [
        rec(1, "run", "start", "R", None, sec=sec, payload=payload),
        rec(2, "run", "end", "R", None, sec=sec + 3, payload=run_end_payload("done")),
    ]


def _order_client(tmp_path):
    """Four runs whose START order (newest first) deliberately CONTRADICTS the
    required group order, so grouping is what decides the result: the newest run of
    all is a dry-run ``done`` (must end up last), the ``running`` run is next-newest,
    and the two ``awaiting_approval`` runs are the oldest (must lead)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa0020", awaited("Awaiting newer", 20), phase="awaiting_approval")
    write_run(repo, "aaaa0010", awaited("Awaiting older", 10), phase="awaiting_approval")
    write_run(repo, "bbbb0030", running("Running", 30), phase="build")
    write_run(repo, "cccc0040", done("Done dry newest", 40, dry_run=True), phase="done")
    return _client([repo])


def test_status_groups_order_awaiting_then_running_then_rest(home, tmp_path):  # noqa: F811
    """AC4: ``awaiting_approval`` precedes ``running`` precedes the remaining
    statuses, regardless of start time."""
    data = _order_client(tmp_path).get("/api/runs").json()
    ids = [e["run_id"] for e in data if e.get("run_id")]
    pos = {rid: ids.index(rid) for rid in ("aaaa0020", "aaaa0010", "bbbb0030", "cccc0040")}

    assert pos["aaaa0020"] < pos["bbbb0030"]  # awaiting before running
    assert pos["aaaa0010"] < pos["bbbb0030"]
    assert pos["bbbb0030"] < pos["cccc0040"]  # running before the rest (done)

    by = {e["run_id"]: e for e in data if e.get("run_id")}
    assert by["aaaa0020"]["status"] == "awaiting_approval"
    assert by["bbbb0030"]["status"] == "running"
    assert by["cccc0040"]["status"] == "done"


def test_newest_first_within_group_and_dry_run_does_not_reorder(home, tmp_path):  # noqa: F811
    """AC4: within a group the newer start comes first, and ``dry_run`` changes
    neither group nor order — the dry-run ``done`` run has the newest start of all
    yet stays last."""
    data = _order_client(tmp_path).get("/api/runs").json()
    ids = [e["run_id"] for e in data if e.get("run_id")]

    # Newest-first within the awaiting_approval group (20 before 10).
    assert ids.index("aaaa0020") < ids.index("aaaa0010")
    # The dry-run run is newest of all but its flag never pulls it forward: last.
    assert ids.index("cccc0040") == max(
        ids.index(r) for r in ("aaaa0020", "aaaa0010", "bbbb0030", "cccc0040")
    )
    by = {e["run_id"]: e for e in data if e.get("run_id")}
    assert by["cccc0040"]["dry_run"] is True
