# Spec — Snapshots als Schritt-Diff-Basis + zwei Nachzügler der Instrumentierung

Maßgeblich ist `docs/GUI-SPEC.md`, insbesondere §4.4 (Event-Payloads), §5
(Snapshots und Schritt-Diffs) und §6 (Instrumentierungs-Punkte). Bei
Widerspruch zwischen dieser Spec und der GUI-SPEC gilt die GUI-SPEC.

## Goal

Dieser Lauf schließt Schritt 5 der Umsetzungsreihenfolge (GUI-SPEC §11) ab und
liefert drei Bausteine:

1. **A** — den fehlenden Test für den bereits gebauten Exception-Pfad des
   `run`-Spans (End-Event mit `status: escalated` vor der unveränderten
   Weiterpropagation), an dessen fehlender Abdeckung der vorige Lauf
   eskalierte.
2. **B** — Verschiebung der `agent.run`- und `codex.review`-**Spans** an die
   Aufrufstellen in `adw/phases.py`, damit auch der Dry-Run
   (0-Token-Abnahmepfad) diese Spans erzeugt und die spätere GUI dort
   Agent-Aktivität zeigt.
3. **C** (Hauptteil) — ein neues Modul `adw/snapshots.py`, das den
   Worktree-Stand an Schrittgrenzen als git-Ref festhält und so die Basis für
   spätere Schritt-Diffs schafft ("was hat dieser Schritt geändert?").

Dieser Lauf **erzeugt** die Diff-Basis; er zeigt keine Diffs an.

## Scope

### Aufgabe A — Test für den Exception-Pfad des `run`-Spans

- Ein neuer Test belegt das bestehende Verhalten von `cli.py:_run_span` bei
  einer unerwarteten Exception. Die Implementierung ist korrekt und wird
  **nicht** geändert (E1).

### Aufgabe B — `agent.run`- und `codex.review`-Span an die Aufrufstelle

- Die beiden Spans werden an die Aufrufstellen in `adw/phases.py` verschoben —
  jedes `ctx.agents.run(...)` und jedes `ctx.codex.review(...)`, einschließlich
  der Draft-Stellen (`_draft_stage`, `_claude_draft`, `_codex_draft`), wo Mock-
  und Echt-Runner gleichermaßen durchlaufen (GUI-SPEC §6, E4).
- Der **Inhalt** der Spans bleibt in den Runnern: die Spiegelung des
  SDK-Streams (`agent.message`, `agent.tool.call`, `agent.tool.result`) und die
  Usage-/Kosten-Felder im End-Payload liegen weiter in `adw/agents.py` bzw.
  `adw/codex.py`. Im Dry-Run fehlen **ausschließlich** diese runnerseitigen
  Inhalte zu Recht — Mocks haben keine Tool-Calls, und es werden keine
  Stream-Ereignisse oder Kostenwerte erfunden. Die an der Aufrufstelle
  bekannten Start-Felder nach §4.4 und die vom (Mock-)Runner tatsächlich
  zurückgegebenen Ergebnisfelder werden dagegen immer erfasst.
- Bestehende Aufrufstellen dürfen ausschließlich **umschlossen** werden. Kein
  Refactoring von `phases.py`.

### Aufgabe C — Snapshots und Schritt-Diff-Basis (GUI-SPEC §5)

- Neues Modul `adw/snapshots.py` mit öffentlicher Schnittstelle
  `capture(ctx, worktree, label)`, die den Worktree-Stand als git-Ref
  festhält:
  1. Über einen **temporären** git-Index (`read-tree HEAD` → `add -A` →
     `write-tree`) ein Tree-Objekt erzeugen — dasselbe Verfahren wie
     `phases.py:_worktree_tree_hash()`; der reale Index bleibt unberührt.
  2. `git commit-tree <tree> -p <base_sha> -m "adw snapshot <label>"`, dann
     `git update-ref refs/adw/<run_id>/<seq> <commit>` — die Ref hält die
     Objekte gegen `git gc` am Leben.
  3. Ein `snapshot`-Event mit den Payload-Feldern `lane`, `tree`, `ref`,
     `label` (GUI-SPEC §4.4) emittieren.
- **Snapshot-Punkte** (GUI-SPEC §5, verortet nach §6 in `_run_lane`,
  `_run_lane_gates` sowie `_confirm_red`/`_run_test_only_pass`/
  `_require_red_tests`): vor und nach jedem Agent-Lauf der Build-Lane, nach dem
  TDD-RED-Test-Only-Lauf, nach jeder Gate-Iteration. Zulässige `label`-Werte:
  `before_agent`, `after_agent`, `after_gates`, `red`. Agent-Läufe der
  Authoring-/Review-Phasen erhalten Spans (Aufgabe B), aber keine Snapshots —
  §6 sieht dort keine `snapshot`-Events vor.
