"""W15: ``adw improve`` — weekly improvement report (and one brief draft) from the
ledger. Every test uses a throwaway ledger clone (``ledger`` fixture) or an absent
ledger; the agent runner is faked via ``adw.improve.SdkAgentRunner`` (E8): no model,
no network, nothing touches ~/.adw/ledger."""

import fcntl
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

import adw
from adw import improve as improve_mod
from adw.agents import AgentResult
from adw.cli import app
from tests.conftest import git

runner = CliRunner()

NOW = datetime.now(UTC)
_YEAR, _WEEK, _ = NOW.date().isocalendar()
WEEK = f"{_YEAR}-W{_WEEK:02d}"
ADW_SOURCE = Path(adw.__file__).resolve().parent.parent
SECTIONS = [
    "## Kennzahlen",
    "## Problemgruppen aus Retros",
    "## Wiederkehrende Findings",
    "## Regressionsfall-Kandidaten",
    "## Hebel je Gruppe",
]
TOO_FEW = "Zu wenige Läufe für eine Auswertung"
LONG = "L" * 400
BRIEF_MD = (
    "# W99: Besserer Orchestrator\n\n## Ausgangslage\n\nx\n\n## Aufgabe\n\nx\n\n"
    "## Vorentscheidungen\n\nx\n\n## Deferred\n\nx\n\n## Akzeptanzkriterien\n\n1. x\n"
)

GOOD = BRIEF_MD
TITLE = "W99: Besserer Orchestrator"


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def folder(ledger: Path, run_id: str, slug: str = "acme/widget") -> Path:
    path = ledger / "runs" / slug.replace("/", "__") / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def add_run(ledger, run_id, days, cost, *, phase="done", findings=(), causes=(),
            feedback=(), slug="acme/widget", problem=None, schema=1, **extra):
    record = {
        **extra, "schema_version": schema, "run_id": run_id, "repo_slug": slug,
        "issue_title": f"Issue {run_id}", "adw_version": "0.36.3",
        "started_at": ago(days), "phase": phase, "cost_usd": cost,
        "findings": [
            {"finding_key": key, "severity": sev, "action": "fix", "reason": "r",
             "loop": "codex_review", "phase": "codex_review", "round": 1}
            for key, sev in findings
        ],
    }
    path = folder(ledger, run_id, slug)
    (path / "run-record.json").write_text(json.dumps(record), encoding="utf-8")
    if causes:
        items = [
            {"problem": problem or f"Problem {run_id} {c}", "resolution": "r",
             "better": f"Besser {run_id}", "cause": c, "lever": f"Hebel {run_id}",
             "evidence": [f"seq:{i}"]}
            for i, c in enumerate(causes, 1)
        ]
        retro = {"schema_version": 1, "run_id": run_id, "phase": phase, "summary": "s",
                 "items": items, "dropped": 0, "model": "claude-opus-5-5"}
        (path / "retro.json").write_text(json.dumps(retro), encoding="utf-8")
    if feedback:
        lines = []
        for kind, value, *key in feedback:
            line = {"ts": "2026-11-08T10:00:00+00:00", "kind": kind, "value": value,
                    "note": None}
            if key:
                line["finding_key"] = key[0]
            lines.append(json.dumps(line) + "\n")
        (path / "feedback.jsonl").write_text("".join(lines), encoding="utf-8")


