# Plan — Snapshots als Schritt-Diff-Basis + zwei Nachzügler der Instrumentierung

Maßgeblich bleibt `docs/GUI-SPEC.md` (§4.4, §5, §6); bei Widerspruch gilt die
GUI-SPEC. Dieser Plan setzt `.adw/spec.md` um und baut strikt gegen
`.adw/contract.yaml`. Single-Lane: es gibt genau den Workstream **backend**.

Leitplanken:

- `adw/events.py` und die Grenze/Implementierung des `run`-Spans bleiben
  unverändert (E1, E2).
- Kein Refactoring von `phases.py`: bestehende Aufrufstellen werden nur mit
  Spans und Snapshot-Aufrufen umschlossen; keine Funktion wird aufgeteilt,
  umbenannt oder umsortiert.
- Stream-, Tool-, Usage- und Kosteninhalte bleiben runnerseitig; Mock-Runner
  erfinden keine solchen Inhalte.
- Snapshots sind fail-open und ändern Rückgabewerte, Exceptions und den
  fachlichen Orchestrator-Ablauf nicht.
- Keine neuen Laufzeit-Dependencies; keine neuen Persistenzzustände außer
  `events.jsonl` und `refs/adw/*`.

## Ausgangslage (verifiziert im Code)

- `cli.py:_run_span` behandelt den Exception-Ausgang bereits:
  `handle.end_payload = {"status": "escalated", "totals": totals()}` wird
  gesetzt, `span()` schreibt das End-Event in seinem `finally`, die Exception
  propagiert unverändert (`raise`). **Korrekt — bleibt unverändert (E1).**
- Die `agent.run`- und `codex.review`-**Spans** liegen heute in den Runnern:
  `agents.py:SdkAgentRunner.run` (`emitter.span("agent.run", …)`) und
  `codex.py:CodexRunner.review` (`emitter.span("codex.review", …)`). Die Mocks
  (`mock.py`) emittieren nichts — ein Dry-Run erzeugt daher heute keinen
  einzigen dieser Spans.
- `phases.py:_worktree_tree_hash()` zeigt exakt das temporäre-Index-Verfahren,
  das Aufgabe C wiederverwendet: `GIT_INDEX_FILE` auf eine Temp-Datei,
  `read-tree HEAD` → `add -A` → `write-tree`, realer Index unberührt.
- `base_sha` ist der auf `LaneState` gepinnte Fork-Point der Lane
  (`state.py: LaneState.base_sha`, gesetzt in `_run_lane`) — er, nicht der
  weiterrückende Base-Branch, ist der Parent der Snapshot-Commits.

## Workstream: backend

### Aufgabe A — Test für den Exception-Pfad des `run`-Spans

Reiner Testzuwachs, **keine** Implementierungsänderung.

A1. Test in der bestehenden `cli`/`_run_span`-Testsuite: einen Kontext bauen,
    dessen `run`-Span-Körper eine unerwartete Exception (z. B. `RuntimeError`)
    wirft, gegen einen echten `EventEmitter` (Temp-Repo). Belegen:
    - das `run`-End-Event trägt `payload.status == "escalated"`;
    - das End-Event wird VOR der Propagation geschrieben (es existiert in
      `events.jsonl`, obwohl die Exception den `with`-Block verlässt);
    - exakt dieselbe Exception-Instanz propagiert unverändert weiter
      (per `pytest.raises`, Identität/Typ/Message).
    Nutzt `_run_span` direkt (nicht den ganzen CLI-Lauf), damit der Test genau
    den spezifizierten Pfad trifft.

### Aufgabe B — `agent.run`/`codex.review`-Span an die Aufrufstelle verschieben

Die **Span-Klammer** wandert aus den Runnern in `adw/phases.py` an jede
Aufrufstelle; der **Inhalt** (Stream-Spiegelung, Usage/Kosten) bleibt in den
Runnern (E4). Bestehende Aufrufstellen werden ausschließlich umschlossen.

B1. `agents.py:SdkAgentRunner.run` / `_collect`: die eigene
    `emitter.span("agent.run", …)`-Klammer entfernen, sodass der Runner keinen
    Span mehr öffnet. `_collect` erhält weiter ein Span-Handle (mit `.id` und
    `.end_payload`) von der Aufrufstelle, sodass die Stream-Spiegelung
    (`agent.message`/`agent.tool.call`/`agent.tool.result`) und die
    End-Payload-Felder (`session_id`, `result_text`, `usage`, `cost_usd`,
    `is_error`) unverändert demselben Span zugeordnet werden. **Rückgabewerte
    von `_collect` und `run` sowie die `AgentRunError`-/Exception-Semantik
    bleiben unverändert (AC 12).** Wie das Handle an den Runner gereicht wird,
    ist Mechanik und NICHT im Kontrakt.
