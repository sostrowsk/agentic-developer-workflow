import json

import pytest

from adw.ci import CiResult, CiTimeoutError, fetch_failed_job_logs, poll_pipeline
from adw.config import CiConfig


class FakeGlab:
    """Skriptbare glab-Antworten pro Aufruf-Sequenz + Aufzeichnung."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        if not self.responses:
            raise AssertionError(f"Kein gescriptetes glab-Ergebnis für {argv}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeSleep:
    def __init__(self):
        self.total = 0.0
        self.calls = []

    def __call__(self, seconds):
        self.total += seconds
        self.calls.append(seconds)


def pipeline_json(status, pipeline_id=77):
    return json.dumps([{"id": pipeline_id, "status": status}])


def jobs_json(*jobs):
    return json.dumps(
        [
            {"id": i, "name": name, "status": status}
            for i, (name, status) in enumerate(jobs, start=1)
        ]
    )


def test_success_after_two_polls(tmp_path):
    cfg = CiConfig(poll_interval=60, timeout=2700, staging_job="deploy-staging")
    glab = FakeGlab(
        [
            pipeline_json("running"),
            pipeline_json("success"),
            jobs_json(("test", "success"), ("deploy-staging", "success")),
        ]
    )
    sleep = FakeSleep()
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep)
    assert result.passed
    assert sleep.total == 60


def test_failed_pipeline_returns_logs(tmp_path):
    cfg = CiConfig(staging_job=None)
    glab = FakeGlab(
        [
            pipeline_json("failed"),
            jobs_json(("test", "failed")),
            "FAILED tests/test_x.py::test_kaputt",
        ]
    )
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())
    assert not result.passed
    assert "test_kaputt" in result.log_excerpt


def test_timeout_raises_after_budget(tmp_path):
    cfg = CiConfig(poll_interval=60, timeout=180, staging_job=None)
    glab = FakeGlab([pipeline_json("running")] * 10)
    sleep = FakeSleep()
    with pytest.raises(CiTimeoutError):
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep)
    assert sleep.total <= 180


def test_staging_job_must_be_green(tmp_path):
    cfg = CiConfig(staging_job="deploy-staging")
    glab = FakeGlab(
        [
            pipeline_json("success"),
            jobs_json(("test", "success"), ("deploy-staging", "failed")),
            "Deploy kaputt: Verbindung verweigert",
        ]
    )
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())
    assert not result.passed
    assert "Verbindung verweigert" in result.log_excerpt


def test_missing_staging_job_is_a_failure(tmp_path):
    cfg = CiConfig(staging_job="deploy-staging")
    glab = FakeGlab(
        [
            pipeline_json("success"),
            jobs_json(("test", "success")),  # deploy-staging fehlt
        ]
    )
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())
    assert not result.passed
    assert "deploy-staging" in result.log_excerpt


def test_no_pipeline_for_branch_keeps_polling_then_times_out(tmp_path):
    cfg = CiConfig(poll_interval=60, timeout=120, staging_job=None)
    glab = FakeGlab(["[]"] * 5)
    with pytest.raises(CiTimeoutError):
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())


def test_broken_glab_json_raises_clear_error(tmp_path):
    from adw.ci import CiError

    cfg = CiConfig(staging_job=None)
    glab = FakeGlab(["das ist kein json"])
    with pytest.raises(CiError, match="JSON"):
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())


def test_fetch_failed_job_logs_collects_all_failed_jobs(tmp_path):
    glab = FakeGlab(
        [
            jobs_json(("lint", "failed"), ("test", "failed"), ("build", "success")),
            "lint kaputt",
            "test kaputt",
        ]
    )
    logs = fetch_failed_job_logs(tmp_path, pipeline_id=77, run_glab=glab)
    assert "lint kaputt" in logs and "test kaputt" in logs


def test_ci_result_is_dataclass_with_pipeline_id(tmp_path):
    cfg = CiConfig(staging_job=None)
    glab = FakeGlab([pipeline_json("success", pipeline_id=123), jobs_json(("t", "success"))])
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())
    assert isinstance(result, CiResult)
    assert result.pipeline_id == 123
