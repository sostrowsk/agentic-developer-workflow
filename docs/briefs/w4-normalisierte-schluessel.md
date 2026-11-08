# W4: Gleicher Fehler, gleicher Schlüssel

Vierter Lauf der Reihe aus `ONBOARDING-SPEC-PLAN.md` (Paket W4 mit Befund C25).
Setzt auf dem gemergten Stand **0.32.0** (nach R1, R2 und W1) auf. Die Ausgangslage ist am
2026-11-08 im Code geprüft und vor dem Bauen gegen den dann aktuellen Stand zu
prüfen (Zeilennummern können sich verschoben haben).

## Ausgangslage (im Code verifiziert, 2026-11-08)

### Der Circuit Breaker der Gates kann bei pytest-Fehlern nie auslösen

`_run_lane` (`adw/lanes.py:~207`) bildet die Breaker-Schlüssel eines roten Gates als
`f"{f.gate}|{f.exit_code}|{f.output}"` — mit dem **vollen** (auf 200 Zeilen
gekürzten) Gate-Output. `check_progress` (`adw/triage.py:88`) vergleicht die
Schlüssel zweier Iterationen **exakt**. Die pytest-Zusammenfassung enthält immer
die Laufzeit (`… failed in 36.02s`, gemessen in den Gate-Events der vorhandenen
Läufe: `in 36.02s`, `in 1.65s`, `in 33.30s`). Zwei Iterationen mit identischem
Fehler unterscheiden sich damit fast immer → der Breaker schlägt nie an, der Lauf
reizt das Iterationslimit aus. In **keinem** der 15 vorhandenen Läufe gibt es ein
`circuit_breaker`-Event.

Dasselbe gilt für Pfade (Worktree-Pfade enthalten die Run-ID,
`.adw/runs/<id>/trees/<lane>/`), Temp-Verzeichnisse (`/tmp/pytest-of-…/pytest-N/`),
Speicheradressen (`0x7f…`) und Zeitstempel in Tracebacks/Logs.

### Findings werden über den exakten Text erkannt

`_finding_key` (`adw/review_policy.py:15`) ist `f"{item.file}|{item.issue}"`. Er
bestimmt den Codex-Breaker (`adw/phases.py:~387`, `adw/authoring.py:~721`), die
Deferred-Liste (`_without_deferred`, `ctx.state.deferred_findings`), die
Dedupe im Follow-up-Report (`:~120–149`) und das `finding_key` im Triage-Event
(`adw/triage.py:63`, eigene Kopie des Formats). Ein Finding, das sich nur in
Zeilennummer, Pfadpräfix oder Leerraum unterscheidet, gilt als neu.

### C25 — Breaker vergleicht über verschiedene Severity-Schwellen

`_actionable_severities` (`adw/review_policy.py`) senkt die Schwelle je Runde
(1: P1–P3, 2: P1+P2, ab 3: P1). Die Basis für Runde N+1 sind alle nicht vertagten
Findings der Runde N (`_circuit_breaker_keys`, alle Severities); verglichen wird
mit allen Findings der Runde N+1. Szenario: Runde 1 meldet {A:P1, B:P3}, der Fix
ändert nichts; Runde 2 meldet {A:P1} (B liegt unter der Schwelle). {A} ≠ {A, B} →
kein Breaker, ein zweiter teurer Fix-Lauf. Die Schlüssel tragen keine Severity, die
Basis lässt sich deshalb heute nicht nachträglich filtern.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Die Severity-Schwellen je Runde, das Rundenlimit und die Semantik von
  „Deferred = P3-Einschätzung" bleiben.
- Der an Agenten übergebene Gate-Output und die Findings-Texte bleiben
  **ungekürzt und unnormalisiert** — normalisiert wird nur der Schlüssel.
- Die Kürzung des Gate-Outputs auf 200 Zeilen (`MAX_OUTPUT_LINES`) bleibt.
- Keine semantische Ähnlichkeit (Embeddings) — das ist W12.

## Aufgabe

**A1 — Eine Normalisierungsfunktion** für Schlüssel, genutzt von **allen**
Schlüsselbildungen: Gate-Breaker, `_finding_key` (und damit Codex-Breaker,
Deferred, Follow-up-Dedupe) und das `finding_key` im Triage-Event (dort die eigene
Formatkopie durch den gemeinsamen Helfer ersetzen).

**A2 — C25.** Der Codex-Breaker vergleicht nur Findings oberhalb der Schwelle der
**aktuellen** Runde, in Basis und aktueller Runde gleichermaßen.

