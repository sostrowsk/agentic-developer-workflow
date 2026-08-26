"""RED tests for the node-time run context added to the run-detail JSON.

Derived from .adw/spec.md (AC 1–11), .adw/contract.yaml (RunContext, RoundContext,
TraceNodeContextAddition, RunDetailContextAdditions, x-behavior C1–C6) and
.adw/plan.md (B1–B5). The context is a PURELY DERIVED, read-only projection of the
event stream already loaded for the detail response: every trace node carries a
six-field ``context`` computed at its seq cutoff, and the response carries a
top-level ``latest_context`` at the greatest seq.

RED until ``_serialize``/``_run_detail`` in ``adw.gui.app`` attach ``context`` /
``latest_context``. No production code is written here.
"""

import pytest
from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    iter_nodes,
    rec,
    run_end_payload,
    run_start_payload,
    simple_run_lines,
    write_run,
    write_state_only_run,
)

# The six-field run context — never more, never fewer (contract RunContext, E3).
SIX = {"phase", "round", "limit_hits", "circuit_breakers", "cost_usd", "followups"}


def _detail(tmp_path, run_id, lines, *, phase=None):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client.get(f"/api/runs/{slug}/{run_id}")


def node_by_seq(tree, seq):
    """The single trace node whose own ``seq`` equals ``seq`` (seqs are unique)."""
    return next((n for n in iter_nodes(tree) if n.get("seq") == seq), None)


def _ctx_by_seq(tree, seq):
    node = node_by_seq(tree, seq)
    assert node is not None, seq
    return node["context"]


# --- the master fixture: one coherent run touching every context source --------
#
# seq  1 run start           (R)
#      2 phase start "spec"   (P1)              -> phase observation "spec" @2
#      3 agent.run start      (A1, in P1)
#      4 agent.run end        (A1) cost 0.10
#      5 state.saved          (in R) phase="spec" -> phase observation "spec" @5
#      6 limit.hit            (in R)            -> a limit hit BEFORE any round
#      7 phase end "spec"     (P1)
#      8 phase start "plan"   (P2)              -> phase observation "plan" @8
#      9 round start          (RD, in P2, n=2 cap=5)
#     10 followup             (in RD)
#     11 agent.run start      (A2, in RD)
#     12 agent.run end        (A2) cost 0.20
#     13 limit.hit            (in RD)
#     14 round end            (RD)
#     15 circuit_breaker      (in P2)
#     16 state.saved          (in R) phase="plan" -> phase observation "plan" @16
#     17 phase end "plan"     (P2)
#     18 run end              (R)
def _context_lines():
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Context run")),
        rec(2, "phase", "start", "P1", "R", sec=2, payload={"name": "spec", "from_phase": "spec"}),
        rec(3, "agent.run", "start", "A1", "P1", sec=3,
            payload={"agent": "spec_agent", "prompt": "p", "system_append": ""}),
        rec(4, "agent.run", "end", "A1", "P1", sec=4,
            payload={"result_text": "ok", "cost_usd": 0.10, "is_error": False}),
        rec(5, "state.saved", "point", "R", sec=5, payload={"seq": 1, "phase": "spec"}),
        rec(6, "limit.hit", "point", "R", sec=6, payload={"limit": "cost", "value": 5, "cap": 10}),
        rec(7, "phase", "end", "P1", "R", sec=7, payload={"name": "spec", "to_phase": "plan"}),
        rec(8, "phase", "start", "P2", "R", sec=8, payload={"name": "plan", "from_phase": "plan"}),
        rec(9, "round", "start", "RD", "P2", sec=9, payload={"loop": "gates", "n": 2, "cap": 5}),
        rec(10, "followup", "point", "RD", sec=10, payload={"issue": "scope gap"}),
        rec(11, "agent.run", "start", "A2", "RD", sec=11,
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        rec(12, "agent.run", "end", "A2", "RD", sec=12,
            payload={"result_text": "ok", "cost_usd": 0.20, "is_error": False}),
        rec(13, "limit.hit", "point", "RD", sec=13,
            payload={"limit": "rounds", "value": 2, "cap": 5}),
        rec(14, "round", "end", "RD", "P2", sec=14, payload={"outcome": "ok"}),
        rec(15, "circuit_breaker", "point", "P2", sec=15,
            payload={"keys": ["k1"], "scope": "gates"}),
        rec(16, "state.saved", "point", "R", sec=16, payload={"seq": 2, "phase": "plan"}),
        rec(17, "phase", "end", "P2", "R", sec=17, payload={"name": "plan", "to_phase": "build"}),
        rec(18, "run", "end", "R", None, sec=18, payload=run_end_payload("done")),
    ]


