# Plan — Run-Detail bleibt 200 bei Events mit Nicht-Mapping-Payload

Single-Lane-Projekt: nur der Workstream **backend**. Kein Frontend-Lane.

Ziel: `_snapshots_by_lane` und `_observed_lanes` in `adw/gui/app.py` beziehen den
Event-Payload über den vorhandenen `_mapping_payload`-Guard, sodass ein truthy
Nicht-Mapping-Payload (Liste, String, Zahl) keinen `AttributeError` mehr auslöst.
`GET /api/runs/{repo}/{run_id}` bleibt in diesen Fällen **200**; solche Events
werden still ignoriert (E1). Änderungen ausschließlich in `adw/gui/app.py` und
`tests/test_gui_change_scope.py` (E2/E5).

TDD ist Pflicht (E6): Tests zuerst, RED bestätigen, dann Fix.

## Workstream: backend

### B0 — RED: Tests zuerst (`tests/test_gui_change_scope.py`)

Vier neue Tests in die bestehende Datei, mit deren vorhandenen Helfern (`_wrap`,
`_lane_start`, `_bad_lane_start`, `_bad_snap`, `_ref`, `write_run`, `_client`,
`_slug_for`). Für die Nicht-Mapping-Fälle wird der **gesamte** Payload durch eine
Liste / einen String / eine Zahl ersetzt (nicht nur ein Feld darin). Die 19
bestehenden Tests bleiben **unverändert**.

1. **RED-Nachweis / Helfer-Direktaufruf (AC4).** Ruft mindestens einen der
   Helfer gemäß Reproduktion direkt auf:
   - `_observed_lanes([{"type":"lane","kind":"start","seq":1,"payload":["nope"]}], {})` → Soll `[]`, und/oder
   - `_snapshots_by_lane([{"type":"snapshot","seq":2,"payload":"text"}], "abcd1234")` → Soll `{}`.
   Vor dem Fix muss der Test durch `AttributeError` scheitern; die Exception
   wird NICHT als erwarteter Erfolg abgefangen.
2. **AC1 — `lane`/`start` mit Nicht-Mapping-Payload neben gültiger Lane.**
   Ein Lauf mit einer gültigen String-Lane plus je einem `lane`/`start`-Event,
   dessen ganzer `payload` eine nichtleere Liste (`["nope"]`), ein nichtleerer
   String (`"text"`) und eine Zahl ≠ 0 (`42`) ist. `GET /api/runs/{repo}/{run_id}`
   → **200**; die einzige gemeldete Lane ist die gültige String-Lane; keine
   zusätzlichen Einträge, Platzhalter oder Diagnosefelder.
3. **AC2 — `snapshot` mit Nicht-Mapping-Payload neben gültigem Snapshot-Paar.**
   Ein Lauf mit einem gültigen Snapshot-Paar (→ ableitbares Diff) plus
   `snapshot`-Events aller drei Payload-Varianten, platziert vor, zwischen und
   nach den gültigen Snapshots. → **200**; das fehlerhafte Event bracketiert
   keinen Knoten und taucht in keiner Lane auf; das gültige Snapshot-Paar und
   dessen `diff_available`/`files` bleiben erhalten — weder ersetzt noch
   entfernt. Nur die betroffenen Antwortteile vergleichen.
4. **AC3 — Lauf NUR mit Nicht-Mapping-Payload-Events.** Über `write_run` einen
   Lauf ausschließlich aus betroffenen `lane`/`start`- und `snapshot`-Events
   anlegen (keine gültigen Mapping-Payload-Events). → **200** mit
   `change_scope.lanes == []`, ohne Fehler und ohne künstlichen Lane-Eintrag.

RED bestätigen: die neuen Tests vor dem Fix gezielt ausführen; Test 1 scheitert
mit `AttributeError`, die Endpunkt-Tests scheitern am 5xx.

