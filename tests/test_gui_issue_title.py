"""RED tests for A3 — the Issue column becomes a one-line title (AC 7, 8).

The list must show a single-line, truncated TITLE derived from the raw issue text
(not raw markdown wrapped over many lines), with the full raw text reachable via
the cell's ``title`` attribute. The derivation (spec "Normative Definitionen"):

1. the first ``#`` heading among the first twelve lines (its text without the
   ``#``); a heading that only names "Issue" (``^issue\b``, case-insensitive) is
   skipped; a heading past line twelve does not win;
2. otherwise the first non-empty line;
3. a leading ``ADW-Issue:`` / ``Issue:`` is stripped;
4. longer than 90 chars → truncated to 89 + ``…``;
5. empty / missing → an empty cell, never a placeholder.

It is text processing, not rendering (E7): neither the cell nor the ``title`` is
interpreted as markdown/HTML. The API ``issue`` field is unchanged (truncated raw)
and is NOT the source of the full-text ``title`` attribute.

*Marker policy (declared here, as .adw/plan.md leaves markup to the
implementation): the title cell keeps today's ``issue`` CSS class; the full raw
text lives in that cell's ``title`` attribute.* Derived from .adw/spec.md (AC 7,
8), .adw/contract.yaml (x-adw-template-behavior.issue_title) and .adw/plan.md (B4).
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
    write_run,
)


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _issue_run(issue, run_id):
    """A minimal finished run carrying ``issue`` verbatim in its ``run`` start."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "run", "end", "R", None, sec=1,
            payload={"status": "done", "totals": {"duration": 1.0}}),
    ]


def _row(html, run_id):
    for chunk in html.split("<tr"):
        if run_id in chunk:
            return chunk.split("</tr>")[0]
    return ""


def _issue_cell(row):
    """The ``<td ...class="issue"...>…</td>`` of a row (open tag + inner)."""
    m = re.search(r'<td[^>]*class="[^"]*\bissue\b[^"]*"[^>]*>(.*?)</td>', row, re.S)
    assert m, f"no issue cell found in row: {row!r}"
    return m.group(0), m.group(1)


def _cell_text(inner):
    """The visible text of the cell inner (tags stripped, whitespace collapsed)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()


def _title_attr(cell):
    m = re.search(r'title="([^"]*)"', cell)
    return m.group(1) if m else None


def _render(tmp_path, runs):
    """Write each ``(run_id, issue)`` run and return the rendered list HTML."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for run_id, issue in runs:
        write_run(repo, run_id, _issue_run(issue, run_id), phase="done", issue=issue)
    return TestClient(create_app(repos=[str(repo)])).get("/").text


# --- AC 7: heading selection ---------------------------------------------------


def test_title_from_heading_skips_issue_heading_and_ignores_late_heading(home, tmp_path):  # noqa: F811
    """AC 7 (1): a ``#`` heading in the first twelve lines becomes the title without
    the ``#``; an "Issue"-only heading is skipped so the next heading wins; a heading
    only appearing after line twelve does not win (the first non-empty line does)."""
    plain = "# My Feature\n\nbody line describing the work"
    skip = "# Issue (redesign)\n# The Real Title\n\nbody"
    late = "\n".join([f"context line {i}" for i in range(1, 13)] + ["# Late Heading", "more"])

    html = _render(tmp_path, [
        ("aaaa1111", plain),
        ("bbbb2222", skip),
        ("cccc3333", late),
    ])

    _cell, inner = _issue_cell(_row(html, "aaaa1111"))
    assert _cell_text(inner) == "My Feature"
    assert "#" not in _cell_text(inner)

    _cell, inner = _issue_cell(_row(html, "bbbb2222"))
    assert _cell_text(inner) == "The Real Title"

    _cell, inner = _issue_cell(_row(html, "cccc3333"))
    text = _cell_text(inner)
    assert text == "context line 1"           # first non-empty line wins
    assert "Late Heading" not in text         # a heading past line 12 does not win


# --- AC 7: first-line fallback and prefix stripping ----------------------------


