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

_CLI_TIMEOUT = 120
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


def run_forge_cli(tool: str, argv: list[str], cwd: Path) -> str:
    """Hardened glab/gh invocation shared by both forges: timeout, env whitelist,
    clear error texts naming the tool."""
    try:
        result = subprocess.run(
            [tool, *argv],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT,
            env=safe_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise CiError(f"{tool} {' '.join(argv)}: Timeout") from exc
    except OSError as exc:
        raise CiError(f"{tool} {' '.join(argv)}: {exc}") from exc
    if result.returncode != 0:
        raise CiError(
            f"{tool} {' '.join(argv)}: Exit {result.returncode} — {result.stderr.strip()}"
        )
    return result.stdout


def parse_json(tool: str, raw: str, context: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CiError(f"{tool} {context}: kein valides JSON — {raw[:200]}") from exc


def poll_until(
    *,
    provider: str,
    branch: str,
    cfg: CiConfig,
    sleep: Callable[[float], None],
    emitter,
    fetch: Callable[[], object],
    describe: Callable[[object], str],
    is_terminal: Callable[[object], bool],
    evaluate: Callable[[object], tuple[CiResult, str]],
    timeout_message: Callable[[object], str],
) -> CiResult:
    """The forge-neutral wait loop: a ``ci.wait`` span, one ``ci.poll`` point per
    poll, the budget kept as remaining time with each sleep capped to it (a 61 s
    budget at a 60 s interval must not sleep until 120 s). The forge supplies what
    to fetch, how to describe it, when it is final and how to judge it."""
    emitter = emitter or NoOpEmitter()
    with emitter.span("ci.wait", {"provider": provider, "pipeline_ref": branch}) as handle:
        handle.end_payload = {"status": "aborted", "polls": 0, "duration": 0}
        remaining = float(cfg.timeout)
        polls = 0
        elapsed = 0.0
        while True:
            observed = fetch()
            polls += 1
            emitter.emit(
                "ci.poll",
                {"provider": provider, "status": describe(observed), "job": None},
                span=handle.id,
            )
            handle.end_payload["polls"] = polls
            handle.end_payload["duration"] = elapsed
            if is_terminal(observed):
                result, status = evaluate(observed)
                handle.end_payload["status"] = status
                return result
            if remaining <= 0:
                handle.end_payload["status"] = "timeout"
                raise CiTimeoutError(timeout_message(observed))
            nap = min(float(cfg.poll_interval), remaining)
            sleep(nap)
            remaining -= nap
            elapsed += nap


def run_glab(argv: list[str], cwd: Path) -> str:
    """Real glab invocation — replaced by FakeGlab in tests."""
    return run_forge_cli("glab", argv, cwd)


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
    ``ci.wait`` span, each executed poll a ``ci.poll`` point.
    """

    def fetch():
        pipeline = _latest_pipeline(repo, branch, run_glab, sha=sha)
        if pipeline is not None and sha is not None and pipeline.get("sha") != sha:
            return None  # Defense in depth — der Server filtert bereits
        return pipeline

    def evaluate(pipeline):
        return _evaluate(repo, pipeline, cfg, run_glab), pipeline["status"]

    return poll_until(
        provider="gitlab",
        branch=branch,
        cfg=cfg,
        sleep=sleep,
        emitter=emitter,
        fetch=fetch,
        describe=lambda pipeline: pipeline["status"] if pipeline else "keine Pipeline",
        is_terminal=lambda pipeline: (
            pipeline is not None and pipeline["status"] in _TERMINAL_STATUSES
        ),
        evaluate=evaluate,
        timeout_message=lambda pipeline: (
            f"Pipeline für {branch} nach {cfg.timeout}s nicht abgeschlossen "
            f"(Status: {pipeline['status'] if pipeline else 'keine Pipeline gefunden'})"
        ),
    )


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
    return parse_json("glab", raw, context)
