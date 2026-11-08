# Plan — W2: Effort pro Agent — und Codex denkt wieder mit

Maßgeblich ist `.adw/spec.md` (Scope A1–A4, AC 1–9). Single-Lane-Projekt
(`.adw/config.yaml`): nur der Workstream **backend**; er umfasst Code, Tests, beide
Handbücher, `examples/config.yaml` und beide Changelogs. Kein Frontend-Workstream.
Dieser Plan beschreibt die Implementierung; er implementiert nichts.

## Ausgangslage

Basis: Stand **0.35.0** (`a18ecdf`), am 2026-11-08 gegen den Code geprüft. Vor dem
Bauen gegen den dann aktuellen Stand erneut prüfen.

- `adw/agents.py`: `AgentSpec` `:158` (frozen dataclass, kein `effort`);
  `agent_run_start_payload` `:198` baut den `agent.run`-Start-Payload; `REGISTRY` `:232`
  (neun Agenten, `retro_agent` `:361`); `SdkAgentRunner` `:540` baut
  `ClaudeAgentOptions` `:564`. **Importkette:** `agents.py` → `adw.events` (`:20`) →
  `adw.state` (`events.py:23`) → `adw.config` (`state.py:14`). Ein Modul-Level-Import
  von `REGISTRY` in `config.py` wäre daher ein Zyklus (partially initialized module
  beim CLI-Start); der Zugriff muss lokal in der Validator-Funktion erfolgen.
- SDK (`claude_agent_sdk/types.py`): `ClaudeAgentOptions.effort:
  Literal["low","medium","high","xhigh","max"] | None = None`.
- `adw/context.py:66` `_agent_run(ctx, agent, …)`: **einzige** Aufrufstelle aller
  Agentenläufe (Authoring, Phasen, Lanes, Retro); baut den Start-Payload und ruft
  `ctx.agents.run(agent, …)` — Mock und echter Runner gleich.
- `adw/codex.py`: `CodexRunner.__init__(emitter, timeout, totals)` `:362`;
  `effective_argv` `:373` und `effective_author_argv` liefern `self._argv(...)`, der
  ausgeführte argv entsteht aus demselben `_argv`; `_argv` `:466` ist `@staticmethod`,
  pinnt nur `-m CODEX_MODEL` und `-c mcp_servers={}`. Start-Events setzen `argv` in
  `context.py:87` (review) und `authoring.py:464` (author).
