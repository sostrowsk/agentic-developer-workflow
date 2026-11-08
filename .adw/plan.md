# Plan — W15: `adw improve`, der wöchentliche Verbesserungsbericht

Basis: `.adw/spec.md`, gemergter Stand **0.36.3** (nach R5). Die Vorentscheidungen
E1–E8 sind bindend. `.adw/config.yaml` enthält ausschließlich die Lane `backend`; es
gibt keinen Frontend-Workstream.

Contract: `.adw/contract.yaml` (Single-Lane). Er pinnt nur beobachtbares Verhalten: CLI
mit Optionen, Exits und stdout; Dateien unter `reports/`; Abschnitte und feste Sätze des
Berichts; Brief-Bedingungen, Pflichtüberschriften und Lebenszyklus; Ledger-Commit mit
Nachricht und Push-Verhalten; Timer-Units. Agentenkonfiguration, JSON-Antwortschema und
Git-Staging sind Umsetzungsdetails dieses Plans (B3, B4), kein Contract. Keine
HTTP-Routen, keine Events, keine internen Helper-Signaturen.

## Workstreams

- **backend** — gesamte Umsetzung (CLI, Bericht, Agent, Ablage, Units, Doku, Tests).
- **frontend** — entfällt (keine Lane).

## Workstream backend

Verantwortung: A1–A5 und Akzeptanzkriterien 1–11.

Betroffene Dateien:

- Neu: `adw/improve.py` (Auswertung, Bericht, Brief-Koordination, Ablage, Git).
- Bestehend: `adw/cli.py` (Subcommand) und `adw/agents.py` (`improve_agent` in `REGISTRY`).
- Neu: `tests/test_improve.py`.
- Neu: `examples/systemd/adw-improve.service`, `examples/systemd/adw-improve.timer`.
- Bestehend: `docs/ADW-HANDBOOK.md`, `docs/ADW-HANDBOOK.de.md`, `CHANGELOG.md`,
  `CHANGELOG.de.md`.

Wiederverwendet werden `_ledger_path`, `_read_ledger_runs`, `compute_kpis`, `run_git`,
`_PUSH_TIMEOUT` (`adw/ledger.py`, `adw/git.py`), das `flock`-Muster von `_repo_lock`
(`adw/cli.py:1170`) und `SdkAgentRunner`. Export, Retro, Feedback, KPI-Semantik und
Dateischemata bleiben unverändert.

### B1 — CLI, Ledger, Lock und Zeitfenster (A1; AK 1, 4, 8)

1. `adw improve` in `adw/cli.py` registrieren: `--since` (int, Default 7),
   `--repo-slug` (optional), `--no-brief` (Flag).
2. Ledger über `_ledger_path` auflösen (`ADW_LEDGER`, sonst `~/.adw/ledger`, Wurzel
   eines Git-Arbeitsbaums). Fehlendes oder nicht nutzbares Ledger → Meldung, Exit 1.
   Kein Ziel-Repo, kein `.adw/runs/` als Ersatz.
3. Vor jedem Lesen, Schreiben und Agentenaufruf exklusiven, nicht blockierenden `flock`
   auf `<ledger>/.improve.lock` nehmen (Muster `_repo_lock`, ohne den Repo-Lock selbst
   aufzurufen). Belegt → sofort Exit 1 mit Meldung, nichts geschrieben, kein
   Agentenaufruf. Lock bis nach den Git-Schritten halten, zuverlässig freigeben, nie
   stagen.
4. Aufrufzeitpunkt `t` einmal erfassen. Aktuelles Fenster
   `t − since Tage ≤ started_at ≤ t`; Vorzeitraum
   `t − 2·since Tage ≤ started_at < t − since Tage`. Zukünftige Läufe gehören zu
   keinem Fenster.
5. Records und Signale gemeinsam über `_read_ledger_runs` lesen; beim Filtern die
   Zuordnung Record ↔ Signale erhalten. `--repo-slug` auf beide Fenster und alle daraus
   abgeleiteten Berichtsteile einschließlich Brief-Auswahl anwenden.
6. **Vorgefilterte** Records samt Signalen an `compute_kpis(..., since=None)` übergeben.
   Dessen eigener `since`-Filter rechnet relativ zu `datetime.now()` und hat keine
   obere Grenze; er darf für den Vorzeitraum nicht benutzt werden. KPI-Semantik bleibt
   unverändert.
