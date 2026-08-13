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

## [0.5.1] — 2026-08-14

### Behoben
- `ONBOARDING.md` wird von git ignoriert. Die Datei ist das Sitzungs-Handover
  des `offboarding`-Skills und war nie committet; seit die Arbeitsbaum-Prüfung
  aus 0.5.0 scharf ist, zählte sie als fremde uncommittete Datei und
  verweigerte jedes `adw run`, `adw resume` und `adw approve`.
- Release-Datum von 0.5.0 in dieser Datei korrigiert (Release war am
  2026-08-14, nicht am 2026-08-12).

## [0.5.0] — 2026-08-14

Nachhol-Release: Die GUI-Arbeit (Läufe 1–5b) ist über mehrere Pushes nach
`main` gelangt, ohne eigene Versions-Bumps. Dieser Eintrag deckt alles seit
0.4.0 ab.

### Hinzugefügt
- **ADW Run Inspector (`adw gui`)** — eine read-only Web-Ansicht eines Laufs.
  Bindet ausschließlich an Loopback, sofern nicht `--i-know` gesetzt ist;
  `--repo` macht Repos über die Registry hinaus verfügbar, `--port`
  (Default 8765) und `--open` steuern die lokale Adresse. Der Web-Stack ist ein
  optionales Extra (`pip install adw[gui]`) und bleibt aus den Kern-
  Dependencies heraus: eine reine `adw run`-Installation importiert ihn nie.
  - Run-Liste und Run-Detail mit den Reitern **Trace**, **Timeline**,
    **Artifacts** und **Raw**; das Detail-Pane zeigt Prompt, Answer, Tools und
    Diff zum ausgewählten Knoten.
  - Diff-Endpunkt mit expliziter Ref-Allowlist.
  - Live-Tail über Server-Sent Events, solange ein Lauf läuft.
- **Ereignis-Log** (`adw/events.py`): Der Orchestrator hängt seine
  Lauf-Ereignisse als JSON Lines an `.adw/runs/<id>/events.jsonl`. Der Emitter
  ist **fail-open** — kein emitter-interner Fehler (Platte voll, Rechte, nicht
  serialisierbare Payload) erreicht je den Aufrufer oder bricht einen Lauf ab;
  `state.json` bleibt die Resume-Autorität.
- **Git-Snapshots** (`adw/snapshots.py`): Der Baum vor und nach jedem
  Agent-Lauf wird unter `refs/adw/<run>/<seq>` festgehalten — erst das macht
  den Diff pro Knoten in der GUI möglich, ohne Arbeitskopien vorzuhalten.
- **Orchestrator-Instrumentierung**: Spans an den Aufrufstellen für Run,
  Phase, Runde, Agent-Lauf, Tool-Nutzung, Gate und Codex-Schritte — für Mock
  und echten Runner gleichermaßen, sodass auch ein Dry-Run dieselbe
  Trace-Form erzeugt.
- **`codex.timeout`** als optionaler Key in `.adw/config.yaml` (ganzzahlige
  Sekunden, > 0, Default 900). Er gilt für die `codex exec`-Subprozesse; ohne
  den Key bleibt das effektive Limit unverändert. Ungültige Werte werden als
  `ConfigError` abgelehnt, bevor der Lauf startet.
- **Arbeitsbaum-Prüfung vor `adw run`, `adw resume` und `adw approve`**:
  Betreffen die uncommitteten Änderungen ausschließlich ADWs eigene sechs
  Authoring-Artefakte (`.adw/issue.md`, `spec.md`, `plan.md`,
  `contract.yaml`, `spec-summary.md`, `plan-summary.md`), setzt ADW sie selbst
  zurück und fährt fort. Jede fremde Datei — oder eine Mischung aus fremder
  Datei und ADW-Artefakt — lässt das Kommando stattdessen verweigern, ohne
  irgendetwas zu verwerfen. Im User-Handbuch dokumentiert (EN + DE).
- Spezifikations- und Messdokumente: `docs/GUI-SPEC.md` (+ `.de.md`) und
  `docs/gui-response-time.md`.

### Behoben
- Ein fehlschlagender **Codex-Autor** im Dual-Authoring bricht den Lauf nicht
  mehr ab. Der `FAILED`-Marker wird wie bisher geschrieben, und die Phase läuft
  einquellig mit dem verbliebenen Claude-Entwurf weiter — kein Traceback, kein
  Exit 1, keine manuelle Recovery. Ein Codex-Timeout hat den Orchestrator
  zuvor zum Absturz gebracht und ein manuelles Säubern des Arbeitsbaums
  erzwungen.
- Die Arbeitsbaum-Prüfung **eskaliert keinen Lauf mehr** — weder bei `run`
  noch bei `resume`. Sie verweigert höchstens; der Run-State bleibt unverändert
  und resumierbar. Zuvor eskalierte eine von ADWs eigenem Crash
  hinterlassene dirty `.adw/spec.md` den Lauf beim Resume dauerhaft und
  machte ihn unwiederbringlich verloren.
- Ein **partieller Synthese-Ausfall** (ein Pflicht-Artefakt fehlt oder ist
  leer) wird jetzt durch genau einen Retry desselben Schritts über die
  bestehende Session repariert, unter Nennung des fehlenden Artefakts. Erst
  wenn auch der Retry scheitert, eskaliert der Lauf. Zuvor tötete eine
  geschriebene `spec.md` plus fehlende `spec-summary.md` die ganze Phase.
- Die **Agent-Session-ID wird persistiert, sobald sie im Message-Stream
  erscheint**, statt erst nach Abschluss des Laufs. Ein Abbruch mitten im Lauf
  hinterlässt sie damit im State, und `adw resume` knüpft an die begonnene
  Session an, statt sie neu zu starten und die bereits verbrauchten Tokens zu
  verlieren.
- `test_measurement_guide_document_is_present_and_complete` prüfte nur den
  ersten Kandidaten, sodass jede unbeteiligte Markdown-Datei, die die beiden
  Messnamen nennt, den Test kippen konnte; jetzt muss *irgendein* Dokument
  vollständig sein.

## [0.4.0] — 2026-08-07

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

[0.5.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.4.0...v0.5.0
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