- `adw/mock.py:115` `MockCodexRunner`: `effective_argv`/`effective_author_argv` liefern
  einen festen Platzhalter-argv („exact content is not contract-pinned"); Konstruktion
  ohne Config in `adw/dry_run.py:112`.
- `adw/config.py`: `CodexConfig` `:97` (nur `timeout`), `AdwConfig` `:115`
  (`extra="forbid"`), `load` `:135` hebt `ValidationError` als `ConfigError`.
- `adw/state.py:147` `pinned_breakpoints: list[...] | None = None` (leere Liste =
  echte Pinnung, `None` = Alt-State); Pin in `adw/cli.py:302`, Anwendung in
  `_config_for_continuation` `:1104`/`:1124` per `model_copy(update=…)`.
  `_build_context` `:1215` baut `CodexRunner(...)` `:1245`.
- `adw/ledger.py:230` `_agent` setzt `"effort": None` (`:241`); `build_run_record` `:251`.
  Golden: `tests/fixtures/ledger/expected-run-record.json` (aus
  `tests/fixtures/ledger/a1b2c3d4/events.jsonl`; die dortigen `codex.review`-Starts
  `seq 8/21/27` tragen kein `argv`).
- Tests: `captured_query`-Fixture in `tests/test_agents.py` für den Options-Bau; exakte
  argv-Vergleiche in `tests/test_codex.py:622/626`; exakte Start-Payload-Vergleiche
  möglich in `tests/test_instrumentation.py`, `tests/test_codex_author_span.py`;
  Pinning-Muster in `tests/test_breakpoints.py`.
- Doku: Handbuch §3.1 Config-Tabelle (`docs/ADW-HANDBOOK.md:143`, DE `:146`; Zeile
  `codex.timeout` `:163`/`:166`), §5.4 „Pinned settings" (`:273`, DE `:280`);
  `examples/config.yaml` (Breakpoints-Block `:30` als Stilvorbild); Changelog oben
  `## [0.35.0] — 2026-11-08`.

Betroffene Dateien (abschließend): `adw/agents.py`, `adw/context.py`, `adw/config.py`,
`adw/state.py`, `adw/cli.py`, `adw/codex.py`, `adw/ledger.py`;
`tests/test_agents.py`, `tests/test_config.py`, `tests/test_codex.py`,
`tests/test_ledger.py`, `tests/test_cli.py`, `tests/fixtures/ledger/…` (Golden +
Fixture-Events) sowie bestehende Tests mit exakten Payload-/argv-Vergleichen;
`docs/ADW-HANDBOOK.md`, `docs/ADW-HANDBOOK.de.md`, `examples/config.yaml`,
`CHANGELOG.md`, `CHANGELOG.de.md`.
Unverändert: Modelle, `max_turns`/`timeout_s`, `codex.timeout`, Phasen/Gates,
`adw/mock.py`, `adw/dry_run.py`, GUI, `schema_version` 1, `pyproject.toml`.

Contract: `.adw/contract.yaml` pinnt nur die extern beobachtbaren Flächen (Config-
Schlüssel, `agent.run`-Start-Payload `effort`, argv-Paar in `codex.review`/
`codex.author`-Starts, `run-record.json`-Felder). Helfer- und Feldnamen im State sowie
der Bau von `ClaudeAgentOptions` (Keyword nur bei konkretem Wert, B3.4) sind
Planvorgaben, keine Vertragsbestandteile.

## Workstream backend

### B1 — `AgentSpec.effort` und Registry-Defaults (A1, E1)

`adw/agents.py`:
1. `ClaudeEffort = Literal["low", "medium", "high", "xhigh", "max"]`.
2. `AgentSpec` erhält `effort: ClaudeEffort | None = None` (am Ende, mit Default, damit
   bestehende Konstruktionen gültig bleiben).
3. `REGISTRY`: `effort="low"` für `build_agent`, `spec_agent`, `plan_agent`,
   `retro_agent`; die übrigen fünf bleiben ohne Angabe (`None`). Das ist die einzige
   Default-Tabelle; `REGISTRY` wird nirgends mutiert.

### B2 — Config-Schlüssel und Validierung (A1, A2, E2, E3; AC 3, 6)

`adw/config.py`:
1. `EffortSetting = Literal["low", "medium", "high", "xhigh", "max", "default"]`;
   `CodexEffort = Literal["low", "medium", "high", "xhigh"]`.
2. `AdwConfig.effort: dict[str, EffortSetting] = Field(default_factory=dict)` plus
   `field_validator("effort")`: jeder Schlüssel muss in `REGISTRY` stehen, sonst
   `ValueError` mit dem Namen (→ `ConfigError` über `load`). **Kein** Modul-Level-Import
   von `adw.agents` in `config.py` (Zyklus über `events` → `state` → `config`, siehe
   Ausgangslage): `from adw.agents import REGISTRY` steht lokal im Validator-Body und
   läuft damit erst nach der Modulinitialisierung, zur Ladezeit der Config. Kurzer
   Kommentar am Import, warum er lokal ist. Unbekannte Stufe, Nicht-Mapping
   (`effort: 3`, `effort:` leer = `None`) scheitern an der Typisierung.
3. `CodexConfig.reasoning_effort: CodexEffort = "high"` (`default`/`max` damit
   unzulässig).
4. Validierung läuft beim Config-Laden vor `RunState.new` — kein Run-Ordner, kein State.
   `extra="forbid"` bleibt auf allen Modellen.
5. Startkontrolle: `uv run adw --help` und die bestehenden CLI-/Config-Tests müssen nach
   B2 ohne Import-Fehler laufen (deckt den Zyklus ab); kein neuer Test dafür.

### B3 — Eine Stelle für den wirksamen `AgentSpec` (A1, E4, E5; AC 1, 2, 4)

1. `adw/agents.py` (Empfehlung): `effective_spec(spec, overrides) -> AgentSpec`:
   ohne Eintrag `spec` unverändert; `"default"` → `dataclasses.replace(spec,
   effort=None)`; sonst `replace(spec, effort=<stufe>)`.
2. `adw/context.py` `_agent_run`: als erste Zeile
   `agent = effective_spec(agent, ctx.config.effort)` — Start-Payload und Runner
   erhalten denselben wirksamen Spec. Keine Änderung an den Aufrufern; damit sind
   Authoring/Synthese, Build, Review, Triage, Analyse und Retro inklusive Mock
   abgedeckt.
3. `agent_run_start_payload`: zusätzliches Feld `"effort": agent.effort`
   (Stufe oder `None` → JSON `null`, niemals der String `default`). Da der Payload an
   der Aufrufstelle entsteht, gilt das für `SdkAgentRunner` und `MockAgentRunner`
   gleich; `adw/mock.py` bleibt unverändert.
4. `SdkAgentRunner.run`: `effort` nur bei konkretem Wert übergeben, z. B.
   `**({"effort": agent.effort} if agent.effort is not None else {})` in
   `ClaudeAgentOptions(...)`; bei `None` fehlt das Keyword vollständig.

### B4 — Codex-Reasoning-Effort im argv (A2, E3; AC 6)

`adw/codex.py`:
1. `CodexRunner.__init__(…, reasoning_effort: str = "high")` → `self._reasoning_effort`.
2. `_argv` wird Instanzmethode; nach `"-c", "mcp_servers={}"` zusätzlich
   `"-c", f"model_reasoning_effort={self._reasoning_effort}"` (kurzer Kommentar: wirkt
   unabhängig von einer Nutzer-Config, das isolierte `CODEX_HOME` hat keine). Damit
   enthalten `effective_argv`, `effective_author_argv` und der ausgeführte argv das Paar
   identisch — für Authoring (Spec und Plan) und Code-Review.
3. `adw/cli.py` `_build_context`: `CodexRunner(…, reasoning_effort=config.codex.reasoning_effort)`.
   `MockCodexRunner` bleibt unverändert: sein argv ist laut Docstring nicht gepinnt, er
   führt kein `codex exec` aus, und AC 6 betrifft den `codex exec`-argv. Dry-Run-Läufe
   haben daher `codex_effort: null` im Run-Record.

### B5 — Pinning im Run-State (Validierung und Pinning; AC 5)

1. `adw/state.py` (Empfehlung): `pinned_effort: dict[str, EffortSetting] | None = None`
   und `pinned_codex_reasoning_effort: CodexEffort | None = None`, kommentiert wie
   `pinned_breakpoints`: das leere Mapping ist eine echte Pinnung („keine
   Übersteuerung"); `None` = Alt-State von vor W2, dann gilt weiterhin die Config.
2. `adw/cli.py` `run` neben `:302`: `state.pinned_effort = dict(config.effort)`,
   `state.pinned_codex_reasoning_effort = config.codex.reasoning_effort` — vor dem
   ersten `state.save`. Auch ein leeres `effort`-Mapping wird gepinnt, damit später
   hinzugefügte Config-Einträge einen laufenden Run nicht verändern.
3. `_config_for_continuation` neben `:1124`: bei nicht-`None`-Pins
   `config.model_copy(update={"effort": dict(state.pinned_effort)})` bzw.
   `codex=config.codex.model_copy(update={"reasoning_effort": …})`. Docstring um die
   beiden Pins ergänzen. `resume` und `approve` gehen beide über diese Funktion; ein
   neuer `run` liest die aktuelle Config. Bestehende States ohne die neuen Felder laden
   weiterhin (optionale Felder mit Default `None`).

### B6 — Ledger (A3; AC 7)

`adw/ledger.py`:
1. `_agent`: `"effort": begin.get("effort")` (Kommentar „not recorded until W2" weg);
   fehlt das Feld (Altlauf) → `None`.
2. `build_run_record`: neues Feld `"codex_effort"` (z. B. direkt nach `"agents"`):
   erstes Event in Dateireihenfolge mit `type in ("codex.review", "codex.author")` und
   `kind == "start"`; aus dessen `payload.argv` (nur wenn Liste) den Wert des ersten
   `str`-Elements mit Präfix `model_reasoning_effort=`; fehlt der Start, das `argv` oder
   das Element → `None`. Nur der erste Start zählt, keine weitere Suche zu späteren
   Starts; keine Ersatzwerte aus Config, Registry oder State.
3. `SCHEMA_VERSION` bleibt 1.
4. Golden: in `tests/fixtures/ledger/a1b2c3d4/events.jsonl` einem `agent.run`-Start
   `"effort": "low"` und dem ersten `codex.review`-Start (`seq 8`) ein `argv` mit
   `-c model_reasoning_effort=high` geben; `expected-run-record.json` entsprechend
   (`agents[].effort`, `"codex_effort": "high"`).

### B7 — Doku und Changelog (A4; AC 8)

- `docs/ADW-HANDBOOK.md` / `.de.md` synchron: §3.1-Tabelle um `effort.<agent>`
  (Registry-Defaults E1, Werte `low|medium|high|xhigh|max|default`, `default` = SDK-
  Default auch für `low`-Agenten, nicht aufgeführte behalten Default) und
  `codex.reasoning_effort` (`low|medium|high|xhigh`, Default `high`, Authoring und
  Review, als `-c model_reasoning_effort=…`); Validierungsabsatz um unbekannten
  Agentennamen/unzulässige Stufe ergänzen; §5.4 um beide Pins (`resume`/`approve`
  verwenden die Start-Werte, ein neuer Run die geänderte Config). Sichtbarkeit: Feld
  `effort` im `agent.run`-Start, argv in `codex.*`-Starts, `agents[].effort`/
  `codex_effort` im Run-Record — ein Satz, wo der Run-Record beschrieben ist.
- `examples/config.yaml`: auskommentierter Block im Stil von `breakpoints` mit dem
  `effort:`-Beispiel aus der Spec und `codex: reasoning_effort: high`.
- `CHANGELOG.md` / `.de.md`: oberste Sektion `## [0.36.0] — Unreleased` mit
  `### Added` (Config `effort`, `codex.reasoning_effort`, Ledger-Felder) und
  `### Changed` (Build-, Spec-, Plan-, Retro-Agent laufen jetzt auf `low`; Codex läuft
  ausdrücklich auf `high` — behebt F17). `pyproject.toml` bleibt `0.35.0`.

## Reihenfolge

B1 → B2 → B3 → B4 → B5 → B6 → B7. Jeden neuen Test zuerst rot sehen.

## Tests (Richtwert 8–12 neue Tests; geplant 10)

Hermetik: kein echtes Modell, kein echtes `codex`; Options-Bau über die bestehende
`captured_query`-Fixture, CLI über `typer.testing.CliRunner` mit `--dry-run`.

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 1 | Options-Bau: `REGISTRY["build_agent"]` → `options.effort == "low"`; `REGISTRY["final_reviewer"]` → `options.effort is None` (kein Keyword übergeben). Dazu eine Dict-Gleichheit `{name: spec.effort}` über `REGISTRY` gegen die E1-Tabelle (eine Assertion, keine Matrix) | `tests/test_agents.py` |
| 2 | `effective_spec` parametrisiert mit `{build_agent: medium, final_reviewer: high, retro_agent: default}`: `medium`, `high`, `None`; nicht aufgeführter `spec_agent` bleibt `low`, `e2e_triage` bleibt `None`; übrige Felder unverändert | dto. |
| 3, 6 | `AdwConfig.load` parametrisiert → `ConfigError`: unbekannter Agentenname, unbekannte Claude-Stufe, `effort` kein Mapping / `null`, `codex.reasoning_effort` `max` / `default` / unbekannt; Gegenprobe: ohne Schlüssel `effort == {}` und `codex.reasoning_effort == "high"` | `tests/test_config.py` |
| 3 | CLI `adw run --dry-run` mit unbekanntem Agentennamen unter `effort:` → Exit ≠ 0, kein Run-Ordner/State angelegt | `tests/test_cli.py` |
| 4 | Dry-Run bis `done` (Mock-Runner): `agent.run`-Start von `build_agent` hat `effort "low"`, von `final_reviewer` `effort null` | dto. |
| 4 | `_agent_run` mit `SdkAgentRunner` (`captured_query`) und Config-Übersteuerung `build_agent: high`: Start-Payload `effort "high"` und `options.effort == "high"` | `tests/test_agents.py` |
| 5 | Dry-Run mit `effort: {build_agent: medium}` und `codex.reasoning_effort: low` bis Plan-Gate; Config auf `high`/`xhigh` ändern; `adw approve` → `build_agent`-Start `effort "medium"`; `_config_for_continuation(...)` liefert `effort` und `codex.reasoning_effort` gepinnt (`medium`/`low`). Zweiter Fall: Run ohne `effort:` gestartet, Config danach um `build_agent: high` ergänzt → Fortsetzung bleibt bei `low` (leeres Mapping gepinnt) | `tests/test_cli.py` |
| 6 | Parametrisiert: `CodexRunner()` / `CodexRunner(reasoning_effort="xhigh")` → `effective_argv` und `effective_author_argv` enthalten das benachbarte Paar `-c`, `model_reasoning_effort=high` bzw. `=xhigh`; ausgeführter argv (`captured_run`) identisch; `_build_context` (Nicht-Dry-Run) reicht `config.codex.reasoning_effort` an den Runner | `tests/test_codex.py` |
| 7 | Parametrisiert über Events: kein Codex-Start → `null`; erster Start ohne Paar, späterer mit → `null`; erster `codex.author` mit `low`, späterer Review mit `high` → `"low"`; `agents[].effort` aus Payload (`"low"`, explizit `null`, fehlendes Feld → `null`) | `tests/test_ledger.py` |
| 7 | Golden-File angepasst (B6.4) — bestehender Test, kein neuer | dto. |
| 8 | Sichtprüfung Handbuch EN/DE, `examples/config.yaml`, Changelogs, `pyproject.toml` | — |
| 9 | `uv run ruff check .`, `uv run pytest -x -q` | Gate |

Bestehende exakte Vergleiche anpassen statt neue Tests: argv in
`tests/test_codex.py:622/626` (und weitere, die `_argv` exakt prüfen), exakte
`agent.run`-Start-Payload-Schlüssel (z. B. `tests/test_instrumentation.py`,
`tests/test_codex_author_span.py`), ggf. `AgentSpec`-Gleichheiten. Keine weiteren
Tests (keine Matrix über alle Stufen oder alle Agenten im Options-Bau).

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); bestehende Suite grün.
- AC 1–7 durch Tests belegt, AC 8 durch Sichtprüfung.
- Keine zweite Default-Tabelle; Modelle, `max_turns`/`timeout_s`, `codex.timeout`,
  Phasen/Gates/Review-Entscheidungen/Retro-Zeitpunkt, `pyproject.toml`-Version
  unverändert; `schema_version` bleibt 1; keine neue KPI-/GUI-Darstellung.
- Handbücher und Changelogs EN/DE inhaltlich synchron.

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
