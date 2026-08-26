"""RED tests for the dry-run identification (AC1–AC3).

A dry run is derived exclusively from the ``dry_run`` field in the start payload
of the ``run`` span (``_run_start_payload``, GUI-SPEC §4.4). Both read endpoints
expose it as a boolean on the existing run record; both HTML surfaces mark a dry
run visibly — the run list with a short language label on the row, the run detail
with a viewport-persistent banner in the header. A missing field, a ``false``
value or a completely absent ``run`` span all yield ``false``; missing
``usage``/token data never participates (no heuristic).

Derived from .adw/spec.md (AC1–AC3), .adw/contract.yaml (RunSummary.dry_run,
x-behavior R1/R2/R3) and .adw/plan.md (B1/B3/B4/B5). Markup and CSS wording are
NOT contractual, so the visible marking is checked as observable content and the
persistence as a MECHANISM (a sticky/fixed rule on the dry-run marking), never as
exact markup. RED until ``_summary`` derives ``dry_run`` and the templates/i18n
render the marking.
"""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_end_payload,
    run_start_payload,
    write_run,
    write_state_only_run,
)

_MISSING = object()


def _client(repos):
    return TestClient(create_app(repos=[str(p) for p in repos]))


def _by_id(entries, run_id):
    return next((e for e in entries if e.get("run_id") == run_id), None)


def dry_lines(issue, dry, *, ended=True, start_sec=0):
    """A minimal one-span ``run`` whose start payload carries ``dry_run`` set to
    ``dry`` (``True``/``False``) or, when ``dry is _MISSING``, no ``dry_run`` key
    at all. ``ended`` closes the span (``done``); otherwise it stays open."""
    payload = run_start_payload(issue)
    if dry is _MISSING:
        payload.pop("dry_run", None)
    else:
        payload["dry_run"] = dry
    lines = [rec(1, "run", "start", "R", None, sec=start_sec, payload=payload)]
    if ended:
        lines.append(rec(2, "run", "end", "R", None, sec=start_sec + 12,
                         payload=run_end_payload("done")))
    else:
        lines.append(rec(2, "phase", "start", "P", "R", sec=start_sec + 1,
                         payload={"name": "build", "from_phase": "build"}))
    return lines


def _dry_labels():
    """The short EN/DE dry-run label from the catalog, located by its ``dry``
    content (its dictionary KEY is not pinned by the contract)."""
    from adw.gui.i18n import CATALOG

    key = next(k for k in CATALOG["en"] if "dry" in CATALOG["en"][k].lower())
    return CATALOG["en"][key], CATALOG["de"][key]


def _detail_header(html):
    """The run-detail ``<header class="run-header">`` … ``</header>`` slice, so a
    marking assertion is scoped to the Kopf without pinning the surrounding markup."""
    i = html.find('<header class="run-header"')
    if i == -1:
        return ""
    j = html.find("</header>", i)
    return html[i:] if j == -1 else html[i:j]


# --- AC1: the dry_run field on both endpoints -----------------------------------


