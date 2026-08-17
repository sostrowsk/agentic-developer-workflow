"""CLI tests for the `--gates` switch (TDD RED step).

`adw run` gains a single, speaking switch `--gates none|spec|plan|both` over
the already-existing 4-way gate matrix (today spanned by the two independent
booleans `--no-approval` and `--spec-approval`). These tests pin ONLY the
externally observable surface named in the contract:

  * the four `--gates` values and where the run halts (AC1),
  * `--gates none` reaching `done`, escalation still halting (AC2),
  * the implicit default `plan` (AC3),
  * the three legacy-flag alias equivalences (AC4),
  * rejection of contradiction and of an invalid value — nonzero exit, no
    persistent run (AC5/AC6),
  * backward compatibility of existing run states (AC7),
  * the effective gate mode printed at start (AC8),
  * `adw run --help` describing the matrix (AC9).

All assertions go through the CLI (typer CliRunner), dry-run mode, real git.
"""

import json

import pytest
from typer.testing import CliRunner

from adw.cli import app
from adw.state import RunState, StateNotFoundError
from tests.conftest import git, write_config

runner = CliRunner()

# Ein rotes Gate erzwingt eine Eskalation (AC2): der einzige Halt im none-Modus.
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


def _run(target_repo, *flags):
    return runner.invoke(
        app, ["run", "--repo", str(target_repo), "--issue", "Demo", "--dry-run", *flags]
    )


def _approve(target_repo, run_id):
    return runner.invoke(app, ["approve", run_id, "--repo", str(target_repo)])


def _commit_config(target_repo, content):
    write_config(target_repo, content)
    git(target_repo, "add", ".adw/config.yaml")
    git(target_repo, "commit", "-m", "config")


# =====================================================================
# AC1 — die vier Werte erzeugen genau die vier Matrix-Zeilen (je ein Test)
# =====================================================================


def test_gates_none_holds_at_no_gate(target_repo):
    """`none`: kein Spec-Halt, kein Plan-Halt — der Lauf erreicht done."""
    result = _run(target_repo, "--gates", "none")
    assert result.exit_code == 0, result.output
    assert RunState.find_latest(target_repo).phase == "done"


def test_gates_spec_holds_after_spec_only(target_repo):
    """`spec`: Stopp NACH der Spec, VOR dem Plan; nach der Spec-Freigabe KEIN
    zweiter Halt am Plan-Gate."""
    result = _run(target_repo, "--gates", "spec")
    assert result.exit_code == 2, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_spec_approval"
    approved = _approve(target_repo, state.run_id)
    assert approved.exit_code == 0, approved.output  # kein Plan-Halt
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_gates_plan_holds_before_build_only(target_repo):
    """`plan`: kein Spec-Halt, ein Plan-Halt vor dem Build (Default-Verhalten)."""
    result = _run(target_repo, "--gates", "plan")
    assert result.exit_code == 2, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_approval"  # NICHT awaiting_spec_approval
    approved = _approve(target_repo, state.run_id)
    assert approved.exit_code == 0, approved.output
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_gates_both_holds_at_both_gates(target_repo):
    """`both`: Stopp zuerst nach der Spec, nach deren Freigabe erneut vor dem
    Build, danach Durchlauf bis done."""
    result = _run(target_repo, "--gates", "both")
    assert result.exit_code == 2, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "awaiting_spec_approval"
    at_plan = _approve(target_repo, state.run_id)
    assert at_plan.exit_code == 2, at_plan.output  # zweiter Halt am Plan-Gate
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_approval"
    done = _approve(target_repo, state.run_id)
    assert done.exit_code == 0, done.output
    assert RunState.load(target_repo, state.run_id).phase == "done"


# =====================================================================
# AC2 — `--gates none` läuft durch bis done; nur eine Eskalation hält an
# =====================================================================


def test_gates_none_still_stops_on_escalation(target_repo):
    """Im none-Modus ist eine Eskalation der EINZIGE Halt (Exit 1)."""
    _commit_config(target_repo, RED_GATE_CONFIG)
    result = _run(target_repo, "--gates", "none")
    assert result.exit_code == 1, result.output
    state = RunState.find_latest(target_repo)
    assert state.phase == "escalated"
    assert (state.run_dir(target_repo) / "escalation.md").is_file()


# =====================================================================
# AC3 — `adw run` ohne Flags ist beobachtbar identisch zu `--gates plan`
# =====================================================================


