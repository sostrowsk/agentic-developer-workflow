# Spec — W15: `adw improve`, der wöchentliche Verbesserungsbericht

Basis: gemergter Stand **0.36.3** (nach R5). Paket W15 aus `ONBOARDING-SPEC-PLAN.md`,
Entscheidung D7 (Bericht mit Brief, kein automatischer Lauf, kein Auto-Merge). Die
Ausgangslage wurde am 2026-11-08 im Code bestätigt; Zeilenangaben sind Orientierung.
Die Vorentscheidungen E1–E8 des Issues sind bindend und hier nicht neu verhandelbar.

Contract: **Single-Lane**. Der Contract umfasst nur extern beobachtbare Flächen (CLI
`adw improve` mit Optionen, Exits und stdout; Dateien unter `reports/`; Abschnitte des
Berichts; Timer-Units). Interne Helper-Signaturen sind kein Contract.

## Ziel

Den Selbstverbesserungs-Kreislauf schließen: `adw improve` liest das Ledger für einen
Zeitraum, verdichtet Kennzahlen, Retro-Ursachen, wiederkehrende Findings und das
menschliche Urteil **deterministisch** zu einem Markdown-Bericht und lässt für die
wichtigste Problemgruppe mit ADW-seitiger Ursache einen nur lesenden Agenten einen Brief
im Format von `docs/briefs/` entwerfen. Ein optional eingerichteter systemd-User-Timer
startet den Bericht wöchentlich. Weder Bericht noch Brief lösen einen ADW-Lauf oder einen
Merge aus; der Mensch entscheidet.

## Geprüfte Ausgangslage (Stand 0.36.3)

- `compute_kpis(records, since=None, repo_slug=None, signals=None)` (`adw/ledger.py:760`)
  liefert `groups` (je `adw_version`), `total`, `candidates`, `skipped`. Die W14-Signale
  `retro_causes`, `retro_runs`, `ratings`, `finding_feedback` stehen **nur in `total`**
  (`_signal_metrics`, `adw/ledger.py:631`). `ratings` zählt je Lauf die **letzte** gültige
  `rate`-Zeile in Dateireihenfolge; `finding_feedback` zählt alle gültigen
  `finding`-Zeilen. Signale je Lauf werden als `{"retro": …, "feedback": [...]}` neben dem
  `run-record.json` gelesen (`adw/ledger.py:592`).
- `retro.json` (`adw/retro.py`): `{"schema_version", "run_id", "phase", "summary",
  "items": [{"problem", "resolution", "better", "cause", "lever", "evidence": [str, …]}],
  "dropped", "model"}`. `cause` ∈ `prompt`, `context_missing`, `gate`, `orchestrator`,
  `issue_quality`, `model`, `target_repo` (`adw/retro.py:26`).
- `feedback.jsonl`: Zeilen `{"ts", "kind": "rate"|"finding", "value", "note"}`;
  `rate` mit `good`/`bad`, `finding` mit `valid`/`noise` und zusätzlich `finding_key`
  (`adw/cli.py:772`).
- `run-record.json` (Schema 1) trägt u. a. `run_id`, `repo_slug`, `adw_version`,
  `started_at`, `phase`, `issue_title`, `escalation_reason`, `cost_usd` und
  `findings[]` mit `finding_key` und `severity`; derselbe Schlüssel kann in einem Lauf
  mehrfach vorkommen.
- Export (`adw/ledger.py:429`): `run_git` (immer `-c core.hooksPath=/dev/null`,
  `adw/git.py:25`), `git add` nur auf den eigenen Pfad, Commit **nur bei gestagten
  Änderungen** (`diff --cached --quiet`), Push mit `_PUSH_TIMEOUT` fail-open mit Warnung.
  Ledger-Auflösung: `ADW_LEDGER`, sonst `~/.adw/ledger`, muss Wurzel eines
  Git-Arbeitsbaums sein (`_ledger_path`).
- Nicht blockierender `flock` existiert als Muster für `.repo.lock`
  (`adw/cli.py:1181`, `LOCK_EX | LOCK_NB`).
