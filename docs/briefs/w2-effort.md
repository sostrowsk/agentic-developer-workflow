# W2: Effort pro Agent — und Codex denkt wieder mit

Paket W2 aus `ONBOARDING-SPEC-PLAN.md` (mit Befund F17, Entscheidung D5). Setzt auf dem
gemergten Stand **0.35.0** (nach W14) auf. Die Ausgangslage ist am 2026-11-08 gemessen und vor
dem Bauen gegen den dann aktuellen Stand zu prüfen (W14 fügt `retro_agent` in
`REGISTRY` hinzu).

## Ausgangslage (gemessen, 2026-11-08)

### Kein Claude-Agent hat einen Effort

`SdkAgentRunner.run` (`adw/agents.py`) baut `ClaudeAgentOptions` ohne `effort`. Das SDK
kennt `effort: Literal["low","medium","high","xhigh","max"] | int | None`
(`claude_agent_sdk/types.py`). `AgentSpec` hat kein Effort-Feld;
`agent_run_start_payload` (`adw/agents.py:~198`) nennt keinen Effort, und
`build_run_record` schreibt deshalb `"effort": None` mit dem Kommentar
„not recorded until W2" (`adw/ledger.py:~238`).

### Wo die Kosten liegen (`adw kpis`, 21 Läufe im Ledger)

Kostenanteile gesamt: `build_agent` **64 %**, `plan_synthesis` 12 %,
`final_reviewer` 11 %, `spec_synthesis` 8 %, `plan_agent` 4 %, `spec_agent` 2 %. Der
Build-Agent erzeugte in 61 Läufen rund 2,3 Mio. Output-Tokens. Laut Lance Martin
(Anthropic) ist ein starkes Modell auf niedrigem Effort oft günstiger und genauso
gut; beim Build-Agent prüfen Gates und Codex-Review das Ergebnis deterministisch
bzw. unabhängig.

### F17 — Codex läuft ohne Reasoning-Effort

`CodexRunner._argv` (`adw/codex.py:~466`) pinnt das Modell (`-m gpt-6-astra`), weil
das isolierte `CODEX_HOME` (`_isolated_codex_home`, `adw/codex.py:254`) **keine**
`config.toml` hat. Aus demselben Grund greift `model_reasoning_effort = "high"` aus
`~/.codex/config.toml` nicht. Gemessen beim Code-Review von R1: 216 602 Input-Tokens,
**259 Reasoning-Tokens**, 44 s. Die End-Payloads von `codex.review`/`codex.author`
tragen `usage.reasoning_output_tokens`.

### Konfiguration

`AdwConfig` (`adw/config.py`) ist strikt (`extra="forbid"`), hat `codex: CodexConfig`
mit `timeout`. Ungültige Werte werden beim Start zu `ConfigError`.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Modelle je Agent bleiben (Opus/Fable/Sonnet wie in `REGISTRY`).
- `max_turns`/`timeout_s` (W1) bleiben.
- Kein Hill Climbing, keine automatische Wahl des Effort (W7).

## Aufgabe

**A1 — Effort je Claude-Agent.** `AgentSpec` bekommt `effort` (Wert oder `None` =
SDK-Default), Defaults nach E1, übersteuerbar in `.adw/config.yaml` (E2). Der wirksame
Wert geht an `ClaudeAgentOptions.effort` und steht im `agent.run`-Start-Payload.

**A2 — Codex-Reasoning-Effort (F17).** Jeder `codex exec`-Aufruf setzt den
Reasoning-Effort ausdrücklich; Default und Übersteuerung nach E3. Der Wert steht im
argv und damit im `codex.review`/`codex.author`-Start-Event.

**A3 — Ledger.** `build_run_record` übernimmt `effort` aus dem `agent.run`-Start-Payload
(statt `None`) und ergänzt je Lauf `codex_effort` (aus dem argv des ersten
Codex-Starts, sonst `null`).

**A4 — Doku und Changelog.** Handbuch EN+DE: Abschnitt zur Config um `effort` und
`codex.reasoning_effort`; `examples/config.yaml` mit auskommentiertem Beispiel.
`CHANGELOG.md`/`CHANGELOG.de.md` synchron: neue Sektion `## [0.36.0] — Unreleased`
mit `### Added`/`### Changed`; ausdrücklich nennen, dass der Build-Agent jetzt auf
`low` läuft und Codex auf `high`. Version in `pyproject.toml` **nicht** ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 — Defaults (Claude):** `build_agent`, `spec_agent`, `plan_agent` und
  `retro_agent` → `"low"`. `spec_synthesis`, `plan_synthesis`, `final_reviewer`,
  `e2e_triage`, `log_analyst` → `None` (SDK-Default, kein `effort` übergeben).
- **E2 — Übersteuerung (Claude):** neuer optionaler Top-Level-Schlüssel
  `effort:` in `.adw/config.yaml`, ein Mapping Agentenname → Stufe
  (`low|medium|high|xhigh|max`) oder `default` (= `None`). Unbekannter Agentenname
  oder unbekannte Stufe → `ConfigError` beim Start. Die Übersteuerung gilt für den
  ganzen Lauf; der wirksame Wert wird beim Run-Start im State gepinnt (wie die
  Breakpoints), damit `resume`/`approve` mit einer geänderten Config keinen Effort
  mitten im Lauf wechseln.
- **E3 — Codex:** neuer Schlüssel `codex.reasoning_effort` (`low|medium|high|xhigh`),
  Default `"high"` (entspricht der bisherigen Nutzer-Config). Gilt für Authoring und
  Code-Review gleichermaßen. Umsetzung als zusätzliches argv-Paar
  `-c model_reasoning_effort=<wert>` in `_argv`. Ebenfalls beim Run-Start gepinnt.
- **E4 — Wo der wirksame Wert entsteht:** eine Stelle, die aus `REGISTRY`-Default und
  gepinnter Übersteuerung den wirksamen `AgentSpec` bildet (z. B. per
  `dataclasses.replace`); keine zweite Default-Tabelle.
- **E5 — Mock-Runner:** schreibt denselben Start-Payload (mit `effort`), damit
  Dry-Runs und Tests den Wert sehen.
- **E6:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Effort je Phase oder je Runde (z. B. höher in Fix-Runden).
- Automatische Effort-Wahl, Hill Climbing (W7).
- Modellwechsel je Agent per Config.
- Effort in der GUI hervorheben (er steht im Start-Payload und ist dort sichtbar).

## Akzeptanzkriterien

1. Ohne `effort:` in der Config bekommt `build_agent` `effort="low"` in
   `ClaudeAgentOptions`, `final_reviewer` bekommt **kein** `effort` (Test über den
   Options-Bau).
2. `effort: {build_agent: medium, final_reviewer: high}` wirkt; `default` setzt auf
   `None`.
3. Unbekannter Agentenname bzw. unbekannte Stufe → `ConfigError` beim Start, kein Run
   angelegt.
4. Der wirksame Effort steht im `agent.run`-Start-Payload (echter und Mock-Runner).
5. Gepinnt: Config nach dem Run-Start ändern, `resume` → der Lauf nutzt weiter den
   gepinnten Wert.
6. Jeder `codex exec`-argv enthält `-c model_reasoning_effort=high` (Default) bzw. den
   konfigurierten Wert; ungültiger Wert → `ConfigError`.
7. `build_run_record` liefert `effort` je Agent aus dem Start-Payload und
   `codex_effort` je Lauf (Golden-File angepasst).
8. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 8–12 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (Config-
Schlüssel `effort` und `codex.reasoning_effort`, Start-Payloads, `run-record.json`-
Felder), keine internen Helper-Signaturen.
