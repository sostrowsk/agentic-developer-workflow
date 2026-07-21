from pathlib import Path

import pytest

from adw.agents import REGISTRY, AgentResult, SdkAgentRunner
from adw.mock import MockAgentRunner


class FakeInitMessage:
    def __init__(self, session_id: str):
        self.subtype = "init"
        self.data = {"session_id": session_id}


class FakeTextBlock:
    def __init__(self, text: str):
        self.text = text


class FakeAssistantMessage:
    def __init__(self, *texts: str):
        self.content = [FakeTextBlock(t) for t in texts]


class FakeResultMessage:
    def __init__(self, result: str | None, session_id: str, is_error: bool = False):
        self.result = result
        self.session_id = session_id
        self.is_error = is_error


@pytest.fixture(autouse=True)
def fake_stored_login(monkeypatch, tmp_path_factory):
    """Hermetic: faked stored-login credentials, independent of the machine."""
    config_dir = tmp_path_factory.mktemp("claude-config")
    (config_dir / ".credentials.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.fixture
def captured_query(monkeypatch):
    captured = {}

    def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options

        async def stream():
            yield FakeInitMessage("sess-init")
            yield FakeAssistantMessage("Zwischenstand")
            yield FakeResultMessage("Fertig: alles grün", "sess-final")

        return stream()

    monkeypatch.setattr("adw.agents.query", fake_query)
    return captured


def test_sdk_runner_passes_spec_options_through(captured_query, tmp_path):
    runner = SdkAgentRunner()
    spec = REGISTRY["final_reviewer"]
    result = runner.run(spec, "Prüfe die Implementierung", cwd=tmp_path, resume="alte-session")
    options = captured_query["options"]
    assert options.model == spec.model
    assert options.cwd == str(tmp_path)
    assert options.resume == "alte-session"
    assert options.allowed_tools == spec.allowed_tools
    assert options.system_prompt["preset"] == "claude_code"
    assert spec.system_append in options.system_prompt["append"]
    assert captured_query["prompt"] == "Prüfe die Implementierung"
    assert result.text == "Fertig: alles grün"


def test_sdk_runner_extracts_session_id_from_stream(captured_query, tmp_path):
    result = SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)
    assert result.session_id == "sess-final"


def test_sdk_runner_falls_back_to_assistant_text(monkeypatch, tmp_path):
    def fake_query(*, prompt, options):
        async def stream():
            yield FakeAssistantMessage("Teil 1", "Teil 2")
            yield FakeResultMessage(None, "sess-1")

        return stream()

    monkeypatch.setattr("adw.agents.query", fake_query)
    result = SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)
    assert result.text == "Teil 1\nTeil 2"


def test_sdk_runner_raises_on_error_result(monkeypatch, tmp_path):
    from adw.agents import AgentRunError

    def fake_query(*, prompt, options):
        async def stream():
            yield FakeResultMessage("Kaputt", "sess-1", is_error=True)

        return stream()

    monkeypatch.setattr("adw.agents.query", fake_query)
    with pytest.raises(AgentRunError):
        SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)


def test_sdk_runner_surfaces_structured_errors_when_result_is_none(monkeypatch, tmp_path):
    """Regression: is_error with result=None must show message.errors, not '(kein Result)'."""
    from adw.agents import AgentRunError

    def fake_query(*, prompt, options):
        async def stream():
            msg = FakeResultMessage(None, "sess-1", is_error=True)
            msg.errors = [{"type": "sandbox_unavailable", "message": "bubblewrap fehlt"}]
            yield msg

        return stream()

    monkeypatch.setattr("adw.agents.query", fake_query)
    with pytest.raises(AgentRunError, match="bubblewrap fehlt"):
        SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)