- Lesende Agenten mit JSON-Antwort: `final_reviewer`, `retro_agent` (`adw/agents.py:354`,
  `:369`; `READ_ONLY_TOOLS`, `SCOPED_READ_RULES`, `FABLE`).
- `docs/briefs/README.md` (Vorlage) und `docs/briefs/r2-log-integritaet.md` (Beispiel)
  existieren. Handbuch: Abschnitt „11.1 Ledger" in `docs/ADW-HANDBOOK.md` (Zeile 620) und
  `docs/ADW-HANDBOOK.de.md` (Zeile 636). `examples/` enthält nur `config.yaml`;
  `examples/systemd/` ist neu.
- Diese Bestände werden nur gelesen; Schemata und Semantik bleiben unverändert.

## Scope

### A1 — `adw improve`: Aufruf, Datenquelle, Zeitraum (E1, E4)

- `adw improve [--since <tage>] [--repo-slug <slug>] [--no-brief]`; `--since` Default 7.
- Quelle ausschließlich das Ledger mit derselben Auflösung wie W13/W3; kein Fallback auf
  ein Ziel-Repo oder dessen `.adw/runs/`. Kein Ledger → Exit 1 mit Meldung.
- Läufe im Zeitraum: `started_at` innerhalb der letzten `since` Tage bis zum
  Aufrufzeitpunkt. Vergleichszeitraum: die unmittelbar davorliegenden `since` Tage; die
  gemeinsame Grenze gehört zum aktuellen Zeitraum. `--repo-slug` filtert beide Zeiträume
  und alle Berichtsteile einschließlich der Brief-Auswahl.
- Kein Repo-Lock (kein Ziel-Repo). Nicht blockierender `flock` auf
  `<ledger>/.improve.lock` (Muster `.repo.lock`); belegt → Exit 1 mit Meldung, nichts
  geschrieben, kein Agentenaufruf. Die Lock-Datei wird nie committet.
- stdout: Pfad des Berichts und je ausgegebenem Abschnitt eine Zeile mit Anzahl (Läufe im
  Zeitraum, Problemgruppen, wiederkehrende Findings, Regressionsfall-Kandidaten, Gruppen
  mit Hebel). Exit 0, sobald der Bericht geschrieben ist, auch ohne Brief.

### A2 — Bericht, rein deterministisch (E2)

Code, 0 Modell-Tokens. Markdown mit fünf Abschnitten in genau dieser Reihenfolge:

1. **Kennzahlen:** `compute_kpis` für den Zeitraum (Versionsgruppen, Gesamtzeile,
   `retro_causes`, `ratings`, `finding_feedback`, so wie `total` sie heute liefert) und
   zum Vergleich **nur die Gesamtzeile** des gleich langen Vorzeitraums.
2. **Problemgruppen aus Retros:** alle Retro-Einträge der Läufe im Zeitraum, gruppiert
   nach `cause`. Je Gruppe: Anzahl Einträge, Anzahl **verschiedener** Läufe, Summe
   `cost_usd` dieser Läufe (ein Lauf zählt je Gruppe einmal, auch mit mehreren
   Einträgen). Sortierung absteigend nach Läufe × Kostensumme, bei Gleichstand
   deterministisch. Je Eintrag `problem`, `better`, `lever`, Run-ID und Belege
   (`evidence`), jedes Textfeld auf höchstens 300 Zeichen gekürzt.
3. **Wiederkehrende Findings:** `finding_key`s, die in **mindestens zwei verschiedenen
   Läufen** vorkommen, höchstens 20, deterministisch geordnet. Anzahl = Zahl der Läufe
   (Mehrfachvorkommen im selben Lauf zählen nicht). Je Eintrag Schlüssel, Anzahl,
   Severity und Feedback `valid`/`noise`, zugeordnet über Lauf und `finding_key`.
