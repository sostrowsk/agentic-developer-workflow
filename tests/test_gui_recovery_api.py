"""RED tests for the recovery-card JSON surface — the additive, derived
``recovery`` object on ``GET /api/runs/{repo}/{run_id}``.

Derived from .adw/spec.md (AC 1-9), .adw/contract.yaml
(RunDetailRecoveryAddition / Recovery oneOf, x-behavior P1-P9) and .adw/plan.md
(B1-B5). The recovery object is a pure projection of the already-loaded state
(``state.phase``), the existing run-status derivation, the event stream and the
server-resolved ``RepoRef.path``: it is PRESENT exactly when the run needs human
intervention and ABSENT otherwise (never an empty object).

RED until ``_run_detail`` hangs the derived ``recovery`` object on its result.
"""

import copy
import json
import os
import shlex

import pytest
from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    escalated_lines,
    home,
    rec,
    run_end_payload,
    run_start_payload,
    simple_run_lines,
    write_run,
    write_state_only_run,
)

RUN_ID = "aaaa1111"


def _real_path(repo):
    return os.path.normpath(str(repo.resolve()))


def _client(tmp_path, *, lines=None, phase=None, state_only=False, run_id=RUN_ID,
            repo_name="repo"):
    """Build the app over one repo and return (client, slug, real_path, run_id)."""
    repo = tmp_path / repo_name
    repo.mkdir(parents=True, exist_ok=True)
    if state_only:
        write_state_only_run(repo, run_id, phase=phase)
    else:
        write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    return client, _slug(_real_path(repo)), _real_path(repo), run_id


def _detail(client, slug, run_id):
    r = client.get(f"/api/runs/{slug}/{run_id}")
    assert r.status_code == 200, r.status_code
    return r.json()


# --- AC 1/2/5/6/9: escalated run -> kind `none` --------------------------------


def escalation_history_lines():
    """One run log carrying TWO escalations (as several ``run`` spans appended to
    one file produce). The GOVERNING escalation is the one with the greatest
    ``seq`` (seq 7). Its aborts are the ``limit.hit``/``circuit_breaker`` events
    between the PRIOR escalation (seq 4) and it (seqs 5, 6) — the earlier
    circuit_breaker (seq 3) belongs to the prior escalation and must NOT appear."""
    return [
        rec(1, "run", "start", "R", None, sec=0,
            payload=run_start_payload("Escalation history")),
        rec(2, "phase", "start", "B", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "circuit_breaker", "point", "B", sec=2,
            payload={"keys": ["old|1|x"], "scope": "lane:backend"}),   # PRIOR's abort
        rec(4, "escalation", "point", "B", sec=3,
            payload={"reason": "old reason", "phase": "spec"}),         # PRIOR escalation
        rec(5, "limit.hit", "point", "B", sec=4,
            payload={"limit": "gate_iterations", "value": 10, "cap": 10}),
        rec(6, "circuit_breaker", "point", "B", sec=5,
            payload={"keys": ["new|2|y"], "scope": "lane:backend"}),
        rec(7, "escalation", "point", "B", sec=6,
            payload={"reason": "new reason", "phase": "build"}),        # GOVERNING
        rec(8, "phase", "end", "B", "R", sec=7,
            payload={"name": "build", "to_phase": None}),
        rec(9, "run", "end", "R", None, sec=8, payload=run_end_payload("escalated")),
    ]


def test_escalated_run_yields_kind_none(home, tmp_path):  # noqa: F811
    """AC 1/2/5/9: ``state.phase == escalated`` -> kind ``none``,
    ``needs_new_run`` true, no continuation command; reason/phase come verbatim
    from the escalation event; the artifact reference is ``escalation.md``. Never
    ``resume``/``approve`` for an escalated run."""
    client, slug, _path, run_id = _client(tmp_path, lines=escalated_lines(),
                                           phase="escalated")
    detail = _detail(client, slug, run_id)

    assert "recovery" in detail
    rc = detail["recovery"]
    assert rc["kind"] == "none"
    assert rc["needs_new_run"] is True
    assert rc["command"] is None
    # reason/phase verbatim from the escalation event (phase is the ORIGIN phase).
    assert rc["reason"] == "gate hopeless"
    assert rc["phase"] == "build"
    assert rc["aborts"] == []                       # no abort events in this run
    assert rc["escalation_artifact"] == "escalation.md"
    assert rc["anchor_seq"] == 3                     # the escalation event's seq


