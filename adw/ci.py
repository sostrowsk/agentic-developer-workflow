"""GitLab CI monitoring: pure glab polling (code, 0 tokens) until staging is green."""

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from adw.config import CiConfig
from adw.env import safe_env
from adw.events import NoOpEmitter

_GLAB_TIMEOUT = 120
_TERMINAL_STATUSES = {"success", "failed", "canceled", "skipped"}
_LOG_EXCERPT_LINES = 200

RunGlab = Callable[[list[str], Path], str]


class CiError(Exception):
    """glab invocation or output is broken."""


class CiTimeoutError(CiError):
    """Pipeline did not finish within the budget — escalation."""


@dataclass(frozen=True)
class CiResult:
    passed: bool
    pipeline_id: int | None
    log_excerpt: str = ""


def run_glab(argv: list[str], cwd: Path) -> str:
    """Real glab invocation — replaced by FakeGlab in tests."""
    try:
        result = subprocess.run(
            ["glab", *argv],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GLAB_TIMEOUT,
            env=safe_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise CiError(f"glab {' '.join(argv)}: Timeout") from exc
    except OSError as exc:
        raise CiError(f"glab {' '.join(argv)}: {exc}") from exc
    if result.returncode != 0:
        raise CiError(f"glab {' '.join(argv)}: Exit {result.returncode} — {result.stderr.strip()}")
    return result.stdout


def poll_pipeline(
    repo: Path,
    branch: str,
    cfg: CiConfig,
    run_glab: RunGlab = run_glab,
    sleep: Callable[[float], None] = time.sleep,
    sha: str | None = None,
    emitter=None,
) -> CiResult:
    """Polls the branch's latest pipeline until it reaches a final status.

    Success means: pipeline `success` AND (if configured) the
    staging job is green. On red, the logs of the failed
    jobs come along as an excerpt — food for the log analyst.

    ``sha`` binds the poll to a specific push: right after a
    re-entry push, GitLab may still deliver the terminal pipeline of the
    PREVIOUS push — which must not judge the fresh result.

    ``emitter`` is additive and optional (default: no-op): the wait is a
    ``ci.wait`` span, each executed poll a ``ci.poll`` point. The end payload is
    seeded with deterministic defaults and advanced per iteration.
    """
    emitter = emitter or NoOpEmitter()
    with emitter.span("ci.wait", {"provider": "gitlab", "pipeline_ref": branch}) as handle:
        handle.end_payload = {"status": "aborted", "polls": 0, "duration": 0}
        # Budget als Restzeit führen und den Sleep darauf kappen — sonst schläft
        # ein 61s-Budget bei 60s-Intervall bis 120s.
        remaining = float(cfg.timeout)
        polls = 0
        elapsed = 0.0
        while True:
            pipeline = _latest_pipeline(repo, branch, run_glab, sha=sha)
            if pipeline is not None and sha is not None and pipeline.get("sha") != sha:
                pipeline = None  # Defense in depth — der Server filtert bereits
            polls += 1
            emitter.emit(
                "ci.poll",
                {
                    "provider": "gitlab",
                    "status": pipeline["status"] if pipeline else "keine Pipeline",
                    "job": None,
                },
                span=handle.id,
            )
            handle.end_payload["polls"] = polls
            handle.end_payload["duration"] = elapsed
            if pipeline is not None and pipeline["status"] in _TERMINAL_STATUSES:
                result = _evaluate(repo, pipeline, cfg, run_glab)
                handle.end_payload["status"] = pipeline["status"]
                return result
            if remaining <= 0:
                handle.end_payload["status"] = "timeout"
                raise CiTimeoutError(
                    f"Pipeline für {branch} nach {cfg.timeout}s nicht abgeschlossen "
                    f"(Status: {pipeline['status'] if pipeline else 'keine Pipeline gefunden'})"
                )
            nap = min(float(cfg.poll_interval), remaining)
            sleep(nap)
            remaining -= nap
            elapsed += nap


def _latest_pipeline(
    repo: Path, branch: str, run_glab: RunGlab, sha: str | None = None
) -> dict | None:
    if sha is None:
        raw = run_glab(["ci", "list", "--ref", branch, "--per-page", "1", "--output", "json"], repo)
    else:
        # SHA-Filter SERVER-seitig: eine neuere fremde Pipeline auf demselben
        # Branch (Schedule, manueller Push) darf die gesuchte weder bewerten
        # noch verdecken — `ci list --per-page 1` sähe nur die neueste Zeile.
        raw = run_glab(
            [
                "api",
                f"projects/:id/pipelines?ref={quote(branch, safe='')}"
                f"&sha={sha}&per_page=1&order_by=id&sort=desc",
            ],
            repo,
        )
    pipelines = _parse_json(raw, "ci list")
    if not isinstance(pipelines, list):
        raise CiError(f"glab ci list: unerwartetes JSON — {raw[:200]}")
    return pipelines[0] if pipelines else None


def _evaluate(repo: Path, pipeline: dict, cfg: CiConfig, run_glab: RunGlab) -> CiResult:
    pipeline_id = pipeline.get("id")
    jobs = _jobs(repo, pipeline_id, run_glab)
    failed_jobs = [job for job in jobs if job.get("status") == "failed"]
    if pipeline["status"] != "success" or failed_jobs:
        excerpt = _logs_for(repo, failed_jobs, run_glab)
        return CiResult(passed=False, pipeline_id=pipeline_id, log_excerpt=excerpt)
    if cfg.staging_job is not None:
        staging = [job for job in jobs if job.get("name") == cfg.staging_job]
        if not staging:
            return CiResult(
                passed=False,
                pipeline_id=pipeline_id,
                log_excerpt=f"Staging-Job {cfg.staging_job!r} existiert nicht in der Pipeline",
            )
        if any(job.get("status") != "success" for job in staging):
            excerpt = _logs_for(repo, staging, run_glab)
            return CiResult(passed=False, pipeline_id=pipeline_id, log_excerpt=excerpt)
    return CiResult(passed=True, pipeline_id=pipeline_id)


def fetch_failed_job_logs(repo: Path, pipeline_id: int, run_glab: RunGlab = run_glab) -> str:
    """Logs of all failed jobs of a pipeline — food for the log analyst."""
    jobs = _jobs(repo, pipeline_id, run_glab)
    failed = [job for job in jobs if job.get("status") == "failed"]
    return _logs_for(repo, failed, run_glab)


_JOBS_PER_PAGE = 100


def _jobs(repo: Path, pipeline_id: int | None, run_glab: RunGlab) -> list[dict]:
    jobs: list[dict] = []
    page = 1
    while True:
        raw = run_glab(
            [
                "api",
                f"projects/:id/pipelines/{pipeline_id}/jobs?per_page={_JOBS_PER_PAGE}&page={page}",
            ],
            repo,
        )
        batch = _parse_json(raw, "pipeline jobs")
        if not isinstance(batch, list):
            raise CiError(f"glab api jobs: unerwartetes JSON — {raw[:200]}")
        jobs.extend(batch)
        if len(batch) < _JOBS_PER_PAGE:
            return jobs
        page += 1


def _logs_for(repo: Path, jobs: list[dict], run_glab: RunGlab) -> str:
    excerpts = []
    for job in jobs:
        raw = run_glab(["ci", "trace", str(job.get("id"))], repo)
        tail = "\n".join(raw.splitlines()[-_LOG_EXCERPT_LINES:])
        excerpts.append(f"### Job {job.get('name')} ({job.get('status')})\n{tail}")
    return "\n\n".join(excerpts)


def _parse_json(raw: str, context: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CiError(f"glab {context}: kein valides JSON — {raw[:200]}") from exc
