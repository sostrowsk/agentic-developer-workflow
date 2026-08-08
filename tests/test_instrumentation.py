"""RED tests for the orchestrator instrumentation (GUI-SPEC §11, steps 2–4).

Derived from .adw/spec.md, .adw/contract.yaml and docs/GUI-SPEC.md §4.3/§4.4/§6.
The emitter (adw/events.py) already exists and is only wired in and called — it
is never modified here.

Every assertion is over externally observable surface:
  * the on-disk event log (.adw/runs/<run_id>/events.jsonl) and its typed
    payloads (contract §2),
  * the additive-compatible signatures of the existing public functions
    (contract §4),
  * the run-span boundary / status mapping (contract §3, E1),
  * the dry-run as the acceptance path (contract §6).

These tests exercise the NEW optional ``emitter=`` (and, for the point-only
modules, ``span_id=``) parameters that do not exist yet — so they are RED until
the instrumentation is wired.
"""

import json
import logging

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from typer.testing import CliRunner

import adw.events as events_module
from adw.agents import REGISTRY, AgentRunError, SdkAgentRunner, agent_run_start_payload
from adw.ci import CiConfig, CiTimeoutError, poll_pipeline
from adw.cli import app
from adw.codex import CodexRunner
from adw.config import Gate
from adw.events import EventEmitter, NoOpEmitter
from adw.findings import Finding, ReviewResult
from adw.gates import GateReport, run_gates
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import EscalationError, run_ci_phase, run_final_review_phase
from adw.state import RunState
from adw.triage import triage_final_review
from tests.conftest import git, write_config

runner = CliRunner()

RUN_ID = "run01a2"


# --- shared helpers ---------------------------------------------------------


def read_events(repo, run_id: str = RUN_ID) -> list[dict]:
    """Every complete line of the run's events.jsonl (empty list if absent)."""
    path = repo.resolve() / ".adw" / "runs" / run_id / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def of_type(records: list[dict], type_: str, kind: str | None = None) -> list[dict]:
    return [
        r for r in records if r["type"] == type_ and (kind is None or r["kind"] == kind)
    ]


def cli_run(repo, *extra):
    return runner.invoke(
        app, ["run", "--repo", str(repo), "--issue", "Instrumentierung", "--dry-run", *extra]
    )


def read_run_events(repo) -> tuple[RunState, list[dict]]:
    state = RunState.find_latest(repo)
    return state, read_events(repo, state.run_id)


PARALLEL_CONFIG = """\
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

HOPELESS_GATE_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: immer-rot, cmd: "sh -c 'echo KAPUTT; exit 1'", timeout: 10}
"""


def make_parallel_repo(repo):
    write_config(repo, PARALLEL_CONFIG)
    git(repo, "add", ".adw/config.yaml")
    git(repo, "commit", "-m", "parallel config")
    return repo


# --- agents.py: SdkAgentRunner._collect / .run (deepest cut, B1) ------------

USAGE = {
    "input_tokens": 12,
    "output_tokens": 7,
    "cache_read_input_tokens": 3,
    "cache_creation_input_tokens": 1,
}


def make_stream_factory(*, is_error: bool = False, result: str = "Fertig"):
    """Fresh SDK stream on every call (a generator is single-use)."""

    def factory():
        async def stream():
            yield AssistantMessage(
                content=[
                    TextBlock("Denke nach"),
                    ToolUseBlock(id="tu1", name="Bash", input={"command": "ls"}),
                ],
                model="claude-x",
                usage=USAGE,
            )
            yield UserMessage(
                content=[ToolResultBlock(tool_use_id="tu1", content="file.txt", is_error=False)]
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=is_error,
                num_turns=1,
                session_id="sess-final",
                total_cost_usd=0.0123,
                usage=USAGE,
                model_usage={"claude-x": USAGE},
                result=result,
            )

        return stream()

    return factory


