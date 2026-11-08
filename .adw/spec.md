# Spec — R5: Parallele Lanes enden sauber, Eingaben scheitern früh

Basis: gemergter Stand **0.36.2** (nach R4). Befunde C24, C27–C29 aus
`ONBOARDING-SPEC-PLAN.md` Abschnitt 3, dazu C30 aus den Retros von R3 und R4. Die
Ausgangslage wurde am 2026-11-08 im Code bestätigt und ist vor dem Bauen gegen den dann
aktuellen Stand abzugleichen; Zeilenangaben im Issue sind Orientierung. Die
Vorentscheidungen E1–E7 des Issues sind bindend und hier nicht neu verhandelbar.

Contract: **Single-Lane**. Der Contract umfasst nur extern beobachtbare Flächen
(CLI-Exits und Meldungen, Aufbau von `escalation.md`); interne Helper-Signaturen sind
kein Contract.

## Ziel

Ein Lauf mit mehreren Lanes eskaliert nicht wegen einer Lane, die zu Recht nichts
geändert hat, beginnt nach der ersten Eskalation keine weitere Arbeit und verliert den
ersten Eskalationsgrund nicht. Reader-Threads können den Orchestrator nicht mehr per
`RuntimeError` abbrechen. Ungültige CLI-Eingaben (`--parallel` ohne zweite Lane,
`--older-than < 0`) scheitern früh und mit Meldung. Jede Triage-Entscheidung des
finalen Reviews wird genau einmal protokolliert.

## Scope

- **A1 — Fan-out-Leerlauf (C24, E1):** Findings mit Lane `unknown` oder einer inaktiven
  Lane bleiben an alle aktiven Lanes geroutet. Die Leerlauf-Prüfung nach einem
  Fix-Dispatch unterscheidet diese Fan-out-Findings von Findings, die der jeweiligen
  Lane direkt zugeordnet sind. Besteht der Dispatch einer Lane **nur** aus
  Fan-out-Findings und hat sie nichts geändert, gilt sie für diese Runde als fertig,
  ohne eigene Eskalation. Eine Stelle nach dem Dispatch prüft über alle Ziel-Lanes:
  hat **keine** etwas geändert, eskaliert der Lauf wie heute; hat mindestens eine
  etwas geändert, geht der Lauf weiter, unabhängig von der Bearbeitungsreihenfolge.
  Sequentiell und parallel gleich. Enthält der Dispatch auch ein der Lane direkt
  zugeordnetes P1/P2-Finding, gilt die bisherige Leerlauf-Regel. Die P3-Regel
  (Follow-up statt Eskalation) bleibt.

