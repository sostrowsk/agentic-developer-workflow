"""RED unit tests for the trace-tree compaction layer (Trace-Baum verdichten).

The feature folds tool RESULTS into their call (A1), collapses repeated (A2) and
groups adjacent (A3) Read/Grep/Glob operations into collapsible synthetic nodes,
and shows a per-page line balance (A6); the default-open phase is a server-side
decision (A5). Per .adw/contract.yaml the CONCRETE markup is NOT pinned and
"interne Helper-Signaturen ... [sind] NICHT Contract"; the compaction is, per
.adw/plan.md §B1/§B3, a PURE server function over the already-windowed pre-order
row list (``tree_window["rows"]``, a list of ``{"node": <serialized>, "depth":
int}``), plus a server helper that names the default-open phase (§B5).

These tests define that seam (the RED contract of this lane):

* ``_compact_rows(rows)`` → ``{"entries": [...], "rows": int, "folded": int}``
  where each *entry* is a dict discriminated by ``kind``:
    - ``{"kind": "node", "node": <serialized node>, "depth": int,
       "result": <serialized result|None>, "outcome": "ok"|"error"|None,
       "duration": <float|None>}`` — an original node, optionally carrying its
       A1-folded result (outcome + determinable duration);
    - ``{"kind": "repeat", "depth": int, "count": int, "duration": <float|None>,
       "children": [node-entries]}`` — an A2 repetition (``count`` == calls);
    - ``{"kind": "group", "depth": int, "count": int, "ops": [str, ...],
       "children": [entries]}`` — an A3 group (``count`` == contained calls,
       incl. those inside a nested repeat; ``ops`` == the operation kinds present).
* ``_default_open_phase(tree)`` → the ``seq`` of the phase opened by default
  (the phase with the tree-order-first determinable error, else the last-started
  phase), or ``None``.

The serialized node shape mirrors ``_serialize`` (point events carry ``type``,
``seq``, ``ts``, ``label``, ``payload``, ``children``); rows are built directly
here so adjacency, depth and the loaded-page boundary are under test control
(E3/E4). RED until the seam exists.
"""

from adw.gui.app import (
    _compact_rows,
    _default_open_phase,
    _display_label,
    _flatten_tree,
    _repo_relative,
    _serialize,
    _tool_names_by_use_id,
    _tree_rows,
)
from adw.gui.model import build_tree
from tests.gui_app_helpers import rec, run_start_payload


def ts(sec: int) -> str:
    return f"2026-08-05T14:00:{sec:02d}.000Z"


# --- direct row builders (a serialized point/span node + its window depth) ------


def _call(seq, tool, uid, target, *, sec=None):
    if tool in ("Read", "Write", "Edit"):
        inp = {"file_path": target}
    elif tool in ("Grep", "Glob"):
        inp = {"pattern": target}
    else:
        inp = {"command": target}
    return {
        "type": "agent.tool.call", "seq": seq, "ts": ts(sec) if sec is not None else None,
        "label": f"{tool} {target}",
        "payload": {"tool": tool, "tool_use_id": uid, "input": inp}, "children": [],
    }


def _result(seq, uid, *, sec=None, is_error=None, exit_code=None):
    payload = {"tool_use_id": uid}
    if is_error is not None:
        payload["is_error"] = is_error
    if exit_code is not None:
        payload["exit_code"] = exit_code
    # The label mirrors _tool_result_label: an outcome only when a valid signal is
    # present, otherwise the type-name fallback (undetermined).
    if isinstance(is_error, bool):
        label = "error" if is_error else "ok"
        if exit_code is not None:
            label = f"{label} (exit {exit_code})"
    elif isinstance(exit_code, int):
        label = f"{'error' if exit_code != 0 else 'ok'} (exit {exit_code})"
    else:
        label = "agent.tool.result"
    return {
        "type": "agent.tool.result", "seq": seq, "ts": ts(sec) if sec is not None else None,
        "label": label, "payload": payload, "children": [],
    }


def _msg(seq):
    return {"type": "agent.message", "seq": seq, "ts": None, "label": "message",
            "payload": {"role": "assistant", "text": "hi"}, "children": []}


def _span(seq, node_type, label):
    return {"type": node_type, "seq": seq, "label": label, "children": [],
            "start_ts": None, "end_ts": None}


