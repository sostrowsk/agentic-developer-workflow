# Spec — W2: Effort pro Agent — und Codex denkt wieder mit

## Ziel

Jeder Claude-Agent läuft mit einem bewusst gewählten Effort (Build-, Spec-, Plan- und
Retro-Agent auf `low`, Synthese/Review/Triage/Analyse auf SDK-Default), und jeder
`codex exec`-Aufruf setzt den Reasoning-Effort ausdrücklich (Default `high`, behebt F17).
Beide Werte sind per `.adw/config.yaml` übersteuerbar, werden beim Run-Start gepinnt und
sind in den Start-Payloads und im Run-Record nachvollziehbar.

Ausgangslage gegen den aktuellen Stand (0.35.0, nach W14) geprüft: `retro_agent` steht in
`REGISTRY` (`adw/agents.py:362`); `build_run_record` schreibt `"effort": None`
(`adw/ledger.py:241`); `CodexRunner._argv` (`adw/codex.py:466`) pinnt nur das Modell und
setzt keinen Reasoning-Effort; Breakpoints werden über `state.pinned_breakpoints` gepinnt
(`adw/state.py:147`, `adw/cli.py:302`, `adw/cli.py:1124`) — das ist das Vorbild für das
Effort-Pinning.

## Scope

### A1 — Effort je Claude-Agent

`AgentSpec` erhält ein Feld `effort` (Stufe oder `None` = SDK-Default). Die Defaults
stehen in `REGISTRY` (E1):

| Agent | Effort |
|---|---|
| `build_agent` | `low` |
| `spec_agent` | `low` |
| `plan_agent` | `low` |
| `retro_agent` | `low` |
| `spec_synthesis` | SDK-Default (`None`) |
| `plan_synthesis` | SDK-Default (`None`) |
| `final_reviewer` | SDK-Default (`None`) |
| `e2e_triage` | SDK-Default (`None`) |
| `log_analyst` | SDK-Default (`None`) |

Der optionale Top-Level-Schlüssel `effort:` in `.adw/config.yaml` ist ein Mapping von
registriertem Agentennamen auf `low`, `medium`, `high`, `xhigh`, `max` oder `default`
(E2). Nicht aufgeführte Agenten behalten ihren Registry-Default. `default` bedeutet
ausdrücklich SDK-Default (`None`), auch für Agenten mit Registry-Default `low`.

```yaml
effort:
  build_agent: medium
  final_reviewer: high
  retro_agent: default
```

Genau eine Stelle bildet aus `REGISTRY`-Default und gepinnter Übersteuerung den wirksamen
`AgentSpec` (E4, z. B. per `dataclasses.replace`); es gibt keine zweite Default-Tabelle.
Ein konkreter wirksamer Wert geht an `ClaudeAgentOptions.effort`; bei `None` wird kein
`effort` übergeben. Der wirksame Wert steht im `agent.run`-Start-Payload als Feld `effort`
(Stufe oder JSON-`null`) — beim echten `SdkAgentRunner` und beim Mock-Runner (E5).

### A2 — Codex-Reasoning-Effort (F17)

Neuer Schlüssel `codex.reasoning_effort` mit den Werten `low`, `medium`, `high`, `xhigh`;
ohne Angabe gilt `high` (E3). `default` und `max` sind hier **nicht** zulässig.

```yaml
codex:
  reasoning_effort: high
```

Jeder `codex exec`-argv (Authoring und Code-Review) enthält zusätzlich das Paar
`-c model_reasoning_effort=<wirksamer Wert>`; damit steht der Wert im argv der
`codex.review`/`codex.author`-Start-Events und wirkt unabhängig von einer Nutzer-Config
außerhalb des isolierten `CODEX_HOME`.

### Validierung und Pinning

Unbekannte Agentennamen, unzulässige Stufen (Claude oder Codex) und Werte außerhalb der
beschriebenen Struktur führen beim Start zu `ConfigError`, bevor ein Run angelegt wird
(`AdwConfig` bleibt `extra="forbid"`).

