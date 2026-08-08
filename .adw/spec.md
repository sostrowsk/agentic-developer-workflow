# Spec — Instrumentierung des Orchestrators mit dem Event-Emitter

Umsetzungsreihenfolge-Schritte 2–4 aus `docs/GUI-SPEC.md` §11. Der in Lauf 1
gebaute Emitter `adw/events.py` (`EventEmitter`, `NoOpEmitter`) wird verdrahtet
und aufgerufen — nicht neu gebaut, nicht erweitert. Maßgeblich sind
GUI-SPEC §4.3 (Fail-open), §4.4 (Event-Typen/Payloads) und §6
(Instrumentierungs-Punkte); bei Widerspruch mit dieser Spec gilt die GUI-SPEC.

## Goal

Jeder ADW-Lauf schreibt ein vollständiges, strukturiertes Event-Log
(`.adw/runs/<run_id>/events.jsonl`), das den Kontrollfluss des Orchestrators
Span für Span abbildet — Phasen, Lanes, Loop-Runden, Agent-Läufe inklusive der
gespiegelten SDK-Stream-Daten (Tool-Calls, Tool-Results, Nachrichten, Usage,
Kosten). Das Log entsteht als reines Nebenprodukt: der Orchestrator verhält
sich mit Instrumentierung an keiner Stelle anders als ohne. Ein Dry-Run
erzeugt denselben vollständigen Trace ohne Tokenverbrauch und ist der
Abnahmepfad.

## Scope

- **Verdrahtung.** `RunContext` (`adw/phases.py`) trägt eine Emitter-Instanz.
  Die Module ohne RunContext-Kenntnis — `adw/agents.py`, `adw/gates.py`,
  `adw/codex.py`, `adw/ci.py`, `adw/github.py`, `adw/triage.py`,
  `adw/state.py` — erhalten den Emitter als OPTIONALEN Parameter mit Default
  `NoOpEmitter()`.
- **Genau eine Emitter-Instanz je Run und Prozess.** Sie wird einmal beim
  Run-Start erzeugt (bei `adw run` nach `RunState.new(...)`, bei
  `adw resume`/`adw approve` nach dem State-Laden) und durchgereicht;
  keine zweite Konstruktion irgendwo.
- **Emit-Aufrufe** an den Stellen aus GUI-SPEC §6 mit den Payloads aus §4.4,
  für alle dort genannten Event-Typen AUSSER `snapshot`.
- **`run`-Span-Grenze** nach vorentschiedenem Punkt E1 (siehe unten).
- **Behaviour-Erhalt** als bindender Vertrag inklusive Regressionstests.
- **Dry-Run** schreibt ein vollständiges Event-Log (0 Tokens), obwohl seine
  Agent-, Codex- und Forge-Aufrufe gemockt sind.

### Betroffene Instrumentierungs-Punkte (GUI-SPEC §6, ohne `snapshot`)

| Datei | Ort | Event-Typen |
| --- | --- | --- |
| `cli.py` | `run`/`resume`/`approve` Eintritt/Austritt | `run` start/end, `approval` |
| `phases.py` | jede Phase-Funktion Eintritt/Austritt | `phase` start/end |
| `phases.py` | `_reviewed_authoring_loop` | `round`, `codex.review`, `artifact` |
| `phases.py` | `_draft_stage`, `_claude_draft`, `_codex_draft` | `agent.run`, `artifact` |
| `phases.py` | `_run_lane`, `_run_lane_gates` | `lane`, `round`, `commit` |
| `phases.py` | `_confirm_red`, `_run_test_only_pass`, `_require_red_tests` | `red.check` |
| `phases.py` | `escalate()`, Limit- und Circuit-Breaker-Checks | `escalation`, `limit.hit`, `circuit_breaker` |
| `phases.py` | Integration/Merge, `_record_followup` | `merge`, `followup` |
| `agents.py` | `SdkAgentRunner.run` / `_collect` | `agent.run` start/end, `agent.message`, `agent.tool.call`, `agent.tool.result` |
| `gates.py` | `run_gates` je Gate | `gate` start/end |
| `codex.py` | Review-Subprozess | `codex.review` start/end |
| `triage.py` | Entscheidungsfunktion | `triage.decision` |
| `ci.py` / `github.py` | Poll-Schleife | `ci.wait`, `ci.poll`, `ci.reentry` |
| `state.py` | `save`/`update` | `state.saved` |
| überall | gespiegelte `logger.warning` des Orchestrators | `log` |

