"""Retro (W14): a read-only agent assesses every finished run (done/escalated).

``run_retro`` runs at the end of ``run``/``resume``/``approve`` inside the run span.
The agent answers with strict JSON; the orchestrator validates it, drops items
without valid evidence and writes ``retro.json`` plus the code-rendered
``retro.md`` into the run folder. It never raises and never touches the state —
exit code and phase stay exactly as without the retro.
"""

import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal, get_args

import typer
from pydantic import BaseModel, ConfigDict, Field

from adw.agents import REGISTRY
from adw.context import _agent_run
from adw.findings import extract_json_object
from adw.ledger import build_run_record
from adw.state import RUNS_RELPATH

SCHEMA_VERSION = 1
MAX_ITEMS = 10
Cause = Literal[
    "prompt", "context_missing", "gate", "orchestrator", "issue_quality", "model", "target_repo",
]
CAUSES = get_args(Cause)
_SEQ = re.compile(r"seq:(\d+)")


class RetroItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: str
    resolution: str
    better: str
    cause: Cause
    lever: str
    # Missing evidence is no schema error — such an item is dropped (E4).
    evidence: list[str] = []


class RetroAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    items: list[RetroItem] = Field(max_length=MAX_ITEMS)


def _task(record: dict) -> str:
    return (
        "Assess this finished ADW run. Your working directory is its run folder "
        "(events.jsonl, state.json, artifacts, escalation.md/followups.md if present). "
        "Run record:\n\n```json\n"
        + json.dumps(record, ensure_ascii=False, indent=2)
        + "\n```\n\nSearch events.jsonl with Grep for details. Respond ONLY with one JSON "
        'object: {"summary": str, "items": [{"problem": str, "resolution": str, '
        '"better": str, "cause": str, "lever": str, "evidence": [str, ...]}]} — at most '
        f"{MAX_ITEMS} items, cause one of {', '.join(CAUSES)}. Evidence is 'seq:<n>' of "
        "an event in events.jsonl or a relative file path in this folder; items without "
        "valid evidence are discarded. A smooth run may return \"items\": []."
    )


def _event_seqs(run_dir: Path) -> set[int]:
    seqs: set[int] = set()
    try:
        lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return seqs
    for line in lines:
        try:
            seq = json.loads(line).get("seq")
        except (ValueError, AttributeError):
            continue
        if isinstance(seq, int):
            seqs.add(seq)
    return seqs


def _valid_evidence(run_dir: Path, seqs: set[int], evidence: str) -> bool:
    match = _SEQ.fullmatch(evidence)
    if match:
        return int(match.group(1)) in seqs
    path = PurePosixPath(evidence)
    if not evidence or path.is_absolute() or ".." in path.parts:
        return False
    return (run_dir / evidence).is_file()


def render_markdown(retro: dict) -> str:
    """``retro.md`` from exactly the ``retro.json`` dict — no agent text besides
    the field values."""
    lines = [f"# Retro {retro['run_id']} ({retro['phase']})", "", retro["summary"], ""]
    if not retro["items"]:
        lines += ["Keine Einträge.", ""]
    for n, item in enumerate(retro["items"], 1):
        lines += [
            f"## {n}. {item['problem']}",
            "",
            f"- **Lösung:** {item['resolution']}",
            f"- **Besser:** {item['better']}",
            f"- **Ursache:** {item['cause']}",
            f"- **Hebel:** {item['lever']}",
            "- **Belege:**",
            *(f"  - {ev}" for ev in item["evidence"]),
            "",
        ]
    if retro["dropped"]:
        lines += [f"Verworfen (ohne gültigen Beleg): {retro['dropped']}", ""]
    return "\n".join(lines)


def _emit(ctx, payload: dict) -> None:
    ctx.emitter.emit("retro", payload, span=ctx.emitter.current_span_id())


def run_retro(ctx) -> None:
    """The retro of a run ending done/escalated (not dry, no retro.json yet).
    Never raises: every failure is reported on stderr and as a ``retro`` event."""
    state = ctx.state
    if state.phase not in ("done", "escalated") or state.dry_run:
        return
    run_dir = ctx.repo / RUNS_RELPATH / state.run_id
    if (run_dir / "retro.json").exists():
        return
    created: list[Path] = []
    try:
        spec = REGISTRY["retro_agent"]
        task = _task(build_run_record(ctx.repo, state.run_id))
        result = _agent_run(ctx, spec, task, cwd=run_dir)
        answer = RetroAnswer.model_validate(extract_json_object(result.text))
        seqs = _event_seqs(run_dir)
        kept = [
            item for item in answer.items
            if any(_valid_evidence(run_dir, seqs, ev) for ev in item.evidence)
        ]
        retro = {
            "schema_version": SCHEMA_VERSION,
            "run_id": state.run_id,
            "phase": state.phase,
            "summary": answer.summary,
            "items": [item.model_dump() for item in kept],
            "dropped": len(answer.items) - len(kept),
            "model": spec.model,
        }
        for name, text in (
            ("retro.json", json.dumps(retro, sort_keys=True, indent=2) + "\n"),
            ("retro.md", render_markdown(retro)),
        ):
            created.append(run_dir / name)
            (run_dir / name).write_text(text, encoding="utf-8")
        _emit(ctx, {"outcome": "written", "items": len(kept)})
    except Exception as exc:  # noqa: BLE001 — the retro never changes exit or phase
        reason = str(exc) or type(exc).__name__
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                reason += f"; cleanup failed: {path.name}"
        try:
            typer.echo(f"Retro fehlgeschlagen: {reason}", err=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            _emit(ctx, {"outcome": "failed", "reason": reason})
        except Exception:  # noqa: BLE001
            pass
