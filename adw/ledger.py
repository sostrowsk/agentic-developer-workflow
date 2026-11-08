"""Run ledger (W13): a deterministic run record plus the case bundle, exported into
a central, private Git ledger that survives the pruning of run directories.

``build_run_record`` reads a run directory (state + event log) and returns the
``run-record.json`` dict (schema_version 1) — no writes, no model, the only
subprocess is ``git remote get-url origin``. ``export_run`` writes record and
bundle to ``$ADW_LEDGER/runs/<slug>/<run_id>/``, commits in the ledger clone and
pushes fail-open. It never raises and takes no run locks (its callers may already
hold them). ADW never creates or clones the ledger.

``compute_kpis`` (W3, ``adw kpis``) aggregates the ledger's run records per ADW
version and lists eval candidates — read-only, no model.
"""

import gzip
import json
import os
import re
import shutil
import stat
import statistics
import subprocess
import sys
from datetime import UTC, datetime, timedelta
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
    "retro.json",
    "retro.md",
)
# Human feedback (W14, ``adw rate``/``adw feedback``) lives only in the ledger.
FEEDBACK_FILE = "feedback.jsonl"
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


_ISSUE_HEADER = "# Issue (ADW-generiert"


def _issue_title(run_dir: Path) -> str | None:
    """First Markdown heading after the ADW header line (authoring.py), else the
    first non-empty line there; an issue without the header keeps its own title."""
    lines = [line.strip() for line in (_read_text(run_dir / "issue.md") or "").splitlines()]
    lines = [line for line in lines if line]
    if lines and lines[0].startswith(_ISSUE_HEADER):
        lines = lines[1:]
    for line in lines:
        if line.startswith("#") and line.lstrip("#").strip():
            return line.lstrip("#").strip()
    return lines[0] if lines else None


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


def _span_context(starts: dict, span_id) -> tuple:
    """(phase, round, loop) from the nearest ``phase``/``round`` ancestors of
    ``span_id`` (itself included), walking the ``parent`` of each start event;
    None where no such ancestor or start event exists. Cycles end the walk."""
    phase = round_ = loop = None
    found_phase = found_round = False
    visited = set()
    while span_id is not None and span_id not in visited and span_id in starts:
        visited.add(span_id)
        start = starts[span_id]
        if start.get("type") == "round" and not found_round:
            found_round = True
            round_, loop = _payload(start).get("n"), _payload(start).get("loop")
        elif start.get("type") == "phase" and not found_phase:
            found_phase = True
            phase = _payload(start).get("name")
        span_id = start.get("parent")
    return phase, round_, loop


def _context_fields(starts: dict, span_id) -> dict:
    phase, round_, loop = _span_context(starts, span_id)
    return {"phase": phase, "round": round_, "loop": loop}


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
        "effort": begin.get("effort"),
        "max_turns": begin.get("max_turns"),
        "timeout_s": begin.get("timeout_s"),
        "duration_s": duration,
        "cost_usd": finish.get("cost_usd") if end is not None else None,
        "usage": finish.get("usage") if end is not None else None,
        "is_error": finish.get("is_error") if end is not None else None,
    }


_CODEX_EFFORT_PREFIX = "model_reasoning_effort="


def _codex_effort(events: list[dict]) -> str | None:
    """The reasoning effort in the argv of the FIRST codex.review/codex.author
    start — no search in later starts, no fallback (old runs stay null)."""
    first = next(
        (
            e for e in events
            if e.get("type") in ("codex.review", "codex.author") and e.get("kind") == "start"
        ),
        None,
    )
    argv = _payload(first).get("argv")
    if not isinstance(argv, list):
        return None
    for item in argv:
        if isinstance(item, str) and item.startswith(_CODEX_EFFORT_PREFIX):
            return item[len(_CODEX_EFFORT_PREFIX):]
    return None


def build_run_record(repo, run_id: str) -> dict:
    """The run record (schema_version 1) of one run — read-only, deterministic."""
    repo = Path(repo)
    state = RunState.load(repo, run_id)
    run_dir = repo / RUNS_RELPATH / run_id
    events = _read_events(run_dir)
    runs = _spans(events, "run")
    starts: dict = {}
    for e in events:
        if e.get("kind") == "start":
            starts.setdefault(e.get("span"), e)
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
        "codex_effort": _codex_effort(events),
        "findings": [
            {
                "finding_key": _payload(e).get("finding_key"),
                "severity": _payload(e).get("severity"),
                "action": _payload(e).get("action"),
                "reason": _payload(e).get("reason"),
                **_context_fields(starts, e.get("span")),
            }
            for e in events
            if e.get("type") == "triage.decision"
        ],
        "review_counts": [
            {**_context_fields(starts, e.get("span")), "n": _count(_payload(e).get("findings"))}
            for e in events
            if e.get("type") == "codex.review" and e.get("kind") == "end"
        ],
        "followups": _followups(run_dir),
    }


