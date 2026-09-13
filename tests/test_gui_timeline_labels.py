"""RED tests for A4 — the Timeline bar's label leaves the proportional bar.

The timeline learns the same rule the phase rail already knows: geometry into the
track, words to a place whose readability does NOT depend on the bar's duration. A
bar becomes pure geometry; its name (``_timeline_bar_label``, the unchanged source)
appears somewhere that is not the width-scaled ``.tl-bar`` element, so a short bar
no longer clips its own name. What stays untouched is the geometry: ``left``/``width``
in percent from the same derivation, the active / waiting / still-running
distinction (``bar-<state>`` / ``bar-running``) and the ``title`` attribute.

The markup is not contractual — these tests read only the observables the plan
pins for the automatable half (``.adw/plan.md`` B2.8/B2.9): each bar's name is
present at a duration-independent place (its geometry element carries no readable
label of its own), the ``title`` stays, and the geometry/state of every bar is
unchanged against the current ``_timeline`` derivation (the golden). The visual
readability / non-overlap proof is the documented browser measurement (B9); the
``title`` attribute alone does not satisfy A4.

Derived from .adw/spec.md (AC 7-8), .adw/contract.yaml
(x-adw-template-behavior.timeline) and .adw/plan.md (B6). RED until the label moves
out of the proportional ``.tl-bar`` element.
"""

import os
import re

from fastapi.testclient import TestClient

from adw.gui.app import _timeline, create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    tab_panel,
    timeline_lines,
    write_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _panel(tmp_path, lines, *, run_id=RUN_ID, name="repo"):
    repo = tmp_path / name
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))
    html = client.get(f"/runs/{_slug_for(repo)}/{run_id}").text
    return tab_panel(html, "timeline")


def _bars_from_model(lines):
    """The bars the current ``_timeline`` derivation yields for ``lines`` — the
    geometry/label/state golden the rendered page must reproduce unchanged."""
    dicts = [x for x in lines if isinstance(x, dict)]
    tl = _timeline(dicts)
    return [b for lane in tl["lanes"] for b in lane["bars"]]


# The proportional geometry element is identified by its ``width:<n>%`` inline style
# (contract: ``left``/``width`` in percent stay the bar's geometry source), NOT by a
# class name — a label element may itself be classed ``tl-bar-…`` and must not be
# mistaken for the bar.
_BAR_SPAN = r'<span[^>]*style="[^"]*width:\s*[\d.]+%[^"]*"[^>]*>'


def _bar_tags(panel: str):
    """Each proportional bar element as ``(opening_tag, inner_html)`` — the
    width-scaled geometry span and whatever text it carries between its own tags."""
    out = []
    for m in re.finditer(rf'({_BAR_SPAN})(.*?)</span>', panel):
        out.append((m.group(1), m.group(2)))
    return out


def _attr(tag: str, name: str):
    m = re.search(rf'{name}="([^"]*)"', tag)
    return m.group(1) if m else None


# --- A4: many short bars in one track --------------------------------------------


def _many_short_bars_lines(n=8):
    """A build lane holding ``n`` short ``agent.run`` bars with DISTINCT names in one
    track, inside a long run span so every bar is a sliver — the clipping case
    (``pytest`` 6 px vs 43 px needed) reproduced with unique labels."""
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Short bars")),
        rec(2, "phase", "start", "PB", "R", sec=1,
            payload={"name": "build", "from_phase": "build"}),
        rec(3, "lane", "start", "L", "PB", sec=2, lane="backend", payload={
            "name": "backend", "branch": "adw/backend", "worktree": "wt",
            "base_sha": None, "ports": {}}),
    ]
    seq = 4
    for i in range(n):
        start = 3 + 2 * i
        lines.append(rec(seq, "agent.run", "start", f"A{i}", "L", sec=start, lane="backend",
                         payload={"agent": f"short-agent-{i:02d}", "prompt": "p",
                                  "system_append": ""}))
        seq += 1
        lines.append(rec(seq, "agent.run", "end", f"A{i}", "L", sec=start + 1, lane="backend",
                         payload={"result_text": "done", "is_error": False}))
        seq += 1
    lines.append(rec(seq, "lane", "end", "L", "PB", sec=57, lane="backend",
                     payload={"completed": True, "gate_iterations": 1, "fix_cycles": 0}))
    seq += 1
    lines.append(rec(seq, "phase", "end", "PB", "R", sec=58,
                     payload={"name": "build", "to_phase": "done"}))
    seq += 1
    lines.append(rec(seq, "run", "end", "R", None, sec=59, payload=run_end_payload("done")))
    return lines


