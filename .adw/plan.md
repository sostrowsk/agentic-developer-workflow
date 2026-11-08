# Plan — R5: Parallele Lanes enden sauber, Eingaben scheitern früh

Basis: `.adw/spec.md`, gemergter Stand **0.36.2** (nach R4). Die Vorentscheidungen E1–E7
des Issues sind bindend. Single-Lane: ein Workstream `backend`, kein Frontend. Vor dem
Bauen jede genannte Stelle gegen den aktuellen Code abgleichen; Zeilen sind Orientierung
(Stand 2026-11-08).

`.adw/contract.yaml` pinnt ausschließlich die CLI-Fehlerfälle, den Aufbau von
`escalation.md` und die Häufigkeit der Events `escalation` und `triage.decision`.
Interne Helper-Signaturen sind kein Contract. Es entstehen keine HTTP-Routen, Templates
oder neuen Event-Typen.

## Workstream: backend

Verantwortung: A1–A6, Akzeptanzkriterien 1–9. Betroffen: `adw/lanes.py`,
`adw/context.py`, `adw/gates.py`, `adw/codex.py`, `adw/cli.py`, `adw/phases.py`, die
zugehörigen bestehenden Testmodule und beide Changelogs. `adw/triage.py` und
`adw/ledger.py` bleiben unverändert.

### B1 — Fan-out-Leerlauf auf Dispatch-Ebene bewerten (A1, AK 1)

- `_dispatch_lane_fixes` (`adw/lanes.py:665`) hält je Ziel-Lane fest, welche Findings
  ihr direkt zugeordnet sind und welche Fan-out sind (`unknown` oder inaktive Lane).
  Das Routing in `adw/triage.py` bleibt unverändert.
- Leerlauf-Prüfung je Lane (`_handle_idle_agent_run`, `:448`): Besteht der Dispatch
  der Lane **nur** aus Fan-out-Findings und hat sie nichts geändert, wird nicht
  eskaliert; die Lane gilt für diese Runde als fertig und verlässt den Lane-Pfad so,
  dass kein `pending_task` zurückbleibt und `completed` wieder gesetzt ist (bestehender
  Lane-Pfad oder direkter Abschluss, ohne neuen persistenten Zustand). Enthält der
  Dispatch ein direkt zugeordnetes P1/P2-Finding, gilt die bisherige Eskalation.
  Initialer Build ohne Änderungen und reine P3-Follow-ups behalten ihr Verhalten.
- **Eine** Stelle nach dem Dispatch (heute die `_run_lane`-Schleife am Ende von
  `_dispatch_lane_fixes`, `:715`) bewertet den Fortschritt über alle Ziel-Lanen:
  hat keine etwas geändert, eskaliert der Lauf wie heute; hat mindestens eine etwas
  geändert, geht er weiter. Maßgeblich sind Änderungen dieser Fix-Runde gegenüber dem
  Stand vor dem Dispatch, in der Lane erfasst **bevor** `_finalize_lane` committet
  (danach ist der Worktree sauber), nicht frühere Build-Änderungen. Nur flüchtige
  Daten je Dispatch, keine neuen Felder im `RunState`.
- Die Bewertung ist reihenfolgeunabhängig und gilt sequentiell wie parallel. Der
  Fix-Dispatch läuft heute auch in parallelen Läufen sequentiell; eine neue
  Parallelisierung gehört nicht zum Umfang. Bereits vertagte P3-Findings dürfen über
  die Gesamtauswertung keine zusätzliche Eskalation auslösen; eine bereits erfolgte
  Eskalation darf nicht um eine zweite Leerlauf-Eskalation ergänzt werden.

### B2 — Kooperatives Ende und erster Eskalationsgrund (A2, AK 2–3)

- `RunContext` (`adw/context.py`) bekommt ein nicht persistiertes
  `abort: threading.Event` (`field(default_factory=...)`, kein Teil von `RunState`).
  `escalate` (`:186`) setzt es unter dem `state_lock`.
- `_run_lane` (`adw/lanes.py:80`) prüft das Signal am Anfang jeder Gate-Iteration und
  unmittelbar vor jedem Agenten-Lauf, einschließlich des Test-only-Agenten des
  RED-Pfads (`_run_test_only_pass`). Gesetzt → Rückkehr ohne weitere Arbeit und ohne
  eigene Eskalation; ein Abbruch im RED-/Agenten-Unterpfad beendet auch den
  aufrufenden Lane-Pfad. Bereits laufende Agenten und Gates laufen zu Ende, ihre
  Timeouts bleiben der Backstop. Die Eskalation der ersten Lane propagiert wie heute
  über `future.result()`; der Lauf bleibt `escalated`.
