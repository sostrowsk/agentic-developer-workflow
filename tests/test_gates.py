import os

from adw.config import Gate
from adw.env import safe_env
from adw.gates import run_gates


def gate(name: str, cmd: str, timeout: int = 10) -> Gate:
    return Gate(name=name, cmd=cmd, timeout=timeout)


def test_all_gates_pass(tmp_path):
    report = run_gates([gate("a", "true"), gate("b", "true")], cwd=tmp_path)
    assert report.passed
    assert report.failures == []


def test_first_failure_stops_subsequent_gates(tmp_path):
    marker = tmp_path / "ran_second_gate"
    report = run_gates(
        [gate("fails", "false"), gate("marker", f"touch {marker}")],
        cwd=tmp_path,
    )
    assert not report.passed
    assert [f.gate for f in report.failures] == ["fails"]
    assert not marker.exists()


def test_failure_captures_output_and_exit_code(tmp_path):
    report = run_gates([gate("g", "sh -c 'echo kaputt >&2; exit 3'")], cwd=tmp_path)
    failure = report.failures[0]
    assert failure.exit_code == 3
    assert "kaputt" in failure.output
    assert not failure.timed_out


def test_timeout_is_reported(tmp_path):
    report = run_gates([gate("slow", "sleep 5", timeout=1)], cwd=tmp_path)
    failure = report.failures[0]
    assert failure.timed_out
    assert failure.exit_code is None


def test_output_is_capped_to_last_lines(tmp_path):
    cmd = "sh -c 'seq 1 500; exit 1'"
    report = run_gates([gate("noisy", cmd)], cwd=tmp_path)
    output = report.failures[0].output
    assert "500" in output
    assert "\n1\n" not in f"\n{output}\n"
    assert len(output.splitlines()) <= 201  # 200 Zeilen + Kappungs-Hinweis


def test_gate_runs_in_given_cwd(tmp_path):
    report = run_gates([gate("pwd", "sh -c 'test -f hier.txt'")], cwd=tmp_path)
    assert not report.passed
    (tmp_path / "hier.txt").write_text("x")
    report = run_gates([gate("pwd", "sh -c 'test -f hier.txt'")], cwd=tmp_path)
    assert report.passed


def test_missing_executable_is_a_gate_failure_not_a_crash(tmp_path):
    report = run_gates([gate("missing", "gibtsnicht-xyz --check")], cwd=tmp_path)
    assert not report.passed
    failure = report.failures[0]
    assert failure.gate == "missing"
    assert failure.exit_code is None
    assert "gibtsnicht-xyz" in failure.output


def test_huge_output_is_bounded(tmp_path):
    cmd = "sh -c 'yes x | head -c 10000000; exit 1'"
    report = run_gates([gate("firehose", cmd)], cwd=tmp_path)
    assert len(report.failures[0].output) < 100_000


def test_unmatched_quote_in_cmd_is_a_gate_failure_not_a_crash(tmp_path):
    report = run_gates([gate("quote", "echo '")], cwd=tmp_path)
    assert not report.passed
    assert report.failures[0].exit_code is None


def test_timeout_kills_whole_process_group(tmp_path):
    import subprocess
    import time

    needle = f"9876.{os.getpid()}"
    cmd = f"sh -c 'sleep {needle} & sleep 30'"
    try:
        report = run_gates([gate("group", cmd, timeout=1)], cwd=tmp_path)
        assert report.failures[0].timed_out
        for _ in range(20):
            alive = subprocess.run(
                ["pgrep", "-f", f"sleep {needle}"], capture_output=True, text=True
            ).stdout.strip()
            if not alive:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"Hintergrund-Kind überlebte den Timeout (PIDs: {alive})")
    finally:
        subprocess.run(["pkill", "-f", f"sleep {needle}"], capture_output=True)


def test_interrupt_during_wait_kills_process_group(tmp_path, monkeypatch):
    import subprocess
    import time

    import pytest

    needle = f"5432.{os.getpid()}"
    original_wait = subprocess.Popen.wait
    interrupted = {}

    def wait_with_interrupt(self, timeout=None):
        if not interrupted.get("done"):
            interrupted["done"] = True
            raise KeyboardInterrupt
        return original_wait(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait_with_interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_gates([gate("g", f"sh -c 'sleep {needle} & sleep 30'", timeout=30)], cwd=tmp_path)
        monkeypatch.undo()
        for _ in range(20):
            alive = subprocess.run(
                ["pgrep", "-f", f"sleep {needle}"], capture_output=True, text=True
            ).stdout.strip()
            if not alive:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"Prozessgruppe überlebte den Interrupt (PIDs: {alive})")
    finally:
        subprocess.run(["pkill", "-f", f"sleep {needle}"], capture_output=True)


def test_failing_gate_leaves_no_background_descendants(tmp_path):
    import subprocess
    import time

    needle = f"3210.{os.getpid()}"
    try:
        report = run_gates(
            [gate("g", f"sh -c 'sleep {needle} & exit 1'", timeout=30)], cwd=tmp_path
        )
        assert not report.passed
        for _ in range(20):
            alive = subprocess.run(
                ["pgrep", "-f", f"sleep {needle}"], capture_output=True, text=True
            ).stdout.strip()
            if not alive:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"Hintergrund-Kind überlebte den Gate-Fail (PIDs: {alive})")
    finally:
        subprocess.run(["pkill", "-f", f"sleep {needle}"], capture_output=True)


def test_all_last_200_lines_survive_even_with_large_lines(tmp_path):
    """Regression: 200 × 1-KB lines are >128 KiB — the tail must be line-based."""
    awk = 'awk \'BEGIN{s=sprintf("%01000d",0);for(i=1;i<=500;i++)print i" "s;exit 1}\''
    report = run_gates([gate("big", awk)], cwd=tmp_path)
    output = report.failures[0].output
    assert "\n301 " in output  # älteste der letzten 200 Zeilen
    assert "\n300 " not in output
    assert "500 " in output
    assert "gekappt" in output


def test_single_giant_line_yields_one_capped_tail_entry(tmp_path):
    """Regression: a newline-free giant line must not turn into many tail lines."""
    cmd = "sh -c 'yes x | tr -d \"\\n\" | head -c 1000000; echo; echo END; exit 1'"
    report = run_gates([gate("giant", cmd)], cwd=tmp_path)
    output = report.failures[0].output
    assert "END" in output
    assert len(output.splitlines()) <= 3
    assert len(output) < 10_000


def test_embedded_null_byte_in_cmd_is_a_gate_failure(tmp_path):
    report = run_gates([gate("nul", 'echo "\0"')], cwd=tmp_path)
    assert not report.passed
    assert report.failures[0].exit_code is None


def test_safe_env_whitelists_only_harmless_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "geheim")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "geheim2")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
    env = safe_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["PATH"]


def test_gates_run_with_safe_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "geheim")
    report = run_gates(
        [gate("leak", "sh -c 'test -z \"$ANTHROPIC_API_KEY\"'")],
        cwd=tmp_path,
    )
    assert report.passed


def test_extra_env_is_merged(tmp_path):
    report = run_gates(
        [gate("port", "sh -c 'test \"$BACKEND_PORT\" = 9101'")],
        cwd=tmp_path,
        extra_env={"BACKEND_PORT": "9101"},
    )
    assert report.passed
