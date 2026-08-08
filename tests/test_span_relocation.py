"""RED tests for tasks A and B (GUI-SPEC §4.4/§6, spec ACs 1–4).

Task A — the missing coverage of the ``run`` span's exception path: the end
event with ``status: escalated`` is written BEFORE the unexpected exception
propagates unchanged. The implementation is correct and stays unchanged (E1);
this test only pins the behaviour ``_run_span`` already has.

Task B — the ``agent.run`` and ``codex.review`` **spans** move from the runners
to the call sites in ``adw/phases.py``. Only then does a dry run (the 0-token
acceptance path, mock runners) produce these spans at all. The runner-side
CONTENT (SDK stream mirroring, usage/cost) stays in the runners and is rightly
absent in a dry run. Every assertion here is over the on-disk event log — the
externally observable surface — never over the (unpinned) mechanism by which a
span handle reaches the runner.
"""

import pytest

from adw.agents import RunTotals
from adw.codex import CodexRunner
from adw.config import AdwConfig
from adw.events import EventEmitter
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import RunContext, run_build_phase, run_codex_review_phase, run_spec_and_plan
from adw.state import RunState
from tests.test_instrumentation import RUN_ID, cli_run, of_type, read_events, read_run_events

# --- Task A: run-span exception path (AC 1, E1) -----------------------------


def test_run_span_writes_escalated_end_before_propagating_unexpected_exception(target_repo):
    """AC 1: an unexpected exception raised inside the ``run`` span body closes
    the span with ``status: escalated`` (end event written BEFORE propagation)
    and the exact same exception instance reaches the caller unchanged.

    Exercised through ``_run_span`` directly — not the whole CLI run — so the
    test hits precisely the specified path (the gap the previous run escalated on)."""
    emitter = EventEmitter(target_repo, RUN_ID)
    state = RunState.new(issue="A", parallel=False)
    boom = RuntimeError("unerwartet im run-Span-Körper")

    from adw.cli import _run_span

    with pytest.raises(RuntimeError) as excinfo:
        with _run_span(emitter, state, AdwConfig.load(target_repo), target_repo, RunTotals()):
            raise boom
    assert excinfo.value is boom  # same instance, type and message — unchanged

    (end,) = of_type(read_events(target_repo, RUN_ID), "run", "end")
    assert end["payload"]["status"] == "escalated"  # written before the exception left the block


# --- Task B: helper to capture the dry-run mock runners ----------------------


def capture_dry_run_runners(monkeypatch) -> dict:
    """Grab the mock agent/codex runners the CLI wires for a dry run, so their
    recorded calls can be counted against the emitted spans."""
    import adw.cli as cli_mod

    captured: dict = {}
    real = cli_mod._dry_run_runners

    def capturing():
        agents, codex = real()
        captured["agents"] = agents
        captured["codex"] = codex
        return agents, codex

    monkeypatch.setattr(cli_mod, "_dry_run_runners", capturing)
    return captured


# --- Task B: dry run produces the spans, with §4.4 payloads (AC 2/3) ---------


def test_dry_run_emits_closed_agent_run_span_with_start_and_end_payloads(target_repo):
    """AC 2/3: a dry run writes at least one fully closed ``agent.run`` span
    (start + end, same span id) with the §4.4 start fields and — in the dry run —
    exactly the base end fields (no runner-only usage/cost)."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    starts = of_type(records, "agent.run", "start")
    ends = of_type(records, "agent.run", "end")
    assert starts and ends, "the dry run must emit at least one agent.run span"

    start = starts[0]
    assert set(start["payload"]) == {
        "agent", "model", "tools", "allowed_tools", "cwd",
        "resume_session", "prompt", "system_append",
    }
    end = next(e for e in ends if e["span"] == start["span"])  # the span is closed
    assert set(end["payload"]) == {"session_id", "result_text", "is_error"}


def test_dry_run_emits_closed_codex_review_span_with_start_and_end_payloads(target_repo):
    """AC 2/3: a dry run writes at least one fully closed ``codex.review`` span
    with the §4.4 start fields (incl. ``argv`` as a list) and the base end fields."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    starts = of_type(records, "codex.review", "start")
    ends = of_type(records, "codex.review", "end")
    assert starts and ends, "the dry run must emit at least one codex.review span"

    start = starts[0]
    assert set(start["payload"]) == {"kind", "argv", "cwd", "custom_prompt"}
    assert isinstance(start["payload"]["argv"], list)
    end = next(e for e in ends if e["span"] == start["span"])
    assert set(end["payload"]) == {"findings", "raw_stdout", "parse_ok"}