@pytest.fixture(autouse=True)
def fake_stored_login(monkeypatch, tmp_path_factory):
    """Hermetic stored-login credentials, independent of the machine."""
    config_dir = tmp_path_factory.mktemp("claude-config")
    (config_dir / ".credentials.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.fixture
def patched_query(monkeypatch):
    def install(factory):
        def fake_query(*, prompt, options):
            return factory()

        monkeypatch.setattr("adw.agents.query", fake_query)

    return install


def _run_with_call_site_span(runner, emitter, spec, task, cwd, resume=None):
    """The agent.run span is owned by the call site (GUI-SPEC §6, E4): open it
    here and hand the runner the handle, exactly as phases.py does."""
    with emitter.span("agent.run", agent_run_start_payload(spec, task, cwd, resume)) as handle:
        return runner.run(spec, task, cwd=cwd, resume=resume, emitter=emitter, span=handle)


def test_agent_run_span_start_payload_fields(target_repo, patched_query):
    patched_query(make_stream_factory())
    _run_with_call_site_span(
        SdkAgentRunner(),
        EventEmitter(target_repo, RUN_ID),
        REGISTRY["spec_agent"],
        "Meine Aufgabe",
        target_repo,
        resume="old-sess",
    )
    (start,) = of_type(read_events(target_repo), "agent.run", "start")
    assert set(start["payload"]) == {
        "agent", "model", "tools", "allowed_tools", "cwd",
        "resume_session", "prompt", "system_append",
    }
    assert start["payload"]["agent"] == "spec_agent"
    assert start["payload"]["model"] == REGISTRY["spec_agent"].model
    assert start["payload"]["prompt"] == "Meine Aufgabe"  # full task string, uncapped
    assert start["payload"]["resume_session"] == "old-sess"
    assert start["payload"]["cwd"] == str(target_repo)


def test_agent_run_span_end_payload_usage_and_cost(target_repo, patched_query):
    patched_query(make_stream_factory())
    _run_with_call_site_span(
        SdkAgentRunner(), EventEmitter(target_repo, RUN_ID), REGISTRY["spec_agent"], "task",
        target_repo,
    )
    (end,) = of_type(read_events(target_repo), "agent.run", "end")
    assert set(end["payload"]) == {"session_id", "result_text", "usage", "cost_usd", "is_error"}
    assert set(end["payload"]["usage"]) == {"input", "output", "cache_read", "cache_creation"}
    assert end["payload"]["session_id"] == "sess-final"
    assert end["payload"]["result_text"] == "Fertig"
    assert end["payload"]["cost_usd"] == 0.0123
    assert end["payload"]["is_error"] is False


def test_agent_stream_mirrors_message_and_tools_with_span_attribution(target_repo, patched_query):
    patched_query(make_stream_factory())
    _run_with_call_site_span(
        SdkAgentRunner(), EventEmitter(target_repo, RUN_ID), REGISTRY["spec_agent"], "task",
        target_repo,
    )
    records = read_events(target_repo)
    (start,) = of_type(records, "agent.run", "start")
    span_id = start["span"]

    (msg,) = of_type(records, "agent.message")
    assert set(msg["payload"]) == {"role", "text"}
    assert msg["payload"]["role"] == "assistant"
    assert msg["payload"]["text"] == "Denke nach"
    assert msg["span"] == span_id  # point carries its enclosing agent.run span

    (call,) = of_type(records, "agent.tool.call")
    assert set(call["payload"]) == {"tool", "tool_use_id", "input"}
    assert call["payload"] == {"tool": "Bash", "tool_use_id": "tu1", "input": {"command": "ls"}}
    assert call["span"] == span_id

    (res,) = of_type(records, "agent.tool.result")
    assert set(res["payload"]) == {"tool_use_id", "is_error", "content"}
    assert res["payload"]["tool_use_id"] == "tu1"
    assert res["payload"]["is_error"] is False
    assert res["payload"]["content"] == "file.txt"
    assert res["span"] == span_id


def test_collect_result_bit_identical_across_emitters(target_repo, patched_query):
    """AC 4: the same SDK stream yields the same AgentResult with the active
    emitter, the NoOpEmitter and no emitter at all — mirroring is read-only."""
    spec = REGISTRY["spec_agent"]

    patched_query(make_stream_factory())
    with_active = SdkAgentRunner().run(
        spec, "task", cwd=target_repo, emitter=EventEmitter(target_repo, RUN_ID)
    )
    patched_query(make_stream_factory())
    with_noop = SdkAgentRunner().run(spec, "task", cwd=target_repo, emitter=NoOpEmitter())
    patched_query(make_stream_factory())
    without = SdkAgentRunner().run(spec, "task", cwd=target_repo)

    assert with_active.text == with_noop.text == without.text == "Fertig"
    assert with_active.session_id == with_noop.session_id == without.session_id == "sess-final"


def test_agent_run_accumulates_cost_and_tokens_into_run_totals(target_repo, patched_query):
    """P3: SdkAgentRunner feeds each run's cost/tokens into the shared RunTotals
    accumulator that the run-end totals are computed from."""
    from adw.agents import RunTotals

    totals = RunTotals()
    patched_query(make_stream_factory())
    SdkAgentRunner(emitter=NoOpEmitter(), totals=totals).run(
        REGISTRY["spec_agent"], "task", cwd=target_repo
    )
    assert totals.cost_usd == 0.0123
    assert totals.tokens == sum(USAGE.values())  # input+output+cache_read+cache_creation
    # A second run adds up.
    patched_query(make_stream_factory())
    SdkAgentRunner(emitter=NoOpEmitter(), totals=totals).run(
        REGISTRY["spec_agent"], "task", cwd=target_repo
    )
    assert totals.cost_usd == pytest.approx(0.0246)
    assert totals.tokens == 2 * sum(USAGE.values())


def test_run_end_totals_reflect_the_accumulator(target_repo):
    """P3: the run-end totals report the aggregated cost/tokens, not hardcoded 0."""
    from adw.agents import RunTotals
    from adw.cli import _run_span
    from adw.config import AdwConfig

    totals = RunTotals()
    totals.add(0.5, 4321)
    emitter = EventEmitter(target_repo, RUN_ID)
    state = RunState.new(issue="T", parallel=False)
    with _run_span(emitter, state, AdwConfig.load(target_repo), target_repo, totals):
        pass
    (end,) = of_type(read_events(target_repo, RUN_ID), "run", "end")
    assert set(end["payload"]["totals"]) == {"duration", "cost", "tokens"}
    assert end["payload"]["totals"]["cost"] == 0.5
    assert end["payload"]["totals"]["tokens"] == 4321


def test_agent_run_error_semantics_preserved_and_end_marks_is_error(target_repo, patched_query):
    """AC 4/AC-exception: an error result still raises AgentRunError with the
    emitter active, and the agent.run end event is written with is_error=True."""
    patched_query(make_stream_factory(is_error=True, result="Kaputt"))
    with pytest.raises(AgentRunError):
        _run_with_call_site_span(
            SdkAgentRunner(), EventEmitter(target_repo, RUN_ID), REGISTRY["spec_agent"], "task",
            target_repo,
        )
    (end,) = of_type(read_events(target_repo), "agent.run", "end")
    assert end["payload"]["is_error"] is True


# --- gates.py: run_gates per gate (B2) --------------------------------------


def test_run_gates_emits_gate_span_on_pass(target_repo):
    gate = Gate(name="lint", cmd="true", timeout=10)
    run_gates([gate], cwd=target_repo, emitter=EventEmitter(target_repo, RUN_ID))
    records = read_events(target_repo)
    (start,) = of_type(records, "gate", "start")
    (end,) = of_type(records, "gate", "end")
    assert set(start["payload"]) == {"name", "cmd", "timeout", "cwd"}
    assert start["payload"]["name"] == "lint"
    assert start["payload"]["cmd"] == "true"
    assert start["payload"]["timeout"] == 10
    assert set(end["payload"]) == {"passed", "exit_code", "timed_out", "output"}
    assert end["payload"]["passed"] is True
    assert end["payload"]["exit_code"] == 0
    assert end["payload"]["timed_out"] is False
    assert start["span"] == end["span"]


def test_run_gates_gate_end_reports_failure(target_repo):
    gate = Gate(name="test", cmd="sh -c 'echo BOOM; exit 3'", timeout=10)
    report = run_gates([gate], cwd=target_repo, emitter=EventEmitter(target_repo, RUN_ID))
    assert report.passed is False  # behaviour unchanged
    (end,) = of_type(read_events(target_repo), "gate", "end")
    assert end["payload"]["passed"] is False
    assert end["payload"]["exit_code"] == 3
    assert end["payload"]["timed_out"] is False
    assert "BOOM" in end["payload"]["output"]  # full gate output, uncapped


def test_gate_span_end_uses_exception_defaults_and_propagates(target_repo, monkeypatch):
    """Contract payload_end_on_exception: an UNEXPECTED exception after the span
    opened leaves the deterministic end defaults in place, and the exception
    propagates unchanged (no emit swallows it, no try/except hardening)."""
    boom = RuntimeError("unerwartet")

    def exploding_popen(*args, **kwargs):
        raise boom

    monkeypatch.setattr("adw.gates.subprocess.Popen", exploding_popen)
    gate = Gate(name="lint", cmd="true", timeout=10)
    with pytest.raises(RuntimeError) as excinfo:
        run_gates([gate], cwd=target_repo, emitter=EventEmitter(target_repo, RUN_ID))
    assert excinfo.value is boom
    (end,) = of_type(read_events(target_repo), "gate", "end")
    assert end["payload"] == {
        "passed": False, "exit_code": None, "timed_out": False, "output": ""
    }


# --- codex.py: review subprocess (B2) ---------------------------------------


def test_codex_review_emits_span_with_findings_payload(target_repo, monkeypatch):
    monkeypatch.setattr(
        CodexRunner, "_execute", staticmethod(lambda argv, env: '{"verdict": "ok", "findings": []}')
    )
    # The codex.review span is owned by the call site (GUI-SPEC §6, E4): open it
    # here and hand the runner the handle, exactly as phases.py does.
    emitter = EventEmitter(target_repo, RUN_ID)
    runner = CodexRunner()
    argv = runner.effective_argv("code", ["a.py"], target_repo)
    start_payload = {"kind": "code", "argv": argv, "cwd": str(target_repo), "custom_prompt": None}
    with emitter.span("codex.review", start_payload) as handle:
        result = runner.review("code", ["a.py"], cwd=target_repo, emitter=emitter, span=handle)
    assert result.verdict == "ok"  # behaviour unchanged
    records = read_events(target_repo)
    (start,) = of_type(records, "codex.review", "start")
    (end,) = of_type(records, "codex.review", "end")
    assert set(start["payload"]) == {"kind", "argv", "cwd", "custom_prompt"}
    assert start["payload"]["kind"] == "code"
    assert set(end["payload"]) == {"findings", "raw_stdout", "parse_ok"}
    assert end["payload"]["parse_ok"] is True
    assert end["payload"]["findings"] == []
    assert '"verdict": "ok"' in end["payload"]["raw_stdout"]


# --- ci.py: poll loop (B3) --------------------------------------------------

_SHA = "a" * 40


def _make_glab(status: str):
    def fake(argv, cwd):
        if argv[0] == "api" and "/pipelines?" in argv[1]:
            return json.dumps([{"id": 1, "status": status, "sha": _SHA}])
        if argv[0] == "api":  # jobs
            return json.dumps([])
        return ""

    return fake


def test_poll_pipeline_emits_ci_wait_span_and_poll_points(target_repo):
    cfg = CiConfig(provider="gitlab", staging_job=None, timeout=60, poll_interval=1)
    result = poll_pipeline(
        target_repo,
        "adw/x/backend",
        cfg,
        run_glab=_make_glab("success"),
        sleep=lambda _s: None,
        sha=_SHA,
        emitter=EventEmitter(target_repo, RUN_ID),
    )
    assert result.passed is True  # behaviour unchanged
    records = read_events(target_repo)
    (start,) = of_type(records, "ci.wait", "start")
    (end,) = of_type(records, "ci.wait", "end")
    assert set(start["payload"]) == {"provider", "pipeline_ref"}
    assert set(end["payload"]) == {"status", "polls", "duration"}
    assert end["payload"]["status"] == "success"
    polls = of_type(records, "ci.poll")
    assert polls, "at least one executed poll must emit ci.poll"
    for poll in polls:
        assert set(poll["payload"]) == {"provider", "status", "job"}
        assert poll["span"] == start["span"]  # point carries its ci.wait span


def test_ci_wait_end_status_timeout_on_citimeout(target_repo):
    # timeout must be > 0 per CiConfig; a never-terminal pipeline + no-op sleep
    # exhausts the budget after one nap and raises CiTimeoutError.
    cfg = CiConfig(provider="gitlab", staging_job=None, timeout=1, poll_interval=1)
    with pytest.raises(CiTimeoutError):
        poll_pipeline(
            target_repo,
            "adw/x/backend",
            cfg,
            run_glab=_make_glab("running"),
            sleep=lambda _s: None,
            sha=_SHA,
            emitter=EventEmitter(target_repo, RUN_ID),
        )
    (end,) = of_type(read_events(target_repo), "ci.wait", "end")
    assert end["payload"]["status"] == "timeout"


# --- state.py: state.saved after successful persistence (B2, AC 9) ----------


def test_state_save_emits_state_saved_after_persistence(target_repo):
    state = RunState.new(issue="Persistenz", parallel=False)
    emitter = EventEmitter(target_repo, state.run_id)
    state.save(target_repo, emitter=emitter, span_id="S-run")
    assert (target_repo / ".adw" / "runs" / state.run_id / "state.json").is_file()
    (saved,) = of_type(read_events(target_repo, state.run_id), "state.saved")
    assert set(saved["payload"]) == {"seq", "phase"}
    assert saved["payload"]["seq"] == state.seq  # the persisted RunState.seq
    assert saved["payload"]["phase"] == "spec"
    assert saved["span"] == "S-run"  # enclosing span id passed by the caller


# --- triage.py: decision points (B3) ----------------------------------------


def _finding(issue: str, category: str, lane: str = "backend") -> Finding:
    return Finding(
        severity="P2", lane=lane, file="src.py", issue=issue,
        remediation_plan=["fix"], category=category,
    )


def test_triage_final_review_emits_decision_points(target_repo):
    review = ReviewResult(
        verdict="needs_fixes",
        findings=[_finding("Bug", "implementation"), _finding("Lücke", "scope_gap")],
    )
    decision = triage_final_review(
        review, active_lanes=["backend"],
        emitter=EventEmitter(target_repo, RUN_ID), span_id="S-final",
    )
    # behaviour unchanged: one fix task (backend) and one follow-up
    assert decision.fix_tasks and decision.followups
    points = of_type(read_events(target_repo), "triage.decision")
    assert len(points) == 2
    for point in points:
        assert set(point["payload"]) == {"finding_key", "severity", "action", "reason"}
        assert point["span"] == "S-final"


# --- cli.py: run-span boundary and status mapping (B5, E1, AC 7) ------------


def test_run_span_status_done_with_payload_fields(target_repo):
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    (start,) = of_type(records, "run", "start")
    (end,) = of_type(records, "run", "end")
    assert set(start["payload"]) == {
        "issue", "parallel", "dry_run", "repo", "base_branch", "adw_version", "lanes",
    }
    assert start["payload"]["dry_run"] is True
    assert isinstance(start["payload"]["lanes"], list)
    assert set(end["payload"]) == {"status", "totals"}
    assert end["payload"]["status"] == "done"
    assert set(end["payload"]["totals"]) == {"duration", "cost", "tokens"}


def test_run_span_status_awaiting_approval(target_repo):
    result = cli_run(target_repo)  # no --no-approval → plan-approval pause
    assert result.exit_code == 2, result.output
    _state, records = read_run_events(target_repo)
    assert len(of_type(records, "run", "start")) == 1
    (end,) = of_type(records, "run", "end")
    assert end["payload"]["status"] == "awaiting_approval"


def test_run_span_status_escalated_and_escalation_point(target_repo):
    write_config(target_repo, HOPELESS_GATE_CONFIG)
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "hopeless gate")
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 1
    state, records = read_run_events(target_repo)
    assert state.phase == "escalated"
    (end,) = of_type(records, "run", "end")
    assert end["payload"]["status"] == "escalated"
    escalations = of_type(records, "escalation")
    assert escalations, "an actually triggered escalation must emit an escalation point"
    assert set(escalations[0]["payload"]) == {"reason", "phase"}
    # The hopeless gate repeats the SAME output → the circuit breaker trips first.
    breakers = of_type(records, "circuit_breaker")
    assert breakers, "no-progress must emit a circuit_breaker point"
    assert set(breakers[0]["payload"]) == {"keys", "scope"}


