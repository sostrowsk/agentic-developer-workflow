"""RED tests for Aufgabe B — the ``adw gui`` CLI and the optional-extra packaging.

Derived from .adw/spec.md (AC 4/5, 21/22), .adw/contract.yaml (x-adw-cli /
x-adw-packaging) and GUI-SPEC §7.1/§8. Pinned surface only: the command exists
with the documented options, binds to loopback unless ``--i-know`` is given,
opens a browser only with ``--open``, ends cleanly (no traceback) with an install
hint when the gui extra is missing, and the core import path never pulls in the
web stack. The app-factory signature and the uvicorn wiring are not contractual;
the tests use ``uvicorn.run`` as the (documented) "start the app via uvicorn"
seam and never bind a real socket.
"""

import subprocess
import sys

from typer.testing import CliRunner

from adw.cli import app
from tests.gui_app_helpers import home  # noqa: F401 — used as a fixture

cli = CliRunner()


def test_gui_rejects_non_loopback_host_without_i_know(home, monkeypatch):  # noqa: F811
    """AC 5: a non-loopback --host without --i-know is refused with a
    comprehensible error BEFORE any bind or server start."""
    started = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append((a, k)))
    result = cli.invoke(app, ["gui", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert started == []  # no bind, no server start
    out = result.output.lower()
    assert "i-know" in out or "loopback" in out


def test_gui_allows_non_loopback_host_with_i_know(home, monkeypatch):  # noqa: F811
    """AC 5: with the explicit --i-know opt-in the non-loopback host is permitted
    and the server is started via uvicorn on that host/port."""
    started = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append((a, k)))
    result = cli.invoke(app, ["gui", "--host", "0.0.0.0", "--i-know"])
    assert result.exit_code == 0, result.output
    assert started, "the server should be started via uvicorn"
    blob = repr(started)
    assert "0.0.0.0" in blob and "8765" in blob  # host passed through, default port


def test_gui_opens_browser_only_with_open_flag(home, monkeypatch):  # noqa: F811
    """AC 4: --open opens the browser on the local loopback address; without
    --open no browser is opened."""
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url))

    without = cli.invoke(app, ["gui"])
    assert without.exit_code == 0, without.output
    assert opened == []

    withflag = cli.invoke(app, ["gui", "--open"])
    assert withflag.exit_code == 0, withflag.output
    assert len(opened) == 1
    assert "127.0.0.1" in opened[0] and "8765" in opened[0]


def test_gui_without_extra_exits_with_install_hint_not_traceback(home, monkeypatch):  # noqa: F811
    """AC 22: without the gui extra, ``adw gui`` ends cleanly with an instruction
    to install adw[gui] — no raw ImportError traceback."""
    # fastapi + jinja2 are the gui-only libs (uvicorn is always present via the
    # core SDK); blocking them makes ``import fastapi`` / ``import jinja2`` raise.
    for mod in ("fastapi", "jinja2"):
        monkeypatch.setitem(sys.modules, mod, None)
    for name in list(sys.modules):
        if name == "adw.gui.app" or name.startswith("adw.gui.app."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    result = cli.invoke(app, ["gui"])
    assert result.exit_code != 0
    # The raw ImportError must be caught and translated, not propagated.
    assert not isinstance(result.exception, ImportError)
    out = result.output
    low = out.lower()
    assert "gui" in low
    assert "adw[gui]" in out or "install" in low or "pip" in low or "uv" in low


def test_core_import_path_does_not_pull_the_web_stack():
    """AC 21: importing the core CLI must not import the gui-only web libraries —
    the web stack lives behind the lazy ``adw.gui.app`` import only.

    The enforceable boundary is FastAPI + Jinja2: those ship ONLY via adw[gui],
    so a plain ``adw run`` install would lack them. uvicorn (and starlette) are
    deliberately excluded here because the core ``claude-agent-sdk`` dependency
    already imports uvicorn transitively (adw.cli -> adw.agents ->
    claude_agent_sdk), so it is always present regardless of the gui extra — the
    contract's listing of uvicorn as gui-only is a documented finding, not a
    thing this run can or should change (adw.agents/the SDK are out of scope)."""
    code = (
        "import sys, adw.cli;"
        "bad = {'fastapi', 'jinja2'} & set(sys.modules);"
        "print(sorted(bad));"
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