Die Effort-Übersteuerung und der Codex-Reasoning-Effort werden beim Run-Start im
bestehenden Run-State gepinnt, analog zu `pinned_breakpoints`. `resume` und `approve`
verwenden die gepinnten Werte, auch wenn `.adw/config.yaml` inzwischen geändert wurde.
Ein neuer Run verwendet die geänderte Config.

### A3 — Ledger

- `build_run_record` übernimmt für jeden Eintrag in `agents` das Feld `effort` aus dem
  jeweiligen `agent.run`-Start-Payload (statt `None`).
- Neues Lauf-Feld `codex_effort`: der Wert aus dem argv des **ersten**
  `codex.review`-/`codex.author`-Starts in Ereignisreihenfolge. Fehlt ein solcher Start
  oder trägt sein argv kein `model_reasoning_effort`, ist der Wert `null`; für Altläufe
  werden keine heutigen Defaults erfunden.
- Das Feld ist additiv; `schema_version` bleibt 1. Das Golden-File wird angepasst.

### A4 — Doku und Changelog

- `docs/ADW-HANDBOOK.md` und `docs/ADW-HANDBOOK.de.md`: Config-Abschnitt um `effort` und
  `codex.reasoning_effort` (Defaults, zulässige Werte, `default`, Validierung, Pinning
  über `resume`/`approve`).
- `examples/config.yaml`: auskommentiertes Beispiel für beide Schlüssel.
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion `## [0.36.0] — Unreleased`
  mit `### Added`/`### Changed`; ausdrücklich nennen, dass der Build-Agent jetzt auf
  `low` und Codex auf `high` läuft. Version in `pyproject.toml` **nicht** ändern.

### Contract (Single-Lane)

Der Contract umfasst ausschließlich die extern beobachtbaren Flächen: Config-Schlüssel
`effort` und `codex.reasoning_effort`, das Feld `effort` im `agent.run`-Start-Payload,
das argv-Paar in den `codex.review`/`codex.author`-Start-Events sowie die
`run-record.json`-Felder `agents[].effort` und `codex_effort`. Interne Helper-Signaturen
gehören nicht zum Contract.

## Non-Goals

- Keine Modelländerung je Agent; Modelle bleiben wie in `REGISTRY`.
- `max_turns`/`timeout_s` (W1) und `codex.timeout` bleiben unverändert.
- Keine Änderung an Phasen, Gates, Review-Entscheidungen oder Ausführungszeitpunkt der Retro.
- Keine garantierte Kostenreduktion oder Mindestzahl an Reasoning-Tokens; Gegenstand ist
  die explizite Einstellung und ihre Nachvollziehbarkeit.
- Keine neue KPI-Auswertung oder GUI-Darstellung für Effort.
- Keine Änderung der Version in `pyproject.toml`.
- Keine zweite Default-Tabelle für Effort neben `REGISTRY`.

## Vorentscheidungen (bindend, kein Finding — auch nicht im Review-Loop)

- **E1:** `build_agent`, `spec_agent`, `plan_agent`, `retro_agent` → `"low"`;
  `spec_synthesis`, `plan_synthesis`, `final_reviewer`, `e2e_triage`, `log_analyst` →
  `None`.
- **E2:** Top-Level `effort:` als Mapping Agentenname → Stufe oder `default` (= `None`);
  unbekannter Name/unbekannte Stufe → `ConfigError` beim Start; gepinnt beim Run-Start.
- **E3:** `codex.reasoning_effort` (`low|medium|high|xhigh`), Default `"high"`, für
  Authoring und Review; umgesetzt als `-c model_reasoning_effort=<wert>` in `_argv`;
  gepinnt beim Run-Start.
