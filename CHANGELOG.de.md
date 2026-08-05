# Changelog

Alle nennenswerten Änderungen am ADW-Orchestrator werden in dieser Datei
dokumentiert. Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
Versionierung nach [SemVer](https://semver.org/lang/de/) (0.x: Minor =
Features, Patch = Doku/Fixes).

**Release-Prozess:** Jeder Push nach `main` ist ein Release — er bekommt
einen Versions-Bump in `pyproject.toml`, einen Eintrag hier und ein Git-Tag
`vX.Y.Z` (`git push && git push --tags`). Die Versionen bis 0.2.1 wurden
rückwirkend aus der Push-Historie vergeben; ihre Tags zeigen auf die
gepushten Stände.

English edition: [CHANGELOG.md](CHANGELOG.md)

## [0.4.0] — Unreleased

### Added
- **RED-Gate in der Build-Phase**: Ein Gate lässt sich in `.adw/config.yaml`
  mit `tdd: true` markieren. Eine Lane mit mindestens einem markierten Gate
  fährt ihren Initial-Build zweistufig — ein Agent-Lauf mit der Anweisung,
  nur Tests zu schreiben („schreibe NUR die Tests, keinen Produktivcode"),
  danach führt der
  Orchestrator selbst genau die markierten Gates aus. Mindestens eines rot
  ist der RED-Beweis (`red_confirmed` plus die Test-Pfade im Lane-State);
  die Implementierung macht in **derselben Session** mit dem gekürzten roten
  Gate-Output weiter und mündet in den bestehenden Gate-Loop. Alle
  markierten Gates grün nach dem Test-Lauf eskaliert statt zu schleifen: Die
  Tests decken das geforderte Verhalten nicht ab oder es existiert bereits.
- Fälschungsschutz um den Beweis: Ein Test-Lauf, der Dateien löscht oder den
  Worktree unverändert lässt, eskaliert; grüne Gates zählen nur, solange die
  Tests, die RED bewiesen haben, noch da sind.
- Dry-Run deckt beide Pfade mit 0 Tokens ab — die Default-Config (ohne
  `tdd`-Gate) bleibt einstufig, eine Config mit `tdd`-Gate fährt den
  kompletten RED-Pfad über die CLI.

### Changed
- Der RED-Check verbraucht keine Gate-Iteration; alle Limits und der
  Circuit-Breaker bleiben unverändert. Fix-Dispatches aus den Review-/
  E2E-Phasen (`pending_task` gesetzt) und Lanes ohne markiertes Gate
  verhalten sich exakt wie bisher. `red_confirmed` überlebt Crash + Resume:
  Ab dem gecheckpointeten Test-Lauf wiederholt ein Crash vor dem RED-Check
  nur noch den Check.
- Doku (SPEC, User-Handbuch, Kontrollfluss-Handbuch, technische Spec, EN+DE
  inkl. HTML/DOCX-Exporte) beschreibt die RED-Stufe.

## [0.3.0] — 2026-08-03

### Added
- **Dual-Authoring mit Best-of-Synthese** für Spec- und Plan-Phase:
  Claude Opus (`spec_agent`/`plan_agent`) und Codex (`CodexRunner.author()`,
  read-only-Sandbox, Marker-Block-Output mit Nonce je Aufruf) schreiben zwei
  unabhängige Entwürfe **parallel** nach `.adw/runs/<id>/drafts/`; ein
  Fable-Synthese-Agent (`spec_synthesis`/`plan_synthesis`) mergt sie zum
  Best-of-Artefakt und schreibt zusätzlich eine Gate-Zusammenfassung
  (`spec-summary.md`/`plan-summary.md`), die archiviert und an den
  Freigabe-Gates vorgelegt wird. Die Synthese ist der erste Lauf des
  bestehenden Codex-Review-Loops — Policy v2, Runden-Cap, Circuit-Breaker
  und Crash-Resume unverändert.
- Codex-Entwurfsfehler **degradieren** statt zu eskalieren: Warnung +
  `<kind>.codex.FAILED`-Marker, die Synthese arbeitet einquellig weiter; ein
  fehlender Claude-Entwurf eskaliert weiterhin. Die Draft-Stage ist über
  Dateien idempotent (ein Resume wiederholt keinen fertigen Autor).
- Dry-Run deckt den neuen Kontrollfluss komplett ab (unterscheidbare
  Draft-Fixtures je Autor, Drafts + Summaries im Run-Ordner, 0 Tokens).
- Dieses Changelog, inkl. rückwirkender Versionen für alle gepushten Stände.

### Changed
- Entwurfs-Autoren von Fable auf Opus umgestellt; die geteilten
  Authoring-Inhaltsregeln liegen nur noch an einer Stelle (`adw/agents.py`)
  und werden von den Codex-Autoren-Prompts importiert — kein Drift zwischen
  den Maßstäben der beiden Autoren.
- Protocol `CodexReviewer` in `CodexClient` umbenannt (review + author).
- Doku (SPEC, User-Handbuch, Kontrollfluss-Handbuch, technische Spec, EN+DE
  inkl. HTML/DOCX-Exporte) auf den Dual-Authoring-Ablauf nachgezogen.

## [0.2.1] — 2026-07-30

### Changed
- HTML- und DOCX-Exporte der Handbücher/Spec auf Review-Loop-Policy v2
  nachgezogen.

## [0.2.0] — 2026-07-30

### Added
- **Review-Loop-Policy v2**: absteigende Severity-Schwelle je Runde (R1
  alles, R2 P1+P2, ab R3 nur P1), Findings-Gedächtnis mit Dispositionen ab
  Runde 2 als Codex-Kontext, hartes Cap von 5 Runden, verbleibende Findings
  als Known Limitations dokumentiert.
- Authoring-Härtung: Proportionalitäts-Gegenkraft in den Authoring-Prompts
  (A1–A3), Runden-Cap im Authoring-Loop, `--spec-approval`-Gate (Stopp nach
  Spec, vor Plan), Issue-Text als Review-Referenz `.adw/issue.md` (B1–B3).
- Prozess-Anforderungen (Commit-Messages, Branch-Topologie, Git-Historie)
  sind aus Specs verbannt; reine P3-Untätigkeit eines Fix-Laufs wird in den
  Follow-up-Report vertagt statt zu eskalieren (A4).

## [0.1.8] — 2026-07-21

### Changed
- Prompts und Docstrings durchgängig Englisch (Kommentare bleiben Deutsch).

## [0.1.7] — 2026-07-18

### Changed
- Zweisprachige Doku, Teil 3: restliche Dokumente in EN- + DE-Fassung
  aufgeteilt.

## [0.1.6] — 2026-07-18

### Changed
- Zweisprachige Doku, Teil 2 (Handbücher, technische Spec).

## [0.1.5] — 2026-07-18

### Changed
- Zweisprachige Doku, Teil 1 (README, SPEC).

## [0.1.4] — 2026-07-18

### Fixed
- Triage verliert keine Findings mehr: Lane-Labels werden tolerant
  behandelt.

## [0.1.3] — 2026-07-18

### Added
- Kontrollfluss-Handbuch; DOCX-/MD-Exporte der Doku.

## [0.1.2] — 2026-07-15

### Added
- MIT-Lizenz.

## [0.1.1] — 2026-07-15

### Changed
- README verweist auf den Claude-Skill (eigenes Repo
  `agentic-developer-workflow-skill`).

## [0.1.0] — 2026-07-15

Erstes Release.

### Added
- 7-Phasen-Orchestrator: Spec → Plan+Kontrakt → Build-Lanes →
  Integration/E2E → Codex-Code-Review → finaler Review → Push/CI.
  Kontrollfluss ist deterministischer Code; Agenten liefern nur
  Urteilsvermögen.
- `adw`-CLI mit `run`/`resume`/`approve`/`status`, Plan-Freigabe-Gate,
  resumefähigem State (atomare Persistenz, Repo-Lock, Crash-Checkpoints)
  und tokenfreiem `--dry-run`-Modus.
- Gehärteter Claude-Agent-SDK-Runner (Env-Whitelist, Secret-Store-Denies,
  sandboxtes Bash, artefakt-exakte Schreibrechte) und Codex-Reviewer als
  isolierter read-only-Subprocess mit striktem Findings-Parsing.
- Lane-Worktrees mit deterministischen Ports, Gate-Runner mit Timeouts und
  Prozessgruppen-Cleanup, Triage-Regeln, Iterations-Limits,
  Circuit-Breaker.
- GitLab- (glab) und GitHub-Support (gh) für Issues und CI-Monitoring.
- README, User-Handbuch, technische Spezifikation (HTML-Handouts),
  Beispiel-Config; ADW als Claude-Skill paketiert (in eigenes Repo
  ausgelagert).

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
