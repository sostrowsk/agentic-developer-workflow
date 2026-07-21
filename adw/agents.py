"""Agent registry (SPEC §3) and SDK runner (Claude Agent SDK)."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import anyio
from claude_agent_sdk import ClaudeAgentOptions, query

from adw.env import safe_env

FABLE = "claude-fable-5"
OPUS = "claude-opus-4-8"
SONNET = "claude-sonnet-5"

_IS_MACOS = sys.platform == "darwin"

READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]
WRITER_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit"]
BUILDER_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit", "Bash"]

# Approval-Regeln immer workspace-relativ — cwd allein ist keine
# Dateisystem-Grenze; ohne passende Regel wird headless verweigert.
SCOPED_READ_RULES = ["Read(./**)", "Grep(./**)", "Glob(./**)"]
# Artefakt-EXAKTE Schreibrechte: .adw/** würde auch .adw/runs/ (State!)
# und .adw/config.yaml erlauben.
SPEC_WRITE_RULES = ["Write(.adw/spec.md)", "Edit(.adw/spec.md)"]
PLAN_WRITE_RULES = [
    "Write(.adw/plan.md)",
    "Edit(.adw/plan.md)",
    "Write(.adw/contract.yaml)",
    "Edit(.adw/contract.yaml)",
]
SCOPED_WORKTREE_WRITE_RULES = ["Write(./**)", "Edit(./**)"]

# Deny schlägt Allow — Secret-Stores sind für ALLE Agents tabu, auch für
# sandboxte Bash-Reads. Beide Regel-Formen: Glob (File-Tools) UND plain
# Directory (Linux-Sandbox ignoriert Glob-only-Denies).
_SECRET_STORES = [
    "~/.ssh",
    "~/.aws",
    "~/.claude",
    "~/.gnupg",
    "~/.config",
    "~/.docker",
    "~/.kube",
    "~/.azure",
    "~/.codex",
]
_SECRET_FILES = [
    "~/.netrc",
    "~/.git-credentials",
    "~/.npmrc",
    "~/.pypirc",
    # Shell-Startup- und History-Files: dort exportierte Secrets dürfen auch
    # per explizitem `source ~/.bashrc` nicht zurückgeholt werden.
    "~/.bashrc",
    "~/.bash_profile",
    "~/.profile",
    "~/.zshrc",
    "~/.zprofile",
    "~/.zshenv",
    "~/.bash_history",
    "~/.zsh_history",
]
SECRET_STORE_DENY_RULES = (
    [rule for store in _SECRET_STORES for rule in (f"Read({store})", f"Read({store}/**)")]
    + [f"Read({file})" for file in _SECRET_FILES]
    # Run-State und Transkripte gehören dem Orchestrator — kein Agent
    # schreibt in .adw/runs/ (Deny schlägt jedes Allow).
    + ["Write(.adw/runs/**)", "Edit(.adw/runs/**)"]
)


class AgentRunError(Exception):
    """The agent run ended with an error result."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    model: str
    # tools RESTRINGIERT, was der Agent überhaupt angeboten bekommt;
    # allowed_tools approved nur (ggf. pfad-beschränkt). Beides nötig.
    tools: list[str]
    allowed_tools: list[str]
    system_append: str
    permission_mode: str = "default"
    sandbox_bash: bool = False


@dataclass(frozen=True)
class AgentResult:
    text: str
    session_id: str | None


class AgentRunner(Protocol):
    """One method, few parameters — no node needs more interface than that."""

    def run(
        self,
        agent: AgentSpec,
        task: str,
        cwd: Path,
        resume: str | None = None,
        deny_read_paths: list[str] | None = None,
    ) -> AgentResult: ...