B2. `codex.py:CodexRunner.review`: analog die `emitter.span("codex.review",
    …)`-Klammer entfernen; das Befüllen von `findings`/`raw_stdout`/`parse_ok`
    erfolgt weiter im Runner in das von der Aufrufstelle gereichte Handle.
B3. In `phases.py` an JEDER `ctx.agents.run(...)`-Aufrufstelle den
    `agent.run`-Span öffnen — Start-Felder nach §4.4 aus den an der
    Aufrufstelle bekannten Werten: `agent`, `model`, `tools`, `allowed_tools`,
    `cwd`, `resume_session`, `prompt`, `system_append`. Aufrufstellen
    (verifiziert): `_claude_draft` (Draft-Stage, läuft im Pool-Worker —
    Span-Stack leer, `parent: null`, Zuordnung über `phase`/`lane`, E2),
    `_reviewed_authoring_loop`, der Gate-Loop in `_run_lane`,
    `_run_test_only_pass`, `_triage_e2e`, Final-Review, `_analyze_ci_logs`.
B4. In `phases.py` an JEDER `ctx.codex.review(...)`-Aufrufstelle den
    `codex.review`-Span öffnen — Start-Felder `kind`, `argv`, `cwd`,
    `custom_prompt`. Aufrufstellen (verifiziert): `_reviewed_authoring_loop`
    und die Codex-Code-Review-Phase. **`argv` muss VOR dem Öffnen des Spans
    vollständig vorliegen** (das Start-Event wird beim Span-Öffnen
    geschrieben; ein nachträgliches Befüllen über das Handle erreicht es
    nicht). Dafür wird die bereits vorhandene, seiteneffektfreie
    argv-Konstruktion des Runners (`CodexRunner._build_prompt` +
    `CodexRunner._argv`, beides statisch) als geteilter Builder nutzbar
    gemacht (z. B. eine öffentliche Methode `effective_argv(kind,
    content_refs, cwd, context)` auf dem Runner-Protokoll); der echte Runner
    führt exakt dieses argv aus, der Mock liefert denselben Builder-Wert.
    Ein Test belegt, dass das serialisierte Start-Event das vollständige,
    vom Runner tatsächlich ausgeführte argv enthält (AC 3). Die vom
    (Mock-)Runner tatsächlich zurückgegebenen Ergebnisfelder werden immer
    erfasst. `ctx.codex.author(...)` in `_codex_draft` erhält KEINEN Span —
    §4.4 kennt keinen `author`-Event-Typ; AC 4 nennt ausschließlich
    `agent.run` und `codex.review`.
B5. Im Dry-Run fehlen weiterhin ausschließlich die runnerseitigen Inhalte:
    keine `agent.message`-/`agent.tool.*`-Events, keine synthetischen Usage-/
    Kostenwerte (Non-Goal). Die Start-Felder nach §4.4 und die vom Mock
    zurückgegebenen Ergebnisfelder (`session_id`, `result_text`, `is_error`
    bzw. `findings`, `raw_stdout`, `parse_ok`) werden dagegen immer erfasst.

Tests B:
- Dry-Run erzeugt ≥1 vollständig geschlossenen `agent.run`-Span (start+end,
  gleiche Span-ID) und ≥1 `codex.review`-Span; der Test prüft AUCH die
  Payload-Felder aus §4.4, nicht nur die Span-Schließung (AC 2/3).
- Jede Aufrufstelle ist von genau einem Span umschlossen, Mock wie Echt
  (AC 4) — z. B. Zählung der `agent.run`-Starts gegen die Zahl der
  Agent-Läufe eines Dry-Runs.
- Dry-Run enthält keine `agent.message`-/`agent.tool.*`-/Usage-/Kosten-
  Simulation; Echt-Runner behalten Stream-Spiegelung und Usage/Kosten —
  bestehende Runner-Tests bleiben grün, ggf. an das gereichte Handle
  angepasst (kein Verhaltenswechsel).
- Das `codex.review`-Start-Event enthält das vollständige argv, das der
  Runner tatsächlich ausführt (Builder-Wert == ausgeführtes argv, AC 3).

### Aufgabe C — `adw/snapshots.py` (Snapshots und Schritt-Diff-Basis)

