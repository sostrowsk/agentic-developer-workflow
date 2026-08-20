"""RED tests for Aufgabe A — GUI i18n (de/en), Spec-Schritt 12 (§7.5).

Derived from .adw/spec.md (AC A1–A6), .adw/contract.yaml
(x-adw-template-behavior.language) and .adw/plan.md §A. The observable surface is
pinned; the concrete dictionary keys, their wording and the cookie NAME are NOT
(contract: "Keine internen Helper-Signaturen, keine Dictionary-Schlüssel").
Therefore the selection/cookie behaviour is exercised end-to-end through the two
HTML routes, and the only module import is the language CATALOG the parity test
(A6) runs the two dictionaries against each other over.

RED until ``adw/gui/i18n.py`` exists and the handlers/templates consume it.
``tests/test_gui_language.py`` must stay green unchanged (E3): the default
request stays fully English.
"""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    FINAL_ANSWER,
    GATE_OUTPUT,
    PROMPT,
    comprehensive_lines,
    home,
    write_run,
)

# A full English chrome phrase that is a HINT text (A4 lists Hinweistexte among the
# translated chrome). It never appears in agent content, so its presence/absence
# is a robust signal for the rendered language without pinning German wording.
EN_CHROME_PHRASE = "No problems reported."


def _build(tmp_path, run_id="aaaa1111"):
    """A finished run exercising every detail node, plus the app and its slug."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, run_id, comprehensive_lines(), phase="done")
    app = create_app(repos=[str(repo)])
    slug = TestClient(app).get("/api/runs").json()[0]["repo"]
    return app, slug, run_id


def _html_lang(html: str):
    m = re.search(r"<html[^>]*\blang=\"([^\"]+)\"", html)
    return m.group(1) if m else None


# --- A6: dictionary parity ------------------------------------------------------


def test_catalog_has_de_and_en_with_identical_keys():
    """A6/A1: the catalog offers exactly de and en; both dictionaries carry the
    same key set (no key missing on either side) and every value is a non-empty
    string."""
    from adw.gui.i18n import CATALOG

    assert {"de", "en"} <= set(CATALOG)
    de, en = CATALOG["de"], CATALOG["en"]
    assert set(de) == set(en), set(de) ^ set(en)
    for key in en:
        assert isinstance(en[key], str) and en[key].strip(), f"en[{key!r}] empty"
        assert isinstance(de[key], str) and de[key].strip(), f"de[{key!r}] empty"


def test_no_multiword_chrome_string_is_left_untranslated():
    """A1/A6: no German value is merely the English text — a shared key whose de/en
    values are identical is only tolerated for a language-neutral single-token term
    (a loanword or technical identifier), never for a multi-word English phrase."""
    from adw.gui.i18n import CATALOG

    de, en = CATALOG["de"], CATALOG["en"]
    untranslated = [
        key for key in en if de[key] == en[key] and " " in en[key].strip()
    ]
    assert not untranslated, f"multi-word chrome left untranslated in de: {untranslated}"


# --- A2: selection order and English default ------------------------------------


def test_default_request_renders_english(home, tmp_path):  # noqa: F811
    """A2: a request without any language signal (no ?lang, no cookie, no
    Accept-Language) renders fully English on both HTML routes."""
    app, slug, run_id = _build(tmp_path)
    client = TestClient(app)
    home_html = client.get("/").text
    detail_html = client.get(f"/runs/{slug}/{run_id}").text
    assert _html_lang(home_html) == "en"
    assert _html_lang(detail_html) == "en"
    assert EN_CHROME_PHRASE in detail_html


def test_query_lang_de_translates_chrome(home, tmp_path):  # noqa: F811
    """A2: ``?lang=de`` renders German chrome — <html lang> flips to de and the
    English hint phrase is gone (translated)."""
    app, slug, run_id = _build(tmp_path)
    html = TestClient(app).get(f"/runs/{slug}/{run_id}?lang=de").text
    assert _html_lang(html) == "de"
    assert EN_CHROME_PHRASE not in html


def test_accept_language_selects_first_supported(home, tmp_path):  # noqa: F811
    """A2: with no ?lang and no cookie, the first SUPPORTED language of
    Accept-Language wins — an unsupported leading entry is skipped, an
    all-unsupported header falls to English."""
    app, slug, run_id = _build(tmp_path)
    url = f"/runs/{slug}/{run_id}"

    de = TestClient(app).get(url, headers={"Accept-Language": "de"}).text
    assert _html_lang(de) == "de"

    first_supported = TestClient(app).get(
        url, headers={"Accept-Language": "fr-FR,fr;q=0.9,de;q=0.8"}
    ).text
    assert _html_lang(first_supported) == "de"

    unsupported = TestClient(app).get(url, headers={"Accept-Language": "fr"}).text
    assert _html_lang(unsupported) == "en"


def test_query_beats_cookie_and_cookie_beats_accept_language(home, tmp_path):  # noqa: F811
    """A2: the precedence is query > cookie > Accept-Language. A valid ?lang wins
    over a differing cookie; absent a query, the cookie wins over Accept-Language.
    The cookie is exercised through the SAME client (its name is not pinned)."""
    app, slug, run_id = _build(tmp_path)
    url = f"/runs/{slug}/{run_id}"
    client = TestClient(app)

    # A valid ?lang=de establishes the cookie for this client (A3).
    assert _html_lang(client.get(f"{url}?lang=de").text) == "de"

    # Query wins over the (de) cookie.
    assert _html_lang(client.get(f"{url}?lang=en").text) == "en"

    # Re-establish the de cookie, then omit ?lang: the cookie wins over a
    # conflicting Accept-Language: en header.
    assert _html_lang(client.get(f"{url}?lang=de").text) == "de"
    cookie_wins = client.get(url, headers={"Accept-Language": "en"}).text
    assert _html_lang(cookie_wins) == "de"


# --- A3: cookie behaviour -------------------------------------------------------


def test_valid_query_sets_cookie_used_by_follow_up(home, tmp_path):  # noqa: F811
    """A3: an explicit valid ?lang=de sets the language cookie; a subsequent
    request WITHOUT ?lang (same client) renders German from that cookie."""
    app, slug, run_id = _build(tmp_path)
    url = f"/runs/{slug}/{run_id}"
    client = TestClient(app)

    first = client.get(f"{url}?lang=de")
    assert "set-cookie" in {k.lower() for k in first.headers}
    follow_up = client.get(url).text
    assert _html_lang(follow_up) == "de"


def test_invalid_query_sets_no_cookie(home, tmp_path):  # noqa: F811
    """A3: an invalid ?lang value sets no language cookie — the follow-up request
    is still the English default."""
    app, slug, run_id = _build(tmp_path)
    url = f"/runs/{slug}/{run_id}"
    client = TestClient(app)

    resp = client.get(f"{url}?lang=fr")
    assert "fr" not in resp.headers.get("set-cookie", "")
    assert _html_lang(client.get(url).text) == "en"


# --- A4: translation scope (chrome only, content never) -------------------------


def test_content_is_byte_identical_across_languages(home, tmp_path):  # noqa: F811
    """A4/E2: only chrome is translated. The prompt, the agent's final answer and
    the gate output render byte-identically regardless of language, while the
    chrome differs (the English hint phrase drops out under de)."""
    app, slug, run_id = _build(tmp_path)
    url = f"/runs/{slug}/{run_id}"
    en = TestClient(app).get(f"{url}?lang=en").text
    de = TestClient(app).get(f"{url}?lang=de").text

    for content in (PROMPT, FINAL_ANSWER, GATE_OUTPUT):
        assert content in en, content
        assert content in de, content  # content untouched by language

    assert EN_CHROME_PHRASE in en and EN_CHROME_PHRASE not in de  # chrome differs


# --- A5: language switch preserves state ----------------------------------------


def test_header_switch_link_preserves_window_and_toggles_language(home, tmp_path):  # noqa: F811
    """A5: the header carries a switch link that changes ONLY the language and
    keeps the windowed slice — offset, tools_offset and focus travel unchanged.
    On the English page the link targets de; on the German page it targets en."""
    app, slug, run_id = _build(tmp_path)
    params = "offset=5&tools_offset=3&focus=7"

    en = TestClient(app).get(f"/runs/{slug}/{run_id}?lang=en&{params}").text
    to_de = [h for h in re.findall(r'href="([^"]+)"', en) if "lang=de" in h]
    assert to_de, "no switch link to German on the English page"
    link = to_de[0]
    assert "tools_offset=3" in link and "focus=7" in link

    de = TestClient(app).get(f"/runs/{slug}/{run_id}?lang=de&{params}").text
    to_en = [h for h in re.findall(r'href="([^"]+)"', de) if "lang=en" in h]
    assert to_en, "no switch link to English on the German page"
    assert "tools_offset=3" in to_en[0] and "focus=7" in to_en[0]


def test_run_list_page_language_is_selectable(home, tmp_path):  # noqa: F811
    """A2/A4: the run-list route honours the same selection — ?lang=de flips its
    <html lang> to de while the default stays en."""
    app, _slug, _run_id = _build(tmp_path)
    assert _html_lang(TestClient(app).get("/").text) == "en"
    assert _html_lang(TestClient(app).get("/?lang=de").text) == "de"


# --- A4: client-injected chrome hints follow the language too -------------------

# The status texts app.js writes into the Diff / Tools / Artifacts panels are UI
# CHROME (hint texts), not content. "No changes in this step." in particular is not
# a transient state but the permanent result of an empty diff, so a German request
# that renders it in English leaves visible English chrome behind.
_JS_HINTS = (
    "Loading…",
    "No changes in this step.",
    "(failed to load the diff — open the Diff tab again to retry)",
    "(payload not found)",
    "(failed to load — expand again to retry)",
    "(failed to load — open again to retry)",
)


def test_client_injected_hints_are_served_translated(tmp_path):
    """A4: every hint app.js can inject is delivered with the page in the requested
    language, so a German request shows German chrome in the Diff/Tools/Artifacts
    panels. The transport (data attributes) is an implementation detail; what is
    pinned is that the German page carries German texts and the English one English.

    The two DIFF hints ride on the Diff panel, which exists only for a bracketed
    node — a page without a Diff tab must not carry them (several tests assert that
    such a page contains no "Diff" at all). So they are checked on a run that HAS a
    bracketed node.
    """
    from adw.gui.i18n import CATALOG
    from tests.gui_app_helpers import bracketed_lane_lines

    keys = [k for k, v in CATALOG["en"].items() if v in _JS_HINTS]
    missing = sorted(set(_JS_HINTS) - set(CATALOG["en"].values()))
    assert len(keys) == len(_JS_HINTS), f"not every client hint is in the catalog: {missing}"
    for key in keys:
        assert CATALOG["de"][key] != CATALOG["en"][key], f"{key} not translated"

    diff_keys = {"hint_no_changes", "hint_diff_failed"}

    # (a) a page WITHOUT a Diff tab carries every general hint ...
    app, slug, rid = _build(tmp_path)
    client = TestClient(app)
    de = client.get(f"/runs/{slug}/{rid}?lang=de").text
    en = client.get(f"/runs/{slug}/{rid}?lang=en").text
    assert "tab-diff" not in de
    for key in keys:
        if key in diff_keys:
            assert CATALOG["de"][key] not in de, f"{key} leaked onto a page without a Diff tab"
            continue
        assert CATALOG["de"][key] in de, f"German page misses {key!r}"
        assert CATALOG["en"][key] in en, f"English page misses {key!r}"

    # (b) ... and a page WITH a Diff tab additionally carries the diff hints.
    repo = tmp_path / "repo_bracketed"
    repo.mkdir(exist_ok=True)
    write_run(repo, "bbbb2222", bracketed_lane_lines("bbbb2222"), phase="done")
    app2 = create_app(repos=[str(repo)])
    client2 = TestClient(app2)
    slug2 = client2.get("/api/runs").json()[0]["repo"]
    de2 = client2.get(f"/runs/{slug2}/bbbb2222?lang=de").text
    en2 = client2.get(f"/runs/{slug2}/bbbb2222?lang=en").text
    assert "tab-diff" in de2
    for key in diff_keys:
        assert CATALOG["de"][key] in de2, f"German diff panel misses {key!r}"
        assert CATALOG["en"][key] in en2, f"English diff panel misses {key!r}"


def test_client_script_reads_the_hints_from_the_page(tmp_path):
    """A4 (wiring): the served app.js resolves its hint texts through the page
    instead of using English literals directly, so the language the server decided
    on also governs what the client injects. Asserted on the served script text —
    wiring only, like the response-time tests do."""
    app, slug, rid = _build(tmp_path)
    client = TestClient(app)
    js = client.get("/static/app.js").text
    de = client.get(f"/runs/{slug}/{rid}?lang=de").text

    # The page delivers the hints as data attributes ...
    assert "data-hint-" in de, "the rendered page carries no hint data attributes"
    # ... and the client reads them from there.
    assert "data-hint-" in js, "app.js does not read the hints from the page"
