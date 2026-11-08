"""W14: the retro agent at the end of every finished run (done/escalated).

The retro never runs in a dry run, so the CLI tests run a *real* (non-dry) run
whose context is built like the dry run's: mock agent/Codex runners, mocked
forge, no sleep (``retro_seam``). Pushes go to a local bare ``origin``; the
ledger stays absent (autouse ``_isolate_ledger``). No test calls a real model."""

import json
import pathlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

import adw.cli as cli_mod
from adw import agents as agents_mod
from adw import dry_run as dry
from adw import retention
from adw.agents import AgentRunError
from adw.cli import app
from adw.context import RunContext
from adw.state import RunState
from tests.conftest import git, write_config

runner = CliRunner()

RED_GATE_CONFIG = """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: immer-rot, cmd: "sh -c 'echo GATE-ROT; exit 1'", timeout: 10}
ci:
  provider: gitlab
  staging_job: deploy-staging
"""

ITEM_KEYS = ("problem", "resolution", "better", "cause", "lever", "evidence")
RETRO_KEYS = {"schema_version", "run_id", "phase", "summary", "items", "dropped", "model"}


def item(n: int, evidence=None, cause: str = "prompt", **extra) -> dict:
    data = {
        "problem": f"Problem {n}",
        "resolution": f"Lösung {n}",
        "better": f"Besser {n}",
        "cause": cause,
        "lever": f"Hebel {n}",
        **extra,
    }
    if evidence is not None:
        data["evidence"] = evidence
    return data


def answer(*items: dict, summary: str = "Lauf ohne Drama") -> str:
    return json.dumps({"summary": summary, "items": list(items)}, ensure_ascii=False)


GOOD = answer(item(1, ["seq:1"], cause="gate"), item(2, ["state.json"], cause="model"))


class Seam:
    """Records every MockAgentRunner the patched context builder created."""

    def __init__(self):
        self.runners = []

    @property
    def calls(self):
        return [call for r in self.runners for call in r.calls]

    def retro_calls(self):
        return [c for c in self.calls if c.agent == "retro_agent"]


@pytest.fixture
def repo(target_repo, tmp_path):
    """Target repo with a local bare ``origin`` so a non-dry run can push."""
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "staging", str(bare))
    git(target_repo, "remote", "add", "origin", str(bare))
    git(target_repo, "push", "-q", "-u", "origin", "staging")
    return target_repo


def retro_seam(monkeypatch, retro=GOOD, fail_agent: str | None = None) -> Seam:
    """Patch ``cli._build_context``: a non-dry-run state gets the dry run's mock
    context. ``retro`` is the retro agent's answer — a string, a callable
    ``cwd -> str`` or an exception to raise. ``fail_agent`` raises
    ``AgentRunError`` for that agent (run stops resumable)."""
    seam = Seam()
    real_runners = cli_mod._dry_run_runners

    def build_context(repo, config, state, emitter=None, run_totals=None):
        agents, codex = real_runners()
        original_run = agents.run

        def run(agent, task, cwd, resume=None, deny_read_paths=None, **kwargs):
            if agent.name == fail_agent:
                raise AgentRunError("Claude-CLI: usage limit reached")
            if agent.name == "retro_agent":
                if isinstance(retro, Exception):
                    agents.calls.append(_call(agent, task, cwd, resume))
                    raise retro
                text = retro(Path(cwd)) if callable(retro) else retro
                agents.script("retro_agent", text)
            return original_run(agent, task, cwd, resume, deny_read_paths, **kwargs)

        agents.run = run
        seam.runners.append(agents)
        config = dry.config(config)
        kwargs = {"emitter": emitter} if emitter is not None else {}
        return RunContext(
            repo=repo,
            config=config,
            state=state,
            agents=agents,
            codex=codex,
            run_glab=dry.glab(config),
            run_gh=dry.gh(config),
            sleep=lambda seconds: None,
            **kwargs,
        )

    monkeypatch.setattr(cli_mod, "_build_context", build_context)
    return seam


