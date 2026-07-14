"""Mocks für --dry-run und Tests: skriptbare Agent-/Codex-Antworten, 0 Tokens."""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from adw.agents import AgentResult, AgentSpec
from adw.findings import ReviewResult


@dataclass(frozen=True)
class AgentCall:
    agent: str
    task: str
    cwd: Path
    resume: str | None
    deny_read_paths: tuple[str, ...] = ()


@dataclass
class MockAgentRunner:
    """Liefert vorab gescriptete Antworten je Agent-Name, in Reihenfolge, und
    zeichnet jeden Aufruf auf — Grundlage aller Phasen- und Dry-Run-Tests."""

    scripts: dict[str, deque[str]] = field(default_factory=lambda: defaultdict(deque))
    calls: list[AgentCall] = field(default_factory=list)
    _session_counter: int = 0

    def script(self, agent_name: str, *responses: str) -> None:
        self.scripts[agent_name].extend(responses)

    def run(
        self,
        agent: AgentSpec,
        task: str,
        cwd: Path,
        resume: str | None = None,
        deny_read_paths: list[str] | None = None,
    ) -> AgentResult:
        self.calls.append(
            AgentCall(
                agent=agent.name,
                task=task,
                cwd=Path(cwd),
                resume=resume,
                deny_read_paths=tuple(deny_read_paths or ()),
            )
        )
        queue = self.scripts[agent.name]
        if not queue:
            raise AssertionError(
                f"Kein gescriptetes Ergebnis für Agent {agent.name!r} (Task: {task[:80]!r})"
            )
        if resume:
            session_id = resume  # Resume behält die Session — wie das echte SDK
        else:
            self._session_counter += 1
            session_id = f"mock-session-{agent.name}-{self._session_counter}"
        return AgentResult(text=queue.popleft(), session_id=session_id)


@dataclass(frozen=True)
class CodexCall:
    kind: str
    content_refs: tuple[str, ...]
    cwd: Path


@dataclass
class MockCodexRunner:
    """Skriptbare Codex-Review-Ergebnisse in Aufruf-Reihenfolge, 0 Tokens."""

    results: deque[ReviewResult] = field(default_factory=deque)
    calls: list[CodexCall] = field(default_factory=list)

    def script(self, *results: ReviewResult) -> None:
        self.results.extend(results)

    def review(self, kind: str, content_refs: list[str], cwd: Path) -> ReviewResult:
        self.calls.append(CodexCall(kind=kind, content_refs=tuple(content_refs), cwd=Path(cwd)))
        if not self.results:
            raise AssertionError(f"Kein gescriptetes Codex-Ergebnis (kind={kind!r})")
        return self.results.popleft()