def test_sdk_runner_exposes_only_spec_tools_and_sanitized_env(
    captured_query, tmp_path, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "geheim")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "geheim2")
    SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)
    options = captured_query["options"]
    # tools RESTRINGIERT das Angebot — allowed_tools approved nur; ohne tools
    # könnte der Spec-Agent mit acceptEdits Produktivcode ändern.
    assert set(options.tools) == {"Read", "Grep", "Glob", "Write", "Edit"}
    assert "Bash" not in options.tools
    assert options.permission_mode == "default"
    # SDK merged os.environ + options.env — Secrets werden per ""-Override
    # neutralisiert (Auth läuft über das Credentials-File unter HOME).
    assert options.env.get("ANTHROPIC_API_KEY") == ""
    assert options.env.get("AWS_SECRET_ACCESS_KEY") == ""
    assert options.env.get("PATH", "geerbt")  # PATH wird nie geblankt


def test_spec_agent_writes_are_only_approved_for_its_artifact(captured_query, tmp_path):
    SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)
    options = captured_query["options"]
    assert "Write(.adw/spec.md)" in options.allowed_tools
    assert "Write" not in options.allowed_tools  # kein pauschales Write-Approval
    assert not any(".adw/**" in rule for rule in options.allowed_tools)


def test_build_agent_is_worktree_scoped_and_bash_sandboxed(captured_query, tmp_path):
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    options = captured_query["options"]
    # Kein pauschales acceptEdits: Schreiben nur im Worktree (./**), Bash nur
    # sandboxed — sonst könnte der Builder Haupt-Checkout/Nachbar-Lanes ändern.
    assert options.permission_mode == "default"
    assert "Write(./**)" in options.allowed_tools
    assert "Edit(./**)" in options.allowed_tools
    assert "Write" not in options.allowed_tools
    assert {"Read", "Write", "Edit", "Bash"} <= set(options.tools)
    assert options.sandbox and options.sandbox["enabled"] is True


def test_all_agents_run_with_isolated_settings(captured_query, tmp_path):
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    options = captured_query["options"]
    # Repo-kontrollierte .claude/settings.json-Hooks und .mcp.json dürfen im
    # Headless-Betrieb nicht geladen werden.
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.mcp_servers == {}


def test_builder_sandbox_has_no_escape_hatch_commands(captured_query, tmp_path):
    """Regression: git ran unsandboxed and could break Lane isolation via hooks."""
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    sandbox = captured_query["options"].sandbox
    assert not sandbox.get("excludedCommands")


def test_builder_sandbox_forbids_unsandboxed_commands(captured_query, tmp_path):
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    sandbox = captured_query["options"].sandbox
    assert sandbox["allowUnsandboxedCommands"] is False


def test_secret_stores_are_deny_listed_for_all_agents(captured_query, tmp_path):
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    denied = set(captured_query["options"].disallowed_tools)
    # Beide Formen: Glob für die File-Tools UND plain Directory für die
    # Linux-Sandbox (die Glob-only-Denies ignoriert).
    for store in ("~/.ssh", "~/.aws", "~/.claude", "~/.azure", "~/.codex"):
        assert f"Read({store}/**)" in denied
        assert f"Read({store})" in denied


def test_custom_codex_home_is_deny_listed_for_agents(captured_query, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "/custom/codex-home")
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    denied = set(captured_query["options"].disallowed_tools)
    for tool in ("Read", "Write", "Edit"):
        assert f"{tool}(/custom/codex-home)" in denied
        assert f"{tool}(/custom/codex-home/**)" in denied


def test_shell_startup_and_history_files_are_deny_listed(captured_query, tmp_path):
    """Regression: source ~/.bashrc must not bring back exported secrets."""
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    denied = set(captured_query["options"].disallowed_tools)
    for startup in ("~/.bashrc", "~/.profile", "~/.bash_profile", "~/.zshrc", "~/.bash_history"):
        assert f"Read({startup})" in denied


def test_sandbox_must_fail_if_unavailable(captured_query, tmp_path):
    """Regression: without bubblewrap the builder must not silently run unsandboxed."""
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    sandbox = captured_query["options"].sandbox
    assert sandbox["failIfUnavailable"] is True


def test_claude_config_dir_is_not_blanked(captured_query, tmp_path):
    SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)
    options = captured_query["options"]
    assert options.env.get("CLAUDE_CONFIG_DIR", "nicht-geblankt") != ""


