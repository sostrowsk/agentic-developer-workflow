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
from pathlib import Path

import pytest

from adw.state import RunState

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
