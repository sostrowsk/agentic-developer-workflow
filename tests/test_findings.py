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
    """Contract: bare JSON surrounded by prose is not valid reviewer output."""
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
    """Contract: a fence without a closing ``` = truncated answer, even with valid JSON."""
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
    """Contract: ```json inside an outer ```` fence is example content, not a result."""
    text = '````markdown\n```json\n{"verdict": "ok", "findings": []}\n```\n````\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_json_fence_inside_tilde_fence_is_not_authoritative():
    text = '~~~\n```json\n{"verdict": "ok", "findings": []}\n```\n~~~\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_fence_line_with_info_string_does_not_close_json_fence():
    """Contract: ```python does not close an open ```json fence — the fence stays unclosed."""
    text = '```json\n{"verdict": "ok", "findings": []}\n```python'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_json_fence_indented_up_to_three_spaces_is_recognized():
    """CommonMark: up to 3 spaces of indentation are allowed for fences (e.g. in lists)."""
    text = '  ```json\n  {"verdict": "ok", "findings": []}\n  ```\n'
    assert extract_review_result(text).verdict == "ok"


def test_backtick_opener_with_tilde_info_swallows_nested_json_fence():
    """CommonMark: ```~~~ is a backtick opener with info '~~~' — content is never authoritative."""
    text = '```~~~\n```json\n{"verdict": "ok", "findings": []}\n```\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_tilde_opener_with_backtick_info_is_linear_and_not_authoritative():
    """CommonMark: ~~~ info may contain backticks; scan stays linear, content is example."""
    text = "~" * 100_000 + " `x\n" + '```json\n{"verdict": "ok", "findings": []}\n```\n~~~\n'
    started = time.monotonic()
    with pytest.raises(FindingsParseError):
        extract_review_result(text)
    assert time.monotonic() - started < 2.0


def test_crlf_fenced_review_is_parsed():
    """Windows line endings must not make a valid fence unreadable."""
    text = '```json\r\n{"verdict": "ok", "findings": []}\r\n```\r\n'
    assert extract_review_result(text).verdict == "ok"


def test_trailing_unclosed_foreign_fence_fails_closed():
    """Contract: ANY unclosed fence at the end = truncated answer, no stale draft."""
    text = (
        '```json\n{"verdict": "ok", "findings": []}\n```\n'
        "Weiter im Text:\n```python\nprint('abgeschnitten"
    )
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_unicode_line_separator_in_string_survives_fence_parsing():
    """U+2028 is allowed in JSON strings and must not be cut apart as a line break."""
    text = (
        '```json\n{"verdict": "needs_fixes", "findings": [{"severity": "P2", '
        '"lane": "backend", "file": "x.py", "issue": "Zeile1 Zeile2", '
        '"remediation_plan": []}]}\n```\n'
    )
    assert extract_review_result(text).findings[0].issue == "Zeile1 Zeile2"


def test_outer_fence_with_spaced_info_string_is_recognized():
    """Contract: ```markdown title=x is a fence opener — its ```json content is example."""
    text = '```markdown title=x\n```json\n{"verdict": "ok", "findings": []}\n```\n```\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_tilde_json_fence_is_not_authoritative():
    """Contract: only backtick ```json is authoritative, ~~~json is not."""
    text = '~~~json\n{"verdict": "ok", "findings": []}\n~~~\n'
    with pytest.raises(FindingsParseError):
        extract_review_result(text)


def test_backtick_line_with_many_spaces_is_prose_in_linear_time():
    """Regression: ``` + a flood of spaces + ` must not trigger regex backtracking."""
    text = "```" + " " * 3000 + "`\n" + '{"verdict": "ok", "findings": []}'
    started = time.monotonic()
    with pytest.raises(FindingsParseError):
        extract_review_result(text)
    assert time.monotonic() - started < 2.0


def test_integers_are_bounded_independent_of_interpreter_limit():
    """Regression: the integer bound applies via the parse_int hook, not only via
    Python's digit limit."""
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
    """Regression (Codex P2): only unknown string labels are normalized —
    non-string values remain a schema violation."""
    raw = VALID.replace('"lane": "backend"', f'"lane": {bad_lane}')
    with pytest.raises(ValidationError):
        extract_review_result(raw)
