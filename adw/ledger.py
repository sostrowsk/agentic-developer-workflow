"""Run ledger (W13): a deterministic run record plus the case bundle, exported into
a central, private Git ledger that survives the pruning of run directories.

``build_run_record`` reads a run directory (state + event log) and returns the
``run-record.json`` dict (schema_version 1) — no writes, no model, the only
subprocess is ``git remote get-url origin``. ``export_run`` writes record and
bundle to ``$ADW_LEDGER/runs/<slug>/<run_id>/``, commits in the ledger clone and
pushes fail-open. It never raises and takes no run locks (its callers may already
hold them). ADW never creates or clones the ledger.
"""

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from adw.config import AdwConfig
from adw.events import EventEmitter
from adw.git import run_git
from adw.state import RUNS_RELPATH, RunState

SCHEMA_VERSION = 1
_DEFAULT_LEDGER = "~/.adw/ledger"
_GIT_TIMEOUT = 30
_PUSH_TIMEOUT = 60
_EXPORT_EVENT = "ledger.export"
# Artifacts copied verbatim into the bundle when present (E4); never drafts/ or trees/.
_ARTIFACTS = (
    "issue.md",
    "spec.md",
    "plan.md",
    "contract.yaml",
    "spec-summary.md",
    "plan-summary.md",
    "followups.md",
    "escalation.md",
    "state.json",
)
_SCP_URL = re.compile(r"^[^/:]+:(?!/)")


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


# --- reading a run directory -----------------------------------------------------


def _log_lines(run_dir: Path) -> list[bytes] | None:
    """Raw lines (with line endings) of events.jsonl, else the decompressed .gz;
    None without any log."""
    plain = run_dir / "events.jsonl"
    if plain.is_file():
        return plain.read_bytes().splitlines(keepends=True)
    gz = run_dir / "events.jsonl.gz"
    if gz.is_file():
        with gzip.open(gz, "rb") as fh:
            return fh.read().splitlines(keepends=True)
    return None


def _parse(line: bytes) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None  # a truncated fragment is skipped
    return record if isinstance(record, dict) else None


def _read_events(run_dir: Path) -> list[dict]:
    return [r for r in map(_parse, _log_lines(run_dir) or []) if r is not None]


def _spans(events: list[dict], type_: str) -> list[tuple[dict, dict | None]]:
    """(start, end) per span of ``type_`` in start order; end None if missing."""
    ends = {e.get("span"): e for e in events if e.get("type") == type_ and e.get("kind") == "end"}
    return [
        (e, ends.get(e.get("span")))
        for e in events
        if e.get("type") == type_ and e.get("kind") == "start"
    ]


def _payload(record: dict | None) -> dict:
    payload = record.get("payload") if record else None
    return payload if isinstance(payload, dict) else {}


def _parse_ts(ts) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _count(items) -> int:
    return len(items) if isinstance(items, list) else 0


def _sum_totals(ends: list[dict], key: str):
    totals = [_payload(e).get("totals") for e in ends]
    values = [t.get(key) for t in totals if isinstance(t, dict)]
    values = [v for v in values if _is_number(v)]
    return sum(values) if values else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _issue_title(run_dir: Path) -> str | None:
    text = _read_text(run_dir / "issue.md")
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip().lstrip("#").strip()
    return None


def _paragraphs(lines: list[str]) -> list[list[str]]:
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in [*lines, ""]:
        if line.strip():
            current.append(line.rstrip())
        elif current:
            paragraphs.append(current)
            current = []
    return paragraphs


def _escalation_reason(run_dir: Path) -> str | None:
    text = _read_text(run_dir / "escalation.md")
    if text is None:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Grund":
            below = _paragraphs(lines[i + 1 :])
            if below:
                return "\n".join(below[0]).strip()
    for paragraph in _paragraphs(lines):
        if not paragraph[0].lstrip().startswith("#"):
            return "\n".join(paragraph).strip()
    return None


def _followups(run_dir: Path) -> int:
    text = _read_text(run_dir / "followups.md")
    return sum(1 for line in (text or "").splitlines() if line.startswith("- "))