REGISTRY: dict[str, AgentSpec] = {
    "spec_agent": AgentSpec(
        name="spec_agent",
        model=FABLE,
        tools=WRITER_TOOLS,
        allowed_tools=[*SCOPED_READ_RULES, *SPEC_WRITE_RULES],
        system_append=(
            "You are the Spec agent of an Agentic Developer Workflow. You write "
            "EXCLUSIVELY the specification following a fixed template (goal, scope, "
            "non-goals, acceptance criteria, Definition of Done) to .adw/spec.md. "
            "You never implement and never change production code."
        ),
    ),
    "plan_agent": AgentSpec(
        name="plan_agent",
        model=FABLE,
        tools=WRITER_TOOLS,
        allowed_tools=[*SCOPED_READ_RULES, *PLAN_WRITE_RULES],
        system_append=(
            "You are the Plan agent of an Agentic Developer Workflow. From "
            ".adw/spec.md you produce the implementation plan .adw/plan.md with the "
            "Workstreams 'backend' and 'frontend' (if the lane exists) as well as the "
            "interface contract .adw/contract.yaml (OpenAPI/types/events). "
            "Both lanes will later build strictly against the contract. You never implement."
        ),
    ),
    "build_agent": AgentSpec(
        name="build_agent",
        model=OPUS,
        tools=BUILDER_TOOLS,
        # Schreiben nur worktree-relativ (./**) statt pauschal; Bash läuft
        # sandboxed (sandbox_bash) — kein Ausbruch in Haupt-Checkout/Lanes.
        allowed_tools=[*SCOPED_READ_RULES, *SCOPED_WORKTREE_WRITE_RULES, "Bash"],
        sandbox_bash=True,
        system_append=(
            "You are a Build agent in an isolated Lane (its own git worktree). "
            "You implement your Workstream from .adw/plan.md strictly against "
            ".adw/contract.yaml, using TDD (test first, confirm RED, then implement "
            "minimally). Fix plans from reviews are recommendations: check them against "
            "the spec and conventions and deviate with justification when necessary. "
            "You do NOT commit — after green Gates the orchestrator commits your changes."
        ),
    ),
    "e2e_triage": AgentSpec(
        name="e2e_triage",
        model=SONNET,
        tools=READ_ONLY_TOOLS,
        allowed_tools=SCOPED_READ_RULES,
        system_append=(
            "You are the E2E triage agent. You assign every Playwright failure to a "
            "Lane (frontend/backend). You fix nothing. Respond ONLY with the "
            "review JSON according to the schema."
        ),
        permission_mode="default",
    ),
    "log_analyst": AgentSpec(
        name="log_analyst",
        model=SONNET,
        tools=READ_ONLY_TOOLS,
        allowed_tools=SCOPED_READ_RULES,
        system_append=(
            "You are the log analyst. You read CI logs and produce structured "
            "Findings with Lane assignment. You fix nothing. Respond ONLY with the "
            "review JSON according to the schema."
        ),
        permission_mode="default",
    ),
    "final_reviewer": AgentSpec(
        name="final_reviewer",
        model=FABLE,
        tools=READ_ONLY_TOOLS,
        allowed_tools=SCOPED_READ_RULES,
        system_append=(
            "You are the final reviewer (strictly read-only). You check the "
            "implementation against .adw/spec.md. You deliver ONLY Findings — no "
            "changes, no fixes. Respond ONLY with the review JSON according to the "
            "schema; set the category (scope_gap | implementation | trivial) on every Finding."
        ),
        permission_mode="default",
    ),
}


def _sanitized_env_overrides() -> dict[str, str]:
    """Neutralizes all non-whitelist variables in the CLI process.

    The SDK merges os.environ with options.env — REMOVING variables is
    not possible, but blanking via ""-override is. Secrets (API keys, cloud creds)
    thus reach neither the model nor Bash tool subprocesses. Claude
    authentication runs via the credentials file under HOME
    (whitelisted), not via env keys.
    """
    keep = safe_env()
    return {key: "" for key in os.environ if key not in keep}


def _env_overrides() -> dict[str, str]:
    overrides = _sanitized_env_overrides()
    config_dir = _config_dir()
    if config_dir:
        # Normalisiert durchreichen — die CLI startet mit Worktree-cwd.
        overrides["CLAUDE_CONFIG_DIR"] = str(config_dir)
    # SHELL auf bash pinnen (Bash-Semantik fürs Bash-Tool bleibt erhalten);
    # BASH_ENV/ENV sind durch die Sanitisierung geblankt, damit
    # nicht-interaktive Shells keine Startup-Files mit Secret-Exporten laden.
    # Known Limitation: Secrets, die NUR in ~/.bashrc exportiert werden,
    # könnten von CLI-Versionen mit Shell-Snapshot re-importiert werden.
    overrides["SHELL"] = "/bin/bash"
    overrides.setdefault("BASH_ENV", "")
    overrides.setdefault("ENV", "")
    return overrides


def _config_dir() -> Path | None:
    """CLAUDE_CONFIG_DIR as an ABSOLUTE path — the CLI starts with the
    Worktree as cwd, a relative value would point into the void there."""
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).resolve() if raw else None


def _deny_rules(deny_read_paths: list[str] | None = None) -> list[str]:
    """Static secret-store denies plus custom config dir plus caller
    paths (e.g. neighboring Lane Worktrees) — no agent tool may read the CLI's
    OAuth credentials or foreign Lanes. Every path in BOTH forms:
    glob (file tools) and plain directory (Linux sandbox)."""
    rules = list(SECRET_STORE_DENY_RULES)
    config_dir = _config_dir()
    if config_dir:
        # Auch Write/Edit verbieten: liegt das Config-Dir im Workspace, würde
        # Write(./**) sonst das Überschreiben der Credentials erlauben.
        for tool in ("Read", "Write", "Edit"):
            rules.extend([f"{tool}({config_dir})", f"{tool}({config_dir}/**)"])
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        resolved = Path(codex_home).resolve()
        # Wie beim Claude-Config-Dir: auch Write/Edit verbieten, falls das
        # Codex-Home im Workspace liegt (kein Credential-/Config-Planting).
        for tool in ("Read", "Write", "Edit"):
            rules.extend([f"{tool}({resolved})", f"{tool}({resolved}/**)"])
    for path in deny_read_paths or []:
        rules.extend([f"Read({path})", f"Read({path}/**)"])
    return rules


