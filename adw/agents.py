"""Agent-Registry (SPEC §3) und SDK-Runner (Claude Agent SDK)."""

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
HAIKU = "claude-haiku-4-5-20251001"

_IS_MACOS = sys.platform == "darwin"

READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]
WRITER_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit"]
BUILDER_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit", "Bash"]

# Approval-Regeln immer workspace-relativ — cwd allein ist keine
# Dateisystem-Grenze; ohne passende Regel wird headless verweigert.
SCOPED_READ_RULES = ["Read(./**)", "Grep(./**)", "Glob(./**)"]
SCOPED_ADW_WRITE_RULES = ["Write(.adw/**)", "Edit(.adw/**)"]
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
SECRET_STORE_DENY_RULES = [
    rule for store in _SECRET_STORES for rule in (f"Read({store})", f"Read({store}/**)")
] + [f"Read({file})" for file in _SECRET_FILES]


class AgentRunError(Exception):
    """Der Agent-Lauf endete mit einem Fehler-Result."""


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
    """Eine Methode, wenige Parameter — mehr Interface braucht kein Node."""

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
        allowed_tools=[*SCOPED_READ_RULES, *SCOPED_ADW_WRITE_RULES],
        system_append=(
            "Du bist der Spec-Agent eines Agentic Developer Workflow. Du schreibst "
            "AUSSCHLIESSLICH die Spezifikation nach fester Vorlage (Ziel, Scope, "
            "Nicht-Ziele, Akzeptanzkriterien, Definition of Done) nach .adw/spec.md. "
            "Du implementierst nie und änderst keinen Produktivcode."
        ),
    ),
    "plan_agent": AgentSpec(
        name="plan_agent",
        model=FABLE,
        tools=WRITER_TOOLS,
        allowed_tools=[*SCOPED_READ_RULES, *SCOPED_ADW_WRITE_RULES],
        system_append=(
            "Du bist der Plan-Agent eines Agentic Developer Workflow. Du erzeugst aus "
            ".adw/spec.md den Implementierungsplan .adw/plan.md mit den Workstreams "
            "'backend' und 'frontend' (sofern die Lane existiert) sowie den "
            "Schnittstellen-Kontrakt .adw/contract.yaml (OpenAPI/Typen/Events). "
            "Beide Lanes bauen später strikt gegen den Kontrakt. Du implementierst nie."
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
            "Du bist ein Build-Agent in einer isolierten Lane (eigener Git-Worktree). "
            "Du implementierst deinen Workstream aus .adw/plan.md strikt gegen "
            ".adw/contract.yaml, mit TDD (Test zuerst, RED bestätigen, dann minimal "
            "implementieren). Fix-Pläne aus Reviews sind Empfehlungen: prüfe sie gegen "
            "Spec und Konventionen und weiche begründet ab, wenn nötig. Du committest "
            "NICHT — nach grünen Gates committet der Orchestrator deine Änderungen."
        ),
    ),
    "e2e_triage": AgentSpec(
        name="e2e_triage",
        model=HAIKU,
        tools=READ_ONLY_TOOLS,
        allowed_tools=SCOPED_READ_RULES,
        system_append=(
            "Du bist der E2E-Triage-Agent. Du ordnest jeden Playwright-Fehler einer "
            "Lane (frontend/backend) zu. Du fixt nichts. Antworte NUR mit dem "
            "Review-JSON gemäß Schema."
        ),
        permission_mode="default",
    ),
    "log_analyst": AgentSpec(
        name="log_analyst",
        model=HAIKU,
        tools=READ_ONLY_TOOLS,
        allowed_tools=SCOPED_READ_RULES,
        system_append=(
            "Du bist der Log-Analyst. Du liest CI-Logs und erzeugst strukturierte "
            "Findings mit Lane-Zuordnung. Du fixt nichts. Antworte NUR mit dem "
            "Review-JSON gemäß Schema."
        ),
        permission_mode="default",
    ),
    "final_reviewer": AgentSpec(
        name="final_reviewer",
        model=FABLE,
        tools=READ_ONLY_TOOLS,
        allowed_tools=SCOPED_READ_RULES,
        system_append=(
            "Du bist der finale Reviewer (strikt read-only). Du prüfst die "
            "Implementierung gegen .adw/spec.md. Du lieferst NUR Findings — keine "
            "Änderungen, keine Fixes. Antworte NUR mit dem Review-JSON gemäß Schema; "
            "setze bei jedem Finding die category (scope_gap | implementation | trivial)."
        ),
        permission_mode="default",
    ),
}


def _sanitized_env_overrides() -> dict[str, str]:
    """Neutralisiert alle Nicht-Whitelist-Variablen im CLI-Prozess.

    Das SDK merged os.environ mit options.env — Variablen ENTFERNEN geht
    nicht, aber per ""-Override blanken. Secrets (API-Keys, Cloud-Creds)
    erreichen so weder das Modell noch Bash-Tool-Subprozesse. Die Claude-
    Authentifizierung läuft über das Credentials-File unter HOME
    (whitelisted), nicht über Env-Keys.
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
    """CLAUDE_CONFIG_DIR als ABSOLUTER Pfad — die CLI startet mit dem
    Worktree als cwd, ein relativer Wert zeigte dort ins Leere."""
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).resolve() if raw else None


def _deny_rules(deny_read_paths: list[str] | None = None) -> list[str]:
    """Statische Secret-Store-Denies plus custom Config-Dir plus Aufrufer-
    Pfade (z. B. Nachbar-Lane-Worktrees) — die OAuth-Credentials der CLI und
    fremde Lanes darf kein Agent-Tool lesen. Jeder Pfad in BEIDEN Formen:
    Glob (File-Tools) und plain Directory (Linux-Sandbox)."""
    rules = list(SECRET_STORE_DENY_RULES)
    config_dir = _config_dir()
    if config_dir:
        # Auch Write/Edit verbieten: liegt das Config-Dir im Workspace, würde
        # Write(./**) sonst das Überschreiben der Credentials erlauben.
        for tool in ("Read", "Write", "Edit"):
            rules.extend([f"{tool}({config_dir})", f"{tool}({config_dir}/**)"])
    for path in deny_read_paths or []:
        rules.extend([f"Read({path})", f"Read({path}/**)"])
    return rules


def _require_stored_login() -> None:
    """Fail fast: Agents authentifizieren NUR über die gespeicherte
    Claude-CLI-Anmeldung (Credentials-File). Env-Keys (ANTHROPIC_API_KEY,
    CLAUDE_CODE_OAUTH_TOKEN) werden bewusst geblankt — sie würden sonst in
    Bash-Tool-Subprozessen der Agents landen. Ohne stored login klar
    abbrechen statt kryptisch an der Auth zu scheitern."""
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
    """Führt einen Agent-Node über das Claude Agent SDK aus (headless)."""

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