def test_default_equals_gates_plan(target_repo):
    default = _run(target_repo)
    sd = RunState.find_latest(target_repo)
    explicit = _run(target_repo, "--gates", "plan")
    se = RunState.find_latest(target_repo)
    assert default.exit_code == explicit.exit_code == 2
    assert sd.run_id != se.run_id  # zwei eigenständige Runs, nicht derselbe
    # kein Spec-Halt, Halt vor dem Build — für beide Aufrufe identisch:
    assert sd.phase == se.phase == "awaiting_approval"


# =====================================================================
# AC4 — die drei Alias-Äquivalenzen, je ein Test
# =====================================================================


def test_no_approval_equals_gates_none(target_repo):
    legacy = _run(target_repo, "--no-approval")
    sl = RunState.find_latest(target_repo)
    modern = _run(target_repo, "--gates", "none")
    sm = RunState.find_latest(target_repo)
    assert legacy.exit_code == modern.exit_code == 0
    assert sl.phase == sm.phase == "done"


def test_spec_approval_equals_gates_both(target_repo):
    legacy = _run(target_repo, "--spec-approval")
    sl = RunState.find_latest(target_repo)
    modern = _run(target_repo, "--gates", "both")
    sm = RunState.find_latest(target_repo)
    assert legacy.exit_code == modern.exit_code == 2
    assert sl.phase == sm.phase == "awaiting_spec_approval"
    # Nach der Spec-Freigabe hält BEIDE erneut am Plan-Gate:
    lp = _approve(target_repo, sl.run_id)
    mp = _approve(target_repo, sm.run_id)
    assert lp.exit_code == mp.exit_code == 2
    assert (
        RunState.load(target_repo, sl.run_id).phase
        == RunState.load(target_repo, sm.run_id).phase
        == "awaiting_approval"
    )


def test_no_approval_and_spec_approval_equals_gates_spec(target_repo):
    legacy = _run(target_repo, "--no-approval", "--spec-approval")
    sl = RunState.find_latest(target_repo)
    modern = _run(target_repo, "--gates", "spec")
    sm = RunState.find_latest(target_repo)
    assert legacy.exit_code == modern.exit_code == 2
    assert sl.phase == sm.phase == "awaiting_spec_approval"
    # Nach der Spec-Freigabe läuft BEIDE OHNE Plan-Halt bis done durch:
    lp = _approve(target_repo, sl.run_id)
    mp = _approve(target_repo, sm.run_id)
    assert lp.exit_code == mp.exit_code == 0
    assert (
        RunState.load(target_repo, sl.run_id).phase
        == RunState.load(target_repo, sm.run_id).phase
        == "done"
    )


# =====================================================================
# AC5 — Widerspruch `--gates` ↔ Altflag: Ablehnung, kein persistenter Run
# =====================================================================

CONFLICTING = [
    ["--gates", "spec", "--spec-approval"],   # Altflags lösen zu both auf
    ["--gates", "none", "--spec-approval"],
    ["--gates", "both", "--no-approval"],
    ["--gates", "plan", "--no-approval"],      # nichts löst zu plan auf
    ["--gates", "plan", "--spec-approval"],
]


@pytest.mark.parametrize("flags", CONFLICTING)
def test_conflicting_combinations_are_rejected(target_repo, flags):
    result = _run(target_repo, *flags)
    assert result.exit_code != 0, result.output  # Nichtnull-Exit
    assert result.exit_code != 2  # keine reguläre Approval-Pause
    assert (result.output or "").strip()  # klare Meldung vorhanden
    with pytest.raises(StateNotFoundError):
        RunState.find_latest(target_repo)  # KEIN persistenter Run


ACCEPTED = [
    (["--gates", "none", "--no-approval"], 0, "done"),
    (["--gates", "spec", "--no-approval", "--spec-approval"], 2, "awaiting_spec_approval"),
    (["--gates", "both", "--spec-approval"], 2, "awaiting_spec_approval"),
]


@pytest.mark.parametrize("flags,exit_code,phase", ACCEPTED)
def test_redundant_but_consistent_combinations_are_accepted(target_repo, flags, exit_code, phase):
    result = _run(target_repo, *flags)
    assert result.exit_code == exit_code, result.output
    assert RunState.find_latest(target_repo).phase == phase  # Run läuft an


