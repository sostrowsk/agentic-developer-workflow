import os
import re
import subprocess
from pathlib import Path

import pytest

from adw.codex import CodexAuthorError, CodexRunner
from adw.findings import FindingsParseError
from adw.mock import MockCodexRunner


@pytest.fixture
def captured_run(monkeypatch):
    captured = {}

    class FakePopen:
        pid = 4242

        def __init__(self, argv, **kwargs):
            import io

            captured["argv"] = argv
            captured["kwargs"] = kwargs
            stdout = captured.get("stdout", '{"verdict": "ok", "findings": []}')
            # Callable-Stdout: Authoring-Marker tragen eine Nonce aus dem
            # Prompt — die Antwort kann erst beim Aufruf gebaut werden.
            self.stdout = io.BytesIO((stdout(argv) if callable(stdout) else stdout).encode())
            self.stderr = io.BytesIO(captured.get("stderr", "").encode())
            self._timed_out_once = False

        def wait(self, timeout=None):
            if timeout is not None:
                # Der Haupt-wait (mit Timeout) — hier "läuft" codex.
                captured["timeout"] = timeout
                home = captured["kwargs"].get("env", {}).get("CODEX_HOME")
                if home and os.path.isdir(home):
                    captured["home_files"] = sorted(os.listdir(home))
                    if "mutate_home" in captured:
                        captured["mutate_home"](home)
                if captured.get("raise_timeout") and not self._timed_out_once:
                    self._timed_out_once = True
                    raise subprocess.TimeoutExpired(captured["argv"], timeout)
            return captured.get("returncode", 0)

        @property
        def returncode(self):
            return captured.get("returncode", 0)

    monkeypatch.setattr("adw.codex.subprocess.Popen", FakePopen)
    # _kill_group läuft jetzt in JEDEM Pfad — nie echte PIDs killen.
    monkeypatch.setattr(
        "adw.codex.os.killpg", lambda pid, sig: captured.setdefault("killed_pid", pid)
    )
    return captured


def test_codex_is_invoked_read_only_in_cwd(captured_run, tmp_path):
    result = CodexRunner().review("code", ["diff gegen staging"], cwd=tmp_path)
    argv = captured_run["argv"]
    assert argv[:2] == ["codex", "exec"]
    assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "read-only"
    assert "-C" in argv and argv[argv.index("-C") + 1] == str(tmp_path)
    assert result.verdict == "ok"


def test_prompt_contains_schema_and_content(captured_run, tmp_path):
    CodexRunner().review("spec", [".adw/spec.md"], cwd=tmp_path)
    prompt = captured_run["argv"][-1]
    assert "verdict" in prompt and "remediation_plan" in prompt
    assert ".adw/spec.md" in prompt
    assert "spec" in prompt.lower()


def test_spec_and_plan_review_flag_process_requirements(captured_run, tmp_path):
    """Prozess-/Historien-Vorgaben in Akzeptanzkriterien/DoD sind ein Finding —
    im ADW committet der Orchestrator, nicht der Implementierungs-Agent."""
    for kind in ("spec", "plan"):
        CodexRunner().review(kind, [".adw/spec.md"], cwd=tmp_path)
        prompt = captured_run["argv"][-1].lower()
        assert "commit" in prompt, kind


def test_prompt_contains_review_context_between_refs_and_schema(captured_run, tmp_path):
    """Review-Policy v2: der Kontext der Vorrunden steht zwischen den Refs und
    der Schema-Instruktion — das Antwortformat bleibt die letzte Anweisung."""
    context = "This is review round 2 of max 5.\n- [P2] a.py: kaputt — disposition: fix dispatched"
    CodexRunner().review("code", ["src.py"], cwd=tmp_path, context=context)
    prompt = captured_run["argv"][-1]
    assert context in prompt
    assert prompt.index("- src.py") < prompt.index(context) < prompt.index('"verdict"')


def test_prompt_without_context_is_unchanged(captured_run, tmp_path):
    CodexRunner().review("code", ["src.py"], cwd=tmp_path)
    without_context = captured_run["argv"][-1]
    CodexRunner().review("code", ["src.py"], cwd=tmp_path, context=None)
    assert captured_run["argv"][-1] == without_context
    CodexRunner().review("code", ["src.py"], cwd=tmp_path, context="   ")
    assert captured_run["argv"][-1] == without_context


