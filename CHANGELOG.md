# Changelog

All notable changes to the ADW orchestrator are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/) (0.x: minor = features,
patch = docs/fixes).

**Release process:** every push to `main` is a release — it gets a version
bump in `pyproject.toml`, an entry here, and a git tag `vX.Y.Z`
(`git push && git push --tags`). Versions up to 0.2.1 were assigned
retroactively from the push history; their tags point to the pushed states.

Deutsche Fassung: [CHANGELOG.de.md](CHANGELOG.de.md)

## [0.4.0] — 2026-08-07

### Added
- **RED gate in the build phase**: a Gate can be marked `tdd: true` in
  `.adw/config.yaml`. A Lane with at least one marked Gate runs its initial
  build in two stages — an agent pass instructed to write only tests ("write
  ONLY the tests, no production code"), then the orchestrator itself runs
  exactly the marked Gates. At least one red is the RED proof (`red_confirmed` plus the test
  paths persisted in the Lane state); the implementation continues in the
  **same session** with the shortened red Gate output and flows into the
  existing Gate loop. All marked Gates green after the test-only pass
  escalates instead of looping: the tests do not cover the required
  behavior, or it already exists.
- Forgery protection around the proof: a test-only pass that deletes files
  or leaves the Worktree untouched escalates, and green Gates count only
  while the tests that proved RED are still in place.
- Dry run covers both paths at 0 tokens — the default config (no `tdd`
  Gate) stays single-stage, a `tdd` Gate walks the full RED path through
  the CLI.

### Changed
- The RED check consumes no Gate iteration; all limits and the circuit
  breaker are unchanged. Fix dispatches from the review/E2E phases
  (`pending_task` set) and Lanes without a marked Gate behave exactly as
  before. `red_confirmed` survives crash + resume: once the test pass is
  checkpointed, a crash before the RED check repeats only the check.
- Docs (SPEC, user handbook, control-flow handbook, technical spec, EN+DE
  incl. HTML/DOCX exports) describe the RED stage.

## [0.3.0] — 2026-08-03

### Added
- **Dual authoring with best-of synthesis** for the spec and plan phases:
  Claude Opus (`spec_agent`/`plan_agent`) and Codex (`CodexRunner.author()`,
  read-only sandbox, marker-block output with per-call nonce) write two
  independent drafts **in parallel** to `.adw/runs/<id>/drafts/`; a Fable
  synthesis agent (`spec_synthesis`/`plan_synthesis`) merges them into the
  best-of artifact and additionally writes a gate summary
  (`spec-summary.md`/`plan-summary.md`) that is archived and shown at the
  approval gates. The synthesis is the first run of the existing Codex
  review loop — policy v2, round cap, circuit breaker and crash resume are
  unchanged.
- Codex draft failures **degrade** instead of escalating: warning +
  `<kind>.codex.FAILED` marker, the synthesis proceeds single-source; a
  missing Claude draft still escalates. The draft stage is idempotent over
  files (a resume never re-runs a finished author).
- Dry run covers the new control flow completely (distinct draft fixtures
  per author, drafts + summaries in the run folder, 0 tokens).
- This changelog, including retroactive versions for all pushed states.

### Changed
- Draft authors moved from Fable to Opus; the shared authoring content
  rules now live in one place (`adw/agents.py`) and are imported by the
  Codex author prompts — no drift between the two authors' standards.
- `CodexReviewer` protocol renamed to `CodexClient` (review + author).
- Docs (SPEC, user handbook, control-flow handbook, technical spec, EN+DE
  incl. HTML/DOCX exports) updated to the dual-authoring flow.

## [0.2.1] — 2026-07-30

### Changed
- HTML and DOCX exports of the handbooks/spec updated to review-loop
  policy v2.

## [0.2.0] — 2026-07-30

### Added
- **Review-loop policy v2**: descending severity floor per round (R1 all,
  R2 P1+P2, R3+ P1 only), findings memory with dispositions passed back to
  Codex from round 2 on, hard cap of 5 rounds, remaining findings recorded
  as known limitations.
- Authoring hardening: proportionality counterweight in the authoring
  prompts (A1–A3), round cap in the authoring loop, `--spec-approval` gate
  (stop after spec, before plan), issue text as review reference
  `.adw/issue.md` (B1–B3).
- Process requirements (commit messages, branch topology, git history) are
  banned from specs; pure-P3 idle fix runs are deferred to the follow-up
  report instead of escalating (A4).

## [0.1.8] — 2026-07-21

### Changed
- Prompts and docstrings consistently English (comments stay German).

## [0.1.7] — 2026-07-18

### Changed
- Bilingual documentation, part 3: remaining docs split into EN + DE
  editions.

## [0.1.6] — 2026-07-18

### Changed
- Bilingual documentation, part 2 (handbooks, technical spec).

## [0.1.5] — 2026-07-18

### Changed
- Bilingual documentation, part 1 (README, SPEC).

## [0.1.4] — 2026-07-18

### Fixed
- Triage no longer loses findings: lane labels are treated tolerantly.

## [0.1.3] — 2026-07-18

### Added
- Control-flow handbook; DOCX/MD exports of the documentation.

## [0.1.2] — 2026-07-15

### Added
- MIT license.

## [0.1.1] — 2026-07-15

### Changed
- README points to the Claude skill (separate repo
  `agentic-developer-workflow-skill`).

## [0.1.0] — 2026-07-15

Initial release.

### Added
- 7-phase orchestrator: spec → plan+contract → build lanes → integration/E2E
  → Codex code review → final review → push/CI. Control flow is
  deterministic code; agents provide judgment only.
- `adw` CLI with `run`/`resume`/`approve`/`status`, plan-approval gate,
  resumable state (atomic persistence, repo lock, crash checkpoints) and a
  token-free `--dry-run` mode.
- Hardened Claude Agent SDK runner (env whitelist, secret-store denies,
  sandboxed bash, artifact-exact write rules) and Codex reviewer as an
  isolated read-only subprocess with strict findings parsing.
- Lane worktrees with deterministic ports, gate runner with timeouts and
  process-group cleanup, triage rules, iteration limits, circuit breakers.
- GitLab (glab) and GitHub (gh) support for issues and CI monitoring.
- README, user handbook, technical spec (HTML handouts), example config;
  ADW packaged as a Claude skill (extracted to its own repo).

[0.4.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.8...v0.2.0
[0.1.8]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sostrowsk/agentic-developer-workflow/releases/tag/v0.1.0
