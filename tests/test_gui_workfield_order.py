"""RED tests for A1/A2 — the work field moves directly under the head and the two
summaries drop below it, collapsed and self-describing.

The run-detail page is re-ordered so the instrument (the trace tree) begins right
under the page head: ``Head → work field (Trace tree │ detail panes │ run context)
→ "Planned tasks" (collapsed) → "Change scope" (collapsed)``. Both former blocks
render as native ``<details>`` WITHOUT ``open`` — no JavaScript, no client state, no
query parameter, exactly like the trace tree's collapse nodes. Each ``<summary>``
line carries the statement one used to have to expand the block for: "Planned
tasks" the per-lane name / task count / state, "Change scope" the number of changed
files with the sums of added and removed lines. When the data is missing the line
says so instead of unfolding into an empty block.

Markup/CSS wording, class names and block order in the markup are NOT contractual
(``.adw/contract.yaml`` x-adw-contract-boundary); these tests read only the
observable behaviour — document order of the trace tree vs the two blocks, the
``<details>``/``<summary>`` collapse, and the tokens each summary line carries —
anchored on the existing ``data-plan-skeleton`` / ``data-change-scope`` markers the
present tests already rely on.

Derived from .adw/spec.md (AC 1-4), .adw/contract.yaml
(x-adw-template-behavior.order / .summaries) and .adw/plan.md (B2.1-B2.4, B3, B4).
RED until the template re-orders the blocks, wraps them in ``<details>`` and renders
the two summary lines.
"""

import os
import re
import subprocess

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.i18n import CATALOG
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


# --- helpers -------------------------------------------------------------------


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _client(repo):
    return TestClient(create_app(repos=[str(repo)]))


def _page(client, slug, run_id=RUN_ID, **params):
    r = client.get(f"/runs/{slug}/{run_id}", params=params or None)
    assert r.status_code == 200, r.status_code
    return r.text


def _text(html: str) -> str:
    """Tag-stripped, whitespace-collapsed text of an HTML fragment."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _block(html: str, anchor: str):
    """The collapsible block whose subtree carries ``anchor`` (``data-plan-skeleton``
    / ``data-change-scope``): returns ``(details_open_tag, summary_inner, body)``.

    The block's ``<details>`` is the tag either bearing the anchor itself or the
    nearest enclosing one; its FIRST ``<summary>`` is the collapse line and the span
    up to the next ``</details>`` is the (initially hidden) body. Neither the anchor
    nor the block are pinned as markup — only that a ``<details>`` collapse exists."""
    i = html.find(anchor)
    assert i != -1, f"{anchor} not rendered"
    tag_start = html.rfind("<", 0, i + 1)
    if html.startswith("<details", tag_start):
        d = tag_start
    else:
        d = html.rfind("<details", 0, tag_start)
    assert d != -1, f"no <details> wraps {anchor}"
    open_tag = html[d:html.find(">", d) + 1]
    s = html.find("<summary", d)
    se = html.find("</summary>", s)
    assert s != -1 and se != -1, f"no <summary> in the {anchor} block"
    summary_inner = html[html.find(">", s) + 1:se]
    body = html[se + len("</summary>"):html.find("</details>", se)]
    return open_tag, summary_inner, body


def _has_open(open_tag: str) -> bool:
    return re.search(r"<details\b[^>]*\bopen\b", open_tag) is not None


# --- git-backed change-scope fixtures ------------------------------------------


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.strip()


def _init_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "adw-test@example.com")
    _git(repo, "config", "user.name", "ADW Test")
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")


def _ref(seq):
    return f"refs/adw/{RUN_ID}/{seq}"


def _commit_ref(repo, files, ref, *, allow_empty=False):
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (bytes, bytearray)):
            p.write_bytes(bytes(content))
        else:
            p.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", *(["--allow-empty"] if allow_empty else []), "-m", f"snap {ref}")
    _git(repo, "update-ref", ref, "HEAD")


def _lane_start(seq, lane):
    return rec(seq, "lane", "start", f"L{lane}", "PB", sec=seq, lane=lane, payload={
        "name": lane, "branch": f"adw/{lane}", "worktree": "wt",
        "base_sha": None, "ports": {}})


def _snap(seq, lane, ref):
    return rec(seq, "snapshot", "point", "L", sec=seq, lane=lane,
               payload={"lane": lane, "tree": "t", "ref": ref, "label": "s"})


def _wrap(inner):
    return [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Change scope")),
        rec(2, "phase", "start", "PB", "R", sec=2,
            payload={"name": "build", "from_phase": "build"}),
        *inner,
        rec(97, "phase", "end", "PB", "R", sec=97, payload={"name": "build", "to_phase": "done"}),
        rec(98, "run", "end", "R", None, sec=98, payload=run_end_payload("done")),
    ]


# The plan.md that yields two workstreams with a distinct task count and, against a
# completed ``backend`` lane, distinct states: backend -> 1 task / done, frontend ->
# 3 tasks / pending. Task texts are content (never translated).
TWO_LANE_PLAN = (
    "## Workstream: backend\n### B1 — Sole task\n"
    "## Workstream: frontend\n### F1 — one\n### F2 — two\n### F3 — three\n"
)


def _write_plan(repo, text, run_id=RUN_ID):
    (repo / ".adw" / "runs" / run_id / "plan.md").write_text(text, encoding="utf-8")


# --- AC 1: the trace tree precedes the two summary blocks -----------------------


def test_trace_list_precedes_the_two_summary_blocks(home, tmp_path):  # noqa: F811
    """AC 1: in the rendered document ``.trace-list`` stands BEFORE both "Planned
    tasks" (``data-plan-skeleton``) and "Change scope" (``data-change-scope``), and
    neither block sits between the page head and the work field."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, RUN_ID, comprehensive_lines(), phase="done")
    _write_plan(repo, TWO_LANE_PLAN)
    html = _page(_client(repo), _slug_for(repo))

    trace = html.find('class="trace-list"')
    plan = html.find("data-plan-skeleton")
    scope = html.find("data-change-scope")
    assert trace != -1 and plan != -1 and scope != -1
    assert trace < plan, "the trace tree must precede the Planned-tasks block"
    assert trace < scope, "the trace tree must precede the Change-scope block"

    # Nothing between the head and the work field: the region from </header> up to
    # the trace tree carries neither summary block.
    head_end = html.find("</header>")
    assert head_end != -1
    between = html[head_end:trace]
    assert "data-plan-skeleton" not in between
    assert "data-change-scope" not in between


