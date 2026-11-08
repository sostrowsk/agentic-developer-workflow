# W15: `adw improve` — der wöchentliche Verbesserungsbericht

Paket W15 aus `ONBOARDING-SPEC-PLAN.md` (Entscheidung D7: **Bericht mit Brief**, kein
automatischer Lauf, kein Auto-Merge). Setzt auf dem gemergten Stand **0.36.3** (nach R5) auf
(Ledger aus W13, `adw kpis` aus W3, Retro und Feedback aus W14, Effort aus W2). Die
Ausgangslage ist vor dem Bauen gegen den dann aktuellen Stand zu prüfen, insbesondere
die tatsächlich gebauten Schemata von `retro.json` und `feedback.jsonl`.

Ziel ist der Abschluss des Selbstverbesserungs-Kreislaufs: Einmal pro Woche liest ADW
das Ledger, verdichtet Kennzahlen, Retro-Ursachen, wiederkehrende Findings und das
menschliche Urteil zu einem Bericht und entwirft für das wichtigste ADW-seitige
Problem einen Brief im Format von `docs/briefs/`. Der Mensch entscheidet, ob daraus
ein ADW-Lauf wird.

## Ausgangslage

- Ledger `~/.adw/ledger` (`ADW_LEDGER`), Layout `runs/<slug>/<run_id>/` mit
  `run-record.json` (Schema 1), ab W14 `retro.json`/`retro.md` und `feedback.jsonl`.
- `compute_kpis(records, since=None, repo_slug=None, signals=None)` (`adw/ledger.py:760`) liefert Kennzahlen je ADW-Version, Kandidaten nach
  festen Regeln und ab W14 `retro_causes`, `ratings`, `finding_feedback`.
- Findings tragen ab W4 normalisierte Schlüssel (`finding_key`), damit lassen sich
  wiederkehrende Findings über Läufe zählen.
- Briefs entstehen heute von Hand nach `docs/briefs/README.md` (gemessene
  Ausgangslage, Vorentscheidungen E1…, Bestand außerhalb des Scopes, Deferred,
  messbare ACs, Toolchain).
- Agenten laufen über `SdkAgentRunner` (`adw/agents.py`); lesende Agenten antworten
  nur mit JSON, das der Orchestrator validiert (Muster `final_reviewer`,
  `retro_agent`).

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- `adw kpis`, Export, Retro und Feedback bleiben unverändert; W15 liest nur.
- Kein Hill Climbing (W7), keine Expertise-Datei (W9).

## Aufgabe

**A1 — `adw improve`.** Neues Kommando, das das Ledger für einen Zeitraum auswertet
und einen Bericht schreibt (E2–E4).

**A2 — Brief-Entwurf.** Für die wichtigste Problemgruppe mit ADW-seitiger Ursache
entwirft ein nur lesender Agent einen Brief (E5).

**A3 — Ablage.** Bericht und Brief kommen ins Ledger, Commit und Push fail-open (E6).

**A4 — Wöchentlicher Start.** Beispiel-Units für einen systemd-User-Timer und eine
Anleitung im Handbuch (E7).

**A5 — Doku und Changelog.** Handbuch EN+DE, Abschnitt „Ledger": `adw improve`,
Bericht, Brief, Timer. `CHANGELOG.md`/`CHANGELOG.de.md` synchron: neue Sektion
`## [0.37.0] — Unreleased` mit `### Added`. Version in `pyproject.toml` **nicht**
ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 — Aufruf:** `adw improve [--since <tage>, Default 7] [--repo-slug <slug>]
  [--no-brief]`. Quelle nur das Ledger (gleiche Auflösung wie W13/W3); kein Ledger →
  Exit 1 mit Meldung. Läufe im Zeitraum = `started_at` innerhalb der letzten
  `since` Tage. Läuft **nicht** unter dem Repo-Lock (kein Ziel-Repo), aber mit einem
  `flock` auf `<ledger>/.improve.lock` (nicht blockierend; belegt → Exit 1).
- **E2 — Bericht, rein deterministisch (Code, 0 Tokens),** Markdown mit festen
  Abschnitten in dieser Reihenfolge:
  1. **Kennzahlen:** Ausgabe von `compute_kpis` für den Zeitraum (Versionen,
     Gesamtzeile, `retro_causes`, `ratings`, `finding_feedback`) und zum Vergleich
     für den gleich langen Zeitraum davor (nur Gesamtzeile).
  2. **Problemgruppen aus Retros:** alle Retro-Einträge im Zeitraum, gruppiert nach
     `cause`; je Gruppe Anzahl Einträge, Anzahl Läufe, Summe `cost_usd` dieser Läufe,
     sortiert nach Anzahl Läufe × Kostensumme; je Gruppe die Einträge mit `problem`,
     `better`, `lever`, Run-ID und Belegen (gekürzt auf 300 Zeichen je Feld).
  3. **Wiederkehrende Findings:** `finding_key`s, die in **mindestens zwei Läufen**
     vorkommen, mit Anzahl, Severity und Feedback (`valid`/`noise`), höchstens 20.
  4. **Regressionsfall-Kandidaten:** Läufe mit `phase == "escalated"` oder letzter
     Bewertung `bad`, mit Run-ID, `issue_title`, Grund.
  5. **Hebel je Gruppe:** feste Zuordnung `prompt`/`model` → „Kandidat für Hill
     Climbing (W7)"; `orchestrator`/`gate` → „Brief für einen ADW-Lauf";
     `context_missing`/`target_repo` → „Wissen für das Ziel-Repo (Expertise, W9)";
     `issue_quality` → „Brief-Vorlage schärfen".
  Weniger als 3 Läufe im Zeitraum → nur Abschnitt 1 und der Satz „Zu wenige Läufe
  für eine Auswertung", kein Brief.