**A3 — Changelog.** `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
`## [0.32.1] — Unreleased` mit `### Fixed`, im Stil der vorhandenen Einträge;
ausdrücklich nennen, dass der Gate-Breaker vorher bei pytest praktisch nie
auslösen konnte. Version in `pyproject.toml` **nicht** ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 — Ort:** eine Funktion `normalize_key_text(text: str) -> str` in
  `adw/triage.py` (dort liegt `check_progress`). Kein neues Modul, keine Klasse,
  keine Konfiguration.
- **E2 — Regeln, in dieser Reihenfolge, als vorkompilierte Regex:**
  1. Worktree-/Run-Pfadpräfix `…/.adw/runs/<8 hex>/trees/<lane>/` → entfernt
     (relativer Rest bleibt)
  2. Temp-Pfade `/tmp/\S+` und `/private/var/folders/\S+` → `<tmp>`
  3. ISO-Zeitstempel (`YYYY-MM-DD[T ]HH:MM:SS[.frac][Z|±HH:MM]`) → `<ts>`, danach
     Uhrzeiten `HH:MM:SS[.frac]` → `<ts>`
  4. Dauern `\d+(\.\d+)?\s?(ms|s|sec|seconds|min)\b` → `<dur>`
  5. Hex-Adressen `0x[0-9a-fA-F]+` → `<hex>`
  6. Zeilen/Spalten: `:\d+(:\d+)?` direkt nach einem Dateinamen mit Endung sowie
     `line \d+` → `:<n>` bzw. `line <n>`
  7. `localhost:\d+` und `127.0.0.1:\d+` → `<host>:<port>`
  8. Leerraum: Folgen von Whitespace → ein Leerzeichen, `strip()`
  Keine Kleinschreibung, keine weiteren Regeln.
- **E3 — Gate-Schlüssel:** `f"{gate}|{exit_code}|{normalize_key_text(output)}"`.
- **E4 — Finding-Schlüssel:** `f"{file}|{normalize_key_text(issue)}"`; `file`
  wird nur mit Regel 1 behandelt (Pfadpräfix), nicht mit den übrigen Regeln.
- **E5 — C25:** Die gespeicherte Breaker-Basis (`review_last_failures`) trägt die
  Severity mit: Einträge `f"{severity}|{finding_key}"`. Beim Vergleich in Runde N
  werden Basis und aktuelle Findings auf `_actionable_severities(N)` gefiltert und
  dann ohne Severity verglichen. Einträge ohne Severity-Präfix (alte States)
  gelten als passend zu jeder Schwelle. Gilt für Code-Review- **und**
  Authoring-Breaker (beide nutzen dieselbe Schwellenlogik); der Gate-Breaker hat
  keine Severities und bleibt bei E3.
- **E6 — Keine Migration:** Alte `deferred_findings`/`review_last_failures` mit
  unnormalisierten Schlüsseln bleiben, wie sie sind; im schlimmsten Fall wird ein
  Finding einmal mehr dispatcht.
- **E7:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Kein
  flake8, isort, black, mypy.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Semantische Duplikat-Erkennung, Umformulierungen (W12).
- Normalisierung des an Agenten übergebenen Textes.
- Weitere Normalisierungsregeln über E2 hinaus.
- Migration alter Schlüssel in States.

## Akzeptanzkriterien

1. Zwei pytest-Gate-Outputs, die sich nur in `in 36.02s` / `in 1.65s`, im
   Temp-Pfad und in der Run-ID im Worktree-Pfad unterscheiden → gleicher Schlüssel;
   zwei aufeinanderfolgende Iterationen damit → Breaker schlägt an
   (`circuit_breaker`-Event, Eskalation).
2. Zwei Outputs mit unterschiedlichem fehlschlagendem Test → verschiedene
   Schlüssel, kein Breaker.
3. Ein Finding, das sich nur in Zeilennummer (`foo.py:12` vs. `foo.py:15`) und
   Leerraum unterscheidet, wird als vertagt erkannt (`_without_deferred`) und im
   Follow-up-Report nicht doppelt geführt.
4. C25: Runde 1 {A:P1, B:P3}, Runde 2 {A:P1} → Breaker schlägt an.
5. Runde 2 {A:P1} nach Runde 1 {A:P1, C:P2} mit geändertem A-Text → kein Breaker.
6. Triage-Event `finding_key` entspricht `_finding_key` (eine Implementierung).
7. Je Regel aus E2 ein Beispiel in einem parametrisierten Test.
8. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 8–12 neue Tests (der parametrisierte Regeltest zählt als
einer). Deutlich mehr ist ein Zeichen für Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen
(`circuit_breaker`-Event, `finding_key` im Triage-Event, Format von
`review_last_failures` im State), keine internen Helper-Signaturen.