Die `snapshot`-Zeile aus §6 (und die zugehörigen `snapshot`-Points bei
`_run_lane`/`_confirm_red`/`_run_lane_gates`) sind hier NICHT enthalten —
Schritt 5, eigener Lauf.

Der tiefste Eingriff ist `agents.py:SdkAgentRunner._collect()`: dort werden
`ToolUseBlock`, `ToolResultBlock`, `AssistantMessage.usage`,
`ResultMessage.total_cost_usd` und `model_usage` ins Log gespiegelt.

## Non-Goals (explizite Scope-Deckel aus dem Issue)

- Keine Änderung an `adw/events.py`. Reicht die öffentliche Emitter-API nicht,
  ist das ein Befund für den Bericht, keine Erweiterung im Vorbeigehen.
- Kein `snapshot`-Event, keine Snapshots, keine git-Refs, kein Schritt-Diff.
- Kein Reader, kein Span-Baum-Modell, keine GUI, kein FastAPI, keine Registry,
  kein `adw gui`.
- Kein `trace:`-Key in `adw/config.py`, kein An-/Abschalten per Config, kein
  `adw runs list`/`adw runs prune`, keine Kompression, keine Retention. Der
  Emitter ist immer aktiv, sobald ein Run-Verzeichnis existiert.
- Keine Redaction, keine Kappung von Payloads.
- Keine Erweiterung von `RunState`, keine neuen Persistenzzustände außer
  `events.jsonl`.
- Keine neuen Laufzeit-Dependencies.
- KEIN Refactoring von `phases.py`: keine Funktion aufteilen, umbenennen oder
  umsortieren — nur emit-Aufrufe ergänzen und den Emitter durchreichen.
- Kein Fix des offenen P2-Follow-ups aus Lauf 1 (Race in `_safe_span_id`),
  keine neue API zur Übergabe von Parent-Spans über Thread-Grenzen.

## Vorentschiedene Punkte (nicht erneut verhandeln)

**E1 — Grenze des `run`-Spans.** Der `run`-Span umschließt den gesamten
Lebenszyklus des CLI-Kommandos ab dem frühestmöglichen Punkt (Run-Identität
steht fest UND der eine Emitter ist erzeugt) bis zum Kommando-Ende, bei JEDEM
Ausgang (`done`, `awaiting_approval`, Eskalation, unerwartete Exception).
- `adw run`: nach `RunState.new(...)` und VOR dem ersten `state.save(repo)` in
  `cli.py:run()`. Das erste `state.saved`-Event liegt damit INNERHALB des
  `run`-Spans.
- `adw resume` / `adw approve`: nach dem Laden des States und der
  Emitter-Erzeugung, vor der ersten Persistenz-Operation bzw. vor dem
  `approval`-Event.
- Der Span umschließt NICHT nur `_execute(ctx)`.
- `_load_config`, `_fetch_gitlab_issue`, `_fetch_github_issue` liegen davor
  und außerhalb; ein Fehlschlag dort erzeugt keinen Run und kein Event-Log.

**E2 — Parallele Lane-Spans tragen `parent: null`.** `_run_lane` läuft bei
`--parallel` in ThreadPoolExecutor-Workern; `EventEmitter.span()` leitet
`parent` aus einem thread-lokalen Stack ab. Die Zuordnung eines Lane-Spans zu
seiner Phase erfolgt über die Felder `phase` und `lane`, nicht über `parent`.
`adw/events.py` wird dafür nicht erweitert; die fehlende
Cross-Thread-Parent-API ist Befund für den Bericht. Bei Single-Lane-Läufen
läuft `_run_lane` im Hauptthread; dort verschachtelt sich alles regulär.

Ein Review-Finding gegen E1 oder E2 wird mit Verweis auf diesen Abschnitt
abgewiesen und dokumentiert.

## Acceptance Criteria

Jedes Kriterium beschreibt beobachtbares Produktverhalten und ist per Test
prüfbar.

1. **Dry-Run-Log rekonstruiert den Kontrollfluss.** `uv run adw run --dry-run`
   erzeugt `.adw/runs/<run_id>/events.jsonl`, aus dem sich der vollständige
   Ablauf aller sieben Phasen rekonstruieren lässt. Ein Test läuft den
   Span-Baum ab und prüft Phasenreihenfolge und Loop-Runden. (GUI-SPEC §10.1)

