"""RED tests for Breakpoints — configurable holds as a generalized approval.

Derived from .adw/spec.md (AC1–AC14), .adw/contract.yaml (C1_config…C7_no_approval)
and .adw/plan.md (B1–B10). Every assertion is over an externally observable
surface only — the `breakpoints` config key, the persisted `pending_breakpoint`
state field, the process exit code, the `approval` event log and the read-only
GUI model. No internal helper signature, module layout or step order is pinned
(contract preamble).

The orchestrator pause/continue behaviour is exercised through the real CLI in
dry-run mode against the mock runners (pattern: tests/test_e2e_dry_run.py):
0 tokens, no network, real git in tmp_path.

RED until the `breakpoints` config field, the `pending_breakpoint` state field
and the breakpoint gates in the orchestrator/CLI exist.
"""

import contextlib
import json
import os
import typing

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from adw.cli import app
from adw.config import AdwConfig, ConfigError
from adw.state import Phase, RunState
from tests.conftest import git, write_config

runner = CliRunner()


# --- config templates (committed before every CLI run) ------------------------

SINGLE = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
ci:
  provider: gitlab
  staging_job: deploy-staging
"""

PARALLEL = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
  frontend:
    gates:
      - {name: pass-gate, cmd: "true", timeout: 10}
e2e:
  cmd: "true"
  timeout: 60
ci:
  provider: gitlab
  staging_job: deploy-staging
"""