def test_escalation_context_governing_event_reason_phase_and_aborts(home, tmp_path):  # noqa: F811
    """AC 5/6: reason/phase are taken from the escalation event with the GREATEST
    seq; ``aborts`` lists exactly the limit.hit/circuit_breaker events between the
    prior escalation and the governing one, in event order, payloads verbatim."""
    client, slug, _path, run_id = _client(tmp_path, lines=escalation_history_lines(),
                                           phase="escalated")
    rc = _detail(client, slug, run_id)["recovery"]

    assert rc["kind"] == "none"
    assert rc["anchor_seq"] == 7
    assert rc["reason"] == "new reason"
    assert rc["phase"] == "build"                    # origin phase of the governing event
    assert [a["type"] for a in rc["aborts"]] == ["limit.hit", "circuit_breaker"]
    assert [a["seq"] for a in rc["aborts"]] == [5, 6]
    assert rc["aborts"][0]["payload"] == {"limit": "gate_iterations", "value": 10, "cap": 10}
    assert rc["aborts"][1]["payload"] == {"keys": ["new|2|y"], "scope": "lane:backend"}
    # The prior escalation's abort (seq 3) is NOT attributed to this escalation.
    assert all(a["seq"] != 3 for a in rc["aborts"])


# --- AC 1/2/3: approval-gate pause -> kind `approve` ---------------------------


@pytest.mark.parametrize("phase", ["awaiting_approval", "awaiting_spec_approval"])
def test_approval_pause_yields_kind_approve(home, tmp_path, phase):  # noqa: F811
    """AC 1/2/3: ``state.phase`` at an approval gate -> kind ``approve`` with the
    finished ``adw approve`` command; ``needs_new_run`` false; no escalation
    context fields (approve carries only the command)."""
    client, slug, path, run_id = _client(tmp_path, state_only=True, phase=phase,
                                          repo_name=phase)
    rc = _detail(client, slug, run_id)["recovery"]

    assert rc["kind"] == "approve"
    assert rc["needs_new_run"] is False
    assert rc["command"] == f"adw approve {run_id} --repo {shlex.quote(path)}"
    for f in ("anchor_seq", "reason", "phase", "aborts", "escalation_artifact"):
        assert f not in rc


# --- AC 1/2/3: crash/abort in a work phase -> kind `resume` --------------------


def test_work_phase_not_running_yields_kind_resume(home, tmp_path):  # noqa: F811
    """AC 1/3: a work phase (``build``) whose derived run-status is not ``running``
    (here a state-only run: no span -> status None) -> kind ``resume`` with the
    ``adw resume`` command; ``needs_new_run`` false."""
    client, slug, path, run_id = _client(tmp_path, state_only=True, phase="build")
    rc = _detail(client, slug, run_id)["recovery"]

    assert rc["kind"] == "resume"
    assert rc["needs_new_run"] is False
    assert rc["command"] == f"adw resume {run_id} --repo {shlex.quote(path)}"


def test_done_and_running_have_no_recovery(home, tmp_path):  # noqa: F811
    """AC 1: ``state.phase == done`` and a work phase with derived status
    ``running`` carry NO recovery object — the key is absent, not an empty
    object."""
    client, slug, _p, run_id = _client(
        tmp_path, lines=simple_run_lines("done run"), phase="done", repo_name="done")
    assert "recovery" not in _detail(client, slug, run_id)

    # An OPEN run span in a work phase is `running` -> no recovery.
    client2, slug2, _p2, run_id2 = _client(
        tmp_path, lines=simple_run_lines("live", ended=False), phase="build",
        repo_name="running")
    assert "recovery" not in _detail(client2, slug2, run_id2)


# --- AC 3/4: command uses the real registry path, shell-safe -------------------


def test_command_uses_real_registry_path_not_the_slug(home, tmp_path):  # noqa: F811
    """AC 3: the command carries the real, server-resolved registry path
    (``RepoRef.path``) and the real run_id — never the slug."""
    client, slug, path, run_id = _client(tmp_path, state_only=True, phase="build")
    rc = _detail(client, slug, run_id)["recovery"]

    assert rc["command"] == f"adw resume {run_id} --repo {shlex.quote(path)}"
    assert path in rc["command"]
    assert slug != path                              # the slug is not the real path


@pytest.mark.parametrize("repo_name", [
    "weird dir",            # a space
    "quote'dir",            # an embedded single quote
    "meta$(id)&dir",        # shell metacharacters
])
def test_command_is_posix_shell_safe(home, tmp_path, repo_name):  # noqa: F811
    """AC 4: run_id and path are rendered per ``shlex.quote`` semantics — parsed by
    a POSIX shell the text yields EXACTLY the intended argv (the path stays ONE
    ``--repo`` argument) and produces no extra command."""
    client, slug, path, run_id = _client(tmp_path, state_only=True, phase="build",
                                          repo_name=repo_name)
    command = _detail(client, slug, run_id)["recovery"]["command"]

    assert shlex.split(command) == ["adw", "resume", run_id, "--repo", path]
    assert command.count("--repo") == 1


# --- AC 7: robustness -----------------------------------------------------------


def test_escalated_without_event_log_stays_usable(home, tmp_path):  # noqa: F811
    """AC 7: an escalated run with NO event log yields no 5xx and no invented
    values — ``kind``/``needs_new_run`` stay visible while the escalation context
    is null/empty (the card falls back to run level, never hidden)."""
    client, slug, _p, run_id = _client(tmp_path, state_only=True, phase="escalated")
    detail = _detail(client, slug, run_id)                    # 200, not 5xx

    rc = detail["recovery"]
    assert rc["kind"] == "none"
    assert rc["needs_new_run"] is True
    assert rc["command"] is None
    assert rc["anchor_seq"] is None
    assert rc["reason"] is None
    assert rc["phase"] is None
    assert rc["aborts"] == []