def test_resume_base_branch_repin_saves_inside_the_run_span(target_repo):
    """E1/AC 7: a `resume --base-branch other` (no lanes yet) persists the
    re-pinned base branch INSIDE the run span — its state.saved lies within the
    resume command's run span, not before it."""
    paused = cli_run(target_repo)  # awaiting_approval, lanes still empty
    assert paused.exit_code == 2, paused.output
    state = RunState.find_latest(target_repo)
    resumed = runner.invoke(
        app, ["resume", state.run_id, "--repo", str(target_repo), "--base-branch", "develop"]
    )
    # Still awaiting approval → exits 2 again; the point is the persistence order.
    assert resumed.exit_code == 2, resumed.output
    records = read_events(target_repo, state.run_id)
    run_starts = of_type(records, "run", "start")
    run_ends = of_type(records, "run", "end")
    assert len(run_starts) == 2 and len(run_ends) == 2  # run + resume commands
    resume_start = run_starts[-1]["seq"]
    resume_end = run_ends[-1]["seq"]
    saved_in_resume = [
        r for r in of_type(records, "state.saved") if resume_start < r["seq"] < resume_end
    ]
    assert saved_in_resume, "the re-pin save must emit state.saved inside the resume run span"


def test_run_span_status_escalated_on_unexpected_exception(target_repo, monkeypatch):
    """AC 7: an UNEXPECTED exception (not Awaiting/Escalation/AgentRunError) still
    closes the run span with status 'escalated' BEFORE propagating, and the
    exception reaches the caller unchanged."""
    boom = RuntimeError("unerwartet in der Phase")

    def raising(_ctx):
        raise boom

    monkeypatch.setattr("adw.cli.run_spec_and_plan", raising)
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code != 0
    assert result.exception is boom  # propagates unchanged to the caller
    state = RunState.find_latest(target_repo)
    records = read_events(target_repo, state.run_id)
    assert len(of_type(records, "run", "start")) == 1
    (end,) = of_type(records, "run", "end")
    assert end["payload"]["status"] == "escalated"


