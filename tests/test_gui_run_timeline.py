"""RED tests for A5 — the phase band becomes a to-scale timeline of the run.

At its current place in the run header, the chip row (width follows the label) is
replaced by a continuous rail: each phase sits at its real temporal position with
its real duration as width; the gaps between phases — the time at the approval
gates — show as their own "waiting" segments; three labelled numbers (work,
waiting, total) sit beneath the rail. A phase that never ran keeps no rail area but
stays visible, dampened, in the legend. When no phase has a parsable timestamp (or
``T`` is not determinable / ≤ 0), the today's chip row is rendered as the fallback —
never an empty or broken rail, and never a second band beside it.

The reference runs ``16f39431`` and ``81795e53`` are rebuilt from REPRODUCIBLE test
data (not from local run directories): only the pinned time relations and sums
matter (contract ``reference_cases``; spec DoD).

*Marker policy (declared here, as .adw/plan.md leaves the markup to the
implementation — mirroring the ``data-tree-entry`` policy of the bounded-DOM run):*
the rail carries one ``data-phase-seg="<name>"`` element per phase WITH a parsable
start, each with an inline ``style`` giving its ``width``; a gap is a
``data-wait-seg`` element; an open (active, no end) phase segment additionally
carries ``data-open``; the three numbers are ``data-total="work|waiting|total"``
elements, each with a machine-readable ``data-seconds`` and a human label+value.
The concrete markup is the implementation's choice; these are the observables the
automated tests read.

Derived from .adw/spec.md (AC 7–10, 12), .adw/contract.yaml
(x-adw-template-behavior.timeline) and .adw/plan.md (B5). RED until ``_phase_bar``
emits start/end and the header renders the rail.
"""

import os
import re
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.model import build_tree
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    phases_lines,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"
_BASE = datetime(2026, 8, 5, 14, 0, 0)


