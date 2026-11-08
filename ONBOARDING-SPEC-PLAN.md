# ADW weiterentwickeln: Review-Befunde, TAC, Claude 5.x, Selbstverbesserung — Onboarding, Spec, Plan

Stand: 2026-11-08 · Basis: ADW 0.31.1 (`main`, `7c4aa7d`) · Baseline: `uv run ruff check .` grün,
`uv run pytest -q` → **1362 grün, 1 Skip**

Dieses Dokument bündelt vier Quellen und ist die Grundlage für eine Reihe einzelner ADW-Läufe,
nicht für einen großen Lauf:

1. **Code-Review des Stands 0.31.1** (2026-11-08): drei Opus-5.5-Reviewer (Kern, CLI/State/Infra,
   GUI) und `codex review` mit `gpt-6-astra`, jeweils über das ganze Repo. Die wichtigsten Befunde
   sind im Code nachgeprüft.
2. **Kurs *Tactical Agentic Coding*** (TAC, 14 Lektionen) und das Jev-Video (IndyDevDan).
3. **Vortrag *Your Old Prompts Are Making Claude Worse*** (Lance Martin, Anthropic).
4. **Konzept Selbstverbesserung:** Rückblick nach jedem Lauf, zentrales Ledger, Wochenschleife.

**Kernaussage:** Beim Kontrollfluss ist ADW weiter als TAC und soll so bleiben; die Trennung
Orchestrator („Gehirn") / Lane-Sandbox („Hände") / anhängendes Event-Log entspricht bereits dem
Architekturprinzip aus dem Vortrag. Die Lücken liegen an vier Stellen:
(1) Sicherheits- und Integritätsfehler aus dem Review, die jeden weiteren Lauf betreffen — auch
jeden Lauf von ADW an sich selbst; (2) welches Wissen die Agenten bekommen; (3) wie teuer und wie
lange ein einzelner Agent-Lauf sein darf; (4) dass Änderungen an Prompts, Effort und Kontext bisher
nicht messbar sind und Erfahrungen aus Läufen mit dem Prune verloren gehen.

---

## 1. First 5 minutes

1. `git status && git log --oneline -5` — erwartet: sauber, HEAD auf dem Commit dieses Dokuments oder neuer
2. `uv run ruff check . && uv run pytest -q` — Baseline 1362 grün, 1 Skip
3. Dieses Dokument lesen, dann Abschnitt 4 (offene Entscheidungen) prüfen
4. Brief-Vorlage lesen: `docs/briefs/README.md` — jeder Lauf aus Abschnitt 6 bekommt einen Brief
   in diesem Format (gemessene Ausgangslage, nummerierte Vorentscheidungen E1…, Bestand außerhalb
   des Scopes, Deferred-Liste, messbare ACs, Toolchain `ruff` + `pytest`)
5. Hintergrund bei Bedarf: die 14 Lektions-Docs (Abschnitt 10)

---

## 2. Ausgangslage — geprüfte Fakten

Jede Zeile ist am 2026-11-08 im Code geprüft. Vor jedem Lauf erneut gegen den gemergten Stand
messen (Regel aus `docs/briefs/README.md`).

