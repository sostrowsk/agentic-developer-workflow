# Plan — Instrumentierung des Orchestrators mit dem Event-Emitter

Umsetzung der Spec `.adw/spec.md` (GUI-SPEC §11, Schritte 2–4). Single-Lane-
Projekt: es existiert nur die Workstream **backend**. Der in Lauf 1 gebaute
Emitter `adw/events.py` (`EventEmitter`, `NoOpEmitter`) wird verdrahtet und
aufgerufen — nicht editiert, nicht erweitert. Massgeblich bei Widerspruch:
GUI-SPEC §4.3/§4.4/§6. Beobachtbare Flaeche und additive Signaturen sind in
`.adw/contract.yaml` gepinnt; beide gelten fuer diesen Plan bindend.

Leitplanken (aus Spec/Issue, hier operativ):
- KEIN Refactoring von `phases.py`: keine Funktion aufteilen, umbenennen oder
  umsortieren — nur emit-Aufrufe ergaenzen und den Emitter durchreichen.
- Genau EINE Emitter-Instanz je Run und Prozess; keine zweite Konstruktion.
- Fail-open ist Sache des realen `EventEmitter`; Aufrufstellen bauen KEINE
  eigene try/except-Haertung.
- Events beschreiben nur eingetretene Produktzustaende (AC 9): `approval`,
  `artifact`, `commit`, `merge`, `followup`, `escalation`, `triage.decision`
  werden erst NACH dem tatsaechlichen Eintritt des Zustands emittiert; die
  Instrumentierung loest keinen davon aus.
