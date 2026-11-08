# Spec — W4: Gleicher Fehler, gleicher Schlüssel

Basis: Stand 0.32.0 (`main`, 420af78). Ausgangslage am 2026-11-08 gegen den Code
geprüft; Zeilenangaben sind Richtwerte und vor dem Bauen zu verifizieren.

## Ziel

Die Circuit Breaker des Orchestrators erkennen „derselbe Fehler wie in der
Vorrunde" auch dann, wenn sich nur volatile Details unterscheiden: Laufzeiten,
Temp- und Worktree-Pfade, Run-IDs, Speicheradressen, Zeitstempel, Zeilennummern,
Ports, Leerraum. Wiederholte Fehler lösen den bestehenden Breaker mit Eskalation
aus, statt das Iterations- oder Rundenlimit auszureizen.

Konkreter Schaden heute: Der Gate-Breaker kann bei pytest-Fehlern praktisch nie
auslösen, weil die pytest-Zusammenfassung immer die Laufzeit enthält (`in 36.02s`)
und `check_progress` Schlüssel exakt vergleicht — in keinem der 15 vorhandenen
Läufe gibt es ein `circuit_breaker`-Event. Finding-Schlüssel reagieren auf
Zeilennummer, Pfadpräfix und Leerraum. Zusätzlich (C25) vergleicht der
Codex-Breaker die Basis der Runde N (alle Severities oberhalb der Schwelle N) mit
den Findings der Runde N+1 (höhere Schwelle) und verpasst so einen unveränderten
Stand — ein zusätzlicher teurer Fix-Lauf.

Im Code bestätigt: unnormalisierte Gate-Schlüssel in `adw/lanes.py:~207` **und**
`adw/phases.py:~154` (Integration/E2E), textbasierter `_finding_key` in
`adw/review_policy.py:15`, dessen Formatkopie in `adw/triage.py:63`, und eine
Breaker-Basis ohne Severity-Information in `review_last_failures` bzw.
`authoring_last_findings`.

## Scope

### 1. Gemeinsame Schlüsselnormalisierung (A1, E1, E2)

Eine Funktion `normalize_key_text(text: str) -> str` in `adw/triage.py`. Kein
neues Modul, keine Klasse, keine Konfiguration, keine I/O. Die Regeln gelten in
dieser Reihenfolge als vorkompilierte Regex auf Modulebene:

| Nr. | Ersetzung | Beispiel |
|---|---|---|
| 1 | Worktree-/Run-Pfadpräfix `…/.adw/runs/<8 hex>/trees/<lane>/` entfernen; relativer Rest bleibt | `/repo/.adw/runs/1234abcd/trees/backend/src/foo.py` → `src/foo.py` |
| 2 | `/tmp/\S+` und `/private/var/folders/\S+` → `<tmp>` | `/tmp/pytest-of-user/pytest-7/data` → `<tmp>` |
| 3 | ISO-Zeitstempel `YYYY-MM-DD[T ]HH:MM:SS[.frac][Z\|±HH:MM]` → `<ts>`, danach Uhrzeiten `HH:MM:SS[.frac]` → `<ts>` | `2026-11-08T12:34:56.123Z` → `<ts>`; `12:34:56` → `<ts>` |
| 4 | Dauern `\d+(\.\d+)?\s?(ms\|s\|sec\|seconds\|min)\b` → `<dur>` | `failed in 36.02s` → `failed in <dur>` |
| 5 | Hex-Adressen `0x[0-9a-fA-F]+` → `<hex>` | `at 0x7fAB12` → `at <hex>` |
| 6 | `:\d+(:\d+)?` direkt nach einem Dateinamen mit Endung → `:<n>`; `line \d+` → `line <n>` | `foo.py:12:4` → `foo.py:<n>`; `line 12` → `line <n>` |
| 7 | `localhost:\d+` und `127.0.0.1:\d+` → `<host>:<port>` | `localhost:8123` → `<host>:<port>` |
| 8 | Whitespace-Folgen → ein Leerzeichen, anschließend `strip()` | `"  Fehler\n  in\tfoo.py  "` → `"Fehler in foo.py"` |

