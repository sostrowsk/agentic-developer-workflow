"""The pane's payload block reads as text, not as a JSON dump.

A run's `issue` or an agent's `prompt` is a multi-thousand-character string with
embedded newlines. Serialised as JSON — even indented — it arrives as one endless
line full of `\\n` escapes, which is the opposite of readable. The pane therefore
renders the payload as an indented FIELD LIST whose multi-line strings keep their
real line breaks.

The same format is produced on both sides (server-rendered span panes, and the
shared pane the client fills from the events route), so a payload reads the same
wherever it is shown; both halves are pinned here.
"""

import json

from adw.gui.app import _pretty_payload
from tests.gui_js_harness import served_app_js


def test_scalars_render_as_key_value_lines():
    out = _pretty_payload({"tool": "Read", "bytes": 12, "ok": True, "missing": None})

    assert out.splitlines() == ["tool: Read", "bytes: 12", "ok: true", "missing: null"]


def test_a_multiline_string_keeps_its_real_line_breaks():
    """The whole point: no `\\n` escape survives, and the text is indented under its
    key so it stays visually attached to it."""
    out = _pretty_payload({"issue": "# Title\nfirst\nsecond", "lane": "backend"})

    assert "\\n" not in out
    assert out.splitlines() == [
        "issue:",
        "  # Title",
        "  first",
        "  second",
        "lane: backend",
    ]


def test_nested_objects_and_lists_are_indented():
    out = _pretty_payload({"input": {"file_path": "/x/a.py"}, "lanes": ["backend", "frontend"]})

    assert out.splitlines() == [
        "input:",
        "  file_path: /x/a.py",
        "lanes:",
        "  - backend",
        "  - frontend",
    ]


def test_empty_containers_stay_on_one_line():
    out = _pretty_payload({"totals": {}, "findings": []})

    assert out.splitlines() == ["totals: {}", "findings: []"]


def test_a_non_object_payload_still_renders():
    """A payload that is not a mapping (or missing) must not blow up the pane."""
    assert _pretty_payload(None) == ""
    assert _pretty_payload("plain text") == "plain text"
    assert _pretty_payload([1, 2]).splitlines() == ["- 1", "- 2"]


def test_the_client_formats_payloads_the_same_way():
    """The shared pane is filled in JS; it must not fall back to a JSON dump."""
    js = served_app_js()

    assert "prettyPayload" in js
    assert "JSON.stringify(found.payload, null, 2)" not in js


def test_client_and_server_agree_on_a_realistic_payload(tmp_path):
    from tests.gui_js_harness import run_scenario

    payload = {
        "adw_version": "0.18.0",
        "issue": "# Head\n\n- one\n- two",
        "lanes": ["backend"],
        "parallel": False,
        "totals": {},
        "nested": {"a": {"b": 1}},
    }
    r = run_scenario(tmp_path, "pretty-payload", json.dumps(payload))

    assert r["text"] == _pretty_payload(payload)


# --- number parity: the two formatters must agree digit for digit ---------------

# JS renders every number through Number::toString; Python's json/repr differ for
# integral floats (`1.0` vs `1`) and in where they switch to exponent notation.
# `_js_number` ports the JS rules so a float reads the same in a server-rendered pane
# and in the shared lazy one. Expected values verified against `node`.
JS_NUMBERS = [
    (1.0, "1"),
    (0.0, "0"),
    (-0.0, "0"),
    (123.0, "123"),
    (100.0, "100"),
    (0.1, "0.1"),
    (-2.5, "-2.5"),
    (0.777149, "0.777149"),
    (1 / 3, "0.3333333333333333"),
    (0.00001, "0.00001"),
    (1e-6, "0.000001"),
    (1e-7, "1e-7"),
    (1e16, "10000000000000000"),
    (1e20, "100000000000000000000"),
    (1.5e21, "1.5e+21"),
    (1e-323, "1e-323"),
    (7, "7"),
    (-3, "-3"),
]


def test_numbers_render_exactly_as_javascript_would():
    from adw.gui.app import _js_number

    assert [_js_number(v) for v, _ in JS_NUMBERS] == [want for _, want in JS_NUMBERS]


def test_float_payloads_agree_between_client_and_server(tmp_path):
    """The parity claim must hold for floats too, not only for strings."""
    from tests.gui_js_harness import run_scenario

    payload = {"cost_usd": 1.0, "duration": 0.777149, "tiny": 1e-7, "huge": 1.5e21,
               "zero": 0.0, "count": 7}
    r = run_scenario(tmp_path, "pretty-payload", json.dumps(payload))

    assert r["text"] == _pretty_payload(payload)
    assert "cost_usd: 1" in r["text"] and "cost_usd: 1.0" not in r["text"]
