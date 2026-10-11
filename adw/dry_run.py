"""Dry-run wiring: canonical fixtures and mock runners (0 tokens, no network).

``adw run --dry-run`` drives the real phases with these: scripted agent and Codex
answers, a synthetic gate that only the second build run turns green (simulated
gate fail → fix task), an E2E gate turned green only by a triaged lane fix, and
simulated green CI for GitLab and GitHub.
"""

import json
import re
from pathlib import Path

from adw import ci, github
from adw.config import AdwConfig
from adw.findings import Finding, ReviewResult
from adw.mock import MockAgentRunner, MockCodexRunner

_DRY_SPEC = "# Spec (Dry-Run)\n\nZiel: Demo-Durchlauf durch alle sieben Phasen.\n"
_DRY_PLAN = "# Plan (Dry-Run)\n\n- Workstream backend\n- Workstream frontend (optional)\n"
_DRY_CONTRACT = "openapi: 3.1.0\ninfo:\n  title: Dry-Run-Kontrakt\n  version: 0.0.1\n"
# Je Autor eigene Entwurfstexte: identische Fixtures würden verdecken, wenn
# beide Draft-Dateien aus derselben Quelle stammten — der Dry-Run soll die zwei
# unabhängigen Entwürfe zeigen, aus denen die Synthese mergt.
_DRY_SPEC_DRAFT = "# Spec-Entwurf Claude (Dry-Run)\n\nZiel: Demo-Durchlauf.\n"
_DRY_PLAN_DRAFT = "# Plan-Entwurf Claude (Dry-Run)\n\n- Workstream backend\n"
_DRY_SPEC_CODEX_DRAFT = "# Spec-Entwurf Codex (Dry-Run)\n\nZiel: Demo-Durchlauf, zweite Sicht.\n"
_DRY_PLAN_CODEX_DRAFT = "# Plan-Entwurf Codex (Dry-Run)\n\n- Workstream backend (zweite Sicht)\n"
_DRY_CONTRACT_CODEX = "openapi: 3.1.0\ninfo:\n  title: Dry-Run-Kontrakt (Codex)\n  version: 0.0.1\n"
_DRY_SPEC_SUMMARY = "# Zusammenfassung der Spec (Dry-Run)\n\nBest-of beider Entwuerfe.\n"
_DRY_PLAN_SUMMARY = "# Zusammenfassung des Plans (Dry-Run)\n\nBest-of beider Entwuerfe.\n"
_OK = ReviewResult(verdict="ok", findings=[])


def config(config: AdwConfig) -> AdwConfig:
    """Canonical fixtures (PLAN task 11 / SPEC DoD 1): every Lane gets a
    synthetic Gate that only the second build run turns green (simulated
    Gate fail → fix task to the same session); the E2E Gate analogously turns
    green only through a triaged Lane fix (E2E triage path)."""
    data = config.model_dump()
    for lane in data["lanes"].values():
        lane["gates"] = list(lane["gates"]) + [
            {"name": "adw-dry-run-gate", "cmd": "test -f .adw-dry-run-fixed", "timeout": 10}
        ]
    if data.get("e2e"):
        data["e2e"]["cmd"] = "test -f .adw-dry-run-e2e-fixed"
    return AdwConfig.model_validate(data)