C1. Neues Modul `adw/snapshots.py` mit öffentlicher Funktion
    `capture(ctx, worktree, label)`:
    1. **Tree** über temporären Index wie `phases.py:_worktree_tree_hash()`:
       eigene `GIT_INDEX_FILE` (Temp-Datei, danach entfernt),
       `read-tree HEAD` → `add -A` → `write-tree`; realer Index,
       Worktree-Dateien, aktueller Branch und `HEAD` bleiben unberührt.
    2. **Commit + Ref:** `git commit-tree <tree> -p <base_sha> -m "adw
       snapshot <label>"`, dann `git update-ref refs/adw/<run_id>/<seq>
       <commit>`. `base_sha` = der für die Lane gepinnte Fork-Point
       (`lane_state.base_sha`); die Ref hält die Objekte gegen `git gc`.
    3. **Event:** genau ein `snapshot`-Point-Event mit Payload `lane`, `tree`,
       `ref`, `label`, erst NACH vollständig erfolgreichem Capture; `tree`/
       `ref` entsprechen dem erzeugten Tree bzw. der gesetzten Ref.
C2. **`<seq>`-Vergabe (prozessübergreifend eindeutig, keine Überschreibung):**
    frische Sequenz aus den bereits existierenden `refs/adw/<run_id>/*`
    ermitteln (höchste vorhandene `<seq>` + 1); `update-ref` so, dass eine
    vorhandene Ref desselben Laufs nie überschrieben wird — auch nach `resume`
    in einem neuen Prozess. Kein Sidecar, kein `RunState`-Feld: die Refs
    selbst sind die Quelle (AC 7). Mechanik der git-Kommandos ist NICHT Teil
    des Kontrakts.
C3. **Fail-open** wie beim Emitter: jeder Fehlerpfad wird intern abgefangen —
    EINE Warnung, `capture` wirft nie, bricht keinen Lauf ab, ändert kein
    Verhalten und verdeckt/ersetzt keine bereits laufende fachliche Exception
    (AC 11). Die "keine Ref, kein Event"-Garantie gilt für Fehler VOR oder
    BEIM Setzen der Ref (scheiterndes git-Kommando: read-tree/add/write-tree,
    commit-tree, update-ref). Schlägt danach nur noch der Event-Append fehl,
    greift die ohnehin fail-open ausgelegte Emitter-Semantik: die bereits
    gesetzte Ref bleibt als nutzbarer Snapshot bestehen, es gibt KEIN
    Rollback und KEINE Recovery-Mechanik (siehe Deferred).
C4. **Snapshot-Punkte** in `phases.py` (nur Build-Lane, §5/§6), ausschließlich
    als eingefügte `snapshots.capture(...)`-Aufrufe — kein Umbau:
    - `_run_lane` Gate-Loop: `before_agent` unmittelbar VOR
      `ctx.agents.run(...)`, `after_agent` unmittelbar NACH normaler Rückkehr.
    - `_run_test_only_pass`: `before_agent` vor und `after_agent` nach dem
      Test-only-Agent-Lauf (auch dies ist ein Agent-Lauf der Build-Lane —
      §5: "before and after every agent run").
    - `red` NACH dem TDD-RED-Test-only-Lauf, an der Stelle im
      `_confirm_red`/`_run_test_only_pass`/`_require_red_tests`-Umfeld, an der
      der RED-Beweis feststeht.
    - `after_gates` nach JEDER Gate-Iteration, unabhängig vom Gate-Ausgang
      (grün wie rot).
    Authoring-/Review-Agent-Läufe (`_claude_draft`, `_reviewed_authoring_loop`,
    `_triage_e2e`, Final-Review, `_analyze_ci_logs`) erhalten Spans
    (Aufgabe B), aber KEINE Snapshots — §6 sieht dort keine `snapshot`-Events
    vor.
C5. **Exception-Semantik `after_agent`:** gilt nur für normal zurückkehrende
    Agent-Läufe. Der Aufruf liegt bewusst NICHT in einem `finally`- oder
    Unwinding-Pfad: wirft `ctx.agents.run(...)`, entfällt der
    `after_agent`-Snapshot samt Event, die Exception propagiert unverändert,
    der `before_agent`-Snapshot bleibt als Diff-Basis erhalten (AC 10). Kein
    Capture während des Stack-Unwindings (siehe Deferred).

Tests C (Richtwert einhalten, siehe Guardrail):
- Realer Temp-Repo: erfolgreicher `capture` erzeugt Tree+Commit; der Tree
  bildet tracked, geänderte, gelöschte und untracked Dateien ab, ignorierte
  nicht; realer Index, Worktree-Dateien, Branch und `HEAD` unverändert (AC 5).
- Ref `refs/adw/<run_id>/<seq>` existiert, zeigt auf einen Commit mit
  passendem Tree und `base_sha` als Parent (AC 6).
- Resume in neuem Prozess für einen Lauf mit vorhandenen Refs: frische
  Sequenzen, keine Überschreibung bestehender Refs (AC 7).
- Erfolgreicher Snapshot → genau ein `snapshot`-Event mit `lane`/`tree`/
  `ref`/`label`; `tree`/`ref` konsistent (AC 8).