# --- export ----------------------------------------------------------------------


def _ledger_path() -> Path | None:
    """The ledger clone, or None when ADW_LEDGER is no Git work tree root.

    Only a genuinely absent or non-repository path — no directory, or no ``.git``
    entry in it — is "no ledger". Once ``.git`` exists the path IS the ledger: a
    probe that times out, cannot run, is refused (exit ≠ 0, e.g. dubious
    ownership) or reports another top level raises, the export then fails and
    the prune keeps the run instead of deleting it unarchived. Decided from the
    file system, not from git's (localised) error text. Explicit stat calls: only
    genuine absence (ENOENT/ENOTDIR) or a non-directory is "no ledger"; a
    permission or I/O error propagates and fails the export."""
    path = Path(os.environ.get("ADW_LEDGER", _DEFAULT_LEDGER)).expanduser()
    try:
        if not stat.S_ISDIR(os.stat(path).st_mode):
            return None
        os.lstat(path / ".git")
    except (FileNotFoundError, NotADirectoryError):
        return None
    result = run_git(path, "rev-parse", "--show-toplevel", timeout=_GIT_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"Ledger {path} nicht lesbar: {result.stderr.strip()}")
    if Path(result.stdout.strip()).resolve() != path.resolve():
        raise RuntimeError(f"Ledger {path} ist nicht die Wurzel eines Git-Arbeitsbaums")
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
        if kept and not kept[-1].endswith(b"\n"):
            # Same bytes before and after an export event lands behind a log
            # without final newline (it then starts on a fresh line).
            kept[-1] += b"\n"
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
    feedback = dest / FEEDBACK_FILE
    kept = feedback.read_bytes() if feedback.is_file() else None
    if dest.exists():
        shutil.rmtree(dest)
    _write_bundle(repo, state, record, dest)
    if kept is not None:  # E8: the human verdict survives the rewrite
        feedback.write_bytes(kept)
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
    .gz would overwrite it at the next --gzip prune. Like the emitter, the record
    starts on a fresh line when the log lacks a final newline (complete last
    record or crash-truncated fragment), so neither is merged with it."""
    lines = _log_lines(gz.parent) or []
    seqs = [e.get("seq") for e in map(_parse, lines) if e]
    prefix = b"\n" if lines and not lines[-1].endswith(b"\n") else b""
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
        fh.write(prefix + json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")


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


# --- kpis (W3) -------------------------------------------------------------------

# --- human feedback (W14) ---------------------------------------------------------


class FeedbackError(Exception):
    """``adw rate``/``adw feedback`` cannot write (no ledger, run not exported, …)."""


def ledger_run_dir(repo: Path, run_id: str) -> tuple[Path, Path]:
    """The ledger clone and the run's folder relative to it — the folder must exist
    (the run was exported). Creates nothing."""
    try:
        ledger = _ledger_path()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise FeedbackError(f"Ledger nicht lesbar: {exc}") from exc
    if ledger is None:
        path = Path(os.environ.get("ADW_LEDGER", _DEFAULT_LEDGER)).expanduser()
        raise FeedbackError(f"Ledger nicht gefunden: {path}")
    rel = Path("runs") / _repo_slug(repo).replace("/", "__") / run_id
    runs_dir = (ledger / "runs").resolve()
    dest = ledger / rel
    if not dest.resolve().is_relative_to(runs_dir) or dest.resolve() == runs_dir:
        raise FeedbackError(f"Ledger-Ziel liegt außerhalb von runs/: {rel}")
    if not dest.is_dir():
        raise FeedbackError(f"Lauf {run_id} nicht im Ledger (erst exportieren: adw ledger export)")
    return ledger, rel


def finding_keys(ledger: Path, rel: Path) -> list[str]:
    """The distinct ``finding_key`` values of the exported run record."""
    try:
        record = json.loads((ledger / rel / "run-record.json").read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise FeedbackError(f"run-record.json nicht lesbar: {exc}") from exc
    keys = (_dict(f).get("finding_key") for f in _list(_dict(record).get("findings")))
    return sorted({k for k in keys if isinstance(k, str)})


def append_feedback(ledger: Path, rel: Path, entry: dict) -> bool:
    """Append one JSON line to the run's feedback.jsonl, commit it and push fail-open.
    Returns whether the push succeeded; a failed add/commit raises FeedbackError."""
    path = ledger / rel / FEEDBACK_FILE
    prefix = ""
    if path.is_file() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            prefix = "" if fh.read(1) == b"\n" else "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        _git_ok(ledger, "add", "--", (rel / FEEDBACK_FILE).as_posix())
        _git_ok(ledger, "commit", "-m", f"feedback {rel.name} {entry['kind']}")
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise FeedbackError(f"Ledger-Commit fehlgeschlagen: {exc}") from exc
    return _push(ledger)


CANDIDATE_RULES = ("escalated", "review_rounds", "gate_iterations", "circuit_breakers", "cost_usd")


def _read_ledger_records(ledger: Path) -> list:
    """Every ``runs/*/*/run-record.json`` parsed; an unreadable file yields None."""
    return _read_ledger_runs(ledger)[0]


def _read_ledger_runs(ledger: Path) -> tuple[list, list[dict]]:
    """The run records plus, in parallel, each run's optional signals (W14):
    ``{"retro": <readable retro.json or None>, "feedback": [<readable lines>]}``."""
    records, signals = [], []
    for path in sorted(ledger.glob("runs/*/*/run-record.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):  # UnicodeDecodeError is a ValueError
            records.append(None)
        signals.append({
            "retro": _read_retro(path.parent / "retro.json"),
            "feedback": _read_feedback(path.parent / FEEDBACK_FILE),
        })
    return records, signals


def _read_retro(path: Path) -> dict | None:
    try:
        retro = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing or unreadable: no retro
        return None
    return retro if isinstance(retro, dict) and isinstance(retro.get("items"), list) else None


def _read_feedback(path: Path) -> list[dict]:
    try:
        data = path.read_bytes()
    except Exception:  # noqa: BLE001 — missing or unreadable: no feedback
        return []
    entries = []
    # Decoded per line: one broken line (bad JSON or bad UTF-8) costs only itself.
    for line in data.splitlines():
        try:
            entry = json.loads(line.decode("utf-8"))
        except ValueError:  # UnicodeDecodeError is a ValueError
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _signal_metrics(signals: list[dict]) -> dict:
    causes: dict = {}
    ratings = {"good": 0, "bad": 0}
    finding_feedback = {"valid": 0, "noise": 0}
    retro_runs = 0
    for signal in signals:
        retro = signal.get("retro")
        if retro is not None:
            retro_runs += 1
            for item in map(_dict, _list(retro.get("items"))):
                if isinstance(item.get("cause"), str):
                    causes[item["cause"]] = causes.get(item["cause"], 0) + 1
        last = None
        for entry in signal.get("feedback", []):
            kind, value = entry.get("kind"), entry.get("value")
            if not isinstance(value, str):  # unhashable values break the lookups below
                continue
            if kind == "rate" and value in ratings:
                last = value
            elif kind == "finding" and value in finding_feedback:
                finding_feedback[value] += 1
        if last is not None:
            ratings[last] += 1
    return {
        "retro_causes": causes,
        "retro_runs": retro_runs,
        "ratings": ratings,
        "finding_feedback": finding_feedback,
    }


def _numbers(values) -> list:
    return [v for v in values if _is_number(v)]


def _median(values):
    return statistics.median(values) if values else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _lane_iterations(run: dict) -> list:
    return _numbers(_dict(lane).get("gate_iterations") for lane in _dict(run.get("lanes")).values())


def _share(count: int, total: int):
    return count / total if total else None


def _metrics(runs: list[dict]) -> dict:
    n = len(runs)
    phases = [r.get("phase") for r in runs]
    review_rounds = _numbers(r.get("review_rounds") for r in runs)
    fix_cycles = _numbers(r.get("fix_cycles") for r in runs)
    gate_iterations = [sum(it) for it in map(_lane_iterations, runs) if it]
    costs = _numbers(r.get("cost_usd") for r in runs)
    circuit_breakers = _numbers(r.get("circuit_breakers") for r in runs)
    agent_costs: dict = {}
    for run in runs:
        for entry in map(_dict, _list(run.get("agents"))):
            agent, cost = entry.get("agent"), entry.get("cost_usd")
            if isinstance(agent, str) and _is_number(cost):
                agent_costs[agent] = agent_costs.get(agent, 0) + cost
    agent_total = sum(agent_costs.values())
    by_severity: dict = {}
    by_action: dict = {}
    for run in runs:
        for finding in map(_dict, _list(run.get("findings"))):
            for counts, key in ((by_severity, "severity"), (by_action, "action")):
                if isinstance(finding.get(key), str):
                    counts[finding[key]] = counts.get(finding[key], 0) + 1
    escalated = sum(
        1 for r in runs if r.get("phase") == "escalated" or r.get("escalation_reason") is not None
    )
    return {
        "runs": n,
        "phase_shares": {
            "done": _share(phases.count("done"), n),
            "escalated": _share(phases.count("escalated"), n),
            "other": _share(n - phases.count("done") - phases.count("escalated"), n),
        },
        "escalation_share": _share(escalated, n),
        "review_rounds": {
            "median": _median(review_rounds),
            "max": max(review_rounds, default=None),
        },
        "fix_cycles": {"median": _median(fix_cycles), "max": max(fix_cycles, default=None)},
        "gate_iterations": {
            "median": _median(gate_iterations),
            "max": max(gate_iterations, default=None),
        },
        "duration_s": {"median": _median(_numbers(r.get("duration_s") for r in runs))},
        "cost_usd": {"sum": sum(costs) if costs else None, "median": _median(costs)},
        "agent_cost_shares": {
            agent: cost / agent_total if agent_total else None
            for agent, cost in agent_costs.items()
        },
        "circuit_breakers": sum(circuit_breakers) if circuit_breakers else None,
        "approvals": {"median": _median(_numbers(r.get("approvals") for r in runs))},
        "findings_by_severity": by_severity,
        "findings_by_action": by_action,
    }


def _version_key(version) -> tuple:
    if isinstance(version, str) and all(p.isdigit() for p in version.split(".")):
        return (0, tuple(int(p) for p in version.split(".")))
    return (1, str(version)) if version is not None else (2,)


def _rules(run: dict, cost_median) -> list[str]:
    cost = run.get("cost_usd")
    fired = {
        "escalated": run.get("phase") == "escalated",
        "review_rounds": _is_number(run.get("review_rounds")) and run["review_rounds"] >= 4,
        "gate_iterations": any(n >= 5 for n in _lane_iterations(run)),
        "circuit_breakers": _is_number(run.get("circuit_breakers"))
        and run["circuit_breakers"] > 0,
        "cost_usd": cost_median is not None and _is_number(cost) and cost > 1.5 * cost_median,
    }
    return [rule for rule in CANDIDATE_RULES if fired[rule]]


def compute_kpis(records, since=None, repo_slug=None, signals=None) -> dict:
    """KPIs per ``adw_version`` plus total and eval candidates over the given run
    records (W3). Records that are no object or not ``schema_version`` 1 count as
    ``skipped``; the filters apply before grouping, total and candidates.
    ``signals`` (W14) runs parallel to ``records`` (retro and human feedback per
    run) and only feeds the total."""
    if signals is None:
        signals = [{} for _ in records]
    valid = [
        (r, s) for r, s in zip(records, signals, strict=True)
        if isinstance(r, dict)
        and type(r.get("schema_version")) is int
        and r["schema_version"] == SCHEMA_VERSION
    ]
    skipped = len(records) - len(valid)
    pairs = valid
    if since is not None:
        cutoff = datetime.now(UTC) - timedelta(days=since)
        pairs = [
            (r, s) for r, s in pairs if (ts := _parse_ts(r.get("started_at"))) and ts >= cutoff
        ]
    if repo_slug is not None:
        pairs = [(r, s) for r, s in pairs if r.get("repo_slug") == repo_slug]
    runs = [r for r, _ in pairs]

    groups: dict = {}
    for run in runs:
        version = run.get("adw_version")
        hashable = isinstance(version, str | int | float)
        groups.setdefault(version if hashable else None, []).append(run)
    cost_median = _median(_numbers(r.get("cost_usd") for r in runs))
    candidates = [(run, rules) for run in runs if (rules := _rules(run, cost_median))]
    far_future = datetime.max.replace(tzinfo=UTC)

    def order(candidate: tuple) -> tuple:
        run = candidate[0]
        ts = _parse_ts(run.get("started_at"))
        return (ts is None, ts or far_future, str(run.get("run_id")))

    return {
        "groups": [
            {"adw_version": version, **_metrics(groups[version])}
            for version in sorted(groups, key=_version_key)
        ],
        "total": {**_metrics(runs), **_signal_metrics([s for _, s in pairs])},
        "candidates": [
            {
                "run_id": run.get("run_id"),
                "repo_slug": run.get("repo_slug"),
                "adw_version": run.get("adw_version"),
                "rules": rules,
                "issue_title": run.get("issue_title"),
            }
            for run, rules in sorted(candidates, key=order)
        ],
        "skipped": skipped,
    }