7. Nach geschriebenem Bericht: Berichtspfad und je **tatsächlich ausgegebenem**
   Abschnitt eine Zeile mit Anzahl auf stdout (Läufe im Zeitraum, Problemgruppen,
   wiederkehrende Findings, Regressionsfall-Kandidaten, Gruppen mit Hebel). Im
   verkürzten Bericht nur die Laufzahl. Exit 0 auch ohne Brief und bei Commit-/
   Push-Fehlern.

### B2 — Deterministischer Markdown-Bericht (A2; AK 1–4)

Code, 0 Modell-Tokens. Überschriften exakt wie im Contract, Reihenfolge fix:

1. **Kennzahlen:** Versionsgruppen und Gesamtzeile aus `compute_kpis` für das aktuelle
   Fenster; die W14-Signale (`retro_causes`, `retro_runs`, `ratings`,
   `finding_feedback`) so, wie `total` sie heute liefert, ohne Verteilung auf
   Versionsgruppen. Vom Vorzeitraum **nur die Gesamtzeile**.
2. **Problemgruppen aus Retros:** alle Retro-Einträge der aktuellen Läufe nach `cause`
   gruppieren. Je Gruppe Anzahl Einträge, Anzahl **verschiedener** Läufe, Summe
   `cost_usd` dieser Läufe (ein Lauf zählt je Gruppe einmal, auch bei mehreren
   Einträgen). Sortierung absteigend nach Läufe × Kostensumme, Gleichstand stabil nach
   `cause`-Name; Eintragsreihenfolge ebenfalls deterministisch. Je Eintrag `problem`,
   `better`, `lever`, Run-ID und `evidence`; jedes Textfeld einschließlich einzelner
   Belege auf höchstens 300 Zeichen gekürzt (Kürzungszeichen zählt zum Limit).
3. **Wiederkehrende Findings:** Vorkommen je (Lauf, `finding_key`) deduplizieren. Nur
   Schlüssel aus mindestens zwei verschiedenen aktuellen Läufen, höchstens 20, sortiert
   nach Laufzahl absteigend, dann Schlüssel. Je Eintrag Schlüssel, Laufzahl, Severity
   und Feedback `valid`/`noise`, zugeordnet über Lauf und `finding_key`;
   Mehrfachvorkommen vervielfachen Feedback nicht.
4. **Regressionsfall-Kandidaten:** aktuelle Läufe mit `phase == "escalated"` oder
   letzter gültiger `rate`-Bewertung `bad`. Maßgeblich ist die Dateireihenfolge der
   gültigen Zeilen, nicht der Zeitstempel; `bad` gefolgt von `good` zählt nicht. Je
   Kandidat einmal Run-ID, `issue_title`, Grund (bei Eskalation und `bad` beide
   Gründe). **Nicht** die weiter gefassten `candidates` von `compute_kpis` übernehmen.
5. **Hebel je Gruppe:** nur vorhandene Gruppen, feste Zuordnung aus dem Contract
   (`prompt`/`model` → Hill Climbing W7; `orchestrator`/`gate` → Brief für einen
   ADW-Lauf; `context_missing`/`target_repo` → Expertise W9; `issue_quality` →
   Brief-Vorlage schärfen).

Weniger als 3 aktuelle Läufe (nach Repo-Filter) → ausschließlich Abschnitt 1 und der
Satz **„Zu wenige Läufe für eine Auswertung"**; dieser Satz ist zugleich der Grund für
den fehlenden Brief. Kein Agentenaufruf, Exit 0.

### B3 — `improve_agent` und genau ein Brief-Entwurf (A3; AK 5–6)

1. `REGISTRY`-Eintrag in `adw/agents.py` analog `retro_agent`: `model=FABLE`,
   `tools=READ_ONLY_TOOLS`, `allowed_tools=SCOPED_READ_RULES`, `max_turns=120`,
   `timeout_s=1800`, Effort Default. Ausführung über `SdkAgentRunner`; Runner für Tests
   injizierbar.
2. `cwd` = Wurzelverzeichnis des laufenden ADW-Quellcodes, aus dem Paketpfad
   abgeleitet; nicht Shell-Verzeichnis, Ledger oder Ziel-Repo. Der Agent schreibt keine
   Dateien.