- **Exception-Semantik:** `after_agent` gilt nur für normal zurückkehrende
  Agent-Läufe. Wirft `ctx.agents.run(...)` eine Exception, entfällt der
  `after_agent`-Snapshot samt Event; die Exception propagiert unverändert
  (der `before_agent`-Snapshot bleibt als Diff-Basis erhalten). Ein Capture
  während des Stack-Unwindings wird bewusst nicht gebaut (siehe Deferred).
- **Fail-open** wie beim Emitter: ein fehlgeschlagener Snapshot bricht niemals
  einen Lauf ab und ändert kein Verhalten. Er unterbleibt still (eine
  Warnung), das zugehörige `snapshot`-Event entfällt.

### Kontrakt (extern beobachtbare Fläche)

Gepinnt werden ausschließlich:

- die öffentliche API von `adw/snapshots.py` (`capture(ctx, worktree, label)`);
- das Ref-Namensschema `refs/adw/<run_id>/<seq>` und die Existenz dieser Refs
  nach einem erfolgreichen Snapshot;
- welche `snapshot`-Events mit welchen Payload-Feldern (`lane`, `tree`, `ref`,
  `label`) an welchen der vier Snapshot-Punkten erscheinen.

Nicht Teil des Kontrakts: interne Helper-Signaturen, Mechanik-Details
(Index-Handhabung, Reihenfolge der git-Kommandos).

### Guardrail Testumfang

Richtwert rund 15–22 neue Tests insgesamt für A, B und C. Deutlich mehr ist
ein Signal für Scope-Drift.

## Non-Goals

In DIESEM Lauf wird nicht gebaut:

- Kein Reader, kein Span-Baum-Modell, keine GUI, kein FastAPI, keine Registry,
  kein `adw gui`, kein Diff-Endpoint. Keine Anzeige oder Berechnung von Diffs.
- Kein Aufräumen/Prunen der Refs, kein `adw runs prune`, keine Retention, kein
  `trace:`-Config-Key (Lauf 5).
- Keine Redaction, keine Kappung, keine Patch-Texte im Event-Log — der Diff
  wird später aus den Refs berechnet, nicht mitgeschrieben.
- Keine Erweiterung von `RunState`, keine neuen Persistenzzustände außer
  `events.jsonl` und den `refs/adw/*`-Refs.
- Keine Änderung an `adw/events.py`; insbesondere keine
  Cross-Thread-Parent-API — parallele Lane-Spans tragen `parent: null`,
  Zuordnung über `phase` und `lane` (E2).
- Keine Änderung an der Grenze des `run`-Spans (E1).
- Keine Verlagerung von Stream-, Tool-, Usage- oder Kostenerfassung in die
  Mock-Runner oder nach `adw/phases.py`; keine Simulation von Tool-Calls durch
  Mocks.
- Keine neuen Laufzeit-Dependencies.
- Kein Refactoring von `phases.py` (keine Funktion aufteilen, umbenennen oder
  umsortieren); für Aufgabe B nur Umschließen bestehender Aufrufstellen.
- Keine Änderung am fachlichen Orchestrator-Ablauf: Rückgabewerte,
  Fehlerbehandlung, Exception-Weitergabe, Limits, Gate-Auswertung, Commit- und
  Merge-Semantik bleiben unberührt.

## Acceptance Criteria

**Aufgabe A**

1. Ein Test lässt den Körper des `run`-Spans eine unerwartete Exception werfen
   und belegt: das `run`-End-Event trägt `status: "escalated"` und wird vor
   der Propagation emittiert; dieselbe Exception propagiert unverändert
   weiter.

**Aufgabe B**

2. Ein Dry-Run erzeugt in `events.jsonl` mindestens einen vollständig
   geschlossenen `agent.run`-Span (start + end, gleiche Span-ID) und
   mindestens einen vollständig geschlossenen `codex.review`-Span — belegt
   durch einen Test, der auch die Payload-Felder aus AC 3 prüft, nicht nur
   die Span-Schließung.
3. Die Span-Payloads folgen GUI-SPEC §4.4 für Mock- wie Echt-Runner: das
   `agent.run`-Start-Event trägt die an der Aufrufstelle bekannten Felder
   (`agent`, `model`, `tools`, `allowed_tools`, `cwd`, `resume_session`,
   `prompt`, `system_append`), das End-Event die tatsächlich zurückgegebenen
   Ergebnisfelder (`session_id`, `result_text`, `is_error`). Das
   `codex.review`-Start-Event trägt `kind`, `argv`, `cwd`, `custom_prompt`,
   das End-Event `findings`, `raw_stdout`, `parse_ok`. Im Dry-Run dürfen
   **ausschließlich** die runnerseitigen Inhalte fehlen — Stream-Events und
   Usage-/Kostenwerte —, nicht die hier genannten Felder.