def test_every_node_and_latest_context_have_exactly_the_six_fields(home, tmp_path):  # noqa: F811
    """AC 1 / contract RunContext: every trace node carries a ``context`` and the
    response a top-level ``latest_context``, each with EXACTLY the six agreed
    fields — never more, never fewer (E3)."""
    detail = _detail(tmp_path, "aaaa1111", _context_lines()).json()

    assert set(detail["latest_context"]) == SIX
    nodes = list(iter_nodes(detail["tree"]))
    assert nodes  # the tree is non-empty
    for node in nodes:
        assert "context" in node, node.get("type")
        assert set(node["context"]) == SIX, node.get("type")


def test_point_node_cutoff_uses_only_events_up_to_its_seq(home, tmp_path):  # noqa: F811
    """AC 2: a point-event node's cutoff is its own ``seq``; only events with
    ``seq`` <= that cutoff participate. The limit.hit at seq 6 sees the first cost
    and the first limit hit, but none of the later round / cost / breaker events."""
    tree = _detail(tmp_path, "aaaa1111", _context_lines()).json()["tree"]

    ctx = _ctx_by_seq(tree, 6)
    assert ctx["phase"] == "spec"                  # highest phase obs <= 6 is @5
    assert ctx["round"] is None                    # seq 6 precedes the round (9..14)
    assert ctx["limit_hits"] == 1                  # only the seq-6 hit
    assert ctx["circuit_breakers"] is None         # the breaker (@15) is later
    assert ctx["followups"] is None                # the followup (@10) is later
    assert ctx["cost_usd"] == pytest.approx(0.10)  # only A1 (@4); A2 (@12) is later


def test_span_context_uses_end_seq_and_includes_inside_span_events(home, tmp_path):  # noqa: F811
    """AC 2: a span node's cutoff is its exposed ``end_seq`` (subtree maximum), so
    selecting the finished round includes the cost, limit.hit and followup that
    occur INSIDE the span after its start — a running/finished span is not limited
    to its start seq."""
    tree = _detail(tmp_path, "aaaa1111", _context_lines()).json()["tree"]

    rd = node_by_seq(tree, 9)  # the round span starts at seq 9
    assert rd["end_seq"] == 14  # subtree maximum (its end + inner points)
    ctx = rd["context"]
    assert ctx["followups"] == 1                   # followup @10, inside after start
    assert ctx["limit_hits"] == 2                  # @6 (before) + @13 (inside)
    assert ctx["cost_usd"] == pytest.approx(0.30)  # A1 @4 + A2 @12 (inside)
    assert ctx["round"] == {"loop": "gates", "n": 2, "cap": 5}  # its own round


def test_later_events_never_change_an_earlier_nodes_context(home, tmp_path):  # noqa: F811
    """AC 2/3 (time travel): an earlier node reflects only its own cutoff, never a
    later event; selecting a different node exposes that node's historical state."""
    detail = _detail(tmp_path, "aaaa1111", _context_lines()).json()
    tree = detail["tree"]

    early = node_by_seq(tree, 3)["context"]   # agent.run A1, cutoff end_seq 4
    late = node_by_seq(tree, 11)["context"]   # agent.run A2, cutoff end_seq 12
    latest = detail["latest_context"]

    assert early["cost_usd"] == pytest.approx(0.10)
    assert early["followups"] is None and early["circuit_breakers"] is None
    # The later node and the run's latest state carry the accumulated values —
    # proving the earlier node was not retro-actively updated.
    assert late["cost_usd"] == pytest.approx(0.30) and late["followups"] == 1
    assert latest["cost_usd"] == pytest.approx(0.30) and latest["circuit_breakers"] == 1


def test_latest_context_reflects_the_greatest_seq(home, tmp_path):  # noqa: F811
    """AC 3: ``latest_context`` is derived through the greatest observed seq — the
    live-run, no-selection view."""
    latest = _detail(tmp_path, "aaaa1111", _context_lines()).json()["latest_context"]

    assert latest["phase"] == "plan"           # state.saved @16
    assert latest["round"] is None             # the round ended at 14, before 18
    assert latest["limit_hits"] == 2
    assert latest["circuit_breakers"] == 1
    assert latest["followups"] == 1
    assert latest["cost_usd"] == pytest.approx(0.30)


