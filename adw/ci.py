"""GitLab-CI-Monitoring: reines glab-Polling (Code, 0 Tokens) bis Staging grün."""

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from adw.config import CiConfig
from adw.env import safe_env

_GLAB_TIMEOUT = 120
_TERMINAL_STATUSES = {"success", "failed", "canceled", "skipped"}
_LOG_EXCERPT_LINES = 200

RunGlab = Callable[[list[str], Path], str]


class CiError(Exception):
    """glab-Aufruf oder -Output ist kaputt."""


class CiTimeoutError(CiError):
    """Pipeline wurde innerhalb des Budgets nicht fertig — Eskalation."""


@dataclass(frozen=True)
class CiResult:
    passed: bool
    pipeline_id: int | None
    log_excerpt: str = ""


def run_glab(argv: list[str], cwd: Path) -> str:
    """Echter glab-Aufruf — in Tests durch FakeGlab ersetzt."""
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
) -> CiResult:
    """Pollt die neueste Pipeline des Branches bis zum finalen Status.

    Erfolg heißt: Pipeline `success` UND (falls konfiguriert) der
    Staging-Job ist grün. Bei Rot kommen die Logs der fehlgeschlagenen
    Jobs als Excerpt mit — Futter für den Log-Analyst.
    """
    # Budget als Restzeit führen und den Sleep darauf kappen — sonst schläft
    # ein 61s-Budget bei 60s-Intervall bis 120s.
    remaining = float(cfg.timeout)
    while True:
        pipeline = _latest_pipeline(repo, branch, run_glab)
        if pipeline is not None and pipeline["status"] in _TERMINAL_STATUSES:
            return _evaluate(repo, pipeline, cfg, run_glab)
        if remaining <= 0:
            raise CiTimeoutError(
                f"Pipeline für {branch} nach {cfg.timeout}s nicht abgeschlossen "
                f"(Status: {pipeline['status'] if pipeline else 'keine Pipeline gefunden'})"
            )
        nap = min(float(cfg.poll_interval), remaining)
        sleep(nap)
        remaining -= nap


def _latest_pipeline(repo: Path, branch: str, run_glab: RunGlab) -> dict | None:
    raw = run_glab(["ci", "list", "--ref", branch, "--per-page", "1", "--output", "json"], repo)
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
    """Logs aller fehlgeschlagenen Jobs einer Pipeline — Futter für den Log-Analyst."""
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
