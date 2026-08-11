"""RED tests for Aufgabe A — the run-level Timeline tab.

The run detail view gains a run-level ``Timeline`` tab in the §7.2 tab set
``Trace`` (default) · ``Timeline`` · ``Artifacts`` · ``Raw``. The timeline shows
horizontal swimlanes (orchestrator, spec, plan, per lane, codex, CI) derived from
the run's ALREADY-loaded event log — the same events Trace uses — with active vs.
waiting (CI polling, gate runtime) sections distinguishable, a header of total
duration / cost / tokens per model, bars carrying their target node's ``data-seq``
for click-to-Trace navigation, a still-running span drawn to the current edge, and
a clear "no trace" indication for a run with no event log. Drawn with own means
only — no charting library, no third-party asset (A7, E5).

Derived from .adw/spec.md (A1–A8), .adw/contract.yaml
(x-adw-template-behavior.timeline / .run_level_tabs) and .adw/plan.md §4. Markup
is not contractual: assertions scope to the timeline panel and check observable
content (strand labels, the semantic "waiting" distinction, node ``data-seq``),
attesting wiring at source level — the visual/navigation checks are documented
manual. RED until the Timeline tab exists.
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_start_payload,
    simple_run_lines,
    tab_panel,
    timeline_lines,
    write_run,
    write_state_only_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _detail_html(tmp_path, lines, *, run_id=RUN_ID, phase="done"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    resp = client.get(f"/runs/{slug}/{run_id}")
    assert resp.status_code == 200
    return resp.text


def test_run_level_tab_set_is_trace_timeline_artifacts_raw_default_trace(home, tmp_path):  # noqa: F811,E501
    """A1: the run-level tab set is Trace (default) · Timeline · Artifacts · Raw,
    in that order, with Trace the default active tab."""
    html = _detail_html(tmp_path, timeline_lines())

    for label in ("Trace", "Timeline", "Artifacts", "Raw"):
        assert f">{label}<" in html or f'"{label}"' in html, label
    order = [html.find(f'data-tab="{t}"') for t in ("trace", "timeline", "artifacts", "raw")]
    assert all(i != -1 for i in order), order
    assert order == sorted(order)  # Timeline and Artifacts sit between Trace and Raw
    # Trace stays the default active tab.
    assert re.search(r'class="tab-btn active"[^>]*data-tab="trace"', html)


def test_timeline_lanes_match_present_strands(home, tmp_path):  # noqa: F811
    """A2: one swimlane per strand PRESENT in the run — orchestrator, spec, plan,
    the build lane (backend), codex, CI — and a strand ABSENT from a run contributes
    no lane and is never an error. A full run shows all strands; a minimal run (no
    lane, no codex, no CI) shows the timeline without them and without a 5xx."""
    full = tab_panel(_detail_html(tmp_path, timeline_lines()), "timeline").lower()
    assert full  # the timeline panel exists
    for strand in ("orchestrator", "spec", "plan", "backend", "codex"):
        assert strand in full, strand
    assert re.search(r"\bci\b", full)  # the CI swimlane

    minimal = tab_panel(
        _detail_html(tmp_path, simple_run_lines("Minimal"), run_id="cccc3333"), "timeline"
    ).lower()
    assert minimal
    assert "codex" not in minimal
    assert not re.search(r"\bci\b", minimal)  # no CI strand for a run that never polled


def test_active_and_waiting_sections_are_distinguishable(home, tmp_path):  # noqa: F811
    """A3: active vs. waiting sections are visually distinguishable; waiting covers
    at least CI polling and gate runtime. Attested at source level: the timeline
    marks waiting intervals with the semantic "waiting" token and the stylesheet
    renders it distinctly (the visual check itself is documented-manual)."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, timeline_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)

    panel = tab_panel(client.get(f"/runs/{slug}/{RUN_ID}").text, "timeline").lower()
    assert "waiting" in panel  # CI polling / gate runtime marked as waiting

    css = client.get("/static/app.css").text.lower()
    assert "waiting" in css  # the distinction is carried into the stylesheet


def test_header_shows_duration_cost_and_tokens_per_model(home, tmp_path):  # noqa: F811
    """A4: the timeline header shows total duration, total cost and tokens per
    model when the log carries them."""
    panel = tab_panel(_detail_html(tmp_path, timeline_lines()), "timeline")

    assert "30s" in panel          # total duration (30.0 s -> "30s")
    assert "$0.42" in panel        # total cost
    assert "opus-4.8" in panel     # the model
    assert "1500" in panel         # tokens for that model (1000 in + 500 out)