def test_dry_run_agent_run_span_omits_stream_and_usage_content(target_repo):
    """AC 3/4: the dry run invents no runner-side content — no agent.message /
    agent.tool.* points and no usage/cost fields in the agent.run end payload."""
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    assert of_type(records, "agent.run", "start"), "spans must exist at the call sites"
    assert of_type(records, "agent.message") == []
    assert of_type(records, "agent.tool.call") == []
    assert of_type(records, "agent.tool.result") == []
    for end in of_type(records, "agent.run", "end"):
        assert "usage" not in end["payload"]
        assert "cost_usd" not in end["payload"]


# --- Task B: exactly one span per call site, mock as well (AC 4) -------------


def test_dry_run_wraps_every_agent_run_call_site_in_exactly_one_span(target_repo, monkeypatch):
    """AC 4: every ctx.agents.run(...) invocation of a dry run is bracketed by
    exactly one agent.run span — the span lives at the call site, not the mock,
    so the count of starts equals the count of recorded mock calls."""
    captured = capture_dry_run_runners(monkeypatch)
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    calls = captured["agents"].calls
    starts = of_type(records, "agent.run", "start")
    ends = of_type(records, "agent.run", "end")
    assert calls, "the dry run must run agents"
    assert len(starts) == len(calls)  # exactly one span opened per invocation
    assert len(ends) == len(calls)  # and each is closed
    assert {s["span"] for s in starts} == {e["span"] for e in ends}


def test_dry_run_wraps_every_codex_review_call_and_gives_author_no_span(target_repo, monkeypatch):
    """AC 4: every ctx.codex.review(...) call is bracketed by exactly one
    codex.review span (mock like real); ctx.codex.author(...) calls happen but
    get NO span (§4.4 knows no author event type)."""
    captured = capture_dry_run_runners(monkeypatch)
    result = cli_run(target_repo, "--no-approval")
    assert result.exit_code == 0, result.output
    _state, records = read_run_events(target_repo)
    codex = captured["codex"]
    starts = of_type(records, "codex.review", "start")
    ends = of_type(records, "codex.review", "end")
    assert codex.calls, "the dry run must run codex reviews"
    assert codex.author_calls, "the dry run must also run codex authoring (drafts)"
    assert len(starts) == len(codex.calls)  # one span per review call, authors excluded
    assert len(ends) == len(codex.calls)
    assert {s["span"] for s in starts} == {e["span"] for e in ends}


# --- Task B: real runner — argv in the start event is the executed argv ------


def test_codex_review_start_argv_matches_the_executed_argv(target_repo, monkeypatch):
    """AC 3: for a real run the ``codex.review`` start event carries the full
    argv the runner actually executes — the argv is complete when the span opens
    (built by the shared builder), not backfilled. Driven through the real
    call site in ``run_codex_review_phase`` with a real CodexRunner."""
    from tests.test_e2e_dry_run import OK, script_authoring, script_draft_artifacts

    agents = MockAgentRunner()
    script_authoring(agents)
    agents.script_files("build_agent", {"src.py": "pass\n"})
    agents.script("build_agent", *["gebaut"] * 4)
    codex = MockCodexRunner()
    script_draft_artifacts(codex)
    codex.script(OK, OK)  # spec + plan authoring reviews
    state = RunState.new(issue="argv", parallel=False)
    emitter = EventEmitter(target_repo, state.run_id)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=state,
        agents=agents,
        codex=codex,
        skip_approval=True,
        emitter=emitter,
    )
    run_spec_and_plan(ctx)
    run_build_phase(ctx)
    assert ctx.state.phase == "codex_review"

    executed: dict = {}

    def fake_execute(argv, env):
        executed["argv"] = list(argv)
        return '{"verdict": "ok", "findings": []}'

    monkeypatch.setattr(CodexRunner, "_execute", staticmethod(fake_execute))
    ctx.codex = CodexRunner(emitter=emitter)
    run_codex_review_phase(ctx)

    records = read_events(target_repo, state.run_id)
    code_starts = [
        s for s in of_type(records, "codex.review", "start") if s["payload"]["kind"] == "code"
    ]
    assert code_starts, "the code review call site must open a codex.review span"
    assert "argv" in executed, "the real runner must have executed the subprocess"
    assert code_starts[-1]["payload"]["argv"] == executed["argv"]