def install(ledger: Path) -> None:
    """5 current runs + 2 older ones, committed. Groups (runs × cost):
    orchestrator 1×10=10 (two entries, one run), gate 2×(1+2)=6, prompt 1×4=4,
    model 1×3=3. ``a.py|Bug`` recurs in r1/r2; ``b.py|Solo`` only in r3 (twice).
    r2 escalated, r3 rated bad, r4 bad→good (not a candidate)."""
    add_run(ledger, "r1", 1, 10.0, causes=["orchestrator", "orchestrator"], problem=LONG,
            circuit_breakers=3, approvals=2, review_rounds=4,
            agents=[{"agent": "build_agent", "cost_usd": 10.0}],
            findings=[("a.py|Bug", "P1"), ("a.py|Bug", "P1")],
            feedback=[("finding", "valid", "a.py|Bug")])
    add_run(ledger, "r2", 2, 1.0, phase="escalated", causes=["gate"],
            findings=[("a.py|Bug", "P1")],
            feedback=[("finding", "noise", ["unhashable"])])  # malformed: skipped

    add_run(ledger, "r3", 3, 2.0, causes=["gate"],
            findings=[("b.py|Solo", "P2"), ("b.py|Solo", "P2")],
            feedback=[("rate", "bad")])
    add_run(ledger, "r4", 4, 3.0, causes=["model"],
            feedback=[("rate", "bad"), ("rate", "good")])
    add_run(ledger, "r5", 5, 4.0, causes=["prompt"], findings=[("c.py|Eins", "P3")])
    path = ledger / "runs" / "acme__widget" / "r5" / "run-record.json"  # malformed run_id
    path.write_text(path.read_text().replace('"run_id": "r5"', '"run_id": ["r5"]'))
    add_run(ledger, "o1", 9, 50.0, phase="escalated", causes=["target_repo"])
    add_run(ledger, "o2", 10, 60.0)
    # unsupported schema: excluded from every section, not only from the KPIs
    add_run(ledger, "s9", 1, 999.0, schema=999, phase="escalated", causes=["issue_quality"],
            findings=[("b.py|Solo", "P2")])
    commit_all(ledger)


def commit_all(ledger: Path) -> None:
    git(ledger, "add", "-A")
    git(ledger, "commit", "-m", "fixture")


def commits(ledger: Path) -> int:
    return int(git(ledger, "rev-list", "--count", "HEAD"))


class FakeRunner:
    calls: list = []

    def __init__(self, answer):
        self.answer = answer

    def __call__(self, *args, **kwargs):  # stands in for the SdkAgentRunner class
        return self

    def run(self, agent, task, cwd, *args, **kwargs):
        FakeRunner.calls.append((agent, task, Path(cwd)))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return AgentResult(text=self.answer, session_id=None)


@pytest.fixture
def agent(monkeypatch):
    """Install a fake runner; ``agent(answer)`` sets the answer, returns the calls."""
    FakeRunner.calls = []

    def install_answer(answer=GOOD):
        monkeypatch.setattr(improve_mod, "SdkAgentRunner", FakeRunner(answer))
        return FakeRunner.calls

    install_answer()
    return install_answer


def improve(*args: str):
    return runner.invoke(app, ["improve", *args])


def report(ledger: Path) -> str:
    return (ledger / "reports" / f"{WEEK}.md").read_text(encoding="utf-8")


def brief_path(ledger: Path) -> Path:
    return ledger / "reports" / f"{WEEK}-brief.md"


def rejected_path(ledger: Path) -> Path:
    return ledger / "reports" / f"{WEEK}-brief.rejected.md"


def head_files(ledger: Path) -> list[str]:
    return git(ledger, "ls-tree", "-r", "--name-only", "HEAD").split()


def section(text: str, heading: str) -> str:
    start = text.index(heading)
    later = [text.index(h) for h in SECTIONS if h in text and text.index(h) > start]
    return text[start:min(later)] if later else text[start:]


# --- AC 1–3: full report ---------------------------------------------------------------


