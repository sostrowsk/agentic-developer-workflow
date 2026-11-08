"""GitHub Actions monitoring: gh polling analogous to the GitLab module (adw/ci.py)."""

import json

import pytest

from adw.ci import CiError, CiTimeoutError
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
    """Simulated sleep coupled to a fake monotonic clock: each nap advances it."""

    def __init__(self):
        self.calls = []
        self.now = 0.0

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


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
            clock=sleep.clock,
        )
    assert sum(sleep.calls) <= 120  # Budget wird nicht überzogen


# --- R4: Staging-Grün (C19), Pagination (C20), Log-Evidenz (C22) --------------


def full_runs_page(start, count=100, conclusion="success"):
    return runs_json(*[(i, "completed", conclusion) for i in range(start, start + count)])


def full_jobs_page(count=100):
    return json.dumps(
        {"jobs": [{"id": i, "name": f"job-{i}", "conclusion": "success"} for i in range(count)]}
    )


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
def test_staging_job_not_success_is_not_green(tmp_path, conclusion):
    gh = FakeGh(
        [
            runs_json((7, "completed", "success")),
            jobs_json(("test", "success"), ("deploy-staging", conclusion)),
            "Deploy-Log",
        ]
    )
    result = poll_ci(tmp_path, "b", make_cfg(), run_gh=gh, sleep=FakeSleep(), sha="abc")
    assert result.passed is False
    assert any(c[:2] == ["run", "view"] and "--log-failed" in c for c in gh.calls)


def test_staging_success_with_skipped_optional_run_is_green(tmp_path):
    gh = FakeGh(
        [
            runs_json((7, "completed", "success"), (8, "completed", "skipped")),
            jobs_json(("deploy-staging", "success")),
            jobs_json(),
        ]
    )
    result = poll_ci(tmp_path, "b", make_cfg(), run_gh=gh, sleep=FakeSleep(), sha="abc")
    assert result.passed is True


def test_staging_job_on_second_jobs_page_is_found(tmp_path):
    gh = FakeGh(
        [
            runs_json((7, "completed", "success")),
            full_jobs_page(),
            jobs_json(("deploy-staging", "success")),
        ]
    )
    result = poll_ci(tmp_path, "b", make_cfg(), run_gh=gh, sleep=FakeSleep(), sha="abc")
    assert result.passed is True
    assert "existiert in keinem Workflow-Run" not in result.log_excerpt
    jobs_calls = [c for c in gh.calls if c[0] == "api" and "/jobs" in c[1]]
    assert len(jobs_calls) == 2
    assert "page=2" in jobs_calls[1][1]


@pytest.mark.parametrize("second_page", ["red", "running"])
def test_runs_on_second_page_are_evaluated(tmp_path, second_page):
    if second_page == "red":
        page2 = runs_json((500, "completed", "failure"))
        responses = [full_runs_page(1), page2, "rotes Log auf Seite 2"]
    else:
        page2 = runs_json((500, "in_progress", None))
        responses = [
            full_runs_page(1),
            page2,
            full_runs_page(1),
            runs_json((500, "completed", "failure")),
            "rotes Log",
        ]
    gh = FakeGh(responses)
    sleep = FakeSleep()
    result = poll_ci(tmp_path, "b", make_cfg(staging_job=None), run_gh=gh, sleep=sleep, sha="abc")
    assert result.passed is False
    assert result.pipeline_id == 500
    if second_page == "running":
        assert sleep.calls  # laufender Run auf Seite 2 hielt das Polling offen
    runs_calls = [c for c in gh.calls if c[0] == "api" and "/actions/runs?" in c[1]]
    assert all("head_sha=abc" in c[1] for c in runs_calls)


@pytest.mark.parametrize("collection", ["runs", "jobs"])
def test_more_than_ten_pages_raise(tmp_path, collection):
    if collection == "runs":
        responses = [full_runs_page(i * 100) for i in range(11)]
    else:
        responses = [runs_json((7, "completed", "success"))] + [full_jobs_page()] * 11
    gh = FakeGh(responses)
    with pytest.raises(CiError, match="Seite|page"):
        poll_ci(tmp_path, "b", make_cfg(), run_gh=gh, sleep=FakeSleep(), sha="abc")


@pytest.mark.parametrize("log", ["", "  \n\t\n"])
def test_red_run_with_empty_log_gives_empty_excerpt(tmp_path, log):
    gh = FakeGh([runs_json((9, "completed", "failure")), log])
    result = poll_ci(
        tmp_path, "b", make_cfg(staging_job=None), run_gh=gh, sleep=FakeSleep(), sha="abc"
    )
    assert result.passed is False
    assert result.log_excerpt == ""


def test_log_fetch_error_skips_only_that_run(tmp_path):
    gh = FakeGh(
        [
            runs_json((1, "completed", "startup_failure"), (2, "completed", "failure")),
            CiError("gh run view 1 --log-failed: Exit 1 — no logs"),
            "FAILED tests/test_y.py",
        ]
    )
    result = poll_ci(
        tmp_path, "b", make_cfg(staging_job=None), run_gh=gh, sleep=FakeSleep(), sha="abc"
    )
    assert result.passed is False
    assert "### Workflow-Run 2 (failure)" in result.log_excerpt
    assert "FAILED tests/test_y.py" in result.log_excerpt
    assert "Workflow-Run 1" not in result.log_excerpt