def test_dry_run_field_reflects_start_payload_in_both_endpoints(home, tmp_path):  # noqa: F811
    """AC1: ``dry_run: true`` in the start payload yields ``true``, explicit
    ``false`` yields ``false``, and the list and detail endpoints agree for the
    same event log."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", dry_lines("Simulated", True), phase="done")
    write_run(repo, "bbbb2222", dry_lines("Real explicit false", False), phase="done")

    client = _client([repo])
    data = client.get("/api/runs").json()

    assert _by_id(data, "aaaa1111")["dry_run"] is True
    assert _by_id(data, "bbbb2222")["dry_run"] is False

    for run_id, expected in (("aaaa1111", True), ("bbbb2222", False)):
        summary = _by_id(data, run_id)
        detail = client.get(f"/api/runs/{summary['repo']}/{run_id}").json()["run"]
        assert detail["dry_run"] is expected
        assert summary["dry_run"] == detail["dry_run"]  # cross-endpoint consistency


def test_missing_field_and_missing_run_span_default_to_false(home, tmp_path):  # noqa: F811
    """AC1 (E4): a start payload without the ``dry_run`` key and a run with no
    ``run`` span at all (a state-only legacy run) both default to ``false`` — never
    an error, on either endpoint."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "cccc3333", dry_lines("No dry_run key", _MISSING), phase="done")
    write_state_only_run(repo, "dddd4444", phase="done", issue="Legacy, no trace")

    client = _client([repo])
    data = client.get("/api/runs").json()
    no_field = _by_id(data, "cccc3333")
    no_span = _by_id(data, "dddd4444")
    assert no_field["dry_run"] is False
    assert no_span["dry_run"] is False

    for run_id in ("cccc3333", "dddd4444"):
        detail = client.get(f"/api/runs/{no_field['repo']}/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["run"]["dry_run"] is False


def test_dry_run_value_is_not_inferred_from_usage(home, tmp_path):  # noqa: F811
    """AC1 (E4): the value comes from the start payload, never from a
    missing-usage heuristic. A dry run that DOES carry token/usage totals stays
    ``true``; a real run with NO usage/token data stays ``false``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # A dry run WITH a full totals block in its end payload.
    write_run(repo, "aaaa1111", dry_lines("Dry but has totals", True), phase="done")
    # A real run whose end payload carries only a status — no totals/usage at all.
    real = [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("Real, no usage")),
        rec(2, "run", "end", "R", None, sec=5, payload={"status": "done"}),
    ]
    write_run(repo, "bbbb2222", real, phase="done")

    data = _client([repo]).get("/api/runs").json()
    assert _by_id(data, "aaaa1111")["dry_run"] is True
    assert _by_id(data, "bbbb2222")["dry_run"] is False


# --- AC2: the run-list row marking ----------------------------------------------


def test_run_list_marks_dry_run_rows_and_not_real_ones(home, tmp_path):  # noqa: F811
    """AC2: a dry-run row carries the short dry-run label in the selected language;
    a real run's row carries no dry-run marking at all."""
    en_label, de_label = _dry_labels()

    dry_repo = tmp_path / "dry"
    dry_repo.mkdir()
    write_run(dry_repo, "aaaa1111", dry_lines("Simulated", True), phase="done")
    dry_client = _client([dry_repo])
    assert en_label in dry_client.get("/").text          # marked (English default)
    assert de_label in dry_client.get("/?lang=de").text  # ... and follows the language

    real_repo = tmp_path / "real"
    real_repo.mkdir()
    write_run(real_repo, "bbbb2222", dry_lines("Real run", False), phase="done")
    assert en_label not in _client([real_repo]).get("/").text


# --- AC3: the run-detail header marking -----------------------------------------


def test_run_detail_header_marks_dry_run_and_ignores_missing_tokens(home, tmp_path):  # noqa: F811
    """AC3: the run-detail header of a dry run shows the short label in the selected
    language even when the run carries NO token/usage data; a real run shows no
    dry-run marking anywhere."""
    en_label, de_label = _dry_labels()
    repo = tmp_path / "repo"
    repo.mkdir()
    # A dry run without any token/usage/totals data (only a status in the end payload).
    dry = [
        rec(1, "run", "start", "R", None, sec=0,
            payload={**run_start_payload("Simulated"), "dry_run": True}),
        rec(2, "run", "end", "R", None, sec=5, payload={"status": "done"}),
    ]
    write_run(repo, "aaaa1111", dry, phase="done")
    write_run(repo, "bbbb2222", dry_lines("Real run", False), phase="done")

    client = _client([repo])

    def slug_of(run_id):
        return _by_id(client.get("/api/runs").json(), run_id)["repo"]

    dry_en = client.get(f"/runs/{slug_of('aaaa1111')}/aaaa1111").text
    dry_de = client.get(f"/runs/{slug_of('aaaa1111')}/aaaa1111?lang=de").text
    real = client.get(f"/runs/{slug_of('bbbb2222')}/bbbb2222").text

    assert en_label in _detail_header(dry_en)
    assert de_label in _detail_header(dry_de)
    assert en_label not in real


def test_dry_run_detail_marking_is_viewport_persistent(home, tmp_path):  # noqa: F811
    """AC3: the dry-run marking sits in the header AND is styled with a
    viewport-persistence MECHANISM (a sticky or fixed position on the dry-run
    marking) so it stays visible while the trace tree scrolls. A static
    top-of-document note would scroll away. The class name and exact CSS wording
    are not pinned — only the mechanism."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_run(repo, "aaaa1111", dry_lines("Simulated", True), phase="done")

    client = _client([repo])
    slug = client.get("/api/runs").json()[0]["repo"]
    html = client.get(f"/runs/{slug}/aaaa1111").text
    css = client.get("/static/app.css").text.lower()

    en_label, _ = _dry_labels()
    assert en_label in _detail_header(html)  # the banner lives in the Kopf

    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    persistent = [
        sel for sel, body in rules
        if "dry" in sel and re.search(r"position\s*:\s*(sticky|fixed)", body)
    ]
    assert persistent, "the dry-run marking has no viewport-persistence rule"