- `escalate` serialisiert unter dem bestehenden `state_lock` Existenzprüfung und
  Schreibvorgang: Fehlt `escalation.md`, wird sie wie heute angelegt (Titel, Issue,
  Phase, Lanes, `## Grund`). Existiert sie, wird **nur** ein durch Leerzeile
  getrennter Abschnitt `## Weitere Eskalation (<Lane/Phase>)` mit dem Grund angehängt;
  bestehender Inhalt bleibt unverändert. `<Lane/Phase>` bezeichnet die Herkunft der
  weiteren Eskalation (Lane-Name, sonst Phase); die Durchreichung ist
  Implementierungsdetail, kein persistenter Zustand.
- Das `escalation`-Event wird weiterhin für jede Eskalation geschrieben. Eine Lane,
  die nur wegen `abort` zurückkehrt, erzeugt kein Event. `adw/ledger.py` bleibt
  unverändert und liest weiterhin den ersten Absatz unter `## Grund`.

### B3 — Ausgabepuffer stabil auswerten (A3, AK 4)

- `adw/gates.py`: `_LineTail` (Zeilen-Deque **und** `dropped`-Zähler) bekommt ein
  `threading.Lock`; `_pump_stream` hält es beim Anhängen. `adw/codex.py`: je ein
  Lock für `out_tail` und `err_tail`; `_pump` hält ihn beim Anhängen. Ein gemeinsamer
  Helper für beide Pfade ist zulässig.
- Nach Prozessgruppen-Beendigung und `join(timeout=5)` (`gates.py:143`,
  `codex.py:590`) schließt der Parent die Pipes, **dann** wird unter dem Lock ein
  Snapshot kopiert (beim Gate-Puffer samt `dropped`) und ausschließlich dieser
  dekodiert bzw. formatiert. Ein noch lebender Reader verändert das Ergebnis danach
  nicht.
- Locks nicht während blockierender `readline`/`read`-Aufrufe, `join` oder
  Pipe-Schließung halten; das Reader-Ende durch geschlossene Pipe sauber behandeln.
- Ausgabegrenzen, Timeouts, Prozessgruppen-Kill unverändert. Keine neue Abhängigkeit,
  kein allgemeiner Umbau der Prozessverwaltung.

### B4 — CLI-Eingaben vor Run-Mutationen ablehnen (A4, AK 5–6)

- `adw/cli.py` `run`: nach dem Laden der Config und **vor** `RunState.new` (`:287`)
  `parallel` gegen `config.is_parallel_capable` prüfen. Nicht parallelfähig →
  verständliche Meldung zu `--parallel` und den konfigurierten Lanes, Exit 1; kein
  Run-Ordner, kein gespeicherter eskalierter Lauf. Die Prüfung in
  `adw/context.py:_validate_lanes` (`:309`) bleibt als zweite Sicherung; die Bedeutung
  von `is_parallel_capable` bleibt.
- `adw/cli.py` `runs prune` (`:668`): den bestehenden Zweig `older_than < 0` um eine
  stderr-Meldung im Stil der `--keep`-Meldung ergänzen, z. B.
  `Fehler: --older-than muss mindestens 0 sein.` Exit 2 bleibt, Prüfung bleibt vor
  jeder Änderung an Run-Daten.
- Keine weiteren CLI-Validierungen.

### B5 — Finale Triage nur einmal protokollieren (A5, AK 7)

- `_dispatch_lane_fixes` bekommt `record_triage: bool = True`; nur bei `True` geht der
  Emitter an `triage_final_review` (`lanes.py:679`). Routing und Triage-Ergebnis
  bleiben gleich.
- Nur der Aufruf aus Phase 6 (`adw/phases.py:510`, nach eigener Triage `:472`)
  übergibt `record_triage=False`. Die Aufrufer `:164`, `:400`, `:623` bleiben
  unverändert. Weder Zahl noch Routing der Fix-Dispatches ändern sich.
- Keine Deduplizierung in `adw/ledger.py`, keine Korrektur historischer Event-Logs
  oder Run-Records.

### B6 — Changelogs (A6, AK 8)

