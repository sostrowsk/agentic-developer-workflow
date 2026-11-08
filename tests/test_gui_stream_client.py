"""R3 (E1/E3): the client's EventSource lifecycle, run in the JS harness against
the served ``app.js``."""

import pytest

from tests.gui_js_harness import run_scenario


@pytest.mark.parametrize("status", ["done", "escalated"])
def test_finished_run_opens_no_event_source(tmp_path, status):
    """A page rendered as done/escalated never connects to the stream."""
    assert run_scenario(tmp_path, "stream-lifecycle", status)["opened"] is False


def test_live_run_resumes_after_rendered_seq_and_closes_only_on_closed(tmp_path):
    """A live page connects with ``?last_id=<data-last-seq>``; a ``run`` end still
    triggers the final refresh but keeps the connection; ``closed`` closes it."""
    r = run_scenario(tmp_path, "stream-lifecycle", "running")
    assert r["opened"] is True
    assert r["url"].endswith("/stream?last_id=7")
    assert r["refreshed"] is True
    assert r["closed_after_end"] == 0
    assert r["closed_after_closed"] == 1
