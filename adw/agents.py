"""Agent registry (SPEC §3) and SDK runner (Claude Agent SDK)."""

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import anyio
from claude_agent_sdk import (
    ClaudeAgentOptions,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)

from adw.env import safe_env
from adw.events import NoOpEmitter, SpanHandle

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
SPEC_SUMMARY_WRITE_RULES = ["Write(.adw/spec-summary.md)", "Edit(.adw/spec-summary.md)"]
PLAN_SUMMARY_WRITE_RULES = ["Write(.adw/plan-summary.md)", "Edit(.adw/plan-summary.md)"]
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


# Inhaltliche Vorgaben, die Entwurfs-Autor UND Synthese teilen — driften sie
# auseinander, kippt die Synthese die Scope-Gegenkraft des Autors wieder um.
_SPEC_CONTENT_RULES = (
    "Proportionality is binding: every acceptance criterion must trace "
    "back to the issue or to a concrete failure with real damage; prefer "
    "existing backstops (TTLs, webhooks, logs) over new mechanisms or "
    "persistent states. Honor explicit non-goals and scope ceilings in "
    "the issue. Put defensible-but-disproportionate hardening ideas into "
    "a 'Deferred (deliberately not built)' section instead of acceptance "
    "criteria. The spec describes PRODUCT behavior only — observable "
    "behavior, data states, interfaces. It never normalizes the "
    "development process: no requirements on commit messages, commit "
    "prefixes, branch topology, commit order or git history, neither in "
    "acceptance criteria nor in the Definition of Done. Implementation "
    "agents do not commit — the orchestrator commits for them, so such "
    "criteria are structurally unsatisfiable and unverifiable. This "
    "concerns the WORKFLOW's own process; git behavior that the product "
    "under change itself produces (e.g. commits written by the code) is "
    "observable product behavior and belongs in the spec. TDD is "
    "the agents' way of working and needs no acceptance criterion. "
    "You never implement and never change production code."
)
_PLAN_CONTENT_RULES = (
    "Both lanes will later build strictly against the contract. "
    "Keep the contract minimal: with a single Workstream it pins only "
    "externally observable surfaces (HTTP routes, events, template "
    "behavior), never internal helper signatures. Do not add plan steps "
    "or tests beyond the spec's scope; carry the spec's 'Deferred' "
    "section through unchanged. You never implement."
)

