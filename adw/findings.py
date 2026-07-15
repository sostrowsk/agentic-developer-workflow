"""Findings-Schema: strukturierte Review-Ergebnisse zwischen allen Workflow-Nodes.

Parser-Kontrakt (bewusst strikt): Reviewer werden auf "antworte NUR mit dem
JSON-Objekt (ggf. im ```json-Fence)" geprompted. Akzeptiert wird ausschließlich

  (a) Text, der als Ganzes (nach strip) EIN JSON-Objekt ist, oder
  (b) der Inhalt des LETZTEN ```json-Fence im Text.

Alles andere — Prosa um nacktes JSON, Entwürfe, Zitate, abgeschnittene oder
verpackte Antworten — ist ein FindingsParseError, den der Aufrufer als
Retry-/Eskalationsfall behandelt. Toleranz-Heuristiken (Prosa-Extraktion,
Entwurfs-/Zitat-Erkennung) sind absichtlich NICHT implementiert: Sie sind
gegen adversariale Outputs nicht abdichtbar und riskieren Stale-ok-Verdicts.
Ein Parse-Fehler ist safe (Retry), ein falsches "ok" nicht.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

SCHEMA_INSTRUCTION = """\
Antworte AUSSCHLIESSLICH mit einem JSON-Objekt nach exakt diesem Schema
(keine Prosa davor oder danach, optional in einem ```json-Fence):
{
  "verdict": "ok | needs_fixes",
  "findings": [{
    "severity": "P1 | P2 | P3",
    "lane": "frontend | backend | unknown",
    "file": "pfad/relativ/zum/repo",
    "issue": "Beschreibung des Problems",
    "remediation_plan": ["Schritt 1", "Schritt 2"]
  }]
}
Regeln: verdict "ok" nur mit leerem findings-Array; "needs_fixes" braucht
mindestens ein Finding; alle Felder sind Pflicht."""


class FindingsParseError(Exception):
    """Agent-/Codex-Output entsprach nicht dem strikten Review-JSON-Kontrakt."""

    def __init__(self, reason: str, raw: str):
        self.raw = raw
        super().__init__(f"{reason}\n--- Roh-Output (gekürzt) ---\n{raw[:2000]}")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["P1", "P2", "P3"]
    lane: Literal["frontend", "backend", "unknown"]
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
    """JSON-Objekt enthält denselben Key mehrfach — Werte würden still überschrieben."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateKeyError(key)
        obj[key] = value
    return obj


_MAX_INT_DIGITS = 100


class _OversizedNumberError(ValueError):
    """Integer-Token jenseits jeder plausiblen Review-Größe (adversarial)."""


def _bounded_int(token: str) -> int:
    # Unabhängig von sys.set_int_max_str_digits — die Grenze gehört dem Parser,
    # nicht dem Interpreter-Setting.
    if len(token.lstrip("+-")) > _MAX_INT_DIGITS:
        raise _OversizedNumberError(f"{len(token)} Stellen")
    return int(token)


_MAX_NESTING = 100


def _too_deep(value: object, limit: int = _MAX_NESTING) -> bool:
    """Iterative Tiefenprüfung — legitime Reviews sind flach, Extremes ist adversarial."""
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
    """(Marker, Info-String) eines Fence-Openers — oder None für Prosa.

    CommonMark: Der Info-String eines Backtick-Fence darf keine Backticks
    enthalten (sonst ist die Zeile Prosa); bei Tilde-Fences ist alles erlaubt.
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
    """Inhalt des letzten ```json-Fence — der ist per Kontrakt autoritativ.

    Zeilenbasierter Fence-Scanner nach CommonMark-Grundregeln: Ein Fence öffnet
    mit >=3 Backticks/Tilden am Zeilenanfang und schließt nur mit einer PUREN
    Zeile desselben Zeichens in mindestens gleicher Länge. Dadurch sind
    ```json-Blöcke in äußeren Fences (```` ```` ````, ``~~~``) nur Content, und
    eine Zeile wie ```` ```python ```` schließt keinen offenen json-Fence.
    Ein json-Fence ohne Closer = abgeschnittener Output → fail-closed.
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
    """Parst ein Review-Ergebnis nach dem strikten Kontrakt (siehe Modul-Docstring).

    Kandidat ist der letzte ```json-Fence, sonst der gesamte Text. Der Kandidat
    muss vollständig EIN JSON-Objekt sein; doppelte Keys, extreme Nesting-Tiefe
    und Nicht-Objekte werden fail-closed abgelehnt.
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