- **A2 — Kooperatives Ende und Eskalationsbericht (C27, E2, E3):**
  - `RunContext` trägt ein nicht persistiertes Abbruchsignal (`threading.Event`
    „abort"), das `escalate` setzt. `_run_lane` prüft es am Anfang jeder
    Gate-Iteration und vor jedem Agenten-Lauf und beendet sich bei gesetztem Signal
    ohne weitere Arbeit und ohne eigene Eskalation. Bereits laufende Agenten und
    Gates werden nicht abgebrochen; ihre bestehenden Timeouts bleiben der Backstop.
  - `escalate` legt `escalation.md` nur an, wenn sie noch nicht existiert (Format wie
    heute, erster Grund unter `## Grund`). Jede weitere Eskalation hängt einen
    Abschnitt `## Weitere Eskalation (<Lane/Phase>)` mit ihrem Grund an; der
    bestehende Inhalt bleibt unverändert. Das Schreiben passiert wie heute unter dem
    `state_lock`, sodass auch konkurrierende Eskalationen keinen Grund verlieren. Das
    `escalation`-Event wird weiterhin für jede Eskalation geschrieben. Der Ledger
    liest den Grund weiterhin aus dem ersten Absatz unter `## Grund`, also den ersten.

- **A3 — Stabiler Puffer (C28, E4):** In `adw/gates.py` und `adw/codex.py` wird der
  Ausgabepuffer nach `join(timeout=5)` erst gelesen, nachdem der Parent die Pipes
  geschlossen hat, und unter einem `threading.Lock` je Puffer, den auch der Reader
  beim Anhängen hält. Lebt der Reader noch, wird unter dem Lock ein Snapshot kopiert
  und dieser ausgewertet; spätere Schreibvorgänge des Readers verändern das Ergebnis
  nicht. Ausgabegrenzen, Timeouts und Prozessgruppen-Beendigung bleiben. Keine neue
  Abhängigkeit.

- **A4 — Frühe, hörbare Eingabeprüfung (C29, E5):**
  - `adw run --parallel` prüft die geladene Config gegen `config.is_parallel_capable`
    **vor** `RunState.new`. Ist sie nicht parallelfähig: Exit 1 mit Meldung, kein
    neuer Run-Ordner, kein gespeicherter eskalierter Lauf. Die spätere Prüfung in
    `adw/context.py` bleibt als zweite Sicherung.
  - `adw runs prune --older-than <n>` mit `n < 0`: Fehlermeldung auf stderr im Stil
    der vorhandenen `--keep`-Meldung (benennt `--older-than` und den zulässigen
    Wertebereich), Exit 2, vor jeder Änderung an Run-Daten.

- **A5 — Einmalige Triage-Protokollierung (C30, E6):** `_dispatch_lane_fixes` bekommt
  den Parameter `record_triage: bool = True`; nur bei `True` geht der Emitter an
  `triage_final_review`. Der Aufruf aus Phase 6 (finaler Review, `adw/phases.py`),
  der bereits selbst protokolliert hat, übergibt `False`; alle anderen Aufrufer
  bleiben unverändert und protokollieren weiterhin. Routing und Anzahl der
  Fix-Dispatches ändern sich nicht. Dadurch zählen Event-Log und `findings` in
  `run-record.json` jedes Finding einmal; eine Deduplizierung im Ledger entfällt.

- **A6 — Changelog:** `CHANGELOG.md` und `CHANGELOG.de.md` bekommen beide eine neue
  Sektion `## [0.36.3] — Unreleased` mit `### Fixed` zu A1–A5, inhaltlich
  deckungsgleich. Die Version in `pyproject.toml` bleibt `0.36.2`.

## Non-Goals

- Version in `pyproject.toml` ändern.
- Laufende Agenten oder Gates abbrechen, wenn eine andere Lane eskaliert.
- Routing von Findings auf bekannte Lanes, die P3-Leerlauf-Regel oder die
  Fix-Zyklus- und Gate-Limits ändern.
- Inhalt und Format des ersten Teils von `escalation.md` ändern (neu ist nur Anhängen
  statt Überschreiben).
- Gate-/Codex-Timeouts, Ausgabegrenzen oder das Beenden der Prozessgruppe ändern.
- Neue persistente Abbruch-, Fortschritts- oder Deduplizierungszustände.
- Deduplizierung im Ledger oder nachträgliche Korrektur historischer Event-Logs und
  Run-Records (R3/R4).
- Neue Abhängigkeiten.
- Contract-Zusagen für interne Helper-Signaturen.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Abbruch laufender Agenten oder Gates bei Eskalation einer anderen Lane.
- Intelligentere Zuordnung von `unknown`-Findings (z. B. nach Dateipfad).
- Weitere Eingabeprüfungen der CLI.

## Akzeptanzkriterien

1. Zwei aktive Lanes, ein P2-Finding mit Lane `unknown` (oder einer inaktiven Lane)
   im Fix-Dispatch: Lane A ändert etwas, Lane B nichts → keine Eskalation, der Lauf
   geht weiter, auch wenn B zuerst bearbeitet wird. Ändert keine von beiden etwas →
   Eskalation wie heute. Gilt sequentiell und parallel.
2. Parallel: Lane A eskaliert in Iteration 1. Danach startet Lane B keinen weiteren
   Agenten-Lauf und keine weitere Gate-Iteration (Mock-Runner zählt die Aufrufe) und
   eskaliert nicht selbst; der Lauf bleibt `escalated`.
3. Zwei Eskalationen im selben Lauf: `escalation.md` enthält den ersten Grund
   unverändert unter `## Grund` und den zweiten unter
   `## Weitere Eskalation (<Lane/Phase>)`. Für beide steht ein `escalation`-Event im
   Log. Der Ledger meldet den ersten Grund.
4. Ein Reader-Thread, der nach `join(timeout)` noch in den Puffer schreibt (Test mit
   gefaktem Stream, je für den Gate- und den Codex-Pfad oder einen gemeinsamen
   Helper): kein `RuntimeError`, das Ergebnis enthält den bis dahin gelesenen Snapshot.
5. `adw run --parallel` mit Single-Lane-Config → Exit 1, Meldung, kein neuer
   Run-Ordner unter dem Runs-Verzeichnis.
6. `adw runs prune --older-than -1` → Exit 2 und Fehlermeldung auf stderr; vorhandene
   Run-Daten bleiben unverändert.
7. Finaler Review mit einem Finding (Mock-Runner): genau **ein**
   `triage.decision`-Event für dieses Finding, der Fix wird genau einmal dispatcht,
   `findings` in `run-record.json` zählt es einmal.
8. `CHANGELOG.md` und `CHANGELOG.de.md` enthalten beide `## [0.36.3] — Unreleased`
   mit `### Fixed`. `pyproject.toml` steht unverändert auf `0.36.2`.
9. `uv run ruff check .` und `uv run pytest -x -q` sind grün.

Richtwert: 8–11 neue Tests, kombinierte oder parametrisierte Fälle zählen einzeln.
Deutlich mehr ist ein Zeichen für Gold-Plating. Unverändertes Verhalten (P3-Regel,
Limits, Routing) belegen die bestehenden Tests.

## Contract (extern beobachtbar)

- `adw run --parallel` bei nicht parallelfähiger Config: Exit 1, Meldung, kein
  Run-Ordner.
- `adw runs prune --older-than <n>` mit `n < 0`: Exit 2, Meldung auf stderr.
- `escalation.md`: erster Grund unter `## Grund` (Format unverändert), jede weitere
  Eskalation als angehängter Abschnitt `## Weitere Eskalation (<Lane/Phase>)` mit
  ihrem Grund.

## Definition of Done

- Akzeptanzkriterien 1–9 erfüllt; die Verhaltensfälle 1–7 sind durch Tests belegt,
  Kriterium 8 durch Prüfung der Dateien.
- Bestehende Tests grün; Verhalten aus den Non-Goals ist unverändert.
- Die Doppelprotokollierung ist an der Ereignisquelle behoben, nicht im Ledger
  kompensiert.
- Keine neue Abhängigkeit und kein neuer persistenter Zustand.
- Changelog-Einträge in EN und DE synchron; Version nicht angehoben.