# Config for the pure config-validation tests (no run, no commit needed).
CFG_BASE = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: 5}
"""


def setup_config(repo, *breakpoints, parallel=False):
    """Write and COMMIT a config with the given active breakpoints — the worktree
    preflight requires the main checkout to be clean before `adw run`/`approve`."""
    body = PARALLEL if parallel else SINGLE
    if breakpoints:
        body += "breakpoints:\n" + "".join(f"  - {name}\n" for name in breakpoints)
    write_config(repo, body)
    git(repo, "add", ".adw/config.yaml")
    # --allow-empty: the no-breakpoints body can be byte-identical to the fixture's.
    git(repo, "commit", "--allow-empty", "-m", "breakpoints config")


# --- CLI + event-log helpers --------------------------------------------------


def cli_run(repo, *extra, issue="Breakpoint-Demo"):
    return runner.invoke(app, ["run", "--repo", str(repo), "--issue", issue, "--dry-run", *extra])


def approve(repo, run_id, *extra):
    return runner.invoke(app, ["approve", run_id, "--repo", str(repo), *extra])


def resume(repo, run_id):
    return runner.invoke(app, ["resume", run_id, "--repo", str(repo)])


def latest_id(repo):
    return RunState.find_latest(repo).run_id


def read_events(repo, run_id):
    path = repo.resolve() / ".adw" / "runs" / run_id / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def of_type(records, type_):
    return [r for r in records if r.get("type") == type_]


def approval_pairs(repo, run_id):
    """Every `approval` event as (gate, event) tuples, in log order."""
    pairs = []
    for record in read_events(repo, run_id):
        if record.get("type") == "approval":
            payload = record.get("payload") or {}
            pairs.append((payload.get("gate"), payload.get("event")))
    return pairs


def _strip_events(repo, run_id, *, gate, event):
    """Delete matching `approval` events from events.jsonl — simulates a crash
    between persisting the waiting state and appending the event."""
    path = repo.resolve() / ".adw" / "runs" / run_id / "events.jsonl"
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)
        payload = record.get("payload") or {}
        if (
            record.get("type") == "approval"
            and payload.get("gate") == gate
            and payload.get("event") == event
        ):
            continue
        kept.append(line)
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


@contextlib.contextmanager
def crash_agent(monkeypatch, agent_name):
    """Run the next CLI invocation with the given dry-run agent's queue emptied —
    its first call raises, crashing the run at the persisted phase checkpoint."""
    import adw.cli as cli_mod

    real = cli_mod._dry_run_runners

    def crashing():
        agents, codex = real()
        agents.scripts[agent_name].clear()
        return agents, codex

    with monkeypatch.context() as m:
        m.setattr(cli_mod, "_dry_run_runners", crashing)
        yield


@contextlib.contextmanager
def crash_after_one(monkeypatch, agent_name):
    """Like crash_agent, but leaves exactly ONE response so the phase makes
    partial progress (checkpointed) before the crash — mirrors test_dod5."""
    import adw.cli as cli_mod

    real = cli_mod._dry_run_runners

    def crashing():
        agents, codex = real()
        agents.scripts[agent_name].clear()
        agents.script(agent_name, "nur ein Lauf")
        return agents, codex

    with monkeypatch.context() as m:
        m.setattr(cli_mod, "_dry_run_runners", crashing)
        yield


# --- AC1 / AC3 / E5: config validation (C1_config) ----------------------------


def test_breakpoints_absent_defaults_to_empty_list(target_repo):
    """AC2: a config without the key behaves as today — an empty breakpoint set."""
    write_config(target_repo, CFG_BASE)
    assert AdwConfig.load(target_repo).breakpoints == []


def test_breakpoints_empty_list_is_valid(target_repo):
    write_config(target_repo, CFG_BASE + "breakpoints: []\n")
    assert AdwConfig.load(target_repo).breakpoints == []


def test_both_breakpoint_values_are_accepted(target_repo):
    """AC1: the only allowed elements are the two enumerated strings."""
    write_config(
        target_repo,
        CFG_BASE + "breakpoints:\n  - before_integration\n  - before_push\n",
    )
    assert AdwConfig.load(target_repo).breakpoints == ["before_integration", "before_push"]


@pytest.mark.parametrize(
    "block",
    [
        "breakpoints:\n  - after_round:2\n",  # unknown free-schema value (E2)
        "breakpoints:\n  - befor_integration\n",  # typo
        "breakpoints:\n  - before_push_extra\n",  # near-miss
        "breakpoints:\n  before_integration: true\n",  # mapping, not a list
        "breakpoints:\n  - true\n",  # boolean element
        "breakpoints:\n  - 5\n",  # integer element
    ],
)
def test_invalid_breakpoints_value_is_a_config_error(target_repo, block):
    """AC3/E5: any other value is a config error under the existing strict
    validation and the run does not start — never silently ignored."""
    write_config(target_repo, CFG_BASE + block)
    with pytest.raises(ConfigError):
        AdwConfig.load(target_repo)


# --- AC6 / C4_state: the `pending_breakpoint` field, no new Phase literal ------


def test_pending_breakpoint_defaults_to_none():
    assert RunState.new(issue="x", parallel=False).pending_breakpoint is None


def test_old_state_without_the_field_loads_as_none(target_repo):
    """C4_state: an old state.json without the field reads as null."""
    state = RunState.new(issue="x", parallel=False)
    data = json.loads(state.model_dump_json())
    data.pop("pending_breakpoint", None)
    run_dir = target_repo / ".adw" / "runs" / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(data), encoding="utf-8")
    assert RunState.load(target_repo, state.run_id).pending_breakpoint is None


def test_pending_breakpoint_survives_save_and_load(target_repo):
    state = RunState.new(issue="x", parallel=False)
    state.pending_breakpoint = "before_push"
    state.save(target_repo)
    assert RunState.load(target_repo, state.run_id).pending_breakpoint == "before_push"


@pytest.mark.parametrize("bad", ["nope", "after_round:2", "integration"])
def test_pending_breakpoint_rejects_unknown_values(bad):
    with pytest.raises(ValidationError):
        RunState(
            run_id="00000000",
            issue="x",
            phase="build",
            parallel=False,
            pending_breakpoint=bad,
        )


def test_pending_breakpoint_is_the_only_persisted_breakpoint_state_field():
    """C4_state / plan B2: the persisted state delta for breakpoints is EXACTLY the
    one field `pending_breakpoint` — no separate grant-proof field (e.g.
    `granted_breakpoints`) sneaks into the `extra="forbid"` schema. Crash-safe
    grant recovery must be derived from the event log, not from extra state."""
    fields = RunState.model_fields
    assert "pending_breakpoint" in fields
    assert "granted_breakpoints" not in fields
    assert [name for name in fields if "breakpoint" in name.lower()] == ["pending_breakpoint"]
    # And the persisted snapshot carries no other breakpoint key either.
    dumped = json.loads(RunState.new(issue="x", parallel=False).model_dump_json())
    assert [key for key in dumped if "breakpoint" in key.lower()] == ["pending_breakpoint"]


def test_phase_literal_is_not_extended():
    """E3b/C4_state: the Phase set of values stays exactly the eleven it had."""
    values = typing.get_args(Phase)
    assert "before_integration" not in values
    assert "before_push" not in values
    assert len(values) == 11


# --- AC4 / AC6 / AC7: before_integration --------------------------------------


def test_before_integration_pauses_after_build_single_lane(target_repo):
    """AC4/AC6/AC7/AC12: single-lane holds after the build lane completes and
    before codex_review begins; `awaiting_approval` + exit 2 + pending_breakpoint;
    `adw approve` continues from that point without repeating the build."""
    setup_config(target_repo, "before_integration")

    first = cli_run(target_repo)
    assert first.exit_code == 2, first.output  # plan approval gate first
    sid = latest_id(target_repo)
    assert RunState.load(target_repo, sid).pending_breakpoint is None  # not a breakpoint yet

    held_run = approve(target_repo, sid)
    assert held_run.exit_code == 2, held_run.output
    held = RunState.load(target_repo, sid)
    assert held.phase == "awaiting_approval"
    assert held.pending_breakpoint == "before_integration"
    assert held.lanes["backend"].completed  # the build lane finished
    assert held.review_rounds == 0  # codex_review has NOT begun
    assert ("before_integration", "awaited") in approval_pairs(target_repo, sid)

    done = approve(target_repo, sid)
    assert done.exit_code == 0, done.output
    final = RunState.load(target_repo, sid)
    assert final.phase == "done"
    assert final.pending_breakpoint is None
    assert final.lanes["backend"].gate_iterations == 2  # AC7: the build was not redone
    assert ("before_integration", "granted") in approval_pairs(target_repo, sid)


def test_before_integration_pauses_before_integration_in_parallel(target_repo):
    """AC4: the parallel path holds after all build lanes are green and before any
    integration/merge work begins."""
    setup_config(target_repo, "before_integration", parallel=True)

    assert cli_run(target_repo, "--parallel").exit_code == 2  # plan gate
    sid = latest_id(target_repo)

    held_run = approve(target_repo, sid)
    assert held_run.exit_code == 2, held_run.output
    held = RunState.load(target_repo, sid)
    assert held.phase == "awaiting_approval"
    assert held.pending_breakpoint == "before_integration"
    assert set(held.lanes) >= {"backend", "frontend"}
    assert all(lane.completed for lane in held.lanes.values())
    assert held.integration_rounds == 0  # no integration/merge work yet

    assert approve(target_repo, sid).exit_code == 0
    assert RunState.load(target_repo, sid).phase == "done"


# --- AC5: before_push ---------------------------------------------------------


def test_before_push_pauses_after_final_review_single_lane(target_repo):
    """AC5: single-lane holds after the final review and before ANY CI-phase work
    — nothing pushed, no CI preparation, no polling."""
    setup_config(target_repo, "before_push")

    assert cli_run(target_repo).exit_code == 2  # plan gate
    sid = latest_id(target_repo)

    held_run = approve(target_repo, sid)
    assert held_run.exit_code == 2, held_run.output
    held = RunState.load(target_repo, sid)
    assert held.phase == "awaiting_approval"
    assert held.pending_breakpoint == "before_push"
    events = read_events(target_repo, sid)
    assert of_type(events, "ci.wait") == []  # no polling started
    assert of_type(events, "ci.poll") == []

    assert approve(target_repo, sid).exit_code == 0
    assert RunState.load(target_repo, sid).phase == "done"


def test_before_push_pauses_before_ci_work_in_parallel(target_repo):
    """AC5: the parallel path holds before the CI phase, so its start-of-phase
    integration/E2E preparation and polling have not run."""
    setup_config(target_repo, "before_push", parallel=True)

    assert cli_run(target_repo, "--parallel").exit_code == 2  # plan gate
    sid = latest_id(target_repo)

    held_run = approve(target_repo, sid)
    assert held_run.exit_code == 2, held_run.output
    held = RunState.load(target_repo, sid)
    assert held.phase == "awaiting_approval"
    assert held.pending_breakpoint == "before_push"
    events = read_events(target_repo, sid)
    assert of_type(events, "ci.wait") == []
    assert of_type(events, "ci.poll") == []

    assert approve(target_repo, sid).exit_code == 0
    assert RunState.load(target_repo, sid).phase == "done"


# --- AC11 / AC12: both breakpoints active -------------------------------------


def test_both_breakpoints_hold_once_each_in_turn(target_repo):
    """AC11/AC12: with both active the run holds once at each in order, then runs
    through; no breakpoint holds twice, each logged as awaited + granted."""
    setup_config(target_repo, "before_integration", "before_push")

    assert cli_run(target_repo).exit_code == 2  # plan gate
    sid = latest_id(target_repo)

    at_integration = approve(target_repo, sid)
    assert at_integration.exit_code == 2, at_integration.output
    assert RunState.load(target_repo, sid).pending_breakpoint == "before_integration"

    at_push = approve(target_repo, sid)
    assert at_push.exit_code == 2, at_push.output
    assert RunState.load(target_repo, sid).pending_breakpoint == "before_push"

    assert approve(target_repo, sid).exit_code == 0
    assert RunState.load(target_repo, sid).phase == "done"

    pairs = approval_pairs(target_repo, sid)
    for gate in ("before_integration", "before_push"):
        assert pairs.count((gate, "awaited")) == 1
        assert pairs.count((gate, "granted")) == 1


# --- AC8 / E6: idempotency of a granted breakpoint ----------------------------


def test_granted_breakpoint_does_not_rehold_after_crash_and_resume(target_repo, monkeypatch):
    """AC8/E6: once granted, a breakpoint never holds again — not even after a
    crash past it followed by `adw resume`, which runs through."""
    setup_config(target_repo, "before_integration")

    assert cli_run(target_repo).exit_code == 2  # plan gate
    sid = latest_id(target_repo)
    assert approve(target_repo, sid).exit_code == 2  # before_integration hold

    # Grant the breakpoint, but crash in final_review — past build and the gate.
    with crash_agent(monkeypatch, "final_reviewer"):
        crashed = approve(target_repo, sid)
    assert crashed.exit_code != 0
    mid = RunState.load(target_repo, sid)
    assert mid.pending_breakpoint is None  # the grant cleared it
    assert mid.phase == "final_review"  # advanced well past the breakpoint

    assert resume(target_repo, sid).exit_code == 0  # no second hold
    assert RunState.load(target_repo, sid).phase == "done"
    pairs = approval_pairs(target_repo, sid)
    assert pairs.count(("before_integration", "awaited")) == 1
    assert pairs.count(("before_integration", "granted")) == 1


# --- AC9 / E6 / AC12: resume at a not-yet-granted breakpoint -------------------


def test_resume_at_ungranted_breakpoint_stays_waiting(target_repo):
    """AC9/E6/B6: resume at an ungranted breakpoint runs no work behind it — it
    stays in the wait, never falling through into the plan-gate build transition."""
    setup_config(target_repo, "before_integration")

    assert cli_run(target_repo).exit_code == 2  # plan gate
    sid = latest_id(target_repo)
    assert approve(target_repo, sid).exit_code == 2  # before_integration hold

    again = resume(target_repo, sid)
    assert again.exit_code == 2, again.output
    still = RunState.load(target_repo, sid)
    assert still.phase == "awaiting_approval"
    assert still.pending_breakpoint == "before_integration"
    assert still.review_rounds == 0  # no work behind the gate


def test_repeated_resume_appends_no_duplicate_awaited(target_repo):
    """AC9/AC12: repeated `resume` at the ungranted breakpoint each exit with
    code 2 but the log keeps exactly ONE `awaited` for it."""
    setup_config(target_repo, "before_integration")

    assert cli_run(target_repo).exit_code == 2  # plan gate
    sid = latest_id(target_repo)
    assert approve(target_repo, sid).exit_code == 2  # before_integration hold

    for _ in range(3):
        assert resume(target_repo, sid).exit_code == 2
    assert approval_pairs(target_repo, sid).count(("before_integration", "awaited")) == 1


def test_missing_awaited_event_is_caught_up_on_resume(target_repo):
    """AC12 (crash window): the waiting state is persisted but the `awaited` event
    never reached the log; the next `resume` catches it up exactly once and the
    run keeps waiting."""
    setup_config(target_repo, "before_integration")

    assert cli_run(target_repo).exit_code == 2  # plan gate
    sid = latest_id(target_repo)
    assert approve(target_repo, sid).exit_code == 2  # before_integration hold

    _strip_events(target_repo, sid, gate="before_integration", event="awaited")
    assert approval_pairs(target_repo, sid).count(("before_integration", "awaited")) == 0

    again = resume(target_repo, sid)
    assert again.exit_code == 2, again.output
    assert approval_pairs(target_repo, sid).count(("before_integration", "awaited")) == 1
    assert RunState.load(target_repo, sid).pending_breakpoint == "before_integration"


def test_missing_granted_event_is_caught_up_on_resume(target_repo, monkeypatch):
    """AC12 (crash window, release side): the grant is persisted (the phase is
    advanced past the breakpoint) but the `granted` event never reached the log —
    a crash between the approval state save and the event append. The next
    `resume` catches it up exactly once and runs the release through, so the log
    keeps exactly ONE `granted` for the breakpoint."""
    setup_config(target_repo, "before_integration")

    assert cli_run(target_repo).exit_code == 2  # plan gate
    sid = latest_id(target_repo)
    assert approve(target_repo, sid).exit_code == 2  # before_integration hold

    # Grant the breakpoint, but crash in final_review — past the boundary.
    with crash_agent(monkeypatch, "final_reviewer"):
        crashed = approve(target_repo, sid)
    assert crashed.exit_code != 0
    mid = RunState.load(target_repo, sid)
    assert mid.pending_breakpoint is None  # the grant was persisted
    assert mid.phase == "final_review"

    # Simulate the crash landing BEFORE the granted event was appended.
    _strip_events(target_repo, sid, gate="before_integration", event="granted")
    assert approval_pairs(target_repo, sid).count(("before_integration", "granted")) == 0

    assert resume(target_repo, sid).exit_code == 0
    assert approval_pairs(target_repo, sid).count(("before_integration", "granted")) == 1
    assert RunState.load(target_repo, sid).phase == "done"


# --- AC10: approve on a run that is not waiting --------------------------------


def test_approve_on_a_run_not_waiting_is_a_clean_error(target_repo):
    """AC10/E6: `adw approve` on a run that is not awaiting approval is a clean
    error without changing the run."""
    setup_config(target_repo)
    assert cli_run(target_repo, "--no-approval").exit_code == 0
    sid = latest_id(target_repo)
    before = RunState.load(target_repo, sid).model_dump()

    result = approve(target_repo, sid)
    assert result.exit_code == 1
    assert RunState.load(target_repo, sid).model_dump() == before  # unchanged


# --- AC14 / C7_no_approval: --no-approval skips the breakpoints ----------------


def test_no_approval_skips_all_breakpoints(target_repo):
    """AC14: `--no-approval` holds at no configured breakpoint and fabricates no
    breakpoint event."""
    setup_config(target_repo, "before_integration", "before_push")
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    sid = latest_id(target_repo)
    assert RunState.load(target_repo, sid).phase == "done"
    pairs = approval_pairs(target_repo, sid)
    assert all(gate not in ("before_integration", "before_push") for gate, _ in pairs)


def test_gates_none_mode_also_skips_breakpoints(target_repo):
    """AC14: the same one switch via `--gates none` skips the breakpoints too."""
    setup_config(target_repo, "before_integration", "before_push")
    result = cli_run(target_repo, "--gates", "none")
    assert result.exit_code == 0, result.output
    assert RunState.find_latest(target_repo).phase == "done"


def test_no_approval_breakpoint_skip_survives_crash_and_resume(target_repo, monkeypatch):
    """AC14: the skip is pinned in the state and survives crash + `resume` — the
    resumed run holds at no breakpoint."""
    setup_config(target_repo, "before_integration")

    with crash_after_one(monkeypatch, "build_agent"):
        crashed = cli_run(target_repo, "--no-approval")
    assert crashed.exit_code != 0
    state = RunState.find_latest(target_repo)
    assert state.phase == "build"
    assert state.skip_approval is True

    assert resume(target_repo, state.run_id).exit_code == 0
    done = RunState.load(target_repo, state.run_id)
    assert done.phase == "done"
    assert done.pending_breakpoint is None
    pairs = approval_pairs(target_repo, state.run_id)
    assert all(gate != "before_integration" for gate, _ in pairs)


# --- AC13: read-only GUI renders a breakpoint hold like the existing gates -----


@pytest.mark.parametrize("gate", ["before_integration", "before_push"])
def test_gui_shows_breakpoint_hold_as_awaiting_with_recovery(tmp_path, gate):
    """AC13/E1/E3b: a run waiting at a breakpoint reports `awaiting_approval` in
    both read endpoints and offers the read-only `adw approve` recovery hint — no
    new write path, no new phase value."""
    from fastapi.testclient import TestClient

    from adw.gui.app import create_app
    from adw.gui.registry import _slug
    from tests.gui_app_helpers import rec, run_start_payload, write_run

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    run_id = "aaaa2222"
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Breakpoint hold")),
        rec(2, "approval", "point", "R", sec=1, payload={"gate": gate, "event": "awaited"}),
    ]
    write_run(repo, run_id, lines, phase="awaiting_approval")

    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug(os.path.normpath(str(repo.resolve())))

    detail = client.get(f"/api/runs/{slug}/{run_id}").json()
    assert detail["run"]["status"] == "awaiting_approval"
    recovery = detail["recovery"]
    assert recovery["kind"] == "approve"
    assert recovery["command"].startswith(f"adw approve {run_id} --repo")

    entry = next(e for e in client.get("/api/runs").json() if e["run_id"] == run_id)
    assert entry["status"] == "awaiting_approval"
