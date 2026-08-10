"""Shared builders and fixtures for the read-only GUI web-app tests (Aufgabe B).

NOT a test module (the name does not start with ``test_``), so pytest imports it
only for its helpers. The web app is addressed exclusively through its externally
observable surface: the FastAPI app built by ``adw.gui.app.create_app(repos=...)``
(the app-factory seam named in .adw/plan.md B3), the HTTP routes, their JSON/HTML
bodies and the SSE framing pinned in .adw/contract.yaml. Event records follow the
frozen schema of ``adw/events.py`` / GUI-SPEC §4.4; the fixtures only *consume*
the reader/model/registry, never change them.
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from adw.state import RunState

_TS_BASE = datetime(2026, 8, 5, 14, 0, 0)


def ts_at(seq: int) -> str:
    """A valid, strictly increasing ISO-UTC timestamp keyed to ``seq``. The span
    model sorts a node's children by timestamp, so large fixtures must use
    monotonic ts (not a cyclic ``sec % 60``) for chronological == seq order —
    otherwise windowing the first N children would pick scattered high-seq rows."""
    return (_TS_BASE + timedelta(seconds=seq)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# The seven workflow phases in their pinned order (contract PhaseStatus enum).
PHASE_ORDER = ["spec", "plan", "build", "integration", "codex_review", "final_review", "ci"]

# --- ASCII, URL-free content so substring assertions survive HTML/JSON escaping
PROMPT = "Fix the failing parser test and add a null guard"
SYSTEM_APPEND = "You are the ADW build agent. Follow the plan strictly."
INTERMEDIATE = "Let me first read the parser module."
TOOL_RESULT = "def parse(x): return x  # current parser body"
FINAL_ANSWER = "Added a null guard in parser.py and the test now passes"
GATE_CMD = "ruff check ."
GATE_OUTPUT = "parser.py:12:1 E501 line too long"
CODEX_STDOUT = "codex analysed 3 files and found one issue"
FINDING = {
    "severity": "P2",
    "lane": "backend",
    "file": "parser.py",
    "issue": "Missing null check before parse",
    "remediation_plan": ["Add a guard clause"],
    "category": "implementation",
}


def ts(sec: int) -> str:
    return f"2026-08-05T14:00:{sec:02d}.000Z"


def rec(seq, type, kind, span, parent=None, payload=None, *, sec=None, **extra):
    """A full event record with every schema field of GUI-SPEC §4.4."""
    r = {
        "seq": seq,
        "ts": ts(sec if sec is not None else (seq if isinstance(seq, int) else 0)),
        "type": type,
        "kind": kind,
        "span": span,
        "parent": parent,
        "phase": None,
        "lane": None,
        "round": None,
        "payload": payload if payload is not None else {},
    }
    r.update(extra)
    return r


def run_start_payload(issue="Fix the parser bug"):
    return {
        "issue": issue,
        "parallel": False,
        "dry_run": False,
        "repo": "/some/target/repo",
        "base_branch": "main",
        "adw_version": "0.4.0",
        "lanes": ["backend"],
    }


def run_end_payload(status="done", duration=12.0, cost=0.0123, tokens=30):
    return {"status": status, "totals": {"duration": duration, "cost": cost, "tokens": tokens}}


def write_run(repo: Path, run_id: str, lines, *, phase=None, issue="Issue text"):
    """Write ``events.jsonl`` (and optionally ``state.json``) for one run.

    ``lines`` items are either dict records (JSON-encoded) or raw strings (used
    verbatim for deliberately broken lines).
    """
    run_dir = repo / ".adw" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    body = "".join((json.dumps(e) if isinstance(e, dict) else e) + "\n" for e in lines)
    (run_dir / "events.jsonl").write_bytes(body.encode("utf-8"))
    if phase is not None:
        RunState(run_id=run_id, issue=issue, phase=phase, parallel=False).save(repo)
    return run_dir


def simple_run_lines(issue, *, ended=True, status="done", cost=0.0123, duration=12.0):
    """A minimal run: start + one build phase (+ end). event_count == 4 (ended)
    or 2 (running)."""
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "P", "R", sec=1, payload={"name": "build", "from_phase": "build"}),
    ]
    if ended:
        lines.append(
            rec(3, "phase", "end", "P", "R", sec=2, payload={"name": "build", "to_phase": status})
        )
        lines.append(
            rec(4, "run", "end", "R", None, sec=12, payload=run_end_payload(status, duration, cost))
        )
    return lines


def phases_lines():
    """Two completed phases (spec, plan) and one active phase (build, running);
    the run never ends. spec duration 2s, plan duration 3s."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Phase bar run")),
        rec(2, "phase", "start", "S", "R", sec=1, payload={"name": "spec", "from_phase": "spec"}),
        rec(3, "phase", "end", "S", "R", sec=3, payload={"name": "spec", "to_phase": "plan"}),
        rec(4, "phase", "start", "PL", "R", sec=4, payload={"name": "plan", "from_phase": "plan"}),
        rec(5, "phase", "end", "PL", "R", sec=7, payload={"name": "plan", "to_phase": "build"}),
        rec(6, "phase", "start", "B", "R", sec=8, payload={"name": "build", "from_phase": "build"}),
    ]