- **Span-Zuordnung von Point-Events:** `EventEmitter.emit()` leitet den
  aktiven Span NICHT selbst ab (nur `span()` nutzt den thread-lokalen Stack,
  und nur fuer `parent`). Jeder Point-emit uebergibt daher EXPLIZIT die ID des
  umgebenden Spans: `emit(type, payload, span=<SpanHandle.id>, ...)`. Kein
  Point-Record traegt `span: null` (GUI-SPEC §4.2: „ID of the span this event
  belongs to"). `adw/events.py` wird dafuer nicht angefasst — die vorhandene
  `span=`-Keyword-Uebergabe reicht.
- **End-Payloads auch bei Exception-Ausgang vollstaendig:** `span()` schreibt
  das End-Event beim Exception-Unwinding selbst — mit dem dann aktuellen
  `handle.end_payload`. Deshalb wird DIREKT nach Span-Start das vollstaendige
  End-Payload mit den deterministischen Defaults aus dem Kontrakt
  (`payload_end_on_exception`) initialisiert und bei Fortschritt/Erfolg
  ueberschrieben. Kein zusaetzliches try/except, kein veraenderter
  Kontrollfluss — die Exception propagiert unveraendert.
- Richtwert 20–28 neue Tests; deutlich mehr ist Scope-Drift-Signal.

Betroffene Produktionsdateien: `adw/cli.py`, `adw/phases.py`, `adw/agents.py`,
`adw/gates.py`, `adw/codex.py`, `adw/ci.py`, `adw/github.py`, `adw/triage.py`,
`adw/state.py`. Unveraendert bleiben `adw/events.py`, `adw/config.py`,
`RunState` (keine neuen Felder) und die Projekt-Dependencies.

---

## Workstream: backend

Reihenfolge folgt GUI-SPEC §11 (tiefster Wert zuerst), damit der Emitter Stufe
fuer Stufe integriert und getestet wird. Jede Stufe ergaenzt nur additive,
optionale Parameter und emit-Aufrufe.

### B0 — Emitter-Konstruktion und Durchreichung (Verdrahtung)

- `RunContext` (`adw/phases.py`) erhaelt ein Feld `emitter` (Typ
  `EventEmitter | NoOpEmitter`). Kein weiteres Feld, keine RunState-Erweiterung.
- Die eine Emitter-Instanz wird in `cli.py` erzeugt und via `_build_context`
  in den `RunContext` gelegt (Kontrakt `single_emitter_per_run`):
  - `adw run`: nach `RunState.new(...)`, VOR dem ersten `state.save(repo)` (E1).
  - `adw resume`/`adw approve`: nach dem State-Laden, vor der ersten Persistenz
    bzw. vor dem `approval`-Event.
- Die Module ohne RunContext-Kenntnis — `adw/agents.py` (`SdkAgentRunner`),
  `adw/gates.py` (`run_gates`), `adw/codex.py` (`CodexRunner.review`),
  `adw/ci.py`, `adw/github.py`, `adw/triage.py`, `adw/state.py`
  (`save`/`update`) — bekommen den Emitter als NEUEN, OPTIONALEN Parameter mit
  Default `NoOpEmitter()`. Alle bestehenden Parameter behalten Name,
  Reihenfolge, Default (Kontrakt `additive_emitter_param`, AC 8).
- **Span-ID-Durchreichung fuer Point-only-Module:** `state.py` und `triage.py`
  emittieren Points, halten aber selbst kein `SpanHandle` — sie bekommen
  zusaetzlich die umgebende Span-ID als zweiten additiven, optionalen
  Parameter (`span_id: str | None = None`), den die Aufrufstellen in
  `cli.py`/`phases.py` aus ihrem lokalen Handle befuellen. Die Span-oeffnenden
  Module (`agents.py`, `gates.py`, `codex.py`, `ci.py`, `github.py`) brauchen
  das nicht: ihr Handle ist lokal verfuegbar, die Parent-Verkettung ihrer
  Spans laeuft ueber den thread-lokalen Stack des Emitters.
- `phases.py` reicht `ctx.emitter` an diese Module durch.

### B1 — `agents.py`: SDK-Stream spiegeln (tiefster Eingriff)

- `SdkAgentRunner.run` oeffnet den `agent.run`-Span und reicht dessen
  `SpanHandle` an `_collect()`; jeder Stream-Point wird mit
  `span=<agent.run-Handle.id>` emittiert. Gespiegelt wird je Stream-Nachricht:
  `AssistantMessage`-Textblock → `agent.message`,
  `ToolUseBlock` → `agent.tool.call`, `ToolResultBlock` → `agent.tool.result`;
  aus `AssistantMessage.usage`/`ResultMessage.total_cost_usd`/`model_usage` das
  `usage`/`cost_usd` im `agent.run`-End-Event.
- Start-Payload: `agent, model, tools, allowed_tools, cwd, resume_session,
  prompt (volle Task-Zeichenkette), system_append`.
  End-Payload: `session_id, result_text, usage, cost_usd, is_error` — ohne
  Kuerzung uebernommen (keine Redaction/Kappung, Spec Non-Goals).
- **Bit-Identitaet (AC 4):** `_collect()` gibt mit Emitter und mit
  `NoOpEmitter` dasselbe `AgentResult` und dieselbe `AgentRunError`-Semantik
  zurueck. Das Spiegeln liest nur, veraendert weder Rueckgabe noch Kontrollfluss;
  Emit-Ergebnisse werden nirgends ausgewertet.

### B2 — `gates.py`, `codex.py`, `state.py`

- `gates.py:run_gates`: `gate`-Span je tatsaechlich gestartetem Gate. Start
  `name, cmd, timeout, cwd`; End `passed, exit_code, timed_out, output` (volle
  Gate-Ausgabe). End-Payload-Init direkt nach Span-Start: `passed: false,
  exit_code: null, timed_out: false, output: ""` — der regulaere Timeout-Pfad
  ueberschreibt mit `timed_out: true`, nur eine unerwartete Exception laesst
  die Defaults stehen.
- `codex.py:CodexRunner.review`: `codex.review`-Span um den Subprozess. Start
  `kind, argv, cwd, custom_prompt`; End `findings[] (volle Finding-Objekte),
  raw_stdout, parse_ok`. End-Payload-Init: `findings: [], raw_stdout: ""
  (bzw. soweit erfasst), parse_ok: false`.
- `state.py:save`/`update`: `state.saved`-Point ERST nach erfolgreicher
  Persistenz, mit `seq` (RunState.seq) und `phase` (AC 9), emittiert mit der
  vom Aufrufer durchgereichten umgebenden Span-ID (B0). `state.json` bleibt
  alleinige Resume-Autoritaet.

### B3 — `ci.py` / `github.py` / `triage.py`

- Poll-Schleifen (`ci.py:poll_pipeline`, `github.py:poll_ci`): `ci.wait`-Span
  (Start `provider, pipeline_ref`; End `status, polls, duration`), je
  tatsaechlich ausgefuehrtem Poll ein `ci.poll`-Point (`provider, status,
  job`) mit `span=<ci.wait-Handle.id>`, bei tatsaechlichem Wiedereintritt
  `ci.reentry` (`n, reason`) mit dem an der Aufrufstelle verfuegbaren
  umgebenden Handle. Werte stammen aus den bestehenden Ablaeufen, nichts wird
  zusaetzlich abgefragt. End-Payload-Init: `status: "aborted", polls: 0,
  duration: 0`; `polls`/`duration` (verstrichene Zeit aus der ohnehin
  vorhandenen Timeout-Buchhaltung) werden je Poll-Iteration fortgeschrieben;
  vor einem `CiTimeoutError` wird `status: "timeout"` gesetzt, der regulaere
  Abschluss ueberschreibt mit dem CI-Endstatus.
- `triage.py:triage_final_review`: je Entscheidung ein `triage.decision`-Point
  (`finding_key, severity, action, reason`), emittiert mit der vom Aufrufer
  durchgereichten umgebenden Span-ID (B0) — protokolliert nur tatsaechlich
  getroffene Entscheidungen (AC 9).

### B4 — `phases.py`: Spans und Points (nur additive emit-Aufrufe)

- `phase`-Span je Phase-Funktion (Eintritt/Austritt): Start `name, from_phase`;
  End `name, to_phase` — mit den tatsaechlichen Phasenwerten. End-Payload-Init:
  `name: <Startwert>, to_phase: null` — bei Exception-Ausgang bleibt
  `to_phase: null` (kein Uebergang eingetreten; `RunState.phase` unberuehrt).
- `_reviewed_authoring_loop`: `round`-Span (`loop=authoring|codex_review`, `n`,
  `cap`; End `outcome`, Init `outcome: "aborted"`), `codex.review` (via B2),
  `artifact` erst NACH erfolgreichem Artefakt-Write (`name, path, bytes,
  sha256`).
- `_draft_stage`, `_claude_draft`, `_codex_draft`: `agent.run` (via B1) und
  `artifact` — Dual-Authoring macht beide Drafts + Synthese einzeln sichtbar.
- `_run_lane`, `_run_lane_gates`: `lane`-Span (Start `name, branch, worktree,
  base_sha, ports`; End `completed, gate_iterations, fix_cycles` aus dem
  tatsaechlichen Lane-State; Init `completed: false, gate_iterations: 0,
  fix_cycles: 0`, Zaehler werden bei Fortschritt fortgeschrieben),
  `round`-Span (`loop=gates`, Init `outcome: "aborted"`), `commit`-Point
  (`lane, sha, subject`) erst nach erfolgtem Commit.
  E2: Bei `--parallel` laufen `_run_lane`-Spans in ThreadPool-Workern und tragen
  `parent: null`; die Zuordnung erfolgt ueber `phase`/`lane`. `adw/events.py`
  wird dafuer NICHT erweitert (Befund fuer den Bericht). Single-Lane laeuft im
  Hauptthread; dort verschachtelt sich alles regulaer.
- `_confirm_red`, `_run_test_only_pass`, `_require_red_tests`: `red.check`-Point
  (`confirmed, test_paths, gates`) am tatsaechlichen Pruefergebnis.
- `escalate()`, Limit-/Circuit-Breaker-Checks: `escalation` (`reason, phase`),
  `limit.hit` (`limit, value, cap`), `circuit_breaker` (`keys, scope`) —
  unmittelbar an den bestehenden Entscheidungsstellen, nur fuer tatsaechlich
  eingetretene Faelle.
- Integration/Merge, `_record_followup`: `merge` (`lane, target, conflicts`)
  und `followup` (`finding_key, text`) erst nach eingetretenem Produktzustand.
- Gespiegelte `logger.warning` des Orchestrators → `log` (`level, message`),
  ohne Logging-Verhalten oder Kontrollfluss zu aendern.
- **Span-Zuordnung aller Points in `phases.py`/`cli.py`:** jeder Point-emit
  (`red.check`, `commit`, `merge`, `escalation`, `limit.hit`,
  `circuit_breaker`, `artifact`, `followup`, `approval`, `log`) uebergibt
  `span=<Handle.id>` des an der Aufrufstelle innersten offenen Spans
  (`run`/`phase`/`lane`/`round`) — die Handles sind dort lokal im Scope.
- KEIN `snapshot`-Event und keine `snapshot`-Points (Schritt 5).

### B5 — `cli.py`: `run`-Span und `approval`

- `run`-Span um den gesamten CLI-Lebenszyklus nach E1, bei JEDEM Ausgang
  (`done`, `awaiting_approval`, Eskalation, unerwartete Exception). Er umschliesst
  NICHT nur `_execute(ctx)`.
- Start-Payload: `issue, parallel, dry_run, repo, base_branch, adw_version,
  lanes[]`. End-Payload: `status, totals`.
- Status-Mapping (AC 7, Kontrakt `run_span.status_mapping`): regulaerer
  Abschluss → `done`; `AwaitingApproval` → `awaiting_approval`; `EscalationError`,
  `AgentRunError` und unerwartete Exceptions → `escalated`. Bei Exception-Ausgang
  wird das End-Event VOR dem Weiterreichen emittiert; die Exception propagiert
  unveraendert (gleicher Exit-Code, gleiche Traceback-Semantik). Der Status
  klassifiziert nur den Log-Ausgang; `RunState.phase`/Resume bleiben unberuehrt.
- `approval`-Point (`gate=spec|plan`, `event=awaited|granted`) an den
  Approval-Stellen — nur fuer tatsaechlich eingetretenes Warten bzw. eine
  tatsaechlich erteilte Freigabe (AC 9).
- Fehler VOR feststehender Run-Identitaet (`_load_config`,
  `_fetch_gitlab_issue`, `_fetch_github_issue`) erzeugen weiterhin weder
  Run-Verzeichnis noch Event-Log.

### B6 — Dry-Run als Abnahmepfad

- Der bestehende Dry-Run laeuft ueber DIESELBEN Produktions-Instrumentierungs-
  pfade: trotz gemockter Agent-, Codex- und Forge-Aufrufe entsteht ein
  vollstaendiges `events.jsonl` mit allen sieben Phasen, Loops und Points.
- Usage und Kosten des Dry-Runs bleiben bei null (0 Tokens).
- Kein `snapshot`-Event, kein weiterer Persistenzzustand ausser `events.jsonl`.

---

## Tests (Richtwert 20–28)

Pflicht-Regressionstests (Definition of Done):
1. **Dry-Run-Span-Baum (AC 1).** `uv run adw run --dry-run` erzeugt
   `events.jsonl`; ein Test laeuft den Span-Baum ab und prueft Phasenreihenfolge
   (alle sieben Phasen) und Loop-Runden.
2. **`_collect()` bit-identisch (AC 4).** Fuer denselben gemockten SDK-Stream
   liefern `EventEmitter` und `NoOpEmitter` dasselbe `AgentResult` und dieselbe
   `AgentRunError`-Semantik.
3. **Fail-open mit realem `EventEmitter` (AC 5).** Ein Run mit induziertem
   Schreibfehler (unbeschreibbarer Pfad bzw. Disk-full-Simulation) laeuft mit
   unveraenderter Semantik durch; die Aufrufstellen tragen keine eigene
   try/except-Haertung.

Weitere gezielte Tests:
4. **Typ-Abdeckung (AC 2).** Jeder §4.4-Typ ausser `snapshot` wird mindestens
   einmal emittiert (Dry-Run-E2E oder Unit); `snapshot` erscheint nie.
5. **Payload-Treue (AC 3).** Je Typ enthaelt das Event genau die §4.4-Felder
   fuer `start`/`end`/`point` (Stichproben u. a. `run`-start/-end, `agent.run`-
   start/-end, `gate`-end, `codex.review`-end).
6. **Additive Signaturen (AC 8).** Repraesentative bestehende Aufrufformen ohne
   Emitter (positional wie keyword) liefern unveraendertes Verhalten.
7. **Ein Emitter je Run (AC 6).** Ein defektes Log erzeugt ueber den ganzen Run
   hoechstens ein `logger.warning`.
8. **`run`-Span-Grenze/Status (AC 7).** Je Ausgang (`done`,
   `awaiting_approval`, Eskalation, `AgentRunError`, unerwartete Exception)
   genau ein `run`-Start und ein `run`-Ende mit korrektem `status`; Exception
   propagiert unveraendert, Resume-Semantik unberuehrt.
9. **`state.saved` nach Persistenz (AC 9).** Event traegt `seq`/`phase` und
   erscheint erst nach erfolgreichem Save.
10. **Keine neuen Persistenzzustaende (AC 10/11).** Ausser `events.jsonl` kein
    neuer Zustand; `adw/events.py` bleibt unveraendert (Import-only).
11. **Span-Zuordnung von Points.** Ein Test laeuft das Dry-Run-Log ab und weist
    nach, dass jedes Point-Event die `span`-ID seines tatsaechlich umgebenden
    Spans traegt (`agent.*` → `agent.run`, `ci.poll` → `ci.wait`,
    `state.saved`/`red.check`/`commit`/… → der jeweils innerste offene
    `run`/`phase`/`lane`/`round`-Span); kein Point-Record hat `span: null`.
12. **End-Payloads bei Exception-Ausgang.** Repraesentative Fehlerpfade —
    ein Gate mit unerwarteter Exception bzw. Timeout, ein `ci.wait` mit
    `CiTimeoutError`, eine Phase/Loop-Runde mit propagierender Exception —
    erzeugen End-Records mit allen Pflichtfeldern und den im Kontrakt
    gepinnten deterministischen Werten (`payload_end_on_exception`); die
    Exception propagiert unveraendert.

Die 519 bestehenden Tests bleiben grün, ohne inhaltlich angepasst zu werden
(AC 12).

---

## Definition of Done

- Alle Acceptance Criteria der Spec erfuellt und durch Tests belegt; darunter
  verpflichtend der `_collect()`-Regressionstest (AC 4), der Fail-open-Test mit
  realem `EventEmitter` und induziertem Schreibfehler (AC 5) und der
  Dry-Run-Span-Baum-Test (AC 1).
- Richtwert Testzahl rund 20–28 neue Tests; deutlich mehr ist ein
  Scope-Drift-Signal.
- Der Diff von `phases.py` beschraenkt sich auf ergaenzte emit-Aufrufe und das
  Durchreichen des Emitters — keine aufgeteilten, umbenannten oder umsortierten
  Funktionen.
- Gates grün (Toolchain dieses Projekts, E3):
  - `uv run ruff check .`
  - `uv run pytest -x -q`

---

## Deferred (bewusst nicht gebaut)

Diese Ideen sind defensibel, aber in diesem Lauf ausserhalb des Scope. Ein
Review-Finding, das einen dieser Punkte als Akzeptanzkriterium einfuehren will,
wird mit Verweis auf diesen Abschnitt abgewiesen und dokumentiert — nicht
umgesetzt. (In Lauf 1 sind auf genau diesem Weg zwei zurueckgestellte
Mechanismen doch eingebaut worden; das wiederholt sich nicht.)

- **`snapshot`-Event, Snapshots, git-Refs, Schritt-Diff** (GUI-SPEC §5,
  Schritt 5).
- **Cross-Thread-Parent-API** fuer parallele Lane-Spans (E2): dass Lane-Spans
  unter `--parallel` `parent: null` tragen, ist akzeptiert; die fehlende API
  ist Befund fuer den Bericht, keine Emitter-Erweiterung.
- **Fix des `_safe_span_id`-Race** (P2-Follow-up aus Lauf 1) — eigener
  Bugfix-Lauf.
- **`trace:`-Config-Sektion**, An-/Abschalten per Config, Retention,
  `adw runs list` / `adw runs prune`, gzip.
- **Redaction / Kappung** von Prompts, Ausgaben, Tool-Payloads und sonstigen
  Event-Inhalten.
- **Reader, Span-Baum-Modell, GUI, FastAPI, Registry, `adw gui`, i18n, SSE,
  Timeline, Diff-Endpoint** (GUI-SPEC §7 ff.).
- **Jede Erweiterung der oeffentlichen Emitter-API**: reicht sie nicht, ist das
  ein Befund, keine stille Ergaenzung.
- **Weitergehende Haertungsmechanismen**, die nicht einen durch diese
  Instrumentierung konkret verursachten Schaden beheben.