Keine Kleinschreibung, keine weiteren Regeln. Ein ISO-Zeitstempel wird als Ganzes
ersetzt, bevor die Uhrzeit-Regel greift (ein `<ts>`, nicht `2026-11-08T<ts>`).

### 2. Gate-Schlüssel (E3)

Überall, wo Breaker-Schlüssel aus Gate-Failures gebildet werden — Lane-Gates
(`adw/lanes.py:~207`) **und** Integration/E2E (`adw/phases.py:~154`) — lautet der
Schlüssel `f"{gate}|{exit_code}|{normalize_key_text(output)}"`. Gate-Name und
Exit-Code bleiben unverändert Teil der Identität. Die beiden Formatkopien werden
durch eine gemeinsame Schlüsselbildung ersetzt.

### 3. Finding-Schlüssel (E4)

`_finding_key` liefert `f"{file}|{normalize_key_text(issue)}"`, wobei `file`
**ausschließlich** mit Regel 1 behandelt wird (Pfadpräfix), nicht mit den übrigen
Regeln. Zeilennummern oder Leerraum im Feld `file` bleiben also unterscheidend;
das Zeilennummern-Beispiel des Issues (`foo.py:12` vs. `foo.py:15`) betrifft
Referenzen im Finding-Text `issue`.

Damit normalisieren automatisch alle Nutzer von `_finding_key`: Codex-Code-Review-
Breaker, Authoring-Breaker, Final-Review-Breaker, Deferred-Erkennung
(`_without_deferred`, Eintragen in `deferred_findings`), Dedupe innerhalb eines
Follow-up-Schreibvorgangs und das `followup`-Event.

### 4. Triage-Event (A1)

Das `finding_key` im `triage.decision`-Event (`adw/triage.py:~63`) entsteht über
dieselbe Schlüsselbildung wie `_finding_key`; die eigene Formatkopie
`f"{item.file}|{item.issue}"` entfällt. Hinweis zur Platzierung: `adw/context.py`
importiert `adw/triage.py`, `adw/review_policy.py` importiert `adw/context.py` —
die gemeinsame Schlüsselbildung muss also in `adw/triage.py` liegen (oder
darunter), `_finding_key` delegiert dorthin.

### 5. C25 — Vergleich an der Schwelle der aktuellen Runde (A2, E5)

Gilt für den Codex-Code-Review-Breaker (`review_last_failures`) **und** den
Authoring-Breaker (`authoring_last_findings`); beide nutzen dieselbe Schwellenlogik
`_actionable_severities(N)`.

- Neu gespeicherte Basis-Einträge haben das Format `f"{severity}|{finding_key}"`
  (z. B. `P1|adw/foo.py|…`).
- Beim Vergleich in Runde N werden Basis **und** aktuelle Findings auf
  `_actionable_severities(N)` gefiltert; verglichen werden danach nur die
  `finding_key`-Teile ohne Severity, weiterhin über `check_progress`.

  | Runde | Berücksichtigte Severities |
  |---|---|
  | 1 | P1, P2, P3 |
  | 2 | P1, P2 |
  | ab 3 | P1 |

- Ein Basis-Eintrag ohne Severity-Präfix (`P1|`/`P2|`/`P3|`) — alter State —
  gilt als passend zu jeder Schwelle und wird unverändert verglichen.
- Die bestehende Ausnahme vertagter Findings aus der Breaker-Basis
  (`_circuit_breaker_keys` nach dem Dispatch) bleibt erhalten.
- Der Final-Review-Breaker (`final_review_last_failures`) und der Gate-/
  Integration-Breaker haben keine Rundenschwelle und behalten ihr Format:
  normalisierte Schlüssel ohne Severity.

### 6. Changelog (A3)

`CHANGELOG.md` und `CHANGELOG.de.md` erhalten synchron eine neue Sektion
`## [0.32.1] — Unreleased` oberhalb von `0.32.0` mit `### Fixed`, im Stil der
vorhandenen Einträge (fette Kopfzeile, Unterpunkte, jeweilige Sprache). Sie nennt
ausdrücklich, dass der Gate-Breaker vorher bei pytest praktisch nie auslösen konnte
(Laufzeit in der Zusammenfassung), die Normalisierung der Finding-Schlüssel und
C25. Die Version in `pyproject.toml` bleibt unverändert.

