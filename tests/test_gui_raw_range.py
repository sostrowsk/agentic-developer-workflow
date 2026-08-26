"""RED tests for A1/A2 — the Raw tab's inclusive seq-range filter and the
span-node jump into it.

The externally observable surface is the server-rendered run-detail page
``GET /runs/{repo}/{run_id}`` with the two NEW query parameters ``raw_from_seq``
and ``raw_to_seq`` (names pinned in .adw/contract.yaml). The range is composed
SERVER-SIDE (logical AND) with the existing ``q``/``type`` filters and the
``limit`` window; ``total`` stays the size of the match set BEFORE windowing and
``types`` stays the full type set of the log (contract R1..R4). Every span node
offers a jump that sets the range to its already-exposed ``[seq, end_seq]`` while
preserving ``q``/``type``/``limit`` (R5/R6); an active range is visible and
clearable in isolation, and landing on it activates the existing Raw tab without a
second Raw widget (R5/R6).

Markup/CSS are not pinned by the contract; following the established GUI test
style (see ``test_gui_context_panel``) this module fixes a small, intuitive
data-attribute / query observable so the implementation has a concrete target:
the Raw filter form round-trips ``raw_from_seq``/``raw_to_seq`` and a
``raw-range-clear`` control drops ONLY the range. No production code is written
here.

Derived from .adw/spec.md (AC 1-7), .adw/contract.yaml (R1..R6) and
.adw/plan.md (B1-B3).
"""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    home,
    iter_nodes,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


def _range_lines():
    """A small log with a run span, five alternating ``note.alpha``/``note.beta``
    points (seqs 2-6), a point WITHOUT an integer seq (``note.noseq``) and a run
    end (seq 7). Each note carries a unique ``tagN`` marker so a free-text query
    can pick a single event. The int seqs present are 1..7."""
    lines = [
        rec(1, "run", "start", "R", None, sec=1, payload=run_start_payload("Range run")),
    ]
    for seq in range(2, 7):
        typ = "note.alpha" if seq % 2 == 0 else "note.beta"
        lines.append(rec(seq, typ, "point", "R", sec=seq, payload={"m": f"tag{seq}"}))
    # An event whose ``seq`` is not an integer (None) — it must NOT satisfy an
    # active range, yet appears when no range is active (contract R1).
    lines.append(rec(None, "note.noseq", "point", "R", sec=6, payload={"m": "tagnoseq"}))
    lines.append(rec(7, "run", "end", "R", None, sec=7, payload=run_end_payload("done")))
    return lines


def _client(tmp_path, lines):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = client.get("/api/runs").json()[0]["repo"]
    return client, slug


def _raw_panel(html):
    """Just the Raw tab-panel markup (everything after its marker)."""
    return html.split('data-tab-panel="raw"', 1)[1]


def _raw_rows(html):
    """Only the rendered ``<ol class="raw-list">`` rows region — excludes the
    filter form (whose ``type`` options always list the full type set)."""
    return _raw_panel(html).split('class="raw-list"', 1)[1].split("</ol>", 1)[0]


def _raw_seqs(html):
    """The ``data-seq`` values of the rendered Raw rows, in order (strings)."""
    return re.findall(r'class="raw-row"[^>]*data-seq="([^"]*)"', _raw_rows(html))


def _raw_total(html):
    """The reported match-set size BEFORE windowing (``data-raw-total``)."""
    m = re.search(r'data-raw-total="([0-9]+)"', _raw_panel(html))
    return int(m.group(1))


def _raw_type_options(html):
    """The values the Raw ``type`` filter offers (excluding the empty all-types)."""
    panel = _raw_panel(html).split("</form>", 1)[0]
    return [v for v in re.findall(r'<option value="([^"]*)"', panel) if v]


def _hrefs(chunk):
    return re.findall(r'href="([^"]*)"', chunk)


def test_range_both_bounds_are_inclusive(home, tmp_path):  # noqa: F811
    """R1: with both bounds set, an event matches iff ``lower <= seq <= upper``;
    each bound is inclusive."""
    client, slug = _client(tmp_path, _range_lines())
    html = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 3, "raw_to_seq": 5}).text

    assert set(_raw_seqs(html)) == {"3", "4", "5"}
    assert _raw_total(html) == 3