# --- AC 2: both blocks are collapsed <details> without open ---------------------


def test_summary_blocks_are_collapsed_details_without_open(home, tmp_path):  # noqa: F811
    """AC 2: "Planned tasks" and "Change scope" each render as a native ``<details>``
    with a ``<summary>`` and NO ``open`` attribute — their bodies start hidden but the
    former block content stays reachable below the summary. No query parameter or
    script drives the collapse."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, RUN_ID, comprehensive_lines(), phase="done")
    _write_plan(repo, TWO_LANE_PLAN)
    html = _page(_client(repo), _slug_for(repo))

    plan_open, _plan_summary, plan_body = _block(html, "data-plan-skeleton")
    assert not _has_open(plan_open), "Planned tasks must render collapsed (no open)"
    assert "B1 — Sole task" in plan_body, "the planned tasks stay reachable in the body"

    scope_open, _scope_summary, scope_body = _block(html, "data-change-scope")
    assert not _has_open(scope_open), "Change scope must render collapsed (no open)"
    # The declared-scope chrome (present or its absence) stays in the expanded body.
    assert (CATALOG["en"]["change_scope_declared"] in scope_body
            or CATALOG["en"]["change_scope_no_declared"] in scope_body)

    # The collapse is pure markup: no script and no query parameter steers it.
    assert "<script" not in plan_open and "<script" not in scope_open
    for pname in ("plan_open", "planned_open", "scope_open", "summaries_open"):
        assert pname not in html


# --- AC 3: the Planned-tasks summary line carries the statement ------------------


def test_planned_tasks_summary_names_lane_task_count_and_state(home, tmp_path):  # noqa: F811
    """AC 3: the "Planned tasks" ``<summary>`` names, per lane, its name, the number
    of tasks and the EXISTING lane state — readable without expanding. Here backend
    (1 task, completed lane -> done) and frontend (3 tasks, never ran -> pending)
    both appear with their counts and their distinct states."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, RUN_ID, comprehensive_lines(), phase="done")
    _write_plan(repo, TWO_LANE_PLAN)
    html = _page(_client(repo), _slug_for(repo))

    _open, summary, _body = _block(html, "data-plan-skeleton")
    text = _text(summary)

    # Lane names (content) and their task counts are on the summary line.
    assert "backend" in text and "frontend" in text
    assert re.search(r"\b1\b", text), f"backend's task count (1) missing: {text!r}"
    assert re.search(r"\b3\b", text), f"frontend's task count (3) missing: {text!r}"

    # The EXISTING lane state vocabulary (done / pending) is shown, not re-derived.
    assert CATALOG["en"]["plan_skeleton_done"] in text
    assert CATALOG["en"]["plan_skeleton_pending"] in text


