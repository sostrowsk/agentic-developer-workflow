"""Server-side markup checks: DOM weight, landmarks, translated tree-region label."""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.model import build_tree
from tests.gui_app_helpers import (
    RUN_ID,
    _slug_for,
    _trace_section,
    comprehensive_lines,
    write_run,
)


def _client(tmp_path, lines=None):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, lines or comprehensive_lines(), phase="done")
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo)


def _detail(client, slug, **params):
    resp = client.get(f"/runs/{slug}/{RUN_ID}", params=params or None)
    assert resp.status_code == 200
    return resp.text




def _tree_node_count(lines) -> int:
    dicts = [x for x in lines if isinstance(x, dict)]

    def walk(nodes):
        return sum(1 + walk(getattr(n, "children", []) or []) for n in nodes)

    return walk(build_tree(dicts))


# --- AC 11: no DOM growth per tree row (E3) --------------------------------------


def test_tree_column_keeps_one_marker_per_node_and_one_entry_point(home, tmp_path):
    """AC 11/E3: the server-rendered trace column carries exactly one
    ``data-tree-entry`` marker per tree node (no new per-row hull), and at most ONE
    new keyboard entry point (``tabindex``) — not one per row."""
    lines = comprehensive_lines()
    client, slug = _client(tmp_path, lines)
    tree = _trace_section(_detail(client, slug))

    assert tree.count("data-tree-entry") == _tree_node_count(lines), (
        "the number of tree-entry markers diverged from the node count (per-row growth)"
    )
    assert tree.count("tabindex") <= 1, (
        f"the trace column carries {tree.count('tabindex')} server-side tab stops (max 1, E2/E3)"
    )


# --- AC 12: preserved accessibility of the surrounding chrome --------------------


def test_pinned_accessibility_landmarks_are_preserved(home, tmp_path):
    """AC 12: the type filter stays wrapped in a ``<label>``, the search field keeps
    its ``aria-label``, the fold toggle its ``aria-expanded`` and the status glyphs
    their ``aria-hidden`` — all literally present."""
    client, slug = _client(tmp_path)
    html = _detail(client, slug)

    # The Raw type filter is inside a <label>.
    assert re.search(r"<label[^>]*>\s*[^<]*<select[^>]*class=\"raw-type-filter\"", html), (
        "the type filter lost its enclosing <label>"
    )
    # The search field carries an aria-label.
    assert re.search(r"<input[^>]*class=\"raw-search\"[^>]*aria-label=", html), (
        "the search field lost its aria-label"
    )
    # The fold toggle keeps its aria-expanded.
    fold = r"data-fold-toggle[^>]*aria-expanded=|aria-expanded=[^>]*data-fold-toggle"
    assert re.search(fold, html), "the fold toggle lost its aria-expanded"
    # The status glyphs keep aria-hidden.
    assert re.search(r"<span class=\"icon\" aria-hidden=", html), "a status glyph lost aria-hidden"


# --- A6: the tree region has a translated accessible name ------------------------


def test_tree_region_has_a_translated_accessible_name(home, tmp_path):
    """A6/AC 6: the tree region carries an accessible name from the i18n catalog —
    present and DIFFERENT in English and German, sharing one catalog key (identical
    key sets, A6)."""
    from adw.gui.i18n import CATALOG

    client, slug = _client(tmp_path)
    en_label = _region_aria_label(_trace_section(_detail(client, slug, lang="en")))
    de_label = _region_aria_label(_trace_section(_detail(client, slug, lang="de")))

    assert en_label, "the tree region has no accessible name (aria-label) in English"
    assert de_label, "the tree region has no accessible name (aria-label) in German"
    assert en_label != de_label, "the tree-region name is not translated"

    en, de = CATALOG["en"], CATALOG["de"]
    keys = [k for k in en if en[k] == en_label]
    assert keys, "the tree-region name is not sourced from the i18n catalog"
    key = keys[0]
    assert de[key] == de_label, "the German tree-region name does not share the catalog key"


def _region_aria_label(trace_html: str):
    m = re.search(r"aria-label=\"([^\"]+)\"", trace_html)
    return m.group(1) if m else None