# --- phase precedence (C3) -----------------------------------------------------


def _phase_precedence_lines():
    """A state.saved phase="spec" (seq 2) then a phase-span start name="plan"
    (seq 4): a cutoff between them sees "spec", a cutoff at/after 4 sees "plan"
    (the spec's seq-30/seq-40 example, condensed)."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Phase precedence")),
        rec(2, "state.saved", "point", "R", sec=2, payload={"seq": 1, "phase": "spec"}),
        rec(3, "note.item", "point", "R", sec=3, payload={"note": "between"}),
        rec(4, "phase", "start", "P", "R", sec=4, payload={"name": "plan", "from_phase": "plan"}),
        rec(5, "note.item", "point", "R", sec=5, payload={"note": "after"}),
        rec(6, "run", "end", "R", None, sec=6, payload=run_end_payload("done")),
    ]


def test_phase_precedence_between_state_saved_and_phase_span_by_seq(home, tmp_path):  # noqa: F811
    """AC 4 / C3: both a ``phase`` span's start ``name`` and a ``state.saved``
    ``phase`` are valid observations; the one with the greatest seq at/before the
    cutoff wins."""
    tree = _detail(tmp_path, "bbbb2222", _phase_precedence_lines()).json()["tree"]

    assert _ctx_by_seq(tree, 3)["phase"] == "spec"  # only state.saved @2 seen
    assert _ctx_by_seq(tree, 5)["phase"] == "plan"  # phase-span @4 now dominates


def _phase_empty_lines():
    """A phase span with an EMPTY name and a state.saved with an EMPTY phase —
    neither counts as an observation."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Phase empty")),
        rec(2, "phase", "start", "P", "R", sec=2, payload={"name": "", "from_phase": ""}),
        rec(3, "state.saved", "point", "R", sec=3, payload={"seq": 1, "phase": ""}),
        rec(4, "note.item", "point", "R", sec=4, payload={"note": "x"}),
        rec(5, "run", "end", "R", None, sec=5, payload=run_end_payload("done")),
    ]


def test_phase_null_when_no_valid_observation(home, tmp_path):  # noqa: F811
    """AC 4 / C3: empty/absent phase values are not observations, so ``phase`` is
    null (not an empty-string artefact)."""
    detail = _detail(tmp_path, "cccc3333", _phase_empty_lines()).json()

    assert _ctx_by_seq(detail["tree"], 4)["phase"] is None
    assert detail["latest_context"]["phase"] is None


# --- rounds (C4) ---------------------------------------------------------------


def test_round_enclosing_own_and_null_outside(home, tmp_path):  # noqa: F811
    """AC 5 / C4: a node inside a round (or the round node itself) carries the
    nearest enclosing round's ``{loop, n, cap}``; a node outside every round has
    ``round`` null."""
    tree = _detail(tmp_path, "aaaa1111", _context_lines()).json()["tree"]

    inside = _ctx_by_seq(tree, 10)["round"]      # followup inside the round
    assert inside == {"loop": "gates", "n": 2, "cap": 5}
    enclosing = _ctx_by_seq(tree, 11)["round"]   # agent.run A2 nested in the round
    assert enclosing == {"loop": "gates", "n": 2, "cap": 5}
    # The plan phase span's cutoff (17) lies AFTER the round ended (14): outside.
    assert _ctx_by_seq(tree, 8)["round"] is None
    assert _ctx_by_seq(tree, 6)["round"] is None  # before the round started


def _round_missing_lines():
    """A round whose start payload omits ``cap`` — the missing value stays null,
    never filled in or estimated."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Round missing")),
        rec(2, "round", "start", "RD", "R", sec=2, payload={"loop": "gates", "n": 2}),
        rec(3, "note.item", "point", "RD", sec=3, payload={"note": "in round"}),
        rec(4, "round", "end", "RD", "R", sec=4, payload={"outcome": "ok"}),
        rec(5, "run", "end", "R", None, sec=5, payload=run_end_payload("done")),
    ]


def test_round_missing_individual_values_stay_null(home, tmp_path):  # noqa: F811
    """AC 5 / C4 missing_values: an absent round field is null, not inferred."""
    tree = _detail(tmp_path, "dddd4444", _round_missing_lines()).json()["tree"]

    rnd = _ctx_by_seq(tree, 3)["round"]
    assert rnd == {"loop": "gates", "n": 2, "cap": None}


def _running_round_lines():
    """A still-running run whose greatest-seq event lies INSIDE an open round."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Running round")),
        rec(2, "phase", "start", "P", "R", sec=2, payload={"name": "build", "from_phase": "build"}),
        rec(3, "round", "start", "RD", "P", sec=3, payload={"loop": "fix", "n": 1, "cap": 3}),
        rec(4, "limit.hit", "point", "RD", sec=4, payload={"limit": "x", "value": 1, "cap": 3}),
    ]


