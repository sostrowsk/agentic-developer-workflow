# Spec — Run-Detail bleibt 200 bei Events mit Nicht-Mapping-Payload

## Goal

Zwei Helfer der Änderungsumfang-Ableitung in `adw/gui/app.py` — `_snapshots_by_lane`
und `_observed_lanes` — lesen den Event-Payload direkt per
`(e.get("payload") or {}).get(...)`. Ein *truthy Nicht-Mapping*-Payload (Liste,
String, Zahl) löst dabei `AttributeError` aus und verwandelt eine sonst
erfolgreiche Run-Detail-Antwort in ein 5xx — entgegen der Zusage in
`docs/GUI-SPEC.md`, dass ein fehlerhafter Bestandteil „never turns the
otherwise-successful detail request into a 5xx". Beide Helfer sollen den bereits
etablierten Mapping-Guard verwenden, sodass `GET /api/runs/{repo}/{run_id}` in
diesen Fällen 200 liefert und gültige Lanes/Snapshots desselben Laufs
vollständig sichtbar bleiben.

## Scope

- `adw/gui/app.py`: `_snapshots_by_lane(events, run_id)` gewinnt den Payload
  über `_mapping_payload` (oder einen nachweislich gleichwertigen
  `isinstance(..., dict)`-Schutz) statt `e.get("payload") or {}`.
- `adw/gui/app.py`: `_observed_lanes(events, snaps)` gewinnt den Payload des
  `lane`/`start`-Events über denselben Guard statt
  `(e.get("payload") or {}).get("name")`.
- Ein Event mit Nicht-Mapping-Payload wird **still ignoriert** (E1): keine Lane,
  kein Snapshot, kein Eintrag, kein Platzhalter, keine Fehlermeldung in der
  Antwort, kein Log-Eintrag, kein Zähler. Das Ignorieren betrifft nur die
  Änderungsumfang-Ableitung und Snapshot-Zuordnung; es entfernt keine Events aus
  dem gespeicherten Event-Log und ändert nicht die bestehende Trace-Darstellung.
- `tests/test_gui_change_scope.py`: 3–5 neue Tests (Richtwert) unter Nutzung der
  vorhandenen Helfer (`_wrap`, `_lane_start`, `_bad_lane_start`, `_bad_snap`,
  `_ref`, `write_run`, `_client`, `_slug_for`). Für die Nicht-Mapping-Fälle wird
  der gesamte Payload ersetzt (nicht nur ein Feld darin).
- Änderungen bleiben insgesamt auf `adw/gui/app.py` und
  `tests/test_gui_change_scope.py` beschränkt (E2, E5).

## Non-Goals

- KEINE zentrale Event-Validierungs-/Normalisierungsschicht, KEIN Event-Schema,
  KEIN Pydantic-Modell für Events (E2).
- KEINE weiteren Stellen in `adw/gui/app.py` werden auf denselben Fehler
  abgesucht oder mitrepariert; genau die zwei benannten Helfer sind Gegenstand.
  Weitere Vorkommen aus dem Review-Loop gehören in „Deferred" (E3).
- KEINE Änderung an `_mapping_payload` selbst — es ist korrekt und bleibt.
- KEINE Änderung an der bestehenden Behandlung fehlerhafter **Werte innerhalb**
  eines Mapping-Payloads (nicht-String-Lane-Namen, unhashbare Werte, ungültige
  Refs) samt ihrer Tests.
- KEINE Änderung am Diff-Verhalten, an `declared_scope`, an `_is_snapshot_ref`
  oder an der Antwortform von `change_scope`
  (`{"lanes": [...], "declared_scope": ...}`).
- KEINE neue Route, KEIN neues Antwortfeld, KEINE Änderung an einer bestehenden
  Antwortform.
- KEIN neuer Persistenz-Zustand, KEINE Änderung am Event-Log-Format, KEIN
  Migrationsschritt.