def _require_stored_login() -> None:
    """Fail fast: agents authenticate ONLY via the stored
    Claude CLI login (credentials file). Env keys (ANTHROPIC_API_KEY,
    CLAUDE_CODE_OAUTH_TOKEN) are deliberately blanked — otherwise they would
    end up in the agents' Bash tool subprocesses. Without a stored login,
    abort clearly instead of failing cryptically at auth."""
    if _IS_MACOS:
        # macOS legt OAuth-Credentials ggf. im Keychain ab (kein File) —
        # dort der CLI die Auth überlassen statt fälschlich zu blocken.
        return
    config_dir = _config_dir()
    # Die CLI honoriert bei gesetztem CLAUDE_CONFIG_DIR NUR dieses Verzeichnis —
    # ein HOME-Fallback hier wäre ein falsches Go mit späterem Auth-Fehler.
    base = config_dir if config_dir else Path.home() / ".claude"
    if (base / ".credentials.json").is_file():
        return
    raise AgentRunError(
        "Keine gespeicherte Claude-CLI-Anmeldung gefunden (~/.claude/.credentials.json). "
        "Bitte einmalig 'claude login' ausführen — Env-API-Keys werden aus "
        "Sicherheitsgründen nicht an Agents durchgereicht."
    )


class SdkAgentRunner:
    """Executes an agent node via the Claude Agent SDK (headless)."""

    def run(
        self,
        agent: AgentSpec,
        task: str,
        cwd: Path,
        resume: str | None = None,
        deny_read_paths: list[str] | None = None,
    ) -> AgentResult:
        _require_stored_login()
        options = ClaudeAgentOptions(
            model=agent.model,
            cwd=str(cwd),
            resume=resume,
            tools=agent.tools,
            allowed_tools=agent.allowed_tools,
            permission_mode=agent.permission_mode,
            env=_env_overrides(),
            # Isolation: keine repo-kontrollierten Settings/Hooks/MCP-Server —
            # das Ziel-Repo darf die Agent-Permissions nicht aufweichen.
            setting_sources=[],
            strict_mcp_config=True,
            mcp_servers={},
            disallowed_tools=_deny_rules(deny_read_paths),
            # Keine excludedCommands: auch git bleibt sandboxed — unsandboxtes
            # git würde via Repo-Hooks aus der Lane ausbrechen können. Commits
            # macht der Orchestrator selbst (Code, hooks deaktiviert). Und kein
            # dangerouslyDisableSandbox-Schlupfloch (allowUnsandboxedCommands).
            sandbox=(
                {
                    "enabled": True,
                    "autoAllowBashIfSandboxed": True,
                    "allowUnsandboxedCommands": False,
                    # Best effort: nicht in der SDK-TypedDict, wird aber an die
                    # CLI durchgereicht — ohne bubblewrap soll der Builder hart
                    # scheitern statt still unsandboxed zu laufen.
                    "failIfUnavailable": True,
                }
                if agent.sandbox_bash
                else None
            ),
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": agent.system_append,
            },
        )
        return anyio.run(self._collect, task, options)

    @staticmethod
    async def _collect(task: str, options: ClaudeAgentOptions) -> AgentResult:
        session_id: str | None = None
        assistant_texts: list[str] = []
        final_text: str | None = None
        is_error = False
        async for message in query(prompt=task, options=options):
            found = getattr(message, "session_id", None)
            if found is None and isinstance(getattr(message, "data", None), dict):
                found = message.data.get("session_id")
            if found:
                session_id = found
            for block in getattr(message, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    assistant_texts.append(text)
            if hasattr(message, "result"):
                final_text = message.result
                is_error = bool(getattr(message, "is_error", False))
                if is_error and not final_text:
                    # SDK liefert die eigentliche Ursache ggf. strukturiert.
                    errors = getattr(message, "errors", None)
                    if errors:
                        final_text = "; ".join(str(item) for item in errors)
        if is_error:
            raise AgentRunError(f"Agent-Lauf fehlgeschlagen: {final_text or '(kein Result)'}")
        text = final_text if final_text else "\n".join(assistant_texts)
        return AgentResult(text=text, session_id=session_id)