def test_run_span_opens_before_first_state_saved(target_repo):
    """E1: the run span opens right after RunState.new and BEFORE the first
    state.save, so the first state.saved event lies inside the run span."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    assert records[0]["type"] == "run" and records[0]["kind"] == "start"
    saved = of_type(records, "state.saved")
    assert saved
    assert records[0]["seq"] < min(r["seq"] for r in saved)


# --- fail-open and single-emitter guarantee (AC 5, AC 6) --------------------


def test_broken_event_log_is_fail_open_and_warns_once(target_repo, monkeypatch, caplog):
    """AC 5/AC 6: with a real EventEmitter whose writes always fail (disk full),
    the run keeps its exact semantics (exit 0, phase done) and — because there
    is exactly one emitter per run — the log warns at most once for the run."""
    def broken_append(self, **_fields):
        raise OSError("disk full")

    monkeypatch.setattr(events_module.EventEmitter, "_append", broken_append)
    with caplog.at_level(logging.WARNING, logger="adw.events"):
        result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    assert RunState.find_latest(target_repo).phase == "done"
    warnings = [r for r in caplog.records
                if r.name == "adw.events" and r.levelno == logging.WARNING]
    assert len(warnings) == 1


# --- dry-run as the acceptance path (AC 1, AC 2, AC 10) ---------------------

SEVEN_PHASES = ["spec", "plan", "build", "integration", "codex_review", "final_review", "ci"]


def test_dry_run_span_tree_covers_seven_phases_in_order(target_repo):
    """AC 1: the parallel dry run writes a full events.jsonl from which the span
    tree of all seven phases is reconstructable — in phase order."""
    make_parallel_repo(target_repo)
    result = cli_run(target_repo, "--no-approval", "--parallel")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    phase_names = [r["payload"]["name"] for r in of_type(records, "phase", "start")]
    assert set(SEVEN_PHASES) <= set(phase_names), phase_names
    first_seen = {name: phase_names.index(name) for name in SEVEN_PHASES}
    assert [first_seen[name] for name in SEVEN_PHASES] == sorted(first_seen.values())
    # Every phase start also carries the §4.4 payload fields.
    for start in of_type(records, "phase", "start"):
        assert set(start["payload"]) == {"name", "from_phase"}


ALL_LOOP_KINDS = {"authoring", "gates", "integration", "codex_review", "final_review"}


def test_dry_run_has_round_spans_for_every_loop_kind(target_repo):
    """AC 1 / plan B4: EVERY loop kind emits round spans with the §4.4 payload —
    authoring, gates, integration, codex_review, final_review — reconstructable
    from a parallel dry run."""
    make_parallel_repo(target_repo)
    result = cli_run(target_repo, "--no-approval", "--parallel")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    rounds = of_type(records, "round", "start")
    assert rounds, "loops must emit round spans"
    for start in rounds:
        assert set(start["payload"]) == {"loop", "n", "cap"}
        assert start["payload"]["loop"] in ALL_LOOP_KINDS
    seen_loops = {r["payload"]["loop"] for r in rounds}
    assert ALL_LOOP_KINDS <= seen_loops, ALL_LOOP_KINDS - seen_loops
    # Every round span is closed too (start/end paired per span id).
    starts = {r["span"] for r in rounds}
    ends = {r["span"] for r in of_type(records, "round", "end")}
    assert starts <= ends


def test_round_spans_contain_their_child_spans_and_points(target_repo):
    """P2: a round span encompasses the full iteration — child spans nest under
    it and the round-driven points carry it as their enclosing span."""
    make_parallel_repo(target_repo)
    result = cli_run(target_repo, "--no-approval", "--parallel")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    round_loop_by_id = {r["span"]: r["payload"]["loop"] for r in of_type(records, "round", "start")}

    # Every gate span opened inside a loop nests under a round (gates for the
    # lane gate loop, integration for the E2E gate).
    gate_starts = of_type(records, "gate", "start")
    assert gate_starts
    for gate in gate_starts:
        assert gate["parent"] in round_loop_by_id, ("gate not nested under a round", gate)

    # The E2E triage decisions are dispatched inside the integration round.
    triages = of_type(records, "triage.decision")
    assert triages
    assert any(round_loop_by_id.get(t["span"]) == "integration" for t in triages), (
        "triage.decision must be attributed to the integration round it ran in"
    )

    # Every round span is closed with a concrete (non-aborted on success) outcome.
    for end in of_type(records, "round", "end"):
        assert set(end["payload"]) == {"outcome"}


# Every §4.4 type, split by the path that provably emits it. Since step 5 the
# agent.run/codex.review spans live at the call sites and the build lane takes
# snapshots, so all three are reachable in a dry run; only the agent STREAM
# (agent.message/agent.tool.*) stays real-runner-only.
DRY_RUN_TYPES = {
    "run", "phase", "lane", "round", "gate", "ci.wait", "ci.poll",
    "state.saved", "triage.decision", "artifact", "commit", "merge",
    "agent.run", "codex.review", "snapshot",
}
UNIT_OR_FOCUSED_TYPES = {
    "agent.message", "agent.tool.call", "agent.tool.result",  # agent stream (real runner only)
    "red.check",             # tdd dry-run test
    "approval",              # approval test
    "escalation", "circuit_breaker",  # hopeless-gate test
    "limit.hit",             # fix-cycle-limit test
    "followup",              # _write_followups test
    "log",                   # _log_warning test
    "ci.reentry",            # ci-reentry test
}
CONTRACT_TYPES = DRY_RUN_TYPES | UNIT_OR_FOCUSED_TYPES


def test_contract_type_partition_covers_every_event_type():
    """Guard against forgetting a type: the union of the dry-run-reachable and
    the unit/focused-covered types is exactly the §4.4 set (26 types incl. snapshot)."""
    assert len(CONTRACT_TYPES) == 26  # §4.4 has 26 event types
    assert "snapshot" in DRY_RUN_TYPES  # step 5: snapshots are emitted in the build lane


def test_dry_run_emits_all_structural_types_including_snapshot(target_repo):
    """AC 2 + step 5: a parallel dry run emits every structurally reachable type,
    now including the relocated agent.run/codex.review spans and build snapshots."""
    make_parallel_repo(target_repo)
    result = cli_run(target_repo, "--no-approval", "--parallel")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    seen = {r["type"] for r in records}
    assert DRY_RUN_TYPES <= seen, DRY_RUN_TYPES - seen


def test_dry_run_span_and_point_payloads_match_contract(target_repo):
    """AC 3: lane/commit/merge/artifact carry exactly their §4.4 payload fields
    (not just present in the log)."""
    make_parallel_repo(target_repo)
    result = cli_run(target_repo, "--no-approval", "--parallel")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)

    lane_starts = of_type(records, "lane", "start")
    lane_ends = of_type(records, "lane", "end")
    assert lane_starts and lane_ends
    for start in lane_starts:
        assert set(start["payload"]) == {"name", "branch", "worktree", "base_sha", "ports"}
    for end in lane_ends:
        assert set(end["payload"]) == {"completed", "gate_iterations", "fix_cycles"}

    for commit in of_type(records, "commit"):
        assert set(commit["payload"]) == {"lane", "sha", "subject"}
    merges = of_type(records, "merge")
    assert merges
    for merge in merges:
        assert set(merge["payload"]) == {"lane", "target", "conflicts"}
    artifacts = of_type(records, "artifact")
    assert artifacts
    for artifact in artifacts:
        assert set(artifact["payload"]) == {"name", "path", "bytes", "sha256"}


def test_lane_end_reports_real_counters_on_escalation(target_repo):
    """P2/contract payload_end_on_exception: a lane that escalates reports the
    ACTUAL gate_iterations counter in its lane-end event, not a stale 0."""
    write_config(target_repo, HOPELESS_GATE_CONFIG)
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "hopeless gate")
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 1
    _state, records = read_run_events(target_repo)
    lane_ends = of_type(records, "lane", "end")
    assert lane_ends, "the escalated lane must still close its lane span"
    assert lane_ends[0]["payload"]["gate_iterations"] > 0, lane_ends[0]["payload"]
    assert lane_ends[0]["payload"]["completed"] is False


def test_dry_run_points_all_carry_a_non_null_span(target_repo):
    """Point-span attribution: every point event carries the id of its innermost
    enclosing span (never null) — GUI-SPEC §4.2."""
    make_parallel_repo(target_repo)
    result = cli_run(target_repo, "--no-approval", "--parallel")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    points = [r for r in records if r["kind"] == "point"]
    assert points
    offenders = [(r["type"], r["seq"]) for r in points if r["span"] is None]
    assert not offenders, offenders


def test_dry_run_only_new_state_is_events_jsonl(target_repo):
    """AC 10: the run creates no new persistent state besides events.jsonl —
    no sidecar/index files next to it."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    run_dir = target_repo.resolve() / ".adw" / "runs" / state.run_id
    assert (run_dir / "events.jsonl").is_file()
    assert sorted(p.name for p in run_dir.glob("events*")) == ["events.jsonl"]