def test_bar_label_leaves_the_proportional_bar_but_stays_readable(home, tmp_path):  # noqa: F811
    """A4: for a track of many short bars every bar's name is present in the panel,
    but NOT inside the width-scaled ``.tl-bar`` element — that element carries no
    readable label of its own, so a short bar cannot clip its name. The ``title``
    attribute stays on the bar (it just does not satisfy A4 alone)."""
    lines = _many_short_bars_lines(8)
    panel = _panel(tmp_path, lines)

    labels = [b["label"] for b in _bars_from_model(lines) if b["label"].startswith("short-agent-")]
    assert len(labels) == 8, labels

    # Every short bar's name appears somewhere in the panel (readable) ...
    for label in labels:
        assert label in panel, f"bar label {label!r} not rendered anywhere"

    # ... but the proportional geometry element carries no readable text of its own.
    tags = _bar_tags(panel)
    assert tags, "no .tl-bar elements found in the timeline panel"
    for open_tag, inner in tags:
        assert inner.strip() == "", (
            f"the width-scaled .tl-bar still carries its label inline: {inner!r}"
        )
        # The bar keeps its title (the geometry element that owns the tooltip).
        assert _attr(open_tag, "title") is not None, "a bar lost its title attribute"


# --- A4: title alone is not the readability answer ------------------------------


def test_a_bar_name_is_rendered_beyond_the_title_attribute(home, tmp_path):  # noqa: F811
    """A4: the ``title`` attribute alone does not meet the readability requirement —
    a bar's name is rendered as visible text OUTSIDE any ``.tl-bar`` element, not only
    as a tooltip. The panel with all ``.tl-bar`` elements removed still carries the
    bar names."""
    lines = _many_short_bars_lines(6)
    panel = _panel(tmp_path, lines)

    stripped = re.sub(rf'{_BAR_SPAN}.*?</span>', " ", panel)
    # Tooltips ride on the removed bars; strip the remaining title="..." too, so what
    # is left is genuine visible text, never an attribute value.
    stripped_text = re.sub(r'title="[^"]*"', " ", stripped)
    for b in _bars_from_model(lines):
        if b["label"].startswith("short-agent-"):
            assert b["label"] in stripped_text, (
                f"{b['label']!r} lives only in the bar / its title — not readable text"
            )


# --- A4: name-to-bar association stays recognizable (no ambiguity) --------------


def _seq_spans(panel: str):
    """The ``data-seq``-bearing SPANS of the timeline panel in document order, each
    classified as the geometry ``bar`` (a ``width:`` percent style) or its ``label``
    (visible text). The per-bar row structure is what makes the pairing observable."""
    out = []
    for m in re.finditer(r'<span[^>]*data-seq="(\d+)"[^>]*>(.*?)</span>', panel):
        tag, seq, inner = m.group(0), m.group(1), m.group(2)
        kind = "bar" if re.search(r"width:\s*[\d.]+%", tag) else "label"
        out.append((kind, seq, inner.strip()))
    return out