def test_planned_tasks_block_is_absent_without_a_plan_skeleton(home, tmp_path):  # noqa: F811
    """AC 3 / normative: without a plan skeleton (no plan.md) the whole block is not
    rendered — no empty collapsible ``<details>`` and no summary line at all."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, RUN_ID, comprehensive_lines(), phase="done")  # no plan.md written
    html = _page(_client(repo), _slug_for(repo))

    assert "data-plan-skeleton" not in html
    # The Change-scope block still renders (it is always present).
    assert "data-change-scope" in html


# --- AC 4: the Change-scope summary line carries the file/line statement ---------


def test_change_scope_summary_counts_files_and_line_sums_across_lanes(home, tmp_path):  # noqa: F811,E501
    """AC 4: the "Change scope" ``<summary>`` names the number of changed files over
    ALL observed lanes and the sums of added and removed lines. A binary file counts
    towards the file number but contributes nothing to the line sums. Here backend
    (a text file +12/-1 and a binary) and frontend (a text file +5/-3) give 3 files,
    +17 and -4."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_ref(repo, {"backend/a.py": "base\n", "assets/logo.bin": bytes(range(50))}, _ref(1))
    _commit_ref(repo, {
        "backend/a.py": "".join(f"L{i}\n" for i in range(12)),  # 12 new lines, 1 removed
        "assets/logo.bin": bytes(reversed(range(50))),          # binary -> null/null
    }, _ref(2))
    _commit_ref(repo, {"frontend/x.js": "x1\nx2\nx3\n"}, _ref(3))
    _commit_ref(repo, {"frontend/x.js": "y1\ny2\ny3\ny4\ny5\n"}, _ref(4))  # +5/-3

    write_run(repo, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", _ref(1)),
        _snap(6, "backend", _ref(2)),
        _lane_start(7, "frontend"),
        _snap(8, "frontend", _ref(3)),
        _snap(10, "frontend", _ref(4)),
    ]), phase="done")
    html = _page(_client(repo), _slug_for(repo))

    _open, summary, _body = _block(html, "data-change-scope")
    text = _text(summary)

    assert re.search(r"\b3\b", text), f"changed-file count (3) missing: {text!r}"
    assert re.search(r"\b17\b", text), f"added-line sum (17) missing: {text!r}"
    assert re.search(r"\b4\b", text), f"removed-line sum (4) missing: {text!r}"
    # The binary file's own path/counts are content, not the summary's business.
    assert "50" not in text, "the binary's byte size must not leak into the sums"


def test_change_scope_summary_distinguishes_empty_from_unavailable(home, tmp_path):  # noqa: F811,E501
    """AC 4 (normative empty vs unavailable): a run whose diff is available but has
    NO changed files shows file count 0 with zero sums; a run with NO usable diff
    shows an explanatory line INSTEAD of a zero. The two summary lines are
    distinguishable — the empty one carries a 0, the unavailable one does not."""
    # (a) available diff, no changed files: two identical snapshots -> files == [].
    empty = tmp_path / "empty"
    _init_repo(empty)
    _commit_ref(empty, {"x.py": "a\n"}, _ref(1))
    _commit_ref(empty, {}, _ref(2), allow_empty=True)
    write_run(empty, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", _ref(1)),
        _snap(6, "backend", _ref(2)),
    ]), phase="done")
    _open, empty_summary, _body = _block(_page(_client(empty), _slug_for(empty)),
                                         "data-change-scope")
    empty_text = _text(empty_summary)

    # (b) no usable diff at all: a lane observed with a single snapshot (no pair).
    unavail = tmp_path / "unavail"
    unavail.mkdir()
    write_run(unavail, RUN_ID, _wrap([
        _lane_start(3, "backend"),
        _snap(4, "backend", _ref(1)),   # one snapshot -> no diff pair
    ]), phase="done")
    _open2, unavail_summary, _body2 = _block(_page(_client(unavail), _slug_for(unavail)),
                                             "data-change-scope")
    unavail_text = _text(unavail_summary)

    assert empty_text != unavail_text, "empty and unavailable summaries must differ"
    assert re.search(r"\b0\b", empty_text), f"the empty diff must show a 0: {empty_text!r}"
    # The unavailable line is an explanation, NOT a zero count.
    assert "0" not in unavail_text, f"unavailable must not read as a 0: {unavail_text!r}"
    assert re.search(r"[A-Za-z]", unavail_text), "the unavailable line carries no words"