- KEINE Schreibpfade in der GUI — sie bleibt read-only.
- KEINE Änderung an `adw/gui/model.py`, `app.js`, `app.css`, den Templates oder
  `i18n.py`. Trace-Baum, Panes, Payload-Formatierung und Markdown-Rendering aus
  den Releases 0.17.0–0.21.0 bleiben unangetastet.
- KEIN neues Frontend-Asset, kein CDN.
- KEINE neue Testdatei und KEIN neues Fixture-Modul (E5).

## Acceptance Criteria

- **AC1** — Ein `lane`/`start`-Event mit `payload` als nichtleerer Liste, als
  nichtleerem String und als Zahl ungleich null (z. B. `["nope"]`, `"text"`,
  `42`) führt neben einer gültigen String-Lane zu **200**; die einzige gemeldete
  Lane ist die gültige String-Lane, die fehlerhaften Events erzeugen keinen
  zusätzlichen Lane-Eintrag.
- **AC2** — Ein `snapshot`-Event mit `payload` als Liste, als String und als
  Zahl führt zu **200**; es bracketiert keinen Knoten und taucht in keiner Lane
  auf. Gültige Snapshots desselben Laufs behalten ihre Zuordnung und Wirkung;
  vorhandene gültige Snapshot-Paare und daraus abgeleitete Diff-Ergebnisse
  werden durch das fehlerhafte Event weder ersetzt noch entfernt.
- **AC3** — Ein Lauf, der NUR Events mit Nicht-Mapping-Payload enthält, liefert
  **200** mit `change_scope.lanes == []` — ohne Fehler und ohne künstlichen
  Lane-Eintrag.
- **AC4** — Ein Regressionstest ruft mindestens einen der beiden Helfer so auf,
  dass er vor dem Fix `AttributeError` wirft (RED-Nachweis gemäß Reproduktion:
  `_observed_lanes([{"type":"lane","kind":"start","seq":1,"payload":["nope"]}], {})`
  bzw. `_snapshots_by_lane([{"type":"snapshot","seq":2,"payload":"text"}], "abcd1234")`).
- **AC5** — `uv run ruff check .` und `uv run pytest -x -q` sind grün.
  Andere Gates (flake8, isort, black, mypy, `ruff format --check`) sind
  ausdrücklich nicht Gegenstand (E4).

## Contract (extern beobachtbare Fläche)

Single-Lane-Projekt (nur `backend`-Lane). Der Contract umfasst ausschließlich
`GET /api/runs/{repo}/{run_id}` bei Events mit Nicht-Mapping-Payload:

- Der Endpunkt antwortet mit **200** (nie 5xx).
- Das Feld `change_scope` behält seine Form
  `{"lanes": [...], "declared_scope": ...}`; gültige Lane-Einträge behalten ihre
  bestehenden Felder und Werte (u. a. `diff_available`, `files`,
  `declared_scope` nach den bisherigen Regeln).
- Nicht-Mapping-Payload-Events sind unsichtbar: keine Lane, kein Bracketing,
  kein Zusatzfeld. Gesunde Lanes/Snapshots desselben Laufs bleiben vollständig
  sichtbar — ein kaputtes Event verdrängt kein gesundes.

Interne Helfer-Signaturen und Modullayouts sind keine Contract-Flächen.

## Definition of Done

- `_snapshots_by_lane` und `_observed_lanes` beziehen den Payload über den
  Mapping-Guard; kein `AttributeError` mehr bei Nicht-Mapping-Payloads.
- AC1–AC5 erfüllt; 3–5 neue Tests in `tests/test_gui_change_scope.py`, davon
  mindestens einer als RED-Nachweis (AC4).
- Bestehendes Verhalten für Mapping-Payloads ist unverändert: die 19 vorhandenen
  Tests in `tests/test_gui_change_scope.py` bleiben **unverändert** grün.
- Keine Änderung außerhalb von `adw/gui/app.py` und
  `tests/test_gui_change_scope.py`.

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
