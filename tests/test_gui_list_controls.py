"""RED tests for A5 — server-side sorting and filtering of the run list.

The list ``/`` gains four OPTIONAL query params, evaluated server-side like the Raw
tab's ``?raw_q``/``?raw_type`` — no client state, no persistence (E2):

* ``?sort`` ∈ {start, duration, cost, events}, ``?dir`` ∈ {asc, desc}; unknown or
  missing values fall back to start/desc — never an error, never an empty list;
* ``?repo`` (repo slug) and ``?status`` (status value); combined = intersection; an
  unknown value yields an EMPTY result with a hint — never the unfiltered list,
  never an error.

The existing status grouping (``awaiting_approval`` first, then ``running``, then
the rest; newest first within a group) is applied BEFORE the chosen sort and keeps
priority. All four params survive the language switch because they ride in the URL
(``switch_qs``). Derived from .adw/spec.md (AC 10–13), .adw/contract.yaml
(x-adw-template-behavior sorting/filtering, invariants) and .adw/plan.md (B6).
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

AW = "aa000000"
D1 = "d1110001"
D2 = "d2220002"
D3 = "d3330003"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _order(html):
    """Run ids in the order they appear as row links, one per listed run."""
    return re.findall(r'/runs/[^/"]+/([0-9a-f]{8})"', html)


def done_run(issue, *, cost, duration, start_sec, pad=0):
    """A finished run with tunable cost, duration and event_count (2 + ``pad``)."""
    lines = [rec(1, "run", "start", "R", None, sec=start_sec, payload=run_start_payload(issue))]
    seq = 2
    for i in range(pad):
        lines.append(rec(seq, "note.item", "point", "R", sec=start_sec, payload={"i": i}))
        seq += 1
    lines.append(rec(seq, "run", "end", "R", None, sec=start_sec + 1,
                     payload=run_end_payload("done", duration, cost, 10)))
    return lines


def awaiting_run(issue, *, start_sec):
    """An open run paused at the plan gate → status awaiting_approval, no totals."""
    return [
        rec(1, "run", "start", "R", None, sec=start_sec, payload=run_start_payload(issue)),
        rec(2, "approval", "point", "R", sec=start_sec + 1,
            payload={"gate": "plan", "event": "awaited"}),
    ]


def _sortable_repo(tmp_path):
    """One repo with an awaiting run plus three finished runs whose cost, duration,
    event_count and start are all distinct, so each sort key gives a distinct order.

      run  cost  duration  events  start
      D1   5.0   100       2       10
      D2   1.0   300       6       20
      D3   3.0   200       3       30
      AW   —     —         2        5   (awaiting_approval)
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, D1, done_run("One", cost=5.0, duration=100, start_sec=10, pad=0),
              phase="done", issue="One")
    write_run(repo, D2, done_run("Two", cost=1.0, duration=300, start_sec=20, pad=4),
              phase="done", issue="Two")
    write_run(repo, D3, done_run("Three", cost=3.0, duration=200, start_sec=30, pad=1),
              phase="done", issue="Three")
    write_run(repo, AW, awaiting_run("Awaiting", start_sec=5),
              phase="awaiting_approval", issue="Awaiting")
    return repo


# --- AC 10: sorting, with the status grouping keeping priority -------------------


def test_sort_keys_order_the_list_and_awaiting_stays_first(home, tmp_path):  # noqa: F811
    """AC 10: each sort key/direction reorders the finished runs accordingly, while
    the ``awaiting_approval`` run stays above all others regardless of the sort."""
    client = TestClient(create_app(repos=[str(_sortable_repo(tmp_path))]))

    cases = {
        ("cost", "desc"): [D1, D3, D2],
        ("cost", "asc"): [D2, D3, D1],
        ("duration", "desc"): [D2, D3, D1],
        ("events", "desc"): [D2, D3, D1],
        ("start", "desc"): [D3, D2, D1],
    }
    for (sort, direction), expected_rest in cases.items():
        order = _order(client.get(f"/?sort={sort}&dir={direction}").text)
        assert order[0] == AW, f"{sort}/{direction}: awaiting not first ({order})"
        assert order[1:] == expected_rest, f"{sort}/{direction}: {order[1:]} != {expected_rest}"


