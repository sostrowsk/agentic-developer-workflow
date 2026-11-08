import contextlib
import json
from types import SimpleNamespace

import pytest

from adw.ci import CiResult, CiTimeoutError, poll_pipeline
from adw.config import CiConfig


class FakeGlab:
    """Scriptable glab responses per call sequence + recording."""

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
    """Simulated sleep coupled to a fake monotonic clock: each nap advances it."""

    def __init__(self):
        self.total = 0.0
        self.calls = []
        self.now = 0.0

    def __call__(self, seconds):
        self.total += seconds
        self.calls.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


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
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep, clock=sleep.clock)
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
    sleep = FakeSleep()
    with pytest.raises(CiTimeoutError):
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep, clock=sleep.clock)


def test_sleep_never_exceeds_remaining_budget(tmp_path):
    """Regression: timeout=90/interval=60 must not sleep 120s."""
    cfg = CiConfig(poll_interval=60, timeout=90, staging_job=None)
    glab = FakeGlab([pipeline_json("running")] * 10)
    sleep = FakeSleep()
    with pytest.raises(CiTimeoutError):
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep, clock=sleep.clock)
    assert sleep.total <= 90
    assert sleep.calls == [60, 30]


def test_jobs_are_paginated_beyond_100(tmp_path):
    """Regression: a staging job on page 2 must not count as 'missing'."""
    page1 = json.dumps([{"id": i, "name": f"job-{i}", "status": "success"} for i in range(1, 101)])
    page2 = json.dumps([{"id": 101, "name": "deploy-staging", "status": "success"}])
    cfg = CiConfig(staging_job="deploy-staging")
    glab = FakeGlab([pipeline_json("success"), page1, page2])
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())
    assert result.passed


def test_broken_glab_json_raises_clear_error(tmp_path):
    from adw.ci import CiError

    cfg = CiConfig(staging_job=None)
    glab = FakeGlab(["das ist kein json"])
    with pytest.raises(CiError, match="JSON"):
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())


def test_ci_result_is_dataclass_with_pipeline_id(tmp_path):
    cfg = CiConfig(staging_job=None)
    glab = FakeGlab([pipeline_json("success", pipeline_id=123), jobs_json(("t", "success"))])
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=FakeSleep())
    assert isinstance(result, CiResult)
    assert result.pipeline_id == 123


def test_poll_queries_pipelines_by_the_given_sha(tmp_path):
    """Regression (Codex P1+P2): the poll binds to the SHA of the fresh push —
    SERVER-side (sha param in the API query), so that a newer foreign pipeline
    on the same branch neither judges nor obscures the result."""
    cfg = CiConfig(poll_interval=60, timeout=2700, staging_job="deploy-staging")
    glab = FakeGlab(
        [
            json.dumps([]),  # Pipeline zum frischen Push existiert noch nicht
            json.dumps([{"id": 2, "status": "success", "sha": "neuesha"}]),
            jobs_json(("deploy-staging", "success")),
        ]
    )
    sleep = FakeSleep()
    result = poll_pipeline(
        tmp_path, "adw/x/backend", cfg, run_glab=glab, sleep=sleep, sha="neuesha"
    )
    assert result.passed is True
    assert result.pipeline_id == 2
    assert sleep.calls  # leere Antwort terminierte den Poll nicht
    first = glab.calls[0]
    assert first[0] == "api"
    assert "sha=neuesha" in first[1]
    assert "ref=adw%2Fx%2Fbackend" in first[1]  # Branch URL-encodiert


# --- R4: monotone Deadline (C21) und GitLab manual (C23) ---------------------


class SpanRecorder:
    """Minimal emitter: records the ci.wait end payload."""

    def __init__(self):
        self.handle = None

    @contextlib.contextmanager
    def span(self, type, payload=None, **kwargs):
        self.handle = SimpleNamespace(id="s1", end_payload={})
        yield self.handle

    def emit(self, *args, **kwargs):
        return None

    def current_span_id(self):
        return None


class SlowFetchGlab(FakeGlab):
    """Each pipeline fetch advances the fake clock by ``cost`` seconds."""

    def __init__(self, responses, clock_owner, cost):
        super().__init__(responses)
        self.clock_owner = clock_owner
        self.cost = cost

    def __call__(self, argv, cwd):
        if argv[0] != "api" or "/jobs" not in argv[1]:
            self.clock_owner.now += self.cost
        return super().__call__(argv, cwd)


def test_deadline_counts_fetch_time(tmp_path):
    """C21: budget 1 s, two fetches of 0.6 s → timeout after fetch 2, no 3rd poll."""
    cfg = CiConfig(poll_interval=60, timeout=1, staging_job=None)
    sleep = FakeSleep()
    glab = SlowFetchGlab([pipeline_json("running")] * 5, sleep, cost=0.6)
    emitter = SpanRecorder()
    with pytest.raises(CiTimeoutError):
        poll_pipeline(
            tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep, clock=sleep.clock, emitter=emitter
        )
    assert len(glab.calls) == 2
    assert len(sleep.calls) == 1
    assert sleep.calls[0] <= 0.4 + 1e-9
    assert emitter.handle.end_payload["status"] == "timeout"
    assert emitter.handle.end_payload["duration"] == pytest.approx(sleep.now)


def test_final_status_after_deadline_is_evaluated(tmp_path):
    cfg = CiConfig(poll_interval=60, timeout=1, staging_job=None)
    sleep = FakeSleep()
    glab = SlowFetchGlab([pipeline_json("success"), jobs_json(("t", "success"))], sleep, cost=5)
    result = poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep, clock=sleep.clock)
    assert result.passed is True
    assert sleep.calls == []


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        (
            jobs_json(("test", "success"), ("deploy-prod", "manual")),
            "Pipeline wartet auf einen manuellen Job (deploy-prod) — Mensch übernimmt",
        ),
        (
            jobs_json(("test", "success")),
            "Pipeline wartet auf einen manuellen Job — Mensch übernimmt",
        ),
    ],
)
def test_manual_pipeline_escalates_immediately(tmp_path, jobs, expected):
    from adw.ci import CiError

    cfg = CiConfig(poll_interval=60, timeout=2700, staging_job="deploy-staging")
    glab = FakeGlab([pipeline_json("manual"), jobs] + [pipeline_json("manual")] * 3)
    sleep = FakeSleep()
    with pytest.raises(CiError) as exc:
        poll_pipeline(tmp_path, "adw/x", cfg, run_glab=glab, sleep=sleep, clock=sleep.clock)
    assert not isinstance(exc.value, CiTimeoutError)
    assert str(exc.value).endswith(expected)
    if "(" not in expected:
        assert "(" not in str(exc.value)
    pipeline_fetches = [c for c in glab.calls if "/jobs" not in " ".join(c)]
    assert len(pipeline_fetches) == 1
    assert sleep.calls == []
