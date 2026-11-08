# W3: `adw kpis` — Kennzahlen über alle Läufe aus dem Ledger

Sechster Lauf der Reihe aus `ONBOARDING-SPEC-PLAN.md` (Paket W3). Setzt auf dem
gemergten Stand **0.33.0** (nach W13) auf: Jeder Lauf liegt als `run-record.json` im Ledger
(`ADW_LEDGER`, Default `~/.adw/ledger`, Layout `runs/<slug>/<run_id>/`). Die
Ausgangslage ist vor dem Bauen gegen den dann aktuellen Stand zu prüfen,
insbesondere das tatsächlich gebaute Schema von `run-record.json`.

Ziel: ADW-Änderungen (Prompts, Effort, Grenzen, Schlüssel) werden **messbar** —
vorher/nachher je ADW-Version — und auffällige Läufe werden als Kandidaten für
das Eval-Set sichtbar. Reines Lesen, 0 Tokens.

## Ausgangslage

- Seit W13 schreibt jeder `run`/`resume`/`approve` und der Nachtrag
  `adw ledger export` ein `run-record.json` mit `schema_version: 1` (Felder siehe
  `adw/ledger.py` `build_run_record` und den Abschnitt „Ledger" im Handbuch).
- Gemessen über die 15 vorhandenen Läufe (vor W13 aus den Run-Ordnern):
  `build_agent` verursacht 67 % der Kosten ($414 von $615), Cache-Trefferquote je
  Agent 80–96 %, in keinem Lauf ein `circuit_breaker`-Event. Solche Zahlen gibt es
  heute nur per Hand-Skript.
- Die GUI zeigt Kennzahlen **pro Lauf**, nicht über Läufe hinweg.
- Das Ledger ist befüllt: `adw ledger export --repo .` hat am 2026-11-08 alle 20
  Läufe dieses Repos exportiert (`~/.adw/ledger/runs/sostrowsk__agentic-developer-workflow/`,
  9,9 MB); ein zweiter Export liefert `unchanged`.

### Zwei Datenfehler im Lauf-Protokoll (gemessen an den exportierten Records)

- **`issue_title` ist bei allen Läufen falsch.** `_issue_title` (`adw/ledger.py:130`)
  nimmt die erste Überschrift aus `issue.md`; die lautet immer
  `# Issue (ADW-generiert aus dem Run-State — nicht manuell editieren)`. Der echte
  Titel ist die erste Überschrift **danach** (z. B. `# W13: Jeder Lauf hinterlässt …`).
- **`phase` und `round` sind bei allen `findings` und `review_counts` `null`.**
  `build_run_record` (`adw/ledger.py:257–266`) liest die Top-Level-Felder `phase`/`round`
  der Events; die sind im Event-Log immer `null`. Die Information steckt in der
  Span-Hierarchie: `codex.review`-Start hat als `parent` eine `round`-Spanne
  (`payload: {loop, n, cap}`, `loop` ∈ `authoring`/`codex_review`/…), deren `parent`
  eine `phase`-Spanne (`payload.name`) ist; `triage.decision` ist ein Punkt-Event,
  dessen `span` die umschließende Spanne ist (im Code-Review die `round`-Spanne mit
  `loop: codex_review`). Ohne diese Werte lässt sich nicht nach Runde auswerten.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Form des `run-record.json`-Schemas und der Export-Mechanismus (W13) bleiben;
  A0 korrigiert nur die Werte zweier Felder und ergänzt ein Feld (`loop`).
- Die GUI bleibt unverändert.
- Kein Retro-Agent, keine Wochenschleife.

## Aufgabe

**A0 — Datenfehler beheben.** `issue_title` ist die erste Markdown-Überschrift
nach der ADW-Kopfzeile (Fallback: erste nicht-leere Zeile danach). `phase`, `round`
und neu `loop` von `findings` und `review_counts` werden über die Span-Hierarchie
bestimmt: nächste `round`-Spanne über `span`/`parent` → `round = payload.n`,
`loop = payload.loop`; nächste `phase`-Spanne → `phase = payload.name`; ohne
passende Vorfahren bleibt der Wert `null`. `schema_version` bleibt 1 (Werte
korrigiert, ein Feld ergänzt). Nach dem Merge wird das Ledger per
`adw ledger export --repo .` neu geschrieben — das ist kein AC des Laufs.

**A1 — `adw kpis`.** Neues Kommando, das alle `run-record.json` im Ledger liest
und Kennzahlen ausgibt, gruppiert nach ADW-Version, optional gefiltert nach
Zeitraum und Repo.

**A2 — Auffällige Läufe.** Die Ausgabe listet Läufe, die nach festen Regeln (E4)
auffällig sind, als Eval-Kandidaten.

**A3 — Doku und Changelog.** Handbuch EN+DE: Abschnitt „Ledger" um `adw kpis`
ergänzen. `CHANGELOG.md`/`CHANGELOG.de.md` synchron: neue Sektion
`## [0.34.0] — Unreleased` mit `### Added`. Version in `pyproject.toml` **nicht**
ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 — Aufruf:** `adw kpis [--since <tage>] [--repo-slug <slug>] [--json]`.
  Quelle ausschließlich das Ledger (`ADW_LEDGER`, gleiche Auflösung wie W13). Kein
  Fallback auf Run-Ordner. Kein Ledger → Exit 1 mit Meldung. Dry-Runs gibt es im
  Ledger nicht; Läufe mit unbekanntem `schema_version` werden übersprungen und
  gezählt („n übersprungen").
- **E2 — Kennzahlen je Gruppe (ADW-Version aus `adw_version`)**:
  Anzahl Läufe; Anteil `done` / `escalated` / sonstige; Median und Maximum von
  `review_rounds`, `fix_cycles`, Gate-Iterationen (Summe über Lanes); Anteil Läufe
  mit Eskalation; Median `duration_s`; Summe und Median `cost_usd`; Anteil Kosten
  je Agent (aus `agents`); Summe `circuit_breakers`; Median `approvals`; Anzahl
  Findings je Severity und je `action`.
  Zusätzlich eine Zeile „gesamt".
- **E3 — Ausgabe:** ohne `--json` eine schlichte Texttabelle (eine Zeile je
  Version, Spalten fest), darunter die Kandidatenliste; mit `--json` ein Objekt
  `{groups: [...], total: {...}, candidates: [...], skipped: n}` mit stabilen
  Schlüsselnamen. Keine Farben, keine neue Dependency.
- **E4 — Kandidaten-Regeln (fest, nicht konfigurierbar):** Lauf ist Kandidat, wenn
  mindestens eins gilt: `phase == "escalated"`; `review_rounds >= 4`;
  Gate-Iterationen einer Lane `>= 5`; `circuit_breakers > 0`; `cost_usd` über dem
  1,5-fachen Median aller betrachteten Läufe. Ausgabe je Kandidat: `run_id`,
  `repo_slug`, `adw_version`, ausgelöste Regeln, `issue_title`.
- **E5 — Ort:** Lese- und Rechenlogik in `adw/ledger.py` (eine Funktion
  `compute_kpis(records, since=None, repo_slug=None) -> dict`), CLI-Anbindung in
  `adw/cli.py`. Keine Schreibzugriffe, auch nicht ins Ledger.
- **E6:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Tests
  arbeiten mit Fixture-`run-record.json`-Dateien in einem temporären Ledger.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Diagramme, HTML-Bericht, GUI-Ansicht.
- Konfigurierbare Kandidaten-Regeln, statistische Tests.
- Fallback auf Run-Ordner ohne Ledger.
- Retro-Agent (W14), Wochenschleife (W15).

## Akzeptanzkriterien

0. A0: Für das bestehende Fixture `tests/fixtures/ledger/a1b2c3d4` ist
   `issue_title` der echte Titel, und jedes Finding/`review_counts`-Element hat
   `phase`, `round` und `loop` aus der Span-Hierarchie (Golden-File angepasst).
1. Für ein Fixture-Ledger mit 6 Läufen in zwei ADW-Versionen stimmen alle
   Kennzahlen aus E2 je Version und gesamt mit von Hand berechneten Werten überein.
2. `--since 7` blendet ältere Läufe aus (`started_at`), `--repo-slug` filtert.
3. Jede Kandidaten-Regel aus E4 wird durch genau einen Fixture-Lauf ausgelöst und
   erscheint mit Regelname; ein unauffälliger Lauf erscheint nicht.
4. `--json` liefert die Struktur aus E3; Text- und JSON-Ausgabe beruhen auf
   denselben Werten.
5. Unbekanntes `schema_version` → übersprungen und gezählt; kaputtes JSON →
   übersprungen und gezählt, kein Traceback.
6. Kein Ledger → Exit 1 mit Meldung.
7. Das Kommando schreibt nichts (Ledger-Verzeichnis byte-gleich vorher/nachher).
8. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 9–14 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (CLI
`adw kpis` mit Optionen, JSON-Struktur aus E3), keine internen Helper-Signaturen.