## Non-Goals

- Severity-Schwellen je Runde, Rundenlimits, Iterationslimits und die Semantik
  „Deferred = P3-Einschätzung" bleiben unverändert.
- Gate-Output und Findings-Texte, die an Agenten, in `escalation.md`, in den
  Follow-up-Report oder in Event-Payloads (außer `finding_key`) gehen, bleiben
  **ungekürzt und unnormalisiert**; normalisiert wird nur der Schlüssel.
- Die Kürzung des Gate-Outputs auf `MAX_OUTPUT_LINES` (200 Zeilen) bleibt.
- Keine semantische Ähnlichkeit, keine Embeddings, keine Erkennung von
  Umformulierungen (W12).
- Keine Migration alter `deferred_findings`, `review_last_failures`,
  `authoring_last_findings`, `final_review_last_failures`,
  `integration_last_failures` oder Lane-`last_failures` (E6); schlimmstenfalls
  wird ein Finding einmal mehr dispatcht bzw. ein Breaker greift eine Runde später.
- Keine neuen State-Felder, keine Konfigurationsoptionen, keine zusätzlichen
  Eskalationsmechanismen.
- Keine Änderung des `circuit_breaker`-Event-Formats (`keys`, `scope`).
- Keine Änderung der Version in `pyproject.toml`.

## Akzeptanzkriterien

1. **Gate-Breaker greift bei pytest (Issue-AC 1).** Zwei Gate-Outputs desselben
   Gates mit gleichem Exit-Code, die sich nur in `failed in 36.02s` vs.
   `failed in 1.65s`, im Temp-Pfad (`/tmp/pytest-of-x/pytest-3/…` vs.
   `…/pytest-7/…`) und in der Run-ID im Worktree-Pfad
   (`.adw/runs/<id1>/trees/backend/` vs. `<id2>`) unterscheiden, ergeben denselben
   Gate-Schlüssel. Zwei aufeinanderfolgende rote Lane-Gate-Iterationen mit diesen
   Outputs lösen den Breaker aus: `circuit_breaker`-Event und Eskalation statt
   weiterer Iteration.
2. **Kein Fehlalarm (Issue-AC 2).** Zwei ansonsten gleiche Outputs, die sich im
   Namen des fehlschlagenden Tests unterscheiden, ergeben verschiedene Schlüssel;
   zwei solche Iterationen lösen keinen Breaker aus.
3. **Finding-Dedupe (Issue-AC 3).** Ein P3-Finding, dessen Schlüssel in
   `deferred_findings` steht, und ein neues Finding mit gleichem `file`, dessen
   `issue` sich nur in der Zeilennummer (`foo.py:12` vs. `foo.py:15`) und im
   Leerraum unterscheidet, wird von `_without_deferred` als vertagt
   herausgefiltert; im Follow-up-Report erscheint es nur einmal. Der im Report
   gespeicherte Finding-Text bleibt unnormalisiert.
4. **C25 greift (Issue-AC 4).** Runde 1 {A:P1, B:P3}, der Fix ändert nichts,
   Runde 2 meldet {A:P1} mit identischem A-Text → Breaker löst in Runde 2 aus
   (`circuit_breaker`-Event, Eskalation, kein zweiter Fix-Dispatch). Gilt für den
   Codex-Code-Review-Loop und den Authoring-Loop.
