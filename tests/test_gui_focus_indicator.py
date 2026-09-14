"""RED tests for A3 — a visible focus indicator, everywhere, in both themes (AC 6/7).

``app.css`` contains today not a single ``:focus`` or ``:focus-visible`` rule: even the
natively focusable elements fall back to the browser default ring, on the Brief-1
backgrounds and in a dark mode that did not exist before. This brief adds a uniform,
clearly visible focus indicator with its OWN token (source may be ``--busy``, but NOT
``--signal`` — reserved for "a human must act", E5), with a contour of its own (not
colour alone), reaching at least 3:1 against ``--paper`` AND ``--surface`` in both
themes, and it is nowhere removed with a bare ``outline: none``.

The token VALUES, names and CSS wording are not contract surface (contract note) —
these tests assert the DISCIPLINE: a visible focus rule exists, no bare ``outline:
none``, the indicator token is not ``--signal`` and clears 3:1 in both themes, computed
from the sheet's own token values. Derived from .adw/spec.md (A3, AC 6/7),
.adw/plan.md (B6) and .adw/contract.yaml (x-adw-template-behavior.focus).
"""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app


def _css() -> str:
    return TestClient(create_app(repos=[])).get("/static/app.css").text


def _block_end(css: str, brace_idx: int) -> int:
    depth = 0
    for i in range(brace_idx, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(css)


def _extract_block(css: str, header_regex: str):
    m = re.search(header_regex, css)
    if not m:
        return None
    brace = css.index("{", m.start())
    end = _block_end(css, brace)
    return css[brace + 1: end - 1]


def _rules(css: str):
    """(selector, body) pairs for the flat rules (the sheet outside the two token
    media blocks is flat, so the innermost brace match is one rule each)."""
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def _focus_rules(css: str):
    return [(sel, body) for sel, body in _rules(css) if ":focus" in sel]


def _decls(block: str) -> dict:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))


def _resolve(name: str, decls: dict):
    """The hex a token resolves to, following ``var(--alias)`` chains within a theme's
    declarations, or ``None`` if it is not a plain 6-digit colour."""
    seen = set()
    value = decls.get(name, "").strip()
    while value.startswith("var("):
        ref = re.match(r"var\(\s*(--[\w-]+)", value)
        if not ref or ref.group(1) in seen:
            return None
        seen.add(ref.group(1))
        value = decls.get(ref.group(1), "").strip()
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else None


def _luminance(hex6: str) -> float:
    def channel(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(fg: str, bg: str) -> float:
    hi, lo = max(_luminance(fg), _luminance(bg)), min(_luminance(fg), _luminance(bg))
    return (hi + 0.05) / (lo + 0.05)


def _indicator_tokens(css: str):
    """The tokens the focus rules use for their contour — the ``var(--x)`` referenced
    in an ``outline``/``outline-color``/``box-shadow`` declaration of a ``:focus`` rule."""
    tokens = set()
    for _sel, body in _focus_rules(css):
        for decl in body.split(";"):
            if ":" not in decl:
                continue
            prop, value = decl.split(":", 1)
            if prop.strip() in ("outline", "outline-color", "box-shadow"):
                tokens |= set(re.findall(r"var\(\s*(--[\w-]+)", value))
    return tokens


# --- AC 6: a visible focus indicator exists; no bare outline:none ----------------


def test_a_visible_focus_rule_exists_and_no_bare_outline_none(tmp_path):
    """AC 6: at least one ``:focus``/``:focus-visible`` rule sets a visible indicator
    (an outline that is not ``none``/``0``, or a box-shadow), and nowhere is
    ``outline`` set to ``none``/``0`` without a compensating box-shadow in the same
    rule (today: no focus rule at all)."""
    css = _css()

    focus_rules = _focus_rules(css)
    assert focus_rules, "app.css contains no :focus or :focus-visible rule"

    def visible(body: str) -> bool:
        if "box-shadow" in body:
            return True
        m = re.search(r"(?<![-\w])outline\s*:\s*([^;]+)", body)
        return bool(m and m.group(1).strip() not in ("none", "0"))

    assert any(visible(body) for _sel, body in focus_rules), (
        "no focus rule sets a visible indicator (outline or box-shadow)"
    )

    for _sel, body in _rules(css):
        for m in re.finditer(r"(?<![-\w])outline\s*:\s*([^;]+)", body):
            if m.group(1).strip() in ("none", "0"):
                assert "box-shadow" in body, "outline:none without a compensating box-shadow"


# --- AC 7: the focus token is not --signal and clears 3:1 in both themes ---------


def test_focus_token_is_not_signal_and_meets_3to1_in_both_themes(tmp_path):
    """AC 7: the focus indicator uses its own token (never ``--signal``, E5), defined
    in both themes, reaching at least 3:1 against ``--paper`` and ``--surface`` in
    light and dark — computed from the sheet's own token values (``--busy`` at 6.41 /
    6.92 is an admissible source)."""
    css = _css()
    light = _decls(_extract_block(css, r":root\b"))
    dark_only = _decls(_extract_block(css, r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)"))
    dark = {**light, **dark_only}

    tokens = _indicator_tokens(css)
    assert tokens, "no focus rule references a token for its outline/box-shadow colour"
    assert "--signal" not in tokens, "the focus indicator uses --signal (reserved, E5)"

    for token in tokens:
        for theme, decls in (("light", light), ("dark", dark)):
            colour = _resolve(token, decls)
            assert colour, f"{theme}: focus token {token} is not defined as a colour"
            for surface in ("--paper", "--surface"):
                bg = _resolve(surface, decls)
                ratio = _contrast(colour, bg)
                assert ratio >= 3.0, f"{theme}: focus {token} vs {surface} is {ratio:.2f} < 3.0"
