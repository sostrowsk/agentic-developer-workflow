"""Run the behavioural GUI scenarios (Aufgaben B and C) against the SERVED
``app.js`` in a plain ``node`` process (``tests/gui_js_harness.js``).

Not a test module (the name does not start with ``test_``). The JS harness stubs
the DOM, ``fetch``, ``performance`` (marks/measures), ``requestAnimationFrame`` and
task scheduling and drives them deterministically — it is a development/test-time
tool only, never a runtime dependency, and it is NOT a browser (no Playwright, no
Selenium). Per .adw/plan.md §1/§6, ``node`` is a REQUIRED verification tool of the
test gate: if it is unavailable the behavioural tests FAIL with a clear message —
they never skip and never weaken to source-text assertions, so the gate cannot go
green with B1/B2/C1 untested.
"""

import json
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from adw.gui.app import create_app

_HARNESS = Path(__file__).with_name("gui_js_harness.js")


def served_app_js() -> str:
    """The exact client script the server delivers (so the harness exercises the
    served artifact, not a stale copy)."""
    return TestClient(create_app(repos=[])).get("/static/app.js").text


def run_scenario(tmp_path, scenario: str, *args: str) -> dict:
    """Execute ``scenario`` in ``node`` and return the parsed observables.

    A missing ``node`` runtime is a verification FAILURE (never a skip): the
    behavioural B/C acceptance criteria stay unproven otherwise.
    """
    node = shutil.which("node")
    assert node, (
        "the behavioural GUI tests (Aufgaben B/C) require a Node.js runtime to "
        "execute the served app.js; `node` was not found on PATH. This is a "
        "verification failure, not a skip — install Node.js so B1/B2/C1 are tested."
    )
    js_path = tmp_path / "served_app.js"
    js_path.write_text(served_app_js(), encoding="utf-8")
    proc = subprocess.run(
        [node, str(_HARNESS), str(js_path), scenario, *args],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"JS harness scenario {scenario!r} failed (exit {proc.returncode}):\n"
        f"{proc.stderr}\n{proc.stdout}"
    )
    return json.loads(proc.stdout)
