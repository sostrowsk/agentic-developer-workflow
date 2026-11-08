# Spec — W3: `adw kpis` — Kennzahlen über alle Läufe aus dem Ledger

Basis: Stand 0.33.0 (`main`, 83f2e40), am 2026-11-08 gegen den Code geprüft.

- `build_run_record` (`adw/ledger.py`) liefert `schema_version: 1` mit u. a.
  `adw_version`, `adw_versions`, `started_at`, `phase`, `escalation_reason`,
  `approvals`, `duration_s`, `cost_usd`, `lanes.<lane>.gate_iterations`,
  `review_rounds`, `fix_cycles` (bereits über Lanes summiert), `circuit_breakers`,
  `agents[].{agent, cost_usd}`, `findings[]`, `review_counts[]`. `repo_slug` steht im
  Record mit `/` (z. B. `acme/widget`); nur der Ledger-Ordnername ersetzt `/` durch `__`.
- Die beiden Datenfehler sind vorhanden: `_issue_title` nimmt die erste nicht-leere
  Zeile von `issue.md` (bei echten Läufen immer die ADW-Kopfzeile aus
  `adw/authoring.py`); `findings[]`/`review_counts[]` lesen `phase`/`round` aus den
  Top-Level-Feldern der Events, die im Event-Log immer `null` sind.
- `_ledger_path()` löst `ADW_LEDGER` (Default `~/.adw/ledger`) auf: kein Verzeichnis
  oder kein `.git` → `None`; vorhandenes `.git`, aber Git-Probe scheitert → `RuntimeError`.
- Das Fixture `tests/fixtures/ledger/a1b2c3d4` bildet das echte Format **nicht** ab:
  `issue.md` ohne ADW-Kopfzeile, Top-Level-`phase`/`round` in den Events gesetzt, keine
  `phase`-/`round`-Spannen. Sein Golden-File `expected-run-record.json` zeigt deshalb
  heute „richtige" Werte, die aus dem falschen Pfad stammen.
- Handbuch: Kommando-Referenz ist §7, Ledger-Abschnitt ist §11.1 (EN und DE).
  Die ältere W3-Skizze in `ONBOARDING-SPEC-PLAN.md` erwähnt einen Run-Ordner-Fallback;
  E1 entscheidet dagegen.

## Ziel

ADW-Änderungen (Prompts, Effort, Grenzen, Schlüssel) werden messbar: Kennzahlen je
ADW-Version über alle Läufe im Ledger, dazu auffällige Läufe nach festen Regeln als
Eval-Kandidaten. Reines Lesen, keine Modellaufrufe, keine Schreibzugriffe.

Konkreter Schaden heute: Kennzahlen über Läufe hinweg gibt es nur per Hand-Skript, und
zwei Feldgruppen des Lauf-Protokolls sind bei allen exportierten Läufen falsch
(`issue_title` ist immer die ADW-Kopfzeile, `phase`/`round` der Findings immer `null`).
Ohne A0 wären Kandidaten nicht benennbar und Findings nicht nach Runde auswertbar.

## Scope

### 1. Datenkorrektur im Lauf-Protokoll (A0)

Nur Werte zweier Feldgruppen ändern sich, ein Feld kommt hinzu. Form des Schemas,
Export-Mechanismus und `schema_version: 1` bleiben.

- **`issue_title`:** Ist die erste nicht-leere Zeile von `issue.md` die ADW-Kopfzeile
  (`# Issue (ADW-generiert …`), gilt der Rest der Datei ab der Zeile danach; sonst die
  ganze Datei (ein Issue ohne Kopfzeile verliert seinen Titel nicht). Darin: Text der
  ersten Markdown-Überschrift ohne `#`-Präfix und Leerraum; gibt es keine, die erste
  nicht-leere Zeile (getrimmt); sonst `null`.
- **`findings[]`** (aus `triage.decision`) und **`review_counts[]`** (aus
  `codex.review`-Ende) erhalten `phase`, `round` und neu `loop` aus der
  Span-Hierarchie statt aus den Top-Level-Feldern der Events:
  - Ausgangspunkt: für `triage.decision` die im Feld `span` genannte umschließende
    Spanne; für `codex.review` die eigene Spanne, deren Vorfahren über das `parent`
    ihres Start-Events erreicht werden.
  - Von dort die Vorfahrenkette hinauf (je Spanne das `parent` ihres Start-Events):
    nächste Spanne vom Typ `round` → `round = payload.n`, `loop = payload.loop`;
    nächste Spanne vom Typ `phase` → `phase = payload.name`.
  - Kein passender Vorfahr oder fehlendes Start-Event → betroffener Wert `null`.
    Top-Level-Eventwerte ersetzen fehlende Vorfahren nicht. Zyklen in der Kette
    führen nicht zu Endlosschleifen.