def test_latest_context_round_inside_a_running_round(home, tmp_path):  # noqa: F811
    """AC 5 / C4 latest_context: with the greatest-seq event inside an open round,
    ``latest_context.round`` is that round; contrast the master fixture, where the
    round ended before the last event and ``latest_context.round`` is null."""
    latest = _detail(tmp_path, "eeee5555", _running_round_lines()).json()["latest_context"]

    assert latest["round"] == {"loop": "fix", "n": 1, "cap": 3}
    assert latest["phase"] == "build"
    assert latest["limit_hits"] == 1


def _open_round_nested_span_lines():
    """An OPEN round whose newest events belong to a NESTED agent/tool span, not to
    the round's own span id. The round span itself carries only its start event, so
    its own seq range ends at that start — the containment must use the round's FULL
    subtree range (through the nested span) or the nested contexts and
    ``latest_context.round`` wrongly collapse to null."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Open nested round")),
        rec(2, "phase", "start", "P", "R", sec=2, payload={"name": "build", "from_phase": "build"}),
        rec(3, "round", "start", "RD", "P", sec=3, payload={"loop": "gates", "n": 1, "cap": 3}),
        rec(4, "agent.run", "start", "A", "RD", sec=4,
            payload={"agent": "build_agent", "prompt": "p", "system_append": ""}),
        # The newest event carries only the CHILD span id (A), never RD.
        rec(5, "agent.tool.call", "point", "A", sec=5,
            payload={"tool": "Bash", "tool_use_id": "t1", "input": {"command": "go"}}),
    ]


def test_open_round_containing_a_nested_active_span(home, tmp_path):  # noqa: F811
    """AC 5 / C4 (regression): a round is recognised for a cutoff inside its SUBTREE,
    even when the round's own span carries only its start event and the newest
    events belong to a nested span. Both the nested node contexts and
    ``latest_context.round`` reflect the enclosing round."""
    detail = _detail(tmp_path, "0d0d0d0d", _open_round_nested_span_lines()).json()
    expected = {"loop": "gates", "n": 1, "cap": 3}

    assert detail["latest_context"]["round"] == expected     # newest event is nested
    assert _ctx_by_seq(detail["tree"], 5)["round"] == expected  # the nested tool point
    assert _ctx_by_seq(detail["tree"], 4)["round"] == expected  # the nested agent.run span


# --- counts & cost (C5): null, never a fabricated 0 ----------------------------


def test_counts_and_cost_are_null_when_the_event_type_never_occurs(home, tmp_path):  # noqa: F811
    """AC 6/7/8 / C5: a run without any limit.hit / circuit_breaker / followup /
    cost carries null for those fields — never a fabricated 0 (E4)."""
    latest = _detail(tmp_path, "ffff6666",
                     simple_run_lines("No metrics run")).json()["latest_context"]

    assert latest["phase"] == "build"      # the build phase span is still observed
    assert latest["limit_hits"] is None
    assert latest["circuit_breakers"] is None
    assert latest["followups"] is None
    assert latest["cost_usd"] is None


def _malformed_lines():
    """Incomplete/invalid payloads: an agent.run end whose ``cost_usd`` is not a
    number, and a round whose start payload is empty. The affected field stays
    null; unrelated fields (a valid limit.hit) still count; no HTTP 5xx."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Malformed")),
        rec(2, "agent.run", "start", "A", "R", sec=2,
            payload={"agent": "a", "prompt": "p", "system_append": ""}),
        rec(3, "agent.run", "end", "A", "R", sec=3,
            payload={"result_text": "x", "cost_usd": "not-a-number", "is_error": False}),
        rec(4, "limit.hit", "point", "R", sec=4, payload={"limit": "x", "value": 1, "cap": 2}),
        rec(5, "round", "start", "RD", "R", sec=5, payload={}),
        rec(6, "note.item", "point", "RD", sec=6, payload={"note": "in round"}),
        rec(7, "round", "end", "RD", "R", sec=7, payload={"outcome": "ok"}),
        rec(8, "run", "end", "R", None, sec=8, payload=run_end_payload("done")),
    ]