def test_conflict_check_is_order_independent(target_repo):
    """Reihenfolgeunabhängig: derselbe Widerspruch wird in beiden Reihenfolgen
    abgelehnt, dieselbe redundante Angabe in beiden Reihenfolgen akzeptiert."""
    for flags in (
        ["--gates", "both", "--no-approval"],
        ["--no-approval", "--gates", "both"],
    ):
        rejected = _run(target_repo, *flags)
        assert rejected.exit_code not in (0, 2), (flags, rejected.output)
        with pytest.raises(StateNotFoundError):
            RunState.find_latest(target_repo)

    for flags in (
        ["--gates", "none", "--no-approval"],
        ["--no-approval", "--gates", "none"],
    ):
        accepted = _run(target_repo, *flags)
        assert accepted.exit_code == 0, (flags, accepted.output)
        assert RunState.find_latest(target_repo).phase == "done"


# =====================================================================
# AC6 — ungültiger Wert: Ablehnung, Meldung nennt die vier Werte, kein Run
# =====================================================================


def test_invalid_gates_value_is_rejected(target_repo):
    result = _run(target_repo, "--gates", "wide-open")
    assert result.exit_code != 0, result.output
    text = " ".join(result.output.split())
    for value in ("none", "spec", "plan", "both"):
        assert value in text, f"{value!r} fehlt in der Fehlermeldung: {text}"
    with pytest.raises(StateNotFoundError):
        RunState.find_latest(target_repo)  # kein Run angelegt


# =====================================================================
# AC7 — Bestandsschutz: Run-States im heutigen Format bleiben nutzbar
# =====================================================================


def _write_legacy_state(target_repo, run_id, *, phase, skip_approval, spec_approval):
    """Schreibt eine state.json im HEUTIGEN Format (nur die bestehenden Felder,
    inkl. der beiden Booleans `skip_approval`/`spec_approval`) — ohne jedes evtl.
    neu hinzukommende Feld. Lädt sie ohne Migration, muss der Bestandsschutz
    halten."""
    runs_dir = target_repo / ".adw" / "runs"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    # Selbst-ignorierende .gitignore wie im echten Lauf (ensure_runs_gitignored) —
    # sonst gilt der frische Run-Ordner dem Vorflug als fremde dirty Änderung.
    (runs_dir / ".gitignore").write_text("*\n", encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "issue": "Legacy Demo",
                "phase": phase,
                "parallel": False,
                "dry_run": True,
                "pinned_base_branch": "staging",
                "skip_approval": skip_approval,
                "spec_approval": spec_approval,
            }
        ),
        encoding="utf-8",
    )