5. **C25 kein Fehlalarm (Issue-AC 5).** Runde 1 {A:P1, C:P2}, Runde 2 {A':P1}
   mit auch nach Normalisierung geändertem A-Text → kein Breaker.
6. **Eine Implementierung (Issue-AC 6).** Das `finding_key` im
   `triage.decision`-Event ist für dasselbe Finding identisch mit
   `_finding_key(item)`, einschließlich der nur auf `file` angewendeten
   Pfadpräfix-Regel; in `adw/triage.py` gibt es keine eigene Formatkopie mehr.
7. **Regeln belegt (Issue-AC 7).** Ein parametrisierter Test enthält je Regel
   aus E2 (1–8) mindestens ein Eingabe/Erwartung-Paar für `normalize_key_text`,
   darunter: Regel 1 lässt den relativen Rest stehen; Regel 3 ersetzt einen
   ISO-Zeitstempel als Ganzes; Groß-/Kleinschreibung bleibt erhalten.
8. **Persistiertes Format (E5, E6).** Nach einer Codex-Code-Review-Runde mit
   Fix-Dispatch enthält `review_last_failures` Einträge der Form
   `<P1|P2|P3>|<finding_key>` (analog `authoring_last_findings`). Ein State mit
   Basis-Einträgen ohne Severity-Präfix wird ohne Fehler geladen und verglichen;
   der alte Eintrag passt zu jeder Schwelle.
9. **Changelog (A3).** Beide Changelogs enthalten die Sektion
   `## [0.32.1] — Unreleased` mit `### Fixed` und inhaltlich gleichen Punkten;
   der Gate-Breaker-Befund ist ausdrücklich genannt. `pyproject.toml` ist
   unverändert.
10. **Gates (Issue-AC 8).** `uv run ruff check .` und `uv run pytest -x -q` grün.

Richtwert: 8–12 neue Tests (der parametrisierte Regeltest zählt als einer).
Deutlich mehr ist Gold-Plating. Bestehende Tests, die das exakte alte
Schlüsselformat pinnen, werden angepasst statt dupliziert; keine Testmatrix für
bewusst ausgeschlossene Erweiterungen.

## Contract (Single-Lane)

Nur extern beobachtbare Flächen, keine internen Helper-Signaturen:

- `circuit_breaker`-Event: Format unverändert (`keys`, `scope`); wird jetzt bei
  normalisiert gleichen Gate- bzw. Finding-Schlüsseln emittiert.
- `triage.decision`-Payload: `finding_key` =
  `<file ohne Worktree-Präfix>|<normalisierter issue>`.
- State `review_last_failures` und `authoring_last_findings`: Liste von
  `<P1|P2|P3>|<finding_key>`; Einträge ohne Präfix werden toleriert.
- State `deferred_findings`, `final_review_last_failures`,
  `integration_last_failures`, Lane-`last_failures`: normalisierte Schlüssel,
  Format sonst unverändert.

## Definition of Done

- Alle Akzeptanzkriterien 1–10 erfüllt und durch Tests belegt.
- `normalize_key_text` existiert genau einmal in `adw/triage.py`; alle
  Schlüsselbildungen aus dem Scope (Lane-Gate, Integration-Gate, `_finding_key`,
  Triage-Event) nutzen sie, keine verbleibende Formatkopie.
- An Agenten, Eskalationen und Reports übergebene Texte sind unverändert;
  bestehende Tests dazu bleiben grün.
- Beide Changelogs synchron aktualisiert; Version in `pyproject.toml` unverändert.
- `uv run ruff check .` und `uv run pytest -x -q` grün (E7: kein flake8, isort,
  black, mypy).

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Semantische Duplikat-Erkennung, Umformulierungen (W12).
- Normalisierung des an Agenten übergebenen Textes.
- Weitere Normalisierungsregeln über E2 hinaus (z. B. Kleinschreibung, UUIDs,
  PIDs, Hashes ohne `0x`-Präfix).
- Migration alter Schlüssel in States.
- Severity-Präfix oder Schwellenfilter für den Final-Review-Breaker: dort gibt es
  keine Rundenschwelle, kein beobachteter Schaden.
- Schlüsselbasierte Dedupe gegen bereits in `followups.md` stehende Einträge aus
  **früheren** Schreibvorgängen (heute Rohtext-Vergleich in `_write_followups`).
  Bräuchte Report-Parsing oder einen neuen persistenten Schlüsselsatz; der Fall
  des Issues (AC 3) ist über `_without_deferred` abgedeckt, weil ein vertagtes
  Finding den Report gar nicht mehr erreicht.
