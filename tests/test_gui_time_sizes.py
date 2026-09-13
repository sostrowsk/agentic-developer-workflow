"""RED tests for A2 — three named time sizes with one vocabulary (AC 5, 6, 15).

A run has three distinct time sizes that today's UI conflates:

* **Work** = sum of ``totals.duration`` over the closed ``run`` spans (real work,
  from A1).
* **Phase time** = sum of the phase spans (the coloured area of the Brief-1 rail);
  NOT work — the two diverge 7–25× when a phase span outlives an interruption.
* **Total** = first phase start to last phase end; **Waiting** = Total − Phase time.

The additive summary fields (``work_seconds``, ``phase_seconds``, ``wait_seconds``,
``total_seconds``) are pinned by the contract and exercised through the API. The
run-detail head must NAME them with the binding vocabulary and must never label
phase time as "Work"/"Arbeit" (E5).

*Marker policy (declared here; the concrete markup is the implementation's choice,
mirroring tests/test_gui_run_timeline.py): the run header shows each time size as a
label directly followed by its formatted value, so tag-stripped text reads
``<Label> <value>``.* Derived from .adw/spec.md (AC 5, 6, 15), .adw/contract.yaml
(x-adw-reference.vocabulary, RunSummary) and .adw/plan.md (B2, B3, B7).
"""

import os
import re
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    ts_at,
    write_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _by_id(entries, run_id):
    return next((e for e in entries if e.get("run_id") == run_id), None)


def _header_raw(html: str) -> str:
    """The run header markup (untouched), for reading the machine-readable
    ``data-total``/``data-seconds`` markers."""
    i = html.find("<header")
    j = html.find("</header>", i)
    assert i != -1 and j != -1, "run header not found"
    return html[i:j]


