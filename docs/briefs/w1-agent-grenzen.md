# W1: Jeder Agent-Lauf hat eine Grenze, jeder Abbruch eine Ursache

Dritter Lauf der Reihe aus `ONBOARDING-SPEC-PLAN.md` (Paket W1 mit Befund C26).
Setzt auf dem gemergten Stand **0.31.3** (nach R1 und R2) auf. Die Ausgangslage ist am
2026-11-08 im Code geprüft und vor dem Bauen gegen den dann aktuellen Stand zu
prüfen (Zeilennummern können sich verschoben haben).

## Ausgangslage (im Code verifiziert, 2026-11-08)

### Claude-Agenten laufen ohne Turn- und Zeitlimit

`SdkAgentRunner.run` (`adw/agents.py:~500–560`) baut `ClaudeAgentOptions` ohne
`max_turns`; `_collect` (`:~585`) iteriert `query()` ohne Zeitlimit. Nur der
Modell-Check setzt `max_turns=1` (`:~461`). Gates (`timeout` je Gate), Codex
(900 s) und CI (`ci.timeout`) haben harte Grenzen — die Claude-Agenten nicht. Ein
hängender SDK-Stream blockiert den Lauf unbegrenzt.

`AgentSpec` (`adw/agents.py:~145`) hat die Felder `name`, `model`, `tools`,
`allowed_tools`, `system_append`, `permission_mode`, `sandbox_bash`. Das SDK kennt
`ClaudeAgentOptions.max_turns`; ein Lauf, der es erreicht, endet mit einem
Result `is_error=True` (Subtype `error_max_turns`), das heute als generischer
`AgentRunError("Agent-Lauf fehlgeschlagen: …")` ankommt.

### Gemessener Bedarf (alle 15 vorhandenen Läufe, `agent.tool.call`-Events je `agent.run`-Spanne)

| Agent | Läufe | Tool-Aufrufe max / p90 | Dauer max / p90 |
|---|---:|---:|---:|
| `build_agent` | 61 | 160 / 108 | 27 min / 18 min |
| `final_reviewer` | 18 | 98 / 86 | 6 min / 6 min |
| `plan_agent` | 15 | 56 / 46 | 7 min / 6 min |
| `spec_agent` | 16 | 40 / 38 | 3 min / 3 min |
| `plan_synthesis` | 32 | 32 / 28 | 5 min / 4 min |
| `spec_synthesis` | 33 | 26 / 18 | 3 min / 3 min |
| `log_analyst` | 1 | 10 | < 1 min |

### C26 — Fehler ohne Resume-Hinweis