def comprehensive_lines(issue=PROMPT, *, cost=0.5, duration=17.0):
    """One finished run exercising every detail-pane node type: agent.run (with
    message/tool.call/tool.result points), gate, codex.review, an unknown type,
    and phase/lane/round aggregate spans."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "PB", "R", sec=2,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "lane", "start", "L", "PB", sec=3, payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
        rec(4, "round", "start", "RD", "L", sec=4, payload={"loop": "gates", "n": 2, "cap": 10}),
        rec(5, "agent.run", "start", "A", "RD", sec=5, payload={
            "agent": "build_agent", "model": "opus-4.8", "tools": [], "allowed_tools": [],
            "cwd": "wt", "resume_session": None, "prompt": PROMPT, "system_append": SYSTEM_APPEND}),
        rec(6, "agent.message", "point", "A", sec=6,
            payload={"role": "assistant", "text": INTERMEDIATE}),
        rec(7, "agent.tool.call", "point", "A", sec=7,
            payload={"tool": "Read", "tool_use_id": "t1", "input": {"file": "parser.py"}}),
        rec(8, "agent.tool.result", "point", "A", sec=8,
            payload={"tool_use_id": "t1", "is_error": False, "content": TOOL_RESULT}),
        rec(9, "agent.run", "end", "A", "RD", sec=9, payload={
            "session_id": "sess", "result_text": FINAL_ANSWER,
            "usage": {"input": 10, "output": 20, "cache_read": 0, "cache_creation": 0},
            "cost_usd": 0.4, "is_error": False}),
        rec(10, "gate", "start", "G", "RD", sec=10,
            payload={"name": "lint", "cmd": GATE_CMD, "timeout": 30, "cwd": "wt"}),
        rec(11, "gate", "end", "G", "RD", sec=11, payload={
            "passed": False, "exit_code": 1, "timed_out": False, "output": GATE_OUTPUT}),
        rec(12, "codex.review", "start", "C", "PB", sec=12,
            payload={"kind": "code", "argv": ["codex", "review"], "cwd": "wt",
                     "custom_prompt": None}),
        rec(13, "codex.review", "end", "C", "PB", sec=13, payload={
            "findings": [FINDING], "raw_stdout": CODEX_STDOUT, "parse_ok": True}),
        rec(14, "weird.unknown", "point", "PB", sec=14, payload={"foo": "bar"}),
        rec(15, "round", "end", "RD", "L", sec=15, payload={"outcome": "ok"}),
        rec(16, "lane", "end", "L", "PB", sec=16,
            payload={"completed": True, "gate_iterations": 2, "fix_cycles": 0}),
        rec(17, "phase", "end", "PB", "R", sec=17, payload={"name": "build", "to_phase": "done"}),
        rec(18, "run", "end", "R", None, sec=18,
            payload=run_end_payload("done", duration, cost, tokens=30)),
    ]


# --- Lauf-5 polish fixtures (Aufgaben A, B, C, G) -------------------------------


def multi_run_span_lines(*, last_ended=True, last_status="done", issue="Gated run"):
    """A single run file holding SEVERAL ``run`` spans, as a real gated run does
    (adw run + adw approve + adw approve). The two earlier spans end
    ``awaiting_approval``; the LAST span carries ``last_status`` (or has no ``end``
    at all when ``last_ended`` is False). Drives Aufgabe A: the reported status is
    the last span's, not the first's."""
    lines = [
        rec(1, "run", "start", "R1", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "run", "end", "R1", None, sec=10,
            payload=run_end_payload("awaiting_approval")),
        rec(3, "run", "start", "R2", None, sec=11, payload=run_start_payload(issue)),
        rec(4, "run", "end", "R2", None, sec=20,
            payload=run_end_payload("awaiting_approval")),
        rec(5, "run", "start", "R3", None, sec=21, payload=run_start_payload(issue)),
    ]
    if last_ended:
        lines.append(
            rec(6, "run", "end", "R3", None, sec=30, payload=run_end_payload(last_status))
        )
    return lines


def interleaved_run_span_lines(*, last_ended=True, last_status="done", issue="Interleaved"):
    """Two ``run`` spans whose completion order differs from their start order
    (start A, start B, end B, end A — as concurrent appenders can produce). The
    LAST-started span is B; its status must be reported, not that of the plainly
    last ``run`` end record (A's). When ``last_ended`` is False, B stays open (end
    A still present) so the run is ``running``."""
    lines = [
        rec(1, "run", "start", "A", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "run", "start", "B", None, sec=1, payload=run_start_payload(issue)),
    ]
    if last_ended:
        lines.append(rec(3, "run", "end", "B", None, sec=2, payload=run_end_payload(last_status)))
        lines.append(rec(4, "run", "end", "A", None, sec=3,
                         payload=run_end_payload("awaiting_approval")))
    else:
        # B stays open; A's end is the plainly-last run end but must NOT be used.
        lines.append(rec(3, "run", "end", "A", None, sec=3,
                         payload=run_end_payload("awaiting_approval")))
    return lines


def tool_label_lines():
    """One ``agent.run`` span whose tool-call/-result points exercise every branch
    of Aufgabe C: Read/Bash/Grep with their main argument, another tool (Write),
    an error result carrying an exit code, and the three no-invention fallbacks
    (missing tool name, present tool but missing argument, result without outcome
    fields). Points are addressed in the tests by their ``tool_use_id``."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Tool labels")),
        rec(2, "agent.run", "start", "A", "R", sec=2,
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        rec(3, "agent.tool.call", "point", "A", sec=3, payload={
            "tool": "Read", "tool_use_id": "read1", "input": {"file_path": "models.py"}}),
        rec(4, "agent.tool.result", "point", "A", sec=4, payload={
            "tool_use_id": "read1", "is_error": False, "content": "ok"}),
        rec(5, "agent.tool.call", "point", "A", sec=5, payload={
            "tool": "Bash", "tool_use_id": "bash1", "input": {"command": "pytest -x -q"}}),
        rec(6, "agent.tool.result", "point", "A", sec=6, payload={
            "tool_use_id": "bash1", "is_error": True, "exit_code": 1, "content": "boom"}),
        rec(7, "agent.tool.call", "point", "A", sec=7, payload={
            "tool": "Grep", "tool_use_id": "grep1", "input": {"pattern": "RUN_ID_RE"}}),
        rec(8, "agent.tool.result", "point", "A", sec=8, payload={
            "tool_use_id": "grep1", "is_error": False, "content": "3 matches"}),
        rec(9, "agent.tool.call", "point", "A", sec=9, payload={
            "tool": "Write", "tool_use_id": "write1",
            "input": {"file_path": "new.py", "content": "x = 1"}}),
        # no tool name → keep the type name
        rec(10, "agent.tool.call", "point", "A", sec=10, payload={
            "tool_use_id": "noname1", "input": {"file_path": "x.py"}}),
        # tool present but no identifiable argument → tool name alone
        rec(11, "agent.tool.call", "point", "A", sec=11, payload={
            "tool": "Read", "tool_use_id": "noarg1", "input": {}}),
        # result without any outcome field → keep the type name
        rec(12, "agent.tool.result", "point", "A", sec=12, payload={"tool_use_id": "bare1"}),
        # resolvable tool (a Read call) but a content-only result (content is the
        # payload body, NOT an outcome field) → keep the type name
        rec(13, "agent.tool.call", "point", "A", sec=13, payload={
            "tool": "Read", "tool_use_id": "content1", "input": {"file_path": "c.py"}}),
        rec(14, "agent.tool.result", "point", "A", sec=14, payload={
            "tool_use_id": "content1", "content": "file body only"}),
        # result whose tool_use_id matches no call → unresolved tool → type name
        rec(15, "agent.tool.result", "point", "A", sec=15, payload={
            "tool_use_id": "orphan1", "is_error": True}),
        # exit-code-only results (no is_error): zero → success, nonzero → error
        rec(16, "agent.tool.call", "point", "A", sec=16, payload={
            "tool": "Bash", "tool_use_id": "exit0", "input": {"command": "true"}}),
        rec(17, "agent.tool.result", "point", "A", sec=17, payload={
            "tool_use_id": "exit0", "exit_code": 0, "content": "done"}),
        rec(18, "agent.tool.call", "point", "A", sec=18, payload={
            "tool": "Bash", "tool_use_id": "exitN", "input": {"command": "false"}}),
        rec(19, "agent.tool.result", "point", "A", sec=19, payload={
            "tool_use_id": "exitN", "exit_code": 2, "content": "boom"}),
        rec(20, "agent.run", "end", "A", "R", sec=20,
            payload={"result_text": "done", "is_error": False}),
        rec(21, "run", "end", "R", None, sec=21, payload=run_end_payload("done")),
    ]


