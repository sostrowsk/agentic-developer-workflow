# Plan — W4: Gleicher Fehler, gleicher Schlüssel

Maßgeblich ist `.adw/spec.md` (Scope 1–6, AC 1–10). Single-Lane-Projekt
(`.adw/config.yaml`): nur der Workstream **backend**; er umfasst Code, Tests und beide
Changelogs. Kein Frontend-Workstream. Die Vorentscheidungen E1–E7 des Issues sind bindend.

Basis: Stand **0.32.0** (`420af78`). Die Code-Stellen der Spec sind am 2026-11-08 geprüft
und stimmen: `adw/lanes.py:207` und `adw/phases.py:154` (Gate-Schlüssel
`f"{f.gate}|{f.exit_code}|{f.output}"`), `adw/review_policy.py:15` (`_finding_key`),
`adw/triage.py:63` (Formatkopie im `triage.decision`-Event), `adw/triage.py:88`
(`check_progress`, exakter Listenvergleich), `adw/context.py:52` (`_require_progress`,
emittiert `circuit_breaker` mit `keys=failures`). Codex-Code-Review-Breaker:
`adw/phases.py:303–319` (`_advance_review_round` persistiert die Basis im Save des
gestagten Fixes), `:391–412` (Vergleich, danach Basis über `_circuit_breaker_keys`).
Authoring-Breaker: `adw/authoring.py:616`, `:721–727`, `:754`. Vertagen:
`adw/lanes.py:462`. Importkette `review_policy → context → triage` bestätigt. Vor dem
Bauen gegen den dann aktuellen Stand prüfen.

Betroffene Dateien (abschließend): `adw/triage.py`, `adw/review_policy.py`,
`adw/lanes.py`, `adw/phases.py`, `adw/authoring.py`, Tests unter `tests/`,
`CHANGELOG.md`, `CHANGELOG.de.md`. `adw/state.py` bleibt unverändert (die betroffenen
Felder sind bereits `list[str]`, keine neuen Felder), `pyproject.toml` bleibt `0.32.0`.

Contract: `.adw/contract.yaml` pinnt nur die extern beobachtbaren Flächen aus der Spec
(`circuit_breaker`-Event, `finding_key` im `triage.decision`- und `followup`-Payload,
persistierte Schlüsselformate im State). Helfernamen, Signaturen und Ablageorte in
diesem Plan sind Empfehlungen, keine Vertragsfläche — mit Ausnahme von
`normalize_key_text` in `adw/triage.py`, den die Spec (DoD) namentlich festlegt.

## Workstream backend

### B1 — `normalize_key_text` (Scope 1, AC 7)

Datei: `adw/triage.py`.

1. `normalize_key_text(text: str) -> str`, reine Funktion, keine I/O, keine Konfiguration,
   keine Klasse, kein neues Modul. Regeln als vorkompilierte Regex auf Modulebene,
   angewendet in genau der Reihenfolge 1–8 der Spec-Tabelle. Keine Kleinschreibung,
   keine weiteren Regeln.
2. Regel 1 als eigene kleine Funktion bzw. eigene Regex (z. B.
   `_strip_worktree_prefix`), weil B3 sie isoliert auf `file` anwendet. Das Präfix kommt
   in drei Formen vor und muss in allen entfernt werden: absolut
   (`/repo/.adw/runs/1234abcd/trees/backend/src/foo.py`), mit relativem Repo-Pfad
   (`../repo/.adw/runs/…/trees/backend/src/foo.py`) und **nackt relativ**
   (`.adw/runs/1234abcd/trees/backend/src/foo.py`, Form aus Spec AC 1). Der Teil vor
   `.adw` ist deshalb optional: Muster sinngemäß
   `(?:\S*/)?\.adw/runs/[0-9a-f]{8}/trees/[^/\s]+/` → `""`; der relative Rest
   (`src/foo.py`) bleibt. Ein Muster, das einen Slash vor `.adw` verlangt, lässt die
   Run-ID in der nackten Form stehen und der Gate-Breaker verfehlt AC 1.