def test_live_run_header_shows_elapsed_duration(home, tmp_path):  # noqa: F811
    """A4 (root cause): the timeline header shows total duration for a LIVE run too
    — the elapsed time from the run start to the current timeline endpoint — not an
    empty value just because no run-end record exists yet. Live and finished runs
    stay observably consistent."""
    panel = tab_panel(_detail_html(tmp_path, timeline_lines(running=True)), "timeline")

    m = re.search(r'class="tl-duration">([^<]*)</span>', panel)
    assert m, panel[:500]
    # The rendered duration carries a real elapsed value (digits), not just its
    # label (an empty duration would render the label alone).
    assert re.search(r"\d", m.group(1)), m.group(1)


def test_dry_run_header_renders_cost_and_tokens_empty_never_zero(home, tmp_path):  # noqa: F811
    """A4 / GUI-SPEC §12: under a dry run the mocks produce no ``usage``, so the
    header renders cost and tokens EMPTY — never a false 0 and never a ``$0``."""
    panel = tab_panel(_detail_html(tmp_path, timeline_lines(dry_run=True)), "timeline")

    assert "30s" in panel  # duration is still known
    assert "$" not in panel
    assert "1500" not in panel
    low = panel.lower()
    assert "tokens: 0" not in low and "0 tokens" not in low


def test_bars_carry_target_node_data_seq_for_trace_navigation(home, tmp_path):  # noqa: F811
    """A5: a bar carries its target node's ``data-seq`` so a click can select that
    node in Trace (reusing the existing data-seq selection path; the navigation
    itself is a documented-manual check). The agent.run node's seq (8) is a bar."""
    panel = tab_panel(_detail_html(tmp_path, timeline_lines()), "timeline")

    assert 'data-seq="8"' in panel   # the agent.run bar targets its trace node
    assert 'data-seq="15"' in panel  # the ci.wait bar targets its trace node


def test_running_span_renders_to_current_edge_same_path_as_finished(home, tmp_path):  # noqa: F811
    """A6: a still-running span's bar is drawn (not omitted); a live run and a
    finished run render through the same path without a 5xx. The live run marks
    its open span running; the finished run has no running span."""
    live = tab_panel(_detail_html(tmp_path, timeline_lines(running=True)), "timeline")
    done = tab_panel(_detail_html(tmp_path, timeline_lines(), run_id="bbbb2222"), "timeline")

    assert live and done
    assert "running" in live.lower()      # the open ci.wait/run bar is drawn as running
    assert "running" not in done.lower()  # a finished run has no open span


def test_open_span_extends_to_current_timeline_endpoint(home, tmp_path):  # noqa: F811
    """A6 (root cause): a running span extends to the CURRENT timeline endpoint
    (now), not the timestamp of the newest logged event. When the open span is
    itself the newest event, ending it at the last event would degenerate its bar
    to a sliver at the right edge. Here the open ``ci.wait`` (seq 4) is the newest
    event yet its bar must span essentially the whole track to the current edge."""
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Open last")),
        rec(2, "phase", "start", "S", "R", sec=1, payload={"name": "spec", "from_phase": "spec"}),
        rec(3, "phase", "end", "S", "R", sec=2, payload={"name": "spec", "to_phase": "plan"}),
        rec(4, "ci.wait", "start", "CI", "R", sec=3,
            payload={"provider": "gitlab", "pipeline_ref": "b"}),
    ]
    panel = tab_panel(_detail_html(tmp_path, lines), "timeline")

    m = re.search(r'data-seq="4"[^>]*style="left:([0-9.]+)%;width:([0-9.]+)%"', panel)
    assert m, panel[:800]
    left, width = float(m.group(1)), float(m.group(2))
    assert width >= 90.0         # the open bar spans to the current edge...
    assert left + width >= 99.0  # ...reaching the right edge, not a 0.5% sliver


def test_no_event_log_shows_empty_timeline_with_no_trace_indication(home, tmp_path):  # noqa: F811
    """A8: a run with no event log shows the timeline empty with a clear "no trace"
    indication, never a 5xx (parallel to the existing no-trace handling)."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_state_only_run(repo, "8f8dc4ff", phase="done", issue="Legacy run")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)

    resp = client.get(f"/runs/{slug}/8f8dc4ff")
    assert resp.status_code == 200
    assert "no trace" in tab_panel(resp.text, "timeline").lower()


def test_timeline_uses_own_means_only_no_charting_library(home, tmp_path):  # noqa: F811
    """A7/E5: the timeline is drawn with own means only — no charting library and
    no third-party frontend asset. The page pulls in only the packaged app.css /
    app.js; no external <script src>/<link href> to a CDN appears."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, timeline_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)

    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    assert "http://" not in html and "https://" not in html  # no external asset/CDN
    for lib in ("d3.", "chart.js", "chartjs", "plotly", "mermaid", "//cdn"):
        assert lib not in html.lower(), lib