def test_malformed_or_incomplete_payloads_yield_null_not_error(home, tmp_path):  # noqa: F811
    """AC 10 / C6: invalid values are not invented as data and never raise a 5xx —
    only the affected fields stay null."""
    resp = _detail(tmp_path, "abcd1234", _malformed_lines())
    assert resp.status_code == 200
    detail = resp.json()

    ctx = _ctx_by_seq(detail["tree"], 6)
    assert ctx["round"] == {"loop": None, "n": None, "cap": None}  # empty payload
    assert ctx["cost_usd"] is None       # the invalid cost is not counted
    assert ctx["limit_hits"] == 1        # the valid limit.hit still counts
    assert detail["latest_context"]["cost_usd"] is None


def _non_dict_agentrun_payload_lines():
    """A completed ``agent.run`` whose end payload is NOT a mapping (a list here) —
    the kind a crafted or corrupt log can carry. The cost read must treat it as no
    cost, not call ``.get`` on it (which would 5xx). The agent.run sits under a
    ``phase`` so the aggregate ``_subtree_cost`` path is exercised as well."""
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Non-dict payload")),
        rec(2, "phase", "start", "P", "R", sec=2, payload={"name": "build", "from_phase": "build"}),
        rec(3, "agent.run", "start", "A", "P", sec=3,
            payload={"agent": "a", "prompt": "p", "system_append": ""}),
        rec(4, "agent.run", "end", "A", "P", sec=4, payload=["not", "a", "dict"]),
        rec(5, "phase", "end", "P", "R", sec=5, payload={"name": "build", "to_phase": "done"}),
        rec(6, "run", "end", "R", None, sec=6, payload=run_end_payload("done")),
    ]


def test_non_dict_agent_run_payload_does_not_5xx(home, tmp_path):  # noqa: F811
    """AC 10 / C6: a non-mapping ``agent.run`` end payload leaves ``cost_usd`` null
    and never raises a 5xx (defensive malformed-payload requirement)."""
    resp = _detail(tmp_path, "12ab34cd", _non_dict_agentrun_payload_lines())
    assert resp.status_code == 200
    detail = resp.json()

    assert detail["latest_context"]["cost_usd"] is None
    assert node_by_seq(detail["tree"], 3)["context"]["cost_usd"] is None  # the agent.run span


def _precise_cost_lines(cost):
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Precise cost")),
        rec(2, "agent.run", "start", "A", "R", sec=2,
            payload={"agent": "a", "prompt": "p", "system_append": ""}),
        rec(3, "agent.run", "end", "A", "R", sec=3,
            payload={"result_text": "ok", "cost_usd": cost, "is_error": False}),
        rec(4, "run", "end", "R", None, sec=4, payload=run_end_payload("done")),
    ]


def test_api_cost_usd_is_exact_not_rounded(home, tmp_path):  # noqa: F811
    """AC 7 / E4: the JSON API returns the EXACT cumulative cost (the shared
    ``_events_cost`` semantics); rounding is presentation-only and must not alter the
    API value."""
    cost = 5.795072500000001  # more than six decimals: display rounding would change it
    detail = _detail(tmp_path, "cafe0001", _precise_cost_lines(cost)).json()

    assert detail["latest_context"]["cost_usd"] == cost           # exact, unrounded
    assert node_by_seq(detail["tree"], 2)["context"]["cost_usd"] == cost
    assert detail["latest_context"]["cost_usd"] != round(cost, 6)  # rounding would differ


def test_run_without_a_trace_has_only_latest_context_all_null(home, tmp_path):  # noqa: F811
    """AC 10 / C6: a run without a trace has no trace nodes, no synthetic node and
    NO top-level ``context`` field — only ``latest_context`` with all six fields
    null, and never a 5xx."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_state_only_run(repo, "beef0001")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]

    resp = client.get(f"/api/runs/{slug}/beef0001")
    assert resp.status_code == 200
    detail = resp.json()

    assert detail["tree"] == []
    assert "context" not in detail                    # no synthetic top-level context
    assert set(detail["latest_context"]) == SIX
    assert all(detail["latest_context"][k] is None for k in SIX)