### B1 — Fix `_snapshots_by_lane` (`adw/gui/app.py`, Zeile ~1331)

`payload = e.get("payload") or {}` ersetzen durch `payload = _mapping_payload(e)`.
Die restliche Logik (seq-`int`-Check, Lane muss nichtleerer String sein,
`_is_snapshot_ref`, Sortierung nach seq) bleibt **unverändert**. Ein Event mit
Nicht-Mapping-Payload liefert `{}`, damit sind `lane`/`ref` `None` und die
`continue`-Bedingung greift → still ignoriert.

### B2 — Fix `_observed_lanes` (`adw/gui/app.py`, Zeile ~2330)

`observe((e.get("payload") or {}).get("name"), e.get("seq"))` ersetzen durch
`observe(_mapping_payload(e).get("name"), e.get("seq"))`. Die innere
`observe`-Prüfung (Name muss nichtleerer String sein, seq `int`) bleibt
**unverändert**. Ein Nicht-Mapping-Payload liefert `name=None` → `observe`
verwirft, keine Lane.

Kein `AttributeError` mehr; keine weiteren `.get`-Stellen anfassen (E3).
Kein Logging, keine Zähler, keine Fehlermeldungen oder Platzhalter (E1).
Gespeicherte Events und bestehende Trace-Darstellung bleiben erhalten.

### B3 — Gates grün und Abschluss (AC5)

- `uv run ruff check .`
- `uv run pytest -x -q`

Alle vier neuen Tests und die 19 unveränderten bestehenden Tests sind grün.
Andere Gates (flake8, isort, black, mypy, `ruff format --check`) sind
ausdrücklich nicht Gegenstand (E4). Abschließend prüfen, dass der gesamte
Änderungsumfang auf `adw/gui/app.py` und `tests/test_gui_change_scope.py`
beschränkt ist und RED-/GREEN-Ergebnisse nachvollziehbar sind.

## Non-Goals / Grenzen (aus der Spec)

- Keine zentrale Event-Validierung/-Normalisierung, kein Event-Schema, kein
  Pydantic-Modell (E2).
- Keine Änderung an `_mapping_payload`, `_is_snapshot_ref`, `declared_scope`,
  am Diff-Verhalten oder an der Antwortform von `change_scope`
  (`{"lanes": [...], "declared_scope": ...}`).
- Keine Änderung an der bestehenden Behandlung fehlerhafter **Werte innerhalb**
  eines Mapping-Payloads samt ihrer Tests.
- Keine neue Route, kein neues Antwortfeld, keine Änderung am Event-Log-Format,
  keine Migration, keine GUI-Schreibpfade.
- Keine Änderung an `adw/gui/model.py`, `app.js`, `app.css`, den Templates oder
  `i18n.py`; Trace-Baum, Panes, Payload-Formatierung und Markdown-Rendering
  (0.17.0–0.21.0) bleiben unangetastet.
- Keine neue Testdatei, kein neues Fixture-Modul (E5); kein neues
  Frontend-Asset, kein CDN.

## Deferred (bewusst nicht gebaut)

Die folgenden Punkte sind defensibel, aber für dieses Issue unverhältnismäßig.
Sie sind **kein Finding** — auch nicht im Review-Loop — und werden in diesem
Lauf nicht gebaut:

- Eine zentrale Event-Validierungs-/Normalisierungsschicht, ein Event-Schema
  oder ein Pydantic-Modell für Events. `_mapping_payload` ist der etablierte
  Mechanismus und genügt (E2).
- Systematisches Absuchen und Reparieren weiterer `.get`-Stellen in
  `adw/gui/app.py` nach demselben Muster; findet der Review-Loop weitere
  Vorkommen, gehören sie hierher, nicht in diesen Lauf (E3).
- Telemetrie, Logging, Zähler oder Diagnosefelder über verworfene Events —
  Nicht-Mapping-Payloads werden still ignoriert (E1).
