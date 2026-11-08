# Plan — W1: Jeder Agent-Lauf hat eine Grenze, jeder Abbruch eine Ursache

Maßgeblich ist `.adw/spec.md` (Scope 1–5, AC 1–9). Single-Lane-Projekt
(`.adw/config.yaml`): nur der Workstream **backend**; er umfasst Code, Tests und beide
Changelogs. Die Vorentscheidungen E1–E8 des Issues sind bindend.

Basis: Stand **0.31.3** (`4a7f958`). Die Code-Stellen der Spec sind am 2026-11-08 erneut
geprüft und stimmen: `adw/agents.py:146` (`AgentSpec`, frozen dataclass, Defaults ab
`permission_mode`), `:183` (`agent_run_start_payload`), `:215–328` (`REGISTRY`, acht
Einträge), `:502–561` (`SdkAgentRunner.run`, `ClaudeAgentOptions` ohne `max_turns`),
`:563–584` (`_collect_into`, Default-End-Payload + Kosten im `finally`), `:586–639`
(`_collect`, `staticmethod`, kennt weder Agentname noch `timeout_s`), `adw/phases.py:344–353`
(Codex-Code-Review fängt nur `FindingsParseError`/`ValidationError`),
`adw/context.py:359–366` (`_git`, nur Returncode), `adw/cli.py:805–823` (Handler für
`EscalationError`/`AgentRunError`). `AgentSpec(...)` wird nur in `adw/agents.py`
konstruiert (keine Test-Konstruktionen). Vor dem Bauen gegen den dann aktuellen Stand
prüfen.

Betroffene Dateien (abschließend): `adw/agents.py`, `adw/phases.py`, `adw/context.py`,
Tests unter `tests/`, `CHANGELOG.md`, `CHANGELOG.de.md`. `adw/cli.py` bleibt unverändert
(die bestehenden Handler decken alle Pfade ab). `pyproject.toml` bleibt `0.31.3`.

Contract: `.adw/contract.yaml` pinnt nur die extern beobachtbaren Flächen aus der Spec
(Start-/End-Payload von `agent.run`, Meldungstexte, CLI-Verhalten). Helfernamen,
Signaturen und Ablageorte in diesem Plan sind Empfehlungen, keine Vertragsfläche.

## Workstream backend

### B1 — Grenzen je Agent (Scope 1, AC 1, 2)

Datei: `adw/agents.py`.

1. `AgentSpec`: Pflichtfelder `max_turns: int` und `timeout_s: int` ohne Default, vor
   `permission_mode` (Dataclass-Reihenfolge: keine Pflichtfelder nach Defaults). Fehlt
   eines → `TypeError` aus der Dataclass, kein eigener Check.
2. Jeder der acht `REGISTRY`-Einträge setzt beide Werte ausdrücklich laut Tabelle:

   | Agent | `max_turns` | `timeout_s` |
   |---|---:|---:|
   | `build_agent` | 400 | 3600 |
   | `final_reviewer` | 250 | 1200 |
   | `spec_agent`, `plan_agent` | 150 | 1200 |
   | `spec_synthesis`, `plan_synthesis` | 100 | 1200 |
   | `e2e_triage`, `log_analyst` | 50 | 600 |

   Keine Config-Felder.
3. `SdkAgentRunner.run`: `max_turns=agent.max_turns` an `ClaudeAgentOptions`.
   `resolve_model_alias` (`max_turns=1`) unverändert.

### B2 — Zeitlimit und benannte Ursache (Scope 2, AC 3, 4)

Datei: `adw/agents.py`.

1. Agentname und `timeout_s` müssen `_collect` erreichen: `run` reicht `agent` (bzw.
   Name + `timeout_s`) über `_collect_into` an `_collect` weiter. Die Signaturen sind
   intern und keine Vertragsfläche.
2. Meldungen an **einem** Ort erzeugen (Empfehlung: zwei kleine Modul-Funktionen oder
   Format-Konstanten neben `AgentRunError`):
   `"<agent>: Zeitlimit <timeout_s> s überschritten"` und
   `"<agent>: max_turns <n> erreicht"`. Keine neue Ausnahmeklasse.
3. In `_collect` die `async for`-Iteration in `with anyio.fail_after(timeout_s):`
   einschließen. Der lokale Zustand (`session_id`, `usage_raw`, `cost_usd`, Texte) wird
   wie heute laufend gesammelt. Keine zusätzliche Spanne eröffnen; die Span-Verantwortung
   bleibt am Call-Site (`adw/context.py:75`).
4. `TimeoutError` (aus `fail_after`) → End-Payload mit den **bis dahin gesammelten**
   Werten (nicht die leeren Defaults aus `_collect_into`), `is_error: True`,
   `error: <Zeitlimit-Meldung>` auf `handle.end_payload` setzen, dann
   `raise AgentRunError(<Meldung>) from exc`. Die Session-ID wurde bereits beim ersten
   Auftreten über `on_session_id` persistiert (unverändert); die Kosten-Aufsummierung im
   `finally` von `_collect_into` liest das gesetzte End-Payload (unverändert).