2. **Vollständige Typ-Abdeckung.** Jeder Event-Typ aus §4.4 AUSSER `snapshot`
   wird mindestens einmal emittiert — durch den Dry-Run-E2E-Test oder einen
   gezielten Unit-Test. Ein `snapshot`-Event wird nirgends emittiert.
   (GUI-SPEC §10.2)

3. **Payload-Treue.** Für jeden emittierten Typ enthält das Event genau die in
   §4.4 für `start`/`end`/`point` genannten Payload-Felder (u. a. `run`-start
   `issue`/`parallel`/`dry_run`/`repo`/`base_branch`/`adw_version`/`lanes[]`
   und `run`-end `status`/`totals`; `agent.run`-start `prompt`/`system_append`
   und -end `session_id`/`result_text`/`usage`/`cost_usd`/`is_error`;
   `gate`-end `passed`/`exit_code`/`timed_out`/`output`;
   `codex.review`-end `findings[]`/`raw_stdout`/`parse_ok`).

4. **`_collect()` ist bit-identisch.** Für denselben (gemockten) SDK-Stream
   liefert `SdkAgentRunner._collect()` mit aktivem Emitter und mit
   `NoOpEmitter` bit-identische Ergebnisse — gleiches `AgentResult` (Text,
   Session-ID), gleiche `AgentRunError`-Semantik. Ein Regressionstest weist
   das nach. (Issue Aufgabe 4, GUI-SPEC §10.5)

5. **Emit ist wirkungsfrei auf den Kontrollfluss.** Kein emit-Aufruf ändert
   einen Rückgabewert, verzweigt den Kontrollfluss abhängig vom Emit-Ergebnis
   oder löst eine Exception aus bzw. verschluckt eine. Fail-open ist Sache des
   realen `EventEmitter` (Backstop aus Lauf 1: einmal warnen, dann für den
   Rest des Runs deaktivieren) — die Aufrufstellen bauen dafür KEINE eigene
   try/except-Härtung und müssen keine Emitter-Implementierung tolerieren,
   die diesen Vertrag verletzt. Regressionstest: ein Run mit realem
   `EventEmitter` und induziertem Schreibfehler (unbeschreibbarer Pfad bzw.
   Disk-full-Simulation) läuft mit unveränderter Semantik durch. (Issue
   Aufgabe 4, GUI-SPEC §4.3, §10.4)

6. **Genau ein Emitter je Run und Prozess.** Der Emitter wird einmal beim
   Run-Start erzeugt und durchgereicht; nirgends existiert eine zweite
   Konstruktion für denselben Run. Als Folge gilt die Fail-open-Garantie
   „genau eine Warnung pro Run" (GUI-SPEC §4.3): ein defektes Log erzeugt über
   den ganzen Run höchstens ein `logger.warning`.

7. **`run`-Span-Grenze nach E1.** Über einen `run`-Span-Anfang und ein
   `run`-Span-Ende ist der Lauf beobachtbar, und zwar bei jedem Ausgang. Das
   `status`-Feld des End-Events bleibt im §4.4-Wertebereich und wird
   deterministisch abgebildet: regulärer Abschluss → `done`,
   `AwaitingApproval` → `awaiting_approval`, jeder andere Ausgang —
   `EscalationError`, `AgentRunError` und unerwartete Exceptions — →
   `escalated`. Dieses Status-Feld klassifiziert nur den Kommando-Ausgang im
   Log; `RunState.phase` und damit die Resume-Fähigkeit (z. B. nach
   `AgentRunError`) bleiben unberührt. Bei einem Exception-Ausgang wird das
   End-Event vor dem Weiterreichen emittiert; die Exception selbst propagiert
   unverändert (gleicher Exit-Code, gleiche Traceback-Semantik). Für
   `adw run` liegt das erste `state.saved`-Event innerhalb dieses Spans; für
   `adw resume`/`adw approve` beginnt der Span vor der ersten
   Persistenz-Operation bzw. vor dem `approval`-Event. Config- und
   Issue-Beschaffung, die vor der Run-Identität scheitern, legen kein
   Event-Log an.