- Schlüssel je Element danach: `findings[]`: `finding_key`, `severity`, `action`,
  `reason`, `phase`, `round`, `loop`; `review_counts[]`: `phase`, `round`, `loop`, `n`.
- Das Fixture `a1b2c3d4` wird auf das echte Format umgestellt (`issue.md` mit
  ADW-Kopfzeile, Top-Level-`phase`/`round` der Events `null`, `phase`- und
  `round`-Spannen um Spec-Review und Code-Review); Golden-File entsprechend.

### 2. Kommando `adw kpis` (A1, E1, E5)

Aufruf: `adw kpis [--since <tage>] [--repo-slug <slug>] [--json]` — Top-Level-Kommando,
nicht unter `adw ledger`.

- **Quelle:** ausschließlich das Ledger, aufgelöst wie beim Export (`ADW_LEDGER`,
  Default `~/.adw/ledger`, gleiche „kein Ledger"-Regel wie W13). Gelesen werden alle
  Dateien `runs/<slug>/<run_id>/run-record.json`. Kein Fallback auf Run-Ordner.
- **Kein Ledger** (Pfad kein Verzeichnis oder ohne `.git`) → Meldung auf stderr
  mit Pfad, Exit 1. Ledger vorhanden, aber Git-Probe scheitert → gleiche Behandlung
  (Meldung, Exit 1), kein Traceback. Ledger vorhanden, aber ohne Records → Exit 0,
  leere Gruppen und Kandidaten, `total` mit `runs: 0`, keine erfundenen Messwerte.
- **Übersprungen** (gezählt in `skipped`, kein Traceback, Exit bleibt 0): Datei kein
  gültiges JSON, kein JSON-Objekt, oder `schema_version` ≠ 1 (auch fehlend).
  `skipped` zählt beim Lesen; Läufe, die nur einen Filter nicht erfüllen, zählen
  nicht als übersprungen.
- **Filter** (wirken gemeinsam vor Gruppierung, Gesamtberechnung und
  Kandidatenauswahl, auch vor dem Median der Kostenregel in §4):
  - `--since N` (ganze Zahl ≥ 0): nur Läufe mit `started_at` ≥ jetzt − N Tage
    (Grenze einschließlich, Vergleich als Zeitpunkte). Läufe ohne oder mit
    unlesbarem `started_at` fallen bei gesetztem `--since` heraus. Negativer Wert →
    Meldung, Exit ≠ 0.
  - `--repo-slug S`: nur Läufe mit `repo_slug == S` (exakt, Record-Form `acme/widget`).
- **Ort:** Lese- und Rechenlogik in `adw/ledger.py` mit einer Funktion
  `compute_kpis(records, since=None, repo_slug=None) -> dict`; CLI-Anbindung in
  `adw/cli.py`. Das Kommando schreibt nichts — weder ins Ledger noch in Repo oder
  Run-Ordner — und ruft kein Modell auf. Keine neue Dependency.

### 3. Kennzahlen (E2)

Gruppiert nach `adw_version` (nicht nach `adw_versions`). Läufe mit
`adw_version: null` bilden eine eigene Gruppe mit `adw_version: null`. Gruppen
aufsteigend nach Version (numerisch je Punkt-Komponente; nicht-numerische Versionen
danach lexikalisch; `null` zuletzt). Zusätzlich `total` über alle gefilterten Läufe
mit denselben Kennzahlen, direkt aus den Records berechnet (nicht aus Gruppenwerten).

Allgemeine Regeln: Median ist der statistische Median (bei gerader Anzahl Mittel der
beiden mittleren Werte). Nicht-numerische oder `null`-Werte werden bei Summen,
Medianen und Maxima ignoriert; bleibt kein Wert, ist das Ergebnis `null` — nie eine
gemessene `0`. Ein gemessener Wert `0` bleibt gültig. Anteile sind Brüche 0..1; ohne
Nenner `null`. JSON-Werte sind ungerundet; die Textausgabe rundet nur zur Anzeige.

Kennzahlen je Gruppe — JSON-Schlüssel (stabil):

| Schlüssel | Bedeutung |
|---|---|
| `adw_version` | Gruppenschlüssel (fehlt in `total`) |
| `runs` | Anzahl Läufe |
| `phase_shares` | `{done, escalated, other}`: Anteil Läufe mit `phase == "done"`, `== "escalated"`, sonst |
| `escalation_share` | Anteil Läufe mit Eskalation: `phase == "escalated"` oder `escalation_reason` ≠ `null` (erfasst auch nach Eskalation fortgesetzte Läufe) |
| `review_rounds` | `{median, max}` über `review_rounds` |
| `fix_cycles` | `{median, max}` über `fix_cycles` |
| `gate_iterations` | `{median, max}` über die je Lauf über alle Lanes summierten `lanes.*.gate_iterations` |
| `duration_s` | `{median}` über `duration_s` |
| `cost_usd` | `{sum, median}` über `cost_usd` |
| `agent_cost_shares` | `{<agent>: Anteil}`: Summe `agents[].cost_usd` je `agent` geteilt durch die Summe aller bekannten Agent-Kosten der Gruppe; leer, wenn keine vorliegen |
| `circuit_breakers` | Summe `circuit_breakers` |
| `approvals` | `{median}` über `approvals` |
| `findings_by_severity` | `{<severity>: Anzahl}` über alle `findings[]` |
| `findings_by_action` | `{<action>: Anzahl}` über alle `findings[]` |

### 4. Auffällige Läufe (A2, E4)

Feste, nicht konfigurierbare Regeln über die gefilterten Läufe; ein Lauf ist genau
einmal Kandidat, wenn mindestens eine greift. Regelnamen (stabil, in Text und JSON):

| Regel | Bedingung |
|---|---|
| `escalated` | `phase == "escalated"` |
| `review_rounds` | `review_rounds >= 4` |
| `gate_iterations` | `gate_iterations` mindestens **einer einzelnen** Lane `>= 5` (nicht die Summe aus §3) |
| `circuit_breakers` | `circuit_breakers > 0` |
| `cost_usd` | `cost_usd` strikt größer als 1,5 × Median `cost_usd` aller gefilterten Läufe (über Versionsgruppen hinweg); Läufe ohne `cost_usd` lösen nie aus |

Je Kandidat: `run_id`, `repo_slug`, `adw_version`, `rules` (Liste der ausgelösten
Regelnamen in obiger Reihenfolge), `issue_title`. Kandidaten sortiert nach
`started_at`, dann `run_id`.

### 5. Ausgabe (E3)

- **Ohne `--json`:** schlichte Texttabelle auf stdout, keine Farben. Eine Zeile je
  Version plus eine Zeile `gesamt`, feste Spaltenfolge: Version, Läufe,
  done/escalated/sonstige, Eskalationsanteil, Review-Runden Median/Max, Fix-Zyklen
  Median/Max, Gate-Iterationen Median/Max, Dauer-Median, Kosten Summe/Median,
  Circuit-Breaker, Approvals-Median. Die Kennzahlen mit variablen Schlüsseln
  (Kosten-Anteile je Agent, Findings je Severity und je Action) als sortierte
  `schlüssel=wert`-Paare je Version plus `gesamt`. Darunter die Kandidatenliste (eine
  Zeile je Kandidat mit allen Feldern aus §4) und die Zeile „n übersprungen".
  `null` wird als `-` angezeigt; Anteile dürfen als Prozent erscheinen. Das exakte
  Textlayout ist keine Contract-Fläche.
- **Mit `--json`:** genau ein JSON-Objekt auf stdout:
  `{"groups": [...], "total": {...}, "candidates": [...], "skipped": n}` mit den
  Schlüsseln aus §3 und §4; `skipped` ist eine Ganzzahl.
- Text- und JSON-Ausgabe beruhen auf demselben Ergebnis von `compute_kpis`; die
  Textausgabe ist reine Formatierung und verändert weder Aggregate noch
  Kandidatenauswahl.

### 6. Doku und Changelog (A3)

- `docs/ADW-HANDBOOK.md` und `docs/ADW-HANDBOOK.de.md` synchron: Abschnitt „Ledger"
  (§11.1) um `adw kpis` ergänzen (Aufruf, Optionen, Datenquelle, Kennzahlen,
  Kandidaten-Regeln, JSON-Struktur, Exit 1 ohne Ledger, Überspringen kaputter oder
  unbekannter Records); Kommando-Referenz (§7) um einen Eintrag `adw kpis` mit
  Verweis auf §11.1; Beschreibung von `findings`/`review_counts` um `loop` und die
  Herkunft aus der Span-Hierarchie, `issue_title` um die Kopfzeilen-Regel ergänzen.
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue oberste Sektion
  `## [0.34.0] — Unreleased` mit `### Added` (`adw kpis`); die Korrektur aus §1 darf
  unter `### Fixed` derselben Sektion stehen.
- Version in `pyproject.toml` bleibt unverändert.

## Non-Goals

- Keine Änderung an Form des `run-record.json`-Schemas über §1 hinaus, kein Wechsel
  von `schema_version`, keine Änderung am Ledger-Layout oder Export-Mechanismus.
- Das Neuschreiben des bestehenden Ledgers (`adw ledger export --repo .` nach dem
  Merge) ist kein Teil des Laufs; `adw kpis` repariert keine Records.
- GUI bleibt unverändert.
- Keine Kennzahlen außerhalb E2 (insbesondere keine Cache-Trefferquote, keine
  automatische Vorher/Nachher-Bewertung).
- Kein Retro-Agent, keine Wochenschleife, keine Modellaufrufe.
- Keine Schreibzugriffe durch `adw kpis`, kein Cache der Kennzahlen.
- Keine Farben, keine neue Dependency.

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

## Akzeptanzkriterien

0. **A0:** Das Fixture `tests/fixtures/ledger/a1b2c3d4` ist auf das echte Format
   umgestellt (§1). Der daraus gebaute Record hat
   `issue_title == "W99: Beispiel-Issue für das Ledger"`, und jedes Element von
   `findings` und `review_counts` trägt `phase`, `round` und `loop` aus der
   Span-Hierarchie; das Golden-File `expected-run-record.json` ist entsprechend
   angepasst, die übrige Struktur unverändert, `schema_version` bleibt 1. Ein
   `triage.decision` bzw. `codex.review` ohne `round`-/`phase`-Vorfahren ergibt
   `null` für die betroffenen Werte.
1. Für ein Fixture-Ledger mit 6 Läufen in zwei ADW-Versionen stimmen alle Kennzahlen
   aus §3 je Version und in `total` mit von Hand berechneten Werten überein,
   einschließlich `null`-Behandlung fehlender Werte.
2. `--since 7` blendet Läufe mit `started_at` älter als 7 Tage aus; `--repo-slug`
   beschränkt auf exakt passende `repo_slug`; die Filter wirken auch auf den Median
   der Kostenregel.
3. Jede Regel aus §4 wird durch genau einen Fixture-Lauf ausgelöst und erscheint mit
   ihrem Regelnamen beim Kandidaten (samt `run_id`, `repo_slug`, `adw_version`,
   `issue_title`); ein unauffälliger Lauf erscheint nicht in `candidates`. Die
   Gate-Regel reagiert auf eine einzelne Lane `>= 5`, nicht auf die Lane-Summe.
4. `--json` liefert genau ein Objekt `{groups, total, candidates, skipped}` mit den
   Schlüsseln aus §3/§4; die Textausgabe zeigt dieselben Werte (gleiche Läufe,
   Kandidaten und `skipped`) wie die JSON-Ausgabe.
5. Ein Record mit unbekanntem `schema_version` und eine Datei mit kaputtem JSON
   werden übersprungen und in `skipped` gezählt, gehen weder in Kennzahlen noch in
   Kandidaten ein; kein Traceback, Exit 0.
6. Kein Ledger → Meldung auf stderr, Exit 1.
7. `adw kpis` schreibt nichts: das Ledger-Verzeichnis ist vor und nach Text- und
   JSON-Aufruf byte-gleich (Dateiliste und Inhalte).
8. `uv run ruff check .` und `uv run pytest -x -q` sind grün.

Richtwert: 9–14 neue Tests; deutlich mehr deutet auf Gold-Plating.

## Definition of Done

- Alle Akzeptanzkriterien 0–8 erfüllt und durch automatisierte Tests mit
  Fixture-`run-record.json`-Dateien in einem temporären Ledger (`ADW_LEDGER`) belegt;
  die Tests benötigen weder das persönliche Ledger noch Modellzugriffe.
- `uv run ruff check .` und `uv run pytest -x -q` grün.
- Handbuch EN+DE und `CHANGELOG.md`/`CHANGELOG.de.md` gemäß §6 synchron aktualisiert
  und entsprechen dem ausgelieferten Verhalten; `pyproject.toml`-Version unverändert.
- Keine neue Dependency; GUI, Ledger-Layout und Export-Mechanismus unverändert.
- Contract (Single-Lane) nur für extern beobachtbare Flächen: CLI `adw kpis` mit
  Optionen `--since`, `--repo-slug`, `--json`, Exit-Codes und die JSON-Struktur aus
  §3–§5; keine internen Helper-Signaturen, kein exaktes Textlayout.