def test_approval_points_emitted_on_pause_and_grant(target_repo):
    """AC 9: approval is recorded only for an actually entered wait (awaited)
    and an actually granted approval (granted) — both across run and approve."""
    paused = cli_run(target_repo)  # plan-approval pause
    assert paused.exit_code == 2, paused.output
    state = RunState.find_latest(target_repo)
    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(target_repo)])
    assert approved.exit_code == 0, approved.output
    records = read_events(target_repo, state.run_id)
    approvals = of_type(records, "approval")
    events = {(a["payload"]["gate"], a["payload"]["event"]) for a in approvals}
    assert ("plan", "awaited") in events
    assert ("plan", "granted") in events
    for approval in approvals:
        assert set(approval["payload"]) == {"gate", "event"}


TDD_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pytest, cmd: "test -f .adw-dry-run-fixed", timeout: 10, tdd: true}
ci:
  provider: gitlab
  staging_job: deploy-staging
"""


def test_dry_run_tdd_gate_emits_red_check(target_repo):
    """red.check: a tdd-marked gate makes the dry run walk the RED path and emit
    a red.check point with the real result."""
    write_config(target_repo, TDD_CONFIG)
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "tdd gate")
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    checks = of_type(records, "red.check")
    assert checks, "the tdd RED path must emit red.check"
    payload = checks[0]["payload"]
    assert set(payload) == {"confirmed", "test_paths", "gates"}
    assert payload["confirmed"] is True
    assert payload["gates"] == ["pytest"]


def test_write_followups_emits_followup_point(target_repo):
    """followup: recorded only after the follow-up was actually written (AC 9)."""
    from adw.config import AdwConfig
    from adw.phases import RunContext, _write_followups

    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=RunState.new(issue="F", parallel=False),
        agents=object(),
        codex=object(),
        emitter=EventEmitter(target_repo, RUN_ID),
    )
    ctx.state.save(target_repo)  # so run_dir exists
    _write_followups(ctx, [_finding("Export fehlt", "scope_gap")])
    (point,) = of_type(read_events(target_repo, RUN_ID), "followup")
    assert set(point["payload"]) == {"finding_key", "text"}
    assert point["span"] is None or isinstance(point["span"], str)
    assert (ctx.run_dir / "followups.md").is_file()


def test_log_warning_mirrors_to_a_log_point(target_repo):
    """log: an orchestrator warning is mirrored as a log point (logging behaviour
    unchanged)."""
    from adw.config import AdwConfig
    from adw.phases import RunContext, _log_warning

    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=RunState.new(issue="L", parallel=False),
        agents=object(),
        codex=object(),
        emitter=EventEmitter(target_repo, RUN_ID),
    )
    _log_warning(ctx, "etwas ist schiefgelaufen")
    (point,) = of_type(read_events(target_repo, RUN_ID), "log")
    assert set(point["payload"]) == {"level", "message"}
    assert point["payload"]["level"] == "warning"
    assert point["payload"]["message"] == "etwas ist schiefgelaufen"


def test_escalate_with_span_id_keeps_state_saved_and_escalation_non_null(target_repo):
    """P3: escalate() from a thread with no open spans (a draft-pool worker)
    must attribute BOTH its state.saved and escalation events to the explicitly
    passed span, never null."""
    from adw.config import AdwConfig
    from adw.phases import RunContext, escalate

    emitter = EventEmitter(target_repo, RUN_ID)
    state = RunState.new(issue="E", parallel=False)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=object(),
        codex=object(),
        emitter=emitter,
    )
    ctx.state.save(target_repo)  # ensure run dir exists

    captured: dict = {}

    def worker():
        # No `with emitter.span(...)` here → the thread-local span stack is empty.
        captured["err"] = escalate(ctx, "worker boom", span_id="PHASE-SPAN")

    import threading

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert isinstance(captured["err"], EscalationError)

    records = read_events(target_repo, RUN_ID)
    (escalation,) = of_type(records, "escalation")
    assert escalation["span"] == "PHASE-SPAN"
    saved = of_type(records, "state.saved")
    assert saved
    assert saved[-1]["span"] == "PHASE-SPAN"  # the escalate() save, non-null


def test_codex_draft_degradation_log_has_non_null_span(target_repo):
    """P2: the Codex draft runs in a pool worker; its degradation `log` point
    must still carry the enclosing phase span, never null."""
    from adw.codex import CodexError
    from adw.config import AdwConfig
    from adw.phases import SPEC_SUMMARY, RunContext, _draft_stage

    agents = MockAgentRunner()
    agents.script_files("spec_agent", {".adw/spec.md": "# Draft Claude\n"})
    agents.script("spec_agent", "ok")
    codex = MockCodexRunner()
    codex.script_author_error("spec", CodexError("codex kaputt"))
    emitter = EventEmitter(target_repo, RUN_ID)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=RunState.new(issue="D", parallel=False),
        agents=agents,
        codex=codex,
        emitter=emitter,
    )
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    with emitter.span("phase", {"name": "spec", "from_phase": "spec"}) as phase:
        _draft_stage(
            ctx, kind="spec", agent_name="spec_agent", task="t",
            artifacts=("spec.md",), summary=SPEC_SUMMARY,
        )
    logs = of_type(read_events(target_repo, RUN_ID), "log")
    assert logs, "the Codex degradation must emit a log point"
    assert logs[0]["span"] == phase.id
    assert logs[0]["span"] is not None


def test_fix_cycle_limit_emits_limit_hit(target_repo):
    """limit.hit: three fix cycles exhaust the per-lane limit and emit a
    limit.hit point before escalating."""
    from tests.test_e2e_dry_run import finding, prepare_final_review, review_json

    ctx = prepare_final_review(target_repo)
    ctx.emitter = EventEmitter(ctx.repo, ctx.state.run_id)

    def writes(cwd):
        return {f"fix{len(ctx.agents.calls)}.py": "pass\n"}

    ctx.agents.file_writes["build_agent"] = writes
    ctx.agents.script(
        "final_reviewer",
        *[review_json([finding(f"Problem {i}", "implementation")]) for i in range(4)],
    )
    with pytest.raises(EscalationError):
        run_final_review_phase(ctx)
    hits = of_type(read_events(ctx.repo, ctx.state.run_id), "limit.hit")
    assert hits, "the fix-cycle limit must emit limit.hit"
    assert set(hits[0]["payload"]) == {"limit", "value", "cap"}
    assert hits[0]["payload"]["limit"] == "fix_cycles"


def test_ci_reentry_emits_reentry_point(target_repo):
    """ci.reentry: a red pipeline routed back through the lanes emits a
    ci.reentry point on the actual re-entry."""
    import json as _json

    from tests.test_e2e_dry_run import prepare_final_review, review_json

    ctx = prepare_final_review(target_repo)
    ctx.agents.script("final_reviewer", review_json([]))  # ok → advance to "ci"
    run_final_review_phase(ctx)  # reach phase "ci" (green path, NoOp emitter)
    assert ctx.state.phase == "ci"
    ctx.emitter = EventEmitter(ctx.repo, ctx.state.run_id)
    ctx.dry_run = True  # skip the real push

    sha_seen: dict = {"red_done": False}

    def glab(argv, cwd):
        if argv[0] == "api" and "/pipelines?" in argv[1]:
            import re as _re

            m = _re.search(r"sha=([0-9a-f]+)", argv[1])
            sha = m.group(1) if m else ""
            status = "failed" if not sha_seen["red_done"] else "success"
            return _json.dumps([{"id": 1, "status": status, "sha": sha}])
        if argv[0] == "api":  # jobs
            if not sha_seen["red_done"]:
                return _json.dumps([{"id": 2, "name": "build", "status": "failed"}])
            return _json.dumps([{"id": 2, "name": "deploy-staging", "status": "success"}])
        if argv[:2] == ["ci", "trace"]:
            sha_seen["red_done"] = True  # after logs are fetched, next poll is green
            return "boom: test failed"
        return ""

    ctx.run_glab = glab
    # The log analyst assigns the CI failure to the backend lane; the fix run
    # then makes the lane green again.
    ctx.agents.script(
        "log_analyst",
        _json.dumps(
            {
                "verdict": "needs_fixes",
                "findings": [
                    {
                        "severity": "P2",
                        "lane": "backend",
                        "file": "src.py",
                        "issue": "CI-Fehler",
                        "remediation_plan": ["fix"],
                    }
                ],
            }
        ),
    )
    ctx.agents.script("build_agent", "fix")
    ctx.agents.file_writes["build_agent"] = {"src.py": "ok = True\n"}
    run_ci_phase(ctx)
    reentries = of_type(read_events(ctx.repo, ctx.state.run_id), "ci.reentry")
    assert reentries, "a CI re-entry must emit ci.reentry"
    assert set(reentries[0]["payload"]) == {"n", "reason"}


# --- plan phase span to_phase (P2) ------------------------------------------


def test_plan_phase_end_reflects_the_actual_resulting_phase(target_repo):
    """The plan phase end payload carries the phase the run actually transitions
    to — 'build' with --no-approval — not a stale 'plan'."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    plan_ends = [r for r in of_type(records, "phase", "end") if r["payload"]["name"] == "plan"]
    assert plan_ends
    assert plan_ends[0]["payload"]["to_phase"] == "build"