def runners() -> tuple[MockAgentRunner, MockCodexRunner]:
    agents = MockAgentRunner()
    # Entwurfs-Autoren der Draft-Stage …
    agents.script_files("spec_agent", {".adw/spec.md": _DRY_SPEC_DRAFT})
    agents.script_files(
        "plan_agent", {".adw/plan.md": _DRY_PLAN_DRAFT, ".adw/contract.yaml": _DRY_CONTRACT}
    )
    # … und die Synthese, die daraus Best-of-Artefakt UND Summary baut.
    agents.script_files(
        "spec_synthesis", {".adw/spec.md": _DRY_SPEC, ".adw/spec-summary.md": _DRY_SPEC_SUMMARY}
    )
    agents.script_files(
        "plan_synthesis",
        {
            ".adw/plan.md": _DRY_PLAN,
            ".adw/contract.yaml": _DRY_CONTRACT,
            ".adw/plan-summary.md": _DRY_PLAN_SUMMARY,
        },
    )

    def build_writes(cwd: Path) -> dict[str, str]:
        # Simulations-Stufe aus dem WORKTREE ableiten, nicht aus Prozess-
        # lokalen Call-Zählern: ein Resume mit frischem Mock muss den
        # checkpointeten Fix fortsetzen statt ihn zu wiederholen.
        demo = cwd / f"adw_dry_run_{cwd.name}.md"
        if not demo.exists():
            stage = 1  # Erstlauf: Demo-Datei, das synthetische Gate bleibt rot
        elif not (cwd / ".adw-dry-run-fixed").exists():
            stage = 2  # Fix des simulierten Gate-Fails
        else:
            stage = 3  # Fix des simulierten E2E-Fails (nur --parallel)
        files = {demo.name: f"Dry-Run-Artefakt der Lane {cwd.name} (Stufe {stage})\n"}
        if stage >= 2:
            files[".adw-dry-run-fixed"] = "ok\n"
        if stage >= 3:
            files[".adw-dry-run-e2e-fixed"] = "ok\n"
        return files

    agents.file_writes["build_agent"] = build_writes
    # Großzügig scripten: Restantworten in den Queues sind unschädlich,
    # eine LEERE Queue würde einen legitimen Folge-Aufruf abbrechen.
    agents.script("spec_agent", *["Spec-Entwurf geschrieben"] * 3)
    agents.script("plan_agent", *["Plan-Entwurf geschrieben"] * 3)
    agents.script("spec_synthesis", *["Spec geschrieben"] * 3)
    agents.script("plan_synthesis", *["Plan geschrieben"] * 3)
    agents.script("build_agent", *["gebaut"] * 12)
    triage = json.dumps(
        ReviewResult(
            verdict="needs_fixes",
            findings=[
                Finding(
                    severity="P2",
                    lane="backend",
                    file="adw_dry_run_backend.md",
                    issue="Simuliertes E2E-Finding",
                    remediation_plan=["Demo-Fix"],
                )
            ],
        ).model_dump()
    )
    agents.script("e2e_triage", *[triage] * 2)
    agents.script("log_analyst", *[triage] * 2)
    agents.script("final_reviewer", *[json.dumps(_OK.model_dump())] * 3)
    codex = MockCodexRunner()
    codex.script(*[_OK] * 8)  # Spec-, Plan-, Code-Reviews — alle grün
    codex.script_artifacts("spec", *[{"spec.md": _DRY_SPEC_CODEX_DRAFT} for _ in range(3)])
    codex.script_artifacts(
        "plan",
        *[
            {"plan.md": _DRY_PLAN_CODEX_DRAFT, "contract.yaml": _DRY_CONTRACT_CODEX}
            for _ in range(3)
        ],
    )
    return agents, codex


def gh(config: AdwConfig) -> github.RunGh:
    """Simulated green GitHub Actions incl. staging job — no network."""
    staging = config.ci.staging_job or "deploy-staging"

    def fake(argv: list[str], cwd: Path) -> str:
        if argv[0] == "api" and "/actions/runs?" in argv[1]:
            return json.dumps(
                {
                    "total_count": 1,
                    "workflow_runs": [{"id": 1, "status": "completed", "conclusion": "success"}],
                }
            )
        if argv[0] == "api" and "/jobs" in argv[1]:
            return json.dumps({"jobs": [{"id": 2, "name": staging, "conclusion": "success"}]})
        if argv[:2] == ["run", "view"]:
            return ""
        raise ci.CiError(f"Dry-Run: unerwarteter gh-Aufruf {argv}")

    return fake


def glab(config: AdwConfig) -> ci.RunGlab:
    """Simulated green pipeline incl. staging job — no network."""
    staging = config.ci.staging_job or "deploy-staging"

    def fake(argv: list[str], cwd: Path) -> str:
        if argv[0] == "api" and "/pipelines?" in argv[1]:
            match = re.search(r"sha=([0-9a-f]+)", argv[1])
            sha = match.group(1) if match else ""
            return json.dumps([{"id": 1, "status": "success", "sha": sha}])
        if argv[0] == "api":  # Jobs der Pipeline
            return json.dumps([{"id": 2, "name": staging, "status": "success"}])
        if argv[:2] == ["ci", "trace"]:
            return ""
        raise ci.CiError(f"Dry-Run: unerwarteter glab-Aufruf {argv}")

    return fake
