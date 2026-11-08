# Plan — W3: `adw kpis` — Kennzahlen über alle Läufe aus dem Ledger

Maßgeblich ist `.adw/spec.md` (Scope §1–§6, AC 0–8). Single-Lane-Projekt
(`.adw/config.yaml`): nur der Workstream **backend**; er umfasst Code, Tests, Fixture,
beide Handbücher und beide Changelogs. Kein Frontend-Workstream, keine Übergabe.

Basis: Stand **0.33.0** (`83f2e40`), am 2026-11-08 gegen den Code geprüft:
`_issue_title` `adw/ledger.py:130` (erste nicht-leere Zeile); `findings`/`review_counts`
`adw/ledger.py:251–271` (lesen `e.get("phase")`/`e.get("round")`); `_ledger_path`
`adw/ledger.py:279` (`None` ohne Verzeichnis/`.git`, sonst `RuntimeError` bei
gescheiterter Probe; `os.stat`-Fehler außer ENOENT/ENOTDIR propagieren als `OSError`,
`run_git` kann `subprocess.TimeoutExpired` werfen); Helfer `_parse_ts` (`:98`),
`_is_number` (`:108`, schließt `bool` aus), `_spans` (`:83`), `_payload` (`:93`) vorhanden.
ADW-Kopfzeile `# Issue (ADW-generiert aus dem Run-State — nicht manuell editieren)` +
Leerzeile + Issue-Text (`adw/authoring.py:777`). Spannen: `phase` mit `payload.name`
(`adw/context.py:243`, `adw/authoring.py:146/192`), `round` mit `payload.{loop, n, cap}`
(`adw/authoring.py:635` `loop "authoring"`, `adw/phases.py:344` `"codex_review"`,
`:437` `"final_review"`, ferner `phases.py:138`, `lanes.py:174`); `triage.decision`
trägt die vom Aufrufer übergebene umschließende Spanne im Feld `span`
(`adw/triage.py:99–108`). CLI: `_fail` `adw/cli.py:52`, `ledger_app` `:575`,
`runs prune` lehnt negatives `--older-than` mit `typer.Exit(2)` ab (`:643`).
Tests: autouse-Fixture `_isolate_ledger` (`tests/conftest.py:52`) setzt `ADW_LEDGER` auf
einen nicht existierenden Pfad; Fixture `ledger` (`tests/conftest.py:60`) liefert einen
echten Ledger-Klon mit `.git` und setzt `ADW_LEDGER` darauf. Handbuch: §7 „CLI
reference" (`docs/ADW-HANDBOOK.md:350`), §11.1 „Ledger" (`:603`), DE analog.
Vor dem Bauen gegen den dann aktuellen Stand prüfen.

Betroffene Dateien (abschließend): `adw/ledger.py`, `adw/cli.py`;
`tests/fixtures/ledger/a1b2c3d4/{issue.md,events.jsonl}`,
`tests/fixtures/ledger/expected-run-record.json`, `tests/test_ledger.py`, neu
`tests/test_kpis.py`; `docs/ADW-HANDBOOK.md`, `docs/ADW-HANDBOOK.de.md`,
`CHANGELOG.md`, `CHANGELOG.de.md`. GUI, `adw/events.py`, `adw/retention.py`,
Ledger-Layout, Export-Mechanismus und `pyproject.toml` (bleibt `0.33.0`) unverändert.

