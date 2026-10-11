"""Presentation formatting used by the templates: durations, costs, timestamps,
markdown and pretty payload blocks."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from markdown_it import MarkdownIt


def _is_number(value) -> bool:
    """A real int/float — ``bool`` is an int subclass but never a measurement."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --- presentation formatting (Aufgabe E): applied in the HTML templates only; the
# JSON API keeps its raw numeric values. Missing values render empty, never 0/null.


def _fmt_duration(seconds) -> str:
    if not _is_number(seconds):
        return ""
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _fmt_cost(cost) -> str:
    if not _is_number(cost):
        return ""
    return f"${cost:.2f}"


def _fmt_ts(ts) -> str:
    """ISO-UTC → ``YYYY-MM-DD HH:MM:SS`` in UTC (no ``Z``, no fractional seconds)."""
    if not isinstance(ts, str) or not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _compact_context(ctx):
    """A context dict for embedding as a data attribute with every null-valued key
    DROPPED (recursively for ``round``). The client reads a missing key exactly as a
    null one (empty display), and the compact form keeps the literal ``null`` out of
    the rendered markup (the polish 'no None/null' rule). ``cost_usd`` is rounded
    HERE (presentation only — the JSON API value stays exact) so no raw float noise
    leaks into the attribute. Serialised via ``tojson`` afterwards, so
    HTML/attribute escaping is unchanged."""
    if not isinstance(ctx, dict):
        return {}
    out = {}
    for k, v in ctx.items():
        if v is None:
            continue
        if k == "round" and isinstance(v, dict):
            out[k] = {rk: rv for rk, rv in v.items() if rv is not None}
        elif k == "cost_usd" and _is_number(v):
            out[k] = round(v, 6)
        else:
            out[k] = v
    return out
def _js_number(value) -> str:
    """A number exactly as JavaScript's ``Number::toString`` renders it.

    The payload formatter runs on BOTH sides (here and as ``prettyPayload`` in
    app.js), and the two disagree on numbers out of the box: ``json.dumps(1.0)`` is
    ``"1.0"`` where JS gives ``"1"``, and the two switch to exponent notation at
    different magnitudes (Python at 1e16 / 1e-5, JS at 1e21 / 1e-7). Porting the JS
    rules here — rather than the reverse — is the only direction that works: a JS
    number carries no int/float distinction, so the client cannot know that ``1``
    was a float. Python's ``repr`` already yields the same shortest round-trip
    digits, so only the placement of the decimal point differs (ECMA-262 §6.1.6.1.20).

    Known limitation (deliberate): an INTEGER beyond 2^53 or >= 1e21 still differs,
    because the client parses JSON numbers as IEEE-754 doubles and loses the exact
    value before formatting. No ADW event payload holds such a number — the largest
    are byte counts and seq numbers — so chasing it would buy nothing.
    """
    if isinstance(value, bool):  # bool is an int subclass — never a number here
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value != value or value in (float("inf"), float("-inf")):
        return "null"  # JSON has no NaN/Infinity; json.dumps' extensions are not JS
    d = Decimal(repr(value)).normalize()
    sign, digits, exp = d.as_tuple()
    text = "".join(str(x) for x in digits)
    if text == "0":
        return "0"  # covers 0.0 and -0.0 (JS prints "0" for both)
    k = len(text)          # number of significant digits
    n = k + exp            # position of the decimal point relative to the digits
    if k <= n <= 21:
        out = text + "0" * (n - k)
    elif 0 < n <= 21:
        out = text[:n] + "." + text[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + text
    else:
        mantissa = text[0] + ("." + text[1:] if k > 1 else "")
        out = f"{mantissa}e{'+' if n - 1 >= 0 else '-'}{abs(n - 1)}"
    return ("-" + out) if sign else out


# Markdown for the payload's multi-line string fields (a run's issue, an agent's
# prompt/answer). `html=False` escapes raw HTML instead of passing it through, and
# the link rules are disabled so a URL in agent-generated text renders as literal
# text — a payload never becomes a click target. Built once: the parser is stateless
# across renders.
_MD = MarkdownIt("gfm-like", {"html": False, "linkify": False})
_MD.disable(["link", "autolink", "reference", "image"])


def _render_markdown(text: str) -> str:
    return _MD.render(text)


def _is_markdown_field(value) -> bool:
    """Only a MULTI-LINE string is rendered. A single-line value belongs in the field
    list, where `key: value` reads better than a one-line paragraph."""
    return isinstance(value, str) and "\n" in value


def _markdown_or_text(value) -> dict:
    """One display block for a free-text field (prompt, answer, assistant message):
    rendered Markdown when it has several lines, literal text otherwise. Keeps the
    single decision — "multi-line means Markdown" — in one place, so the prompt and
    answer tabs read like the payload fields do."""
    if _is_markdown_field(value):
        return {"kind": "markdown", "html": _render_markdown(value)}
    return {"kind": "text", "text": value if isinstance(value, str) else ""}


def _payload_blocks(value):
    """The payload as an alternating sequence of blocks: literal field-list ``text``
    and rendered ``markdown``.

    Markdown is deliberately NOT run over the whole field list. CommonMark folds
    consecutive lines into one paragraph — the scalar fields would run together —
    and reads four-space indentation as a code block, so nested values would be
    mangled. Rendering only the multi-line string values keeps the scaffolding
    legible and turns the parts that ARE Markdown into a document.
    """
    blocks = []
    buf = []

    def flush():
        if buf:
            blocks.append({"kind": "text", "text": "\n".join(buf)})
            buf.clear()

    def walk(node, indent):
        if isinstance(node, dict):
            for key, item in node.items():
                emit(key, item, indent)
        elif isinstance(node, list):
            for item in node:
                if _is_markdown_field(item) or (isinstance(item, (dict, list)) and item):
                    emit(None, item, indent)
                else:
                    buf.append(_pretty_item(item, indent))

    def emit(key, item, indent):
        pad = " " * indent
        head = f"{pad}{key}:" if key is not None else f"{pad}-"
        if _is_markdown_field(item):
            buf.append(head)
            flush()
            blocks.append({"kind": "markdown", "html": _render_markdown(item),
                           "indent": indent + 2})
        elif isinstance(item, (dict, list)) and item:
            buf.append(head)
            walk(item, indent + 2)
        elif key is not None:
            buf.append(_pretty_field(key, item, indent))
        else:
            buf.append(_pretty_item(item, indent))

    if value is None:
        return []
    if isinstance(value, (dict, list)) and not value:
        # A valid, EMPTY payload: `walk` would append nothing and leave the pane
        # blank, so render it the way a nested empty container renders.
        return [{"kind": "text", "text": "{}" if isinstance(value, dict) else "[]"}]
    if not isinstance(value, (dict, list)):
        if _is_markdown_field(value):
            return [{"kind": "markdown", "html": _render_markdown(value), "indent": 0}]
        return [{"kind": "text", "text": _pretty_payload(value)}]
    walk(value, 0)
    flush()
    return blocks


def _pretty_payload(value, indent=0) -> str:
    """An event payload as a readable, indented FIELD LIST — not a JSON dump.

    A `run`'s issue or an agent's prompt is a long string with embedded newlines;
    serialised as JSON it becomes one endless line of ``\n`` escapes. Here a
    multi-line string keeps its real line breaks, indented under its key so it stays
    attached to it; scalars are ``key: value``; objects and lists nest by two spaces;
    an empty container stays on its line. ``app.js`` renders the shared pane with the
    same format (``prettyPayload``), so a payload reads identically wherever it is
    shown — the format is pinned on both sides by tests.
    """
    if value is None:
        return ""  # a missing payload renders as nothing, never as the word "null"
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}"
        # Insertion order, as the server received it. JS `Object.keys` would hoist
        # integer-like keys ahead of the rest — a divergence no event payload can
        # produce (payload keys are identifiers), documented in GUI-SPEC.
        return "\n".join(_pretty_field(k, v, indent) for k, v in value.items())
    if isinstance(value, list):
        if not value:
            return pad + "[]"
        return "\n".join(_pretty_item(v, indent) for v in value)
    return "\n".join(pad + line for line in _scalar_text(value).split("\n"))


