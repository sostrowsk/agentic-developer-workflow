"""RED tests for Aufgabe A — the ``codex.author`` span (GUI-SPEC §4.4).

Derived from .adw/spec.md (AC 1–5), .adw/contract.yaml (``codex_author_span``)
and docs/GUI-SPEC.md §4.4/§6. The span is owned by the call site in
``phases.py:_codex_draft`` (E4): it therefore appears in a dry run (mock
runner) and stays closed even when the Codex draft fails. These tests exercise
the not-yet-existing instrumentation, so they are RED until it is wired.

Everything asserted here is externally observable surface: the on-disk event
log and its typed payloads.
"""

import json
import re

from adw.codex import CodexError, CodexRunner
from adw.config import AdwConfig
from adw.events import EventEmitter
from adw.mock import MockAgentRunner, MockCodexRunner
from adw.phases import SPEC_SUMMARY, RunContext, _draft_stage
from adw.state import RunState

RUN_ID = "runauthor"


def read_events(repo, run_id: str = RUN_ID) -> list[dict]:
    path = repo.resolve() / ".adw" / "runs" / run_id / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def of_type(records, type_, kind=None):
    return [r for r in records if r["type"] == type_ and (kind is None or r["kind"] == kind)]


def make_ctx(target_repo, agents, codex):
    emitter = EventEmitter(target_repo, RUN_ID)
    ctx = RunContext(
        repo=target_repo,
        config=AdwConfig.load(target_repo),
        state=RunState.new(issue="Author-Span", parallel=False),
        agents=agents,
        codex=codex,
        emitter=emitter,
    )
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    return ctx, emitter


def _drafting_agents():
    agents = MockAgentRunner()
    agents.script_files("spec_agent", {".adw/spec.md": "# Draft Claude\n"})
    agents.script("spec_agent", "ok")
    return agents


def test_dry_run_codex_author_span_start_and_end_payload_fields(target_repo):
    """AC 1–3, AC 5: the mock runner produces exactly one closed codex.author
    start/end pair with the §4.4 start AND end payload fields."""
    agents = _drafting_agents()
    codex = MockCodexRunner()
    codex.script_artifacts("spec", {"spec.md": "# Codex-Spec-Entwurf\n"})
    ctx, emitter = make_ctx(target_repo, agents, codex)

    with emitter.span("phase", {"name": "spec", "from_phase": "spec"}):
        _draft_stage(
            ctx, kind="spec", agent_name="spec_agent", task="Meine Aufgabe",
            artifacts=("spec.md",), summary=SPEC_SUMMARY,
        )

    records = read_events(target_repo)
    (start,) = of_type(records, "codex.author", "start")
    (end,) = of_type(records, "codex.author", "end")
    assert start["span"] == end["span"]  # same span id (contract invariant)

    assert set(start["payload"]) == {"kind", "argv", "cwd", "task"}
    assert start["payload"]["kind"] == "spec"
    assert start["payload"]["cwd"] == str(ctx.repo)
    assert start["payload"]["task"] == "Meine Aufgabe"
    argv = start["payload"]["argv"]
    assert isinstance(argv, list) and argv  # full argv, before the span

    assert set(end["payload"]) == {"artifacts", "raw_stdout", "parse_ok"}
    assert end["payload"]["parse_ok"] is True
    assert end["payload"]["artifacts"] == ["spec.md"]
    assert isinstance(end["payload"]["raw_stdout"], str)


def _author_output_for_prompt(prompt: str) -> str:
    """Fill the empty marker blocks the author prompt carries, echoing the
    per-call marker_id so _extract_blocks accepts it — regardless of HOW the
    marker_id is shared between the argv builder and the execution (mechanic)."""
    blocks = []
    for name, marker_id in re.findall(r"===BEGIN (\S+) ([0-9a-f]+)===", prompt):
        blocks.append(f"===BEGIN {name} {marker_id}===\n# {name}\n===END {name} {marker_id}===")
    return "\n\n".join(blocks)


def test_codex_author_start_argv_identical_to_executed_argv(target_repo, monkeypatch):
    """AC 2: the argv in the start event is complete BEFORE the span and byte-for-
    byte identical to what the real runner executes (shared per-call marker_id)."""
    captured: dict = {}

    def fake_execute(argv, env):
        captured["argv"] = list(argv)
        return _author_output_for_prompt(argv[-1])

    monkeypatch.setattr(CodexRunner, "_execute", staticmethod(fake_execute))

    agents = _drafting_agents()
    ctx, emitter = make_ctx(target_repo, agents, CodexRunner())
    with emitter.span("phase", {"name": "spec", "from_phase": "spec"}):
        _draft_stage(
            ctx, kind="spec", agent_name="spec_agent", task="Meine Aufgabe",
            artifacts=("spec.md",), summary=SPEC_SUMMARY,
        )

    (start,) = of_type(read_events(target_repo), "codex.author", "start")
    assert captured["argv"], "the runner must have executed"
    assert start["payload"]["argv"] == captured["argv"]


def test_failed_codex_author_still_closes_span_with_full_end_record(target_repo):
    """AC 4: a scripted author failure still closes the span with a complete end
    record (parse_ok=false, raw_stdout present); the degradation behaviour is
    unchanged and the run does not abort."""
    agents = _drafting_agents()
    codex = MockCodexRunner()
    codex.script_author_error("spec", CodexError("codex kaputt"))
    ctx, emitter = make_ctx(target_repo, agents, codex)

    with emitter.span("phase", {"name": "spec", "from_phase": "spec"}):
        drafts = _draft_stage(
            ctx, kind="spec", agent_name="spec_agent", task="Meine Aufgabe",
            artifacts=("spec.md",), summary=SPEC_SUMMARY,
        )

    # Degradation unchanged: single-source synthesis, no Codex draft path handed on.
    assert drafts.codex == ()

    records = read_events(target_repo)
    (start,) = of_type(records, "codex.author", "start")
    (end,) = of_type(records, "codex.author", "end")
    assert start["span"] == end["span"]
    assert set(end["payload"]) == {"artifacts", "raw_stdout", "parse_ok"}
    assert end["payload"]["parse_ok"] is False
    assert isinstance(end["payload"]["raw_stdout"], str)

    # The degradation is logged (run keeps going), not escalated.
    assert of_type(records, "log"), "the Codex degradation must still emit a log point"


def test_dry_run_via_cli_emits_closed_codex_author_spans(target_repo):
    """AC 1/AC 5 acceptance path: a full dry run emits closed codex.author spans
    (both authoring phases) with the §4.4 payload fields."""
    from typer.testing import CliRunner

    from adw.cli import app

    result = CliRunner().invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Author", "--dry-run", "--no-approval"]
    )
    assert result.exit_code == 0, result.output
    state = RunState.find_latest(target_repo)
    records = read_events(target_repo, state.run_id)
    starts = of_type(records, "codex.author", "start")
    ends = of_type(records, "codex.author", "end")
    assert starts, "a dry run must emit codex.author spans"
    # every start is a closed pair with the right payload fields
    assert {r["span"] for r in starts} == {r["span"] for r in ends}
    for start in starts:
        assert set(start["payload"]) == {"kind", "argv", "cwd", "task"}
        assert start["payload"]["kind"] in {"spec", "plan"}
    for end in ends:
        assert set(end["payload"]) == {"artifacts", "raw_stdout", "parse_ok"}
