"""RED tests for the recovery-card MARKUP and i18n — the observable rendering on
``GET /runs/{repo}/{run_id}`` (HTML) and the bilingual labels of ``adw/gui/i18n.py``.

Derived from .adw/spec.md (AC 10, 11, 12), .adw/contract.yaml (P10_render,
P11_readonly) and .adw/plan.md (B6). The page renders EXACTLY one recovery card,
following the derived ``recovery`` object: in the escalation case anchored at the
governing ``escalation`` node (``data-anchor-seq``) with reason, affected phase,
its abort events, the new-run hint and the link to ``escalation.md``; in the
approve/resume case at run level with the command text. Without a recovery object
no card appears. The card is pure display; the command line is not translated.

Markup/CSS is not part of the contract; these tests pin only the observable hooks
this workstream introduces (``data-recovery-card``, ``data-recovery-kind``,
``data-anchor-seq``, ``data-recovery-artifact``) and rendered label/command text.

RED until the run-detail template renders the recovery card.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.i18n import CATALOG
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    comprehensive_lines,
    escalated_lines,
    home,
    write_run,
    write_state_only_run,
)

RUN_ID = "aaaa1111"

# The label keys this feature adds to BOTH language blocks (AC 12).
RECOVERY_LABEL_KEYS = [
    "recovery_reason",
    "recovery_phase",
    "recovery_aborts",
    "recovery_new_run",
    "recovery_command",
    "recovery_report",
]


def _real_path(repo):
    return os.path.normpath(str(repo.resolve()))


def _client(tmp_path, *, lines=None, phase=None, state_only=False, repo_name="repo"):
    repo = tmp_path / repo_name
    repo.mkdir(parents=True, exist_ok=True)
    if state_only:
        write_state_only_run(repo, RUN_ID, phase=phase)
    else:
        write_run(repo, RUN_ID, lines, phase=phase)
    client = TestClient(create_app(repos=[str(repo)]))
    return client, _slug(_real_path(repo)), _real_path(repo)


def _card(html: str) -> str:
    """The ``<section … data-recovery-card …>…</section>`` slice of the card, or ''
    when there is none. The card is a single section with no nested ``<section>``,
    so the first closing tag ends it."""
    i = html.find("data-recovery-card")
    if i == -1:
        return ""
    start = html.rfind("<section", 0, i)
    end = html.find("</section>", i)
    return html[start:end if end != -1 else None]


def _enclosing_open_tag(html: str, tag: str) -> str:
    """The opening ``<tag …>`` element that the recovery card sits INSIDE — i.e. the
    nearest ``<tag`` before the card marker. Proves DOM placement rather than a mere
    attribute value: an escalation card must be nested in its escalation node's
    ``<li>``, not floating at the top of the document."""
    i = html.find("data-recovery-card")
    assert i != -1, "no recovery card in the page"
    start = html.rfind("<" + tag, 0, i)
    assert start != -1, f"no enclosing <{tag}> before the card"
    return html[start:html.find(">", start) + 1]


def test_escalated_card_is_anchored_at_the_escalation_node(home, tmp_path):  # noqa: F811
    """AC 10/P10: exactly one card, structurally NESTED in the tree entry whose
    ``data-seq`` equals the governing escalation's ``anchor_seq`` — showing reason,
    affected phase, the new-run hint and the link to ``escalation.md``; no
    continuation command. The anchoring is a real DOM placement, not just an
    attribute."""
    client, slug, _p = _client(tmp_path, lines=escalated_lines(), phase="escalated")
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert html.count("data-recovery-card") == 1
    # The card's nearest enclosing element is the escalation node's tree entry <li>.
    li = _enclosing_open_tag(html, "li")
    assert 'data-seq="3"' in li and "data-tree-entry" in li

    card = _card(html)
    assert 'data-recovery-kind="none"' in card
    assert 'data-anchor-seq="3"' in card             # the escalation event's seq
    assert "gate hopeless" in card                   # the verbatim reason
    assert CATALOG["en"]["recovery_new_run"] in card   # the new-run hint (EN default)
    assert 'data-recovery-artifact="escalation.md"' in card  # link, not embedded content
    # The escalation report content is referenced, not duplicated into the card.
    assert "## Grund" not in card


def test_escalated_card_lists_the_abort_events(home, tmp_path):  # noqa: F811
    """AC 10 (with AC 6): the card shows the associated abort events' values —
    here a circuit_breaker scope carried verbatim."""
    from tests.test_gui_recovery_api import escalation_history_lines

    client, slug, _p = _client(tmp_path, lines=escalation_history_lines(), phase="escalated")
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    # Anchored in the escalation node's tree entry (seq 7).
    li = _enclosing_open_tag(html, "li")
    assert 'data-seq="7"' in li and "data-tree-entry" in li

    card = _card(html)
    assert 'data-anchor-seq="7"' in card
    assert "lane:backend" in card                    # circuit_breaker scope value
    assert "gate_iterations" in card                 # limit.hit value


def test_approve_card_is_run_level_with_command_and_is_display_only(home, tmp_path):  # noqa: F811
    """AC 10/11: the approve/resume card sits at run level (before the tab
    container, not inside a tree entry) and shows the command as plain, selectable
    text; it triggers nothing (no form/button)."""
    client, slug, path = _client(tmp_path, state_only=True, phase="awaiting_approval")
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert html.count("data-recovery-card") == 1
    # Run-level placement: the card precedes the Trace tab (it is not nested in a
    # tree entry <li>).
    assert html.index("data-recovery-card") < html.index('data-tab-panel="trace"')
    card = _card(html)
    assert 'data-recovery-kind="approve"' in card
    assert "data-anchor-seq" not in card             # run-level, no escalation node
    assert f"adw approve {RUN_ID} --repo" in card     # the command text is present
    # Pure display: the card spawns no execution affordance (E1).
    assert "<form" not in card
    assert "<button" not in card


def test_no_recovery_card_without_recovery_object(home, tmp_path):  # noqa: F811
    """AC 10: a finished (``done``) run needs no human step -> no card at all."""
    client, slug, _p = _client(tmp_path, lines=comprehensive_lines(), phase="done")
    html = client.get(f"/runs/{slug}/{RUN_ID}").text

    assert "data-recovery-card" not in html


def test_recovery_labels_are_bilingual(home, tmp_path):  # noqa: F811
    """AC 12: every recovery label key exists in BOTH language blocks, non-empty,
    and the prose new-run hint differs between the languages."""
    for key in RECOVERY_LABEL_KEYS:
        assert key in CATALOG["en"] and CATALOG["en"][key].strip()
        assert key in CATALOG["de"] and CATALOG["de"][key].strip()
    assert CATALOG["en"]["recovery_new_run"] != CATALOG["de"]["recovery_new_run"]


def test_command_line_is_not_translated(home, tmp_path):  # noqa: F811
    """AC 12: the command line is identical regardless of GUI language, and the
    German new-run hint renders in the German page."""
    client, slug, _p = _client(tmp_path, state_only=True, phase="build")
    en = client.get(f"/runs/{slug}/{RUN_ID}", params={"lang": "en"}).text
    de = client.get(f"/runs/{slug}/{RUN_ID}", params={"lang": "de"}).text

    command = client.get(f"/api/runs/{slug}/{RUN_ID}").json()["recovery"]["command"]
    assert command in en and command in de          # same command text in both languages
    assert CATALOG["de"]["recovery_command"] in _card(de)
    assert CATALOG["en"]["recovery_command"] in _card(en)


def test_recovery_link_opens_artifacts_tab_and_reveals_escalation_md(tmp_path):
    """AC 9/10: clicking the escalation-report link activates the Artifacts tab,
    opens the escalation.md entry and loads its content on demand — read-only
    navigation through the existing tab/artifact machinery, no page navigation.
    Executed against the SERVED app.js via the plain-node harness."""
    from tests.gui_js_harness import run_scenario

    obs = run_scenario(tmp_path, "recovery-link")
    assert obs["navigations"] == []                  # read-only: no URL navigation
    assert obs["artifacts_active"] is True           # the Artifacts tab is activated
    assert obs["trace_active"] is False              # ... and the Trace tab deactivated
    assert obs["details_open"] is True               # the escalation.md entry is revealed
    assert obs["fetch_escalation"] >= 1              # ... and its content loads on demand
