import time

import pytest
from pydantic import ValidationError

from adw.findings import FindingsParseError, extract_review_result

VALID = """\
{"verdict": "needs_fixes", "findings": [{
  "severity": "P1", "lane": "backend", "file": "app/models.py",
  "issue": "Race condition beim Claim",
  "remediation_plan": ["Conditional UPDATE nutzen", "Test ergänzen"],
  "category": "implementation"
}]}
"""


def test_parses_pure_json_review():
    result = extract_review_result(VALID)
    assert result.verdict == "needs_fixes"
    assert result.findings[0].severity == "P1"
    assert result.findings[0].lane == "backend"
    assert result.findings[0].remediation_plan == ["Conditional UPDATE nutzen", "Test ergänzen"]


def test_parses_ok_verdict_with_empty_findings():
    result = extract_review_result('{"verdict": "ok", "findings": []}')
    assert result.verdict == "ok"
    assert result.findings == []


def test_parses_last_json_fence_surrounded_by_prose():
    text = f"Meine Analyse ist abgeschlossen.\n```json\n{VALID}```\nViel Erfolg!"
    result = extract_review_result(text)
    assert result.verdict == "needs_fixes"
    assert len(result.findings) == 1


def test_last_of_multiple_json_fences_wins():
    text = (
        'Entwurf:\n```json\n{"verdict": "ok", "findings": []}\n```\n'
        f"Korrigiert:\n```json\n{VALID}```\n"
    )
    assert extract_review_result(text).verdict == "needs_fixes"


def test_prose_around_bare_json_is_rejected():
    """Kontrakt: nacktes JSON mit Prosa drumherum ist kein gültiger Reviewer-Output."""
    with pytest.raises(FindingsParseError):
        extract_review_result('Analyse: {"verdict": "ok", "findings": []} — fertig!')


def test_trailing_junk_after_pure_json_is_rejected():
    with pytest.raises(FindingsParseError):
        extract_review_result('{"verdict": "ok", "findings": []}\nQuelle [1]')


def test_text_without_json_is_rejected():
    with pytest.raises(FindingsParseError) as exc:
        extract_review_result("Hier ist kein JSON, nur Prosa { kaputt")
    assert "nur Prosa" in str(exc.value)


def test_truncated_fence_content_is_rejected():
    text = 'Final:\n```json\n{"verdict": "needs_fixes", "findings": [{"sev\n```\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_fence_with_non_object_body_is_rejected():
    for body in ("[]", "null", '"ok"', "42"):
        with pytest.raises(FindingsParseError):
            extract_review_result(f"```json\n{body}\n```")


def test_unclosed_fence_is_rejected():
    """Kontrakt: Fence ohne schließendes ``` = abgeschnittene Antwort, auch bei validem JSON."""
    with pytest.raises(FindingsParseError):
        extract_review_result('```json\n{"verdict": "ok", "findings": []}')


def test_unclosed_fence_after_closed_fence_is_rejected():
    text = (
        'Entwurf:\n```json\n{"verdict": "ok", "findings": []}\n```\n'
        'Korrektur:\n```json\n{"verdict": "needs_fixes", "findings": ['
    )
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_json_fence_inside_outer_backtick_fence_is_not_authoritative():
    """Kontrakt: ```json in einem äußeren ````-Fence ist Beispiel-Content, kein Ergebnis."""
    text = '````markdown\n```json\n{"verdict": "ok", "findings": []}\n```\n````\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_json_fence_inside_tilde_fence_is_not_authoritative():
    text = '~~~\n```json\n{"verdict": "ok", "findings": []}\n```\n~~~\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_fence_line_with_info_string_does_not_close_json_fence():
    """Kontrakt: ```python schließt keinen offenen ```json-Fence — Fence bleibt unclosed."""
    text = '```json\n{"verdict": "ok", "findings": []}\n```python'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_json_fence_indented_up_to_three_spaces_is_recognized():
    """CommonMark: bis zu 3 Leerzeichen Einrückung sind für Fences erlaubt (z. B. in Listen)."""
    text = '  ```json\n  {"verdict": "ok", "findings": []}\n  ```\n'
    assert extract_review_result(text).verdict == "ok"


def test_backtick_opener_with_tilde_info_swallows_nested_json_fence():
    """CommonMark: ```~~~ ist ein Backtick-Opener mit Info '~~~' — Inhalt ist nie autoritativ."""
    text = '```~~~\n```json\n{"verdict": "ok", "findings": []}\n```\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_tilde_opener_with_backtick_info_is_linear_and_not_authoritative():
    """CommonMark: ~~~-Info darf Backticks enthalten; Scan bleibt linear, Inhalt Beispiel."""
    text = "~" * 100_000 + " `x\n" + '```json\n{"verdict": "ok", "findings": []}\n```\n~~~\n'
    started = time.monotonic()
    with pytest.raises(FindingsParseError):
        extract_review_result(text)
    assert time.monotonic() - started < 2.0


def test_crlf_fenced_review_is_parsed():
    """Windows-Zeilenenden dürfen einen validen Fence nicht unlesbar machen."""
    text = '```json\r\n{"verdict": "ok", "findings": []}\r\n```\r\n'
    assert extract_review_result(text).verdict == "ok"