def _header_text(html: str) -> str:
    """The run header, tags stripped and whitespace collapsed — so a label and its
    value read as adjacent words. Scoped to the header so the Timeline TAB's own
    numbers are never counted."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _header_raw(html))).strip()


def _header_seconds(header: str, kind: str):
    """The ``data-seconds`` of the ``data-total="{kind}"`` marker (attribute order is
    the implementation's choice), or None."""
    m = re.search(r'data-total="' + kind + r'"[^>]*data-seconds="([\d.]+)"', header)
    if m is None:
        m = re.search(r'data-seconds="([\d.]+)"[^>]*data-total="' + kind + r'"', header)
    return float(m.group(1)) if m else None


_BASE = datetime(2026, 8, 5, 14, 0, 0)


def _iso(offset: float) -> str:
    """A fractional ISO-UTC timestamp ``offset`` seconds after the base — so the
    implementation must keep real (non-integer) span seconds, not round them."""
    return (_BASE + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _phase(seq, sid, name, start_sec, end_sec):
    # ts_at (timedelta-based) yields valid ISO timestamps for offsets beyond 59 s,
    # unlike the minute-bound ``sec=`` helper — the phase spans here reach thousands
    # of seconds and must stay parsable.
    return [
        rec(seq, "phase", "start", sid, "R", ts=ts_at(start_sec),
            payload={"name": name, "from_phase": name}),
        rec(seq + 1, "phase", "end", sid, "R", ts=ts_at(end_sec),
            payload={"name": name, "to_phase": "done"}),
    ]


# --- AC 6: Phase time + Waiting = Total ----------------------------------------


def test_phase_decomposition_sums_to_total(home, tmp_path):  # noqa: F811
    """AC 6: phases at 0–100 s and 300–500 s give phase_seconds 300, wait_seconds
    200 and total_seconds 500, and the decomposition adds up (the gaps do not
    overlap)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [rec(1, "run", "start", "R", None, ts=ts_at(0), payload=run_start_payload("Decompose"))]
    lines += _phase(2, "S", "spec", 0, 100)
    lines += _phase(4, "PL", "plan", 300, 500)
    lines.append(rec(6, "run", "end", "R", None, ts=ts_at(500),
                     payload=run_end_payload("done", 400, 1.0, 10)))
    write_run(repo, RUN_ID, lines, phase="done", issue="Decompose")

    entry = _by_id(TestClient(create_app(repos=[str(repo)])).get("/api/runs").json(), RUN_ID)
    assert entry["phase_seconds"] == 300
    assert entry["wait_seconds"] == 200
    assert entry["total_seconds"] == 500
    assert abs((entry["phase_seconds"] + entry["wait_seconds"]) - entry["total_seconds"]) <= 1


# --- AC 5: work and phase time are separate sizes -------------------------------


def test_work_and_phase_time_diverge_when_a_phase_outlives_a_gap(home, tmp_path):  # noqa: F811
    """AC 5: when a phase span straddles an interruption, phase_seconds is far
    larger than the real work (the summed ``totals.duration``). Both are reported
    and they differ — phase time is not the work number."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # One phase spans 0–5000 s (phase time 5000) but the run only worked 100 s.
    lines = [rec(1, "run", "start", "R", None, ts=ts_at(0), payload=run_start_payload("Divergent"))]
    lines += _phase(2, "PB", "build", 0, 5000)
    lines.append(rec(4, "run", "end", "R", None, ts=ts_at(5000),
                     payload=run_end_payload("done", 100, 1.0, 10)))
    write_run(repo, RUN_ID, lines, phase="done", issue="Divergent")

    entry = _by_id(TestClient(create_app(repos=[str(repo)])).get("/api/runs").json(), RUN_ID)
    assert entry["work_seconds"] == 100
    assert entry["phase_seconds"] == 5000
    assert entry["work_seconds"] != entry["phase_seconds"]


def test_detail_head_labels_work_and_phase_time_distinctly(home, tmp_path):  # noqa: F811
    """AC 5 / E5: the run header names Work, Phase time, Waiting and Total; the real
    work value sits under "Work" and the (larger) phase-time value under "Phase
    time" — phase time is NEVER labeled "Work". Two phases with a gap so all four
    numbers format distinctly: work 100 s (1m 40s), phase time 5000 s (1h 23m 20s),
    waiting 3000 s (50m 0s), total 8000 s (2h 13m 20s)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [rec(1, "run", "start", "R", None, ts=ts_at(0),
                 payload=run_start_payload("Vocabulary"))]
    lines += _phase(2, "S", "spec", 0, 2000)      # phase time 2000
    lines += _phase(4, "PL", "plan", 5000, 8000)  # + 3000 = 5000 phase; gap 2000..5000 = 3000
    lines.append(rec(6, "run", "end", "R", None, ts=ts_at(8000),
                     payload=run_end_payload("done", 100, 1.0, 10)))  # real work 100 s
    write_run(repo, RUN_ID, lines, phase="done", issue="Vocabulary")

    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    head = _header_text(client.get(f"/runs/{slug}/{RUN_ID}?lang=en").text)

    # Every size is present and labeled with the binding English vocabulary.
    for label in ("Work", "Phase time", "Waiting", "Total"):
        assert label in head, f"missing {label!r} in the run header"

    # The real work value follows "Work"; the phase-time value follows "Phase time".
    assert re.search(r"Work\W{0,4}1m 40s", head), head
    assert re.search(r"Phase time\W{0,4}1h 23m 20s", head), head
    # E5: phase time is never the number labeled "Work".
    assert not re.search(r"\bWork\W{0,4}1h 23m 20s", head), "phase time labeled as Work"


def test_detail_head_named_metrics_come_from_the_summary_for_active_phase(home, tmp_path):  # noqa: F811
    """P2: the header's named numbers are the SUMMARY metrics, not the presentation
    timeline. With a closed phase (0–100) and an active phase from 200, the summary
    reports phase_seconds=100 (the active phase is excluded); the header must show
    that 100 too — the timeline geometry would instead count the active phase to the
    page-build instant. Work is empty because no run span completed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [rec(1, "run", "start", "R", None, ts=ts_at(0),
                 payload=run_start_payload("Active header"))]
    lines += _phase(2, "S", "spec", 0, 100)                    # closed → phase time 100
    lines.append(rec(4, "phase", "start", "PB", "R", ts=ts_at(200),
                     payload={"name": "build", "from_phase": "build"}))  # active, open
    write_run(repo, RUN_ID, lines, phase="build", issue="Active header")

    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    api = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["run"]
    assert api["phase_seconds"] == 100  # the active phase is excluded from phase time

    header = _header_raw(client.get(f"/runs/{slug}/{RUN_ID}").text)
    # The header's Phase-time number is the summary's phase_seconds (100), not the
    # timeline geometry (which would also count the active phase to now).
    assert _header_seconds(header, "work") == api["phase_seconds"]
    assert _header_seconds(header, "work") == 100
    # Work (the real work) is empty — the run has no completed span.
    assert 'data-total="realwork"' not in header


# --- AC 15: the vocabulary exists in both languages ----------------------------


def test_time_vocabulary_present_in_both_languages():
    """AC 15 / E5: the catalog carries the FULL binding time vocabulary in both
    languages — Work/Arbeit, Phase time/Phasenzeit, Waiting/Wartezeit and
    Total/Gesamt — and the four labels are distinct in each language (phase time is
    never labeled as work)."""
    from adw.gui.i18n import CATALOG

    en_values = set(CATALOG["en"].values())
    de_values = set(CATALOG["de"].values())

    assert {"Work", "Phase time", "Waiting", "Total"} <= en_values, en_values
    assert {"Arbeit", "Phasenzeit", "Wartezeit", "Gesamt"} <= de_values, de_values
    # Each size has its OWN word — four distinct labels per language (E5).
    assert len({"Work", "Phase time", "Waiting", "Total"}) == 4
    assert len({"Arbeit", "Phasenzeit", "Wartezeit", "Gesamt"}) == 4


def test_work_metric_uses_the_work_vocabulary_not_duration(home, tmp_path):  # noqa: F811
    """P3/AC 15: the run's work metric is labeled with the binding Work/Arbeit
    vocabulary in the run list, not the old "Duration"/"Dauer" — the same size uses
    the same word everywhere."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [rec(1, "run", "start", "R", None, ts=ts_at(0), payload=run_start_payload("A run"))]
    lines += _phase(2, "PB", "build", 0, 100)
    lines.append(rec(4, "run", "end", "R", None, ts=ts_at(100),
                     payload=run_end_payload("done", 100, 1.0, 10)))
    write_run(repo, RUN_ID, lines, phase="done", issue="A run")

    client = TestClient(create_app(repos=[str(repo)]))
    en = client.get("/").text
    de = client.get("/?lang=de").text
    assert "Work" in en and "Duration" not in en
    assert "Arbeit" in de and "Dauer" not in de


# --- P2: summary time sizes follow the NORMATIVE definitions, not the timeline ---


def test_open_active_phase_is_excluded_from_phase_seconds(home, tmp_path):  # noqa: F811
    """P2: an open ACTIVE phase never contributes to phase_seconds (only phases with
    a parsable start AND end do), but it does extend total_seconds; the phase's API
    ``end`` stays null."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [rec(1, "run", "start", "R", None, ts=ts_at(0), payload=run_start_payload("Live"))]
    lines += _phase(2, "S", "spec", 0, 100)                    # closed → contributes 100
    lines.append(rec(4, "phase", "start", "PB", "R", ts=ts_at(200),
                     payload={"name": "build", "from_phase": "build"}))  # open, active
    # no run end → the run is still running, build phase is active/open
    write_run(repo, RUN_ID, lines, phase="build", issue="Live")

    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    entry = _by_id(client.get("/api/runs").json(), RUN_ID)
    assert entry["phase_seconds"] == 100  # excludes the open build phase (not 100 + elapsed)
    assert entry["total_seconds"] is not None
    assert entry["total_seconds"] > entry["phase_seconds"]  # the open phase extends total

    detail = client.get(f"/api/runs/{slug}/{RUN_ID}").json()
    build = next(p for p in detail["phases"] if p["name"] == "build")
    assert build["end"] is None  # the page-build instant is never written back as an end


def test_closed_zero_duration_phase_is_zero_not_null(home, tmp_path):  # noqa: F811
    """P2: a closed zero-length phase (start == end) yields genuine zeros, not null —
    the presentation timeline rejected a zero-length span, the summary must not."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [
        rec(1, "run", "start", "R", None, ts=ts_at(0), payload=run_start_payload("Zero phase")),
        rec(2, "phase", "start", "S", "R", ts=ts_at(50),
            payload={"name": "spec", "from_phase": "spec"}),
        rec(3, "phase", "end", "S", "R", ts=ts_at(50),
            payload={"name": "spec", "to_phase": "done"}),
        rec(4, "run", "end", "R", None, ts=ts_at(60), payload=run_end_payload("done", 10, 1.0, 10)),
    ]
    write_run(repo, RUN_ID, lines, phase="done", issue="Zero phase")

    entry = _by_id(TestClient(create_app(repos=[str(repo)])).get("/api/runs").json(), RUN_ID)
    assert entry["phase_seconds"] == 0
    assert entry["total_seconds"] == 0
    assert entry["wait_seconds"] == 0


def test_fractional_phase_timestamps_are_not_rounded(home, tmp_path):  # noqa: F811
    """P2: fractional span seconds are preserved in the summary (the timeline rounds
    for drawing; the metrics must not)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [
        rec(1, "run", "start", "R", None, ts=_iso(0), payload=run_start_payload("Fractional")),
        rec(2, "phase", "start", "S", "R", ts=_iso(0.0),
            payload={"name": "spec", "from_phase": "spec"}),
        rec(3, "phase", "end", "S", "R", ts=_iso(100.5),
            payload={"name": "spec", "to_phase": "plan"}),
        rec(4, "phase", "start", "PL", "R", ts=_iso(200.0),
            payload={"name": "plan", "from_phase": "plan"}),
        rec(5, "phase", "end", "PL", "R", ts=_iso(350.25),
            payload={"name": "plan", "to_phase": "done"}),
        rec(6, "run", "end", "R", None, ts=_iso(350.25),
            payload=run_end_payload("done", 250.75, 1.0, 10)),
    ]
    write_run(repo, RUN_ID, lines, phase="done", issue="Fractional")

    entry = _by_id(TestClient(create_app(repos=[str(repo)])).get("/api/runs").json(), RUN_ID)
    assert abs(entry["phase_seconds"] - 250.75) < 1e-6   # 100.5 + 150.25, not rounded
    assert abs(entry["total_seconds"] - 350.25) < 1e-6
    assert abs(entry["wait_seconds"] - 99.5) < 1e-6