4. **Regressionsfall-Kandidaten:** Läufe mit `phase == "escalated"` oder letzter
   gültiger Bewertung `bad` (eine von `good` abgelöste `bad`-Bewertung zählt nicht); je
   Lauf Run-ID, `issue_title`, Grund (Eskalation bzw. Bewertung, bei beidem beides).
5. **Hebel je Gruppe:** feste Zuordnung für die vorhandenen Gruppen:

   | Ursache | Hebel |
   |---|---|
   | `prompt`, `model` | Kandidat für Hill Climbing (W7) |
   | `orchestrator`, `gate` | Brief für einen ADW-Lauf |
   | `context_missing`, `target_repo` | Wissen für das Ziel-Repo (Expertise, W9) |
   | `issue_quality` | Brief-Vorlage schärfen |

Weniger als 3 Läufe im Zeitraum → Bericht nur mit Abschnitt 1 und dem Satz
**„Zu wenige Läufe für eine Auswertung"**, kein Agentenaufruf, Exit 0.

### A3 — Brief-Entwurf (E5)

Nur wenn **alle** Bedingungen gelten: nicht `--no-brief`; mindestens 3 Läufe im Zeitraum
(und Repo-Filter); mindestens eine Gruppe mit `cause` ∈ {`orchestrator`, `gate`,
`prompt`}. Gewählt wird die höchstsortierte solche Gruppe; genau ein Agentenaufruf.
`prompt` ist laut E5 briefberechtigt, obwohl Abschnitt 5 dafür W7 nennt; `model` allein
berechtigt nicht.

Neuer Agent `improve_agent` in `REGISTRY`: `model=FABLE`, `tools=READ_ONLY_TOOLS`,
`allowed_tools=SCOPED_READ_RULES`, `max_turns=120`, `timeout_s=1800`, Effort Default,
Ausführung über `SdkAgentRunner`. `cwd` ist das Wurzelverzeichnis des ADW-Quellcodes, aus
dem das Paket läuft, damit er Prämissen im Code prüfen kann; er schreibt keine Dateien.

Auftrag: der Bericht-Abschnitt der gewählten Gruppe, die Brief-Vorlage
`docs/briefs/README.md` und das Beispiel `docs/briefs/r2-log-integritaet.md`. Antwort
ausschließlich JSON `{"title": str, "brief_markdown": str}`. Der Code validiert JSON und
Feldtypen und prüft, dass `brief_markdown` die Überschriften **„Ausgangslage"**,
**„Aufgabe"**, **„Vorentscheidungen"**, **„Deferred"** und **„Akzeptanzkriterien"**
enthält. Sonst entsteht kein Brief, und der Bericht vermerkt
**„Brief-Entwurf verworfen: <Grund>"**. Jede Ausnahme des Agenten (auch Timeout oder
nicht lesbare Vorlage/Beispiel im `cwd`) wird gefangen und mit Grund im Bericht vermerkt.
Der Bericht entsteht immer; Brief-Fehler ändern Exit 0 nicht. Kein Retry, kein zweiter
Agent.

### A4 — Ablage, Commit und Push (E3, E6)

- Bericht: `<ledger>/reports/<ISO-Jahr>-W<ISO-Woche, zweistellig>.md`; gültiger Brief:
  `<ledger>/reports/<ISO-Jahr>-W<ISO-Woche, zweistellig>-brief.md`. Maßgeblich ist die
  ISO-Woche des **Aufrufdatums**, unabhängig vom ausgewerteten Zeitraum. `reports/` wird
  bei Bedarf angelegt. Erneuter Aufruf in derselben Woche überschreibt den Bericht und
  einen erneut erzeugten Brief.
- **Brief und Bericht gehören zusammen:** Erzeugt ein Aufruf keinen gültigen Brief
  (`--no-brief`, zu wenige Läufe, keine berechtigte Gruppe, verworfen, Ausnahme), wird
  ein vorhandener `…-brief.md` derselben Woche **entfernt**, damit kein veralteter
  Entwurf neben dem aktuellen Bericht liegt. Der Bericht vermerkt den Grund, warum kein
  Brief vorliegt (bei verworfen/Ausnahme wie in A3, sonst ein kurzer Satz).