def test_report_has_five_sections_with_sorted_groups_and_truncation(ledger, agent):
    install(ledger)

    res = improve()

    assert res.exit_code == 0, res.output
    text = report(ledger)
    positions = [text.index(h) for h in SECTIONS]
    assert positions == sorted(positions)
    assert TOO_FEW not in text
    groups = section(text, SECTIONS[1])
    order = [groups.index(c) for c in ("orchestrator", "gate", "prompt", "model")]
    assert "### orchestrator\n\nEinträge 2, Läufe 1, Kosten 10 USD" in groups  # run once
    assert "### gate\n\nEinträge 2, Läufe 2, Kosten 3 USD" in groups
    assert order == sorted(order)
    assert "target_repo" not in groups and "o1" not in groups  # older run excluded
    assert LONG not in text and "L" * 299 in groups
    assert all(len(line) < 400 for line in groups.splitlines())
    levers = section(text, SECTIONS[4])
    assert "Brief für einen ADW-Lauf" in levers
    assert "Kandidat für Hill Climbing (W7)" in levers
    assert "Wissen für das Ziel-Repo" not in levers  # target_repo only in older run
    assert "Brief-Vorlage schärfen" not in levers
    assert "issue_quality" not in text and "s9" not in text
    kpis = section(text, SECTIONS[0])
    previous = kpis.split("Vorzeitraum", 1)[1]
    [total_row] = [ln for ln in previous.splitlines() if ln.startswith("| gesamt")]
    assert total_row.split("|")[2].strip() == "2"  # exactly the two older runs
    for needle in ["done/esc/sonst", "Runden med/max", "4/4", "CB", "Appr. med",
                   "build_agent=", "P1=", "fix="]:
        assert needle in kpis, needle
    # stdout: report path, then one count per section in contract order
    assert f"{WEEK}.md" in res.stdout
    counts = [int(re.search(r"\d+", line).group()) for line in res.stdout.splitlines()[-5:]]
    assert counts == [5, 4, 1, 2, 4]


def test_recurring_findings_count_runs_and_show_feedback(ledger, agent):
    install(ledger)

    assert improve("--no-brief").exit_code == 0
    findings = section(report(ledger), SECTIONS[2])

    assert "a.py|Bug" in findings
    [line] = [ln for ln in findings.splitlines() if "a.py|Bug" in ln]
    assert "2" in line and "P1" in line and "valid" in line
    assert "b.py|Solo" not in findings


def test_regression_candidates_are_escalated_and_last_bad(ledger, agent):
    install(ledger)

    assert improve("--no-brief").exit_code == 0
    candidates = section(report(ledger), SECTIONS[3])

    assert "r2" in candidates and "Issue r2" in candidates
    assert "r3" in candidates and "Issue r3" in candidates
    assert "escalated" in candidates and "bad" in candidates
    assert "r4" not in candidates and "o1" not in candidates


# --- AC 4: too few runs ----------------------------------------------------------------


def test_too_few_runs_gives_only_kpis_and_no_agent(ledger, agent):
    add_run(ledger, "r1", 1, 1.0, causes=["orchestrator"])
    add_run(ledger, "r2", 2, 1.0, causes=["gate"])
    add_run(ledger, "o1", 30, 1.0, causes=["gate"])
    commit_all(ledger)

    res = improve()

    assert res.exit_code == 0, res.output
    text = report(ledger)
    assert SECTIONS[0] in text and TOO_FEW in text
    assert not any(h in text for h in SECTIONS[1:])
    assert FakeRunner.calls == []
    assert not brief_path(ledger).exists()
    after_path = res.stdout.split(f"{WEEK}.md", 1)[1]
    assert len(re.findall(r"\d+", after_path)) == 1 and "2" in after_path


# --- AC 5–6: brief draft ---------------------------------------------------------------


def test_orchestrator_group_gets_one_brief_from_adw_source(ledger, agent):
    install(ledger)
    calls = agent()

    res = improve()

    assert res.exit_code == 0, res.output
    [(spec, task, cwd)] = calls
    assert spec.name == "improve_agent"
    assert cwd.resolve() == ADW_SOURCE
    assert "orchestrator" in task
    assert brief_path(ledger).read_text(encoding="utf-8").count("## Ausgangslage") == 1


