"""Mocks for --dry-run and tests: scriptable agent/Codex responses, 0 tokens."""

from collections import defaultdict, deque
from collections.abc import Callable
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
    """Delivers pre-scripted responses per agent name, in order, and
    records every call — the foundation of all phase and dry-run tests."""

    scripts: dict[str, deque[str]] = field(default_factory=lambda: defaultdict(deque))
    calls: list[AgentCall] = field(default_factory=list)
    # Simulierte Datei-Outputs je Agent: werden bei jedem run() relativ zum
    # cwd geschrieben — so entstehen .adw/spec.md & Co. auch im Dry-Run.
    # Statt eines dicts ist auch ein Callable (cwd -> dict) erlaubt, wenn der
    # Output vom Arbeitsverzeichnis abhängt (z. B. je Lane verschieden).
    file_writes: dict[str, dict[str, str] | Callable[[Path], dict[str, str]]] = field(
        default_factory=dict
    )
    _session_counter: int = 0

    def script(self, agent_name: str, *responses: str) -> None:
        self.scripts[agent_name].extend(responses)

    def script_files(self, agent_name: str, files: dict[str, str]) -> None:
        self.file_writes[agent_name] = files

    def run(
        self,
        agent: AgentSpec,
        task: str,
        cwd: Path,
        resume: str | None = None,
        deny_read_paths: list[str] | None = None,
        emitter=None,
    ) -> AgentResult:
        # ``emitter`` is accepted for protocol parity (the orchestrator passes
        # the run's emitter through); the mock writes no event log itself.
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
        files = self.file_writes.get(agent.name, {})
        if callable(files):
            files = files(Path(cwd))
        for relpath, content in files.items():
            target = Path(cwd) / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
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
    context: str | None = None


@dataclass(frozen=True)
class CodexAuthorCall:
    kind: str
    task: str
    cwd: Path


@dataclass
class MockCodexRunner:
    """Scriptable Codex review results and authored artifacts in call order, 0 tokens."""

    results: deque[ReviewResult] = field(default_factory=deque)
    calls: list[CodexCall] = field(default_factory=list)
    # Authoring liefert Datei-Inhalte (Name -> Inhalt) statt eines Verdicts —
    # je kind eine eigene Queue, damit Spec- und Plan-Aufrufe sich nicht
    # gegenseitig die Reihenfolge verschieben. Eine gescriptete Exception in
    # der Queue wird geworfen statt zurückgegeben (Degradations-Tests).
    artifacts: dict[str, deque[dict[str, str] | Exception]] = field(
        default_factory=lambda: defaultdict(deque)
    )
    author_calls: list[CodexAuthorCall] = field(default_factory=list)

    def script(self, *results: ReviewResult) -> None:
        self.results.extend(results)

    def script_artifacts(self, kind: str, *artifacts: dict[str, str]) -> None:
        self.artifacts[kind].extend(artifacts)

    def script_author_error(self, kind: str, *errors: Exception) -> None:
        self.artifacts[kind].extend(errors)

    def author(self, kind: str, task: str, cwd: Path) -> dict[str, str]:
        self.author_calls.append(CodexAuthorCall(kind=kind, task=task, cwd=Path(cwd)))
        queue = self.artifacts[kind]
        if not queue:
            raise AssertionError(f"Keine gescripteten Codex-Artefakte (kind={kind!r})")
        scripted = queue.popleft()
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    def review(
        self,
        kind: str,
        content_refs: list[str],
        cwd: Path,
        context: str | None = None,
        emitter=None,
    ) -> ReviewResult:
        self.calls.append(
            CodexCall(kind=kind, content_refs=tuple(content_refs), cwd=Path(cwd), context=context)
        )
        if not self.results:
            raise AssertionError(f"Kein gescriptetes Codex-Ergebnis (kind={kind!r})")
        return self.results.popleft()