3. Umsetzungshinweise, damit die festgelegte Reihenfolge die Beispiele der Spec trifft:
   - Regel 3: zuerst ISO-Zeitstempel komplett
     (`\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?`), danach die
     reine Uhrzeit — ergibt ein einziges `<ts>`, nicht `2026-11-08T<ts>`.
   - Regel 4: die Zahl am Wortanfang verankern (`\b\d+…`), damit Bezeichner wie
     `test_1s` nicht zu `<dur>` werden (sonst könnten unterschiedliche Testnamen
     zusammenfallen, AC 2). Das präzisiert Regel 4 (eine Dauer ist ein eigenes Token),
     fügt keine Regel hinzu.
   - Regel 6: die Dateiendung muss mit einem Buchstaben beginnen (z. B.
     `(\.[A-Za-z]\w*):\d+(:\d+)?` → `\1:<n>`), sonst ersetzt Regel 6 den Port in
     `127.0.0.1:8000` vor Regel 7 und das Spec-Beispiel `<host>:<port>` wird nie
     erreicht. `line \d+` → `line <n>`.
   - Regel 8: `\s+` → `" "`, dann `strip()`.

### B2 — Gemeinsame Gate-Schlüssel (Scope 2, AC 1, 2)

Dateien: `adw/triage.py` (Helfer), `adw/lanes.py:207`, `adw/phases.py:154`.

1. Eine gemeinsame Schlüsselbildung neben `normalize_key_text`, Empfehlung
   `gate_failure_keys(report: GateReport) -> list[str]` mit
   `f"{f.gate}|{f.exit_code}|{normalize_key_text(f.output)}"` je Failure. Gate-Name und
   Exit-Code bleiben unverändert Teil der Identität. Keine neue Datei.
2. Beide Formatkopien (Lane-Gate-Loop in `_run_lane`, `_integration_loop`) durch den
   Aufruf ersetzen. Fortschrittsprüfung, Event-Ausgabe, Eskalationspfade und
   Speicherzeitpunkte bleiben, wie sie sind.
3. `_gate_failure_text(report)` für Agent-Task und Eskalation bleibt unverändert
   (unnormalisiert, gekürzt auf `MAX_OUTPUT_LINES` wie heute).
4. Persistiert werden in `last_failures` / `integration_last_failures` die normalisierten
   Schlüssel (Format sonst gleich). Keine Migration alter Einträge.

### B3 — Gemeinsame Finding-Schlüssel (Scope 3, 4; AC 3, 6)

Dateien: `adw/triage.py`, `adw/review_policy.py`.

1. In `adw/triage.py` eine öffentliche Schlüsselbildung, Empfehlung
   `finding_key(item: Finding) -> str` =
   `f"{_strip_worktree_prefix(item.file)}|{normalize_key_text(item.issue)}"` — auf
   `file` **nur** Regel 1; Zeilennummern und Leerraum in `file` bleiben unterscheidend.
   Keine Rückimporte von `triage` nach `review_policy` oder `context`, damit die
   Importkette `review_policy → context → triage` zyklenfrei bleibt.
2. `triage_final_review` nutzt sie für `finding_key` im `triage.decision`-Payload; die
   Formatkopie `f"{item.file}|{item.issue}"` entfällt. Übrige Payload-Felder
   (`severity`, `action`, `reason`) unverändert.
3. `review_policy._finding_key` delegiert an diese Funktion (Name `_finding_key` bleibt
   wegen der bestehenden Importe in `lanes.py`, `phases.py`, `authoring.py`). Damit
   normalisieren ohne weitere Änderung: `_finding_keys`, `_circuit_breaker_keys`,
   `_without_deferred`, Vertagen in `lanes.py:462`, Dedupe-Menge `seen` in
   `_write_followups`, `finding_key` im `followup`-Event.
4. Unverändert: der Rohtext-Vergleich `recorded = … in existing` in `_write_followups`
   (Deferred), `text` im `followup`-Event, alle an Agenten/Eskalation/Report gehenden
   Texte (`_findings_text`, `_disposition_line`, Report-Einträge), die Finding-Objekte.

### B4 — C25: Vergleich an der Schwelle der aktuellen Runde (Scope 5, AC 4, 5, 8)

Dateien: `adw/review_policy.py` (Helfer), `adw/phases.py` (Codex-Code-Review),
`adw/authoring.py` (Authoring).