- `CHANGELOG.md` und `CHANGELOG.de.md`: oberhalb von 0.36.2 die Sektion
  `## [0.36.3] — Unreleased` mit `### Fixed`, inhaltlich deckungsgleich, zu A1–A5
  (Fan-out-Leerlauf, kooperatives Ende und Erhalt der Eskalationsgründe, stabile
  Ausgabepuffer, frühe CLI-Fehler, einmalige finale Triage).
- `pyproject.toml` bleibt `0.36.2`.

### Tests und Abnahme (AK 1–9)

Bestehende Fixtures, Mock-Runner, Emitter und CLI-Helfer wiederverwenden. Für Threads
Events/Barrieren und kontrollierte Fakes statt zeitabhängiger Sleeps. Richtwert 8–11
neue Tests; parametrisierte Fälle zählen einzeln.

1. **Fan-out mit Fortschritt** (`tests/test_phases.py`, AK 1): zwei aktive Lanes, P2
   mit `unknown`, nur eine Lane ändert etwas; B zuerst bearbeitet; sequentiell und
   parallel → keine Eskalation, Lauf geht weiter. (parametrisiert, ~2)
2. **Fan-out ohne Fortschritt** (`tests/test_phases.py`, AK 1): keine Ziel-Lane
   ändert etwas → Eskalation nach der Ziel-Lane-Bearbeitung. (~1)
3. **Kooperatives Ende** (`tests/test_phases.py`, AK 2): parallele Lanes, synchroner
   Mock-Runner; A eskaliert in Iteration 1 → B startet keinen weiteren Agenten-Lauf
   und keine Gate-Iteration, eskaliert nicht; Lauf `escalated`. (1)
4. **Mehrere Eskalationen** (`tests/test_instrumentation.py`, AK 3): zwei
   Eskalationen; erster Bericht bleibt unveränderter Präfix, zweiter Grund im
   angehängten Abschnitt, zwei `escalation`-Events, Ledger liefert den ersten Grund.
   Im Konkurrenzfall keine feste Gewinner-Lane voraussetzen. (1)
5. **Gate-Puffer** (`tests/test_gates.py`, AK 4): gefakter Stream, Reader schreibt
   nach dem Join-Timeout weiter → kein `RuntimeError`, Ergebnis = Snapshot und bleibt
   danach unverändert. (1)
6. **Codex-Puffer** (`tests/test_codex.py`, AK 4): gleicher Fall für stdout/stderr
   ohne echtes Timeout-Warten; entfällt, wenn ein gemeinsamer Helper getestet wird. (1)
7. **Ungültiges `--parallel`** (`tests/test_cli.py`, AK 5): Single-Lane-Config → Exit
   1, Meldung, kein neuer Run-Ordner. (1)
8. **Negatives `--older-than`** (`tests/test_runs_cli.py`, AK 6): Exit 2, stderr nennt
   Option und Wertebereich, Run-Daten unverändert. (1)
9. **Finale Triage** (`tests/test_instrumentation.py` oder `tests/test_phases.py`,
   AK 7): ein Finding im finalen Review → genau ein `triage.decision`-Event, ein
   Fix-Dispatch, `findings` in `run-record.json` = 1. (1)
10. *Optional, nur falls kein bestehender Test es belegt:* ein anderer
    Dispatch-Aufrufer protokolliert ohne `record_triage=False` weiterhin. (≤1)

Keine zusätzliche Testmatrix für unveränderte P3-Regel, Limits oder Routing; das
belegen die bestehenden Tests. AK 8 durch Prüfung beider Changelogs und der
Projektversion. Gates (AK 9, E7):

- `uv run ruff check .`
- `uv run pytest -x -q`

Abnahme: AK 1–9 erfüllt, Non-Goals der Spec unverändert, keine neue Abhängigkeit, kein
neuer persistenter Zustand, Doppelprotokollierung an der Quelle behoben.

## Non-Goals

Wie in `.adw/spec.md`: keine Versionsänderung; kein Abbruch laufender Agenten/Gates;
Routing bekannter Lanes, P3-Leerlauf-Regel, Fix-Zyklus- und Gate-Limits, Timeouts,
Ausgabegrenzen und der erste Teil von `escalation.md` bleiben; kein neuer persistenter
Zustand; keine Ledger-Deduplizierung; keine neuen Abhängigkeiten; kein Contract für
interne Helper.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Abbruch laufender Agenten oder Gates bei Eskalation einer anderen Lane.
- Intelligentere Zuordnung von `unknown`-Findings (z. B. nach Dateipfad).
- Weitere Eingabeprüfungen der CLI.