def long_duration_lines(issue="Long detail run"):
    """A run whose phase and agent.run span ~2828.7 s and whose agent.run costs
    ~$5.80, so the run DETAIL (tree rows, phase badge, aggregate pane) exercises
    the readable duration/cost formatting — not only the run list (Aufgabe E)."""
    a = "2026-08-05T14:00:00.000Z"
    b = "2026-08-05T14:47:08.700Z"  # exactly 2828.7 s after ``a``
    return [
        rec(1, "run", "start", "R", None, ts=a, payload=run_start_payload(issue)),
        rec(2, "phase", "start", "PB", "R", ts=a,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "agent.run", "start", "A", "PB", ts=a,
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        rec(4, "agent.run", "end", "A", "PB", ts=b, payload={
            "result_text": "done", "cost_usd": 5.795072500000001, "is_error": False,
            "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_creation": 0}}),
        rec(5, "phase", "end", "PB", "R", ts=b, payload={"name": "build", "to_phase": "done"}),
        rec(6, "run", "end", "R", None, ts=b,
            payload=run_end_payload("done", 2828.7, 5.795072500000001)),
    ]


# Sentinels bracketing the single >= 1 MB tool result of the big fixture, so a test
# can prove the FULL content is reachable (head + body + tail) via the events route.
BIG_RESULT_HEAD = "BIGRESULTHEADSENTINEL"
BIG_RESULT_TAIL = "BIGRESULTTAILSENTINEL"


