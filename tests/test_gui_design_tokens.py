"""RED tests for A1–A4 — the token layer, the scales and the signal-colour rules.

The stylesheet is redesigned around a NAMED token/scale system: every colour comes
from a custom property defined in exactly two blocks (``:root`` = light,
``@media (prefers-color-scheme: dark)`` = dark); font sizes and spacings come from a
six-step scale; the three roles "a human must act" / "working" / "technically
waiting" are visually distinct; a ``prefers-reduced-motion`` block switches the
transitions off. The concrete token VALUES, class names and CSS wording are NOT
contract surface (contract note / AC 13) — these tests assert the DISCIPLINE
(where literals may live, how many distinct scale values exist, that referenced
variables are really defined, that the two clashing roles no longer share a hue),
not the exact bytes.

Derived from .adw/spec.md (AC 1–6), .adw/plan.md (B4). RED until app.css is rebuilt
around the token layer. The existing GUI tests stay green unchanged (E2: every
pinned class name survives).
"""

import re

from fastapi.testclient import TestClient

from adw.gui.app import create_app

# The twelve normative colour tokens (spec "Normative Definitionen"). Their VALUES
# are not pinned here — only that both themes DEFINE every one of them (AC 4).
NORMATIVE_TOKENS = [
    "--paper", "--surface", "--ink", "--ink-soft", "--rule", "--code-bg",
    "--signal", "--signal-ink", "--ok", "--fail", "--busy", "--wait",
]

# The six-step scales (spec "Skalen"). No other value is allowed for these.
FONT_SIZES = {"0.75rem", "0.8125rem", "0.875rem", "1rem", "1.25rem", "1.5rem"}
SPACINGS = {"0.25", "0.5", "0.75", "1", "1.5", "2"}
RADII = {"3px", "6px"}


def _css() -> str:
    return TestClient(create_app(repos=[])).get("/static/app.css").text


def _block_end(css: str, brace_idx: int) -> int:
    """Index just past the ``}`` that closes the block opened at ``brace_idx``."""
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
    """The inner body of the first block whose header matches ``header_regex``, or
    ``None``. Brace-matched, so a nested ``:root`` inside the dark ``@media`` is kept
    whole."""
    m = re.search(header_regex, css)
    if not m:
        return None
    brace = css.index("{", m.end() - 1) if "{" not in m.group(0) else css.index("{", m.start())
    end = _block_end(css, brace)
    return css[brace + 1: end - 1]


def _strip_token_blocks(css: str) -> str:
    """``css`` with the light ``:root`` block and the whole dark
    ``@media (prefers-color-scheme: dark)`` block removed — the two places where
    colour literals are allowed to live (AC 1)."""
    out = css
    for header in (r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)", r":root\b"):
        while True:
            m = re.search(header, out)
            if not m:
                break
            brace = out.index("{", m.start())
            end = _block_end(out, brace)
            out = out[:m.start()] + out[end:]
    return out


def _rules(css: str):
    """(selector, body) pairs — good enough for flat, non-nested rules (the whole
    sheet outside the two media blocks is flat)."""
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def _colours(body: str):
    """The colour signals a rule carries: hex literals and referenced token names."""
    return set(re.findall(r"#[0-9a-fA-F]{3,8}", body)) | set(
        re.findall(r"var\(\s*(--[\w-]+)", body)
    )


# --- AC 1: colour literals live only in the two token blocks --------------------


def test_no_hex_literal_outside_the_two_token_blocks():
    """AC 1: outside ``:root`` and the dark ``@media`` block, the sheet carries zero
    hex literals — every colour is a token reference (today: 39 literals)."""
    remainder = _strip_token_blocks(_css())
    stray = re.findall(r"#[0-9a-fA-F]{3,8}\b", remainder)
    assert not stray, f"hex literals outside the token blocks: {sorted(set(stray))}"


# --- AC 2: the six-step scales -------------------------------------------------