| # | Befund | Beleg | Folge |
|---|---|---|---|
| F1 | Agenten laden weder `CLAUDE.md` noch `.claude/` des Ziel-Repos | `adw/agents.py:525` `setting_sources=[]` (bewusst, Isolation) | Projektkonventionen erreichen Agenten nur über Spec/Plan |
| F2 | Claude-Agenten laufen ohne `max_turns` und ohne Zeitlimit | `adw/agents.py:515–548`; `max_turns=1` nur im Modell-Check (`:461`) | ein hängender oder ausufernder Agent-Lauf hat keine Grenze; Codex (900 s), Gates und CI haben eine |
| F3 | Kein Effort gesetzt, alle Agenten auf Default | `adw/agents.py:515` `ClaudeAgentOptions` ohne `effort` (das SDK kennt `low`…`max`) | Kosten/Leistung nicht gesteuert |
| F4 | Findings werden über den exakten Text erkannt | `adw/review_policy.py:15` `f"{item.file}\|{item.issue}"` | umformuliertes Finding gilt als neu: Circuit Breaker, Deferred-Liste und Findings-Gedächtnis greifen nicht |
| F5 | Circuit Breaker vergleicht Fehler exakt | `adw/context.py:51` `_require_progress` | gleicher Fehler mit anderen Zeilennummern/Zeitstempeln gilt als Fortschritt |
| F6 | Fix-Runden setzen dieselbe Agent-Session fort | `adw/lanes.py:182` `resume=resume`, `adw/authoring.py:559` `resume=session` | Kontext wächst über bis zu 10 Gate-Iterationen |
| F7 | Gate-Output wird auf die letzten 200 Zeilen gekürzt | `adw/gates.py:16` `MAX_OUTPUT_LINES = 200` | der eigentliche Fehler steht nicht immer am Ende |
| F8 | E2E-Gate nur mit `--parallel` | `adw/phases.py:99` | Single-Lane-Läufe haben kein E2E |
| F9 | Kein Wissen über Runs hinweg; Run-Artefakte werden nach 20 Runs gelöscht | `adw/config.py:112` `keep_runs = 20` | jeder Lauf startet ohne Vorwissen; Erfahrungen und Kennzahlen gehen mit dem Prune verloren |
| F10 | Prompts enthalten keine klassischen Booster | grep nach „step by step", „think hard", „IMPORTANT", „double-check" in `adw/*.py`: 0 Treffer in Prompts | gut; Restrisiko: ein Agent-Prompt wird aus bis zu drei Modulen zusammengesetzt (`agents.py`, `authoring.py`/`lanes.py`, `codex.py`) → Widersprüche möglich, siehe F13 |
| F11 | `docs/SPEC.md` nennt noch Opus 4.8 / Fable 5 | `docs/SPEC.md` Abschnitt 3 | veraltete Fakten in der Doku (README nennt Opus 5.5 / Fable 5.1) |
| F12 | Router (chore/bug/feature) und Webhook-Intake sind Non-Goals | `docs/SPEC.md:30` | W10/W11 erfordern zuerst eine Entscheidung (D1) |
| F13 | **Widerspruch im Build-Auftrag:** der Build-Agent soll RED selbst bestätigen, obwohl der Orchestrator RED in einem eigenen reinen Test-Lauf beweist | `adw/agents.py` `build_agent.system_append` („using TDD (test first, confirm RED, …)"), `adw/lanes.py:141` („TDD: test first, confirm RED"); dagegen `adw/lanes.py:367` („Write ONLY the tests") + `_confirm_red` | genau das Muster „Verifikationsritual + widersprüchliche Regel" aus dem Vortrag: zusätzliche Tokens, mögliche Verwirrung |
| F14 | **Kostenverteilung und Cache gemessen** (15 vorhandene Runs, `cost_usd` aus dem SDK, bei Abo-Login ein Rechenwert) | `agent.run`-End-Events in `.adw/runs/*/events.jsonl` | `build_agent` $414 von $615 (67 %, 2,3 Mio. Output-Tokens); Synthese-Agents $110; Cache-Trefferquote 80–96 % → **Cache ist kein Hebel, Effort beim Build-Agent schon** |
| F15 | Prompts jedes Agent-Laufs liegen vollständig im Event-Log | `adw/agents.py:194` (`"prompt": task` im `agent.run`-Start), Snapshots vor/nach jedem Agenten | einzelne Agentenschritte lassen sich mit identischer Eingabe wiederholen → Grundlage für Evals (W7) |
| F16 | Das ADW-Repo auf GitHub ist **öffentlich** | `github.com:sostrowsk/agentic-developer-workflow` | Retros/Ledger mit Kundencode dürfen **nicht** ins ADW-Repo (→ D6) |

---

## 3. Code-Review 0.31.1 — Befunde

Zusammengeführt aus beiden Reviews und dedupliziert. Spalte „Quelle": O = Opus, C = Codex, O+C =
beide unabhängig. ✔ = im Code nachgeprüft. „Lauf" verweist auf Abschnitt 5.

### Sicherheit und fremde Daten

| ID | Prio | Stelle | Befund | Quelle | Lauf |
|---|---|---|---|---|---|
| C1 | **P1** | `adw/lanes.py:529–545` `_seed_artifacts` | `shutil.copy2` folgt Symlinks am Ziel; beim Resume läuft das Seeding **vor** der symlink-sicheren Restauration. Ein Agent legt `.adw/spec.md -> ~/.bashrc` an → der unsandboxte Orchestrator überschreibt Dateien außerhalb des Worktrees. Zusätzlich: `git add .adw` committet Fremddateien bzw. den ganzen Index und bewegt HEAD → `_require_expected_head` eskaliert fälschlich | O+C ✔ | R1 |
| C2 | **P1** | `adw/phases.py:640–655` `_push_branch` | nacktes `--force-with-lease` nimmt den aktuellen Tracking-Ref als Erwartung; nach einem `fetch` (IDE, Nutzer) überschreibt der Push fremde Commits auf dem Integrations-Branch. Fix: `--force-with-lease=<branch>:<persistierte zuletzt gepushte SHA>` | C ✔ | R1 |
| C3 | **P1/P2** | `adw/preflight.py:302–304`, `adw/cli.py:297–302`, `adw/authoring.py` | kein Repo-weiter Lock für Preflight und Authoring im Haupt-Checkout: die Preflight-Selbstheilung löscht/resettet `.adw`-Artefakte eines laufenden anderen Runs; beim eigenen Resume mitten in der Plan-Synthese wird die reviewte Zwischenfassung durch eine alte Fassung ersetzt | O+C | R1 |
| C4 | P2 | `adw/retention.py:228` `_run_worktrees` | ist `trees/` ein Symlink auf einen fremden Worktree, wird er nach `resolve()` zur Lösch-Grenze; `_delete_run` entfernt den fremden Worktree | C ✔ | R1 |
| C5 | P2 | `adw/gui/app.py:210` `create_app` | keine Host-Header-Prüfung → DNS-Rebinding liest `/api/runs` und Events (ungeschwärzt). Fix: `TrustedHostMiddleware` | O+C | R3 |

### Resume- und Log-Integrität (Voraussetzung für das Ledger)

| ID | Prio | Stelle | Befund | Quelle | Lauf |
|---|---|---|---|---|---|
| C6 | P2 | `adw/lanes.py:299–303` `_confirm_red` | `session_id` wird schon beim Stream-Start gecheckpointet und gilt als „Test-Lauf fertig". Bricht der Test-Lauf ab (Abo-Limit, Ctrl-C), überspringt der Resume ihn → Dauer-Eskalation „keine Änderungen" oder halbe Tests als RED-Beweis. Fix: eigenes Flag `test_pass_done` | O+C ✔ | R2 |
| C7 | P2 | `adw/snapshots.py:65–67` | Seq-Vergabe ohne Lock: parallele Lanes schreiben denselben Ref, der zweite gewinnt; GUI-Diffs zeigen den Tree der falschen Lane. Fix: create-only `update-ref <ref> <commit> ""` + Retry | O+C ✔ | R2 |
| C8 | P2 | `adw/retention.py:424–431` | Prune nimmt den Execution-Lock des Laufs nicht; läuft der Executor nach `done`/`escalated` noch, gehen beim Komprimieren Appends verloren | C | R2 |
| C9 | P2 | `adw/events.py:215–219` | Resume eines fertigen Laufs mit `.gz`-Log legt ein neues `events.jsonl` ab Seq 1 an; die nächste Komprimierung überschreibt die Historie | C | R2 |
| C10 | P3 | `adw/state.py:209–216` `_write_snapshot` | kein `fsync` vor `os.replace` → nach Stromausfall ggf. leeres `state.json`, Lauf nicht mehr resumebar | O | R2 |
| C11 | P3 | `adw/cli.py:610–625` `_maybe_auto_prune` | Auto-Prune läuft auch nach `--dry-run` und kann den gerade fertig gewordenen Lauf löschen (er wird nicht ausgeschlossen) | O ✔ | R2 |
| C12 | P3 | `adw/retention.py:72–73` `_git` | `TimeoutExpired`/`OSError` werden nicht zu `RetentionError` → Traceback, bereits gelöschte Läufe werden nicht gemeldet | O | R2 |

### GUI (Live-Ansicht)

| ID | Prio | Stelle | Befund | Quelle | Lauf |
|---|---|---|---|---|---|
| C13 | **P1/P2** | `adw/gui/app.py:172–180` + `static/app.js:1343–1348` | SSE-Stream endet beim **ersten** `run`-End-Record; nach `adw approve`/`resume` (zweite Run-Spanne im selben Log) gibt es keine Live-Updates mehr — betrifft jeden Lauf mit Approval-Gate | O+C ✔ | R3 |
| C14 | P2 | `adw/gui/app.py:181–185` | synchrone Poll-Schleife ohne Yield belegt Worker-Threads auch nach Client-Abbruch; ~40 Aufrufe abgebrochener Läufe → GUI hängt. Fix: async Generator mit Heartbeat | O+C | R3 |
| C15 | P2 | `adw/gui/registry.py:170–173` | Read-modify-write ohne Interprozess-Lock: zwei gleichzeitige Registrierungen → ein Repo verschwindet aus der GUI | C | R3 |
| C16 | P3 | `adw/gui/app.py:156,181`, `app.js:1334` | Läufe ohne Trace und `.gz`-Logs: EventSource verbindet sich endlos neu; fertige Läufe spielen beim Öffnen das ganze Log erneut ab | O | R3 |
| C17 | P3 | `adw/gui/runs.py`, `serialize.py`, `model.py`, `run_list.html` | Record-Felder mit falschem Typ (`status` als Liste, `ts` als int) → 500 auf `/` für alle Repos | O | R3 |
| C18 | P3 | `adw/gui/templates/run_detail.html:123–125` | Tools-Prev/Next-Links verlieren `limit` und die Raw-Filter | O | R3 |

### CI / Forge

| ID | Prio | Stelle | Befund | Quelle | Lauf |
|---|---|---|---|---|---|
| C19 | P2 | `adw/github.py:111–114` | der konfigurierte Staging-Job gilt auch bei `skipped`/`neutral` als grün → übersprungenes Deploy = Erfolg | C | R4 |
| C20 | P2 | `adw/github.py:117–123` | Jobs und Workflow-Runs ohne Pagination; > 100 Jobs → Staging-Job „fehlt" | C | R4 |
| C21 | P2 | `adw/ci.py:104–110` | Timeout zieht nur Sleep-Zeit ab, keine API-Laufzeit. Fix: monotone Deadline | C | R4 |
| C22 | P2 | `adw/github.py:130–132`, `:193–200` | leere `--log-failed`-Logs bekommen trotzdem eine Überschrift (Guard „keine Evidenz" greift nicht); log-lose rote Runs (`startup_failure`, `cancelled`) lassen `poll_ci` mit `CiError` scheitern | O+C | R4 |
| C23 | P3 | `adw/ci.py:16` | GitLab-Status `manual` gilt nicht als final → irreführender CI-Timeout | O | R4 |

### Orchestrierung und Robustheit

| ID | Prio | Stelle | Befund | Quelle | Lauf |
|---|---|---|---|---|---|
| C24 | P2 | `adw/lanes.py:451–469` + `adw/triage.py` | Findings mit Lane `unknown` gehen an alle Lanes; eine Lane, die zu Recht nichts ändert, eskaliert als „keine Änderungen" | O | R5 |
| C25 | P3 | `adw/review_policy.py:23–26` + `adw/phases.py:405` | Circuit Breaker vergleicht die neue Runde mit der Vorrunde, ohne die strengere Severity-Schwelle der neuen Runde auf die Vorrunde anzuwenden → unverändertes P1 löst bei 1→2 und 2→3 nicht aus | O | W4 |
| C26 | P3 | `adw/phases.py:344–353`, `adw/context.py:359–366`, `adw/agents.py:574` | `CodexError`, `subprocess.TimeoutExpired` aus `_git` und SDK-`ProcessError` kommen als roher Traceback ohne Resume-Hinweis und ohne `escalation.md` | O | W1 |
| C27 | P3 | `adw/lanes.py:64–67` | nach Eskalation einer Lane arbeitet die parallele Lane weiter; eine zweite Eskalation überschreibt `escalation.md` | O | R5 |
| C28 | P3 | `adw/gates.py:143–152`, `adw/codex.py:581–584` | Reader-Thread läuft nach `join(5)` evtl. weiter → „deque mutated during iteration" kann den Orchestrator abstürzen lassen | O | R5 |
| C29 | P3 | `adw/cli.py:265–302`, `:573–574` | `--parallel` bei Single-Lane-Config wird erst nach dem Speichern abgelehnt (Leichen-Lauf); `--older-than -1` endet mit Exit 2 ohne Meldung | O | R5 |

**Geprüft und sauber** (beide Reviews): keine Shell-Injection (Gates via `shlex.split`, git als
Argumentliste mit deaktivierten Hooks), keine Path-Traversal über Run-IDs/Artefakte/Diff-Refs, kein
XSS (Autoescape, Markdown ohne HTML, JS nur über `textContent`), kein CDN, i18n-Schlüssel EN/DE
vollständig, Severity-Schwellen der Review-Runden und Reihenfolge der Crash-Saves korrekt.

---

## 4. Entscheidungen

| ID | Frage | Betrifft | Stand |
|---|---|---|---|
| D1 | Non-Goals „workflow router" und „webhook intake" beibehalten oder aufheben? | W10, W11 | offen · Empfehlung: Router als **Eingabe** (`--kind` vom Menschen gesetzt) erlauben, Modell-Klassifikation nur als Vorschlag; Trigger weiter Non-Goal, bis W3 Daten liefert |
| D2 | Darf Text (Findings, Logs, Issues) an externe Dienste (Jev, Embedding-APIs)? | W12 | offen · Empfehlung: erst lokal (Embeddings/Reranker); extern nur mit Klärung der Datenaufbewahrung |
| D3 | `context_files`: welche Agenten, welche Größengrenze, nur Text? | W5 | offen · Empfehlung: nur Text, Spec/Plan/Build, harte Größengrenze, Isolation aus F1 bleibt |
| D4 | Expertise-Datei: Ablage, wer schreibt, Freigabe durch Menschen? | W9 | offen · Empfehlung: `.adw/expertise/` im Ziel-Repo, Änderungen als Vorschlag mit Freigabe |
| D5 | Default-Effort pro Agent | W2 | offen · Empfehlung: `build_agent` und Entwurfs-Agents `low`, `final_reviewer` und Synthese vorerst Default; Änderung nur mit Messung (W3/W7) |
| D6 | Wo liegt das Ledger (Retros, Kennzahlen, Fälle)? | W13–W15 | **entschieden:** eigenes **privates GitLab-Repo**. Das ADW-Repo (öffentlich, F16) bekommt nur Ergebnisse: Prompt-Änderungen, bereinigte Eval-Fälle, Changelog |
| D7 | Wie selbstständig ist die Wochenschleife? | W15 | **entschieden:** **Bericht mit Brief.** Kein automatischer ADW-Lauf, kein Auto-Merge; du gibst frei |
| D8 | Retro-Agent bei jedem Lauf oder nur bei eskalierten/schlechten? | W14 | offen · Empfehlung: **bei jedem Lauf** (ca. $0,20–1 gegenüber ~$40 pro Lauf); glatte Läufe liefern die Gegenbeispiele |
| D9 | Name/Pfad des Ledger-Repos und lokaler Klon | W13 | offen · Vorschlag: `<gitlab-gruppe>/adw-ledger`, lokal `~/.adw/ledger/`; Anlage per `glab` |

---

## 5. Spec — Arbeitspakete

Format je Paket: Problem · Lösung · Akzeptanzkriterien (AC) · Nicht im Scope · Aufwand ·
Abhängigkeit · Gate-Empfehlung. Die ACs sind Startpunkte; im Brief werden sie gemessen und präzisiert.
Jeder Bugfix bekommt einen Regressionstest, der vor dem Fix RED ist.

### R1 — Sicherheit und fremde Daten (C1–C4)

- **Problem:** Der Orchestrator läuft unsandboxed; C1 schreibt über Agent-Symlinks außerhalb des
  Worktrees, C2 überschreibt fremde Remote-Commits, C3/C4 löschen fremde Arbeit.
- **Lösung:**
  - C1: vor dem Seeding dieselbe Unlink-/Containment-Prüfung wie `_restore_approved_artifacts`;
    nur die drei Artefaktpfade stagen und committen, nicht `.adw` pauschal.
  - C2: zuletzt gepushte SHA pro Integrations-Branch im State persistieren,
    `--force-with-lease=<branch>:<sha>`; erster Push ohne bekannte SHA nur, wenn der Remote-Branch
    nicht existiert.
  - C3: Repo-weiter Lock (flock) über Preflight und Authoring im Haupt-Checkout; die
    Selbstheilung fasst Artefakte eines nicht-terminalen Laufs mit `authoring_session` nicht an.
  - C4: `trees/` muss echtes Verzeichnis innerhalb des Run-Ordners sein (kein Symlink); jede
    Worktree-Entfernung wird gegen den Run-Ordner geprüft.
- **AC:** je Befund ein Regressionstest, der vorher RED ist (Symlink auf Datei außerhalb bleibt
  unverändert; fremder Remote-Commit nach `fetch` wird nicht überschrieben, Push eskaliert; zweiter
  `adw run` wartet bzw. bricht ab, statt Artefakte zu löschen; symlinkter `trees/`-Ordner → Prune
  verweigert).
- **Aufwand:** M · **Abhängigkeit:** — · **Gate:** `plan` (C2/C3 haben Designfragen)

### R2 — Resume- und Log-Integrität (C6–C12)

- **Problem:** Das Event-Log und `state.json` sind die Wiederherstellungsgrundlage („Session-Log"
  im Sinne des Vortrags) und ab W13 die Datenquelle des Ledgers. Heute können sie bei Crash, Prune
  oder Resume Daten verlieren.
- **Lösung:** C6 Flag `test_pass_done` nach regulärem Ende des Test-Laufs, Session ohne Flag →
  Test-Lauf in derselben Session fortsetzen; C7 create-only `update-ref` mit Retry; C8 Prune nimmt
  den Execution-Lock und lädt den State neu; C9 Resume eines fertigen Laufs ist No-op vor dem
  Emitter (oder `.gz` atomar zurückholen und Seq fortsetzen); C10 `fsync` von Datei und Verzeichnis;
  C11 kein Auto-Prune bei `dry_run`, aktueller Lauf immer ausgenommen; C12 `_git`-Fehler →
  `RetentionError`.
- **AC:** je Befund ein Regressionstest (RED vor dem Fix).
- **Aufwand:** M · **Abhängigkeit:** — · **Gate:** `none` (Lösungen sind entschieden)

### R3 — GUI-Live-Ansicht und Härtung (C5, C13–C18)

- **Lösung:** Stream endet nur am End-Record der **letzten** Run-Spanne (Span-ID-Abgleich wie
  `_run_span`, JS entsprechend); async Generator mit Heartbeat; terminales `event: closed` für Läufe
  ohne Trace/`.gz`; fertige Läufe ohne EventSource bzw. mit letzter Seq als Startpunkt;
  `TrustedHostMiddleware` (Loopback-Namen, erweitert bei `--i-know`); Registry mit Interprozess-Lock;
  Typprüfung von `type`/`span`/`status`/`ts` an einer Stelle; Prev/Next-Links aus dem aktuellen
  Query-String.
- **AC:** Log mit zwei Run-Spannen → Stream liefert alle Events beider Spannen; fremder Host → 400;
  zwei gleichzeitige Registrierungen → beide Repos vorhanden; falsch typisierter Record → Seite
  rendert, Problem wird angezeigt.
- **Aufwand:** M · **Abhängigkeit:** — · **Gate:** `none`

### R4 — CI/Forge (C19–C23)

- **Lösung:** Staging-Job nur bei `success` grün; Pagination für Jobs und Workflow-Runs; monotone
  Deadline über Requests und Sleeps; Überschrift nur bei echtem Log-Inhalt, `CiError` pro Run
  abfangen; GitLab `manual` als final mit eigener Eskalationsmeldung.
- **AC:** je Befund ein Test mit gefakter `gh`/`glab`-Antwort.
- **Aufwand:** S–M · **Abhängigkeit:** — · **Gate:** `none`

### R5 — Orchestrierung und Robustheit (C24, C27–C29)

- **Lösung:** `unknown`-Findings: Leerlauf einer Ziel-Lane ist kein Fehler, solange mindestens eine
  Ziel-Lane etwas geändert hat; gemeinsames Cancel-Event für parallele Lanes, nur die erste
  Eskalation schreibt `escalation.md`; Gate-/Codex-Ausgabe nur nach beendetem Reader-Thread bzw.
  unter Lock lesen; `--parallel` vor `RunState.new` validieren, `--older-than < 0` mit Meldung.
- **Aufwand:** S–M · **Abhängigkeit:** — · **Gate:** `none`

### W0 — Prompt-Audit (kein Code-Lauf)

- **Problem:** F10, F11, F13. Laut Lance Martin verwirren widersprüchliche oder veraltete Regeln die
  5.x-Modelle und Verifikationsrituale kosten Tokens.
- **Lösung:** In Claude Code `/claude-api prompt-audit` auf dieses Repo; Zielmodelle je Agent laut
  `adw/agents.py:REGISTRY`. Ergebnis: Bericht + vorgeschlagener Diff (der Skill ändert nichts selbst).
  **F13 ist bereits bekannt** und wird in jedem Fall behoben: „confirm RED" aus
  `build_agent.system_append` und dem Build-Auftrag `adw/lanes.py:141` entfernen — der
  Orchestrator beweist RED. F11 (Modellnamen in `docs/SPEC.md` EN+DE) gleich mit.
- **AC:** Bericht unter `docs/briefs/`; jeder Fund mit `datei:zeile`, Muster, Konfidenz;
  Entscheidung je Fund dokumentiert. Die Übernahme der Prompt-Änderungen ist ein kleiner ADW-Lauf
  (Gate `none`), Messung danach über W3.
- **Aufwand:** S · **Gate:** — (manuell), Übernahme `none`

### W1 — Grenzen für einen einzelnen Agent-Lauf (+ C26)

- **Problem:** F2, C26.
- **Lösung:** `AgentSpec` um `max_turns` und `timeout_s` erweitern; an `ClaudeAgentOptions`
  durchreichen; Timeout per `anyio.fail_after` um das Einsammeln des Streams. Überschreitung →
  definierte Eskalation bzw. Fehlversuch im Loop, Event mit Grund. `CodexError`,
  `subprocess.TimeoutExpired` aus `_git` und SDK-Fehler an den Aufrufstellen in
  `AgentRunError`/`escalate` übersetzen (Resume-Hinweis, `escalation.md`).
- **AC:** (1) jeder Agent in `REGISTRY` hat explizite Werte; (2) Mock-Runner überschreitet
  `max_turns` → Lauf endet mit benannter Ursache im Event-Log; (3) Timeout → Session-ID ist
  gecheckpointet, `adw resume` funktioniert; (4) Codex-Timeout im Review → `escalation.md` statt
  Traceback.
- **Nicht im Scope:** Budget in USD (Abrechnung läuft über den Plan).
- **Aufwand:** S · **Abhängigkeit:** — · **Gate:** `none`

### W2 — Effort pro Agent

- **Problem:** F3, F14. Lance Martin: stärkeres Modell auf niedriger Stufe ist oft günstiger und
  besser als schwächeres auf hoher. Der Build-Agent trägt 67 % der Kosten.
- **Lösung:** `effort` in `AgentSpec`, Default aus D5; Übersteuerung in `.adw/config.yaml`
  (strikt validiert wie der Rest der Config).
- **AC:** Wert erscheint im `agent.run`-Start-Event und im Lauf-Protokoll (W13); ungültiger Wert in
  der Config → Abbruch beim Start.
- **Aufwand:** S · **Abhängigkeit:** D5 · **Gate:** `none`

### W3 — `adw kpis`: Auswertung über Runs

- **Problem:** Ohne Messung lassen sich W0, W2, W5, W6, W12 nicht bewerten.
- **Lösung:** Read-only-Kommando über das **Ledger** (W13), Fallback `.adw/runs/` solange das Ledger
  fehlt: pro Run Ausgang, Gate-Iterationen, Review-Runden, Fix-Zyklen, Eskalationsgrund,
  menschliche Eingriffe, Dauer, Verbrauch pro Agent; aggregiert pro Zeitraum **und pro ADW-Version**.
  Markiert auffällige Runs als Kandidaten für das Eval-Set (W7).
- **AC:** Zahlen stimmen für mindestens 5 Runs mit dem Run Inspector überein; keine Schreibzugriffe;
  Ausgabe auch als JSON; Vergleich zweier ADW-Versionen.
- **Aufwand:** S–M · **Abhängigkeit:** W13 · **Gate:** `none`

### W4 — Normalisierte Schlüssel für Findings und Fehler (+ C25)

- **Problem:** F4, F5, C25.
- **Lösung:** Eine Normalisierungsfunktion vor Hash/Vergleich: Zeilennummern, absolute Pfade,
  Hex-Adressen, Zeitstempel, Ports, Run-IDs, Whitespace. Für Findings: Datei + normalisierter Text.
  Der Circuit Breaker filtert die Vorrunde mit `_actionable_severities` der aktuellen Runde.
  Semantische Stufe erst in W12.
- **AC:** (1) zwei Fehlerausgaben, nur Zeilennummer/Zeitstempel verschieden → Breaker schlägt an;
  (2) echte neue Fehler → kein Breaker; (3) Deferred-Findings bleiben nach Umformatierung erkannt;
  (4) unverändertes P1 von Runde 1 nach 2 → Breaker schlägt an.
- **Aufwand:** S · **Abhängigkeit:** — · **Gate:** `none`

### W5 — `context_files`: Projektwissen als Text

- **Problem:** F1. Vier Lektionen (2, 9, 13, 14) kommen unabhängig darauf.
- **Lösung:** Config-Feld `context_files` (Pfade relativ zum Ziel-Repo); Inhalt als **reiner Text**
  an die Aufträge von Spec-, Plan- und Build-Agent. Keine Settings, Hooks oder MCP (Isolation aus F1
  bleibt). Harte Größengrenze. **Cache-Regel (Abschnitt 7):** fester Block vor allem Variablen.
- **AC:** Inhalt erscheint im Auftrag; Datei fehlt → klare Fehlermeldung beim Start; Größe über
  Grenze → Abbruch; Präfix zweier Aufträge identisch.
- **Aufwand:** S · **Abhängigkeit:** D3 · **Gate:** `plan`

### W6 — Frische Session in Fix-Runden (cache-bewusst)

- **Problem:** F6. Widerspricht TAC „one agent, one prompt, one purpose"; Kontext wächst.
- **Lösung:** Ab Schwelle (Rundenzahl oder gemessene Kontextgröße) frische Session statt `resume`;
  Einstieg = stabiler Block (Spec, Plan, Contract, `context_files`) + variabler Teil (Gate-Fehler,
  Finding). Schwelle konfigurierbar.
- **AC:** unter Schwelle `resume`, darüber neue Session mit vollständigem Einstieg; Session-Wechsel
  als Event; W3 zeigt Gate-Iterationen und Verbrauch vorher/nachher.
- **Aufwand:** S–M · **Abhängigkeit:** W3, W5 · **Gate:** `plan`

### W7 — Eval-Set und Hill Climbing der Text-Flächen

- **Problem:** Prompt-, Effort- und Kontext-Änderungen sind heute Meinung, nicht Messung.
- **Lösung:** nach Lance Martin mit dem Skill `claude-api` (`build-eval`, `eval-hillclimb`,
  `cost-hillclimb`). **Evals pro Baustein**, nicht über ganze Läufe (ein Lauf ≈ $40, zu langsam,
  zu wenig Fälle):

  | Baustein | Eval | Signal |
  |---|---|---|
  | `final_reviewer` | Fehler aus Abschnitt 3 in alte Commits zurücksetzen (Seeded Bugs) | gefunden ja/nein |
  | Spec-/Plan-Agents und Synthese | Briefs aus `docs/briefs/` + Issues aus dem Ledger, Ja/Nein-Rubrik (jedes AC aufs Issue rückführbar, keine Prozessvorgaben, Deferred vorhanden) | mehrere Modelle bewerten, Wiederholungsläufe |
  | Codex-Review | eingefrorener Diff (F15), wird Finding X gemeldet | ja/nein, Streuung über 2 Läufe |
  | `e2e_triage` / `log_analyst` | gelabelte Fälle aus dem Ledger | Lane und Klasse korrekt |
  | `build_agent` | wenige ganze Läufe, selten | grün ohne Eskalation, Runden, Kosten |

  Hill Climbing nur auf Text-Flächen (Rollen-Prompts in `REGISTRY`, `SCHEMA_INSTRUCTION`, Effort,
  Modell pro Agent), **nicht** auf Orchestrator-Code. Train/Test-Split; der Verbesserer sieht nur
  Train-Fehler. Zu klären mit den Daten: Effort pro Agent; ob die Doppel-Entwürfe Claude+Codex mit
  Fable-Synthese ($139 der $615) besser sind als ein Entwurf; ob `build_agent` auf Fable `low`
  besser/günstiger ist als Opus Default.
- **AC:** Eval-Set mit ≥ 10 Fällen je bewertetem Baustein, Streuung über 3 Wiederholungen
  dokumentiert; ein Hill-Climb-Lauf mit Bericht (Train/Test je Runde).
- **Nicht im Scope:** automatische Übernahme; jede Übernahme ist ein normaler ADW-Lauf.
- **Aufwand:** M · **Abhängigkeit:** W3, W13 · **Gate:** — (manuell begleitet)

### W8 — E2E-Gate auch im Single-Lane-Modus

- **Problem:** F8.
- **Lösung:** `_run_e2e_gate` nach grünen Lane-Gates wiederverwenden, wenn `e2e` konfiguriert ist;
  Fehler gehen als Fix-Task in die eine Lane.
- **AC:** Single-Lane-Lauf mit konfiguriertem E2E führt E2E aus; ohne `e2e` unverändert.
- **Aufwand:** M · **Abhängigkeit:** — · **Gate:** `plan`

### W9 — Expertise über Runs (Agent Experts, TAC 13)

- **Problem:** F9 — Wissen über das **Ziel-Repo** (nicht über ADW; das ist W13–W15).
- **Lösung:** Expertise-Datei pro Ziel-Repo (Ablage laut D4), außerhalb der Retention; wird wie
  `context_files` als Text eingebettet und per Snapshot geschützt. Vorschläge kommen aus den Retros
  mit `cause = target_repo` (W14); Prüfung von Format und Zeilenlimit als Code; Übernahme nach
  Freigabe.
- **Aufwand:** M–L · **Abhängigkeit:** W5, W14, D4 · **Gate:** `both`

### W10 — Arbeitsart als Eingabe (`--kind chore|bug|feature`)

- **Problem:** Ein Spec-Template für alles; TAC 3/7 unterscheiden die Arbeitsart.
- **Lösung:** `--kind` vom Menschen gesetzt; eigenes Spec-Template je Art (Bug: Reproduktion, Root
  Cause); Default-Gates je Art in der Config.
- **Aufwand:** M · **Abhängigkeit:** D1 · **Gate:** `plan`

### W11 — Trigger und Rückmeldung ins Issue

- **Problem:** ADW startet nur von Hand; TAC 4/8 setzen auf Trigger.
- **Lösung (falls D1 es erlaubt):** separates Skript außerhalb des Kerns, pollt ein Issue-Label,
  startet `adw run`, setzt Labels je Exit-Code; Abschluss-Kommentar am Issue, der den Lauf nie
  scheitern lässt.
- **Aufwand:** M · **Abhängigkeit:** D1, W3 · **Gate:** `plan`

### W12 — Semantische Stufe für Duplikate und Klassifikation

- **Problem:** W4 löst nur Formatunterschiede, keine Umformulierungen.
- **Lösung:** zweite Stufe nach W4: lokal Qwen3-Embedding-0.6B (Schwelle) + Qwen3-Reranker-0.6B
  für Grenzfälle; Klassifikation (flaky/infra/code, Lane) zuerst Regeln, dann SetFit mit Beispielen
  aus dem Ledger. Gehostete Alternativen: TypeSafe Jev oder Cloudflare Clef-flash. Fail-open.
- **AC:** Benchmark auf Stefanos Hardware (Latenz, Speicher) vor der Integration; Treffer- und
  Fehlerquote auf Findings aus dem Ledger gemessen.
- **Aufwand:** M · **Abhängigkeit:** W4, W3, D2 · **Gate:** `plan`

### W13 — Lauf-Protokoll und Ledger-Export (deterministisch, 0 Tokens)

- **Problem:** F9, F16. Erfahrungen aus Läufen gehen mit dem Prune verloren und liegen nur lokal.
- **Lösung:**
  - Am Ende jedes Laufs (`done`, `escalated`, Pause am Gate, Abbruch) schreibt der Orchestrator
    `run-record.json` aus `state.json` und `events.jsonl`: Ausgang, Eskalationsgrund,
    Gate-Iterationen pro Lane, Review-Runden mit Findings (normalisierter Schlüssel aus W4,
    Severity, Kategorie, Disposition umgesetzt/abgewiesen/deferred), wiederkehrende Findings,
    Circuit-Breaker-Treffer, CI-Wiederholungen, Approvals/Resumes/Artefakt-Änderungen am Gate,
    Dauer und Verbrauch pro Agent, **ADW-Version, Modell und Effort pro Agent**, Ziel-Remote und
    `base_sha`, Run-ID.
  - Export ins Ledger-Repo (D6/D9): `runs/<repo-slug>/<run_id>/run-record.json` plus ein
    **Fall-Bündel** zum Nachspielen (Issue, Spec, Plan, Contract, Findings, finaler Diff,
    Agent-Prompts aus F15). Export **vor** jedem Prune; Auto-Prune verweigert Läufe ohne Export.
  - Commit ins lokale Ledger-Klon; Push fail-open (kein Netz → beim nächsten Lauf nachholen).
    Ein Fehler beim Export lässt den Lauf nie scheitern, wird aber als Event gemeldet.
- **AC:** Protokoll für Erfolg, Eskalation und Gate-Pause; Zahlen stimmen mit dem Run Inspector
  für ≥ 5 Runs; Prune löscht keinen nicht exportierten Lauf; Ledger-Pfad fehlt → klare Meldung,
  Lauf läuft weiter; Backfill-Kommando für die vorhandenen Runs.
- **Nicht im Scope:** Bewertung durch ein Modell (W14), Auswertung (W3).
- **Aufwand:** M · **Abhängigkeit:** R2 (Log-Integrität), W4 (Schlüssel), D6, D9 · **Gate:** `plan`

### W14 — Retro-Agent und menschliches Feedback

- **Problem:** Das Protokoll zählt, *was* passiert ist, nicht *warum* und was besser gewesen wäre.
- **Lösung:**
  - Neuer Agent `retro_agent` (nur lesend, z. B. Opus 5.5 Effort `low`), läuft nach W13 (laut D8).
    Liest `run-record.json` und das Event-Log und gibt strukturiertes JSON aus, pro Problem:

    | Feld | Inhalt |
    |---|---|
    | `problem` | was schiefging (rotes Gate, wiederkehrendes Finding, Eskalation, Eingriff) |
    | `resolution` | wie es gelöst wurde, nach wie vielen Runden |
    | `better` | wie es besser gegangen wäre |
    | `cause` | `prompt` · `context_missing` · `gate` · `orchestrator` · `issue_quality` · `model` · `target_repo` |
    | `lever` | vorgeschlagener Hebel (Prompt-Stelle, Config, Code, Expertise) |
    | `evidence` | Event-Seq bzw. Dateien; ohne Beleg wird der Eintrag verworfen (Code-Prüfung) |

    Dazu Ja/Nein-Felder statt Skalen („Musste der Mensch eingreifen?", „Kam ein Finding > 2 Runden
    wieder?"). Ergebnis `retro.json` + kurze `retro.md` ins Ledger. Fail-open.
  - `adw rate <run> good|bad [--note …]` und `adw feedback <run> --finding <key> valid|noise` —
    das stärkste Signal, schreibt ins Ledger.
- **AC:** Schema strikt validiert; Eintrag ohne `evidence` wird verworfen; Retro-Fehler ändert
  Exit-Code und Phase des Laufs nicht; `rate`/`feedback` erscheinen in W3.
- **Aufwand:** S–M · **Abhängigkeit:** W13, D8 · **Gate:** `plan`

### W15 — Wöchentliche Verbesserungsschleife (`adw improve`)

- **Problem:** Ohne regelmäßige Auswertung bleiben die Daten liegen.
- **Lösung:** `adw improve --since 7d`, geplant per systemd-User-Timer (lokal, weil das Ledger
  lokal geklont ist; eine Cloud-Routine sähe es nicht). Liefert laut D7 **Bericht mit Brief**:
  1. Kennzahlen mit Trend pro ADW-Version (W3).
  2. Probleme aus den Retros nach normalisiertem Schlüssel und `cause` gruppieren, sortiert nach
     Häufigkeit × Kosten, jeweils mit Belegen.
  3. Pro Top-Gruppe der passende Hebel:
     - Text-Flächen (Prompt, Effort, Modell) → Kandidat fürs Hill Climbing (W7);
     - Orchestrator-Code → **Brief** nach `docs/briefs/README.md`, den du als ADW-Lauf auf dem
       ADW-Repo startest;
     - Wissen über das Ziel-Repo → Vorschlag für die Expertise-Datei (W9).
  4. Schlechte Läufe werden als Kandidaten für Regressionsfälle im Eval-Set vorgeschlagen.
  Bericht und Brief-Entwurf landen im Ledger-Repo (`reports/YYYY-Www.md`); Benachrichtigung an dich.
- **Leitplanken:** kein Auto-Merge, kein automatischer ADW-Lauf (D7); Token-Budget pro Woche;
  bei < 3 neuen Läufen nur Kennzahlen, keine Vorschläge; Fälle aus echten Läufen, nicht nur aus den
  Fehlern eines Modells (Lance Martin: „gezackte" Modellstärken).
- **AC:** Bericht für eine Fixture-Woche enthält Kennzahlen, ≥ 1 Gruppe mit Belegen und einen
  Brief-Entwurf im Vorlagenformat; ohne neue Läufe → kurzer Bericht ohne Brief.
- **Aufwand:** M · **Abhängigkeit:** W3, W13, W14 · **Gate:** `plan`

### Kleinere Punkte (einzeln oder als Beifang in passendem Lauf)

- Claude-Reviewer (`final_reviewer`, `e2e_triage`, `log_analyst`) mit erzwungenem Antwortformat
  (SDK `output_format`), Schema analog `REVIEW_OUTPUT_SCHEMA` — Format der installierten SDK-Version
  vorher prüfen.
- Commit des Build-Agenten schon beim Versuch blocken (Deny-Regel für `git commit`), zusätzlich zu
  `_require_no_agent_commit`.
- Reviewer-Streuung messen: denselben Diff zweimal reviewen lassen, Übereinstimmung der Severity zählen.
- `RunState.save`-Docstring verweist auf eine nicht existierende `update()`-Methode.

---

## 6. Plan — Reihenfolge der Läufe

Ein Lauf = ein Brief = ein Release. Nacheinander, nicht parallel (Clean-Tree-Preflight, gemeinsame
Dateien wie `adw/agents.py`, `adw/lanes.py`). Vor jedem Lauf: Prämissen neu messen, Brief nach
`docs/briefs/README.md`.

| Schritt | Paket | Art | Gates | Warum diese Position |
|---|---|---|---|---|
| 1 | R1 Sicherheit (C1–C4) | ADW-Lauf | `plan` | jeder weitere Lauf — auch ADW an sich selbst — nutzt die fehlerhaften Pfade |
| 2 | R2 Resume-/Log-Integrität (C6–C12) | ADW-Lauf | `none` | Voraussetzung für das Ledger (W13) |
| 3 | W0 Prompt-Audit + F13 + F11 | manuell, Übernahme als Lauf | `none` | billig; F13 ist ein belegter Widerspruch |
| 4 | W1 Agent-Grenzen (+ C26) | ADW-Lauf | `none` | klar umrissen; schützt alle folgenden Läufe |
| 5 | W4 Normalisierte Schlüssel (+ C25) | ADW-Lauf | `none` | Schlüssel für Ledger und Breaker |
| 6 | W13 Lauf-Protokoll + Ledger-Export | ADW-Lauf | `plan` | ab hier sammeln sich Daten; braucht D9 und das GitLab-Repo |
| 7 | W3 `adw kpis` | ADW-Lauf | `none` | Messbasis für alles Weitere |
| 8 | W2 Effort | ADW-Lauf | `none` | nach D5; Wirkung über W3 sichtbar |
| 9 | W14 Retro-Agent + `rate`/`feedback` | ADW-Lauf | `plan` | nach D8 |
| 10 | R3 GUI, R4 CI/Forge, R5 Robustheit | je ein ADW-Lauf | `none` | unabhängig voneinander und vom Rest; **R3 jederzeit vorziehbar**, da die Live-Ansicht nach Approve heute nicht funktioniert |
| 11 | W15 Wochenschleife | ADW-Lauf | `plan` | sinnvoll, sobald 3–4 Wochen Ledger-Daten vorliegen |
| 12 | W7 Eval-Set + Hill Climbing | manuell mit Skill | — | braucht W3/W13; entscheidet Effort/Prompts mit Daten |
| 13 | W5, W6, W8 | ADW-Läufe | `plan` | W6 braucht W3 und W5 |
| 14+ | W9, W10, W11, W12 | ADW-Läufe | siehe Paket | nach D1, D2, D4 |

`--gates none` hat in der GUI-Redesign-Reihe fünfmal ohne Eskalation getragen — Voraussetzung war
jeweils ein Brief mit entschiedenen Vorentscheidungen. Für Pakete mit offenen Designfragen deshalb `plan`.

**Warum nicht ein großer Lauf:** Die Review-Loop-Policy (max. 5 Runden, absteigende Schwelle)
konvergiert nur bei überschaubarem Diff; ein Lauf über viele Pakete vergrößert den Review-Scope
jeder Runde, mischt Gate-Stufen (`plan` vs. `none`) und macht eine Eskalation teuer, weil alles
zusammen hängen bleibt. Kleine Läufe verkleinern zudem den Review-Scope aller Folgeläufe.

**Pro Lauf (Definition of Done):** `uv run ruff check .` + `uv run pytest -q` grün, CHANGELOG EN+DE,
Release nach `CLAUDE.md`, W3-Kennzahlen des Laufs notiert (ab Schritt 7).

---

## 7. Querschnittsregel: Prompt-Cache

Der Prompt-Cache greift nur bei exakt gleichem Präfix. Gemessen (F14) liegt die Trefferquote heute
bei 80–96 %; das soll so bleiben. Für jeden Auftrag an einen Agenten gilt ab W5:

1. System-Preset und Rollen-Text (stabil über alle Läufe)
2. `context_files` / Expertise (stabil pro Ziel-Repo)
3. Spec, Plan, Contract (stabil pro Lauf)
4. Variables zuletzt: Gate-Fehler, Findings, gefilterter Kontext

Ein pro Auftrag anders gefilterter Kontext (z. B. Relevanzfilter aus W12) gehört in Block 4, sonst
ist jeder Auftrag ein Cache-Miss.

---

## 8. Was bewusst so bleibt

- „Control flow is code, not prompt" — kein LLM-Orchestrator (TAC 12 O-Agent), kein LLM-Router.
- Trennung Orchestrator / Lane-Sandbox / anhängendes Event-Log (entspricht „Gehirn / Hände /
  Session-Log" aus dem Vortrag); R2 macht das Log belastbar, statt die Architektur zu ändern.
- Deterministische Gates als Beweis. Prompts sollen keine Selbstprüfung verlangen, die der
  Orchestrator ohnehin deterministisch führt — **heute noch verletzt durch F13**, behoben in W0.
- Rundenlimits, Circuit Breaker, absteigende Severity-Schwelle — Hill Climbing konvergiert laut
  Vortrag ebenfalls nicht garantiert.
- Isolation der Agenten (F1) — `context_files` liefert Wissen, nicht Konfiguration.
- Keine Selbständerung von ADW ohne Gate: Verbesserungen aus W15 sind normale ADW-Läufe mit Brief.
- ZTE (Auto-Merge in Produktion) bleibt Non-Goal.

---

## 9. Unsicherheiten

- Viele „fehlt"-Aussagen der Lektionsanalyse beruhen auf grep ohne Treffer, nicht auf
  Laufzeittests. F1–F16 und die mit ✔ markierten Review-Befunde sind direkt im Code nachgesehen;
  die übrigen Review-Befunde stammen aus den Reviews (Codex mit eigenen Repros in Temp-Repos) und
  werden im jeweiligen Brief erneut gemessen.
- Prioritäten: Codex stuft C3 und C4 als P1 ein; C4 setzt eine präparierte Umgebung voraus, hier P2.
- Ob `setting_sources=[]` die `CLAUDE.md` wirklich nie erreichbar macht: laut SDK-Doku ja; Agenten
  können sie aber selbst per `Read` öffnen. Nicht live getestet.
- Effort-Default im SDK und dessen Wirkung auf das Plan-Kontingent: nicht gemessen.
- F14 beruht auf 15 Läufen (ältere sind gelöscht); `cost_usd` ist bei Abo-Login ein Rechenwert.
- Datenmenge: bei wenigen Läufen pro Woche ist Hill Climbing über ganze Läufe statistisch schwach —
  deshalb Baustein-Evals (W7).
- Jev/Clef: Latenz- und Genauigkeitszahlen stammen vom Hersteller; lokale Hardware-Tauglichkeit unbelegt.

---

## 10. Quellen

**Code-Review 2026-11-08:** drei Opus-5.5-Reviews (Kern: `phases`, `lanes`, `agents`, `codex`,
`review_policy`, `findings`, `gates`, `ci`; CLI/State/Infra; GUI + `scripts/`) und
`codex review` (`gpt-6-astra`, Repo-weit). Alle Befunde stehen vollständig in Abschnitt 3.

**TAC-Lektions-Docs (Lernunterlage + ADW-Abgleich):**
01 https://claude.ai/artifact/5xm9ghQCyNxi6AxgHzdMxv ·
02 https://claude.ai/artifact/GsyrmeX7ozU4uJt6UtFDYM ·
03 https://claude.ai/artifact/81jR2CmeXUh66y3vJhai7j ·
04 https://claude.ai/artifact/RzqeCxA18KWWjZQnVhpJk1 ·
05 https://claude.ai/artifact/7MYYmFv5Dzvxyx14mFya2T ·
06 https://claude.ai/artifact/RgUYcoADSJxnptKzrNEpeq ·
07 https://claude.ai/artifact/1YXFx6hzmDZ9paks8fUd6a ·
08 https://claude.ai/artifact/Ad3UGohbFMaj7MffJWWQ6A ·
09 https://claude.ai/artifact/U6gKB8onq7uYGjcJQ6F8KV ·
10 https://claude.ai/artifact/WxSbHhBjS5GKR8wJJdJEQd ·
11 https://claude.ai/artifact/XoJG7hwKSiJvgeXecPwFg2 ·
12 https://claude.ai/artifact/KJcVvZ6X35CiMCFZLxNUwg ·
13 https://claude.ai/artifact/4TsGMr1nLGsvmVWYZe6tGx ·
14 https://claude.ai/artifact/6mArDqNipwziV8LPtpNVuL

**Kurs-Repos und Transkripte:** `~/PycharmProjects/tac/` (tac-1 … tac-8, elite-context-engineering,
`docs/vtt-transcripts/`)

**Jev und Alternativen:** docs.typesafe.ai/models · typesafe.ai/blog/introducing-system-one-models-and-jev ·
huggingface.co/Cloudflare/clef-flash · huggingface.co/Qwen/Qwen3-Embedding-0.6B ·
huggingface.co/Qwen/Qwen3-Reranker-0.6B · huggingface.co/docs/setfit ·
akitaonrails.com/en/2026/10/03/understanding-the-jev-hype-what-is-it-for/ (Kritik, Autor ist Konkurrent)

**Vortrag:** Lance Martin (Anthropic), *Your Old Prompts Are Making Claude Worse*, Transkript und
Zusammenfassung vom 2026-11-07. Skill `claude-api` (gebündelt mit Claude Code): `shared/prompt-audit.md`,
`shared/evals/build-eval.md`, `eval-hillclimb.md`, `cost-hillclimb.md`, `shared/prompt-caching.md`.