5. Result mit `is_error=True` und `subtype == "error_max_turns"` (per
   `getattr(message, "subtype", None)` beim Result-Message festhalten) → Meldung
   `"<agent>: max_turns <n> erreicht"` mit `n = options.max_turns`; End-Payload wie
   heute plus `error: <Meldung>`; `AgentRunError(<Meldung>)`. Keine Eskalation.
6. Übrige `is_error`-Results: Verhalten und Meldung (`"Agent-Lauf fehlgeschlagen: …"`)
   unverändert. Ob dort ebenfalls `error` im End-Payload steht, ist nicht gefordert —
   nicht ergänzen.

### B3 — SDK-Ausnahmen ohne Traceback (Scope 3 SDK, AC 5)

Datei: `adw/agents.py`, `_collect`.

1. Um die Iteration (innerhalb von `_collect`, außerhalb von `fail_after`, damit B2.4
   zuerst greift): `except AgentRunError: raise` (Meldung bleibt); `except Exception as
   exc:` → End-Payload mit gesammelten Werten, `is_error: True`,
   `error: f"{type(exc).__name__}: {exc}"`, dann
   `raise AgentRunError(f"{type(exc).__name__}: {exc}") from exc`.
2. `Exception` statt `BaseException`: `KeyboardInterrupt`/`SystemExit` bleiben unberührt.
   `_collect` läuft direkt unter `anyio.run` ohne Task-Gruppe; der Fang sieht die
   Originalausnahme aus `query()`, keine `ExceptionGroup`.
3. End-Payload-Bau für B2/B3 in einem kleinen gemeinsamen Helfer, damit die fünf
   bestehenden Felder überall gleich befüllt werden.
4. **Bestandstest anpassen:** `tests/test_agents.py:85–100` (Stream wirft nach früher
   Session-Meldung `RuntimeError`) erwartet heute `pytest.raises(RuntimeError)`; künftig
   `AgentRunError`, dessen Meldung `RuntimeError` und den Ursachentext enthält. Die
   Assertion `seen == ["sess-frueh"]` bleibt.

### B4 — `CodexError` im Code-Review ist resumierbar (Scope 3 Codex, AC 6)

Datei: `adw/phases.py`, `run_codex_review_phase`.

1. Den bestehenden `try` um `_codex_review(...)` (Zeile 344–353) um
   `except CodexError as exc: raise AgentRunError(f"Codex-Code-Review: {exc}") from exc`
   ergänzen. `CodexError` aus `adw.codex` importieren; `CodexAuthorError` ist Unterklasse
   und wird mit abgedeckt. Die vorhandene Behandlung von
   `FindingsParseError`/`ValidationError` bleibt.
2. Kein `escalate`, kein Save: der Lauf bleibt am Checkpoint, die CLI gibt über den
   bestehenden `AgentRunError`-Handler (`adw/cli.py:811`) den Resume-Hinweis aus, Exit 1.
   Der `round`-Span endet wie bei jeder Ausnahme mit `outcome: aborted`.
3. `_changed_files` (git) steht im selben `try`, wirft aber `EscalationError` — davon
   nicht betroffen. Authoring-Pfad (`adw/authoring.py:478`) und Codex-Timeout (900 s)
   unverändert.

### B5 — git-Timeout/OSError in `context._git` eskaliert (Scope 3 git, AC 7)

Datei: `adw/context.py`, `_git`.

1. `run_git(...)` in `try`; `except (subprocess.TimeoutExpired, OSError) as exc:`
   `raise escalate(ctx, f"git {' '.join(args)} in {cwd} fehlgeschlagen: {exc}") from exc`
   — gleicher Meldungsaufbau wie der Returncode-Pfad (Kommando + Ursache).
2. Returncode-Pfad und `timeout=120` unverändert. `_git`-Helfer in
   `worktrees.py`/`retention.py` nicht anfassen (Deferred).

### B6 — Grenzen im Start-Payload (Scope 4, AC 8)

Datei: `adw/agents.py`, `agent_run_start_payload`.

1. Zusätzlich `"max_turns": agent.max_turns` und `"timeout_s": agent.timeout_s`;
   bestehende Felder unverändert. Gilt automatisch für echten und Mock-Runner
   (Call-Site `adw/context.py:75`).
2. Die beiden Bestandstests, die die exakte Schlüsselmenge festschreiben
   (`tests/test_instrumentation.py:166–168`, `tests/test_span_relocation.py:75–77`), um
   die zwei Schlüssel erweitern. GUI-Fixtures (`tests/fixtures/contract_baseline_*.json`,
   `tests/gui_app_helpers.py`) sind feste Aufnahmen und bleiben unverändert.

### B7 — Changelog (Scope 5)

- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue oberste Sektion
  `## [0.32.0] — Unreleased` mit `### Added` (Grenzen je Agent `max_turns`/`timeout_s`;
  benannte Ursache im `agent.run`-End-Event via `error`; Start-Payload-Felder) und
  `### Fixed` (C26: SDK-Ausnahme, `CodexError` im Code-Review, git-Timeout/OSError in
  `context._git` enden ohne Traceback), jeweils in der Sprache und im Stil der Datei,
  inhaltlich gleich in beiden Sprachen.
