"""RED tests for A3 / Contract R3 — ``awaiting`` in the phase bar.

For a run paused at an approval gate, the waiting business phase reports the new
status ``awaiting`` instead of ``active``: the spec gate (``approval`` event
``gate: spec``; fallback state phase ``awaiting_spec_approval``) marks phase
``spec``; the plan gate (``gate: plan``; fallback ``awaiting_approval``) marks
phase ``plan``. The awaiting phase is never simultaneously
``active``/``pending``/``completed``, no other phase is ``active`` at the same
time, and after the matching ``granted`` the bar follows the normal progression.
``RunDetail.phases`` keeps its seven business-phase cells — no approval phase cell
is added.

Derived from .adw/spec.md (AC3), .adw/contract.yaml (x-behavior.R3_phase_awaiting)
and .adw/plan.md (B3). The ``phases[*].status`` field is the pinned surface. RED
until ``_phase_bar`` learns ``awaiting``.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    PHASE_ORDER,
    home,
    rec,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _phases(tmp_path, lines, *, phase, run_id=RUN_ID):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    cells = client.get(f"/api/runs/{slug}/{run_id}").json()["phases"]
    return {c["name"]: c["status"] for c in cells}


def spec_gate_lines(issue="Spec gate pause"):
    """A run paused at the SPEC gate: the ``spec`` phase span ended into
    ``awaiting_spec_approval``, an ``approval`` ``awaited`` (``gate: spec``) event
    sits on the still-open ``run`` span."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "S", "R", sec=1, payload={"name": "spec", "from_phase": "spec"}),
        rec(3, "phase", "end", "S", "R", sec=2,
            payload={"name": "spec", "to_phase": "awaiting_spec_approval"}),
        rec(4, "approval", "point", "R", sec=3, payload={"gate": "spec", "event": "awaited"}),
    ]


def plan_gate_lines(*, granted=False, issue="Plan gate pause"):
    """A run paused at the PLAN gate: ``spec`` completed (into ``plan``), the
    ``plan`` phase span ended into ``awaiting_approval``, an ``approval``
    ``awaited`` (``gate: plan``) event on the open ``run`` span. With ``granted``
    a later ``approval`` ``granted`` lifts the pause and ``build`` starts."""
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "S", "R", sec=1, payload={"name": "spec", "from_phase": "spec"}),
        rec(3, "phase", "end", "S", "R", sec=2, payload={"name": "spec", "to_phase": "plan"}),
        rec(4, "phase", "start", "PL", "R", sec=3, payload={"name": "plan", "from_phase": "plan"}),
        rec(5, "phase", "end", "PL", "R", sec=4,
            payload={"name": "plan", "to_phase": "awaiting_approval"}),
        rec(6, "approval", "point", "R", sec=5, payload={"gate": "plan", "event": "awaited"}),
    ]
    if granted:
        lines.append(
            rec(7, "approval", "point", "R", sec=6, payload={"gate": "plan", "event": "granted"})
        )
        lines.append(
            rec(8, "phase", "start", "B", "R", sec=7,
                payload={"name": "build", "from_phase": "build"})
        )
    return lines


def _assert_single_awaiting(status_by_name, awaiting_phase):
    """The named phase is the ONLY ``awaiting`` cell, and no phase is ``active`` at
    the same time; the seven business-phase cells are all still present."""
    assert set(status_by_name) == set(PHASE_ORDER)  # no approval-phase cell added
    assert status_by_name[awaiting_phase] == "awaiting"
    assert "active" not in status_by_name.values()  # the pause is not "active"
    for name, status in status_by_name.items():
        if name == awaiting_phase:
            # The awaiting phase is not simultaneously anything else.
            assert status not in ("active", "pending", "completed", "failed")


def test_spec_gate_marks_the_spec_phase_awaiting(home, tmp_path):  # noqa: F811
    """R3: at the spec gate the ``spec`` phase is ``awaiting`` and nothing is
    ``active``; the later phases stay ``pending``."""
    status_by_name = _phases(tmp_path, spec_gate_lines(), phase="awaiting_spec_approval")

    _assert_single_awaiting(status_by_name, "spec")
    assert status_by_name["plan"] == "pending"
    assert status_by_name["build"] == "pending"


def test_plan_gate_marks_the_plan_phase_awaiting(home, tmp_path):  # noqa: F811
    """R3: at the plan gate the ``plan`` phase is ``awaiting`` while ``spec``
    (already done) stays ``completed`` and nothing is ``active``."""
    status_by_name = _phases(tmp_path, plan_gate_lines(), phase="awaiting_approval")

    _assert_single_awaiting(status_by_name, "plan")
    assert status_by_name["spec"] == "completed"


def test_after_granted_the_phase_bar_follows_the_normal_progression(
    home, tmp_path  # noqa: F811
):
    """R3: once the plan gate is ``granted``, no phase is ``awaiting`` any more and
    the bar resumes normally — ``build`` is ``active`` again."""
    status_by_name = _phases(tmp_path, plan_gate_lines(granted=True), phase="build")

    assert "awaiting" not in status_by_name.values()
    assert status_by_name["build"] == "active"
    assert status_by_name["plan"] == "completed"