@pytest.mark.parametrize("answer", [
    BRIEF_MD,
    "```markdown\n" + BRIEF_MD + "```\n",
    ("```markdown\n" + BRIEF_MD + "```\n").replace("\n", "\r\n"),
    "```markdown\n" + BRIEF_MD + "\n```python\nprint(1)\n```\n```\n",
], ids=["plain", "markdown-fence", "markdown-fence-crlf", "markdown-fence-inner-block"])
def test_markdown_brief_is_accepted_with_h1_title(ledger, agent, answer):
    install(ledger)
    agent(answer)

    res = improve()

    assert res.exit_code == 0, res.output
    brief = brief_path(ledger).read_text(encoding="utf-8")
    assert brief.lstrip().startswith("# W99: Besserer Orchestrator")
    assert "```markdown" not in brief
    assert TITLE in report(ledger)
    assert "Brief-Entwurf verworfen" not in report(ledger)
    assert not rejected_path(ledger).exists()


@pytest.mark.parametrize("answer, reason", [
    (BRIEF_MD.replace("# W99: Besserer Orchestrator\n\n", ""), "H1"),
    (BRIEF_MD.replace("## Deferred", "## X"), "Deferred"),
    ("# T\n\n## Ausgangslage Aufgabe Vorentscheidungen Deferred Akzeptanzkriterien\n",
     "Überschriften fehlen"),
    (BRIEF_MD.replace("## Deferred\n\nx\n", "```\n## Deferred\n```\n"), "Deferred"),
    ("````\n```\n" + BRIEF_MD + "````\n", "H1"),
    ('{"title": "T", "brief_markdown": "# T"}\n', "H1"),
    ("```markdown\n" + BRIEF_MD + "```\n\nProsa\n\n```python\nprint(1)\n```\n", "H1"),
], ids=["missing-h1", "missing-heading", "combined-heading", "fenced", "long-fence",
        "json", "two-separate-blocks"])
def test_invalid_brief_answer_is_rejected_and_saved(ledger, agent, answer, reason):
    install(ledger)
    calls = agent(answer)

    res = improve()

    assert res.exit_code == 0, res.output
    assert len(calls) == 1
    assert not brief_path(ledger).exists()
    assert rejected_path(ledger).read_text(encoding="utf-8") == answer
    note = [line for line in report(ledger).splitlines()
            if "Brief-Entwurf verworfen: " in line]
    assert note, report(ledger)
    assert reason in note[0] and f"{WEEK}-brief.rejected.md" in note[0]
    assert f"reports/{WEEK}-brief.rejected.md" in head_files(ledger)
    assert git(ledger, "status", "--porcelain", "reports") == ""


def test_valid_brief_after_rejected_one_removes_rejected_file(ledger, agent):
    install(ledger)
    agent(BRIEF_MD.replace("## Deferred", "## X"))
    assert improve().exit_code == 0
    assert rejected_path(ledger).exists()

    agent(GOOD)
    assert improve().exit_code == 0
    assert brief_path(ledger).exists()
    assert TITLE in report(ledger)
    assert not rejected_path(ledger).exists()
    files = head_files(ledger)
    assert f"reports/{WEEK}-brief.rejected.md" not in files
    assert f"reports/{WEEK}-brief.md" in files
    status = git(ledger, "show", "--no-renames", "--name-status", "--format=", "HEAD")
    assert f"D\treports/{WEEK}-brief.rejected.md" in status


@pytest.mark.parametrize("exc", [RuntimeError("Agent kaputt"), TimeoutError("zu langsam")],
                         ids=["error", "timeout"])
def test_agent_exception_is_noted_without_retry(ledger, agent, exc):
    install(ledger)
    calls = agent(exc)

    res = improve()

    assert res.exit_code == 0, res.output
    assert len(calls) == 1
    assert not brief_path(ledger).exists()
    assert not rejected_path(ledger).exists()
    assert str(exc) in report(ledger)
    assert all(h in report(ledger) for h in SECTIONS)