def test_plan_phase_end_to_phase_is_awaiting_approval_when_gated(target_repo):
    """With the approval gate active the plan phase resolves to awaiting_approval."""
    result = cli_run(target_repo)  # no --no-approval → pause
    assert result.exit_code == 2, result.output
    _state, records = read_run_events(target_repo)
    plan_ends = [r for r in of_type(records, "phase", "end") if r["payload"]["name"] == "plan"]
    assert plan_ends
    assert plan_ends[0]["payload"]["to_phase"] == "awaiting_approval"


def test_plan_phase_to_phase_stays_null_when_archival_fails(target_repo, monkeypatch):
    """P2: the plan span stays open through archival, so a failure there leaves
    to_phase null (the transition was never reached) and the error propagates."""
    from adw.config import AdwConfig
    from adw.phases import RunContext, run_spec_and_plan
    from tests.test_e2e_dry_run import OK, script_authoring, script_draft_artifacts

    agents = MockAgentRunner()
    script_authoring(agents)
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
    codex.script(OK, OK)  # spec + plan reviews both green
    state = RunState.new(issue="Boom", parallel=False)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=agents,
        codex=codex,
        emitter=EventEmitter(target_repo, state.run_id),
        skip_approval=True,
    )
    boom = RuntimeError("archival kaputt")

    def raising(_ctx, overwrite_existing_archive=True):
        raise boom

    monkeypatch.setattr("adw.phases._archive_artifacts", raising)
    with pytest.raises(RuntimeError) as excinfo:
        run_spec_and_plan(ctx)
    assert excinfo.value is boom  # propagates unchanged
    records = read_events(target_repo, state.run_id)
    plan_ends = [r for r in of_type(records, "phase", "end") if r["payload"]["name"] == "plan"]
    assert plan_ends
    assert plan_ends[0]["payload"]["to_phase"] is None


# --- additive-compatible signatures (AC 8) ----------------------------------


def test_existing_call_forms_without_emitter_are_unchanged(target_repo):
    """AC 8: every pre-existing call form (no emitter) stays valid, keeps its
    result and writes NO event log — the emitter defaults to NoOpEmitter."""
    # gates.py — positional and keyword forms
    assert run_gates([Gate(name="g", cmd="true", timeout=10)], cwd=target_repo).passed is True
    assert isinstance(
        run_gates([Gate(name="g", cmd="true", timeout=10)], cwd=target_repo, extra_env={}),
        GateReport,
    )
    # state.py
    state = RunState.new(issue="X", parallel=False)
    state.save(target_repo)
    assert (target_repo / ".adw" / "runs" / state.run_id / "state.json").is_file()
    # triage.py — with and without active_lanes
    review = ReviewResult(verdict="needs_fixes", findings=[_finding("B", "implementation")])
    assert triage_final_review(review).fix_tasks
    assert triage_final_review(review, active_lanes=["backend"]).fix_tasks
    # No event log was created by any of the emitter-less calls.
    assert read_events(target_repo, state.run_id) == []
    assert list((target_repo / ".adw").glob("runs/*/events.jsonl")) == []