- Bericht und ggf. Brief bzw. dessen Entfernung werden gemeinsam committet und gepusht:
  wie beim Export über `run_git` (Hooks aus). Gestagt wird **immer** der Berichtspfad und
  der Briefpfad **nur, wenn die Datei existiert oder im Index getrackt ist** (dann als
  Löschung, `git add -A -- <pfad>`). Ein nie vorhandener Brief wird nicht als Pathspec
  übergeben, sonst scheitert `git add` und der Bericht bliebe uncommittet. Commit nur
  bei gestagten Änderungen, Nachricht **`improve <ISO-Jahr>-W<Woche>`**, Push mit
  Timeout. Commit- und Push-Fehler sind fail-open: Artefakte bleiben lokal im Zustand des
  aktuellen Aufrufs, Warnung, Exit 0.

### A5 — Timer-Units und Dokumentation (E7)

- `examples/systemd/adw-improve.service`: `[Unit]`, `[Service]` mit `Type=oneshot` und
  `ExecStart` = `uv run --project <ADW-Pfad> adw improve`; anzupassende Pfade (ADW-Pfad,
  ggf. `ADW_LEDGER`) klar als Platzhalter markiert.
- `examples/systemd/adw-improve.timer`: `[Unit]`, `[Timer]` mit
  `OnCalendar=Mon *-*-* 07:00` und `Persistent=true`, dazu `[Install]` mit
  `WantedBy=timers.target`, damit `systemctl --user enable` funktioniert.
- ADW installiert nichts. Handbuch EN+DE, Abschnitt „Ledger" (11.1): `adw improve` mit
  Optionen und Exits, Aufbau des Berichts, Brief-Entwurf und menschliche Entscheidung,
  Ablage unter `reports/`, Einrichtung des Timers mit Pfadanpassung und
  `systemctl --user enable --now adw-improve.timer`.
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue Sektion
  `## [0.37.0] — Unreleased` mit `### Added`. Version in `pyproject.toml` bleibt
  `0.36.3`.

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

## Akzeptanzkriterien

1. Fixture-Ledger mit 5 Läufen im Zeitraum (Retros mit verschiedenen `cause`, ein
   Finding in zwei Läufen, ein eskalierter Lauf, eine `bad`-Bewertung) und 2 älteren
   Läufen im Vorzeitraum: der Bericht enthält die fünf Abschnitte aus E2 in Reihenfolge
   mit den erwarteten Zahlen; die Vergleichs-Gesamtzeile umfasst die 2 älteren Läufe;
   Gruppen sind nach Läufe × Kostensumme sortiert, ein Lauf mit zwei Einträgen derselben
   `cause` zählt einmal; Hebel entsprechen der festen Zuordnung.
2. Das wiederkehrende Finding erscheint mit Anzahl 2, Severity und seinem Feedback;
   Findings aus nur einem Lauf erscheinen nicht, auch bei Mehrfachvorkommen im selben
   Lauf.
3. Eskalierter und `bad`-bewerteter Lauf stehen unter Regressionsfall-Kandidaten mit
   Run-ID, `issue_title` und Grund; ein Lauf mit `bad`, dann `good` nicht.
4. Weniger als 3 Läufe → Bericht nur mit Kennzahlen und „Zu wenige Läufe für eine
   Auswertung", kein Agentenaufruf, Exit 0.
5. Mit Gruppe `orchestrator`: der gefakte `improve_agent` wird genau einmal mit `cwd` =
   ADW-Quellverzeichnis aufgerufen; gültige Antwort → `…-brief.md` geschrieben; Antwort
   ohne Pflichtüberschriften bzw. Ausnahme → kein Brief, Vermerk „Brief-Entwurf
   verworfen: …" bzw. Ausnahmevermerk im Bericht, Exit 0.