`adw/cli.py` (`_execute`, `:~760–795`) übersetzt `EscalationError` und
`AgentRunError` in klare Meldungen (Eskalation bzw. „später fortsetzen mit: adw
resume"). Drei Fehlerarten kommen dagegen als roher Traceback an, ohne Hinweis und
ohne `escalation.md`:

- `CodexError` (z. B. `codex exec: Timeout nach 900s`, `adw/codex.py:~576`) in der
  Codex-Code-Review-Phase (`adw/phases.py:~345`, `_codex_review` aus `adw/context.py:82`; dort werden nur
  `FindingsParseError`/`ValidationError` gefangen). In den Authoring-Phasen ist
  `CodexError` bereits behandelt (`adw/authoring.py:~478`).
- `subprocess.TimeoutExpired` aus `context._git` (`adw/context.py:~359–366`), das
  nur den Returncode prüft.
- Ausnahmen des SDK aus `query()` (z. B. `ProcessError`, `CLIConnectionError`) in
  `_collect`.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Die Semantik von `AgentRunError` („transient, Lauf bleibt am Checkpoint,
  `adw resume` setzt fort") und von `EscalationError` („endgültig") bleibt.
- Die Session-Checkpoints (`on_session_id`) und die Kosten-Aufsummierung im
  `finally` von `_collect_into` bleiben unverändert und müssen auch bei Timeout
  greifen.
- Codex-Timeouts (900 s), Gate-Timeouts, CI-Timeout bleiben.
- Kein Effort-Feld (eigener Lauf W2).

## Aufgabe

**A1 — Grenzen je Agent.** `AgentSpec` bekommt `max_turns: int` und
`timeout_s: int`, **ohne Default** (jeder Eintrag in `REGISTRY` setzt beide
ausdrücklich). `max_turns` geht an `ClaudeAgentOptions`, `timeout_s` begrenzt das
Einsammeln des Streams.

**A2 — Benannte Ursache.** Erreicht ein Lauf `max_turns` oder `timeout_s`, endet
er mit `AgentRunError` und einer Meldung, die Agent, Grenze und Wert nennt; das
`agent.run`-End-Event trägt die Ursache.

**A3 — C26.** Die drei genannten Fehlerarten enden nie mehr als Traceback.

**A4 — Sichtbarkeit.** `max_turns` und `timeout_s` erscheinen im
`agent.run`-Start-Payload (`agent_run_start_payload`).

**A5 — Changelog.** `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
`## [0.32.0] — Unreleased` (`### Added` für A1/A2/A4, `### Fixed` für A3), im Stil
der vorhandenen Einträge. Version in `pyproject.toml` **nicht** ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 — Werte** (etwa das 2,5–3-fache des gemessenen Maximums, gerundet):

  | Agent | `max_turns` | `timeout_s` |
  |---|---:|---:|
  | `build_agent` | 400 | 3600 |
  | `final_reviewer` | 250 | 1200 |
  | `spec_agent`, `plan_agent` | 150 | 1200 |
  | `spec_synthesis`, `plan_synthesis` | 100 | 1200 |
  | `e2e_triage`, `log_analyst` | 50 | 600 |

  Die Werte sind Konstanten in `REGISTRY`, **keine** Config-Felder in
  `.adw/config.yaml`.
- **E2 — Timeout-Mechanismus:** `anyio.fail_after(timeout_s)` um die Iteration in
  `_collect`. Bei Ablauf: `AgentRunError("<agent>: Zeitlimit <timeout_s> s
  überschritten")`. Die Session-ID ist dann bereits gecheckpointet (bestehender
  Mechanismus), `adw resume` setzt dieselbe Session fort.
- **E3 — `max_turns`:** Liefert das Result `is_error` mit Subtype
  `error_max_turns`, lautet die Meldung `"<agent>: max_turns <n> erreicht"`.
  Keine Eskalation — `AgentRunError` wie jeder andere transiente Abbruch; der
  Nutzer entscheidet über den Resume.
- **E4 — C26, SDK:** Jede Ausnahme aus `query()`, die nicht schon
  `AgentRunError` ist, wird in `_collect` zu `AgentRunError` mit Typ und Text der
  Ursache (resumierbar). `KeyboardInterrupt`/`SystemExit` bleiben unberührt.
- **E5 — C26, Codex im Code-Review:** `CodexError` in der Codex-Code-Review-Phase
  wird zu `AgentRunError` (resumierbar, gleiche Meldung wie andere Abbrüche). Die
  Behandlung in den Authoring-Phasen bleibt wie sie ist.
- **E6 — C26, git:** `subprocess.TimeoutExpired` (und `OSError`) in
  `context._git` führen auf denselben Pfad wie ein Returncode ≠ 0 (`escalate`
  mit Kommando und Ursache).
- **E7:** Ein gemeinsamer Ort für die Meldungserzeugung; keine neue
  Ausnahmeklasse.
- **E8:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Kein
  flake8, isort, black, mypy.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Grenzen in `.adw/config.yaml` überschreibbar machen.
- Budget-Grenzen in USD (`max_budget_usd`) — Abrechnung läuft über den Plan.
- Automatischer Retry nach Timeout.
- Effort pro Agent (Lauf W2).

## Akzeptanzkriterien

1. Jeder Eintrag in `REGISTRY` hat `max_turns` und `timeout_s` mit den Werten aus
   E1; ein `AgentSpec` ohne diese Felder ist ein `TypeError`.
2. `max_turns` wird an `ClaudeAgentOptions` übergeben (Test über den Options-Bau).
3. Ein gefakter `query()`-Stream, der länger als `timeout_s` hängt (Test mit
   kleinem Wert), endet mit `AgentRunError` mit „Zeitlimit"; die vorher gemeldete
   Session-ID ist gecheckpointet; das End-Event ist geschrieben.
4. Ein Result mit `error_max_turns` endet mit `AgentRunError` mit „max_turns".
5. Eine `RuntimeError` aus `query()` endet als `AgentRunError`; die CLI gibt den
   Resume-Hinweis aus (Exit 1), kein Traceback.
6. `CodexError` in der Code-Review-Phase → Resume-Hinweis, Lauf nicht `escalated`.
7. `TimeoutExpired` aus `context._git` → Eskalation mit `escalation.md`, kein
   Traceback.
8. `max_turns` und `timeout_s` stehen im `agent.run`-Start-Payload.
9. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 8–12 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (CLI-Exit
und Meldungen, Felder im `agent.run`-Start-Payload), keine internen
Helper-Signaturen.
