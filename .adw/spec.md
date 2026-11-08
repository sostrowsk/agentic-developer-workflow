# Spec — W1: Jeder Agent-Lauf hat eine Grenze, jeder Abbruch eine Ursache

Basis: Stand 0.31.3 (main, `4a7f958`). Ausgangslage am 2026-11-08 gegen den Code
geprüft: `AgentSpec` (`adw/agents.py:146`) ohne Grenzfelder; `SdkAgentRunner.run`
baut `ClaudeAgentOptions` ohne `max_turns`; `_collect` (`adw/agents.py:587`)
iteriert `query()` ohne Zeitlimit und ohne Ausnahmebehandlung; das
`agent.run`-End-Payload (`:630–636`) hat kein Ursachenfeld; `agent_run_start_payload`
(`:183`) kennt keine Grenzwerte; `run_codex_review_phase` (`adw/phases.py:~345`)
fängt nur `FindingsParseError`/`ValidationError`; `context._git`
(`adw/context.py:359`) prüft nur den Returncode; die CLI (`adw/cli.py:805–823`)
behandelt nur `EscalationError` und `AgentRunError`. `REGISTRY` enthält acht
Agenten: `spec_agent`, `plan_agent`, `spec_synthesis`, `plan_synthesis`,
`build_agent`, `e2e_triage`, `log_analyst`, `final_reviewer`.

## Ziel

Kein Claude-Agent-Lauf kann den ADW-Lauf mehr unbegrenzt blockieren: jeder Agent
hat eine feste Turn- und Zeitgrenze. Jeder Abbruch — Grenze erreicht, SDK-Fehler,
`CodexError` im Code-Review, git-Timeout — endet mit einer benannten Ursache und
einer klaren CLI-Meldung (Resume-Hinweis bzw. Eskalation) statt mit einem rohen
Traceback. Transiente Abbrüche bleiben über `adw resume` fortsetzbar.

## Scope

1. **Grenzen je Agent (A1, E1).** `AgentSpec` erhält die Pflichtfelder
   `max_turns: int` und `timeout_s: int` ohne Default. Jeder `REGISTRY`-Eintrag
   setzt beide ausdrücklich:

   | Agent | `max_turns` | `timeout_s` |
   |---|---:|---:|
   | `build_agent` | 400 | 3600 |
   | `final_reviewer` | 250 | 1200 |
   | `spec_agent`, `plan_agent` | 150 | 1200 |
   | `spec_synthesis`, `plan_synthesis` | 100 | 1200 |
   | `e2e_triage`, `log_analyst` | 50 | 600 |

   Die Werte sind Konstanten in `REGISTRY`, keine Felder in `.adw/config.yaml`.
   `max_turns` wird an `ClaudeAgentOptions` übergeben. `timeout_s` begrenzt das
   Einsammeln des `query()`-Streams per `anyio.fail_after(timeout_s)` um die
   Iteration in `_collect` (E2). Der Modell-Check (`max_turns=1`) bleibt
   unverändert.

2. **Benannte Ursache (A2, E2, E3, E7).**
   - Zeitlimit überschritten → `AgentRunError` mit Meldung
     `"<agent>: Zeitlimit <timeout_s> s überschritten"`.
   - Result `is_error=True` mit Subtype `error_max_turns` → `AgentRunError` mit
     Meldung `"<agent>: max_turns <n> erreicht"`. Keine Eskalation; der Nutzer
     entscheidet über den Resume.
   - Das `agent.run`-End-Event wird in beiden Fällen geschrieben, hat
     `is_error: true` und trägt die Ursache als zusätzliches Feld `error`
     (Text gleich der `AgentRunError`-Meldung). Die bestehenden End-Payload-Felder
     (`session_id`, `result_text`, `usage`, `cost_usd`, `is_error`) bleiben.
   - Eine vor dem Abbruch gemeldete Session-ID ist über den bestehenden
     `on_session_id`-Checkpoint persistiert; `adw resume` setzt dieselbe Session
     fort. Die Kosten-Aufsummierung im `finally` von `_collect_into` greift
     weiterhin, auch bei Timeout.
   - Die Meldungen werden an einem gemeinsamen Ort erzeugt; keine neue
     Ausnahmeklasse.