6. `--no-brief` → kein Agentenaufruf, Bericht wird geschrieben und committet, obwohl nie
   ein Brief existiert hat (kein `git add`-Fehler). Ohne Gruppe aus {`orchestrator`,
   `gate`, `prompt`} ebenfalls kein Agentenaufruf.
7. Bericht und Brief liegen unter `reports/` im Fixture-Ledger, genau ein Commit mit
   Nachricht `improve <Jahr>-W<Woche>`; erneuter Aufruf in derselben Woche überschreibt
   die Dateien. Folgt auf einen Aufruf mit gültigem Brief ein Aufruf derselben Woche
   ohne Brief (z. B. `--no-brief` oder weniger als 3 Läufe), ist `…-brief.md` danach
   nicht mehr vorhanden, der Bericht nennt den Grund, und die Löschung ist Teil des
   Commits. Fehlschlagender Push (simuliert) ändert den Exit-Code nicht.
8. Belegter `.improve.lock` → Exit 1 ohne Warten, nichts geschrieben; fehlendes Ledger →
   Exit 1 mit Meldung.
9. `examples/systemd/adw-improve.service` und `adw-improve.timer` existieren; ein Test
   liest beide und prüft `[Unit]`/`[Service]` bzw. `[Unit]`/`[Timer]` und
   `OnCalendar=Mon *-*-* 07:00`.
10. Handbuch EN+DE beschreiben im Abschnitt „Ledger" `adw improve`, Bericht, Brief und
    Timer; beide Changelogs enthalten `## [0.37.0] — Unreleased` mit `### Added`;
    `pyproject.toml` steht unverändert auf `0.36.3`.
11. `uv run ruff check .` und `uv run pytest -x -q` sind grün.

Richtwert: 10–14 neue Tests. Die Zusatzfälle in 1–3, 6 und 7 (Dedup je Gruppe,
Mehrfachvorkommen, `bad`→`good`, keine berechtigte Gruppe, Commit ohne je vorhandenen
Brief, Entfernen des alten Briefs) sind Fixture-Details bzw. Erweiterungen bestehender
Fälle, keine eigenen Testdateien. Deutlich mehr ist ein Zeichen für
Gold-Plating. Tests nutzen ein temporäres Fixture-Ledger über `ADW_LEDGER` und einen
gefakten Runner; kein echtes Modell, kein Netz, kein Zugriff auf `~/.adw/ledger` (E8).

## Contract (extern beobachtbar)

- CLI `adw improve [--since <tage>] [--repo-slug <slug>] [--no-brief]`; Exit 0 nach
  geschriebenem Bericht, Exit 1 bei fehlendem Ledger oder belegtem Lock; stdout mit
  Berichtspfad und einer Zeile je Abschnitt.
- Dateien `<ledger>/reports/<YYYY>-W<WW>.md` und `<ledger>/reports/<YYYY>-W<WW>-brief.md`;
  der Brief existiert genau dann, wenn der zuletzt geschriebene Bericht derselben Woche
  einen gültigen Brief erzeugt hat.
- Abschnittsüberschriften und -reihenfolge des Berichts (E2), Hinweissatz „Zu wenige
  Läufe für eine Auswertung", Vermerk „Brief-Entwurf verworfen: <Grund>".
- Ledger-Commit `improve <YYYY>-W<WW>` (vom Produkt erzeugtes Verhalten).
- `examples/systemd/adw-improve.service`, `examples/systemd/adw-improve.timer`.
- Keine internen Helper-Signaturen.

## Definition of Done

- Akzeptanzkriterien 1–11 erfüllt; die Verhaltensfälle 1–9 sind durch Tests belegt,
  Kriterium 10 durch Prüfung der Dateien.
- Bestehende Tests grün; `adw kpis`, Export, Retro und Feedback verhalten sich wie vorher,
  Schemata unverändert.
- Kein Test berührt `~/.adw/ledger`, Netz oder ein echtes Modell.
- Handbuch und Changelog in EN und DE inhaltlich synchron; Version nicht angehoben.
- Kein neuer persistenter Zustand außer `reports/` und `.improve.lock`; keine neue
  Abhängigkeit.