- Lauf-weit: `snapshot`-Events erscheinen an den vier Punkten mit korrektem
  Label (`before_agent`, `after_agent`, `red`, `after_gates`); `after_gates`
  unabhängig vom Gate-Ausgang (AC 9).
- Agent-Lauf wirft → kein `after_agent`-Snapshot/-Event, Exception
  unverändert, `before_agent`-Snapshot bleibt (AC 10).
- Fehlgeschlagener Snapshot durch einen git-Fehler VOR oder BEIM Setzen der
  Ref (z. B. gemocktes scheiterndes `write-tree`/`commit-tree`/`update-ref`)
  → keine Ref, kein Event, eine Warnung, Lauf läuft unverändert weiter; eine
  bereits laufende fachliche Exception bleibt unangetastet (AC 11). AC 11s
  "keine Ref"-Garantie meint genau diesen Fall; ein Event-Append-Fehler nach
  gesetzter Ref bleibt fail-open ohne Rollback (siehe C3) und wird nicht als
  Rollback-Anforderung getestet.

### Regression

- `agents.py:_collect`-Rückgabe und der fachliche Orchestrator-Ablauf
  (Rückgabewerte, Fehlerbehandlung, Exception-Weitergabe, Limits,
  Gate-Auswertung, Commit-/Merge-Semantik) bleiben unverändert; die
  bestehenden Tests bleiben grün (AC 12).

## Guardrail Testumfang

Richtwert rund 15–22 neue Tests insgesamt für A, B und C. Deutlich mehr ist
ein Signal für Scope-Drift.

## Definition of Done

1. Alle Akzeptanzkriterien (Spec AC 1–12) sind durch Tests belegt.
2. `uv run ruff check .` ist grün.
3. `uv run pytest -x -q` ist grün (die bestehenden 562 Tests plus die neuen).
4. Keine neuen Laufzeit-Dependencies; keine neuen Persistenzzustände außer
   `events.jsonl` und `refs/adw/*`.
5. `adw/events.py`, der `run`-Span und der fachliche Orchestrator-Ablauf sind
   unverändert (außer dem in Aufgabe B erlaubten Umschließen der
   Aufrufstellen); der Diff in `phases.py` beschränkt sich auf das Umschließen
   vorhandener Runner-Aufrufe, die Handle-Übergabe und die vier
   Snapshot-Punkte. Die Non-Goals und Deferred-Punkte sind nicht Bestandteil
   des Produkts geworden.

## Deferred (bewusst nicht gebaut)

Folgende Ideen sind defensibel, aber für diesen Lauf disproportional oder
vorentschieden ausgeschlossen. Sie gehören **nicht** in Akzeptanzkriterien.
Ein Review-Finding, das einen dieser Punkte oder einen vorentschiedenen Punkt
(E1–E4) einführen will, wird mit Begründung abgewiesen, nicht umgesetzt.

- Reader, Span-Baum-Modell, GUI, FastAPI, Registry, `adw gui`, Diff-Endpoint,
  Diff-Berechnung und -Anzeige (Läufe 6+).
- Prunen/Retention der Refs, `adw runs prune`, `adw runs list`,
  `trace:`-Config (Lauf 5).
- Redaction, Kappung oder Patch-Texte im Event-Log.
- Cross-Thread-Parent-API in `adw/events.py` für parallele Lane-Spans (E2).
- Tool-/Stream-Simulation oder synthetische Usage-/Kostenwerte in den
  Mock-Runnern.
- Zusätzliche Snapshot-Metadaten, Sidecar-Dateien oder Snapshot-Status in
  `RunState`.
- Retry-/Recovery-Mechanik für fehlgeschlagene Snapshots; Warnung, Events und
  die vorhandenen Refs sind die vorgesehenen Backstops.
- `after_agent`-Capture während des Stack-Unwindings (Review-Finding,
  abgewiesen): Snapshot-Aufnahme im Exception-Pfad fügt am heikelsten Punkt
  Fehlerfläche hinzu; der `before_agent`-Snapshot und der bei Eskalation
  erhaltene Lane-Worktree sind die vorhandenen Backstops. Das Verhalten bei
  Exception ist stattdessen explizit spezifiziert (AC 10).
- Nebenläufigkeits-Garantien und Concurrent-Capture-Tests für parallele Lanes
  (Review-Finding, teilweise abgewiesen): Single-Lane-Projekt laut Kontrakt;
  die Eindeutigkeit über Prozessgrenzen (resume) ist dagegen übernommen
  (AC 7).
- Snapshots an weiteren Grenzen als den vier aus §5 (z. B. in Authoring-,
  Integrations- oder Review-Phasen).
- Codex-CLI-Volltranskript im Log (GUI-SPEC §12, v1.1).
