"""One hardened way to run git: repo hooks off, env whitelist, a timeout.

Every git subprocess of the orchestrator, the GUI and the CLI goes through
``run_git`` — repo-controlled hooks must never run as the orchestrator (outside
any sandbox), and no secret may reach a git child process. Callers keep their
own timeouts and error semantics (escalate, refuse, fail open, HTTP 404); this
module only builds and runs the command.
"""

import subprocess
from pathlib import Path

from adw.env import safe_env


def run_git(
    cwd: Path, *args: str, timeout: float, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """``git -C <cwd> -c core.hooksPath=/dev/null <args>`` with text output.

    ``env`` is the COMPLETE environment for the child (default: ``safe_env()``);
    callers that need extra variables build it from ``safe_env(...)`` themselves.
    TimeoutExpired/OSError propagate — the caller decides what they mean."""
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=safe_env() if env is None else env,
    )
