"""RED tests for A2 / Contract R2 — ``awaiting_approval`` as run status.

While a run's ``run`` span is OPEN and its most recent ``approval`` event is
``event: awaited`` with no later ``event: granted``, both read endpoints report
``awaiting_approval`` instead of ``running`` — in the same, already-present
``status`` field. A later ``granted`` returns the still-open run to ``running``;
a CLOSED ``run`` span keeps the terminal status from its end payload untouched.
Without a trace, a run whose state phase is ``awaiting_approval`` or
``awaiting_spec_approval`` reports ``awaiting_approval``; a state-only run outside
those phases stays statusless (``null``).

Derived from .adw/spec.md (AC2), .adw/contract.yaml
(x-behavior.R2_run_awaiting_approval) and .adw/plan.md (B2). RED until
``_summary`` derives the open-span awaiting status. The event log is
authoritative when a trace exists; the state phase is only the no-trace fallback.
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
    write_run,
    write_state_only_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _client(tmp_path, lines, *, run_id=RUN_ID, phase="build", state_only=False):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    if state_only:
        write_state_only_run(repo, run_id, phase=phase)
    else:
        write_run(repo, run_id, lines, phase=phase)
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo), run_id


def _list_status(client, run_id):
    entry = next(e for e in client.get("/api/runs").json() if e.get("run_id") == run_id)
    return entry["status"]


def _detail_status(client, slug, run_id):
    return client.get(f"/api/runs/{slug}/{run_id}").json()["run"]["status"]


def open_run_awaited_lines(gate="plan", *, granted=False, issue="Awaiting approval"):
    """An OPEN ``run`` span (no matching ``run`` end) carrying an ``approval``
    ``awaited`` point event on the run span — optionally followed by a ``granted``
    event that lifts the pause. The approval events live on the run span exactly as
    the CLI emits them (cli.py:480/721)."""
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "approval", "point", "R", sec=1, payload={"gate": gate, "event": "awaited"}),
    ]
    if granted:
        lines.append(
            rec(3, "approval", "point", "R", sec=2, payload={"gate": gate, "event": "granted"})
        )
    return lines  # the run span deliberately stays open (no `run` end)


def closed_run_after_awaited_lines(status="done", issue="Closed after awaited"):
    """A CLOSED ``run`` span whose last ``approval`` event is ``awaited`` (no
    ``granted``) yet whose span ended with ``status`` in its payload. The terminal
    end-payload status must win — the open-span derivation must not touch it."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "approval", "point", "R", sec=1, payload={"gate": "plan", "event": "awaited"}),
        rec(3, "run", "end", "R", None, sec=2, payload=run_end_payload(status)),
    ]


def test_open_run_with_awaited_approval_is_awaiting_in_both_endpoints(
    home, tmp_path  # noqa: F811
):
    """R2: an open run whose latest ``approval`` is ``awaited`` reports
    ``awaiting_approval`` (not ``running``) identically in the list and the
    detail."""
    client, slug, run_id = _client(tmp_path, open_run_awaited_lines(), phase="awaiting_approval")

    assert _list_status(client, run_id) == "awaiting_approval"
    assert _detail_status(client, slug, run_id) == "awaiting_approval"
    # Both endpoints agree on the same run.
    assert _list_status(client, run_id) == _detail_status(client, slug, run_id)


def test_a_later_granted_returns_the_open_run_to_running(home, tmp_path):  # noqa: F811
    """R2: once a later ``approval`` ``granted`` follows, the still-open run is
    ``running`` again — the event log, not the state phase, decides."""
    client, slug, run_id = _client(
        tmp_path, open_run_awaited_lines(granted=True), phase="build"
    )

    assert _list_status(client, run_id) == "running"
    assert _detail_status(client, slug, run_id) == "running"


def test_closed_run_span_keeps_its_terminal_status_despite_a_prior_awaited(
    home, tmp_path  # noqa: F811
):
    """R2: a CLOSED ``run`` span keeps the end-payload status (``done``) even
    though its last ``approval`` event is ``awaited`` — the derivation never
    rewrites a terminal result."""
    client, slug, run_id = _client(
        tmp_path, closed_run_after_awaited_lines("done"), phase="done"
    )

    assert _list_status(client, run_id) == "done"
    assert _detail_status(client, slug, run_id) == "done"


def test_state_phase_is_the_no_trace_fallback_but_not_a_false_status(
    home, tmp_path  # noqa: F811
):
    """R2 (E4): without a trace, state phase ``awaiting_approval`` /
    ``awaiting_spec_approval`` reports ``awaiting_approval``; a state-only run
    outside those phases stays statusless (never a false ``running`` /
    ``awaiting_approval``)."""
    for phase in ("awaiting_approval", "awaiting_spec_approval"):
        client, slug, run_id = _client(tmp_path / phase, None, state_only=True, phase=phase)
        assert _detail_status(client, slug, run_id) == "awaiting_approval"
        assert _list_status(client, run_id) == "awaiting_approval"

    client, slug, run_id = _client(tmp_path / "done", None, state_only=True, phase="done")
    assert _detail_status(client, slug, run_id) in (None, "")
    assert _detail_status(client, slug, run_id) != "awaiting_approval"
    assert _list_status(client, run_id) in (None, "")