def test_list_page_offers_visible_sort_and_filter_controls(home, tmp_path):  # noqa: F811
    """A5: the list carries visible server-side controls — sortable column links
    (carrying ``sort=``) and a filter control (a form/select). Today the rendered
    document has none (0 ``form``, 0 ``select``, 0 sortable ``th`` link)."""
    html = TestClient(create_app(repos=[str(_sortable_repo(tmp_path))])).get("/").text
    assert "sort=" in html, "no sortable controls carrying ?sort="
    assert ("<form" in html) or ("<select" in html), "no visible filter control"


# --- AC 11: filtering by status / repo, and the empty-result hint ---------------


def test_filter_by_status_and_repo_act_as_intersection(home, tmp_path):  # noqa: F811
    """AC 11: ``?status`` keeps only that status; ``?repo`` keeps only that repo;
    combined they intersect."""
    repo_a = tmp_path / "repoA"
    repo_a.mkdir()
    write_run(repo_a, D1, done_run("A done", cost=1.0, duration=10, start_sec=1),
              phase="done", issue="A done")
    write_run(repo_a, "ee000001",
              [rec(1, "run", "start", "R", None, sec=2, payload=run_start_payload("A esc")),
               rec(2, "run", "end", "R", None, sec=3, payload=run_end_payload("escalated"))],
              phase="escalated", issue="A esc")
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    write_run(repo_b, D2, done_run("B done", cost=1.0, duration=10, start_sec=1),
              phase="done", issue="B done")

    client = TestClient(create_app(repos=[str(repo_a), str(repo_b)]))
    slug_a = _slug_for(repo_a)

    only_esc = set(_order(client.get("/?status=escalated").text))
    assert only_esc == {"ee000001"}

    only_a = set(_order(client.get(f"/?repo={slug_a}").text))
    assert only_a == {D1, "ee000001"}
    assert D2 not in only_a

    intersection = set(_order(client.get(f"/?repo={slug_a}&status=done").text))
    assert intersection == {D1}


def test_unknown_filter_value_gives_empty_result_not_the_full_list(home, tmp_path):  # noqa: F811
    """AC 11: an unknown filter value yields an EMPTY result (with a hint), not an
    error and not the unfiltered list."""
    client = TestClient(create_app(repos=[str(_sortable_repo(tmp_path))]))

    resp = client.get("/?status=does-not-exist")
    assert resp.status_code == 200
    assert _order(resp.text) == [], "an unknown status must not return any run"

    full = client.get("/").text
    assert _order(full), "sanity: the unfiltered list has runs"
    assert resp.text != full, "the unknown-filter page must differ from the full list"


# --- AC 12: robustness of unknown sort/dir -------------------------------------


def test_unknown_sort_and_dir_fall_back_to_default_without_error(home, tmp_path):  # noqa: F811
    """AC 12: unknown ``sort``/``dir`` values fall back to start/desc — no error,
    no empty list — and null metrics sort without raising (the awaiting run has
    no cost/duration)."""
    client = TestClient(create_app(repos=[str(_sortable_repo(tmp_path))]))

    default_order = _order(client.get("/").text)
    resp = client.get("/?sort=bogus&dir=sideways")
    assert resp.status_code == 200
    order = _order(resp.text)
    assert order == default_order, (order, default_order)
    assert order  # never empty


# --- AC 13: sort/filter survive the language switch ----------------------------


def test_sort_and_filter_survive_the_language_switch(home, tmp_path):  # noqa: F811
    """AC 13: the language switch link keeps every sort/filter param (it flips only
    ``lang``), so switching language preserves the chosen sort and filter."""
    repo = _sortable_repo(tmp_path)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)

    html = client.get(f"/?sort=cost&dir=asc&status=done&repo={slug}&lang=en").text
    m = re.search(r'class="lang-switch"[^>]*href="([^"]+)"', html)
    assert m, "no language switch link on the list page"
    href = m.group(1)
    for token in ("sort=cost", "dir=asc", "status=done", "repo=", "lang=de"):
        assert token in href, f"{token!r} not preserved across the language switch: {href}"