3. Aufruf nur, wenn **alle** Bedingungen gelten: nicht `--no-brief`; ≥ 3 aktuelle
   Läufe; mindestens eine Gruppe mit `cause` ∈ {`orchestrator`, `gate`, `prompt`}.
   Gewählt wird die erste berechtigte Gruppe der sortierten Liste. `prompt` ist
   briefberechtigt trotz W7-Hebel; `model` allein nicht. Sonst kein Aufruf und ein
   kurzer Grund im Bericht.
4. Auftrag: Bericht-Abschnitt der gewählten Gruppe, Inhalt von `docs/briefs/README.md`
   und `docs/briefs/r2-log-integritaet.md`. Antwort ausschließlich JSON
   `{"title": str, "brief_markdown": str}`.
5. Validierung im Code: JSON, Feldtypen, fünf Pflichtüberschriften („Ausgangslage",
   „Aufgabe", „Vorentscheidungen", „Deferred", „Akzeptanzkriterien"). Fehlschlag →
   kein Brief, Vermerk **„Brief-Entwurf verworfen: <Grund>"**.
6. Vorlagen-/Beispielzugriff, Runner-Aufruf und Validierung stehen unter einer
   Fehlerbehandlung: jede Ausnahme (auch Timeout, unlesbare Referenzdatei) → kein
   Brief, Vermerk mit Grund, Bericht entsteht trotzdem, Exit 0. Kein Retry, kein
   zweiter Agent, keine eigene nummerierte Berichtssektion für den Vermerk.

### B4 — Wochenartefakte, Commit und Push (A4; AK 6–7)

1. ISO-Jahr und zweistellige ISO-Woche des erfassten Aufrufdatums bestimmen;
   `reports/` bei Bedarf anlegen.
2. Bericht nach `<ledger>/reports/<YYYY>-W<WW>.md`, gültigen Brief nach
   `<ledger>/reports/<YYYY>-W<WW>-brief.md`; erneuter Aufruf derselben Woche
   überschreibt.
3. Entsteht kein gültiger Brief (aus welchem Grund auch immer), einen vorhandenen
   `…-brief.md` derselben Woche entfernen; der Bericht nennt den Grund.
4. Erst den lokalen Zielzustand herstellen, dann gemeinsam committen: `run_git`
   (Hooks aus). Berichtspfad immer per `git add -A --` stagen; Briefpfad nur, wenn die
   Datei existiert oder im Index getrackt ist (`git ls-files --error-unmatch`), dann
   ebenfalls `git add -A -- <pfad>` (erfasst Löschung). Ein nie vorhandener Brief wird
   nicht als Pathspec übergeben.
5. Commit nur bei gestagten Änderungen (`diff --cached --quiet`), Nachricht
   `improve <YYYY>-W<WW>`. Push mit `_PUSH_TIMEOUT`.
6. Commit- und Push-Fehler fail-open: Warnung, lokaler Artefaktzustand bleibt, Exit 0.
   Kein Fehlerstatus auf Platte, keine Warteschlange, keine Wiederholung.

### B5 — Timer-Units, Handbuch, Changelog (A5; AK 9–10)

1. `examples/systemd/adw-improve.service`: `[Unit]`, `[Service]` mit `Type=oneshot`
   und `ExecStart=uv run --project <ADW-Pfad> adw improve`; Platzhalter für ADW-Pfad
   und optional `Environment=ADW_LEDGER=<Ledger-Pfad>` klar markiert.
2. `examples/systemd/adw-improve.timer`: `[Unit]`, `[Timer]` mit
   `OnCalendar=Mon *-*-* 07:00` und `Persistent=true`, `[Install]` mit
   `WantedBy=timers.target`. ADW installiert oder aktiviert nichts.
3. Handbuch EN+DE, Abschnitt 11.1 „Ledger": Aufruf, Optionen, Exits, Zeiträume,
   Berichtsaufbau, Brief-Entwurf und menschliche Entscheidung, Ablage unter `reports/`
   mit Überschreiben, Timer-Einrichtung mit Pfadanpassung und
   `systemctl --user enable --now adw-improve.timer`. Inhaltlich synchron.
4. `CHANGELOG.md` / `CHANGELOG.de.md`: `## [0.37.0] — Unreleased` mit `### Added`.
   `pyproject.toml` bleibt `0.36.3`.

### B6 — Tests (`tests/test_improve.py`, Richtwert 10–14; AK 1–9)