def test_broken_codex_output_raises_parse_error(captured_run, tmp_path):
    captured_run["stdout"] = "Ich bin nur Prosa ohne JSON."
    with pytest.raises(FindingsParseError):
        CodexRunner().review("code", ["x"], cwd=tmp_path)


def test_nonzero_exit_raises(captured_run, tmp_path):
    from adw.codex import CodexError

    captured_run["returncode"] = 2
    captured_run["stderr"] = "kaputt"
    with pytest.raises(CodexError, match="kaputt"):
        CodexRunner().review("code", ["x"], cwd=tmp_path)


def test_timeout_kills_process_group_and_raises(captured_run, tmp_path):
    from adw.codex import CodexError

    captured_run["raise_timeout"] = True
    with pytest.raises(CodexError, match="[Tt]imeout"):
        CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert captured_run["killed_pid"] == 4242  # ganze Prozessgruppe, nicht nur das Kind


def test_group_cleanup_runs_on_normal_exit(captured_run, tmp_path):
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert captured_run["killed_pid"] == 4242


def test_codex_starts_in_its_own_session(captured_run, tmp_path):
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert captured_run["kwargs"]["start_new_session"] is True


def test_codex_runs_with_sanitized_env(captured_run, tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "geheim")
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    env = captured_run["kwargs"]["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_codex_call_has_timeout(captured_run, tmp_path):
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert captured_run["timeout"] > 0


def test_default_effective_timeout_is_900(captured_run, tmp_path):
    """Ohne konfigurierten Wert bleibt das effektive Subprozess-Timeout 900s."""
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert captured_run["timeout"] == 900


def test_configured_timeout_reaches_the_review_subprocess(captured_run, tmp_path):
    """A3/F2: der aus der Config gereichte Timeout-Wert kommt am gemeinsamen
    `codex exec`-Subprozesspfad als effektives Zeitlimit an."""
    CodexRunner(timeout=123).review("code", ["x"], cwd=tmp_path)
    assert captured_run["timeout"] == 123


def test_configured_timeout_is_shared_by_the_author_subprocess(captured_run, tmp_path):
    """Autor UND Review teilen denselben Timeout-Wert am `codex exec`-Pfad."""
    captured_run["stdout"] = _blocks(**{"spec.md": "# Ziel\n"})
    CodexRunner(timeout=77).author("spec", "Issue", cwd=tmp_path)
    assert captured_run["timeout"] == 77


def test_codex_home_is_isolated_copy_with_auth_only(captured_run, tmp_path, monkeypatch):
    """Regression: config.toml (incl. MCP registrations) must not reach the review."""
    from adw.env import safe_env

    src = tmp_path / "codex-home"
    src.mkdir()
    (src / "auth.json").write_text('{"token": "x"}')
    (src / "config.toml").write_text('[mcp_servers.evil]\ncommand = "x"\n')
    monkeypatch.setenv("CODEX_HOME", str(src))
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    passed_home = captured_run["kwargs"]["env"]["CODEX_HOME"]
    assert passed_home != str(src)
    assert captured_run["home_files"] == ["auth.json"]
    assert not Path(passed_home).exists()  # Temp-Home (mit Token) wird aufgeräumt
    assert "CODEX_HOME" not in safe_env()  # Gates/Agents sehen den Pfad nie


def test_rotated_auth_token_is_synced_back(captured_run, tmp_path, monkeypatch):
    """Regression: tokens rotated by codex must not die with the temp home."""
    src = tmp_path / "codex-home"
    src.mkdir()
    (src / "auth.json").write_text('{"token": "alt"}')
    monkeypatch.setenv("CODEX_HOME", str(src))
    captured_run["mutate_home"] = lambda home: (Path(home) / "auth.json").write_text(
        '{"token": "rotiert"}'
    )
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert (src / "auth.json").read_text() == '{"token": "rotiert"}'


def test_externally_rotated_source_wins_over_stale_copy(captured_run, tmp_path, monkeypatch):
    """Regression: a source rotated in parallel must not be overwritten with our copy."""
    src = tmp_path / "codex-home"
    src.mkdir()
    (src / "auth.json").write_text('{"token": "alt"}')
    monkeypatch.setenv("CODEX_HOME", str(src))

    def mutate(home):
        (Path(home) / "auth.json").write_text('{"token": "unsere-rotation"}')
        (src / "auth.json").write_text('{"token": "extern-rotiert"}')

    captured_run["mutate_home"] = mutate
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert (src / "auth.json").read_text() == '{"token": "extern-rotiert"}'


def test_failed_sync_keeps_recoverable_token_copy(captured_run, tmp_path, monkeypatch, capsys):
    """Regression: if the sync-back fails, the only valid token must not die."""
    src = tmp_path / "codex-home"
    src.mkdir()
    (src / "auth.json").write_text('{"token": "alt"}')
    monkeypatch.setenv("CODEX_HOME", str(src))
    captured_run["mutate_home"] = lambda home: (Path(home) / "auth.json").write_text(
        '{"token": "rotiert"}'
    )
    # Deterministische Fehlerinjektion statt chmod — als root (Container-CI)
    # würde chmod 555 nichts verhindern und der Test falsch fehlschlagen.
    import tempfile as real_tempfile

    real_mkstemp = real_tempfile.mkstemp

    def failing_mkstemp(*args, **kwargs):
        if str(kwargs.get("dir", "")) == str(src):
            raise OSError("mkstemp kaputt")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr("adw.codex.tempfile.mkstemp", failing_mkstemp)
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    passed_home = Path(captured_run["kwargs"]["env"]["CODEX_HOME"])
    try:
        assert passed_home.exists(), "Temp-Home mit rotiertem Token wurde trotzdem gelöscht"
        assert (passed_home / "auth.json").read_text() == '{"token": "rotiert"}'
        assert "auth.json" in capsys.readouterr().err
    finally:
        import shutil

        shutil.rmtree(passed_home, ignore_errors=True)


def test_failed_replace_leaves_no_tmp_token_files(captured_run, tmp_path, monkeypatch):
    """Regression: a failed sync must not leave .auth.*.tmp token copies behind."""
    import shutil

    src = tmp_path / "codex-home"
    src.mkdir()
    (src / "auth.json").write_text('{"token": "alt"}')
    monkeypatch.setenv("CODEX_HOME", str(src))
    captured_run["mutate_home"] = lambda home: (Path(home) / "auth.json").write_text(
        '{"token": "rotiert"}'
    )
    real_replace = os.replace

    def failing_replace(a, b):
        if str(b).endswith("auth.json") and str(src) in str(b):
            raise OSError("replace kaputt")
        return real_replace(a, b)

    monkeypatch.setattr("adw.codex.os.replace", failing_replace)
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert not list(src.glob(".auth.*.tmp")), "Token-Kopie als tmp-File liegengeblieben"
    shutil.rmtree(captured_run["kwargs"]["env"]["CODEX_HOME"], ignore_errors=True)


def test_default_codex_home_is_also_isolated(captured_run, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}")
    (home / ".codex" / "config.toml").write_text('[mcp_servers.evil]\ncommand = "x"\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    CodexRunner().review("code", ["x"], cwd=tmp_path)
    assert captured_run["home_files"] == ["auth.json"]


def _marker_id(prompt: str) -> str:
    """Nonce der Marker aus dem Authoring-Prompt (pro Aufruf frisch)."""
    match = re.search(r"===BEGIN [^\s=]+ ([0-9a-f]+)===", prompt)
    assert match, f"Kein Marker mit Nonce im Prompt: {prompt[-500:]!r}"
    return match.group(1)


def _blocks(**named: str):
    """Stdout-Builder: baut die Marker-Blöcke mit der Nonce des echten Prompts."""

    def build(argv: list[str]) -> str:
        nonce = _marker_id(argv[-1])
        return "\n".join(
            f"===BEGIN {name} {nonce}===\n{body}\n===END {name} {nonce}==="
            for name, body in named.items()
        )

    return build


def _join(*parts):
    def build(argv: list[str]) -> str:
        return "\n".join(part(argv) if callable(part) else part for part in parts)

    return build


def test_author_is_invoked_read_only_in_cwd(captured_run, tmp_path):
    captured_run["stdout"] = _blocks(**{"spec.md": "# Ziel\nDas Issue lösen."})
    artifacts = CodexRunner().author("spec", "Setze Issue #7 um", cwd=tmp_path)
    argv = captured_run["argv"]
    assert argv[:2] == ["codex", "exec"]
    assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "read-only"
    assert "-C" in argv and argv[argv.index("-C") + 1] == str(tmp_path)
    assert artifacts == {"spec.md": "# Ziel\nDas Issue lösen."}


def test_author_prompt_carries_task_and_marker_contract(captured_run, tmp_path):
    captured_run["stdout"] = _blocks(**{"spec.md": "x"})
    CodexRunner().author("spec", "Setze Issue #7 um", cwd=tmp_path)
    prompt = captured_run["argv"][-1]
    nonce = _marker_id(prompt)
    assert "Setze Issue #7 um" in prompt
    assert f"===BEGIN spec.md {nonce}===" in prompt and f"===END spec.md {nonce}===" in prompt


def test_author_markers_carry_a_fresh_nonce_per_call(captured_run, tmp_path):
    """Die Nonce macht die Marker kollisionssicher: Inhalte, die selbst über
    das Marker-Format schreiben, können den Block nicht früh beenden."""
    captured_run["stdout"] = _blocks(**{"spec.md": "x"})
    CodexRunner().author("spec", "Issue", cwd=tmp_path)
    first = _marker_id(captured_run["argv"][-1])
    CodexRunner().author("spec", "Issue", cwd=tmp_path)
    assert _marker_id(captured_run["argv"][-1]) != first


def test_author_keeps_content_that_mentions_the_bare_markers(captured_run, tmp_path):
    """Regression: eine Spec ÜBER dieses Protokoll darf nicht abgeschnitten werden."""
    body = "# Ziel\n===END spec.md===\nsteht mitten im Text.\n===BEGIN spec.md===\nauch."
    captured_run["stdout"] = _blocks(**{"spec.md": body})
    assert CodexRunner().author("spec", "Issue", cwd=tmp_path) == {"spec.md": body}


def test_author_rejects_a_pure_prompt_echo_as_answer(captured_run, tmp_path):
    """Regression: echot codex nur den Prompt (leere Vorlage-Blöcke), ist das
    keine Antwort — sonst landeten Platzhalter als Artefakte auf der Platte."""
    from adw.codex import CodexAuthorError

    captured_run["stdout"] = lambda argv: argv[-1]
    with pytest.raises(CodexAuthorError, match="[Ll]eer"):
        CodexRunner().author("spec", "Issue", cwd=tmp_path)
    captured_run["stdout"] = lambda argv: argv[-1]
    with pytest.raises(CodexAuthorError, match="[Ll]eer"):
        CodexRunner().author("plan", "Aus .adw/spec.md", cwd=tmp_path)


def test_author_spec_prompt_mirrors_the_spec_agent_requirements(captured_run, tmp_path):
    """Same content requirements as the spec_agent system prompt: proportionality,
    'Deferred' section, no process/git requirements."""
    captured_run["stdout"] = _blocks(**{"spec.md": "x"})
    CodexRunner().author("spec", "Issue", cwd=tmp_path)
    prompt = captured_run["argv"][-1].lower()
    assert "proportionality" in prompt
    assert "deferred" in prompt
    assert "commit" in prompt
    assert "acceptance criteria" in prompt


def test_author_prompts_carry_the_shared_content_rules_verbatim(captured_run, tmp_path):
    """Claude-Autor und Codex-Autor müssen nach demselben Maßstab schreiben:
    die Regeln stammen aus adw.agents, nicht aus einer zweiten Kopie."""
    from adw.agents import _PLAN_CONTENT_RULES, _SPEC_CONTENT_RULES

    captured_run["stdout"] = _blocks(**{"spec.md": "x"})
    CodexRunner().author("spec", "Issue", cwd=tmp_path)
    assert _SPEC_CONTENT_RULES in captured_run["argv"][-1]

    captured_run["stdout"] = _blocks(**{"plan.md": "x", "contract.yaml": "y"})
    CodexRunner().author("plan", "Aus .adw/spec.md", cwd=tmp_path)
    assert _PLAN_CONTENT_RULES in captured_run["argv"][-1]


def test_author_plan_prompt_requires_plan_and_contract_blocks(captured_run, tmp_path):
    captured_run["stdout"] = _blocks(
        **{"plan.md": "## Workstream backend", "contract.yaml": "routes: []"}
    )
    artifacts = CodexRunner().author("plan", "Aus .adw/spec.md", cwd=tmp_path)
    prompt = captured_run["argv"][-1]
    nonce = _marker_id(prompt)
    assert f"===BEGIN plan.md {nonce}===" in prompt
    assert f"===BEGIN contract.yaml {nonce}===" in prompt
    assert "contract" in prompt.lower() and "workstream" in prompt.lower()
    assert artifacts == {"plan.md": "## Workstream backend", "contract.yaml": "routes: []"}


def test_author_takes_the_last_block_when_the_transcript_echoes_markers(captured_run, tmp_path):
    """The tool transcript may echo the prompt (incl. markers) — only the final
    block counts, as with the review JSON."""
    captured_run["stdout"] = _join(
        _blocks(**{"spec.md": "Entwurf, verworfen."}),
        "Jetzt die echte Antwort:",
        _blocks(**{"spec.md": "# Ziel\nEcht."}),
    )
    assert CodexRunner().author("spec", "Issue", cwd=tmp_path) == {"spec.md": "# Ziel\nEcht."}


def test_author_without_block_raises_author_error(captured_run, tmp_path):
    from adw.codex import CodexAuthorError

    captured_run["stdout"] = "Ich bin nur Prosa ohne Marker."
    with pytest.raises(CodexAuthorError, match="spec.md"):
        CodexRunner().author("spec", "Issue", cwd=tmp_path)


def test_author_with_empty_block_raises_author_error(captured_run, tmp_path):
    from adw.codex import CodexAuthorError

    captured_run["stdout"] = _blocks(**{"spec.md": "   \n  "})
    with pytest.raises(CodexAuthorError, match="[Ll]eer"):
        CodexRunner().author("spec", "Issue", cwd=tmp_path)


def test_author_with_one_missing_block_raises_author_error(captured_run, tmp_path):
    from adw.codex import CodexAuthorError

    captured_run["stdout"] = _blocks(**{"plan.md": "## Workstream backend"})
    with pytest.raises(CodexAuthorError, match="contract.yaml"):
        CodexRunner().author("plan", "Aus .adw/spec.md", cwd=tmp_path)


def test_author_never_mixes_blocks_from_prompt_echo_and_answer(captured_run, tmp_path):
    """Regression: fehlt contract.yaml in der finalen Antwort, darf NICHT der
    Platzhalter aus dem echoten Prompt einspringen."""
    from adw.codex import CodexAuthorError

    captured_run["stdout"] = _join(
        _blocks(**{"plan.md": "Entwurf", "contract.yaml": "Entwurf"}),
        "Jetzt die echte Antwort:",
        _blocks(**{"plan.md": "## Workstream backend"}),
    )
    with pytest.raises(CodexAuthorError, match="contract.yaml"):
        CodexRunner().author("plan", "Aus .adw/spec.md", cwd=tmp_path)


def test_author_takes_the_last_coherent_block_set(captured_run, tmp_path):
    captured_run["stdout"] = _join(
        _blocks(**{"plan.md": "Entwurf", "contract.yaml": "Entwurf"}),
        "Jetzt die echte Antwort:",
        _blocks(**{"plan.md": "## Workstream backend", "contract.yaml": "routes: []"}),
    )
    assert CodexRunner().author("plan", "Aus .adw/spec.md", cwd=tmp_path) == {
        "plan.md": "## Workstream backend",
        "contract.yaml": "routes: []",
    }


def test_author_error_is_a_codex_error():
    """Callers that catch CodexError must not have to know the authoring subclass."""
    from adw.codex import CodexAuthorError, CodexError

    assert issubclass(CodexAuthorError, CodexError)


def test_author_nonzero_exit_raises(captured_run, tmp_path):
    from adw.codex import CodexError

    captured_run["returncode"] = 2
    captured_run["stderr"] = "kaputt"
    with pytest.raises(CodexError, match="kaputt"):
        CodexRunner().author("spec", "Issue", cwd=tmp_path)


def test_author_uses_isolated_codex_home_and_sanitized_env(captured_run, tmp_path, monkeypatch):
    """Same isolation as review(): auth.json only, no config.toml, no secrets."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "geheim")
    src = tmp_path / "codex-home"
    src.mkdir()
    (src / "auth.json").write_text('{"token": "x"}')
    (src / "config.toml").write_text('[mcp_servers.evil]\ncommand = "x"\n')
    monkeypatch.setenv("CODEX_HOME", str(src))
    captured_run["stdout"] = _blocks(**{"spec.md": "x"})
    CodexRunner().author("spec", "Issue", cwd=tmp_path)
    env = captured_run["kwargs"]["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["CODEX_HOME"] != str(src)
    assert captured_run["home_files"] == ["auth.json"]
    assert not Path(env["CODEX_HOME"]).exists()  # Temp-Home wird aufgeräumt


def test_mock_codex_returns_scripted_results_in_order(tmp_path):
    from adw.findings import ReviewResult

    mock = MockCodexRunner()
    mock.script(
        ReviewResult.model_validate(
            {
                "verdict": "needs_fixes",
                "findings": [
                    {
                        "severity": "P1",
                        "lane": "backend",
                        "file": "x.py",
                        "issue": "kaputt",
                        "remediation_plan": ["fix"],
                    }
                ],
            }
        ),
        ReviewResult(verdict="ok", findings=[]),
    )
    first = mock.review("code", ["x"], cwd=tmp_path)
    second = mock.review("code", ["x"], cwd=tmp_path)
    assert first.verdict == "needs_fixes"
    assert second.verdict == "ok"
    assert [call.kind for call in mock.calls] == ["code", "code"]


def test_mock_codex_records_the_review_context(tmp_path):
    from adw.findings import ReviewResult

    mock = MockCodexRunner()
    mock.script(ReviewResult(verdict="ok", findings=[]), ReviewResult(verdict="ok", findings=[]))
    mock.review("code", ["x"], cwd=tmp_path)
    mock.review("code", ["x"], cwd=tmp_path, context="Runde 2 von 5")
    assert [call.context for call in mock.calls] == [None, "Runde 2 von 5"]


def test_mock_codex_without_script_raises(tmp_path):
    mock = MockCodexRunner()
    with pytest.raises(AssertionError):
        mock.review("spec", ["x"], cwd=tmp_path)


def test_mock_codex_authors_scripted_artifacts_per_kind_in_order(tmp_path):
    mock = MockCodexRunner()
    mock.script_artifacts("spec", {"spec.md": "# Ziel"}, {"spec.md": "# Ziel v2"})
    mock.script_artifacts("plan", {"plan.md": "## Plan", "contract.yaml": "{}"})
    # Die Queues sind je kind unabhängig — ein Plan-Aufruf dazwischen
    # verschiebt die Spec-Reihenfolge nicht.
    first = mock.author("spec", "Issue #7", cwd=tmp_path)
    plan = mock.author("plan", "Aus .adw/spec.md", cwd=tmp_path)
    second = mock.author("spec", "Issue #7 (Retry)", cwd=tmp_path)
    assert first == {"spec.md": "# Ziel"}
    assert second == {"spec.md": "# Ziel v2"}
    assert plan == {"plan.md": "## Plan", "contract.yaml": "{}"}
    assert [(call.kind, call.task) for call in mock.author_calls] == [
        ("spec", "Issue #7"),
        ("plan", "Aus .adw/spec.md"),
        ("spec", "Issue #7 (Retry)"),
    ]


def test_mock_codex_author_raises_scripted_error(tmp_path):
    mock = MockCodexRunner()
    mock.script_author_error("spec", CodexAuthorError("Block fehlt"))
    mock.script_artifacts("spec", {"spec.md": "# Ziel"})
    with pytest.raises(CodexAuthorError, match="Block fehlt"):
        mock.author("spec", "Issue #7", cwd=tmp_path)
    # Der Fehlversuch ist aufgezeichnet, die Queue läuft danach weiter.
    assert mock.author("spec", "Issue #7", cwd=tmp_path) == {"spec.md": "# Ziel"}
    assert [call.kind for call in mock.author_calls] == ["spec", "spec"]


def test_mock_codex_author_without_script_raises(tmp_path):
    mock = MockCodexRunner()
    mock.script_artifacts("plan", {"plan.md": "## Plan"})
    with pytest.raises(AssertionError, match="spec"):
        mock.author("spec", "Issue #7", cwd=tmp_path)
