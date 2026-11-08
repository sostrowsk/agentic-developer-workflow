"""W3: ``adw kpis`` — metrics per ADW version and eval candidates from the ledger's
``run-record.json`` files. Every test reads a throwaway ledger clone (``ledger``
fixture) or the absent default ledger; nothing touches ~/.adw/ledger (E10)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adw import ledger as ledger_mod
from adw.cli import app

runner = CliRunner()

NOW = datetime.now(UTC)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def write_record(ledger: Path, slug: str, run_id: str, record) -> Path:
    """``record`` as dict (serialised) or raw bytes, at the ledger layout path."""
    path = ledger / "runs" / slug.replace("/", "__") / run_id / "run-record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = record if isinstance(record, bytes) else json.dumps(record).encode("utf-8")
    path.write_bytes(data)
    return path


def run(run_id: str, **fields) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "repo_slug": "acme/widget",
        "issue_title": f"Issue {run_id}",
        **fields,
    }


def lanes(**gate_iterations) -> dict:
    return {name: {"gate_iterations": n} for name, n in gate_iterations.items()}


def finding(severity, action) -> dict:
    return {"finding_key": "k", "severity": severity, "action": action, "reason": "r"}


# Six runs in two versions; each candidate rule fires for exactly one of them
# (r2 escalated, r3 review_rounds, r4 gate_iterations, r5 circuit_breakers,
# r6 cost_usd: median of [2, 2, 2, 1, 10] is 2 → threshold 3), r1 is unremarkable.
SIX = [
    run(
        "r1", adw_version="0.9.0", started_at=ago(1), phase="done", escalation_reason=None,
        review_rounds=1, fix_cycles=0, lanes=lanes(backend=3, frontend=3), duration_s=100,
        cost_usd=2.0, agents=[{"agent": "build_agent", "cost_usd": 1.5},
                              {"agent": "spec_agent", "cost_usd": 0.5}],
        circuit_breakers=0, approvals=1, findings=[finding("P1", "fix"), finding("P2", "defer")],
    ),
    run(
        "r2", adw_version="0.9.0", started_at=ago(3), phase="escalated",
        escalation_reason="Gate rot", review_rounds=2, fix_cycles=1, lanes=lanes(backend=2),
        duration_s=300, cost_usd=2.0, agents=[{"agent": "build_agent", "cost_usd": 2.0}],
        circuit_breakers=0, approvals=0, findings=[finding("P1", "fix")],
    ),
    run(
        "r3", adw_version="0.9.0", started_at=ago(5), phase="done",
        escalation_reason="fortgesetzt", review_rounds=4, fix_cycles=None, lanes={},
        duration_s=None, cost_usd=None, agents=[], circuit_breakers=None, approvals=2,
        findings=[],
    ),
    run(
        "r4", adw_version="0.10.0", started_at=ago(2), phase="done", escalation_reason=None,
        review_rounds=0, fix_cycles=2, lanes=lanes(backend=5), duration_s=50, cost_usd=2.0,
        agents=[{"agent": "build_agent", "cost_usd": 1.0},
                {"agent": "codex_agent", "cost_usd": None}],
        circuit_breakers=0, approvals=None, findings=[finding("P3", "defer")],
    ),
    run(
        "r5", adw_version="0.10.0", started_at=ago(6), phase="done", escalation_reason=None,
        review_rounds=3, fix_cycles=0, lanes=lanes(backend=0, frontend=4), duration_s=0,
        cost_usd=1.0, agents=[{"agent": "build_agent", "cost_usd": 1.0}],
        circuit_breakers=1, approvals=1, findings=[finding(None, "fix")],
    ),
    run(
        "r6", adw_version="0.10.0", started_at=ago(2), phase="awaiting_approval",
        escalation_reason=None, review_rounds=None, fix_cycles=1, lanes=lanes(backend=None),
        duration_s=400, cost_usd=10.0, agents=[{"agent": "spec_agent", "cost_usd": 0.0}],
        circuit_breakers=0, approvals=0, findings=[],
    ),
]

METRIC_KEYS = {
    "runs", "phase_shares", "escalation_share", "review_rounds", "fix_cycles",
    "gate_iterations", "duration_s", "cost_usd", "agent_cost_shares", "circuit_breakers",
    "approvals", "findings_by_severity", "findings_by_action",
}
# W14: only in ``total``, never per version
SIGNAL_KEYS = {"retro_causes", "retro_runs", "ratings", "finding_feedback"}
NO_SIGNALS = {
    "retro_causes": {},
    "retro_runs": 0,
    "ratings": {"good": 0, "bad": 0},
    "finding_feedback": {"valid": 0, "noise": 0},
}


def approx(value):
    """Floats compared approximately, recursively through dicts/lists."""
    if isinstance(value, dict):
        return {k: approx(v) for k, v in value.items()}
    if isinstance(value, list):
        return [approx(v) for v in value]
    if isinstance(value, float):
        return pytest.approx(value)
    return value


def install(ledger: Path, records: list[dict]) -> None:
    for record in records:
        write_record(ledger, record["repo_slug"], record["run_id"], record)


def kpis_json(*args: str) -> dict:
    res = runner.invoke(app, ["kpis", "--json", *args])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


# --- AC 1: metrics per version and total ------------------------------------------


def test_metrics_per_version_and_total_match_hand_computed_values(ledger):
    install(ledger, SIX)
    result = kpis_json()

    assert [g["adw_version"] for g in result["groups"]] == ["0.9.0", "0.10.0"]
    old, new = result["groups"]
    assert set(old) == METRIC_KEYS | {"adw_version"}
    assert set(result["total"]) == METRIC_KEYS | SIGNAL_KEYS
    assert old == approx({
        "adw_version": "0.9.0",
        "runs": 3,
        "phase_shares": {"done": 2 / 3, "escalated": 1 / 3, "other": 0.0},
        "escalation_share": 2 / 3,  # r2 escalated, r3 continued after escalation
        "review_rounds": {"median": 2, "max": 4},
        "fix_cycles": {"median": 0.5, "max": 1},  # r3 null ignored, even count
        "gate_iterations": {"median": 4.0, "max": 6},  # lane sums 6 and 2, r3 no lanes
        "duration_s": {"median": 200.0},
        "cost_usd": {"sum": 4.0, "median": 2.0},
        "agent_cost_shares": {"build_agent": 0.875, "spec_agent": 0.125},
        "circuit_breakers": 0,
        "approvals": {"median": 1},
        "findings_by_severity": {"P1": 2, "P2": 1},
        "findings_by_action": {"fix": 2, "defer": 1},
    })
    assert new == approx({
        "adw_version": "0.10.0",
        "runs": 3,
        "phase_shares": {"done": 2 / 3, "escalated": 0.0, "other": 1 / 3},
        "escalation_share": 0.0,
        "review_rounds": {"median": 1.5, "max": 3},
        "fix_cycles": {"median": 1, "max": 2},
        "gate_iterations": {"median": 4.5, "max": 5},  # r6 has no numeric lane value
        "duration_s": {"median": 50},
        "cost_usd": {"sum": 13.0, "median": 2.0},
        "agent_cost_shares": {"build_agent": 1.0, "spec_agent": 0.0},
        "circuit_breakers": 1,
        "approvals": {"median": 0.5},
        "findings_by_severity": {"P3": 1},
        "findings_by_action": {"defer": 1, "fix": 1},
    })
    assert result["total"] == approx({
        "runs": 6,
        "phase_shares": {"done": 4 / 6, "escalated": 1 / 6, "other": 1 / 6},
        "escalation_share": 2 / 6,
        "review_rounds": {"median": 2, "max": 4},
        "fix_cycles": {"median": 1, "max": 2},
        "gate_iterations": {"median": 4.5, "max": 6},
        "duration_s": {"median": 100},
        "cost_usd": {"sum": 17.0, "median": 2.0},
        "agent_cost_shares": {"build_agent": 5.5 / 6, "spec_agent": 0.5 / 6},
        "circuit_breakers": 1,
        "approvals": {"median": 1},
        "findings_by_severity": {"P1": 2, "P2": 1, "P3": 1},
        "findings_by_action": {"fix": 3, "defer": 2},
        **NO_SIGNALS,
    })
    assert result["skipped"] == 0


# --- AC 2: filters ----------------------------------------------------------------


def test_since_drops_older_runs_and_runs_without_readable_start(ledger):
    install(ledger, [
        run("new", adw_version="0.9.0", started_at=ago(2), phase="done"),
        run("old", adw_version="0.9.0", started_at=ago(10), phase="done"),
        run("nots", adw_version="0.9.0", phase="done"),
        run("bad", adw_version="0.9.0", started_at="gestern", phase="done"),
    ])

    assert kpis_json()["total"]["runs"] == 4
    result = kpis_json("--since", "7")
    assert result["total"]["runs"] == 1
    assert [g["runs"] for g in result["groups"]] == [1]
    assert result["skipped"] == 0


def test_repo_slug_filters_exactly_and_before_the_cost_median(ledger):
    """Without a filter the costs [1, 1, 4, 4, 4] have median 4 → no cost candidate;
    restricted to acme/a ([1, 1, 4], median 1) the run costing 4 is one."""
    install(ledger, [
        run("a1", repo_slug="acme/a", adw_version="0.9.0", started_at=ago(1), cost_usd=1.0),
        run("a2", repo_slug="acme/a", adw_version="0.9.0", started_at=ago(1), cost_usd=1.0),
        run("a3", repo_slug="acme/a", adw_version="0.9.0", started_at=ago(1), cost_usd=4.0),
        run("b1", repo_slug="acme/ab", adw_version="0.9.0", started_at=ago(1), cost_usd=4.0),
        run("b2", repo_slug="acme/ab", adw_version="0.9.0", started_at=ago(1), cost_usd=4.0),
    ])

    assert kpis_json()["candidates"] == []
    result = kpis_json("--repo-slug", "acme/a")
    assert result["total"]["runs"] == 3
    assert result["total"]["cost_usd"] == {"sum": 6.0, "median": 1.0}
    assert [(c["run_id"], c["rules"]) for c in result["candidates"]] == [("a3", ["cost_usd"])]


# --- AC 3: candidates -------------------------------------------------------------


def test_each_candidate_rule_fires_for_exactly_its_run(ledger):
    install(ledger, SIX)
    candidates = kpis_json()["candidates"]

    # sorted by started_at (r5 oldest … r4/r6 same instant → by run_id); r1 absent
    assert candidates == [
        {"run_id": "r5", "repo_slug": "acme/widget", "adw_version": "0.10.0",
         "rules": ["circuit_breakers"], "issue_title": "Issue r5"},
        {"run_id": "r3", "repo_slug": "acme/widget", "adw_version": "0.9.0",
         "rules": ["review_rounds"], "issue_title": "Issue r3"},
        {"run_id": "r2", "repo_slug": "acme/widget", "adw_version": "0.9.0",
         "rules": ["escalated"], "issue_title": "Issue r2"},
        {"run_id": "r4", "repo_slug": "acme/widget", "adw_version": "0.10.0",
         "rules": ["gate_iterations"], "issue_title": "Issue r4"},
        {"run_id": "r6", "repo_slug": "acme/widget", "adw_version": "0.10.0",
         "rules": ["cost_usd"], "issue_title": "Issue r6"},
    ]


# --- AC 4: JSON and text output ---------------------------------------------------


def test_json_and_text_output_rest_on_the_same_result(ledger):
    install(ledger, SIX)
    write_record(ledger, "acme/widget", "broken", b"{nicht json")

    res = runner.invoke(app, ["kpis", "--json"])
    assert res.exit_code == 0, res.output
    result = json.loads(res.stdout)  # exactly one object, nothing else on stdout
    assert set(result) == {"groups", "total", "candidates", "skipped"}
    assert result == approx(ledger_mod.compute_kpis(SIX) | {"skipped": 1})

    res = runner.invoke(app, ["kpis"])
    assert res.exit_code == 0, res.output
    text = res.stdout
    assert "\x1b" not in text
    assert text.index("0.9.0") < text.index("0.10.0") < text.index("gesamt")
    for candidate in result["candidates"]:
        assert candidate["run_id"] in text
        assert candidate["issue_title"] in text
        for rule in candidate["rules"]:
            assert rule in text
    assert "Issue r1" not in text
    assert "1 übersprungen" in text


# --- AC 5: skipped records --------------------------------------------------------


def test_unknown_schema_and_broken_files_are_skipped_and_counted(ledger):
    install(ledger, [run("ok", adw_version="0.9.0", started_at=ago(1), phase="escalated")])
    write_record(ledger, "acme/widget", "v2", run("v2", schema_version=2, phase="escalated"))
    nover = run("nover", phase="escalated")
    del nover["schema_version"]
    write_record(ledger, "acme/widget", "nover", nover)
    write_record(ledger, "acme/widget", "broken", b'{"schema_version": 1, "run_id": ')
    write_record(ledger, "acme/widget", "list", b"[1, 2]")

    result = kpis_json()
    assert result["skipped"] == 4
    assert result["total"]["runs"] == 1
    assert [c["run_id"] for c in result["candidates"]] == ["ok"]

    res = runner.invoke(app, ["kpis"])
    assert res.exit_code == 0, res.output
    assert res.exception is None
    assert "Traceback" not in res.output
    assert "4 übersprungen" in res.stdout


# --- W14 AC 10: retro causes and human feedback in total --------------------------


def run_folder(ledger: Path, run_id: str, slug: str = "acme/widget") -> Path:
    return ledger / "runs" / slug.replace("/", "__") / run_id


def write_retro(ledger: Path, run_id: str, causes: list[str], slug: str = "acme/widget"):
    items = [
        {"problem": "p", "resolution": "r", "better": "b", "cause": c, "lever": "l",
         "evidence": ["seq:1"]}
        for c in causes
    ]
    retro = {"schema_version": 1, "run_id": run_id, "phase": "done", "summary": "s",
             "items": items, "dropped": 0, "model": "claude-opus-5-5"}
    (run_folder(ledger, run_id, slug) / "retro.json").write_text(
        json.dumps(retro, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def write_feedback(ledger: Path, run_id: str, *entries, slug: str = "acme/widget"):
    """``entries``: ("rate", value) / ("finding", value) tuples or raw str lines."""
    out = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(entry)
            continue
        kind, value = entry
        line = {"ts": "2026-11-08T10:00:00+00:00", "kind": kind, "value": value, "note": None}
        if kind == "finding":
            line["finding_key"] = "x.py|Null-Check fehlt"
        out.append(json.dumps(line))
    (run_folder(ledger, run_id, slug) / "feedback.jsonl").write_text(
        "".join(f"{line}\n" for line in out), encoding="utf-8"
    )


def test_retro_causes_and_feedback_are_counted_in_total(ledger):
    install(ledger, [
        run("r1", adw_version="0.9.0", started_at=ago(1), phase="done"),
        run("r2", adw_version="0.9.0", started_at=ago(1), phase="escalated"),
        run("r3", adw_version="0.10.0", started_at=ago(1), phase="done"),
        run("r4", adw_version="0.10.0", started_at=ago(1), phase="done"),
        run("x1", adw_version="0.10.0", started_at=ago(1), phase="done", repo_slug="other/x"),
    ])
    write_retro(ledger, "r1", ["prompt", "gate", "prompt"])
    write_feedback(ledger, "r1", ("rate", "good"), ("finding", "valid"), ("rate", "bad"),
                   ("finding", "noise"))
    write_retro(ledger, "r2", [])  # a smooth run's retro still counts as a retro run
    write_feedback(ledger, "r2", ("rate", "good"), ("finding", "valid"))
    write_retro(ledger, "r4", ["model"])
    write_feedback(ledger, "r4", ("rate", "bad"), ("rate", "good"))
    write_retro(ledger, "x1", ["orchestrator"], slug="other/x")
    write_feedback(ledger, "x1", ("rate", "bad"), ("finding", "noise"), slug="other/x")

    total = kpis_json()["total"]
    assert total["retro_causes"] == {"prompt": 2, "gate": 1, "model": 1, "orchestrator": 1}
    assert total["retro_runs"] == 4
    assert total["ratings"] == {"good": 2, "bad": 2}  # the last rate line per run counts
    assert total["finding_feedback"] == {"valid": 2, "noise": 2}

    result = kpis_json("--repo-slug", "acme/widget")
    assert {k: result["total"][k] for k in SIGNAL_KEYS} == {
        "retro_causes": {"prompt": 2, "gate": 1, "model": 1},
        "retro_runs": 3,
        "ratings": {"good": 2, "bad": 1},
        "finding_feedback": {"valid": 2, "noise": 1},
    }
    for group in result["groups"]:
        assert set(group) == METRIC_KEYS | {"adw_version"}

    res = runner.invoke(app, ["kpis", "--repo-slug", "acme/widget"])
    assert res.exit_code == 0, res.output
    below_total = res.stdout[res.stdout.index("gesamt"):]
    for cause in ("prompt", "gate", "model"):
        assert cause in below_total
    assert "orchestrator" not in below_total


def test_unreadable_retro_and_feedback_lines_are_ignored(ledger):
    install(ledger, [
        run("a", adw_version="0.9.0", started_at=ago(1), phase="done"),
        run("b", adw_version="0.9.0", started_at=ago(1), phase="done"),
    ])
    (run_folder(ledger, "a") / "retro.json").write_bytes(b'{"items": [')
    (run_folder(ledger, "b") / "retro.json").write_text('{"items": 3}', encoding="utf-8")
    write_feedback(ledger, "a", ("rate", "good"), "{kaputt", "[1, 2]", ("finding", "noise"),
                   ("rate", "meh"))

    res = runner.invoke(app, ["kpis", "--json"])

    assert res.exit_code == 0, res.output
    assert "Traceback" not in res.output
    result = json.loads(res.stdout)
    assert result["skipped"] == 0
    assert result["total"]["runs"] == 2
    assert result["total"]["retro_causes"] == {}
    assert result["total"]["retro_runs"] == 0
    assert result["total"]["ratings"] == {"good": 1, "bad": 0}
    assert result["total"]["finding_feedback"] == {"valid": 0, "noise": 1}
    assert runner.invoke(app, ["kpis"]).exit_code == 0


# --- AC 6: no ledger / empty ledger / invalid option ------------------------------


@pytest.mark.parametrize("kind", ["missing", "no-git", "probe-fails"])
def test_no_ledger_exits_1_with_message(kind, tmp_path, monkeypatch):
    path = tmp_path / "ledger"
    if kind != "missing":
        path.mkdir()
    if kind == "probe-fails":
        (path / ".git").write_text("gitdir: /nonexistent/adw-ledger-git\n")
    monkeypatch.setenv("ADW_LEDGER", str(path))

    res = runner.invoke(app, ["kpis"])

    assert res.exit_code == 1, res.output
    assert str(path) in res.stderr
    assert isinstance(res.exception, SystemExit)  # no traceback-worthy exception
    assert res.stdout == ""


def test_negative_since_is_rejected_before_the_ledger_is_read():
    """The autouse ledger path does not exist: rejecting --since must come first
    (otherwise this would be the "no ledger" exit 1)."""
    res = runner.invoke(app, ["kpis", "--since", "-1"])
    assert res.exit_code not in (0, 1), res.output


def test_empty_ledger_gives_empty_result_without_invented_values(ledger):
    result = kpis_json()
    assert result == {
        "groups": [],
        "total": {
            "runs": 0,
            "phase_shares": {"done": None, "escalated": None, "other": None},
            "escalation_share": None,
            "review_rounds": {"median": None, "max": None},
            "fix_cycles": {"median": None, "max": None},
            "gate_iterations": {"median": None, "max": None},
            "duration_s": {"median": None},
            "cost_usd": {"sum": None, "median": None},
            "agent_cost_shares": {},
            "circuit_breakers": None,
            "approvals": {"median": None},
            "findings_by_severity": {},
            "findings_by_action": {},
            **NO_SIGNALS,
        },
        "candidates": [],
        "skipped": 0,
    }


# --- AC 7: read-only ----------------------------------------------------------------


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else b"<dir>"
        for p in sorted(root.rglob("*"))
    }


def test_kpis_writes_nothing(ledger):
    install(ledger, SIX)
    write_record(ledger, "acme/widget", "broken", b"{nicht json")
    before = snapshot(ledger)

    for args in (["kpis"], ["kpis", "--json"], ["kpis", "--since", "7", "--repo-slug", "x/y"]):
        res = runner.invoke(app, args)
        assert res.exit_code == 0, res.output

    assert snapshot(ledger) == before