4. Jeder Aufruf von `ctx.agents.run(...)` bzw. `ctx.codex.review(...)` in
   `adw/phases.py` ist von genau einem `agent.run`- bzw. `codex.review`-Span
   umschlossen, unabhängig davon, ob Mock- oder Echt-Runner läuft (die Spans
   liegen an der Aufrufstelle, nicht mehr im Runner). Im Dry-Run werden keine
   Inhalte erfunden: keine `agent.message`-/`agent.tool.*`-Events, keine
   synthetischen Usage- oder Kostenwerte. Bei echten Läufen bleiben
   Stream-Spiegelung und Usage-/Kosten-Felder wie bisher an die
   Runner-Inhalte gebunden (GUI-SPEC §4.4/§6).

**Aufgabe C**

5. `adw/snapshots.py` stellt `capture(ctx, worktree, label)` als öffentliche
   Schnittstelle bereit. Ein erfolgreicher Snapshot erzeugt über einen
   temporären git-Index ein Tree-Objekt und daraus per `commit-tree` einen
   Commit; realer git-Index, Worktree-Dateien, aktueller Branch und `HEAD`
   bleiben unverändert — belegt durch einen Test mit realem Temp-Repo.
6. Nach einem erfolgreichen Snapshot existiert die Ref
   `refs/adw/<run_id>/<seq>` und zeigt auf einen Commit, dessen Tree den
   Worktree-Stand (inkl. untracked, ohne ignored) abbildet und der `base_sha`
   als Parent hat.
7. Die `<seq>`-Komponente unterscheidet die Snapshots eines Laufs eindeutig,
   auch über Prozessgrenzen hinweg: Nach einem `resume` desselben Laufs in
   einem neuen Prozess vergibt `capture` frische Sequenzen und überschreibt
   niemals eine bereits existierende Ref desselben Laufs — belegt durch einen
   Test, der für einen bestehenden Lauf mit vorhandenen Refs neu aufsetzt und
   erneut captured.
8. Jeder erfolgreiche Snapshot emittiert genau ein `snapshot`-Event mit den
   Payload-Feldern `lane`, `tree`, `ref`, `label`; `tree` und `ref`
   entsprechen dem erzeugten Tree bzw. der gesetzten Ref.
9. Snapshots werden an den vier Punkten aus GUI-SPEC §5 genommen — vor jedem
   Agent-Lauf der Build-Lane (`before_agent`), nach jedem normal
   zurückkehrenden Agent-Lauf der Build-Lane (`after_agent`), nach dem
   TDD-RED-Test-Only-Lauf (`red`), nach jeder Gate-Iteration (`after_gates`),
   unabhängig vom Gate-Ausgang — und tragen den jeweils passenden
   `label`-Wert. Belegt durch einen Test, der die `snapshot`-Events eines
   Laufs auf Punkt und Label prüft.
10. Wirft ein Agent-Lauf eine Exception, entfallen der `after_agent`-Snapshot
    und sein Event; die Exception propagiert unverändert weiter, der zuvor
    genommene `before_agent`-Snapshot bleibt bestehen — belegt durch einen
    Test.
11. Ein fehlgeschlagener Snapshot (z. B. scheiterndes git-Kommando) bricht den
    Lauf nicht ab und ändert kein Verhalten: keine Ref, kein `snapshot`-Event,
    eine Warnung, der Lauf läuft unverändert weiter; eine bereits laufende
    fachliche Exception wird weder verdeckt noch ersetzt — belegt durch einen
    Test.

**Regression / übergreifend**

12. Die Rückgabewerte der Runner (insbesondere `agents.py:_collect`) und der
    fachliche Orchestrator-Ablauf bleiben unverändert; die bestehenden Tests
    bleiben grün.

## Definition of Done

1. Alle Akzeptanzkriterien sind durch Tests belegt.
2. `uv run ruff check .` ist grün.
3. `uv run pytest -x -q` ist grün (die bestehenden 562 Tests plus die neuen).
4. Keine neuen Laufzeit-Dependencies; keine neuen Persistenzzustände außer
   `events.jsonl` und `refs/adw/*`.
5. `adw/events.py`, der `run`-Span und der fachliche Orchestrator-Ablauf sind
   unverändert (außer dem in Aufgabe B erlaubten Umschließen der
   Aufrufstellen); die Non-Goals und Deferred-Punkte sind nicht Bestandteil
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
