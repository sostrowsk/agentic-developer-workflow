"""Tests for no horizontal overflow at page level."""

from fastapi.testclient import TestClient

from adw.gui.app import create_app


def _css(tmp_path):
    client = TestClient(create_app(repos=[str(tmp_path)]))
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    return resp.text.replace(" ", "").lower()


def test_css_sets_shrink_limits_on_layout_containers(home, tmp_path):
    """F1: grid/flex containers get a shrink limit (``min-width: 0``) so a wide
    child cannot widen the viewport."""
    css = _css(tmp_path)
    assert "min-width:0" in css


def test_css_contains_page_level_overflow_containment(home, tmp_path):
    """F2: the page never scrolls horizontally — a page-level rule contains the
    overflow (``overflow-x: hidden`` or a ``max-width: 100%`` clamp)."""
    css = _css(tmp_path)
    assert "overflow-x:hidden" in css or "max-width:100%" in css