def _rows(*pairs):
    """``pairs`` are ``node`` (depth 2) or ``(node, depth)`` tuples."""
    out = []
    for p in pairs:
        if isinstance(p, tuple):
            node, depth = p
        else:
            node, depth = p, 2
        out.append({"node": node, "depth": depth})
    return out


def _entries(result):
    return result["entries"]


def _node_seqs(entries):
    """Every original-node seq reachable in the compacted structure (top-level and
    nested inside collectors), NOT counting A1-folded attached results."""
    seqs = []
    for e in entries:
        if e["kind"] == "node":
            seqs.append(e["node"]["seq"])
        else:
            seqs.extend(_node_seqs(e["children"]))
    return seqs


# --- A1: fold tool results into the immediately preceding call ------------------


def test_a1_folds_result_into_call_and_keeps_orphans_and_non_adjacent():
    # A read call + its result (same tool_use_id, adjacent) fold into one node.
    # A result whose tool_use_id matches NO preceding call stays its own node.
    # A result whose immediate predecessor is a DIFFERENT call (even if an earlier
    # call shares the id) is NOT folded — only the unmittelbar vorangehende call.
    rows = _rows(
        _call(1, "Read", "a", "/x/a.py", sec=1),
        _result(2, "a", sec=2, is_error=False),          # folds into seq 1
        _result(3, "orphan", sec=3, is_error=True),      # no matching call -> own node
        _call(4, "Read", "b", "/x/b.py", sec=4),
        _call(5, "Read", "c", "/x/c.py", sec=5),
        _result(6, "b", sec=6, is_error=False),          # predecessor is call 5 (id c) -> own
    )
    out = _compact_rows(rows)
    ents = _entries(out)

    # The folded result (seq 2) is no longer a node of its own anywhere.
    assert 2 not in _node_seqs(ents)
    call1 = next(e for e in ents if e["kind"] == "node" and e["node"]["seq"] == 1)
    assert call1["result"] is not None and call1["result"]["seq"] == 2
    assert call1["outcome"] == "ok"

    # The orphan result and the non-adjacent result remain standalone nodes with no
    # attached result of their own.
    orphan = next(e for e in ents if e["kind"] == "node" and e["node"]["seq"] == 3)
    assert orphan["node"]["type"] == "agent.tool.result" and orphan["result"] is None
    nonadj = next(e for e in ents if e["kind"] == "node" and e["node"]["seq"] == 6)
    assert nonadj["node"]["type"] == "agent.tool.result" and nonadj["result"] is None


def test_a1_undetermined_outcome_is_never_success_and_duration_needs_valid_ts():
    # (a) A result without is_error/exit_code has an UNDETERMINED outcome — never ok.
    undetermined = _compact_rows(_rows(
        _call(1, "Read", "a", "/x/a.py", sec=1),
        _result(2, "a", sec=4),  # no outcome signal
    ))["entries"][0]
    assert undetermined["outcome"] is None
    # ts parseable, diff >= 0 -> a duration is present (3 s).
    assert undetermined["duration"] == 3.0

    # (b) result timestamp BEFORE the call -> negative diff -> no duration.
    neg = _compact_rows(_rows(
        _call(1, "Read", "a", "/x/a.py", sec=8),
        _result(2, "a", sec=5, is_error=False),
    ))["entries"][0]
    assert neg["outcome"] == "ok"
    assert neg["duration"] is None

    # (c) an unparseable/absent timestamp -> no duration (no substitute value).
    missing = _compact_rows(_rows(
        _call(1, "Read", "a", "/x/a.py"),        # ts None
        _result(2, "a", is_error=False),         # ts None
    ))["entries"][0]
    assert missing["duration"] is None


def test_a1_mixed_timezone_awareness_folds_without_duration_and_never_crashes():
    # One naive ISO timestamp and one tz-aware (Z) one: subtracting them raises
    # TypeError in datetime, which must be treated as an UNDETERMINED duration —
    # the fold still succeeds (page must not 500).
    call = _call(1, "Read", "a", "/x/a.py")
    call["ts"] = "2026-08-05T14:00:00"          # timezone-naive
    result = _result(2, "a", is_error=False)
    result["ts"] = "2026-08-05T14:00:05.000Z"   # timezone-aware
    entry = _compact_rows(_rows(call, result))["entries"][0]
    assert entry["result"] is not None and entry["result"]["seq"] == 2
    assert entry["outcome"] == "ok"
    assert entry["duration"] is None


# --- A2: count immediate repetitions of the same target -------------------------