def _call(agent, task, cwd, resume):
    from adw.mock import AgentCall

    return AgentCall(agent=agent.name, task=task, cwd=Path(cwd), resume=resume)


def start(repo: Path, *extra: str, gates: str = "none"):
    res = runner.invoke(
        app, ["run", "--repo", str(repo), "--issue", "Demo", "--gates", gates, *extra]
    )
    return res, RunState.find_latest(repo)


def events(run_dir: Path) -> list[dict]:
    return list(retention._iter_events(run_dir))


def retro_events(run_dir: Path) -> list[dict]:
    return [e for e in events(run_dir) if e.get("type") == "retro"]


def run_end_status(run_dir: Path) -> list[str]:
    return [
        e["payload"]["status"] for e in events(run_dir)
        if e.get("type") == "run" and e.get("kind") == "end"
    ]


def make_red(repo: Path) -> None:
    write_config(repo, RED_GATE_CONFIG)
    git(repo, "add", ".adw/config.yaml")
    git(repo, "commit", "-q", "-m", "rotes gate")


def assert_no_retro(seam: Seam, run_dir: Path) -> None:
    assert seam.retro_calls() == []
    assert not (run_dir / "retro.json").exists()
    assert not (run_dir / "retro.md").exists()
    assert retro_events(run_dir) == []


# --- AC 1: a run that ends done gets exactly one retro -----------------------------