def _repo_slug(repo: Path) -> str:
    fallback = repo.resolve().name
    try:
        result = run_git(repo, "remote", "get-url", "origin", timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return fallback
    url = result.stdout.strip() if result.returncode == 0 else ""
    url = url.removesuffix("/").removesuffix(".git")
    if "://" in url:
        _, _, path = url.split("://", 1)[1].partition("/")
    elif _SCP_URL.match(url):
        path = url.split(":", 1)[1]
    else:
        path = ""
    path = path.strip("/")
    return path or fallback


def _agent(start: dict, end: dict | None) -> dict:
    begin = _payload(start)
    finish = _payload(end)
    duration = None
    if end is not None:
        t0, t1 = _parse_ts(start.get("ts")), _parse_ts(end.get("ts"))
        if t0 is not None and t1 is not None:
            duration = round((t1 - t0).total_seconds(), 3)
    return {
        "agent": begin.get("agent"),
        "model": begin.get("model"),
        "effort": None,  # not recorded until W2
        "max_turns": begin.get("max_turns"),
        "timeout_s": begin.get("timeout_s"),
        "duration_s": duration,
        "cost_usd": finish.get("cost_usd") if end is not None else None,
        "usage": finish.get("usage") if end is not None else None,
        "is_error": finish.get("is_error") if end is not None else None,
    }


def build_run_record(repo, run_id: str) -> dict:
    """The run record (schema_version 1) of one run — read-only, deterministic."""
    repo = Path(repo)
    state = RunState.load(repo, run_id)
    run_dir = repo / RUNS_RELPATH / run_id
    events = _read_events(run_dir)
    runs = _spans(events, "run")
    run_ends = [end for _, end in runs if end is not None]
    versions: list[str] = []
    for start, _ in runs:
        version = _payload(start).get("adw_version")
        if version is not None and version not in versions:
            versions.append(version)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": state.run_id,
        "repo_slug": _repo_slug(repo),
        "adw_version": _payload(runs[0][0]).get("adw_version") if runs else None,
        "adw_versions": versions,
        "started_at": runs[0][0].get("ts") if runs else None,
        "ended_at": runs[-1][1].get("ts") if runs and runs[-1][1] is not None else None,
        "phase": state.phase,
        "dry_run": state.dry_run,
        "parallel": state.parallel,
        "gates": {"skip_approval": state.skip_approval, "spec_approval": state.spec_approval},
        "issue_title": _issue_title(run_dir),
        "escalation_reason": _escalation_reason(run_dir),
        "invocations": len(runs),
        "approvals": sum(1 for e in events if e.get("type") == "approval"),
        "duration_s": _sum_totals(run_ends, "duration"),
        "cost_usd": _sum_totals(run_ends, "cost"),
        "tokens": _sum_totals(run_ends, "tokens"),
        "lanes": {
            name: {"gate_iterations": lane.gate_iterations, "red_confirmed": lane.red_confirmed}
            for name, lane in state.lanes.items()
        },
        "review_rounds": state.review_rounds,
        "ci_reentries": state.ci_reentries,
        "fix_cycles": sum(lane.fix_cycles for lane in state.lanes.values()),
        "circuit_breakers": sum(1 for e in events if e.get("type") == "circuit_breaker"),
        "agents": [_agent(start, end) for start, end in _spans(events, "agent.run")],
        "findings": [
            {
                "finding_key": _payload(e).get("finding_key"),
                "severity": _payload(e).get("severity"),
                "action": _payload(e).get("action"),
                "reason": _payload(e).get("reason"),
                "phase": e.get("phase"),
                "round": e.get("round"),
            }
            for e in events
            if e.get("type") == "triage.decision"
        ],
        "review_counts": [
            {
                "phase": e.get("phase"),
                "round": e.get("round"),
                "n": _count(_payload(e).get("findings")),
            }
            for e in events
            if e.get("type") == "codex.review" and e.get("kind") == "end"
        ],
        "followups": _followups(run_dir),
    }


# --- export ----------------------------------------------------------------------