def test_no_brief_flag_or_no_eligible_group_calls_no_agent(ledger, agent):
    install(ledger)
    res = improve("--no-brief")
    assert res.exit_code == 0, res.output
    assert FakeRunner.calls == [] and not brief_path(ledger).exists()
    assert f"{WEEK}-brief.md" not in git(ledger, "show", "--name-only", "--format=", "HEAD")

    # other repo: only model/context_missing groups → not eligible
    for i, cause in enumerate(["model", "context_missing", "model"], 1):
        add_run(ledger, f"x{i}", i, 1.0, causes=[cause], slug="other/x")
    commit_all(ledger)
    res = improve("--repo-slug", "other/x")
    assert res.exit_code == 0, res.output
    assert FakeRunner.calls == []
    assert "r1" not in report(ledger)


# --- AC 7: files, commit, overwrite, push ----------------------------------------------


def test_one_commit_per_call_overwrite_and_brief_removal(ledger, agent):
    install(ledger)
    before = commits(ledger)
    (ledger / "fremd.txt").write_text("staged by someone else\n", encoding="utf-8")
    git(ledger, "add", "fremd.txt")

    assert improve().exit_code == 0
    assert commits(ledger) == before + 1
    assert git(ledger, "log", "-1", "--format=%s") == f"improve {WEEK}"
    files = git(ledger, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == [f"reports/{WEEK}-brief.md", f"reports/{WEEK}.md"]
    assert ".improve.lock" not in git(ledger, "ls-files")

    agent(BRIEF_MD + "\nZweite Fassung\n")
    assert improve().exit_code == 0
    assert "Zweite Fassung" in brief_path(ledger).read_text(encoding="utf-8")
    assert commits(ledger) == before + 2

    assert improve("--no-brief").exit_code == 0
    assert not brief_path(ledger).exists()
    assert commits(ledger) == before + 3
    status = git(ledger, "show", "--name-status", "--format=", "HEAD")
    assert f"D\treports/{WEEK}-brief.md" in status
    assert "--no-brief" in report(ledger)  # reason for the missing brief

    assert improve("--no-brief").exit_code == 0
    assert commits(ledger) == before + 3  # unchanged artifacts: no empty commit
    status = git(ledger, "status", "--porcelain").splitlines()
    assert sorted(status) == ["?? .improve.lock", "A  fremd.txt"]  # staged, never committed


def test_push_failure_warns_and_keeps_local_artifacts(ledger, agent, tmp_path):
    install(ledger)
    git(ledger, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    before = commits(ledger)

    res = improve()

    assert res.exit_code == 0, res.output
    assert res.stderr.strip()
    assert (ledger / "reports" / f"{WEEK}.md").exists() and brief_path(ledger).exists()
    assert commits(ledger) == before + 1


# --- AC 8: lock and missing ledger -----------------------------------------------------


def test_held_lock_or_missing_ledger_exits_1(ledger, agent, tmp_path, monkeypatch):
    install(ledger)
    exclude = ledger / ".git" / "info" / "exclude"
    exclude_before = exclude.read_bytes() if exclude.exists() else None
    with open(ledger / ".improve.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        res = improve()
    assert res.exit_code == 1
    assert res.stderr.strip()
    assert not (ledger / "reports").exists()
    assert (exclude.read_bytes() if exclude.exists() else None) == exclude_before
    assert FakeRunner.calls == []

    monkeypatch.setenv("ADW_LEDGER", str(tmp_path / "kein-ledger"))
    res = improve()
    assert res.exit_code == 1
    assert res.stderr.strip()
    assert FakeRunner.calls == []


# --- AC 9: timer units -----------------------------------------------------------------


def test_systemd_units_are_complete():
    units = ADW_SOURCE / "examples" / "systemd"
    service = (units / "adw-improve.service").read_text(encoding="utf-8")
    timer = (units / "adw-improve.timer").read_text(encoding="utf-8")

    for needle in ["[Unit]", "[Service]", "Type=oneshot",
                   "ExecStart=uv run --project <ADW-Pfad> adw improve"]:
        assert needle in service
    for needle in ["[Unit]", "[Timer]", "OnCalendar=Mon *-*-* 07:00", "Persistent=true",
                   "[Install]", "WantedBy=timers.target"]:
        assert needle in timer