def test_a2_repeats_only_target_identical_neighbours_and_sums_duration():
    # Two Read a in a row fold into a repeat (count 2, summed determinable duration);
    # a following Read b (different target) is a SEPARATE node, never counted in.
    # A message between keeps the trailing Read b from forming its own collector.
    rows = _rows(
        _call(1, "Read", "u1", "/x/a.py", sec=1),
        _result(2, "u1", sec=4, is_error=False),   # dur 3
        _call(3, "Read", "u2", "/x/a.py", sec=5),
        _result(4, "u2", sec=7, is_error=False),   # dur 2
        _msg(5),
        _call(6, "Read", "u3", "/x/b.py", sec=8),
    )
    ents = _compact_rows(rows)["entries"]

    assert [e["kind"] for e in ents] == ["repeat", "node", "node"]
    repeat = ents[0]
    assert repeat["count"] == 2
    assert repeat["duration"] == 5.0
    assert [c["node"]["seq"] for c in repeat["children"]] == [1, 3]
    assert ents[1]["node"]["type"] == "agent.message"
    assert ents[2]["node"]["payload"]["input"]["file_path"] == "/x/b.py"


def test_a2_determinate_error_breaks_the_repetition():
    # Read X (ok), Read X (error), Read X (ok) -> three own nodes: the error call is
    # never taken into a collector, and no repeat forms on either side (spec A2 case).
    rows = _rows(
        _call(1, "Read", "u1", "/x/x.py", sec=1),
        _result(2, "u1", sec=2, is_error=False),
        _call(3, "Read", "u2", "/x/x.py", sec=3),
        _result(4, "u2", sec=4, is_error=True),
        _call(5, "Read", "u3", "/x/x.py", sec=5),
        _result(6, "u3", sec=6, is_error=False),
    )
    ents = _compact_rows(rows)["entries"]
    assert [e["kind"] for e in ents] == ["node", "node", "node"]
    assert [e["node"]["seq"] for e in ents] == [1, 3, 5]
    assert ents[1]["outcome"] == "error"


# --- A3: group uninterrupted Read/Grep/Glob runs --------------------------------


def test_a3_group_breaks_at_message():
    rows = _rows(
        _call(1, "Read", "u1", "/x/a.py"),
        _call(2, "Grep", "u2", "needle"),
        _msg(3),
        _call(4, "Read", "u3", "/x/c.py"),
        _call(5, "Read", "u4", "/x/d.py"),
    )
    ents = _compact_rows(rows)["entries"]
    assert [e["kind"] for e in ents] == ["group", "node", "group"]
    assert ents[0]["count"] == 2 and set(ents[0]["ops"]) == {"Read", "Grep"}
    assert ents[1]["node"]["type"] == "agent.message"
    assert ents[2]["count"] == 2 and set(ents[2]["ops"]) == {"Read"}


def test_a3_group_breaks_at_write_operation():
    rows = _rows(
        _call(1, "Read", "u1", "/x/a.py"),
        _call(2, "Read", "u2", "/x/b.py"),
        _call(3, "Write", "u3", "/x/new.py"),   # write is never folded/grouped
    )
    ents = _compact_rows(rows)["entries"]
    assert [e["kind"] for e in ents] == ["group", "node"]
    assert ents[0]["count"] == 2
    assert ents[1]["node"]["payload"]["tool"] == "Write"


def test_a3_group_breaks_at_error_and_the_breaking_node_stays_own():
    rows = _rows(
        _call(1, "Read", "u1", "/x/a.py"),
        _call(2, "Read", "u2", "/x/b.py"),
        _call(3, "Read", "u3", "/x/c.py", sec=3),
        _result(4, "u3", sec=4, is_error=True),   # determinate error -> breaks, own node
        _call(5, "Read", "u4", "/x/d.py"),
        _call(6, "Read", "u5", "/x/e.py"),
    )
    ents = _compact_rows(rows)["entries"]
    assert [e["kind"] for e in ents] == ["group", "node", "group"]
    assert ents[1]["node"]["seq"] == 3 and ents[1]["outcome"] == "error"
    # The erroring call belongs to no group.
    assert 3 not in _node_seqs(ents[0]["children"])
    assert 3 not in _node_seqs(ents[2]["children"])