def test_legacy_plan_gate_state_resumes_and_approves(target_repo):
    """Heutiges Format, beide Booleans False → Plan-Gate: resume hält vor dem
    Build, approve schließt ab. Lädt ohne Migration."""
    run_id = "a1b2c3d4"
    _write_legacy_state(target_repo, run_id, phase="spec", skip_approval=False, spec_approval=False)
    resumed = runner.invoke(app, ["resume", run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 2, resumed.output
    assert RunState.load(target_repo, run_id).phase == "awaiting_approval"
    approved = _approve(target_repo, run_id)
    assert approved.exit_code == 0, approved.output
    assert RunState.load(target_repo, run_id).phase == "done"


def test_legacy_skip_approval_state_runs_through(target_repo):
    """Heutiges Format, skip_approval True → das gespeicherte Boolean treibt das
    Gate: resume läuft ohne Halt bis done."""
    run_id = "b2c3d4e5"
    _write_legacy_state(target_repo, run_id, phase="spec", skip_approval=True, spec_approval=False)
    resumed = runner.invoke(app, ["resume", run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 0, resumed.output
    assert RunState.load(target_repo, run_id).phase == "done"


def test_legacy_spec_approval_state_stops_after_spec(target_repo):
    """Heutiges Format, spec_approval True → das gespeicherte Boolean treibt das
    Spec-Gate: resume hält nach der Spec."""
    run_id = "c3d4e5f6"
    _write_legacy_state(target_repo, run_id, phase="spec", skip_approval=False, spec_approval=True)
    resumed = runner.invoke(app, ["resume", run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 2, resumed.output
    assert RunState.load(target_repo, run_id).phase == "awaiting_spec_approval"


# =====================================================================
# AC8 — der wirksame Gate-Modus wird beim Start ausgegeben
# =====================================================================


def test_effective_gate_mode_is_printed_at_start(target_repo):
    # (a) expliziter --gates-Aufruf: none läuft durch, "none" ist eindeutig.
    gates_call = _run(target_repo, "--gates", "none")
    assert gates_call.exit_code == 0, gates_call.output
    assert "none" in gates_call.output

    # (b) reiner Altflag-Aufruf: --spec-approval wirkt als Modus both.
    altflag_call = _run(target_repo, "--spec-approval")
    assert altflag_call.exit_code == 2, altflag_call.output
    assert "both" in altflag_call.output

    # (c) impliziter Default: Modus plan wird bei Start ausgegeben. Der spätere
    # Plan-Approval-Hinweis enthält ebenfalls "plan" — deshalb nur den
    # Ausgabeteil VOR dem Hinweis prüfen, damit die Modus-Ausgabe belegt ist.
    default_call = _run(target_repo)
    assert default_call.exit_code == 2, default_call.output
    at_start = default_call.output.split("Plan-Approval")[0]
    assert "plan" in at_start


# =====================================================================
# AC9 — `adw run --help` erklärt die Matrix statt zwei Booleans
# =====================================================================


def test_run_help_describes_the_gates_matrix(target_repo):
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "--gates" in text
    for value in ("none", "spec", "plan", "both"):
        assert value in text
    assert "efault" in text  # plan als Default beschrieben
    # Die weiterhin gültigen Altflags bleiben im Hilfetext beschrieben:
    assert "--no-approval" in text
    assert "--spec-approval" in text


# =====================================================================
# Review-Finding (P2): der wirksame skip_approval-Entscheid muss beim
# Run-Start gepinnt werden — nicht erst lazy in run_spec_and_plan. Sonst
# lässt ein Crash zwischen dem ersten state.save und dem lazy-Pin einen
# resumierbaren State mit skip_approval=false zurück, der den Modus beim
# Resume ohne CLI-Flags still verschiebt (none→plan, spec→both).
# =====================================================================


def _crash_before_first_phase(monkeypatch):
    """Ersetzt run_spec_and_plan durch einen sauberen, resumierbaren Abbruch
    NACH dem ersten state.save (AgentRunError → Exit 1, Phase unverändert)."""
    import adw.cli as cli_mod
    from adw.agents import AgentRunError

    def crash(ctx):
        raise AgentRunError("Crash vor dem Pinnen des Gate-Modus")

    monkeypatch.setattr(cli_mod, "run_spec_and_plan", crash)


def test_gates_none_pins_skip_approval_before_first_save(target_repo, monkeypatch):
    """`--gates none`: der beim Start persistierte State trägt bereits
    skip_approval=True; ein Resume ohne Flags bewahrt den Modus none (läuft bis
    done, KEIN untergeschobenes Plan-Gate)."""
    with monkeypatch.context() as m:
        _crash_before_first_phase(m)
        crashed = _run(target_repo, "--gates", "none")
        assert crashed.exit_code == 1, crashed.output
    state = RunState.find_latest(target_repo)
    assert state.skip_approval is True  # bereits beim ersten Save gepinnt
    assert state.spec_approval is False
    resumed = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 0, resumed.output  # none bleibt none, kein Plan-Halt
    assert RunState.load(target_repo, state.run_id).phase == "done"


def test_gates_spec_pins_skip_approval_before_first_save(target_repo, monkeypatch):
    """`--gates spec`: der beim Start persistierte State trägt skip_approval=True
    UND spec_approval=True; ein Resume ohne Flags bewahrt den Modus spec (Halt
    nach der Spec, danach KEIN Plan-Gate)."""
    with monkeypatch.context() as m:
        _crash_before_first_phase(m)
        crashed = _run(target_repo, "--gates", "spec")
        assert crashed.exit_code == 1, crashed.output
    state = RunState.find_latest(target_repo)
    assert state.skip_approval is True  # bereits beim ersten Save gepinnt
    assert state.spec_approval is True
    resumed = runner.invoke(app, ["resume", state.run_id, "--repo", str(target_repo)])
    assert resumed.exit_code == 2, resumed.output
    assert RunState.load(target_repo, state.run_id).phase == "awaiting_spec_approval"
    approved = _approve(target_repo, state.run_id)
    assert approved.exit_code == 0, approved.output  # kein Plan-Gate → direkt done
    assert RunState.load(target_repo, state.run_id).phase == "done"