Temporäres Git-Ledger über `ADW_LEDGER`, festes `now`, gefakter Runner; Push gefaked
oder gegen ein temporäres lokales Bare-Repo. Kein Netz, kein Modell, kein
`~/.adw/ledger`. Muster aus `tests/test_kpis.py`, `tests/test_ledger.py`,
`tests/test_retro.py` wiederverwenden. Zusatzvarianten gehören in die Fixture, nicht in
neue Dateien. Zielbild 12 Fälle:

1. Fixture 5 aktuelle + 2 ältere Läufe: fünf Abschnitte in Reihenfolge, Zahlen,
   Vergleichs-Gesamtzeile nur mit den 2 älteren Läufen, Sortierung Läufe × Kosten,
   Dedup Lauf je Gruppe, Hebel, Kürzung auf 300 Zeichen (AK 1).
2. Wiederkehrendes Finding mit Anzahl 2, Severity, Feedback; Einzellauf-Finding trotz
   Duplikaten nicht (AK 2).
3. Regressionskandidaten: eskaliert und `bad` mit Run-ID, `issue_title`, Grund;
   `bad`→`good` nicht (AK 3).
4. < 3 Läufe: nur Kennzahlen und exakter Hinweissatz, stdout nur Laufzahl, kein Agent,
   Exit 0 (AK 4).
5. Gruppe `orchestrator`, gültige Antwort: genau ein Aufruf, `cwd` = ADW-Quelle,
   `…-brief.md` geschrieben; höchstsortierte berechtigte Gruppe gewählt (AK 5).
6. Ungültiges JSON, falscher Feldtyp oder fehlende Pflichtüberschrift: kein Brief,
   „Brief-Entwurf verworfen: …", Exit 0 (AK 5).
7. Agentenausnahme (inkl. Timeout): kein Brief, Vermerk mit Grund, kein Retry, Exit 0
   (AK 5).
8. `--no-brief` → kein Agent, Bericht committet ohne je vorhandenen Brief; keine
   berechtigte Gruppe (nur `model`/`context_missing`) → kein Agent (AK 6).
9. Gleiche ISO-Woche: Pfade, genau ein Commit `improve <YYYY>-W<WW>` mit Bericht und
   Brief; Überschreiben; Folgeaufruf ohne Brief entfernt alten Brief als Git-Löschung
   und nennt den Grund; unveränderte Artefakte → kein leerer Commit (AK 7).
10. Simulierter Push-Fehler: Warnung, Exit 0, Artefakte lokal erhalten (AK 7).
11. Belegter Lock → Exit 1 ohne Warten, nichts geschrieben, kein Agent; fehlendes
    Ledger → Exit 1 mit Meldung (AK 8).
12. Timer-Units: Sektionen, `Type`, `ExecStart`, `OnCalendar`, `Persistent`,
    `WantedBy` (AK 9).

### Gates

- `uv run ruff check .`
- `uv run pytest -x -q`

AK 10 per Dateiprüfung (beide Handbücher, beide Changelogs, `pyproject.toml` auf
`0.36.3`). Bestehende Tests grün; keine neue Abhängigkeit; kein neuer persistenter
Zustand außer `reports/` und `.improve.lock`.

## Non-Goals

- Änderungen an `adw kpis`, Export, Retro, `adw rate`/`adw feedback` oder an den
  Schemata von `run-record.json`, `retro.json`, `feedback.jsonl`.
- Nachträgliche Reparatur oder Migration vorhandener Laufdaten.
- Version in `pyproject.toml` ändern.
- Repo-Lock oder Ziel-Repo-Bezug für `adw improve`.
- Neuer persistenter Zustand außer den Dateien unter `reports/` und der Lock-Datei.
- Neue Abhängigkeiten.
- Contract-Zusagen für interne Helper-Signaturen.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Automatisches Starten eines ADW-Laufs aus dem Brief, Auto-Merge.
- Semantisches Clustering der Retro-Texte (W12).
- Hill Climbing (W7), Expertise-Datei (W9).
- Benachrichtigung per Mail/Slack/Desktop.
- Installation des Timers durch ADW.
- Retry oder Reparatur-Lauf bei verworfenem Brief; inhaltliche Qualitätsprüfung über
  JSON- und Überschriftencheck hinaus.
- Historie/Archiv mehrerer Berichte je Woche (Überschreiben ist gewollt).
- Persistente Warteschlangen oder Wochenstatus für Agenten- und Push-Fehler; Warnung,
  Vermerk im Bericht und erneuter Aufruf reichen.