3. **C26 — keine Tracebacks (A3).**
   - **SDK (E4):** Jede Ausnahme aus `query()` in `_collect`, die nicht schon
     `AgentRunError` ist, wird zu `AgentRunError` mit Typname und Text der
     Ursache (resumierbar). Ein bereits vorhandener `AgentRunError` behält seine
     Meldung. `KeyboardInterrupt`/`SystemExit` bleiben unberührt.
   - **Codex im Code-Review (E5):** `CodexError` in der Codex-Code-Review-Phase
     (einschließlich Codex-Timeout) wird zu `AgentRunError`. Die CLI gibt den
     bestehenden Resume-Hinweis aus, Exit 1; der Lauf bleibt am Checkpoint und
     wird nicht `escalated`. Die Behandlung in den Authoring-Phasen bleibt.
   - **git (E6):** `subprocess.TimeoutExpired` und `OSError` in `context._git`
     führen auf denselben Pfad wie Returncode ≠ 0: `escalate` mit Kommando und
     Ursache → `escalation.md`, CLI-Meldung „Eskalation: …", Exit 1.

4. **Sichtbarkeit (A4).** `agent_run_start_payload` enthält zusätzlich die
   Integer-Felder `max_turns` und `timeout_s` des Agents. Da der Payload am
   Call-Site gebaut wird, gilt das für echten und Mock-Runner gleichermaßen;
   bestehende Felder bleiben erhalten.

5. **Changelog (A5).** `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue
   Sektion `## [0.32.0] — Unreleased` mit `### Added` (Grenzen je Agent,
   benannte Ursache, Start-Payload-Felder) und `### Fixed` (C26: drei
   Fehlerarten ohne Traceback), im Stil der vorhandenen Einträge. Version in
   `pyproject.toml` bleibt `0.31.3`.

## Non-Goals

- Grenzen in `.adw/config.yaml` konfigurierbar machen.
- Budget-Grenzen in USD (`max_budget_usd`).
- Automatischer Retry nach Timeout oder `max_turns`.
- Effort-Feld pro Agent (Lauf W2).
- Änderungen an Codex-Timeout (900 s), Gate-Timeouts, CI-Timeout.
- Änderungen an der Semantik von `AgentRunError` (transient, resumierbar) und
  `EscalationError` (endgültig).
- Änderungen am Session-Checkpoint-Verfahren oder an der Kosten-Aufsummierung.
- Änderungen an der `CodexError`-Behandlung in den Authoring-Phasen.
- Neue Ausnahmeklassen; Eskalation bei `max_turns` oder Zeitlimit.
- Fehlerbehandlung für weitere, im Issue nicht genannte Subsysteme; insbesondere
  keine Änderungen an den `_git`-Helfern in `worktrees.py` und `retention.py` —
  nur `context._git` ist betroffen.
- Neue persistente Zustände oder zusätzliche Überwachungsmechanismen.
- Versionsänderung in `pyproject.toml`.

## Contract (Single-Lane, nur extern beobachtbare Flächen)

- **`agent.run`-Start-Payload:** zusätzliche Felder `max_turns` (int) und
  `timeout_s` (int); bestehende Felder unverändert.
- **`agent.run`-End-Payload bei Grenz-/Ursachen-Abbruch:** `is_error: true`,
  zusätzliches Feld `error` (str) mit der Ursachenmeldung.
- **Meldungstexte:** `"<agent>: Zeitlimit <timeout_s> s überschritten"`,
  `"<agent>: max_turns <n> erreicht"`.
- **CLI bei `AgentRunError`** (Zeitlimit, `max_turns`, SDK-Ausnahme, `CodexError`
  im Code-Review): bestehende Meldung „Agent-Lauf abgebrochen … : <Ursache> …
  später fortsetzen mit: adw resume <run_id>" auf stderr, Exit 1, kein
  Traceback, kein `escalation.md`, Phase unverändert.
