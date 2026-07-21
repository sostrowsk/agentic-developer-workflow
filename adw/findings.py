"""Findings schema: structured review results between all workflow nodes.

Parser contract (deliberately strict): reviewers are prompted to "respond ONLY
with the JSON object (optionally in a ```json fence)". Accepted is exclusively

  (a) text that as a whole (after strip) is ONE JSON object, or
  (b) the content of the LAST ```json fence in the text.

Everything else — prose around bare JSON, drafts, quotes, truncated or
wrapped answers — is a FindingsParseError, which the caller treats as a
retry/escalation case. Tolerance heuristics (prose extraction,
draft/quote detection) are deliberately NOT implemented: they cannot be
sealed against adversarial outputs and risk stale-ok verdicts.
A parse error is safe (retry), a wrong "ok" is not.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SCHEMA_INSTRUCTION = """\
Respond EXCLUSIVELY with a JSON object following exactly this schema
(no prose before or after, optionally in a ```json fence):
{
  "verdict": "ok | needs_fixes",
  "findings": [{
    "severity": "P1 | P2 | P3",
    "lane": "frontend | backend | unknown",
    "file": "path/relative/to/repo",
    "issue": "description of the problem",
    "remediation_plan": ["Step 1", "Step 2"]
  }]
}
Rules: verdict "ok" only with an empty findings array; "needs_fixes" requires
at least one Finding; all fields are mandatory."""


class FindingsParseError(Exception):
    """Agent/Codex output did not conform to the strict review JSON contract."""

    def __init__(self, reason: str, raw: str):
        self.raw = raw
        super().__init__(f"{reason}\n--- Roh-Output (gekürzt) ---\n{raw[:2000]}")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["P1", "P2", "P3"]
    lane: Literal["frontend", "backend", "unknown"]

    @field_validator("lane", mode="before")
    @classmethod
    def _coerce_unrecognized_lane(cls, value: object) -> object:
        # Reviewer erfinden gelegentlich Lane-Labels ("ios", …). Kein Finding
        # wird verworfen — unbekannte STRING-Labels werden wie "unknown"
        # behandelt; Nicht-Strings bleiben Schema-Verletzung (ValidationError).
        if isinstance(value, str) and value not in ("frontend", "backend", "unknown"):
            return "unknown"
        return value

    file: str
    issue: str
    remediation_plan: list[str]
    category: Literal["scope_gap", "implementation", "trivial"] | None = None


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["ok", "needs_fixes"]
    findings: list[Finding]

    @model_validator(mode="after")
    def _verdict_matches_findings(self) -> "ReviewResult":
        if self.verdict == "ok" and self.findings:
            raise ValueError("verdict 'ok' darf keine findings enthalten")
        if self.verdict == "needs_fixes" and not self.findings:
            raise ValueError("verdict 'needs_fixes' braucht mindestens ein finding")
        return self


class _DuplicateKeyError(ValueError):
    """JSON object contains the same key multiple times — values would be silently overwritten."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateKeyError(key)
        obj[key] = value
    return obj


_MAX_INT_DIGITS = 100


class _OversizedNumberError(ValueError):
    """Integer token beyond any plausible review size (adversarial)."""


def _bounded_int(token: str) -> int:
    # Unabhängig von sys.set_int_max_str_digits — die Grenze gehört dem Parser,
    # nicht dem Interpreter-Setting.
    if len(token.lstrip("+-")) > _MAX_INT_DIGITS:
        raise _OversizedNumberError(f"{len(token)} Stellen")
    return int(token)


_MAX_NESTING = 100


def _too_deep(value: object, limit: int = _MAX_NESTING) -> bool:
    """Iterative depth check — legitimate reviews are flat, extremes are adversarial."""
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return False


# Getrennte Opener-Regexes ohne überlappende Quantifier (kein Backtracking):
# Marker greedy, Rest pauschal — die Info-String-Prüfung passiert danach in Code.
_BACKTICK_OPEN = re.compile(r"^ {0,3}(`{3,})(.*)$")
_TILDE_OPEN = re.compile(r"^ {0,3}(~{3,})(.*)$")


def _match_fence_open(line: str) -> tuple[str, str] | None:
    """(Marker, info string) of a fence opener — or None for prose.

    CommonMark: the info string of a backtick fence must not contain
    backticks (otherwise the line is prose); for tilde fences anything is allowed.
    """
    match = _BACKTICK_OPEN.match(line)
    if match is not None:
        info = match.group(2).strip(" \t")
        if "`" in info:
            return None
        return match.group(1), info
    match = _TILDE_OPEN.match(line)
    if match is not None:
        return match.group(1), match.group(2).strip(" \t")
    return None


def _last_json_fence_content(text: str) -> str | None:
    """Content of the last ```json fence — which is authoritative per contract.

    Line-based fence scanner following CommonMark ground rules: a fence opens
    with >=3 backticks/tildes at the start of a line and closes only with a PURE
    line of the same character of at least equal length. As a result,
    ```json blocks inside outer fences (```` ```` ````, ``~~~``) are just content, and
    a line like ```` ```python ```` does not close an open json fence.
    A json fence without a closer = truncated output → fail-closed.
    """
    last: str | None = None
    open_char: str | None = None
    open_len = 0
    is_json = False
    buf: list[str] = []
    # Nur CR/LF sind Zeilengrenzen — splitlines() würde auch U+2028/U+2029
    # zerschneiden, die in JSON-Strings erlaubt sind.
    for line in re.split(r"\r\n|\r|\n", text):
        if open_char is None:
            opened = _match_fence_open(line)
            if opened:
                marker, info = opened
                open_char, open_len = marker[0], len(marker)
                # Autoritativ ist NUR der Backtick-```json-Fence (Kontrakt);
                # ~~~json o. Ä. gilt als Beispiel-Block.
                is_json = open_char == "`" and info.lower() == "json"
                buf = []
            continue
        stripped = line.rstrip(" \t")
        core = stripped.lstrip(" ")
        indent = len(stripped) - len(core)
        if indent <= 3 and core == open_char * len(core) and len(core) >= open_len:
            if is_json:
                last = "\n".join(buf)
            open_char = None
            continue
        buf.append(line)
    if open_char is not None:
        raise FindingsParseError(
            "Fence ohne schließenden Marker am Ende des Outputs (abgeschnittene Antwort)", text
        )
    return last


def extract_review_result(text: str) -> ReviewResult:
    """Parses a review result according to the strict contract (see module docstring).

    The candidate is the last ```json fence, otherwise the entire text. The candidate
    must be entirely ONE JSON object; duplicate keys, extreme nesting depth
    and non-objects are rejected fail-closed.
    """
    candidate = _last_json_fence_content(text)
    if candidate is None:
        candidate = text
    candidate = candidate.strip()
    try:
        data = json.loads(
            candidate, object_pairs_hook=_reject_duplicate_keys, parse_int=_bounded_int
        )
    except (ValueError, RecursionError) as exc:
        # ValueError deckt JSONDecodeError, _DuplicateKeyError und Pythons
        # int-Digit-Limit (>4300 Stellen) ab; RecursionError extreme Nesting-
        # Tiefe im Decoder — alles fail-closed.
        reason = f"Kein reines Review-JSON ({exc.__class__.__name__})"
        raise FindingsParseError(reason, text) from exc
    if not isinstance(data, dict):
        raise FindingsParseError("Top-Level-JSON ist kein Objekt", text)
    if _too_deep(data):
        raise FindingsParseError("JSON übersteigt die maximale Nesting-Tiefe", text)
    return ReviewResult.model_validate(data)