- `pyproject.toml` bleibt `0.31.3`.

## Reihenfolge

B1 zuerst (Pflichtfelder brechen sonst den Import). B2 vor B3 (gleiche Stelle in
`_collect`, Fang-Reihenfolge). B4, B5, B6 unabhängig. B7 zuletzt. Jeden neuen Test zuerst
gegen den Ausgangsstand rot sehen.

## Tests (Richtwert 8–12 neue Tests + 3 angepasste)

Bestehende Fixtures wiederverwenden (`captured_query`, `fake_stored_login` in
`tests/test_agents.py`; `ctx`/`target_repo` in `tests/test_phases.py`/`tests/test_cli.py`;
die `AgentRunError`-CLI-Tests ab `tests/test_cli.py:559` als Vorlage). Fake-`query()` als
Async-Generator per Monkeypatch; keine echten SDK-/Codex-Aufrufe. Kleine Grenzwerte im
Test über `dataclasses.replace(REGISTRY[...], timeout_s=1)`. Die Tabelle ergibt 9 neue
Tests.

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 1 | `REGISTRY`-Werte exakt laut Tabelle (ein Test mit Soll-Dict); `AgentSpec(...)` ohne `max_turns` bzw. ohne `timeout_s` → `TypeError` (parametrisiert) | `tests/test_agents.py` |
| 2 | Options-Bau: `captured_query` sieht `options.max_turns == agent.max_turns` | dto. |
| 3 | Fake-Stream meldet Session-ID, hängt dann (`anyio.sleep`) länger als ein kleines `timeout_s`: `AgentRunError` mit „Zeitlimit" und Agentname; `on_session_id` mit der ID aufgerufen; Span-Handle-End-Payload hat `is_error: True`, `error` und die bereits gemeldete `session_id` | dto. |
| 4 | Fake-Result `is_error=True`, `subtype="error_max_turns"`: `AgentRunError` mit „max_turns", Agentname, Wert; End-Payload trägt `error` | dto. |
| 5a | Fake-`query()` wirft `RuntimeError("boom")`: `AgentRunError` mit `RuntimeError` und `boom`; End-Payload trägt `error` (ersetzt/erweitert den Bestandstest aus B3.4) | dto. |
| 5b | CLI: Runner wirft `AgentRunError` aus SDK-Ausnahme → stderr Resume-Hinweis, Exit 1, kein Traceback, Phase nicht `escalated`, kein `escalation.md` | `tests/test_cli.py` (neben den bestehenden `AgentRunError`-Tests) |
| 6 | Codex-Client wirft `CodexError` in der Code-Review-Phase: über die CLI Resume-Hinweis, Exit 1, Phase nicht `escalated`, kein `escalation.md` | `tests/test_cli.py` bzw. `tests/test_phases.py` (Phase wirft `AgentRunError`) |
| 7 | `run_git` wirft `subprocess.TimeoutExpired` (ggf. parametrisiert mit `OSError`) in `context._git`: `EscalationError`, `escalation.md` nennt git-Kommando und Ursache | `tests/test_phases.py` |
| 8 | `agent_run_start_payload` enthält `max_turns`/`timeout_s` des übergebenen Agents; bestehende Felder bleiben — durch Erweiterung der zwei Schlüsselmengen-Tests plus Wert-Assertion | `tests/test_instrumentation.py:166`, `tests/test_span_relocation.py:75` |
| 9 | `uv run ruff check .`, `uv run pytest -x -q` | Gate |

Keine zusätzliche Testmatrix, keine Tests für Verhalten außerhalb des Scopes (kein
eigener Test für `KeyboardInterrupt`/`SystemExit`).

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); die gesamte bestehende Suite bleibt grün. Keine
  weiteren Linter/Typechecker (E8).
- AC 1–9 erfüllt und durch Tests belegt.
- Changelogs EN/DE synchron mit `## [0.32.0] — Unreleased`, Version unverändert `0.31.3`.
- Unverändert: Session-Checkpoint, Kosten-Aufsummierung, Codex-/Gate-/CI-Timeouts,
  Authoring-`CodexError`-Pfad, Modell-Check, andere `_git`-Helfer, CLI-Handler. Keine
  neuen Ausnahmeklassen, persistenten Zustände oder Überwachungsmechanismen.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Grenzen in `.adw/config.yaml` überschreibbar machen.
- Budget-Grenzen in USD (`max_budget_usd`) — Abrechnung läuft über den Plan.
- Automatischer Retry nach Timeout oder `max_turns`.
- Effort pro Agent (Lauf W2).
- Dieselbe `TimeoutExpired`/`OSError`-Härtung für die `_git`-Helfer in
  `worktrees.py` und `retention.py` — kein Befund mit realem Schaden im Issue.

Die Vorentscheidungen E1–E8 des Issues sind bindend und kein Finding-Gegenstand.