def test_font_size_spacing_and_radius_scales_are_bounded():
    """AC 2: at most six distinct ``font-size`` values and at most six distinct
    ``padding``/``margin`` rem values, each drawn from the scale; the only radii are
    3px and 6px. Today: nine font sizes, 21 rem spacings, plus 2px/4px radii."""
    css = _css()

    sizes = {v.strip() for v in re.findall(r"font-size\s*:\s*([^;}\n]+)", css)}
    assert len(sizes) <= 6, f"{len(sizes)} distinct font sizes: {sorted(sizes)}"
    assert sizes <= FONT_SIZES, f"off-scale font sizes: {sorted(sizes - FONT_SIZES)}"

    decls = re.findall(r"(?:padding|margin)(?:-\w+)?\s*:\s*([^;}\n]+)", css)
    rems = {n for d in decls for n in re.findall(r"([\d.]+)rem", d)}
    assert len(rems) <= 6, f"{len(rems)} distinct rem spacings: {sorted(rems)}"
    assert rems <= SPACINGS, f"off-scale spacings: {sorted(rems - SPACINGS)}"

    radii = {r for d in re.findall(r"border-radius\s*:\s*([^;}\n]+)", css)
             for r in re.findall(r"[\d.]+px", d)}
    assert radii <= RADII, f"off-scale radii: {sorted(radii - RADII)}"


# --- AC 3: the three "fallback" variables become real tokens -------------------


def test_no_var_reference_relies_on_a_fallback_for_an_undefined_token():
    """AC 3: every ``var(--x, literal)`` names a token that is actually DEFINED, so
    the literal fallback never has to win. Today ``--ok``/``--err``/``--code-bg`` are
    only ever referenced with a literal fallback and never defined."""
    css = _css()
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    referenced = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    undefined = referenced - defined
    assert not undefined, f"variables referenced but never defined: {sorted(undefined)}"


def test_ok_fail_and_code_bg_are_defined_in_both_themes():
    """AC 3/4: ``--ok``, ``--code-bg`` and a failure token (``--fail`` or ``--err``)
    are defined in BOTH the light ``:root`` and the dark ``@media`` block."""
    css = _css()
    light = _extract_block(css, r":root\b")
    dark = _extract_block(css, r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)")
    assert light is not None, "no :root token block"
    assert dark is not None, "no dark prefers-color-scheme block"
    for block, name in ((light, "light"), (dark, "dark")):
        assert "--ok:" in block.replace(" ", ""), f"--ok missing in {name}"
        assert "--code-bg:" in block.replace(" ", ""), f"--code-bg missing in {name}"
        squished = block.replace(" ", "")
        assert "--fail:" in squished or "--err:" in squished, f"failure token missing in {name}"


# --- AC 4: dark mode defines every token; reduced-motion switches off ----------


def test_dark_theme_redefines_every_token_and_reduced_motion_switches_off():
    """AC 4: the dark ``@media`` block redefines EACH of the twelve normative tokens
    (the theme switch is purely a token swap), and a
    ``@media (prefers-reduced-motion: reduce)`` block exists and turns the
    transitions off (E9)."""
    css = _css()
    dark = _extract_block(css, r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)")
    assert dark is not None, "no @media (prefers-color-scheme: dark) block"
    squished = dark.replace(" ", "")
    missing = [tok for tok in NORMATIVE_TOKENS if f"{tok}:" not in squished]
    assert not missing, f"dark theme does not redefine: {missing}"

    reduced = _extract_block(css, r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)")
    assert reduced is not None, "no @media (prefers-reduced-motion: reduce) block"
    assert "transition" in reduced, "reduced-motion block does not address transitions"


# --- AC 5: the #3651d6 double role is resolved ---------------------------------


def test_waiting_and_awaiting_no_longer_share_a_colour():
    """AC 5: "technically waiting" (``.node-waiting``) and "a human must act"
    (``.phase-awaiting`` / ``.status-awaiting_approval``) carry DIFFERENT colours,
    and "working" (``.phase-active`` / ``.node-running``) a third, distinct one.
    Today all three approval/waiting selectors render in the single hue ``#3651d6``."""
    waiting, awaiting, busy = set(), set(), set()
    for sel, body in _rules(_css()):
        if "node-waiting" in sel:
            waiting |= _colours(body)
        if "phase-awaiting" in sel or "status-awaiting_approval" in sel:
            awaiting |= _colours(body)
        if "phase-active" in sel or "node-running" in sel:
            busy |= _colours(body)

    assert waiting, "no colour found for .node-waiting"
    assert awaiting, "no colour found for the awaiting/approval selectors"
    assert not (waiting & awaiting), f"waiting and awaiting still share {waiting & awaiting}"
    assert not (busy & waiting), f"working and waiting share {busy & waiting}"
    assert not (busy & awaiting), f"working and awaiting share {busy & awaiting}"