8. **Additiv kompatible Signaturen.** `adw/agents.py`, `adw/gates.py`,
   `adw/codex.py`, `adw/ci.py`, `adw/github.py`, `adw/triage.py`,
   `adw/state.py` nehmen den Emitter ausschließlich als NEUEN, optionalen
   Parameter mit Default `NoOpEmitter()` entgegen. Kontraktgepinnt ist
   additive Kompatibilität, nicht wörtliche Signatur-Identität: alle
   bestehenden Parameter behalten Name, Reihenfolge und Defaults; jede vor
   der Änderung gültige Aufrufform (positional wie keyword, ohne Emitter)
   bleibt gültig und liefert unveränderte Ergebnisse, Seiteneffekte und
   Exception-Semantik. Der Kontrakttest ruft repräsentative bestehende
   Aufrufformen ohne Emitter auf und weist unverändertes Verhalten nach.

9. **Events beschreiben nur eingetretene Zustände.** Commit-, Merge-,
   Triage-, Approval-, Eskalations-, Artefakt- und Follow-up-Events
   protokollieren ausschließlich tatsächlich eingetretene Produktzustände;
   die Instrumentierung löst keinen davon aus. `state.saved` wird erst nach
   erfolgreicher Persistenz emittiert und trägt deren `seq` und `phase`;
   `state.json` bleibt alleinige Resume-Autorität.

10. **Keine neuen Persistenzzustände.** Der Lauf legt außer `events.jsonl`
    keinen neuen persistenten Zustand an; `RunState` wird nicht erweitert,
    keine neuen Laufzeit-Dependencies, kein Konfigurationsschalter — der
    Emitter ist aktiv, sobald ein Run-Verzeichnis existiert.

11. **`adw/events.py` bleibt unverändert.** Die Emitter-Datei wird nur
    importiert und aufgerufen, nicht editiert.

12. **Bestehende Tests bleiben grün.** Die 519 bestehenden Tests bleiben grün,
    ohne inhaltlich angepasst zu werden.

## Deferred (bewusst nicht gebaut)

Diese Ideen sind defensibel, aber in diesem Lauf außerhalb des Scope. Ein
Review-Finding, das einen dieser Punkte als Akzeptanzkriterium einführen will,
wird mit Verweis auf diesen Abschnitt abgewiesen und dokumentiert — nicht
umgesetzt. (In Lauf 1 sind auf genau diesem Weg zwei zurückgestellte
Mechanismen doch eingebaut worden; das wiederholt sich nicht.)

- **`snapshot`-Event, Snapshots, git-Refs, Schritt-Diff** (GUI-SPEC §5,
  Schritt 5).
- **Cross-Thread-Parent-API** für parallele Lane-Spans (E2): dass Lane-Spans
  unter `--parallel` `parent: null` tragen, ist akzeptiert; die fehlende API
  ist Befund für den Bericht, keine Emitter-Erweiterung.
- **Fix des `_safe_span_id`-Race** (P2-Follow-up aus Lauf 1) — eigener
  Bugfix-Lauf.
- **`trace:`-Config-Sektion**, An-/Abschalten per Config, Retention,
  `adw runs list` / `adw runs prune`, gzip.
- **Redaction / Kappung** von Prompts, Ausgaben, Tool-Payloads und sonstigen
  Event-Inhalten.
- **Reader, Span-Baum-Modell, GUI, FastAPI, Registry, `adw gui`, i18n, SSE,
  Timeline, Diff-Endpoint** (GUI-SPEC §7 ff.).
- **Jede Erweiterung der öffentlichen Emitter-API**: reicht sie nicht, ist das
  ein Befund, keine stille Ergänzung.
- **Weitergehende Härtungsmechanismen**, die nicht einen durch diese
  Instrumentierung konkret verursachten Schaden beheben.

## Definition of Done

- Alle Acceptance Criteria erfüllt und durch Tests belegt; darunter
  verpflichtend der `_collect()`-Regressionstest (AC 4), der Fail-open-Test
  mit realem `EventEmitter` und induziertem Schreibfehler (AC 5) und der
  Dry-Run-Span-Baum-Test (AC 1).
- Richtwert Testzahl rund 20–28 neue Tests; deutlich mehr ist ein
  Scope-Drift-Signal.
- Der Diff von `phases.py` beschränkt sich auf ergänzte emit-Aufrufe und das
  Durchreichen des Emitters — keine aufgeteilten, umbenannten oder
  umsortierten Funktionen.
- Gates grün (Toolchain dieses Projekts, E3):
  - `uv run ruff check .`
  - `uv run pytest -x -q`