- **E4:** eine Stelle bildet den wirksamen `AgentSpec` (z. B. `dataclasses.replace`).
- **E5:** Mock-Runner schreibt denselben Start-Payload inkl. `effort`.
- **E6:** Toolchain `uv run ruff check .` und `uv run pytest -x -q`.

## Akzeptanzkriterien

1. **Claude-Defaults (A1, E1):** Ohne `effort:` in der Config erhält `build_agent`
   `effort="low"` in `ClaudeAgentOptions`; `final_reviewer` erhält **kein** `effort`
   (Test über den Options-Bau).
2. **Claude-Übersteuerung (A1, E2):** Mit `effort: {build_agent: medium, final_reviewer: high}`
   erhalten diese Agenten `medium` bzw. `high`; ein Eintrag `default` setzt den Agenten
   auf `None` (kein `effort` in den Options). Nicht aufgeführte Agenten behalten ihren
   Default.
3. **Strikte Config (E2, E3):** Unbekannter Agentenname oder unbekannte Stufe unter
   `effort:` → `ConfigError` beim Start; es wird kein Run angelegt.
4. **Start-Payload (A1, E5):** Der wirksame Effort steht als `effort` im
   `agent.run`-Start-Payload, sowohl beim echten `SdkAgentRunner` als auch beim
   Mock-Runner.
5. **Pinning (E2, E3):** Wird die Config nach dem Run-Start geändert (`effort:` bzw.
   `codex.reasoning_effort`), nutzt der Lauf nach `resume` weiter die gepinnten Werte.
6. **Codex-argv (A2, E3):** Jeder `codex exec`-argv enthält `-c model_reasoning_effort=high`
   (Default) bzw. den konfigurierten Wert; ein ungültiger `codex.reasoning_effort` →
   `ConfigError`.
7. **Ledger (A3):** `build_run_record` liefert `effort` je Agent aus dem
   `agent.run`-Start-Payload und `codex_effort` je Lauf (aus dem argv des ersten
   Codex-Starts, sonst `null`); das Golden-File ist angepasst.
8. **Doku und Changelog (A4):** Handbuch EN+DE dokumentiert `effort` und
   `codex.reasoning_effort`; `examples/config.yaml` enthält ein auskommentiertes Beispiel;
   `CHANGELOG.md` und `CHANGELOG.de.md` enthalten synchron `## [0.36.0] — Unreleased` mit
   `### Added`/`### Changed` und nennen Build-Agent `low` und Codex `high`;
   `pyproject.toml`-Version unverändert.
9. **Toolchain (E6):** `uv run ruff check .` und `uv run pytest -x -q` sind grün.

Richtwert: 8–12 neue Tests. Deutlich mehr ist Gold-Plating.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Effort je Phase oder je Runde (z. B. höher in Fix-Runden).
- Automatische Effort-Wahl, Hill Climbing (W7).
- Modellwechsel je Agent per Config.
- Hervorhebung des Effort in der GUI (steht im Start-Payload und ist dort sichtbar).
- Nachträgliches Befüllen von `effort`/`codex_effort` für Altläufe im Ledger (bleiben `null`).
- Überwachungs-, Retry- oder Fallback-Mechanismen, die eine bestimmte Reasoning-Tokenzahl
  oder Kostenersparnis erzwingen. Gates, unabhängiges Review, Limits und Events bleiben
  die Backstops.
- Zusätzliche persistente Effort-Historien neben dem Pinning im vorhandenen Run-State,
  den Events und den Ledger-Feldern.

## Definition of Done

- Alle Akzeptanzkriterien 1–9 erfüllt; AC 1–7 durch Tests belegt, AC 8 durch Sichtprüfung.
- `uv run ruff check .` und `uv run pytest -x -q` grün.
- Keine Änderungen außerhalb des Scopes (Modelle, `max_turns`/`timeout_s`,
  `pyproject.toml`-Version unverändert).
- Handbuch und Changelog in EN und DE inhaltlich synchron.
