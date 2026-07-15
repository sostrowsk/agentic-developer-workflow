"""GitHub-Actions-Monitoring: reines gh-Polling (Code, 0 Tokens) — das
GitHub-Gegenstück zu adw/ci.py (GitLab). Gleiche Fehlerklassen (CiError,
CiTimeoutError) und gleiches Ergebnis (CiResult), damit Phase 7 beide
Forges über einen Codepfad behandeln kann.

Abweichung zum GitLab-Modell: Ein Push kann MEHRERE Workflow-Runs starten
(ein Run je Workflow-Datei). "Grün" heißt deshalb: alle Runs zur SHA sind
completed und keiner davon rot.
"""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from adw.ci import CiError, CiResult, CiTimeoutError
from adw.config import CiConfig
from adw.env import safe_env

_LOG_EXCERPT_LINES = 200
_RUNS_PER_PAGE = 100

# Konclusion-Werte, die nicht als "rot" gelten.
_GREEN_CONCLUSIONS = {"success", "skipped", "neutral"}

RunGh = Callable[[list[str], Path], str]


_GH_TIMEOUT = 120


def run_gh(argv: list[str], cwd: Path) -> str:
    """Echter gh-Aufruf — in Tests durch FakeGh ersetzt. Gleiche gehärtete
    Subprocess-Mechanik wie run_glab (Timeout, safe_env, klare Fehlertexte)."""
    try:
        result = subprocess.run(
            ["gh", *argv],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
            env=safe_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise CiError(f"gh {' '.join(argv)}: Timeout") from exc
    except OSError as exc:
        raise CiError(f"gh {' '.join(argv)}: {exc}") from exc
    if result.returncode != 0:
        raise CiError(f"gh {' '.join(argv)}: Exit {result.returncode} — {result.stderr.strip()}")
    return result.stdout


def poll_ci(
    repo: Path,
    branch: str,
    cfg: CiConfig,
    run_gh: RunGh = run_gh,
    sleep: Callable[[float], None] = time.sleep,
    sha: str | None = None,
) -> CiResult:
    """Pollt alle Workflow-Runs des Pushes bis zum finalen Status.

    ``sha`` bindet den Poll an einen konkreten Push (head_sha-Filter der
    Actions-API) — die Runs eines früheren Pushes oder fremder Commits
    können das Ergebnis weder bewerten noch verdecken.
    """
    remaining = float(cfg.timeout)
    while True:
        runs = _workflow_runs(repo, branch, run_gh, sha)
        if runs and all(run.get("status") == "completed" for run in runs):
            return _evaluate(repo, runs, cfg, run_gh)
        if remaining <= 0:
            pending = [run.get("status") for run in runs] or ["keine Workflow-Runs gefunden"]
            raise CiTimeoutError(
                f"GitHub-Actions für {branch} nach {cfg.timeout}s nicht abgeschlossen "
                f"(Status: {', '.join(str(s) for s in pending)})"
            )
        nap = min(float(cfg.poll_interval), remaining)
        sleep(nap)
        remaining -= nap


def _workflow_runs(repo: Path, branch: str, run_gh: RunGh, sha: str | None) -> list[dict]:
    if sha is not None:
        query = f"head_sha={sha}&per_page={_RUNS_PER_PAGE}"
    else:
        query = f"branch={quote(branch, safe='')}&per_page={_RUNS_PER_PAGE}"
    raw = run_gh(["api", f"repos/{{owner}}/{{repo}}/actions/runs?{query}"], repo)
    data = _parse_json(raw, "actions runs")
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        raise CiError(f"gh api actions/runs: unerwartetes JSON — {raw[:200]}")
    return runs


def _evaluate(repo: Path, runs: list[dict], cfg: CiConfig, run_gh: RunGh) -> CiResult:
    red = [run for run in runs if run.get("conclusion") not in _GREEN_CONCLUSIONS]
    if red:
        excerpt = _failed_logs(repo, red, run_gh)
        return CiResult(passed=False, pipeline_id=red[0].get("id"), log_excerpt=excerpt)
    first_id = runs[0].get("id") if runs else None
    if cfg.staging_job is not None:
        staging = [
            job
            for run in runs
            for job in _jobs(repo, run.get("id"), run_gh)
            if job.get("name") == cfg.staging_job
        ]
        if not staging:
            return CiResult(
                passed=False,
                pipeline_id=first_id,
                log_excerpt=f"Staging-Job {cfg.staging_job!r} existiert in keinem Workflow-Run",
            )
        if any(job.get("conclusion") not in _GREEN_CONCLUSIONS for job in staging):
            excerpt = _failed_logs(repo, runs, run_gh)
            return CiResult(passed=False, pipeline_id=first_id, log_excerpt=excerpt)
    return CiResult(passed=True, pipeline_id=first_id)


def _jobs(repo: Path, run_id: int | None, run_gh: RunGh) -> list[dict]:
    raw = run_gh(["api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs?per_page=100"], repo)
    data = _parse_json(raw, "run jobs")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise CiError(f"gh api run jobs: unerwartetes JSON — {raw[:200]}")
    return jobs


def _failed_logs(repo: Path, runs: list[dict], run_gh: RunGh) -> str:
    """`gh run view --log-failed` je rotem Run — Futter für den Log-Analyst."""
    excerpts = []
    for run in runs:
        raw = run_gh(["run", "view", str(run.get("id")), "--log-failed"], repo)
        tail = "\n".join(raw.splitlines()[-_LOG_EXCERPT_LINES:])
        excerpts.append(f"### Workflow-Run {run.get('id')} ({run.get('conclusion')})\n{tail}")
    return "\n\n".join(excerpts)


def _parse_json(raw: str, context: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CiError(f"gh {context}: kein valides JSON — {raw[:200]}") from exc