1. Zwei kleine Helfer neben `_actionable_severities` (Namen sind Empfehlung):
   - `_severity_keys(findings) -> list[str]`: sortiert `f"{item.severity}|{_finding_key(item)}"`.
   - `_baseline_for_round(previous: list[str] | None, round_no: int) -> list[str] | None`:
     `None` bleibt `None`. Sonst je Eintrag: beginnt er mit `P1|`/`P2|`/`P3|` (nur dieses
     führende Präfix wird erkannt und abgetrennt), wird er nur übernommen, wenn die
     Severity in `_actionable_severities(round_no)` liegt, und ohne Präfix zurückgegeben;
     Einträge ohne Präfix (alter State) werden unverändert übernommen und passen damit
     zu jeder Schwelle. Keine Migration, keine nachträgliche Normalisierung.
2. **Codex-Code-Review** (`run_codex_review_phase`):
   - Vergleich: `current = _finding_keys(review)` (`review` ist an dieser Stelle bereits
     auf die actionable Findings der Runde gefiltert, Schlüssel ohne Präfix);
     `_require_progress(ctx, _baseline_for_round(previous, round_no), current, …)`.
     Damit trägt `circuit_breaker.keys` wie bisher Finding-Schlüssel ohne Präfix.
   - Persistieren: `_advance_review_round(..., failures)` erhält
     `_severity_keys(review.findings)` (Checkpoint im selben Save wie der Fix-Task);
     die Basis nach dem Dispatch wird `_severity_keys(_without_deferred(ctx, review.findings))`
     (Ausnahme vertagter Findings bleibt erhalten). Das lokale `previous` hält das
     präfixierte Format. Atomare Saves und Reihenfolge wie heute.
3. **Authoring** (`_authoring_loop`, `adw/authoring.py:721–754`): gleiches Muster —
   Vergleich `_require_progress(ctx, _baseline_for_round(previous_failures, round_no),
   _finding_keys(review), …)`; `previous_failures` und `authoring_last_findings` erhalten
   `_severity_keys(review.findings)` und werden am bestehenden Checkpoint zusammen mit
   Pending-Task, Rundenzähler und Findings-Verlauf gespeichert.
4. Unverändert: Final-Review-Breaker (`adw/phases.py:477`, `:515`,
   `final_review_last_failures`: normalisierte Schlüssel ohne Präfix, kein Filter —
   die gemeinsame Nutzung von `_circuit_breaker_keys` dort darf dessen Format nicht
   ändern), Gate-/Integration-Breaker, `check_progress`, `_require_progress`, Schwellen,
   Rundenlimits, Behandlung leerer/actionable Findings, Deferred-P3-Semantik.

### B5 — Changelog (Scope 6, AC 9)

- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion `## [0.32.1] — Unreleased`
  oberhalb von `## [0.32.0] — 2026-11-08`, mit `### Fixed` im Stil der vorhandenen
  Einträge (fette Kopfzeile, Unterpunkte, jeweilige Sprache). Inhalt: Gate-Breaker konnte
  bei pytest praktisch nie auslösen (Laufzeit in der Zusammenfassung) → normalisierte
  Gate-Schlüssel; normalisierte Finding-Schlüssel (Zeilennummern, Pfadpräfix, Leerraum)
  inkl. `triage.decision`; C25 (Basis und aktuelle Findings an der Schwelle der
  aktuellen Runde).
- `pyproject.toml` bleibt `0.32.0`.

## Reihenfolge

B1 → B2 und B3 (beide brauchen `normalize_key_text`) → B4 (baut auf `_finding_key` aus
B3) → B5. Jeden neuen Test zuerst gegen den Ausgangsstand rot sehen.

## Tests (Richtwert 8–12 neue Tests)