def test_done_run_gets_one_retro_inside_the_run_span(repo, monkeypatch):
    seam = retro_seam(monkeypatch)

    res, state = start(repo)

    assert res.exit_code == 0, res.output
    assert state.phase == "done"
    run_dir = state.run_dir(repo)
    [call] = seam.retro_calls()
    assert call.cwd.resolve() == run_dir.resolve()

    text = (run_dir / "retro.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert text == json.dumps(data, sort_keys=True, indent=2) + "\n"
    assert set(data) == RETRO_KEYS
    assert data["schema_version"] == 1
    assert data["run_id"] == state.run_id
    assert data["phase"] == "done"
    assert data["summary"] == "Lauf ohne Drama"
    assert data["dropped"] == 0
    assert data["model"] == agents_mod.OPUS
    assert data["items"] == json.loads(GOOD)["items"]

    md = (run_dir / "retro.md").read_text(encoding="utf-8")
    for value in (state.run_id, "done", "Lauf ohne Drama"):
        assert value in md
    for entry in data["items"]:
        for key in ITEM_KEYS:
            values = entry[key] if isinstance(entry[key], list) else [entry[key]]
            for value in values:
                assert value in md

    log = events(run_dir)
    [run_start] = [e for e in log if e["type"] == "run" and e["kind"] == "start"]
    [run_end] = [e for e in log if e["type"] == "run" and e["kind"] == "end"]
    [span_start] = [
        e for e in log
        if e["type"] == "agent.run" and e["kind"] == "start"
        and e["payload"]["agent"] == "retro_agent"
    ]
    assert span_start["parent"] == run_start["span"]
    assert span_start["seq"] < run_end["seq"]
    payload = span_start["payload"]
    assert payload["model"] == agents_mod.OPUS
    assert payload["max_turns"] == 60
    assert payload["timeout_s"] == 900
    assert payload["tools"] == ["Read", "Grep", "Glob"]
    assert Path(payload["cwd"]).resolve() == run_dir.resolve()
    [event] = retro_events(run_dir)
    assert event["kind"] == "point"
    assert event["payload"] == {"outcome": "written", "items": 2}
    assert event["seq"] < run_end["seq"]
    assert run_end["payload"]["status"] == "done"


# --- AC 2: escalated → retro; gate pause / AgentRunError → none ---------------------


def test_escalated_run_gets_a_retro(repo, monkeypatch):
    make_red(repo)
    seam = retro_seam(monkeypatch)

    res, state = start(repo)

    assert res.exit_code == 1, res.output
    assert state.phase == "escalated"
    assert len(seam.retro_calls()) == 1
    data = json.loads((state.run_dir(repo) / "retro.json").read_text(encoding="utf-8"))
    assert data["phase"] == "escalated"
    assert (state.run_dir(repo) / "retro.md").is_file()
    assert run_end_status(state.run_dir(repo)) == ["escalated"]


@pytest.mark.parametrize(
    ("gates", "fail_agent", "exit_code", "phase"),
    [("plan", None, 2, "awaiting_approval"), ("none", "build_agent", 1, "build")],
    ids=["gate-pause", "agent-run-error"],
)
def test_unfinished_run_gets_no_retro(repo, monkeypatch, gates, fail_agent, exit_code, phase):
    seam = retro_seam(monkeypatch, fail_agent=fail_agent)

    res, state = start(repo, gates=gates)

    assert res.exit_code == exit_code, res.output
    assert state.phase == phase
    assert_no_retro(seam, state.run_dir(repo))


# --- AC 3: a failing retro changes neither exit code nor phase ---------------------


def _partial_write(monkeypatch, name: str):
    real = pathlib.Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self.name == name:
            real(self, data[: max(1, len(data) // 2)], *args, **kwargs)
            raise OSError(f"Platte voll beim Schreiben von {name}")
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", write_text)


def _unlink_fails(monkeypatch):
    real = pathlib.Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name in ("retro.json", "retro.md"):
            raise PermissionError(f"darf {self.name} nicht löschen")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)


FAILURES = {
    "agent-run-error": AgentRunError("Claude-CLI: usage limit reached"),
    "not-json": "Hier ist meine Einschätzung: alles gut.",
    "eleven-items": answer(*[item(n, ["seq:1"]) for n in range(11)]),
    "unknown-cause": answer(item(1, ["seq:1"], cause="wetter")),
    "extra-item-key": answer(item(1, ["seq:1"], severity="P1")),
    "extra-top-key": json.dumps({"summary": "s", "items": [], "mood": "gut"}),
    "md-write-fails": GOOD,
    "cleanup-fails": GOOD,
}


@pytest.mark.parametrize("failure", list(FAILURES))
def test_failing_retro_keeps_exit_code_phase_and_status(repo, monkeypatch, failure):
    seam = retro_seam(monkeypatch, retro=FAILURES[failure])
    if failure in ("md-write-fails", "cleanup-fails"):
        _partial_write(monkeypatch, "retro.md")
    if failure == "cleanup-fails":
        _unlink_fails(monkeypatch)

    res, state = start(repo)

    assert res.exit_code == 0, res.output
    assert state.phase == "done"
    run_dir = state.run_dir(repo)
    assert run_end_status(run_dir) == ["done"]
    assert len(seam.retro_calls()) == 1
    assert res.stderr.strip()
    [event] = retro_events(run_dir)
    assert event["kind"] == "point"
    assert set(event["payload"]) == {"outcome", "reason"}
    assert event["payload"]["outcome"] == "failed"
    reason = event["payload"]["reason"]
    assert isinstance(reason, str) and reason
    if failure == "cleanup-fails":
        assert "retro.json" in reason  # the failed cleanup is part of the reason
    else:
        assert not (run_dir / "retro.json").exists()
        assert not (run_dir / "retro.md").exists()


def test_escalated_run_with_failing_retro_stays_escalated(repo, monkeypatch):
    make_red(repo)
    retro_seam(monkeypatch, retro=AgentRunError("Claude-CLI: usage limit reached"))

    res, state = start(repo)

    assert res.exit_code == 1, res.output
    assert state.phase == "escalated"
    run_dir = state.run_dir(repo)
    assert run_end_status(run_dir) == ["escalated"]
    [event] = retro_events(run_dir)
    assert event["payload"]["outcome"] == "failed"
    assert not (run_dir / "retro.json").exists()


# --- AC 4: evidence is checked by code ----------------------------------------------


def _mixed_evidence(cwd: Path) -> str:
    return answer(
        item(1, ["seq:1"]),                                # keep: existing seq
        item(2, ["seq:999999"]),                           # drop: unknown seq
        item(3, ["state.json"]),                           # keep: file in run dir
        item(4, ["gibt-es-nicht.md"]),                     # drop: missing file
        item(5, [str(cwd / "state.json")]),                # drop: absolute path
        item(6, [f"../{cwd.name}/state.json"]),            # drop: '..' component
        item(7, []),                                       # drop: no evidence
        item(8),                                           # drop: evidence missing
        item(9, ["gibt-es-nicht.md", "seq:2", "/etc"]),    # keep: one valid suffices
    )


def _all_invalid(cwd: Path) -> str:
    return answer(item(1, ["seq:999999"]), item(2, ["../../config.yaml"]), item(3, []))


def _malformed_evidence(cwd: Path) -> str:
    """Malformed evidence is invalid evidence, no schema error: only its item drops."""
    return answer(
        item(1, ["seq:1"]),                                # keep: valid neighbour
        {**item(2), "evidence": None},                     # drop: null
        {**item(3), "evidence": "seq:1"},                  # drop: no list
        item(4, [42]),                                     # drop: no string
        item(5, ["x" * 300]),                              # drop: name too long for the OS
        item(6, ["seq:" + "9" * 5000]),                    # drop: absurd seq
    )


@pytest.mark.parametrize(
    ("make_answer", "kept", "dropped"),
    [(_mixed_evidence, [1, 3, 9], 6), (_all_invalid, [], 3), (_malformed_evidence, [1], 5)],
    ids=["mixed", "all-dropped", "malformed"],
)
def test_items_without_valid_evidence_are_dropped(repo, monkeypatch, make_answer, kept, dropped):
    seen = {}

    def retro(cwd: Path) -> str:
        seen["answer"] = json.loads(make_answer(cwd))
        return json.dumps(seen["answer"])

    retro_seam(monkeypatch, retro=retro)

    res, state = start(repo)

    assert res.exit_code == 0, res.output
    run_dir = state.run_dir(repo)
    data = json.loads((run_dir / "retro.json").read_text(encoding="utf-8"))
    by_problem = {entry["problem"]: entry for entry in seen["answer"]["items"]}
    # kept items stay complete, their invalid evidence included
    assert data["items"] == [by_problem[f"Problem {n}"] for n in kept]
    assert data["dropped"] == dropped
    [event] = retro_events(run_dir)
    assert event["payload"] == {"outcome": "written", "items": len(kept)}


# --- AC 5 / §2: dry run and an existing retro.json → no retro -----------------------


def test_dry_run_gets_no_retro(target_repo, monkeypatch):
    seam = Seam()
    real_runners = cli_mod._dry_run_runners

    def recording():
        agents, codex = real_runners()
        agents.script("retro_agent", GOOD)
        seam.runners.append(agents)
        return agents, codex

    monkeypatch.setattr(cli_mod, "_dry_run_runners", recording)

    res, state = start(target_repo, "--dry-run")

    assert res.exit_code == 0, res.output
    assert state.phase == "done"
    assert seam.runners
    assert_no_retro(seam, state.run_dir(target_repo))


def test_existing_retro_json_is_never_overwritten(repo, monkeypatch):
    seam = retro_seam(monkeypatch)
    res, state = start(repo, gates="plan")
    assert res.exit_code == 2, res.output
    retro_json = state.run_dir(repo) / "retro.json"
    retro_json.write_bytes(b'{"schema_version": 1, "vorhanden": true}\n')

    approved = runner.invoke(app, ["approve", state.run_id, "--repo", str(repo)])

    assert approved.exit_code == 0, approved.output
    assert RunState.load(repo, state.run_id).phase == "done"
    assert seam.retro_calls() == []
    assert retro_json.read_bytes() == b'{"schema_version": 1, "vorhanden": true}\n'
    assert retro_events(state.run_dir(repo)) == []


def test_retro_task_names_the_top_level_event_seq():
    from adw.retro import _task

    task = _task({"run_id": "r1"})
    assert '{"seq": <n>' in task
    assert "top-level" in task.lower()
    assert "payload" in task and "state.saved" in task