def test_a3_group_breaks_at_a_structural_span_boundary():
    # A structural node (a nested agent.run span) between two read runs ends the
    # group; it is a plain node, never absorbed (E4).
    rows = _rows(
        (_call(1, "Read", "u1", "/x/a.py"), 2),
        (_call(2, "Read", "u2", "/x/b.py"), 2),
        (_span(3, "agent.run", "spec_agent"), 1),
        (_call(4, "Read", "u3", "/x/c.py"), 2),
        (_call(5, "Read", "u4", "/x/d.py"), 2),
    )
    ents = _compact_rows(rows)["entries"]
    assert [e["kind"] for e in ents] == ["group", "node", "group"]
    assert ents[1]["node"]["type"] == "agent.run"


def test_a3_no_group_hull_below_two_children_including_a_lone_repeat():
    # A single groupable call stays bare (no hull).
    lone_call = _compact_rows(_rows(
        _msg(1), _call(2, "Read", "u1", "/x/a.py"), _msg(3),
    ))["entries"]
    assert [e["kind"] for e in lone_call] == ["node", "node", "node"]
    assert lone_call[1]["node"]["payload"]["tool"] == "Read"

    # A single repeat is NOT wrapped in a group hull either.
    lone_repeat = _compact_rows(_rows(
        _call(1, "Read", "u1", "/x/a.py"),
        _call(2, "Read", "u2", "/x/a.py"),
        _msg(3),
    ))["entries"]
    assert [e["kind"] for e in lone_repeat] == ["repeat", "node"]


# --- A6: per-page line balance (the spec's worked reference example) ------------


def test_a6_line_balance_matches_the_spec_reference_example():
    # 10 events: phase, message, Read a + result (x2), Grep p + result, Write + result
    # -> 4 rows (phase, message, group{repeat Read a x2, Grep p}, Write); 7 folded.
    rows = _rows(
        (_span(1, "phase", "build"), 0),
        (_msg(2), 1),
        (_call(3, "Read", "u1", "/x/a.py"), 1),
        (_result(4, "u1", is_error=False), 1),
        (_call(5, "Read", "u2", "/x/a.py"), 1),
        (_result(6, "u2", is_error=False), 1),
        (_call(7, "Grep", "u3", "p"), 1),
        (_result(8, "u3", is_error=False), 1),
        (_call(9, "Write", "u4", "/x/new.py"), 1),
        (_result(10, "u4", is_error=False), 1),
    )
    out = _compact_rows(rows)
    ents = out["entries"]

    assert [e["kind"] for e in ents] == ["node", "node", "group", "node"]
    group = ents[2]
    assert group["count"] == 3 and set(group["ops"]) == {"Read", "Grep"}
    assert [c["kind"] for c in group["children"]] == ["repeat", "node"]
    assert group["children"][0]["count"] == 2

    assert out["rows"] == 4
    assert out["folded"] == 7


# --- A4: repo-relative paths use CONTAINMENT, not a lexical prefix ---------------


def test_a4_repo_relative_only_shortens_paths_truly_inside_the_repo():
    root = "/repo/root"
    # A clean inside path is relativised.
    assert _repo_relative("/repo/root/adw/app.py", root) == "adw/app.py"
    # A traversal path that ESCAPES the repo is NOT shortened (normalised containment,
    # not a lexical startswith) — it stays visibly unchanged (A4).
    assert _repo_relative("/repo/root/../outside/file", root) == "/repo/root/../outside/file"
    # A mere textual prefix collision (/repo/rootkit vs /repo/root) is NOT shortened.
    assert _repo_relative("/repo/rootkit/file", root) == "/repo/rootkit/file"
    # A genuinely outside path and a non-absolute path are unchanged.
    assert _repo_relative("/etc/hosts", root) == "/etc/hosts"
    assert _repo_relative("relative/inside.py", root) == "relative/inside.py"


def test_a4_display_label_keeps_escaping_path_unchanged_with_full_title():
    # An outside-repo path reached via traversal renders visibly unchanged AND keeps
    # its full raw path in the title (A4 — the tooltip stays present for path args).
    node = {
        "type": "agent.tool.call", "seq": 1, "label": "Read x",
        "payload": {"tool": "Read", "tool_use_id": "u1",
                    "input": {"file_path": "/repo/root/../outside/x.py"}},
        "children": [],
    }
    text, title = _display_label(node, "/repo/root")
    assert text == "Read /repo/root/../outside/x.py"   # visibly unchanged
    assert title == "/repo/root/../outside/x.py"        # full raw path in the title


# --- the column is not paged: compaction spans the WHOLE run -------------------