@pytest.mark.parametrize("bad_payload", ["a bare string", ["reason", "in", "a", "list"], 42])
def test_non_mapping_escalation_payload_does_not_500(home, tmp_path, bad_payload):  # noqa: F811
    """AC 7: a truthy but non-mapping escalation payload (a crafted/corrupt string,
    list or number) yields no 5xx — reason/phase are null, never accessed via
    ``.get`` on a non-mapping, and the run still escalates (kind ``none``)."""
    esc = rec(3, "escalation", "point", "B", sec=2, payload={})
    esc["payload"] = bad_payload                         # override the {} default
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Bad payload")),
        rec(2, "phase", "start", "B", "R", sec=1, payload={"name": "build", "from_phase": "build"}),
        esc,
        rec(4, "run", "end", "R", None, sec=3, payload=run_end_payload("escalated")),
    ]
    client, slug, _p, run_id = _client(tmp_path, lines=lines, phase="escalated")
    rc = _detail(client, slug, run_id)["recovery"]       # 200, not 500

    assert rc["kind"] == "none"
    assert rc["reason"] is None and rc["phase"] is None
    assert rc["anchor_seq"] == 3


def test_abort_payloads_are_carried_verbatim(home, tmp_path):  # noqa: F811
    """AC 6/7 (P7_robust): abort payloads are carried VERBATIM with no truthiness
    coercion — a present null/empty-list/populated payload survives unchanged. A
    genuinely ABSENT payload is represented as ``null`` (no value), NEVER a
    fabricated empty object."""
    null_ab = rec(3, "limit.hit", "point", "B", sec=2, payload={})
    null_ab["payload"] = None                            # present null
    list_ab = rec(4, "circuit_breaker", "point", "B", sec=3, payload={})
    list_ab["payload"] = []                              # present empty list
    dict_ab = rec(5, "limit.hit", "point", "B", sec=4,
                  payload={"limit": "fix_cycles", "value": 3, "cap": 3})
    missing_ab = rec(6, "circuit_breaker", "point", "B", sec=5, payload={})
    del missing_ab["payload"]                            # genuinely absent
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Atypical aborts")),
        rec(2, "phase", "start", "B", "R", sec=1, payload={"name": "build", "from_phase": "build"}),
        null_ab, list_ab, dict_ab, missing_ab,
        rec(7, "escalation", "point", "B", sec=6, payload={"reason": "r", "phase": "build"}),
        rec(8, "run", "end", "R", None, sec=7, payload=run_end_payload("escalated")),
    ]
    client, slug, _p, run_id = _client(tmp_path, lines=lines, phase="escalated")
    rc = _detail(client, slug, run_id)["recovery"]

    # The absent payload (last) is `null`, not `{}`; present values are verbatim.
    assert [a["payload"] for a in rc["aborts"]] == [
        None, [], {"limit": "fix_cycles", "value": 3, "cap": 3}, None,
    ]


def test_escalation_with_missing_payload_fields_is_robust(home, tmp_path):  # noqa: F811
    """AC 7: an escalation event whose payload lacks ``reason``/``phase`` yields
    ``null`` for those, never a substitute — but the run still escalates
    (``kind`` none) and stays anchored at the event."""
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Broken payload")),
        rec(2, "phase", "start", "B", "R", sec=1, payload={"name": "build", "from_phase": "build"}),
        rec(3, "escalation", "point", "B", sec=2, payload={}),   # no reason/phase
        rec(4, "run", "end", "R", None, sec=3, payload=run_end_payload("escalated")),
    ]
    client, slug, _p, run_id = _client(tmp_path, lines=lines, phase="escalated")
    rc = _detail(client, slug, run_id)["recovery"]

    assert rc["kind"] == "none"
    assert rc["reason"] is None
    assert rc["phase"] is None
    assert rc["anchor_seq"] == 3


# --- AC 8: the real repo path never leaks into a URL ---------------------------


def test_real_path_appears_only_in_command_never_in_a_url(home, tmp_path):  # noqa: F811
    """AC 8: the real repo path appears ONLY in ``recovery.command``. The detail is
    addressed by slug; blanking the command removes every trace of the path from
    the response body."""
    client, slug, path, run_id = _client(tmp_path, state_only=True, phase="build")
    detail = _detail(client, slug, run_id)

    assert path in detail["recovery"]["command"]
    # The addressing slug is not the real path (§7.4).
    assert path not in f"/api/runs/{slug}/{run_id}"
    # With the command blanked, the path is nowhere else in the response.
    blanked = copy.deepcopy(detail)
    blanked["recovery"]["command"] = None
    assert path not in json.dumps(blanked)