def big_agent_run_lines(pairs=45):
    """A deterministic ``agent.run`` span with ``pairs`` (>= 40) tool-call/-result
    pairs carrying full inputs/outputs. Total result payload is >= 5 MB, with the
    first result >= 1 MB (bracketed by the sentinels) — the Aufgabe-B reference
    case (well below the 200 MB guard of GUI-SPEC §9). Generated in-memory so no
    multi-megabyte binary artefact is checked in."""
    lines = [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Big agent run")),
        rec(2, "agent.run", "start", "A", "R", sec=2,
            payload={"agent": "build_agent", "prompt": "Big run prompt", "system_append": ""}),
    ]
    seq = 3
    for i in range(pairs):
        lines.append(rec(seq, "agent.tool.call", "point", "A", sec=seq % 60, payload={
            "tool": "Bash", "tool_use_id": f"t{i}", "input": {"command": f"step {i}"}}))
        seq += 1
        if i == 0:
            content = BIG_RESULT_HEAD + ("x" * (1200 * 1024)) + BIG_RESULT_TAIL
        else:
            content = (f"result-{i:03d} " * ((120 * 1024) // 12))  # ~120 KB each
        lines.append(rec(seq, "agent.tool.result", "point", "A", sec=seq % 60, payload={
            "tool_use_id": f"t{i}", "is_error": False, "content": content}))
        seq += 1
    lines.append(rec(seq, "agent.run", "end", "A", "R", sec=seq % 60,
                     payload={"result_text": "big done", "is_error": False}))
    seq += 1
    lines.append(rec(seq, "run", "end", "R", None, sec=seq % 60,
                     payload=run_end_payload("done")))
    return lines


def write_state_only_run(repo: Path, run_id: str, *, phase="done", issue="Legacy run"):
    """Write a run that predates instrumentation: a ``state.json`` and NO
    ``events.jsonl`` (e.g. 8f8dc4ff, e680e005). Drives Aufgabe G."""
    RunState(run_id=run_id, issue=issue, phase=phase, parallel=False).save(repo)
    return repo / ".adw" / "runs" / run_id


# A payload that tries to break out of a <script> element (P1 XSS regression).
XSS_BREAKOUT = "</script><script>alert(document.cookie)</script>"


def xss_lines():
    """A finished run whose event payloads carry a script-breakout string, so the
    detail page's embedded JSON and rendered panes must neutralise it."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("XSS run")),
        rec(2, "agent.run", "start", "A", "R", sec=2, payload={
            "agent": "build_agent", "prompt": XSS_BREAKOUT, "system_append": ""}),
        rec(3, "agent.message", "point", "A", sec=3,
            payload={"role": "assistant", "text": XSS_BREAKOUT}),
        rec(4, "agent.run", "end", "A", "R", sec=4,
            payload={"result_text": XSS_BREAKOUT, "is_error": False}),
        rec(5, "run", "end", "R", None, sec=5, payload=run_end_payload("done")),
    ]


def escalated_lines():
    """A run whose ``build`` phase escalates (fails): an ``escalation`` point names
    the failed phase, the run ends ``escalated``."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Escalated run")),
        rec(2, "phase", "start", "B", "R", sec=2, payload={"name": "build", "from_phase": "build"}),
        rec(3, "escalation", "point", "B", sec=3,
            payload={"reason": "gate hopeless", "phase": "build"}),
        rec(4, "phase", "end", "B", "R", sec=4, payload={"name": "build", "to_phase": None}),
        rec(5, "run", "end", "R", None, sec=5, payload=run_end_payload("escalated")),
    ]