- **E3 — Ablage:** `reports/<ISO-Jahr>-W<ISO-Woche, zweistellig>.md` im Ledger
  (Woche des Aufrufdatums), bei erneutem Aufruf in derselben Woche überschrieben.
- **E4 — Ausgabe:** Pfad des Berichts und eine Zeile je Abschnitt mit Anzahl auf
  stdout; Exit 0 auch ohne Brief.
- **E5 — Brief-Entwurf:** Nur wenn nicht `--no-brief`, mindestens 3 Läufe und
  mindestens eine Gruppe mit `cause` ∈ {`orchestrator`, `gate`, `prompt`} existiert;
  gewählt wird die höchstsortierte solche Gruppe. Neuer Agent `improve_agent` in
  `REGISTRY`: `model=FABLE`, nur lesend (`READ_ONLY_TOOLS`, `SCOPED_READ_RULES`),
  `max_turns=120`, `timeout_s=1800`, Effort Default. `cwd` ist das Wurzelverzeichnis
  des ADW-Quellcodes (das Repo, aus dem das Paket läuft), damit er Prämissen im Code
  prüfen kann. Auftrag: der Bericht-Abschnitt der gewählten Gruppe, die
  Brief-Vorlage `docs/briefs/README.md` und ein vorhandener Brief als Beispiel
  (`docs/briefs/r2-log-integritaet.md`); Antwort nur als JSON
  `{"title": str, "brief_markdown": str}`. Der Code prüft, dass `brief_markdown`
  die Überschriften „Ausgangslage", „Aufgabe", „Vorentscheidungen", „Deferred" und
  „Akzeptanzkriterien" enthält; sonst wird kein Brief geschrieben und der Bericht
  vermerkt „Brief-Entwurf verworfen: <Grund>". Ablage
  `reports/<Jahr>-W<Woche>-brief.md`. Jede Ausnahme des Agenten wird gefangen und im
  Bericht vermerkt; der Bericht entsteht immer.
- **E6 — Commit/Push:** wie beim Export (`run_git`, Hooks aus, Push mit Timeout,
  fail-open); Commit-Nachricht `improve <Jahr>-W<Woche>`.
- **E7 — Timer:** `examples/systemd/adw-improve.service` (oneshot, `ExecStart` ruft
  `uv run --project <ADW-Pfad> adw improve`, Platzhalter klar markiert) und
  `examples/systemd/adw-improve.timer` (`OnCalendar=Mon *-*-* 07:00`,
  `Persistent=true`). ADW installiert nichts selbst; das Handbuch beschreibt
  `systemctl --user enable --now adw-improve.timer`.
- **E8:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Tests mit
  Fixture-Ledger (temporär, `ADW_LEDGER`) und gefaktem Runner; kein echtes Modell,
  kein Netz, kein Zugriff auf `~/.adw/ledger`.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Automatisches Starten eines ADW-Laufs aus dem Brief, Auto-Merge.
- Semantisches Clustering der Retro-Texte (W12).
- Hill Climbing (W7), Expertise-Datei (W9).
- Benachrichtigung per Mail/Slack/Desktop.
- Installation des Timers durch ADW.

## Akzeptanzkriterien

1. Fixture-Ledger mit 5 Läufen im Zeitraum (Retros mit verschiedenen `cause`,
   ein Finding in zwei Läufen, ein eskalierter Lauf, eine `bad`-Bewertung) und 2
   älteren Läufen: der Bericht enthält die fünf Abschnitte aus E2 mit den erwarteten
   Zahlen; Sortierung der Gruppen nach Läufe × Kosten stimmt.
2. Das wiederkehrende Finding erscheint mit Anzahl 2 und Feedback; Findings aus nur
   einem Lauf erscheinen nicht.
3. Eskalierter und `bad`-bewerteter Lauf stehen unter Regressionsfall-Kandidaten.
4. Weniger als 3 Läufe → nur Kennzahlen und der Hinweissatz, kein Agentenaufruf.
5. Mit Gruppe `orchestrator`: der gefakte `improve_agent` wird genau einmal mit
   `cwd` = ADW-Quellverzeichnis aufgerufen; gültige Antwort → `…-brief.md`
   geschrieben; Antwort ohne Pflichtüberschriften bzw. Ausnahme → kein Brief,
   Vermerk im Bericht, Exit 0.
6. `--no-brief` → kein Agentenaufruf.
7. Bericht und Brief liegen unter `reports/` im Fixture-Ledger, genau ein Commit;
   erneuter Aufruf in derselben Woche überschreibt.
8. Belegter `.improve.lock` → Exit 1.
9. Die Timer-Units existieren unter `examples/systemd/` und sind syntaktisch
   vollständig (Test liest beide Dateien und prüft `[Unit]`/`[Service]`/`[Timer]`
   und `OnCalendar`).
10. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 10–14 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (CLI
`adw improve` mit Optionen, Dateien unter `reports/`, Abschnitte des Berichts,
Timer-Units), keine internen Helper-Signaturen.