def test_trailing_unclosed_foreign_fence_fails_closed():
    """Kontrakt: JEDER unclosed Fence am Ende = abgeschnittene Antwort, kein Stale-Draft."""
    text = (
        '```json\n{"verdict": "ok", "findings": []}\n```\n'
        "Weiter im Text:\n```python\nprint('abgeschnitten"
    )
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_unicode_line_separator_in_string_survives_fence_parsing():
    """U+2028 ist in JSON-Strings erlaubt und darf nicht als Zeilenumbruch zerschnitten werden."""
    text = (
        '```json\n{"verdict": "needs_fixes", "findings": [{"severity": "P2", '
        '"lane": "backend", "file": "x.py", "issue": "Zeile1 Zeile2", '
        '"remediation_plan": []}]}\n```\n'
    )
    assert extract_review_result(text).findings[0].issue == "Zeile1 Zeile2"


def test_outer_fence_with_spaced_info_string_is_recognized():
    """Kontrakt: ```markdown title=x ist ein Fence-Opener — sein ```json-Inhalt ist Beispiel."""
    text = '```markdown title=x\n```json\n{"verdict": "ok", "findings": []}\n```\n```\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_tilde_json_fence_is_not_authoritative():
    """Kontrakt: nur Backtick-```json ist autoritativ, ~~~json nicht."""
    text = '~~~json\n{"verdict": "ok", "findings": []}\n~~~\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_backtick_line_with_many_spaces_is_prose_in_linear_time():
    """Regression: ``` + Leerzeichenflut + ` darf kein Regex-Backtracking auslösen."""
    text = "```" + " " * 3000 + "`\n" + '{"verdict": "ok", "findings": []}'
    started = time.monotonic()
    with pytest.raises(FindingsParseError):
        extract_review_result(text)
    assert time.monotonic() - started < 2.0


def test_integers_are_bounded_independent_of_interpreter_limit():
    """Regression: Integer-Grenze gilt per parse_int-Hook, nicht nur via Python-Digit-Limit."""
    text = '{"verdict": "ok", "findings": [], "n": ' + "9" * 200 + "}"
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_unknown_severity_raises_validation_error():
    with pytest.raises(ValidationError):
        extract_review_result(
            '{"verdict": "needs_fixes", "findings": [{"severity": "P9", "lane": "backend", '
            '"file": "x.py", "issue": "x", "remediation_plan": []}]}'
        )


def test_misspelled_findings_key_is_rejected():
    with pytest.raises(ValidationError):
        extract_review_result('{"verdict": "ok", "finding": []}')


def test_missing_findings_key_is_rejected():
    with pytest.raises(ValidationError):
        extract_review_result('{"verdict": "ok"}')


def test_ok_verdict_with_findings_is_rejected():
    with pytest.raises(ValidationError):
        extract_review_result(
            '{"verdict": "ok", "findings": [{"severity": "P1", "lane": "backend", '
            '"file": "x.py", "issue": "kritisch", "remediation_plan": ["fix"]}]}'
        )


def test_needs_fixes_without_findings_is_rejected():
    with pytest.raises(ValidationError):
        extract_review_result('{"verdict": "needs_fixes", "findings": []}')


def test_finding_without_lane_file_or_plan_is_rejected():
    with pytest.raises(ValidationError):
        extract_review_result(
            '{"verdict": "needs_fixes", "findings": [{"severity": "P2", "issue": "Fehlt"}]}'
        )


def test_category_is_optional():
    result = extract_review_result(
        '{"verdict": "needs_fixes", "findings": [{"severity": "P2", "lane": "unknown", '
        '"file": "a.py", "issue": "x", "remediation_plan": []}]}'
    )
    assert result.findings[0].category is None


def test_duplicate_findings_key_fails_closed():
    text = (
        '{"verdict": "ok", "findings": [{"severity": "P1", "lane": "backend", "file": "x.py", '
        '"issue": "kritisch", "remediation_plan": ["fix"]}], "findings": []}'
    )
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_deeply_nested_json_fails_controlled_not_with_recursion_error():
    text = "```json\n" + "[" * 5000 + "]" * 5000 + "\n```"
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_oversized_integer_fails_closed_not_with_value_error():
    text = '{"verdict": "ok", "findings": [], "n": ' + "9" * 5000 + "}"
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_huge_adversarial_input_parses_in_linear_time():
    text = "{" * 200_000
    started = time.monotonic()
    with pytest.raises(FindingsParseError):
        extract_review_result(text)
    assert time.monotonic() - started < 2.0


def test_unrecognized_lane_label_normalizes_to_unknown():
    raw = VALID.replace('"lane": "backend"', '"lane": "ios"')
    result = extract_review_result(raw)
    assert result.findings[0].lane == "unknown"


@pytest.mark.parametrize("bad_lane", ["null", "3", "[]", "{}", "true"])
def test_non_string_lane_raises_validation_error(bad_lane):
    """Regression (Codex P2): nur unbekannte String-Labels werden normalisiert —
    Nicht-String-Werte bleiben Schema-Verletzung."""
    raw = VALID.replace('"lane": "backend"', f'"lane": {bad_lane}')
    with pytest.raises(ValidationError):
        extract_review_result(raw)
