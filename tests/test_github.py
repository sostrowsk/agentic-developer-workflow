"""GitHub Actions monitoring: gh polling analogous to the GitLab module (adw/ci.py)."""

import json

import pytest

from adw.ci import CiTimeoutError
from adw.config import CiConfig
from adw.github import poll_ci


class FakeGh:
    """Scriptable gh responses per call sequence + recording."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        if not self.responses:
            raise AssertionError(f"Kein gescriptetes gh-Ergebnis für {argv}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def runs_json(*runs):
    return json.dumps(
        {
            "total_count": len(runs),
            "workflow_runs": [
                {"id": rid, "status": status, "conclusion": conclusion}
                for rid, status, conclusion in runs
            ],
        }
    )


def jobs_json(*jobs):
    return json.dumps(
        {
            "jobs": [
                {"id": i, "name": name, "conclusion": conclusion}
                for i, (name, conclusion) in enumerate(jobs, start=1)
            ]
        }
    )


def make_cfg(**overrides):
    values = {"poll_interval": 60, "timeout": 2700, "staging_job": "deploy-staging"}
    values.update(overrides)
    return CiConfig(**values)


def test_success_after_waiting_for_completion(tmp_path):
    gh = FakeGh(
        [
            runs_json((7, "in_progress", None)),
            runs_json((7, "completed", "success")),
            jobs_json(("test", "success"), ("deploy-staging", "success")),
        ]
    )
    sleep = FakeSleep()
    result = poll_ci(tmp_path, "adw/x/backend", make_cfg(), run_gh=gh, sleep=sleep, sha="abc123")
    assert result.passed is True
    assert result.pipeline_id == 7
    assert sleep.calls  # in_progress terminierte den Poll nicht
    first = gh.calls[0]
    assert first[0] == "api"
    assert "head_sha=abc123" in first[1]  # SHA-gebunden wie beim GitLab-Poll


def test_failed_run_delivers_log_excerpt(tmp_path):
    gh = FakeGh(
        [
            runs_json((9, "completed", "failure")),
            "##[error] pytest: 3 failed\nZeile 2 des Logs",
        ]
    )
    result = poll_ci(tmp_path, "adw/x/backend", make_cfg(), run_gh=gh, sleep=FakeSleep(), sha="abc")
    assert result.passed is False
    assert result.pipeline_id == 9
    assert "pytest: 3 failed" in result.log_excerpt
    log_call = gh.calls[-1]
    assert log_call[:2] == ["run", "view"] and "--log-failed" in log_call


def test_one_red_workflow_among_many_fails_the_result(tmp_path):
    gh = FakeGh(
        [
            runs_json((1, "completed", "success"), (2, "completed", "failure")),
            "LOG der roten Pipeline",
        ]
    )
    result = poll_ci(tmp_path, "b", make_cfg(), run_gh=gh, sleep=FakeSleep(), sha="abc")
    assert result.passed is False
    assert result.pipeline_id == 2


def test_missing_staging_job_fails(tmp_path):
    gh = FakeGh(
        [
            runs_json((7, "completed", "success")),
            jobs_json(("test", "success")),  # deploy-staging fehlt
        ]
    )
    result = poll_ci(tmp_path, "b", make_cfg(), run_gh=gh, sleep=FakeSleep(), sha="abc")
    assert result.passed is False
    assert "deploy-staging" in result.log_excerpt


def test_no_staging_job_configured_passes_on_green_runs(tmp_path):
    gh = FakeGh([runs_json((7, "completed", "success"))])
    result = poll_ci(
        tmp_path, "b", make_cfg(staging_job=None), run_gh=gh, sleep=FakeSleep(), sha="abc"
    )
    assert result.passed is True


def test_timeout_when_runs_never_complete(tmp_path):
    gh = FakeGh([runs_json((7, "queued", None))] * 100)
    sleep = FakeSleep()
    with pytest.raises(CiTimeoutError):
        poll_ci(
            tmp_path,
            "b",
            make_cfg(poll_interval=60, timeout=120),
            run_gh=gh,
            sleep=sleep,
            sha="abc",
        )
    assert sum(sleep.calls) <= 120  # Budget wird nicht überzogen