def test_range_one_sided_lower_and_upper(home, tmp_path):  # noqa: F811
    """R1: a single active bound filters one-sided (>= lower / <= upper)."""
    client, slug = _client(tmp_path, _range_lines())

    lower = client.get(f"/runs/{slug}/{RUN_ID}", params={"raw_from_seq": 4}).text
    assert set(_raw_seqs(lower)) == {"4", "5", "6", "7"}

    upper = client.get(f"/runs/{slug}/{RUN_ID}", params={"raw_to_seq": 3}).text
    assert set(_raw_seqs(upper)) == {"1", "2", "3"}


def test_event_without_integer_seq_never_satisfies_active_range(home, tmp_path):  # noqa: F811
    """R1: an event whose ``seq`` is not an integer does not satisfy an active
    range, but is present when no range is active."""
    client, slug = _client(tmp_path, _range_lines())

    ranged = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 1, "raw_to_seq": 7}).text
    assert "note.noseq" not in _raw_rows(ranged)
    assert _raw_total(ranged) == 7            # the seven int-seq events, not the noseq one

    plain = client.get(f"/runs/{slug}/{RUN_ID}").text
    assert "note.noseq" in _raw_rows(plain)
    assert _raw_total(plain) == 8             # unchanged: every event including the noseq one


def test_range_composes_with_type_and_free_text(home, tmp_path):  # noqa: F811
    """R2: the range is AND-composed with ``type`` and with ``q`` (evaluated over
    the full serialized payload)."""
    client, slug = _client(tmp_path, _range_lines())

    # note.alpha lives at seqs 2, 4, 6; the range must drop the out-of-range 6.
    typed = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 2, "raw_to_seq": 4, "raw_type": "note.alpha"}).text
    assert set(_raw_seqs(typed)) == {"2", "4"}

    # ``tag`` matches every note payload (seqs 2-6); the range must drop 5 and 6.
    queried = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 2, "raw_to_seq": 4, "raw_q": "tag"}).text
    assert set(_raw_seqs(queried)) == {"2", "3", "4"}
    assert _raw_total(queried) == 3


def test_range_limit_windows_after_filtering_and_total_is_pre_window(home, tmp_path):  # noqa: F811
    """R2: ``limit`` windows only the fully filtered match set; the reported
    ``total`` stays the match-set size BEFORE windowing."""
    client, slug = _client(tmp_path, _range_lines())
    html = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 2, "raw_to_seq": 6, "limit": 2}).text

    assert len(_raw_seqs(html)) == 2          # windowed to the requested limit
    assert _raw_total(html) == 5              # 5 events match the range before windowing


def test_types_stay_the_full_log_type_set_with_active_range(home, tmp_path):  # noqa: F811
    """R3: the offered ``types`` list is the full type set of the log even while a
    narrow range is active."""
    client, slug = _client(tmp_path, _range_lines())
    html = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 3, "raw_to_seq": 3}).text

    offered = set(_raw_type_options(html))
    assert {"run", "note.alpha", "note.beta", "note.noseq"} <= offered


def test_non_numeric_bound_is_inactive_and_other_filters_stay_effective(home, tmp_path):  # noqa: F811
    """R4: a non-numeric bound is treated as a missing bound (that bound inactive);
    a concurrently set ``q`` stays effective; two non-numeric bounds leave the Raw
    tab exactly as before."""
    client, slug = _client(tmp_path, _range_lines())

    one_sided = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": "abc", "raw_to_seq": 3}).text
    assert set(_raw_seqs(one_sided)) == {"1", "2", "3"}    # lower inactive, upper active

    with_q = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": "abc", "raw_q": "tag4"}).text
    assert set(_raw_seqs(with_q)) == {"4"}                 # q still applies

    both_bad = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": "abc", "raw_to_seq": "xyz"}).text
    assert _raw_total(both_bad) == 8                       # no range -> unchanged


def test_upper_below_lower_is_a_defined_empty_set_no_5xx(home, tmp_path):  # noqa: F811
    """R4: an upper bound below the lower yields a defined EMPTY match set with
    ``total`` 0 — never a traceback / 5xx — while ``q`` stays set."""
    client, slug = _client(tmp_path, _range_lines())
    resp = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 6, "raw_to_seq": 2, "raw_q": "tag4"})

    assert resp.status_code == 200
    assert _raw_seqs(resp.text) == []
    assert _raw_total(resp.text) == 0