def test_title_falls_back_to_first_line_and_strips_issue_prefix(home, tmp_path):  # noqa: F811
    """AC 7 (2, 3): without a heading the first non-empty line is used, and a
    leading ``ADW-Issue:`` / ``Issue:`` is removed from the result."""
    html = _render(tmp_path, [
        ("aaaa1111", "\n\nADW-Issue: Do the important thing\nmore detail"),
        ("bbbb2222", "Issue: Fix the parser\nnext line"),
        ("cccc3333", "Just a plain first line\nsecond line"),
    ])

    assert _cell_text(_issue_cell(_row(html, "aaaa1111"))[1]) == "Do the important thing"
    assert _cell_text(_issue_cell(_row(html, "bbbb2222"))[1]) == "Fix the parser"
    assert _cell_text(_issue_cell(_row(html, "cccc3333"))[1]) == "Just a plain first line"


# --- AC 7: truncation and empty cell -------------------------------------------


def test_long_title_truncated_and_empty_issue_is_blank(home, tmp_path):  # noqa: F811
    """AC 7 (4, 5): a title longer than 90 chars is cut to 89 + ``…``; an empty
    issue text yields an empty cell (never a placeholder)."""
    long_heading = "# " + ("A" * 120)
    html = _render(tmp_path, [
        ("aaaa1111", long_heading),
        ("bbbb2222", "   \n\n   "),
    ])

    long_text = _cell_text(_issue_cell(_row(html, "aaaa1111"))[1])
    assert long_text.endswith("…")
    assert len(long_text) == 90               # 89 chars + the ellipsis
    assert long_text[:89] == "A" * 89

    empty_text = _cell_text(_issue_cell(_row(html, "bbbb2222"))[1])
    assert empty_text == ""


# --- AC 8: single line + full raw text in the (escaped) title attribute ---------


def test_issue_cell_is_single_line_with_full_escaped_raw_in_title(home, tmp_path):  # noqa: F811
    """AC 8 / E7: the cell renders one line; the FULL raw text (well past the API
    ``issue`` truncation) sits in the ``title`` attribute, HTML-escaped, never
    interpreted as markdown/HTML — so it is not sourced from the truncated field."""
    tail = "ZZTAILSENTINELZZ"
    # A long, multi-line raw issue with HTML-special characters; the sentinel sits
    # far beyond the 120-char API truncation so a truncated source could not carry it.
    issue = (
        '# Heading with <b>tags</b> & "quotes"\n'
        + ("filler body line to push the tail well past 120 characters " * 4)
        + "\n" + tail
    )
    html = _render(tmp_path, [("aaaa1111", issue)])
    cell, inner = _issue_cell(_row(html, "aaaa1111"))

    # One visible line (no newline leaked through into the rendered cell text).
    assert "\n" not in _cell_text(inner)
    assert "\n" not in inner.strip()

    # The full raw text (including the far-tail sentinel) is in the title attribute.
    title = _title_attr(cell)
    assert title is not None
    assert tail in title, "the title attribute is not the full raw text (truncated?)"

    # E7: the raw markdown/HTML is escaped, not rendered as live markup.
    assert "<b>" not in cell                      # the issue's <b> is not live markup
    assert ("&lt;b&gt;" in cell) or ("&lt;b&gt;" in title)
    assert ("&amp;" in cell) or ("&amp;" in title)
    assert ("&quot;" in title) or ("&#34;" in title)


def test_issue_cell_css_clips_to_one_rendered_line(home, tmp_path):  # noqa: F811
    """AC 8 (rendered): the issue cell is styled to occupy ONE rendered line —
    `white-space: nowrap`, `overflow: hidden`, `text-overflow: ellipsis` — so a long
    title clips with an ellipsis instead of wrapping across rows (removing newlines
    from the source is not enough on a fixed-layout table). Asserted on the served
    stylesheet, the level the repo pins layout at."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, "aaaa1111", _issue_run("A run", "aaaa1111"), phase="done", issue="A run")
    css = TestClient(create_app(repos=[str(repo)])).get("/static/app.css").text

    m = re.search(r"table\.run-list\s+\.issue\s*\{([^}]*)\}", css)
    assert m, "no dedicated .issue cell rule in the run-list stylesheet"
    block = re.sub(r"\s+", " ", m.group(1))
    assert "white-space: nowrap" in block
    assert "overflow: hidden" in block
    assert "text-overflow: ellipsis" in block