Bestehende Fixtures und Muster wiederverwenden: `ctx`/`pctx`/`target_repo`,
`prepare_reviewable`, `ctx.codex.script(code_findings(...))`, `ctx.agents.script` in
`tests/test_phases.py`; Emitter-Erfassung wie in `test_authoring_circuit_breaker_emits_the_event`
(`tests/test_phases.py:4474`). Vorlagen für die Breaker-Szenarien:
`test_identical_gate_failures_trigger_circuit_breaker` (`:639`),
`test_identical_e2e_failures_trigger_circuit_breaker` (`:2263`),
`test_circuit_breaker_ignores_findings_below_the_threshold` (`:2591`). Keine echten
Codex-/SDK-Aufrufe.

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 7 | Parametrisiert: je Regel 1–8 mindestens ein Paar für `normalize_key_text`, darunter Regel 1 sowohl mit absolutem Präfix als auch nackt relativ (`.adw/runs/1234abcd/trees/backend/src/foo.py` → `src/foo.py`), jeweils mit erhaltenem relativem Rest; ISO-Zeitstempel als ein `<ts>`; Groß-/Kleinschreibung bleibt | `tests/test_triage.py` |
| 1 | Zwei pytest-Outputs, die sich nur in `in 36.02s`/`in 1.65s`, `/tmp/pytest-of-x/pytest-3/…`/`…/pytest-7/…` und Run-ID im Worktree-Pfad unterscheiden — die Worktree-Pfade in der **nackten relativen** Form `.adw/runs/<id1>/trees/backend/…` vs. `.adw/runs/<id2>/trees/backend/…`: zwei rote Lane-Gate-Iterationen → `circuit_breaker`-Event, Eskalation, keine dritte Iteration | `tests/test_phases.py` |
| 2 | Gleiche Outputs, aber anderer fehlschlagender Testname → kein Breaker nach zwei Iterationen | dto. |
| Scope 2 | Integration/E2E: Bestandstest `:2263` so erweitern, dass sich die beiden Outputs nur volatil unterscheiden (Dauer, Temp-Pfad) → Breaker greift weiterhin (Erweiterung, kein neuer Test) | dto. |
| 3 | P3-Finding in `deferred_findings`, neues Finding mit gleichem `file`, `issue` nur in `foo.py:12`/`foo.py:15` und Leerraum verschieden → `_without_deferred` filtert es; Follow-up-Report enthält den Text einmal, unnormalisiert | `tests/test_phases.py` |
| 4 | Codex-Code-Review: Runde 1 {A:P1, B:P3}, Fix ohne Wirkung, Runde 2 {A:P1} → Breaker in Runde 2, kein zweiter Fix-Dispatch | dto. |
| 4 | Authoring-Loop: dasselbe Szenario → Breaker in Runde 2 | dto. |
| 5 | Runde 1 {A:P1, C:P2}, Runde 2 {A':P1} mit auch normalisiert geändertem Text → kein Breaker (ein Loop genügt, Codex-Code-Review) | dto. |
| 6 | `triage.decision`-`finding_key` == `_finding_key(item)` für ein Finding mit Worktree-Präfix im `file` und Zeilennummer im `issue`; Zeilennummer im `file` bleibt unterscheidend | `tests/test_instrumentation.py` (neben `:471`) |
| 8 | Nach einer Runde mit Fix-Dispatch: `review_last_failures` (Checkpoint und Basis nach Dispatch) besteht aus `<P1\|P2\|P3>\|<finding_key>`; ein State mit präfixlosem Alt-Eintrag wird geladen und gegen Runde 2 verglichen (Alt-Eintrag passt zu jeder Schwelle → Breaker bei gleichem Schlüssel) | `tests/test_phases.py` (ggf. Erweiterung von `:2565`) |
| 9 | Changelogs: kein eigener Test, Prüfung im Review | — |
| 10 | `uv run ruff check .`, `uv run pytest -x -q` | Gate |

Bestandstests, die das exakte alte Schlüsselformat pinnen, werden angepasst statt
dupliziert. Keine Testmatrix für bewusst ausgeschlossene Erweiterungen (Deferred).
Bestehende Tests zur unveränderten Weitergabe von Texten bleiben grün.

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); die gesamte bestehende Suite bleibt grün. Kein
  flake8, isort, black, mypy (E7).
- AC 1–10 erfüllt und durch Tests belegt.
- `normalize_key_text` existiert genau einmal in `adw/triage.py`; Lane-Gate,
  Integration-Gate, `_finding_key` und Triage-Event nutzen sie, keine Formatkopie bleibt.
- Neue Review-/Authoring-Basen tragen Severity; beide Seiten werden an der aktuellen
  Rundenschwelle ohne Severity verglichen. Alt-State bleibt ladbar, ohne Migration.
- An Agenten, Eskalationen, Reports und Event-Payloads (außer `finding_key`) gehende
  Texte unverändert; `MAX_OUTPUT_LINES` unverändert.
- Changelogs EN/DE synchron mit `## [0.32.1] — Unreleased`; `pyproject.toml` unverändert.
- Keine neuen State-Felder, keine Konfigurationsoptionen, keine Migration, kein
  geändertes `circuit_breaker`-Event-Format.

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