def _iso(offset: float) -> str:
    """An ISO-UTC timestamp ``offset`` seconds after the base — fractional, so the
    implementation must handle real (non-integer) span times (plan B0.8)."""
    return (_BASE + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _phase_events(spans):
    """A finished-run event log whose phases occupy the given (start, end) offset
    pairs. ``spans`` is an ordered list of ``(name, start_offset, end_offset)``; a
    phase omitted from the list never ran. The run itself ends after the last
    phase, so every listed phase is ``completed``."""
    lines = [rec(1, "run", "start", "R", None, ts=_iso(0), payload=run_start_payload("Timeline"))]
    seq = 2
    last_end = 0.0
    for name, start, end in spans:
        sid = f"S{seq}"
        lines.append(rec(seq, "phase", "start", sid, "R", ts=_iso(start),
                         payload={"name": name, "from_phase": name}))
        seq += 1
        lines.append(rec(seq, "phase", "end", sid, "R", ts=_iso(end),
                         payload={"name": name, "to_phase": "done"}))
        seq += 1
        last_end = max(last_end, end)
    lines.append(rec(seq, "run", "end", "R", None, ts=_iso(last_end),
                     payload=run_end_payload("done")))
    return lines


# Reference run 16f39431: work 6084 s, waiting 3289 s, total 9372 s (±2). The spec
# gate holds 3206 s between spec and plan; build (2606.6 s) is 21x ci (124 s);
# integration never ran. Fractional offsets exercise the rounding tolerance.
RUN_16F39431 = [
    ("spec", 0.0, 806.6),
    ("plan", 4013.0, 4547.4),
    ("build", 4567.0, 7173.6),
    ("codex_review", 7194.0, 8339.4),
    ("final_review", 8359.0, 9225.6),
    ("ci", 9249.0, 9373.0),
]

# Reference run 81795e53: contiguous phases, no gaps — work 3656 s = total 3656 s,
# no waiting segment at all.
RUN_81795E53 = [
    ("spec", 0.0, 800.0),
    ("plan", 800.0, 1200.0),
    ("build", 1200.0, 3000.0),
    ("codex_review", 3000.0, 3400.0),
    ("final_review", 3400.0, 3600.0),
    ("ci", 3600.0, 3656.0),
]


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _detail(tmp_path, lines, *, phase="done", run_id=RUN_ID):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    return client, slug, client.get(f"/runs/{slug}/{run_id}").text


def _header(html: str) -> str:
    """The run header (where the phase band lives), scoped so a token echoed in the
    Timeline TAB (its own ``tl-*`` markup, untouched by A5 — E7) is never counted."""
    i = html.find("<header")
    j = html.find("</header>", i)
    assert i != -1 and j != -1, "run header not found"
    return html[i:j]


def _seg_width(header: str, name: str):
    """The numeric ``width`` of the phase ``name``'s rail segment (unit-agnostic —
    both segments in a comparison use the same unit), or ``None`` if absent."""
    m = re.search(
        r'data-phase-seg="' + re.escape(name) + r'"[^>]*style="[^"]*(?<![-\w])width\s*:\s*([\d.]+)',
        header,
    )
    return float(m.group(1)) if m else None


def _total_seconds(header: str, kind: str):
    m = re.search(r'data-total="' + kind + r'"[^>]*data-seconds="([\d.]+)"', header)
    if m is None:  # attribute order is the implementation's choice
        m = re.search(r'data-seconds="([\d.]+)"[^>]*data-total="' + kind + r'"', header)
    return float(m.group(1)) if m else None


# --- AC 7: the rail is to scale ------------------------------------------------


def test_build_segment_is_to_scale_and_a_dead_phase_keeps_no_area(home, tmp_path):  # noqa: F811
    """AC 7 / A5: for 16f39431 the ``build`` segment (2607 s) is at least 20x as wide
    as the ``ci`` segment (124 s) — width follows duration, not the label — while
    ``integration`` (never ran, no parsable start) takes no rail area yet stays
    visible in the header legend (dampened)."""
    _c, _s, html = _detail(tmp_path, _phase_events(RUN_16F39431))
    header = _header(html)

    build_w = _seg_width(header, "build")
    ci_w = _seg_width(header, "ci")
    assert build_w is not None and ci_w is not None and ci_w > 0
    assert build_w / ci_w >= 20, f"build/ci width ratio {build_w / ci_w:.1f} < 20"

    assert 'data-phase-seg="integration"' not in header  # no rail area for a dead phase
    assert "integration" in header                        # still visible in the legend


# --- AC 8/9: waiting segments and the three numbers ----------------------------


def test_waiting_segment_and_the_three_numbers(home, tmp_path):  # noqa: F811
    """AC 8: for 16f39431 the rail shows a waiting segment, and the three numbers are
    work 6084 s, waiting 3289 s, total 9372 s (±2); work + waiting = total up to
    rounding — so the 2 px minimum width (a geometric-only floor) never leaks into
    the sums."""
    _c, _s, html = _detail(tmp_path, _phase_events(RUN_16F39431))
    header = _header(html)

    assert "data-wait-seg" in header, "no waiting segment on the rail"
    work = _total_seconds(header, "work")
    wait = _total_seconds(header, "waiting")
    total = _total_seconds(header, "total")
    assert work is not None and wait is not None and total is not None
    assert abs(work - 6084) <= 2, work
    assert abs(wait - 3289) <= 2, wait
    assert abs(total - 9372) <= 2, total
    assert abs((work + wait) - total) <= 2, (work, wait, total)


def test_a_gapless_run_shows_no_waiting(home, tmp_path):  # noqa: F811
    """AC 9: for 81795e53 (contiguous phases) the rail shows NO waiting segment and
    waiting = 0, while work equals total (3656 s)."""
    _c, _s, html = _detail(tmp_path, _phase_events(RUN_81795E53), run_id="bbbb2222")
    header = _header(html)

    assert "data-wait-seg" not in header, "a gapless run must not show a waiting segment"
    wait = _total_seconds(header, "waiting")
    assert wait is not None and abs(wait) <= 2, wait
    work = _total_seconds(header, "work")
    total = _total_seconds(header, "total")
    assert abs(work - 3656) <= 2 and abs(total - 3656) <= 2, (work, total)


# --- A5: open active phase ------------------------------------------------------


def test_open_active_phase_is_marked_open(home, tmp_path):  # noqa: F811
    """A5: a still-active phase without an ``end`` reaches the right edge and is
    marked open (the ``.tl-bar.bar-running`` convention). ``build`` is active here."""
    _c, _s, html = _detail(tmp_path, phases_lines(), phase="build")
    header = _header(html)

    m = re.search(r'data-phase-seg="build"[^>]*>', header)
    assert m, "the active phase has no rail segment"
    assert "data-open" in m.group(0), "the open active phase is not marked open"


# --- AC 10: the fallback stays the chip row ------------------------------------


def test_run_without_parsable_timestamps_falls_back_to_the_chip_row(home, tmp_path):  # noqa: F811
    """AC 10: a run with no parsable phase timestamps renders the today's chip row —
    not an empty or broken rail, and no second band. The chips (``.phase-…``) are
    present while the rail markers are absent."""
    lines = [rec(1, "run", "start", "R", None, ts=_iso(0), payload=run_start_payload("No phases"))]
    _c, _s, html = _detail(tmp_path, lines, phase="build")
    header = _header(html)

    assert "data-phase-seg" not in header, "a timestamp-less run must not draw a rail"
    assert "data-wait-seg" not in header
    assert re.search(r'class="[^"]*\bphase\b', header), "the chip-row fallback is gone"


# --- AC 12: the per-entry DOM budget is untouched ------------------------------


def test_tree_entry_marker_count_is_unchanged(home, tmp_path):  # noqa: F811
    """AC 12/E4: the timeline lives only in the header with a fixed, phase-count
    number of elements — it adds NO element per tree entry. The ``data-tree-entry``
    marker count still equals the number of trace-tree nodes."""
    lines = _phase_events(RUN_16F39431)
    _c, _s, html = _detail(tmp_path, lines)

    dicts = [x for x in lines if isinstance(x, dict)]

    def walk(nodes):
        return sum(1 + walk(getattr(n, "children", []) or []) for n in nodes)

    assert html.count("data-tree-entry") == walk(build_tree(dicts))


# --- AC 15: the new labels exist in both languages ----------------------------


def _totals_labels(header: str) -> str:
    """The non-numeric chrome of the three-number region under the rail (from the
    first ``data-total`` marker to the end of the header), tags and numbers stripped
    — so only the translatable labels remain. Scoped past the header's language
    switch and title, which are translated independently."""
    i = header.find("data-total=")
    seg = header[i:] if i != -1 else ""
    seg = re.sub(r"<[^>]+>", " ", seg)     # strip tags
    seg = re.sub(r"[\d.]+", " ", seg)      # strip the (language-neutral) numbers
    return re.sub(r"\s+", " ", seg).strip()


def test_timeline_total_labels_are_translated_in_both_languages(home, tmp_path):  # noqa: F811
    """AC 15: the three numbers (work, waiting, total) are LABELLED, and the labels
    are delivered in both languages — the German header's totals labels differ from
    the English one's, while the numbers (``data-seconds``) stay language-neutral."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, RUN_ID, _phase_events(RUN_16F39431), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)

    en = _header(client.get(f"/runs/{slug}/{RUN_ID}?lang=en").text)
    de = _header(client.get(f"/runs/{slug}/{RUN_ID}?lang=de").text)

    for kind in ("work", "waiting", "total"):
        assert f'data-total="{kind}"' in en and f'data-total="{kind}"' in de, kind
        assert _total_seconds(en, kind) == _total_seconds(de, kind), f"{kind} number differs"

    en_labels, de_labels = _totals_labels(en), _totals_labels(de)
    assert en_labels and de_labels, "the three numbers carry no visible labels"
    assert en_labels != de_labels, f"totals labels left untranslated: {en_labels!r}"