# Merge- und Summary-Vorgaben teilen sich beide Synthese-Agents: eine
# Zusammenfassung, die je Phase anders aufgebaut ist, wäre am Freigabe-Gate
# nur Rauschen.
_BEST_OF_MERGE_RULES = (
    "Two independent drafts exist: one written by a Claude agent, one by "
    "Codex. You read the issue and both drafts and merge them into ONE "
    "best-of artifact — per section the stronger formulation wins, and a "
    "point that only one draft saw is kept when it holds up against the "
    "issue. Never merge by union: everything you carry over must earn its "
    "place, contradictions are resolved in favor of the issue. If a draft "
    "is missing because its author failed, work from the remaining one and "
    "state that single-source basis in the summary."
)
_SUMMARY_FORMAT_RULES = (
    "It is short (at most 30 lines as a guideline) and written in the "
    "language of the issue text, not automatically English. Structure: "
    "what and why (2-3 sentences), key decisions, scope boundaries and "
    "deferred items, provenance (what came from which draft and where the "
    "drafts disagreed), open questions. It summarizes for a human "
    "approving the artifact — it never repeats the artifact in full."
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


class RunTotals:
    """Process-wide accumulator of agent cost/tokens for the run-end ``totals``.

    Thread-safe: parallel Lane build agents update it concurrently. A dry run
    uses the mock runner, which never calls ``add``, so its totals stay 0/0."""

    def __init__(self) -> None:
        self.cost_usd: float = 0.0
        self.tokens: int = 0
        self._lock = threading.Lock()

    def add(self, cost_usd: float | None, tokens: int | None) -> None:
        with self._lock:
            self.cost_usd += cost_usd or 0.0
            self.tokens += tokens or 0


def agent_run_start_payload(agent: AgentSpec, task: str, cwd, resume: str | None) -> dict:
    """The ``agent.run`` span start payload (GUI-SPEC §4.4) — built from values
    known at the call site, so the span can live in ``phases.py`` (mock and real
    runner alike) instead of in the runner."""
    return {
        "agent": agent.name,
        "model": agent.model,
        "tools": list(agent.tools),
        "allowed_tools": list(agent.allowed_tools),
        "cwd": str(cwd),
        "resume_session": resume,
        "prompt": task,
        "system_append": agent.system_append,
    }


class AgentRunner(Protocol):
    """One method, few parameters — no node needs more interface than that."""

    def run(
        self,
        agent: AgentSpec,
        task: str,
        cwd: Path,
        resume: str | None = None,
        deny_read_paths: list[str] | None = None,
        emitter=None,
        span=None,
        on_session_id=None,
    ) -> AgentResult: ...


REGISTRY: dict[str, AgentSpec] = {
    "spec_agent": AgentSpec(
        name="spec_agent",
        model=OPUS,
        tools=WRITER_TOOLS,
        allowed_tools=[*SCOPED_READ_RULES, *SPEC_WRITE_RULES],
        system_append=(
            "You are the Spec agent of an Agentic Developer Workflow. You write "
            "EXCLUSIVELY the specification following a fixed template (goal, scope, "
            "non-goals, acceptance criteria, Definition of Done) to .adw/spec.md. "
            f"{_SPEC_CONTENT_RULES}"
        ),
    ),
    "plan_agent": AgentSpec(
        name="plan_agent",
        model=OPUS,
        tools=WRITER_TOOLS,
        allowed_tools=[*SCOPED_READ_RULES, *PLAN_WRITE_RULES],
        system_append=(
            "You are the Plan agent of an Agentic Developer Workflow. From "
            ".adw/spec.md you produce the implementation plan .adw/plan.md with the "
            "Workstreams 'backend' and 'frontend' (if the lane exists) as well as the "
            "interface contract .adw/contract.yaml (OpenAPI/types/events). "
            f"{_PLAN_CONTENT_RULES}"
        ),
    ),
    "spec_synthesis": AgentSpec(
        name="spec_synthesis",
        model=FABLE,
        tools=WRITER_TOOLS,
        allowed_tools=[*SCOPED_READ_RULES, *SPEC_WRITE_RULES, *SPEC_SUMMARY_WRITE_RULES],
        system_append=(
            "You are the Spec synthesis agent of an Agentic Developer Workflow. "
            f"{_BEST_OF_MERGE_RULES} "
            "The artifact is the specification .adw/spec.md following a fixed "
            "template (goal, scope, non-goals, acceptance criteria, Definition "
            "of Done). "
            f"{_SPEC_CONTENT_RULES} "
            "In addition you write the summary .adw/spec-summary.md, which the "
            f"user reads at the approval gate. {_SUMMARY_FORMAT_RULES}"
        ),
    ),
    "plan_synthesis": AgentSpec(
        name="plan_synthesis",
        model=FABLE,
        tools=WRITER_TOOLS,
        allowed_tools=[*SCOPED_READ_RULES, *PLAN_WRITE_RULES, *PLAN_SUMMARY_WRITE_RULES],
        system_append=(
            "You are the Plan synthesis agent of an Agentic Developer Workflow. "
            f"{_BEST_OF_MERGE_RULES} "
            "The artifacts are the implementation plan .adw/plan.md with the "
            "Workstreams 'backend' and 'frontend' (if the lane exists) and the "
            "interface contract .adw/contract.yaml (OpenAPI/types/events), both "
            "derived from .adw/spec.md. "
            f"{_PLAN_CONTENT_RULES} "
            "In addition you write the summary .adw/plan-summary.md, which the "
            f"user reads at the approval gate. {_SUMMARY_FORMAT_RULES}"
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


def _mirror_block(emitter, span_id: str, block) -> None:
    """Mirror ONE SDK content block into the event log — read-only, never
    changes the collected result (AC 4). Unknown block types are ignored."""
    if isinstance(block, TextBlock):
        emitter.emit("agent.message", {"role": "assistant", "text": block.text}, span=span_id)
    elif isinstance(block, ToolUseBlock):
        emitter.emit(
            "agent.tool.call",
            {"tool": block.name, "tool_use_id": block.id, "input": block.input},
            span=span_id,
        )
    elif isinstance(block, ToolResultBlock):
        emitter.emit(
            "agent.tool.result",
            {
                "tool_use_id": block.tool_use_id,
                "is_error": block.is_error,
                "content": block.content,
            },
            span=span_id,
        )


def _map_usage(raw: dict | None) -> dict:
    """SDK token usage → the §4.4 usage shape (missing counters default to 0)."""
    raw = raw or {}
    return {
        "input": raw.get("input_tokens", 0),
        "output": raw.get("output_tokens", 0),
        "cache_read": raw.get("cache_read_input_tokens", 0),
        "cache_creation": raw.get("cache_creation_input_tokens", 0),
    }


class SdkAgentRunner:
    """Executes an agent node via the Claude Agent SDK (headless)."""

    def __init__(self, emitter=None, totals=None):
        # Optional run emitter injected by the orchestrator, so ctx.agents.run()
        # call sites need no per-call wiring; the explicit run(emitter=...) arg
        # still wins (used by unit tests). ``totals`` (a RunTotals) accumulates
        # this run's agent cost/tokens for the run-end totals.
        self._emitter = emitter
        self._totals = totals

    def run(
        self,
        agent: AgentSpec,
        task: str,
        cwd: Path,
        resume: str | None = None,
        deny_read_paths: list[str] | None = None,
        emitter=None,
        span=None,
        on_session_id=None,
    ) -> AgentResult:
        _require_stored_login()
        emitter = emitter or self._emitter or NoOpEmitter()
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
        # The agent.run SPAN is owned by the phases.py call site (GUI-SPEC §6,
        # E4): the runner never opens one, it only fills the CONTENTS (stream
        # mirroring, usage/cost) of the handle it is handed. A direct call
        # without a handle (an uninstrumented caller) collects into a detached
        # handle and mirrors to a NoOp — no orphan span or stream events.
        if span is not None:
            return self._collect_into(task, options, emitter, span, on_session_id)
        return self._collect_into(
            task, options, NoOpEmitter(), SpanHandle("detached"), on_session_id
        )

    def _collect_into(self, task, options, emitter, handle, on_session_id=None) -> AgentResult:
        # Deterministic end-payload BEFORE the run: an unexpected exception
        # unwinding the span still writes a complete end record.
        handle.end_payload = {
            "session_id": None,
            "result_text": "",
            "usage": _map_usage(None),
            "cost_usd": 0.0,
            "is_error": True,
        }
        try:
            return anyio.run(self._collect, task, options, emitter, handle, on_session_id)
        finally:
            # Accumulate the run's cost/tokens even on AgentRunError — the
            # cost was incurred; handle.end_payload is set by _collect before
            # it raises (or holds the defaults).
            if self._totals is not None:
                usage = handle.end_payload.get("usage") or {}
                self._totals.add(
                    handle.end_payload.get("cost_usd"),
                    sum(v for v in usage.values() if isinstance(v, int)),
                )

    @staticmethod
    async def _collect(
        task: str, options: ClaudeAgentOptions, emitter, handle, on_session_id=None
    ) -> AgentResult:
        span_id = handle.id
        session_id: str | None = None
        assistant_texts: list[str] = []
        final_text: str | None = None
        is_error = False
        usage_raw: dict | None = None
        cost_usd: float = 0.0
        async for message in query(prompt=task, options=options):
            found = getattr(message, "session_id", None)
            if found is None and isinstance(getattr(message, "data", None), dict):
                found = message.data.get("session_id")
            if found:
                # Checkpoint the session id the FIRST time it appears (idempotent):
                # a mid-run abort must still leave it in the state so a resume can
                # reconnect instead of restarting the agent run (F5).
                if session_id is None and on_session_id is not None:
                    on_session_id(found)
                session_id = found
            msg_usage = getattr(message, "usage", None)
            if isinstance(msg_usage, dict):
                usage_raw = msg_usage
            for block in getattr(message, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    assistant_texts.append(text)
                # Mirroring is read-only — the two lines above still compute the
                # result exactly as before (AC 4 bit-identity).
                _mirror_block(emitter, span_id, block)
            if hasattr(message, "result"):
                final_text = message.result
                is_error = bool(getattr(message, "is_error", False))
                cost = getattr(message, "total_cost_usd", None)
                if cost is not None:
                    cost_usd = cost
                if is_error and not final_text:
                    # SDK liefert die eigentliche Ursache ggf. strukturiert.
                    errors = getattr(message, "errors", None)
                    if errors:
                        final_text = "; ".join(str(item) for item in errors)
        text = final_text if final_text else "\n".join(assistant_texts)
        handle.end_payload = {
            "session_id": session_id,
            "result_text": text,
            "usage": _map_usage(usage_raw),
            "cost_usd": cost_usd,
            "is_error": is_error,
        }
        if is_error:
            raise AgentRunError(f"Agent-Lauf fehlgeschlagen: {final_text or '(kein Result)'}")
        return AgentResult(text=text, session_id=session_id)