# --- AC 4 / dark-mode contrast: interactive chrome stays legible ---------------


def _decls(block: str) -> dict:
    """The custom-property declarations of a token block, as ``{name: value}``."""
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))


def _resolve(name: str, decls: dict):
    """The hex value a token resolves to, following ``var(--alias)`` chains within a
    theme's declarations, or ``None`` if it is not a colour."""
    seen = set()
    value = decls.get(name, "").strip()
    while value.startswith("var("):
        ref = re.match(r"var\(\s*(--[\w-]+)", value)
        if not ref or ref.group(1) in seen:
            return None
        seen.add(ref.group(1))
        value = decls.get(ref.group(1), "").strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    return value if m else None


def _luminance(hex6: str) -> float:
    def channel(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _rule_body(css: str, selector: str) -> str:
    for sel, body in _rules(css):
        # A rule may be preceded by a comment that the greedy selector capture
        # swallows; strip comments before comparing.
        clean = re.sub(r"/\*.*?\*/", "", sel, flags=re.S).strip()
        if clean == selector:
            return body
    return ""


def _fg_token(body: str):
    m = re.search(r"(?<![-\w])color\s*:\s*var\(\s*(--[\w-]+)", body)
    return m.group(1) if m else None


def test_tab_buttons_and_links_meet_contrast_in_both_themes():
    """AC 4 / P2: the tab buttons and ordinary links carry a token foreground (a
    <button>/<a> does not inherit body colour), so they stay legible in dark mode
    instead of showing browser-default dark button text or blue/purple link text.
    The computed foreground/background contrast is >= 4.5:1 in BOTH themes — the
    actual token values are read from the sheet, not assumed."""
    css = _css()
    light = _decls(_extract_block(css, r":root\b"))
    dark_only = _decls(_extract_block(css, r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)"))
    dark = {**light, **dark_only}  # dark inherits :root aliases, overrides base tokens

    tab_body = _rule_body(css, ".tab-btn")
    tab_fg = _fg_token(tab_body)
    tab_bg = re.search(r"background\s*:\s*var\(\s*(--[\w-]+)", tab_body)
    assert tab_fg and tab_bg, "the tab button sets no token foreground/background"

    link_fg = _fg_token(_rule_body(css, "a") or _rule_body(css, "a, a:visited"))
    assert link_fg, "ordinary links carry no token foreground"
    # The visited state must be styled too (never browser-default purple).
    assert "a:visited" in css

    for theme, decls in (("light", light), ("dark", dark)):
        tb = _contrast(_resolve(tab_fg, decls), _resolve(tab_bg.group(1), decls))
        assert tb >= 4.5, f"{theme}: tab button contrast {tb:.2f} < 4.5"
        lk = _contrast(_resolve(link_fg, decls), _resolve("--paper", decls))
        assert lk >= 4.5, f"{theme}: link-on-page contrast {lk:.2f} < 4.5"


# --- AC 6: the pinned class names / mechanisms survive the rework --------------


def test_pinned_selectors_and_dry_run_mechanism_are_preserved():
    """AC 6/E2: every pinned string is still in the sheet and the dry-run marking
    keeps a sticky/fixed viewport-persistence rule — only how the selectors LOOK
    changes, never their names."""
    css = _css()
    for pinned in (".phase-active", ".phase-completed", ".node-running", ".node-done",
                   "node-waiting", "awaiting", "trace-summary", "nowrap"):
        assert pinned in css, pinned

    dry_sticky = [
        sel for sel, body in _rules(css.lower())
        if "dry" in sel and re.search(r"position\s*:\s*(sticky|fixed)", body)
    ]
    assert dry_sticky, "the dry-run marking lost its sticky/fixed rule"