def _scalar_text(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return _js_number(value)
    return json.dumps(value, ensure_ascii=False)


def _pretty_field(key, value, indent) -> str:
    pad = " " * indent
    if isinstance(value, (dict, list)):
        if not value:
            return f"{pad}{key}: " + ("{}" if isinstance(value, dict) else "[]")
        return f"{pad}{key}:\n" + _pretty_payload(value, indent + 2)
    text = _scalar_text(value)
    if "\n" in text:
        body = "\n".join(" " * (indent + 2) + line for line in text.split("\n"))
        return f"{pad}{key}:\n{body}"
    return f"{pad}{key}: {text}"


def _pretty_item(value, indent) -> str:
    pad = " " * indent
    if isinstance(value, (dict, list)) and value:
        nested = _pretty_payload(value, indent + 2)
        return f"{pad}-\n{nested}"
    text = _scalar_text(value)
    if "\n" in text:
        lines = text.split("\n")
        rest = "\n".join(" " * (indent + 2) + line for line in lines[1:])
        return f"{pad}- {lines[0]}\n{rest}"
    return f"{pad}- {text}"


def _as_mapping(value) -> dict:
    """``value`` if it is a mapping, else an empty dict. The single place that turns
    an arbitrary — possibly corrupt — JSON value into something safe to ``.get`` on.
    Also covers NESTED values such as ``payload.totals``, which the payload guard
    alone does not reach."""
    return value if isinstance(value, dict) else {}


def _mapping_payload(rec) -> dict:
    """The event's payload if it is a mapping, else an empty dict. A crafted or
    corrupt event whose ``payload`` is a truthy non-mapping (string/list/number)
    must never reach ``.get`` — it would raise and turn a read into a 5xx (AC 7)."""
    return _as_mapping(rec.get("payload"))