def _ledger_path() -> Path | None:
    """The ledger clone, or None when ADW_LEDGER is no Git work tree root."""
    path = Path(os.environ.get("ADW_LEDGER", _DEFAULT_LEDGER)).expanduser()
    if not path.is_dir():
        return None
    try:
        result = run_git(path, "rev-parse", "--show-toplevel", timeout=_GIT_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or Path(result.stdout.strip()).resolve() != path.resolve():
        return None
    return path


def _git_ok(ledger: Path, *args: str) -> subprocess.CompletedProcess:
    result = run_git(ledger, *args, timeout=_GIT_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result


def _final_diff(repo: Path, state: RunState) -> str | None:
    parts = []
    for name in sorted(state.lanes):
        lane = state.lanes[name]
        if not lane.base_sha:
            continue
        exists = run_git(
            repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{lane.branch}",
            timeout=_GIT_TIMEOUT,
        )
        if exists.returncode != 0:
            continue
        parts.append(_git_ok(repo, "diff", f"{lane.base_sha}..{lane.branch}").stdout)
    return "".join(parts) if parts else None


def _write_bundle(repo: Path, state: RunState, record: dict, dest: Path) -> None:
    run_dir = repo / RUNS_RELPATH / state.run_id
    dest.mkdir(parents=True)
    (dest / "run-record.json").write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    names = [*_ARTIFACTS, *sorted(p.name for p in run_dir.glob("authoring-*-known-findings.md"))]
    for name in names:
        source = run_dir / name
        if source.is_file():
            shutil.copyfile(source, dest / name)
    lines = _log_lines(run_dir)
    if lines is not None:
        kept = [
            line for line in lines if (_parse(line) or {}).get("type") != _EXPORT_EVENT
        ]
        (dest / "events.jsonl.gz").write_bytes(gzip.compress(b"".join(kept), mtime=0))
    diff = _final_diff(repo, state)
    if diff is not None:
        (dest / "final.diff").write_text(diff, encoding="utf-8")


def _push(ledger: Path) -> bool:
    try:
        result = run_git(ledger, "push", timeout=_PUSH_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _warn(f"Ledger-Push fehlgeschlagen (Commit bleibt lokal): {exc}")
        return False
    if result.returncode != 0:
        _warn(f"Ledger-Push fehlgeschlagen (Commit bleibt lokal): {result.stderr.strip()}")
        return False
    return True


def _export(repo: Path, run_id: str) -> tuple[str, bool | None, str | None]:
    state = RunState.load(repo, run_id)
    if state.dry_run:
        return "skipped", None, "dry_run"
    ledger = _ledger_path()
    if ledger is None:
        path = Path(os.environ.get("ADW_LEDGER", _DEFAULT_LEDGER)).expanduser()
        _warn(f"Ledger nicht gefunden: {path}, Export übersprungen")
        return "skipped", None, "no_ledger"
    record = build_run_record(repo, run_id)
    slug = record["repo_slug"]
    rel = Path("runs") / slug.replace("/", "__") / run_id
    runs_dir = (ledger / "runs").resolve()
    dest = ledger / rel
    if not dest.resolve().is_relative_to(runs_dir) or dest.resolve() == runs_dir:
        raise RuntimeError(f"Ledger-Ziel liegt außerhalb von runs/: {rel}")
    if dest.exists():
        shutil.rmtree(dest)
    _write_bundle(repo, state, record, dest)
    _git_ok(ledger, "add", "--", rel.as_posix())
    staged = run_git(ledger, "diff", "--cached", "--quiet", "--", rel.as_posix(),
                     timeout=_GIT_TIMEOUT)
    if staged.returncode == 0:
        outcome = "unchanged"
    elif staged.returncode == 1:
        _git_ok(ledger, "commit", "-m", f"run {run_id} {state.phase} ({slug})")
        outcome = "exported"
    else:
        raise RuntimeError(f"git diff --cached: {staged.stderr.strip()}")
    return outcome, _push(ledger), None


def _append_gz_event(gz: Path, payload: dict) -> None:
    """Append the event as a further gzip member — a new events.jsonl next to the
    .gz would overwrite it at the next --gzip prune."""
    seqs = [e.get("seq") for e in map(_parse, _log_lines(gz.parent) or []) if e]
    now = datetime.now(UTC)
    record = {
        "seq": max([s for s in seqs if isinstance(s, int)], default=0) + 1,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        "type": _EXPORT_EVENT,
        "kind": "point",
        "span": None,
        "parent": None,
        "phase": None,
        "lane": None,
        "round": None,
        "payload": payload,
    }
    with gzip.open(gz, "ab") as fh:
        fh.write(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")


def _record_event(repo: Path, run_id: str, payload: dict) -> None:
    """Fail-open ``ledger.export`` point event in the run's own log."""
    try:
        run_dir = repo / RUNS_RELPATH / run_id
        if not run_dir.is_dir():
            return
        try:
            enabled = AdwConfig.load(repo).trace.enabled
        except Exception:  # noqa: BLE001 — unreadable config: log anyway
            enabled = True
        if not enabled:
            return
        gz = run_dir / "events.jsonl.gz"
        if not (run_dir / "events.jsonl").exists() and gz.is_file():
            _append_gz_event(gz, payload)
        else:
            EventEmitter(repo, run_id, enabled=True).emit(_EXPORT_EVENT, payload)
    except Exception as exc:  # noqa: BLE001 — logging never fails the export
        _warn(f"ledger.export-Event für {run_id} nicht geschrieben: {exc}")


def export_run(repo, run_id: str) -> str:
    """Export one run into the ledger: ``exported``, ``unchanged``, ``skipped`` or
    ``failed``. Never raises."""
    repo = Path(repo)
    try:
        outcome, pushed, reason = _export(repo, run_id)
    except Exception as exc:  # noqa: BLE001 — fail-open by design (E6)
        outcome, pushed, reason = "failed", None, str(exc) or type(exc).__name__
        _warn(f"Ledger-Export von {run_id} fehlgeschlagen: {reason}")
    _record_event(repo, run_id, {"outcome": outcome, "pushed": pushed, "reason": reason})
    return outcome