def test_each_bar_shares_a_row_with_its_own_label(home, tmp_path):  # noqa: F811
    """A4 (association, Codex P2): with many short bars in one track the name↔bar
    correspondence must stay unambiguous. Each bar and its label sit in the SAME row
    and carry the SAME ``data-seq`` — so the ``data-seq``-bearing spans come in
    adjacent (label, bar) pairs, one per bar, each label carrying exactly that bar's
    name. That is a recognizable association without JavaScript and without relying on
    shared colour or global ordering alone."""
    lines = _many_short_bars_lines(8)
    panel = _panel(tmp_path, lines)

    names = {str(b["seq"]): b["label"] for b in _bars_from_model(lines)}
    spans = _seq_spans(panel)
    assert spans, "no data-seq spans in the timeline panel"
    assert len(spans) == 2 * len(names), (len(spans), len(names))

    for i in range(0, len(spans), 2):
        (k1, s1, t1), (k2, s2, t2) = spans[i], spans[i + 1]
        # The two spans of a row share the bar's seq: one geometry bar, one label.
        assert s1 == s2, f"a bar and its label drifted apart: {s1} vs {s2}"
        assert {k1, k2} == {"bar", "label"}, (k1, k2)
        # The label carries exactly this bar's name (not a neighbour's).
        label_text = t1 if k1 == "label" else t2
        assert label_text == names[s1], (s1, label_text, names.get(s1))


# --- A4: the label itself is never truncated (Codex P2) -------------------------


def test_bar_label_css_never_truncates_the_name(home, tmp_path):  # noqa: F811
    """A4 (readability, Codex P2): moving the name out of the width-scaled bar only
    fixes *duration*-dependent clipping — the label element itself must also show the
    COMPLETE name, never an ellipsis or a hidden overflow that hides a long agent /
    lane / gate name. The label rule must allow the text to wrap (so it stays fully
    visible) and must not clip it. Mirrors ``test_long_tree_labels_wrap_inside_their
    _column`` for the trace column."""
    css = TestClient(create_app(repos=[])).get("/static/app.css").text
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    bodies = [b for sel, b in re.findall(r"([^{}]*)\{([^}]*)\}", css)
              if re.search(r"\.tl-bar-label\b", sel)]
    assert bodies, "no .tl-bar-label rule in app.css"
    joined = " ".join(bodies)

    assert not re.search(r"text-overflow\s*:\s*ellipsis", joined), \
        "the timeline label truncates with an ellipsis"
    assert not re.search(r"white-space\s*:\s*nowrap", joined), \
        "the timeline label is kept on one line (nowrap) and can be clipped"
    # It must be allowed to stay fully visible by wrapping.
    assert re.search(r"overflow-wrap\s*:\s*(anywhere|break-word)", joined) \
        or re.search(r"white-space\s*:\s*normal", joined), \
        "the timeline label is not allowed to wrap, so a long name is clipped"


# --- AC 8: the geometry and state of every bar are unchanged --------------------


def test_bar_geometry_and_state_are_unchanged(home, tmp_path):  # noqa: F811
    """AC 8: for identical inputs every bar's ``left``/``width`` percent and its
    ``bar-<state>`` / ``bar-running`` / ``data-seq`` / ``title`` are unchanged against
    the current ``_timeline`` derivation. A finished run pins active vs waiting; a
    live run additionally pins the open bar's ``bar-running``."""
    for name, lines in (("done", timeline_lines()), ("live", timeline_lines(running=True))):
        panel = _panel(tmp_path, lines, run_id=RUN_ID, name=name)
        rendered = {}
        for open_tag, _inner in _bar_tags(panel):
            seq = _attr(open_tag, "data-seq")
            style = _attr(open_tag, "style") or ""
            m = re.search(r"left:\s*([\d.]+)%;\s*width:\s*([\d.]+)%", style)
            assert seq is not None and m, open_tag
            rendered[seq] = {
                "left": float(m.group(1)), "width": float(m.group(2)),
                "class": _attr(open_tag, "class") or "", "title": _attr(open_tag, "title"),
            }

        for b in _bars_from_model(lines):
            key = str(b["seq"])
            assert key in rendered, f"{name}: bar seq {key} not rendered"
            got = rendered[key]
            assert abs(got["left"] - b["left"]) < 0.01, (name, key, got["left"], b["left"])
            assert abs(got["width"] - b["width"]) < 0.01, (name, key, got["width"], b["width"])
            assert f"bar-{b['state']}" in got["class"], (name, key, got["class"])
            assert ("bar-running" in got["class"]) == bool(b["running"]), (name, key)
            assert got["title"] is not None, f"{name}: bar {key} lost its title"