def test_macos_keychain_login_is_not_blocked(captured_query, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("adw.agents._IS_MACOS", True)
    result = SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)
    assert result.text  # Keychain-Auth wird der CLI überlassen, kein Vorab-Block


def test_custom_config_dir_is_deny_listed(captured_query, fake_stored_login, tmp_path):
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    denied = set(captured_query["options"].disallowed_tools)
    for tool in ("Read", "Write", "Edit"):
        assert f"{tool}({fake_stored_login})" in denied
        assert f"{tool}({fake_stored_login}/**)" in denied


def test_credential_files_are_deny_listed(captured_query, tmp_path):
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    denied = set(captured_query["options"].disallowed_tools)
    for rule in (
        "Read(~/.netrc)",
        "Read(~/.git-credentials)",
        "Read(~/.docker/**)",
        "Read(~/.kube/**)",
    ):
        assert rule in denied


def test_custom_config_dir_without_credentials_fails_despite_home_login(
    monkeypatch, tmp_path, tmp_path_factory
):
    from adw.agents import AgentRunError

    home = tmp_path_factory.mktemp("home")
    (home / ".claude").mkdir()
    (home / ".claude" / ".credentials.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    empty_config = tmp_path_factory.mktemp("empty-config")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty_config))
    monkeypatch.setattr("adw.agents._IS_MACOS", False)
    # Die CLI honoriert NUR das custom Config-Dir — der HOME-Fallback wäre
    # ein falsches Go und endete in kryptischem Auth-Fehler.
    with pytest.raises(AgentRunError, match="claude login|Anmeldung"):
        SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)


def test_deny_read_paths_become_both_rule_forms(captured_query, tmp_path):
    SdkAgentRunner().run(
        REGISTRY["build_agent"],
        "task",
        cwd=tmp_path,
        deny_read_paths=["/repo/.adw/runs/x/trees/frontend"],
    )
    denied = set(captured_query["options"].disallowed_tools)
    assert "Read(/repo/.adw/runs/x/trees/frontend/**)" in denied
    assert "Read(/repo/.adw/runs/x/trees/frontend)" in denied


def test_relative_config_dir_is_normalized_for_the_cli(captured_query, monkeypatch, tmp_path):
    rel = tmp_path / "rel-config"
    rel.mkdir()
    (rel / ".credentials.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "rel-config")
    SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)
    passed = captured_query["options"].env.get("CLAUDE_CONFIG_DIR")
    assert passed == str(rel)  # absolut — die CLI startet mit anderem cwd


def test_shell_keeps_bash_semantics_but_blanks_startup_hooks(captured_query, tmp_path):
    """Bash semantics are preserved; BASH_ENV/ENV are blanked (no secret re-import
    via startup files of non-interactive shells)."""
    SdkAgentRunner().run(REGISTRY["build_agent"], "task", cwd=tmp_path)
    env = captured_query["options"].env
    assert env.get("SHELL") == "/bin/bash"
    assert env.get("BASH_ENV", "") == ""
    assert env.get("ENV", "") == ""


def test_mock_records_deny_read_paths(tmp_path):
    mock = MockAgentRunner()
    mock.script("build_agent", "ok")
    mock.run(
        REGISTRY["build_agent"],
        "baue",
        cwd=tmp_path,
        deny_read_paths=["/repo/.adw/runs/x/trees/frontend"],
    )
    assert mock.calls[0].deny_read_paths == ("/repo/.adw/runs/x/trees/frontend",)


def test_missing_stored_login_fails_fast_with_clear_message(monkeypatch, tmp_path):
    from adw.agents import AgentRunError

    monkeypatch.setenv("HOME", str(tmp_path))  # kein ~/.claude vorhanden
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-nur-env")
    monkeypatch.setattr("adw.agents._IS_MACOS", False)
    with pytest.raises(AgentRunError, match="claude login|Anmeldung"):
        SdkAgentRunner().run(REGISTRY["spec_agent"], "task", cwd=tmp_path)


def test_builder_is_instructed_not_to_commit():
    assert "do not commit" in REGISTRY["build_agent"].system_append.lower()