Contract: `.adw/contract.yaml` pinnt nur extern beobachtbare Flächen (CLI `adw kpis`,
Exit-Codes, JSON-Struktur, Regelnamen, die Wertänderung im `run-record.json`).
Helfernamen und -aufteilung in diesem Plan sind Empfehlungen; `compute_kpis` legt die
Spec selbst fest (§2 „Ort") — das ist Plan-, nicht Contract-Fläche.

## Workstream backend

### B1 — Datenkorrektur im Lauf-Protokoll (Scope §1; AC 0)

Datei: `adw/ledger.py`, nur `build_run_record` und seine Helfer.

1. **`_issue_title`:** Zeilen von `issue.md`; ist die erste nicht-leere Zeile (getrimmt)
   eine ADW-Kopfzeile (`startswith("# Issue (ADW-generiert")`), weiter mit den Zeilen
   danach, sonst mit allen (ein Issue ohne Kopfzeile verliert seinen Titel nicht). Darin
   die erste Zeile, die (getrimmt) mit `#` beginnt → `lstrip("#").strip()`; ist das leer,
   als nicht gefunden behandeln. Keine Überschrift → erste nicht-leere Zeile getrimmt;
   sonst `null`. Ohne Datei `null` wie heute.
2. **Span-Hierarchie:** einmal je Record ein Index `span_id → Start-Event` (erstes
   `kind == "start"` je `span`). Helfer `_span_context(starts, span_id) ->
   (phase, round, loop)`: von `span_id` aus die Kette über `starts[id]["parent"]`
   hinauf; die erste Spanne vom Typ `round` liefert `round = payload.n`,
   `loop = payload.loop`, die erste vom Typ `phase` liefert `phase = payload.name`
   (unabhängig voneinander). Abbruch bei fehlendem Start-Event, `parent` `null` oder
   bereits besuchter ID (`visited`-Menge gegen Zyklen); nicht gefundene Werte `null`.
   Kein Rückgriff auf Top-Level-`phase`/`round` der Events.
3. Anwenden: `findings[]` je `triage.decision` mit Start bei `e["span"]`;
   `review_counts[]` je `codex.review`-Ende ebenfalls mit Start bei `e["span"]` (die
   eigene `codex.review`-Spanne ist weder `round` noch `phase`; die Kette läuft über
   das `parent` ihres Start-Events weiter — genau die Spec-Regel). Schlüssel:
   `findings`: `finding_key, severity, action, reason, phase, round, loop`;
   `review_counts`: `phase, round, loop, n`. Alle übrigen Felder unverändert,
   `schema_version` bleibt 1.
4. **Fixture auf echtes Format** (`tests/fixtures/ledger/a1b2c3d4/`):
   - `issue.md`: Kopfzeile wie `authoring.py:777`, Leerzeile, dann der bisherige Inhalt
     (`# W99: Beispiel-Issue für das Ledger` …).
   - `events.jsonl`: Top-Level-`phase` und `round` aller Events `null`. Um die
     Spec-Review eine `phase`-Spanne (`name "spec"`) mit einer `round`-Spanne
     (`loop "authoring"`, `n 1`) darin; `codex.review` (spec) mit `parent` = Round-Spanne,
     die beiden `triage.decision` mit `span` = Round-Spanne. Um die Code-Review eine
     `phase`-Spanne (`name "codex_review"`) mit zwei `round`-Spannen
     (`loop "codex_review"`, `n 1` und `n 2`); Reviews und `triage.decision` darin
     analog. `seq` neu durchnummerieren; die abgeschnittene Zeile (heute `seq 18`), beide
     `run`-Spannen, Totals, `approval`, `circuit_breaker` und `agent.run`-Spannen bleiben
     inhaltlich gleich, damit sich am Golden-File nur die A0-Felder ändern.
   - `expected-run-record.json`: `findings` mit `phase/round/loop` =
     `spec/1/authoring` (2×) und `codex_review/1/codex_review`; `review_counts` =
     `spec/1/authoring n 2`, `codex_review/1/codex_review n 1`,
     `codex_review/2/codex_review n 0`; `issue_title` bleibt
     `"W99: Beispiel-Issue für das Ledger"`; Rest unverändert.

### B2 — `compute_kpis` und Ledger-Lesen (Scope §2–§4; AC 1–3, 5)

Datei: `adw/ledger.py`. Rein lesend, keine Modellaufrufe, keine neue Dependency.

1. **Lesen** (`_read_ledger_records(ledger) -> list`): `sorted(ledger.glob(
   "runs/*/*/run-record.json"))`; je Datei `json.loads(read_text("utf-8"))`; bei
   `ValueError`/`UnicodeDecodeError`/`OSError` → `None`. Rückgabe: Liste der geparsten
   Werte (ungültig → `None`). Keine weitere Feldvalidierung, keine Diagnose fehlerhafter
   Einzelwerte (Deferred).
2. **`compute_kpis(records, since=None, repo_slug=None) -> dict`** (Signatur aus der
   Spec, keine weiteren Parameter; „jetzt" = einmal `datetime.now(UTC)` je Aufruf):
   - Gültig ist ein Wert nur als `dict` mit `type(schema_version) is int` und `== 1`
     (`True`/`1.0` gelten nicht). Alles andere (auch `None` aus B2.1) zählt in
     `skipped`. Filter danach; Filter-Ausfälle zählen nicht als übersprungen.
   - `since`: `cutoff = now - timedelta(days=since)`; Lauf bleibt nur bei
     `_parse_ts(started_at) >= cutoff`; fehlend/unlesbar → raus. `repo_slug`: exakte
     Gleichheit (Record-Form `acme/widget`). Beide Filter wirken gemeinsam vor
     Gruppierung, `total`, Kandidatenauswahl und Kosten-Median.
   - Gruppen nach `adw_version` (nicht `adw_versions`); Sortierschlüssel: Version als
     `str` aus nur Ziffern-Komponenten → `(0, tuple(int))`; sonstiger Nicht-`null`-Wert →
     `(1, str(v))`; `null` → `(2,)`. Je Gruppe `{"adw_version": v, **_metrics(runs)}`;
     `total = _metrics(alle gefilterten)` (direkt aus den Records, ohne
     `adw_version`-Schlüssel; bei 0 Läufen alle Schlüssel vorhanden, `runs: 0`, Werte
     `null`, Maps leer).
   - `_metrics(runs)` nach der Tabelle in Spec §3. Werte über `_is_number` filtern;
     leere Wertmenge → `null` (Summe, Median, Max); gemessene `0` bleibt gültig. Median:
     `statistics.median`. Anteile: Nenner `runs`, bei `0` alle `null`.
     `phase_shares.other` = alle, die weder `"done"` noch `"escalated"` sind.
     `escalation_share`: `phase == "escalated"` oder `escalation_reason is not None`.
     `gate_iterations` je Lauf = Summe der numerischen `lanes.*.gate_iterations` (kein
     numerischer Wert/keine Lanes → Lauf ignoriert, keine erfundene 0); `fix_cycles` ist
     im Record bereits über Lanes summiert. `agent_cost_shares`: nur Einträge mit
     `str`-`agent` und numerischem `cost_usd`; keine Einträge → `{}`; Einträge vorhanden,
     Gesamtsumme `0` → je Agent `null` (Spec §3 „ohne Nenner `null`").
     `findings_by_severity`/`_by_action`: nur `str`-Werte zählen (JSON-Schlüssel).
     Nicht-Listen/Nicht-Dicts werden wie fehlende Werte behandelt.
   - **Kandidaten** über die gefilterten Läufe, Regeln in der Spec-Reihenfolge
     `escalated, review_rounds, gate_iterations, circuit_breakers, cost_usd`;
     `cost_usd`: `median` der numerischen `cost_usd` aller gefilterten Läufe (über
     Versionsgruppen hinweg), Regel greift nur bei Median ≠ `null` und
     `cost_usd > 1.5 * median`. `gate_iterations`: irgendeine einzelne Lane mit
     numerischem Wert `>= 5` (nicht die Summe). Ein Lauf höchstens einmal, `rules` nie
     leer. Je Kandidat `{run_id, repo_slug, adw_version, rules, issue_title}`;
     Sortierung nach (`started_at` geparst, ungültig/fehlend zuletzt), dann `str(run_id)`.
   - Rückgabe `{"groups": [...], "total": {...}, "candidates": [...],
     "skipped": int}`; JSON-Werte ungerundet.

### B3 — CLI `adw kpis` (Scope §2, §5; AC 4–7)

Datei: `adw/cli.py`, `@app.command("kpis")` (Top-Level, nicht unter `ledger_app`).

1. Optionen: `--since` (`int | None`), `--repo-slug` (`str | None`), `--json` (bool).
   `since < 0` → Meldung auf stderr, `typer.Exit(2)` (wie `runs prune --older-than`),
   vor jedem Ledger-Zugriff.
2. Ledger: `ledger._ledger_path()` in `try/except (RuntimeError, OSError,
   subprocess.SubprocessError)`; Ausnahme oder `None` → `_fail(...)` mit dem Pfad
   (`ADW_LEDGER` expandiert), Exit 1, kein Traceback. Vorhandenes Ledger ohne Records →
   reguläres leeres Ergebnis, Exit 0.
3. `result = ledger.compute_kpis(ledger._read_ledger_records(path), since=…,
   repo_slug=…)` — genau einmal berechnet.
4. `--json`: `typer.echo(json.dumps(result, ensure_ascii=False))` — genau ein Objekt.
   Sonst Textformatierung aus genau diesem `result` (privater Helfer in `cli.py`):
   Tabelle mit fester Spaltenfolge aus Spec §5, eine Zeile je Gruppe plus `gesamt`;
   je Gruppe/`gesamt` eine Zeile mit sortierten `schlüssel=wert`-Paaren für
   `agent_cost_shares`, `findings_by_severity`, `findings_by_action`; dann je Kandidat
   eine Zeile (`run_id`, `repo_slug`, `adw_version`, Regeln kommagetrennt,
   `issue_title`); zuletzt `"<n> übersprungen"`. `null` → `-`, Anteile als Prozent,
   Zahlen nur zur Anzeige gerundet. Keine Farben. Die Formatierung berechnet nichts neu
   und verändert weder Aggregate noch Kandidatenauswahl.
5. Keine Schreibzugriffe, keine Locks, kein `_require_repo` (kein Ziel-Repo nötig),
   kein Modellaufruf.

### B4 — Doku und Changelog (Scope §6; DoD)

- Beide Handbücher synchron (EN englisch, DE deutsch, inhaltlich gleich): §11.1 um
  `adw kpis` (Aufruf, `--since`/`--repo-slug`/`--json`, Quelle nur Ledger, Kennzahlen
  mit JSON-Schlüsseln, Gruppierung/Sortierung, die fünf Kandidaten-Regeln,
  JSON-Struktur `{groups, total, candidates, skipped}`, Exit 1 ohne Ledger,
  Überspringen kaputter/unbekannter Records); Hinweis, dass nur nach W3 exportierte
  oder neu exportierte Records korrekte `issue_title`/`phase`/`round`/`loop` tragen
  (`adw ledger export --repo .`). §7 um einen Eintrag `adw kpis` mit Verweis auf §11.1.
  Beschreibung von `findings`/`review_counts` (+ `loop`, Herkunft aus der
  Span-Hierarchie) und `issue_title` (Kopfzeilen-Regel) anpassen.
- `CHANGELOG.md`/`CHANGELOG.de.md`: `## [0.34.0] — Unreleased` oberhalb `0.33.0`,
  `### Added` (`adw kpis`), `### Fixed` (`issue_title`, `phase`/`round` + neu `loop` in
  `findings`/`review_counts`), im Stil der vorhandenen Einträge.
- `pyproject.toml` unverändert.

## Reihenfolge

B1 → B2 → B3 → B4. Jeden neuen Test zuerst rot sehen (beim Golden-File: Fixture und
Golden umstellen, Test rot, dann B1). Das Neuschreiben des persönlichen Ledgers nach dem
Merge gehört nicht zum Lauf.

## Tests (Richtwert 9–14 neue Tests; geplant 10)

Hermetik: `_isolate_ledger` (autouse) zeigt auf einen nicht existierenden Pfad; für
Tests mit Ledger die **bestehende** conftest-Fixture `ledger` verwenden (echter Klon mit
`.git`, Git-Probe besteht) — keine neue Ledger-Fixture bauen. Helfer in
`tests/test_kpis.py`: `write_record(ledger, slug, run_id, record_or_bytes)` schreibt
`runs/<slug mit / → __>/<run_id>/run-record.json`. Records als minimale Dicts mit
`schema_version: 1` und nur den gebrauchten Feldern. `started_at` relativ zu
`datetime.now(UTC)` erzeugen (z. B. jetzt − 2 Tage / − 10 Tage), keine Uhr-Injektion.
CLI über `typer.testing.CliRunner` wie in `tests/test_ledger.py`.

Fixture-Ledger für AC 1/3/4 (6 Läufe; Versionen `"0.9.0"` und `"0.10.0"`, damit die
numerische Sortierung `0.9.0` vor `0.10.0` mitgeprüft wird). Vorschlag für die
regelrelevanten Felder (übrige Felder so setzen, dass jede Kennzahl mindestens einen
`null`/fehlenden Wert und mindestens eine gemessene `0` enthält):

| Lauf | Version | löst aus | Kernwerte |
|---|---|---|---|
| r1 | 0.9.0 | — (unauffällig) | `phase done`, Lanes `backend 3`, `frontend 3` (Summe 6, keine ≥ 5), `cost 2.0` |
| r2 | 0.9.0 | `escalated` | `phase escalated`, `cost 2.0` |
| r3 | 0.9.0 | `review_rounds` | `review_rounds 4`, `phase done`, `escalation_reason` gesetzt (zählt in `escalation_share`, nicht als Regel), `cost_usd`/`duration_s` `null` |
| r4 | 0.10.0 | `gate_iterations` | Lane `backend 5`, `cost 2.0` |
| r5 | 0.10.0 | `circuit_breakers` | `circuit_breakers 1`, `cost 1.0` |
| r6 | 0.10.0 | `cost_usd` | `cost 10.0` (Median 2.0 → Schwelle 3.0), `phase` weder done noch escalated |

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 0 | Golden File mit umgestelltem Fixture (bestehender Test, Daten neu) | `tests/test_ledger.py` (kein neuer Test) |
| 0 | `triage.decision`/`codex.review` ohne `round`-/`phase`-Vorfahren (an die Fixture-Kopie angehängt bzw. `span`/`parent` auf `run`-Spanne), dazu eine zyklische `parent`-Kette → `phase`/`round`/`loop` `null`, kein Hänger, trotz gesetzter Top-Level-Werte | `tests/test_ledger.py` |
| 1 | 6-Lauf-Ledger: `compute_kpis` → `groups` (Reihenfolge, alle §3-Schlüssel) und `total` gleich von Hand berechneten Werten, inkl. `null`-Fälle, gerader Median und `escalation_share` über `escalation_reason` | `tests/test_kpis.py` |
| 2 | `since=7`: älterer Lauf und Lauf ohne/unlesbares `started_at` fallen heraus; `skipped` bleibt 0 | dto. |
| 2 | `repo_slug`: nur exakter Slug; Kosten-Median über den gefilterten Satz (z. B. Slug A `[1, 1, 4]`, Slug B `[4, 4]`: ohne Filter kein Kosten-Kandidat, mit `repo_slug=A` ist der 4er Kandidat) | dto. |
| 3 | 6-Lauf-Ledger: jede Regel genau bei ihrem Lauf mit allen Kandidatenfeldern, r1 fehlt, Reihenfolge nach `started_at`/`run_id` | dto. |
| 4 | CLI `--json` → genau ein Objekt mit `{groups, total, candidates, skipped}`; Textausgabe enthält dieselben Versionen, `gesamt`-Läufe, Kandidaten-`run_id`s und `skipped` (kein exaktes Layout) | dto. |
| 5 | Record mit `schema_version 2`, Datei mit kaputtem JSON und JSON-Nicht-Objekt → `skipped == 3`, nicht in Kennzahlen/Kandidaten, Exit 0, kein Traceback | dto. |
| 6 | CLI ohne Ledger, parametrisiert: Pfad fehlt / Verzeichnis ohne `.git` / `.git` vorhanden, Probe scheitert → stderr mit Pfad, Exit 1, keine Exception im Result; dazu `--since -1` → Exit ≠ 0 | dto. |
| 6 (Scope §2) | Ledger ohne Records → Exit 0, `groups`/`candidates` leer, `total.runs == 0`, Kennzahlen `null`/leer | dto. |
| 7 | Snapshot (Dateiliste + Bytes) des Ledgers vor/nach Text- und `--json`-Aufruf gleich | dto. |
| 8 | `uv run ruff check .`, `uv run pytest -x -q` | Gate |

Keine weiteren Tests (keine Matrix über Versionsformate, Zeitzonen-Varianten, Rundung
der Textausgabe oder einzelne fehlerhafte Feldtypen).

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); bestehende Suite grün.
- AC 0–8 durch Tests belegt; Tests brauchen weder das persönliche Ledger noch Modelle.
- `adw kpis` schreibt nichts und ruft kein Modell auf; keine neue Dependency.
- Handbücher und Changelogs EN/DE synchron; `pyproject.toml` unverändert; GUI,
  Ledger-Layout und Export-Mechanismus unverändert.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Diagramme, HTML-Bericht, GUI-Ansicht.
- Konfigurierbare Kandidaten-Regeln oder Schwellen, statistische Tests.
- Fallback auf Run-Ordner ohne Ledger.
- Retro-Agent (W14), Wochenschleife (W15).
- Weitere Filter (Agent, Modell, Phase), weitere Sortier- oder Ausgabeformate (CSV o. Ä.).
- Persistente KPI-Caches, Indizes, Hintergrundaktualisierung, automatische
  Ledger-Reparatur.
- Validierung einzelner Record-Felder über `schema_version` hinaus; fehlerhafte
  Einzelwerte werden nach den `null`-Regeln aus §3 ignoriert, nicht gemeldet.