def _span_with_subtree(tree):
    """A serialized span node whose subtree extends past its own seq (``end_seq >
    seq``), so its jump range ``[seq, end_seq]`` is a real interval."""
    for n in iter_nodes(tree):
        end = n.get("end_seq")
        seq = n.get("seq")
        if isinstance(end, int) and isinstance(seq, int) and end > seq:
            return n
    raise AssertionError("no span node with a subtree range in the fixture")


def _trace_section(html):
    i = html.find('data-tab-panel="trace"')
    j = html.find('data-tab-panel="timeline"')
    return html[i:j]


def test_span_node_offers_a_jump_to_its_subtree_seq_range(home, tmp_path):  # noqa: F811
    """R5: every span node offers a jump into the Raw tab pre-filtered to the
    node's already-exposed ``[seq, end_seq]``; the jump preserves the existing
    ``q``/``type``/``limit`` values, and no second Raw widget appears."""
    client, slug = _client(tmp_path, comprehensive_lines())
    tree = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["tree"]
    span = _span_with_subtree(tree)
    seq, end = span["seq"], span["end_seq"]

    html = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_q": "foo", "raw_type": "gate", "limit": 50}).text
    jumps = [h for h in _hrefs(_trace_section(html)) if f"raw_from_seq={seq}" in h]

    assert jumps, "no jump link carrying the node's lower seq bound"
    assert any(f"raw_to_seq={end}" in h for h in jumps)          # exact upper bound
    target = next(h for h in jumps if f"raw_to_seq={end}" in h)
    assert "raw_q=foo" in target and "raw_type=gate" in target and "limit=50" in target
    assert html.count('data-tab-panel="raw"') == 1              # E5: one Raw widget


def test_active_range_is_visible_clearable_in_isolation_and_activates_raw(home, tmp_path):  # noqa: F811
    """R5/R6: an active range round-trips in the Raw filter form (so changing
    ``q``/``type``/``limit`` keeps it) and landing on it activates the existing Raw
    tab. A ``raw-range-clear`` control drops ONLY the range, keeping
    ``q``/``type``/``limit``; with no active range no clear control is asserted."""
    client, slug = _client(tmp_path, comprehensive_lines())
    html = client.get(f"/runs/{slug}/{RUN_ID}", params={
        "raw_from_seq": 5, "raw_to_seq": 9, "raw_q": "foo",
        "raw_type": "gate", "limit": 50}).text
    panel = _raw_panel(html)

    # The active bounds are carried in the Raw tab (form round-trip -> visible).
    assert re.search(r'name="raw_from_seq"[^>]*value="5"', panel) or \
        re.search(r'value="5"[^>]*name="raw_from_seq"', panel)
    assert re.search(r'name="raw_to_seq"[^>]*value="9"', panel) or \
        re.search(r'value="9"[^>]*name="raw_to_seq"', panel)

    # Landing with a range active makes the Raw tab the active tab, not Trace.
    raw_cls = re.search(r'<section class="([^"]*)"[^>]*data-tab-panel="raw"', html).group(1)
    trace_cls = re.search(r'<section class="([^"]*)"[^>]*data-tab-panel="trace"', html).group(1)
    assert "active" in raw_cls and "active" not in trace_cls

    # Clearing removes ONLY the seq bounds; q/type/limit survive.
    clear = re.search(r'class="[^"]*raw-range-clear[^"]*"[^>]*href="([^"]*)"', panel) or \
        re.search(r'href="([^"]*)"[^>]*class="[^"]*raw-range-clear', panel)
    assert clear, "no isolated range-clear control while a range is active"
    href = clear.group(1)
    assert "raw_q=foo" in href and "raw_type=gate" in href and "limit=50" in href
    assert "raw_from_seq" not in href and "raw_to_seq" not in href

    plain = _raw_panel(client.get(f"/runs/{slug}/{RUN_ID}").text)
    assert "raw-range-clear" not in plain               # no range -> no range state asserted
