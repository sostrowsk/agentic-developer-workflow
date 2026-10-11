"""Tests for the run-level Artifacts tab."""


from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (
    LARGE_ARTIFACT_TAIL,
    RUN_ID,
    _slug_for,
    large_artifact_body,
    tab_panel,
    write_artifacts_run,
)


def _artifacts_panel(tmp_path, **kw):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_artifacts_run(repo, RUN_ID, **kw)
    client = TestClient(create_app(repos=[str(repo)]))
    resp = client.get(f"/runs/{_slug_for(repo)}/{RUN_ID}")
    assert resp.status_code == 200
    return resp.text, tab_panel(resp.text, "artifacts")


def test_tab_lists_present_and_missing_artifacts(home, tmp_path):
    """B1/B3/B6: the tab lists the whitelisted artifacts and the drafts. A present
    artifact is a fetch anchor; a missing one (``escalation.md`` on a non-escalating
    run, and a draft left only as a ``*.FAILED`` marker) is listed by name and
    marked missing, never as a fetch anchor and never an error."""
    _, panel = _artifacts_panel(
        tmp_path, drafts=("spec.claude.md",), failed_markers=("spec.codex.FAILED",)
    )

    assert panel
    # Present top-level artifacts are fetchable content anchors.
    for name in ("issue.md", "spec.md", "plan.md", "contract.yaml", "followups.md"):
        assert f'data-artifact="{name}"' in panel, name
    assert 'data-artifact="spec.claude.md"' in panel  # the present draft

    # Missing artifacts are named and marked missing, with no fetch anchor.
    assert "escalation.md" in panel  # listed even though absent
    assert 'data-artifact="escalation.md"' not in panel
    assert "spec.codex.md" in panel  # the FAILED-only draft is listed
    assert 'data-artifact="spec.codex.md"' not in panel
    assert 'data-artifact="spec.codex.FAILED"' not in panel  # the marker has no name
    assert "missing" in panel.lower()


def test_dual_authored_artifact_presents_both_drafts_against_synthesis(home, tmp_path):  # noqa: E501
    """B2: for a dual-authored artifact the two drafts (``spec.claude.md``,
    ``spec.codex.md``) are presented against the synthesis (``spec.md``) so a reader
    can compare what each author contributed."""
    _, panel = _artifacts_panel(tmp_path)

    for name in ("spec.md", "spec.claude.md", "spec.codex.md"):
        assert name in panel, name
    # Both authors are addressable as their own fetch anchors.
    assert 'data-artifact="spec.claude.md"' in panel
    assert 'data-artifact="spec.codex.md"' in panel


def test_large_artifact_initial_render_is_bounded_and_reachable(home, tmp_path):
    """B7/E8: opening a large artifact does not inline its content into the initial
    page — the served HTML does not grow with the artifact and does not carry the
    tail sentinel — while the full content stays reachable through the route (a
    fetch anchor + the client's artifact loader)."""
    body = large_artifact_body()
    html, panel = _artifacts_panel(tmp_path, large_name="plan.md", large_body=body)

    assert 'data-artifact="plan.md"' in panel        # reachable via the route
    assert LARGE_ARTIFACT_TAIL not in html           # not inlined into the page
    assert len(html) < len(body) // 2                # the initial render is bounded

    repo = tmp_path / "repo"
    js = TestClient(create_app(repos=[str(repo)])).get("/static/app.js").text
    assert "artifacts" in js  # the client loads artifact content from the route