def test_mock_runner_gives_fresh_sessions_per_conversation(tmp_path):
    from adw.mock import MockAgentRunner as Mock

    mock = Mock()
    mock.script("build_agent", "be", "fe")
    first = mock.run(REGISTRY["build_agent"], "backend", cwd=tmp_path)
    second = mock.run(REGISTRY["build_agent"], "frontend", cwd=tmp_path)
    assert first.session_id != second.session_id


def test_mock_runner_resume_returns_same_session(tmp_path):
    from adw.mock import MockAgentRunner as Mock

    mock = Mock()
    mock.script("build_agent", "eins", "zwei")
    first = mock.run(REGISTRY["build_agent"], "a", cwd=tmp_path)
    resumed = mock.run(REGISTRY["build_agent"], "b", cwd=tmp_path, resume=first.session_id)
    assert resumed.session_id == first.session_id


def test_registry_reviewers_are_read_only():
    for name in ("final_reviewer", "e2e_triage", "log_analyst"):
        tools = REGISTRY[name].allowed_tools
        assert not {"Write", "Edit", "Bash"} & set(tools), f"{name} darf nicht schreiben"


def test_registry_spec_and_plan_write_only_adw_dir():
    for name in ("spec_agent", "plan_agent"):
        tools = REGISTRY[name].allowed_tools
        assert any(t.startswith("Write") for t in tools)
        assert "Bash" not in tools


def test_registry_builders_have_full_tooling_and_opus():
    build = REGISTRY["build_agent"]
    assert {"Read", "Write", "Edit", "Bash"} <= set(build.tools)
    assert "opus" in build.model


def test_all_read_rules_are_workspace_scoped():
    """No blanket Read/Grep/Glob approval — cwd is not a filesystem boundary."""
    for spec in REGISTRY.values():
        for rule in spec.allowed_tools:
            assert not rule.startswith(("Read,", "Grep,", "Glob,"))
            assert rule not in {"Read", "Grep", "Glob", "Write", "Edit"}, (
                f"{spec.name}: Regel {rule!r} approved global statt workspace-relativ"
            )


def test_registry_models_match_spec():
    assert REGISTRY["spec_agent"].model == "claude-fable-5"
    assert REGISTRY["final_reviewer"].model == "claude-fable-5"
    assert REGISTRY["build_agent"].model == "claude-opus-4-8"
    assert REGISTRY["e2e_triage"].model == "claude-sonnet-5"
    assert REGISTRY["log_analyst"].model == "claude-sonnet-5"


def test_mock_runner_returns_scripted_responses_in_order(tmp_path):
    mock = MockAgentRunner()
    mock.script("spec_agent", "Antwort 1", "Antwort 2")
    spec = REGISTRY["spec_agent"]
    assert mock.run(spec, "a", cwd=tmp_path).text == "Antwort 1"
    assert mock.run(spec, "b", cwd=tmp_path).text == "Antwort 2"


def test_mock_runner_records_calls_with_resume(tmp_path):
    mock = MockAgentRunner()
    mock.script("build_agent", "ok")
    mock.run(REGISTRY["build_agent"], "baue", cwd=tmp_path, resume="sess-7")
    call = mock.calls[0]
    assert call.agent == "build_agent"
    assert call.task == "baue"
    assert call.cwd == Path(tmp_path)
    assert call.resume == "sess-7"


def test_mock_runner_without_script_raises(tmp_path):
    mock = MockAgentRunner()
    with pytest.raises(AssertionError, match="spec_agent"):
        mock.run(REGISTRY["spec_agent"], "x", cwd=tmp_path)


def test_mock_runner_keeps_stable_session_per_agent(tmp_path):
    mock = MockAgentRunner()
    mock.script("build_agent", "eins", "zwei")
    first = mock.run(REGISTRY["build_agent"], "a", cwd=tmp_path)
    second = mock.run(REGISTRY["build_agent"], "b", cwd=tmp_path, resume=first.session_id)
    assert isinstance(first, AgentResult)
    assert first.session_id == second.session_id