- **CLI bei `TimeoutExpired`/`OSError` in `context._git`:** bestehende Meldung
  „Eskalation: … Report: .adw/runs/<run_id>/escalation.md", Exit 1, kein
  Traceback; die Ursache nennt das git-Kommando.
- Interne Helper-Signaturen sind nicht Teil des Contracts.

## Akzeptanzkriterien

1. Jeder Eintrag in `REGISTRY` hat `max_turns` und `timeout_s` mit exakt den
   Werten aus der Tabelle in Scope 1; `AgentSpec(...)` ohne eines der beiden
   Felder wirft `TypeError`.
2. Der von `SdkAgentRunner.run` gebaute `ClaudeAgentOptions` enthält
   `max_turns` gleich `agent.max_turns` (Test über den Options-Bau).
3. Ein gefakter `query()`-Stream, der nach Meldung einer Session-ID länger als
   `timeout_s` hängt (Test mit kleinem Wert), endet mit `AgentRunError`, dessen
   Meldung „Zeitlimit" und den Agentnamen enthält; `on_session_id` wurde mit der
   Session-ID aufgerufen; das End-Event ist geschrieben und trägt `is_error: true`
   und die Ursache.
4. Ein Result mit `is_error=True` und Subtype `error_max_turns` endet mit
   `AgentRunError`, dessen Meldung „max_turns", den Agentnamen und den Wert
   enthält; das End-Event trägt die Ursache.
5. Eine `RuntimeError` aus `query()` endet als `AgentRunError` mit Typ und Text
   der Ursache; über die CLI ergibt das den Resume-Hinweis auf stderr, Exit 1,
   keinen Traceback, kein `escalated`.
6. `CodexError` in der Codex-Code-Review-Phase führt über die CLI zum
   Resume-Hinweis (Exit 1); der Lauf ist danach nicht `escalated` und es
   existiert kein `escalation.md`.
7. `subprocess.TimeoutExpired` aus `context._git` führt zur Eskalation mit
   `escalation.md`, die das git-Kommando und die Ursache nennt; kein Traceback.
8. `agent_run_start_payload` enthält `max_turns` und `timeout_s` des übergebenen
   Agents; bestehende Felder bleiben.
9. `uv run ruff check .` und `uv run pytest -x -q` sind grün.

Richtwert: 8–12 neue Tests. Das ist keine Mindestquote; deutlich mehr ist
Gold-Plating.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Grenzen in `.adw/config.yaml` überschreibbar machen.
- Budget-Grenzen in USD (`max_budget_usd`) — Abrechnung läuft über den Plan.
- Automatischer Retry nach Timeout oder `max_turns`.
- Effort pro Agent (Lauf W2).
- Dieselbe `TimeoutExpired`/`OSError`-Härtung für die `_git`-Helfer in
  `worktrees.py` und `retention.py` — kein Befund mit realem Schaden im Issue.

Die Vorentscheidungen E1–E8 des Issues sind bindend und kein Finding-Gegenstand.

## Definition of Done

- Alle Akzeptanzkriterien 1–9 erfüllt und durch Tests abgedeckt (Options-Bau,
  hängender Fake-Stream mit vorheriger Session-Meldung, `error_max_turns`,
  SDK-Ausnahme, Codex- und git-Fehlerpfad, Start-Payload).
- `uv run ruff check .` und `uv run pytest -x -q` grün; keine weiteren Linter
  oder Typechecker (E8).
- `CHANGELOG.md` und `CHANGELOG.de.md` enthalten synchron die Sektion
  `## [0.32.0] — Unreleased` mit `### Added` und `### Fixed`;
  `pyproject.toml`-Version unverändert `0.31.3`.
- Bestehendes Verhalten außerhalb des Scopes (Session-Checkpoint,
  Kosten-Aufsummierung, Codex-/Gate-/CI-Timeouts, Authoring-`CodexError`-Pfad,
  Modell-Check, andere `_git`-Helfer) unverändert; bestehende Tests grün.
