# R5: Parallele Lanes enden sauber, Eingaben scheitern früh

Lauf R5 aus `ONBOARDING-SPEC-PLAN.md` (Abschnitt 3, Befunde C24, C27–C29, dazu C30 aus den Retros von R3 und R4). Setzt auf
dem gemergten Stand **0.36.2** (nach R4) auf. Die Ausgangslage ist am 2026-11-08 im Code
geprüft und vor dem Bauen gegen den dann aktuellen Stand zu prüfen.

## Ausgangslage (im Code verifiziert, 2026-11-08)

### C24 — `unknown`-Findings eskalieren unbeteiligte Lanes

Ein P1/P2-Finding mit Lane `unknown` (oder einer inaktiven Lane) geht an **alle**
aktiven Lanes (`adw/triage.py`, Dispatch in `adw/lanes.py` `_dispatch_lane_fixes`,
`:~665`). Eine Lane, die zu Recht nichts ändert, weil das Finding nicht ihren Code
betrifft, wird in `_run_lane` als Leerlauf erkannt und über `_handle_idle_agent_run`
(`adw/lanes.py:448`) eskaliert — bei P1/P2 immer („keine Änderungen"), obwohl die
andere Lane das Finding behoben hat.

### C27 — Parallele Lanes laufen nach einer Eskalation weiter

`run_build_phase` (`adw/lanes.py:~59–69`) startet die Lanes in einem
`ThreadPoolExecutor`. Eskaliert eine Lane, propagiert `future.result()` die
Ausnahme erst, nachdem das `with`-Ende auf **alle** Futures gewartet hat: die andere
Lane fährt weiter Agenten und Gates (bis zu 10 Iterationen), obwohl der State schon
`escalated` ist. Eskaliert sie ebenfalls, überschreibt `escalate`
(`adw/context.py:~189`, `report.write_text(...)`) die `escalation.md` der ersten —
der erste Grund geht verloren.

### C28 — Lese-Wettlauf nach `join(timeout=5)`

`adw/gates.py:143` und `adw/codex.py:582` warten mit `pump.join(timeout=5)` auf die
Reader-Threads und lesen danach den Puffer (`b"".join(out_tail)`, `codex.py:583`; der
Zeilenpuffer in `gates.py`). Lebt ein Reader noch (ein Kindprozess hat per `setsid` die
Prozessgruppen-Beendigung überlebt und hält die Pipe offen), kann
`RuntimeError: deque mutated during iteration` den Orchestrator mit Traceback abbrechen.

### C29 — Späte bzw. stumme Eingabeprüfung

- `--parallel` mit einer Config ohne zweite Lane wird erst in `run_spec_and_plan`
  abgelehnt (`adw/context.py:305`, `is_parallel_capable`) — **nach** dem Anlegen und
  Speichern des Laufs: ein eskalierter Leichen-Lauf bleibt zurück.
- `adw runs prune --older-than -1` endet mit Exit 2 **ohne Meldung**
  (`adw/cli.py:644–645`), anders als die `--keep`-Prüfung direkt darüber.

### C30 — Jede Entscheidung des finalen Reviews wird doppelt protokolliert

Gefunden von den Retro-Agenten der Läufe `4a8b2b50` (R3) und `4af7d8fb` (R4), im Code
bestätigt: Phase 6 triagiert die Findings des finalen Reviews mit
`triage_final_review(..., emitter=ctx.emitter, ...)` (`adw/phases.py:472`) und reicht
sie dann an `_dispatch_lane_fixes`, das **erneut** `triage_final_review` mit Emitter
aufruft (`adw/lanes.py:679`). Ergebnis: Für **ein** Finding stehen zwei identische
`triage.decision`-Events im Log (R4: Seq 345 und 346, gleicher Span), und das
Lauf-Protokoll im Ledger zählt das Finding doppelt (`findings` in `run-record.json`).
Der Dispatch selbst passiert nur einmal.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Routing von Findings auf bekannte Lanes, Leerlauf-Regel für P3 (Follow-up statt
  Eskalation) und Fix-Zyklus-Limits bleiben.
- Inhalt und Format von `escalation.md` bleiben; nur das Überschreiben entfällt.
- Gate- und Codex-Timeouts und das Beenden der Prozessgruppe bleiben.

## Aufgabe

**A1 (C24)** Leerlauf einer Lane nach einem Fan-out-Finding ist kein Fehler, solange
eine andere Ziel-Lane etwas geändert hat.
**A2 (C27)** Nach der ersten Eskalation hören parallele Lanes auf; der erste Grund
bleibt erhalten.
**A3 (C28)** Gelesen wird nur ein stabiler Puffer.
**A4 (C29)** `--parallel` wird vor dem Anlegen des Laufs geprüft; `--older-than < 0`
meldet sich.
**A5 (C30)** Jede Triage-Entscheidung erscheint genau einmal im Event-Log.
**A6** `CHANGELOG.md`/`CHANGELOG.de.md` synchron: neue Sektion
`## [0.36.3] — Unreleased` mit `### Fixed`. Version in `pyproject.toml` **nicht**
ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 (C24):** Die Leerlauf-Prüfung für Fix-Dispatches unterscheidet Findings, die
  **dieser** Lane zugeordnet sind, von Fan-out-Findings (`unknown`/inaktive Lane).
  Enthält der Dispatch einer Lane **nur** Fan-out-Findings und hat sie nichts
  geändert, wird nicht eskaliert, sondern die Lane gilt für diese Runde als fertig;
  hat **keine** der Ziel-Lanes etwas geändert, eskaliert der Lauf wie heute (eine
  Stelle nach dem Dispatch prüft das über alle Ziel-Lanes). Sequentiell und parallel
  gleich.
- **E2 (C27):** Ein `threading.Event` im `RunContext` („abort"), gesetzt von
  `escalate`. `_run_lane` prüft es am Anfang jeder Gate-Iteration und vor jedem
  Agenten-Lauf und beendet sich dann ohne weitere Arbeit (ohne eigene Eskalation).
  Laufende Agenten/Gates werden **nicht** abgebrochen.
- **E3 (C27):** `escalate` schreibt `escalation.md` nur, wenn sie noch nicht
  existiert; jede weitere Eskalation hängt einen Abschnitt
  `## Weitere Eskalation (<Lane/Phase>)` mit ihrem Grund an. Das
  `escalation`-Event wird weiterhin für jede Eskalation geschrieben.
- **E4 (C28):** Nach `join(timeout=5)` wird gelesen, nachdem die Pipes vom Parent
  geschlossen wurden, und unter einem Lock, den auch der Reader beim Anhängen hält
  (je Puffer ein `threading.Lock`); lebt der Reader noch, wird der Puffer als
  Snapshot unter dem Lock kopiert. Keine neue Abhängigkeit.
- **E5 (C29):** In `run` wird `--parallel` gegen `config.is_parallel_capable`
  **vor** `RunState.new` geprüft: Exit 1 mit Meldung, kein Run-Ordner. Die spätere
  Prüfung in `context.py` bleibt als zweite Sicherung. `--older-than < 0` gibt eine
  Fehlermeldung auf stderr aus (Wortlaut im Stil der `--keep`-Meldung), Exit 2.
- **E6 (C30):** `_dispatch_lane_fixes` bekommt einen Parameter
  `record_triage: bool = True`; nur wenn `True`, übergibt es den Emitter an
  `triage_final_review`. Der Aufruf aus Phase 6 (`adw/phases.py`, finaler Review),
  der bereits selbst protokolliert hat, übergibt `False`. Alle anderen Aufrufer
  bleiben unverändert. Keine Deduplizierung im Ledger (die Ursache wird behoben).
- **E7:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Abbruch laufender Agenten oder Gates bei Eskalation einer anderen Lane.
- Intelligentere Zuordnung von `unknown`-Findings (z. B. nach Dateipfad).
- Weitere Eingabeprüfungen der CLI.

## Akzeptanzkriterien

1. Zwei Lanes, ein P2-Finding mit Lane `unknown`: Lane A ändert, Lane B ändert
   nichts → keine Eskalation, Lauf geht weiter. Ändert keine von beiden → Eskalation
   wie heute.
2. Parallel: Lane A eskaliert in Iteration 1; Lane B startet danach keinen weiteren
   Agenten-Lauf und keine weitere Gate-Iteration (Mock-Runner zählt Aufrufe).
3. Zwei Eskalationen: `escalation.md` enthält den ersten Grund unter `## Grund` und
   den zweiten unter `## Weitere Eskalation …`.
4. Reader-Thread, der nach `join(timeout)` noch schreibt (Test mit gefaktem
   Stream): kein `RuntimeError`, das Ergebnis enthält den Snapshot.
5. `adw run --parallel` mit Single-Lane-Config → Exit 1, Meldung, kein neuer
   Run-Ordner.
6. `adw runs prune --older-than -1` → Exit 2 mit Meldung auf stderr.
6a. Finaler Review mit einem Finding (Mock-Runner): genau **ein**
    `triage.decision`-Event für dieses Finding; der Fix wird einmal dispatcht.
7. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 8–11 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (CLI-Exits
und Meldungen, Aufbau von `escalation.md`), keine internen Helper-Signaturen.