def _pipeline_rows(lines):
    events = [e for e in lines if isinstance(e, dict)]
    tool_names = _tool_names_by_use_id(events)
    tree = [_serialize(r, tool_names) for r in build_tree(events)]
    rows = _tree_rows(tree)
    return rows, _compact_rows(rows["rows"])


def _six_read_run():
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Reads")),
        rec(2, "agent.run", "start", "A", "R", sec=1,
            payload={"agent": "spec_agent", "prompt": "p", "system_append": ""}),
    ]
    seq = 3
    for i in range(6):
        lines.append(rec(seq, "agent.tool.call", "point", "A", sec=seq, payload={
            "tool": "Read", "tool_use_id": f"u{i}", "input": {"file_path": f"/x/f{i}.py"}}))
        seq += 1
        lines.append(rec(seq, "agent.tool.result", "point", "A", sec=seq,
                         payload={"tool_use_id": f"u{i}", "is_error": False}))
        seq += 1
    lines.append(rec(seq, "agent.run", "end", "A", "R", sec=seq,
                     payload={"result_text": "done", "is_error": False}))
    seq += 1
    lines.append(rec(seq, "run", "end", "R", None, sec=seq,
                     payload={"status": "done", "totals": {"duration": 1.0}}))
    return lines


def test_a_group_spans_the_whole_run_now_that_the_column_is_not_paged():
    """The trace column renders every node, so an uninterrupted read run collapses
    into ONE group over all six calls — the old page boundary that used to cut a
    group in half is gone."""
    lines = _six_read_run()
    total = len(_flatten_tree(
        [_serialize(r, {}) for r in build_tree([e for e in lines if isinstance(e, dict)])]))

    rows, out = _pipeline_rows(lines)

    assert rows["total"] == total
    assert len(rows["rows"]) == total          # every node is rendered, nothing cut

    groups = [e for e in out["entries"] if e["kind"] == "group"]
    assert len(groups) == 1                    # one group, not one per page
    assert len(set(_node_seqs(groups[0]["children"]))) == 6


# --- A5: the server names the phase opened by default ---------------------------


def _two_phase_tree(*, error_in=None):
    """A run of three phases (spec, plan, build). When ``error_in`` names a phase,
    that phase carries an erroring tool result; otherwise every phase is clean."""
    lines = [rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Phases"))]
    seq = 2
    phase_seq = {}
    for name in ("spec", "plan", "build"):
        pstart = seq
        phase_seq[name] = pstart
        lines.append(rec(seq, "phase", "start", f"P{name}", "R", sec=seq,
                         payload={"name": name, "from_phase": name}))
        seq += 1
        lines.append(rec(seq, "agent.run", "start", f"A{name}", f"P{name}", sec=seq,
                         payload={"agent": f"{name}_agent", "prompt": "p", "system_append": ""}))
        seq += 1
        lines.append(rec(seq, "agent.tool.call", "point", f"A{name}", sec=seq, payload={
            "tool": "Read", "tool_use_id": f"{name}1", "input": {"file_path": "/x/a.py"}}))
        seq += 1
        lines.append(rec(seq, "agent.tool.result", "point", f"A{name}", sec=seq, payload={
            "tool_use_id": f"{name}1", "is_error": name == error_in}))
        seq += 1
        lines.append(rec(seq, "agent.run", "end", f"A{name}", f"P{name}", sec=seq,
                         payload={"result_text": "done", "is_error": False}))
        seq += 1
        lines.append(rec(seq, "phase", "end", f"P{name}", "R", sec=seq,
                         payload={"name": name, "to_phase": "done"}))
        seq += 1
    lines.append(rec(seq, "run", "end", "R", None, sec=seq,
                     payload={"status": "done", "totals": {"duration": 1.0}}))
    events = [e for e in lines if isinstance(e, dict)]
    tool_names = _tool_names_by_use_id(events)
    tree = [_serialize(r, tool_names) for r in build_tree(events)]
    return tree, phase_seq


def test_default_open_phase_is_first_error_phase_else_last_started():
    # With a determinate error in the middle phase, the tree-order-FIRST error phase
    # opens — even though a later phase started afterwards.
    tree, phase_seq = _two_phase_tree(error_in="plan")
    assert _default_open_phase(tree) == phase_seq["plan"]

    # Without any error, the LAST-started phase opens.
    tree2, phase_seq2 = _two_phase_tree(error_in=None)
    assert _default_open_phase(tree2) == phase_seq2["build"]