def problems_lines():
    """A run whose log has a broken line (bad_line) and a missing seq 2 (seq_gap),
    yet still ends so a stream over it closes."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Problem run")),
        "GARBAGE - this is not valid json",
        rec(3, "phase", "start", "P", "R", sec=3, payload={"name": "build", "from_phase": "build"}),
        rec(4, "phase", "end", "P", "R", sec=4, payload={"name": "build", "to_phase": "done"}),
        rec(5, "run", "end", "R", None, sec=5, payload=run_end_payload("done")),
    ]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Hermetic HOME so ``load_registry()`` starts empty and no test touches the
    real ~/.adw/repos.json (Aufgabe A guarantees this globally; app tests pin it
    locally too so they are self-contained)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def parse_sse(text: str):
    """Parse an SSE body into frames: [{event, id, data}], one per record block."""
    frames = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        frame = {"event": None, "id": None, "data": None}
        for line in block.split("\n"):
            if line.startswith("id:"):
                frame["id"] = line[len("id:"):].strip()
            elif line.startswith("event:"):
                frame["event"] = line[len("event:"):].strip()
            elif line.startswith("data:"):
                frame["data"] = line[len("data:"):].strip()
        frames.append(frame)
    return frames


def iter_nodes(nodes):
    for n in nodes or []:
        yield n
        yield from iter_nodes(n.get("children"))


def find_node(tree, node_type):
    return next((n for n in iter_nodes(tree) if n.get("type") == node_type), None)


def find_node_by_label(tree, label):
    return next((n for n in iter_nodes(tree) if n.get("label") == label), None)


# --- Lauf-6 fixtures: Tools responsiveness (A), Diff endpoint/tab (B), Raw tab (C)
#
# The polish run's reference case (big_agent_run_lines) measured MANY BYTES in FEW
# nodes; Aufgabe A corrects the criterion to the NODE COUNT (many entries, small
# contents). These builders keep the large fixtures in-memory (generated by the
# helpers, never checked in as bulky artefacts) as .adw/plan.md §1 requires.


def many_tool_entries_lines(pairs=800, *, issue="Many tool entries"):
    """One ``agent.run`` span with ``pairs`` tool-call/-result PAIRS — 2*``pairs``
    entries (>= 1500 by default), each with a SMALL content. This is the Aufgabe-A
    reference: a great many DOM nodes, not many bytes. Every call carries a unique
    ``toolstep-<i>`` command and every result a unique ``out-<i>`` body, so a test
    can tell which entries a windowed initial render materialised and prove the
    rest stay reachable through the events route (AC-A1/A2/A3)."""
    lines = [
        rec(1, "run", "start", "R", None, ts=ts_at(1), payload=run_start_payload(issue)),
        rec(2, "agent.run", "start", "A", "R", ts=ts_at(2),
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
    ]
    seq = 3
    for i in range(pairs):
        lines.append(rec(seq, "agent.tool.call", "point", "A", ts=ts_at(seq), payload={
            "tool": "Bash", "tool_use_id": f"t{i}", "input": {"command": f"toolstep-{i:05d}"}}))
        seq += 1
        lines.append(rec(seq, "agent.tool.result", "point", "A", ts=ts_at(seq), payload={
            "tool_use_id": f"t{i}", "is_error": False, "content": f"out-{i:05d}"}))
        seq += 1
    lines.append(rec(seq, "agent.run", "end", "A", "R", ts=ts_at(seq),
                     payload={"result_text": "done", "is_error": False}))
    seq += 1
    lines.append(rec(seq, "run", "end", "R", None, ts=ts_at(seq),
                     payload=run_end_payload("done")))
    return lines


def tool_entry_command(i: int) -> str:
    """The unique per-call token of :func:`many_tool_entries_lines` (call ``i``)."""
    return f"toolstep-{i:05d}"


# Marker for the one unknown-type event of :func:`large_event_log_lines` (early, so
# it is inside the first window) and per-event tokens to check windowing/reach.
RAW_UNKNOWN_MARK = "UNKNOWNMARK"


def raw_event_mark(seq: int) -> str:
    return f"rawmark-{seq:05d}"


def large_event_log_lines(count=3000, *, issue="Large raw log"):
    """A run log of at least ``count`` events for the Raw tab (Aufgabe C). It holds
    an UNKNOWN event type early (so it is visible in the first window, AC-C1), a
    deliberately broken line and a ``seq`` gap (both reader-reported, AC-C3), and a
    unique ``rawmark-<seq>`` payload field per event so a test can prove the initial
    render is windowed yet every event stays reachable (AC-C4). Contents are small.
    """
    lines = [
        rec(1, "run", "start", "R", None, ts=ts_at(1), payload=run_start_payload(issue)),
        rec(2, "weird.unknown", "point", "R", ts=ts_at(2),
            payload={"note": RAW_UNKNOWN_MARK, "rawmark": raw_event_mark(2)}),
        "GARBAGE - this is not valid json",  # -> bad_line problem (no seq consumed)
    ]
    # Skip seq 3 -> a seq_gap (expected 3, found 4) the reader reports.
    for seq in range(4, count + 1):
        lines.append(rec(seq, "note.item", "point", "R", ts=ts_at(seq),
                         payload={"rawmark": raw_event_mark(seq)}))
    lines.append(rec(count + 1, "run", "end", "R", None, ts=ts_at(count + 1),
                     payload=run_end_payload("done")))
    return lines


def bracketed_lane_lines(run_id="aaaa1111"):
    """A build lane whose ``snapshot`` events bracket TWO ``agent.run`` nodes, each
    resolving its OWN ``from``/``to`` pair (AC-B5/B8), plus a third ``agent.run``
    with no trailing snapshot (unbracketed, AC-B8 -> AC-B6). Two FRONTEND-lane
    decoy snapshots sit at seqs CLOSER to the first node's boundaries than the
    correct backend snapshots, so a lane-ignoring pairing would wrongly pick them —
    proving other-lane snapshots are never used. Refs are ``refs/adw/<run_id>/<n>``.

    Derived pairs (the observable the Diff tab requests):
      agent_one  -> (refs/.../1, refs/.../2)   (decoys refs/.../91,92 rejected)
      agent_two  -> (refs/.../3, refs/.../4)   (its own, distinct pair)
      agent_three-> unbracketed (no snapshot at/after its last event)
    """
    def ref(n):
        return f"refs/adw/{run_id}/{n}"

    def snap(seq, lane, n, label):
        return rec(seq, "snapshot", "point", "L", sec=seq, lane=lane,
                   payload={"lane": lane, "tree": f"tree{n}", "ref": ref(n), "label": label})

    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Bracketed")),
        rec(2, "phase", "start", "PB", "R", sec=2,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "lane", "start", "L", "PB", sec=3, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
        snap(4, "backend", 1, "before agent_one"),
        snap(5, "frontend", 91, "frontend decoy before"),   # closer to A1 start, wrong lane
        rec(6, "agent.run", "start", "A1", "L", sec=6, lane="backend",
            payload={"agent": "agent_one", "prompt": "p", "system_append": ""}),
        rec(7, "agent.tool.call", "point", "A1", sec=7, lane="backend",
            payload={"tool": "Bash", "tool_use_id": "c1", "input": {"command": "go"}}),
        rec(8, "agent.run", "end", "A1", "L", sec=8, lane="backend",
            payload={"result_text": "one done", "is_error": False}),
        snap(9, "frontend", 92, "frontend decoy after"),    # closer to A1 end, wrong lane
        snap(10, "backend", 2, "after agent_one"),
        snap(11, "backend", 3, "before agent_two"),
        rec(12, "agent.run", "start", "A2", "L", sec=12, lane="backend",
            payload={"agent": "agent_two", "prompt": "p", "system_append": ""}),
        rec(13, "agent.run", "end", "A2", "L", sec=13, lane="backend",
            payload={"result_text": "two done", "is_error": False}),
        snap(14, "backend", 4, "after agent_two"),
        rec(15, "agent.run", "start", "A3", "L", sec=15, lane="backend",
            payload={"agent": "agent_three", "prompt": "p", "system_append": ""}),
        rec(16, "agent.run", "end", "A3", "L", sec=16, lane="backend",
            payload={"result_text": "three done", "is_error": False}),
        rec(17, "lane", "end", "L", "PB", sec=17, lane="backend",
            payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}),
        rec(18, "phase", "end", "PB", "R", sec=18, payload={"name": "build", "to_phase": "done"}),
        rec(19, "run", "end", "R", None, sec=19, payload=run_end_payload("done")),
    ]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.strip()


def build_diff_run(repo: Path, run_id="aaaa1111", *, large=False):
    """Create a real git repo with two snapshot refs of ``run_id`` whose ``git
    diff`` is deterministic, and write the run's events.jsonl referencing EXACTLY
    those two refs (bracketing an ``agent.run`` so a Diff tab appears). Also create
    refs that are NOT in the event log — an unlisted ref of this run and a foreign
    run's ref — for the allowlist/security tests (AC-B2).

    ``large=False`` — a small deterministic diff: ``src/example.py`` (+3/-1) and a
    changed binary ``assets/logo.bin`` (null/null), for the schema/binary/order and
    empty-diff cases (AC-B1). ``large=True`` — >= 100 changed files and >= 1500
    changed lines plus a binary, for the responsiveness case (AC-B7).

    Returns a dict with the ref names, the repo, and the run_id.
    """
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "adw-test@example.com")
    _git(repo, "config", "user.name", "ADW Test")
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    binp = repo / "assets" / "logo.bin"
    binp.parent.mkdir(parents=True, exist_ok=True)

    def write_state(second: bool):
        if large:
            src = repo / "src"
            src.mkdir(parents=True, exist_ok=True)
            tag = "v2 changed" if second else "v1"
            for i in range(110):
                (src / f"mod_{i:03d}.py").write_text(
                    "".join(f"line {j} {tag}\n" for j in range(20)))
        else:
            p = repo / "src" / "example.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("a\nb\nX\nd\ne\nf\ng\n" if second else "a\nb\nc\nd\ne\n")
        binp.write_bytes(bytes(reversed(range(200))) if second else bytes(range(200)))

    write_state(second=False)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "state1")
    ref1 = f"refs/adw/{run_id}/1"
    _git(repo, "update-ref", ref1, "HEAD")

    write_state(second=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "state2")
    ref2 = f"refs/adw/{run_id}/2"
    _git(repo, "update-ref", ref2, "HEAD")

    # Refs that exist in git but are NOT referenced by any snapshot event of this
    # run — a pattern match alone must not admit them (AC-B2).
    unlisted = f"refs/adw/{run_id}/3"
    _git(repo, "update-ref", unlisted, "HEAD")
    foreign = "refs/adw/bbbb2222/1"
    _git(repo, "update-ref", foreign, "HEAD")
    head_sha = _git(repo, "rev-parse", "HEAD")

    lines = [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Diff run")),
        rec(2, "phase", "start", "PB", "R", sec=2,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "lane", "start", "L", "PB", sec=3, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
        rec(4, "snapshot", "point", "L", sec=4, lane="backend",
            payload={"lane": "backend", "tree": "t1", "ref": ref1, "label": "before"}),
        rec(5, "agent.run", "start", "A", "L", sec=5, lane="backend",
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        rec(6, "agent.run", "end", "A", "L", sec=6, lane="backend",
            payload={"result_text": "done", "is_error": False}),
        rec(7, "snapshot", "point", "L", sec=7, lane="backend",
            payload={"lane": "backend", "tree": "t2", "ref": ref2, "label": "after"}),
        rec(8, "lane", "end", "L", "PB", sec=8, lane="backend",
            payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}),
        rec(9, "phase", "end", "PB", "R", sec=9, payload={"name": "build", "to_phase": "done"}),
        rec(10, "run", "end", "R", None, sec=10, payload=run_end_payload("done")),
    ]
    write_run(repo, run_id, lines, phase="done")
    return {
        "repo": repo, "run_id": run_id, "ref1": ref1, "ref2": ref2,
        "unlisted": unlisted, "foreign": foreign, "head_sha": head_sha,
        "agent_label": "build_agent",
    }
