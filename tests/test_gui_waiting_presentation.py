"""RED tests for A4 / Contract R4 — presentation and labels of the new states.

``waiting`` (tree node), ``awaiting_approval`` (run status) and ``awaiting``
(phase) are rendered visually distinguishable from ``running`` with ADDITIVE
styling — every existing status presentation remains. Labels for the newly
visible states exist in English and German, semantically equivalent. The
language-neutral JSON status values are never translated. Both the run list's
status column and the run-detail header surface the derived run status, so a run
awaiting approval shows ``awaiting_approval`` on both.

Derived from .adw/spec.md (AC4), .adw/contract.yaml (x-behavior.R4_presentation)
and .adw/plan.md (B4). Markup/CSS wording is NOT contractual; the checks stay at
the loose level the repo uses for the Timeline (a state token surfaces in the
stylesheet and the rendered page). RED until the styling and labels are added.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _client(tmp_path, lines, *, phase, run_id=RUN_ID):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_run(repo, run_id, lines, phase=phase)
    return TestClient(create_app(repos=[str(repo)])), _slug_for(repo), run_id


def awaiting_run_lines(issue="Awaiting presentation"):
    """An OPEN run paused at the plan gate: ``approval`` ``awaited`` on the open
    ``run`` span, so its derived run status is ``awaiting_approval``."""
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload(issue)),
        rec(2, "approval", "point", "R", sec=1, payload={"gate": "plan", "event": "awaited"}),
    ]


def test_new_state_styling_is_additive_and_distinct(home, tmp_path):  # noqa: F811
    """R4/E3: the stylesheet gains rules for the new states (a ``waiting`` tree
    node, an ``awaiting`` phase / run status) WHILE every existing status class
    remains — the change adds, never replaces."""
    client, _slug, _run_id = _client(tmp_path, awaiting_run_lines(), phase="awaiting_approval")
    css = client.get("/static/app.css").text.lower()

    # Existing status presentation is preserved (E3, no colour-system refactor).
    for existing in (".phase-active", ".phase-completed", ".node-running", ".node-done"):
        assert existing in css, existing
    # The waiting tree node and the awaiting phase/run status are styled distinctly.
    assert "node-waiting" in css      # open ci.wait/gate marker (new)
    assert "awaiting" in css          # phase-awaiting / awaiting_approval (new)


def test_awaiting_approval_surfaces_on_both_list_and_detail(home, tmp_path):  # noqa: F811
    """R4: while a run awaits approval, both the run list's status column and the
    run-detail header render the derived ``awaiting_approval`` status (not
    ``running``)."""
    client, slug, run_id = _client(tmp_path, awaiting_run_lines(), phase="awaiting_approval")

    list_html = client.get("/").text.lower()
    detail_html = client.get(f"/runs/{slug}/{run_id}").text.lower()
    assert "awaiting" in list_html
    assert "awaiting" in detail_html
    # The technical JSON value stays language-neutral and is the same on both APIs.
    assert client.get(f"/api/runs/{slug}/{run_id}").json()["run"]["status"] == "awaiting_approval"


def test_new_state_labels_exist_and_are_translated_in_both_languages():
    """R4: the catalog carries human labels for the newly visible states, present
    and non-empty in EN and DE, with any multi-word English label actually
    translated in German (the key set parity is guarded by the i18n parity test)."""
    from adw.gui.i18n import CATALOG

    en, de = CATALOG["en"], CATALOG["de"]
    # Labels for the awaiting-approval run status and the waiting/awaiting states.
    approval_labels = [k for k in en if "approval" in en[k].lower()]
    waiting_labels = [k for k in en if "wait" in en[k].lower()]
    assert approval_labels, "no awaiting-approval chrome label in the catalog"
    assert waiting_labels, "no waiting/awaiting chrome label in the catalog"

    for key in set(approval_labels) | set(waiting_labels):
        assert isinstance(de.get(key), str) and de[key].strip(), f"de[{key!r}] missing/empty"
        if " " in en[key].strip():  # a multi-word phrase must not stay English
            assert de[key] != en[key], f"{key!r} left untranslated in German"
