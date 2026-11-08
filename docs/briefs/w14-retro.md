# W14: Jeder Lauf bekommt einen Rückblick — und du ein Urteil

Paket W14 aus `ONBOARDING-SPEC-PLAN.md` (Entscheidung D8: Retro-Agent bei **jedem**
Lauf). Setzt auf dem gemergten Stand **0.34.0** auf (Ledger aus W13, `adw kpis` aus
W3). Die Ausgangslage ist am 2026-11-08 im Code geprüft und vor dem Bauen gegen den
dann aktuellen Stand zu prüfen.

Das Lauf-Protokoll (`run-record.json`) zählt, **was** passiert ist. Es sagt nicht,
**warum** etwas schiefging und was besser gewesen wäre. Dieser Lauf ergänzt zwei
Signale: ein Urteil durch einen nur lesenden Agenten am Ende jedes Laufs, und das
Urteil des Menschen per Kommando. Beide landen im Ledger und in `adw kpis`. Die
Wochenschleife (W15) ist **nicht** Teil dieses Laufs.

## Ausgangslage (im Code verifiziert, 2026-11-08, Stand 0.34.0)

### Wo ein Lauf endet

`run`, `resume` und `approve` (`adw/cli.py`) führen den Lauf innerhalb von
`with _run_span(emitter, state, config, repo, run_totals) as run_handle:` aus und rufen
dort `_execute(ctx)` (`adw/cli.py:329`, `:388`, `:475`; Definition `:914`). `_execute`
beendet Eskalation, Gate-Pause und `AgentRunError` mit `typer.Exit` (1/2). `_run_span`
schreibt das `run`-End-Event mit `status` und `totals` (Summe der Agentenkosten über
`RunTotals`). **Danach**, außerhalb der Spanne, exportiert `_export_after_execution`
(`adw/cli.py:184`) den Lauf ins Ledger, dann folgt der Auto-Prune.

### Wie ein lesender Agent heute arbeitet

`final_reviewer` (`adw/agents.py:346`) ist das Muster: `tools=READ_ONLY_TOOLS`,
`allowed_tools=SCOPED_READ_RULES` (workspace-relativ, `Read(./**)` usw.),
`permission_mode="default"`, `max_turns`/`timeout_s` gesetzt (W1), Antwort **nur**
als JSON, das der Orchestrator streng validiert. Agenten schreiben nie nach
`.adw/runs/**` (Deny-Regel in `SECRET_STORE_DENY_RULES`). Aufruf mit Span über
`context._agent_run(ctx, agent, task, cwd, …)` (`adw/context.py:66`).

### Ledger

`adw/ledger.py`: `build_run_record`, `export_run`. Das Fall-Bündel enthält die Dateien
aus `_ARTIFACTS` (`adw/ledger.py:38–48`). **Ein erneuter Export löscht den
Lauf-Ordner im Ledger vollständig und schreibt ihn neu** (`shutil.rmtree(dest)`,
`adw/ledger.py:419`). Eine Datei, die nur im Ledger existiert (z. B. menschliches
Feedback), ginge damit beim nächsten Export verloren.

`adw kpis` (`adw/cli.py:777`, `compute_kpis` in `adw/ledger.py:607`) liest alle
`run-record.json` und gruppiert nach ADW-Version.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Kontrollfluss, Phasen, Gates, Review-Loops und Exit-Codes bleiben unverändert. Die
  Retro ist ein Anhängsel am Ende, kein Teil der Entscheidung über `done`/`escalated`.
- Schema von `run-record.json` (Version 1) bleibt; die Retro ist eine eigene Datei.
- Export-Mechanik (Commit, Push fail-open, Idempotenz) bleibt, nur die
  Feedback-Datei wird beim Neuschreiben erhalten (E8).
- Die GUI bleibt unverändert.

## Aufgabe

**A1 — Retro-Agent.** Neuer Agent `retro_agent` in `REGISTRY`, nur lesend. Er läuft
am Ende jedes Laufs, der in `done` oder `escalated` endet, und liefert strukturiertes
JSON (E3). Der Orchestrator validiert es, verwirft Einträge ohne gültigen Beleg (E4)
und schreibt `retro.json` und `retro.md` in den Run-Ordner.

**A2 — Export.** `retro.json` und `retro.md` gehören zum Fall-Bündel.

**A3 — Menschliches Urteil.** `adw rate` und `adw feedback` schreiben ins Ledger (E7);
das Feedback überlebt einen erneuten Export (E8).

**A4 — `adw kpis`.** Gesamtzeile und JSON zeigen die Häufigkeit der Retro-Ursachen
(`cause`) und die Bewertungen `good`/`bad` (E9).

**A5 — Doku und Changelog.** Handbuch EN+DE, Abschnitt „Ledger": Retro, `adw rate`,
`adw feedback`. `CHANGELOG.md`/`CHANGELOG.de.md` synchron: neue Sektion
`## [0.35.0] — Unreleased` mit `### Added`. Version in `pyproject.toml` **nicht**
ändern.

## Vorentscheidungen (bindend — diese Fragen sind entschieden, kein Finding, auch nicht im Review-Loop)

- **E1 — Agent:** `retro_agent` mit `model=OPUS`, `max_turns=60`, `timeout_s=900`,
  `tools=READ_ONLY_TOOLS`, `allowed_tools=SCOPED_READ_RULES`,
  `permission_mode="default"`. `cwd` ist der **Run-Ordner** (`.adw/runs/<id>/`), also
  liest er `events.jsonl`, `state.json`, die Artefakte und `escalation.md`/
  `followups.md` dort. Kurzer Rollentext im Stil der anderen: Er bewertet einen
  abgeschlossenen ADW-Lauf, ändert nichts, antwortet nur mit JSON nach Schema, jeder
  Eintrag braucht Belege.
- **E2 — Wann:** In `run`, `resume` und `approve` **innerhalb** der `run`-Spanne,
  nach `_execute` in einem `try/finally` (auch wenn `_execute` mit `typer.Exit`
  endet). Die Retro läuft nur, wenn die Phase danach `done` oder `escalated` ist,
  kein Dry-Run vorliegt und noch kein `retro.json` existiert. Bei Gate-Pause und
  `AgentRunError` läuft sie **nicht** (der Lauf ist nicht fertig). Ihre Kosten gehen
  über den normalen Agentenpfad in `RunTotals` und damit in die `run`-`totals`. Die
  Retro ändert **nie** Exit-Code oder Phase: jede Ausnahme (auch `AgentRunError`,
  ungültiges JSON) wird gefangen, auf stderr gemeldet und als Event
  `retro` mit `outcome: "failed"` und Grund festgehalten. Erfolg: Event `retro` mit
  `outcome: "written"` und Anzahl Einträge.
- **E3 — Auftrag und Schema:** Der Auftrag enthält das aktuelle
  `build_run_record(repo, run_id)` als JSON und den Hinweis, Details per Grep in
  `events.jsonl` zu suchen. Antwort-Schema (pydantic, `extra="forbid"`):
  `{"summary": str, "items": [{"problem": str, "resolution": str, "better": str,
  "cause": "prompt"|"context_missing"|"gate"|"orchestrator"|"issue_quality"|"model"|"target_repo",
  "lever": str, "evidence": [str, …]}]}`, höchstens 10 Einträge. Ein glatter Lauf
  darf `items: []` liefern. Geparst wird wie bei den Reviewern (JSON aus dem
  Antworttext, strikt); kein neuer Parser-Stil.
- **E4 — Belege als Code-Prüfung:** Ein Beleg ist entweder `seq:<n>` (es gibt im
  Event-Log einen Record mit dieser `seq`) oder ein relativer Dateipfad, der im
  Run-Ordner existiert (kein `..`, keine absoluten Pfade). Ein Eintrag, dessen Belege
  **alle** ungültig sind oder der keine hat, wird verworfen; die Zahl verworfener
  Einträge steht in `retro.json` (`dropped`).
- **E5 — `retro.json`:** `{"schema_version": 1, "run_id", "phase", "summary",
  "items": [...], "dropped": n, "model"}`, sortierte Schlüssel, Einrückung 2.
  `retro.md` wird **vom Code** aus `retro.json` gerendert (Überschrift mit Run-ID und
  Phase, Zusammenfassung, je Eintrag ein Abschnitt mit den Feldern) — kein zweiter
  Agententext.
- **E6 — Bündel:** `retro.json` und `retro.md` kommen in `_ARTIFACTS`; sonst ändert
  sich am Export nichts.
- **E7 — Kommandos:**
  - `adw rate <RUN_ID> good|bad [--note TEXT] [--repo PFAD]`
  - `adw feedback <RUN_ID> --finding <PRÄFIX> valid|noise [--note TEXT] [--repo PFAD]`
  Beide schreiben je **eine JSON-Zeile** an `feedback.jsonl` im Ledger-Ordner des
  Laufs (`runs/<slug>/<run_id>/`): `{"ts", "kind": "rate"|"finding", "value", "note",
  "finding_key"}` (`finding_key` nur bei `feedback`). Der Ordner muss im Ledger
  existieren (der Lauf wurde exportiert), sonst Exit 1 mit Meldung. `--finding`
  ist ein Präfix, der **genau einen** `finding_key` aus dem `run-record.json` dieses
  Laufs treffen muss; kein oder mehrere Treffer → Exit 1 mit Liste der Kandidaten
  (gekürzt). Danach Commit `feedback <run_id> <kind>` und Push wie beim Export
  (fail-open). Läuft unter dem Repo-Lock aus R1, wie die anderen Kommandos.
- **E8 — Feedback überlebt den Export:** `_export` sichert vor dem Löschen des
  Lauf-Ordners eine vorhandene `feedback.jsonl` und schreibt sie danach unverändert
  zurück. Sonst keine Änderung an der Export-Logik.
- **E9 — `adw kpis`:** `compute_kpis` liest zusätzlich, falls vorhanden,
  `retro.json` und `feedback.jsonl` jedes Laufs. Neu im `total`-Objekt und in der
  Textausgabe unter der Gesamtzeile: `retro_causes` (Anzahl je `cause` über alle
  Retro-Einträge), `retro_runs` (Läufe mit Retro), `ratings` (`{"good": n, "bad": n}`,
  maßgeblich ist je Lauf die **letzte** `rate`-Zeile), `finding_feedback`
  (`{"valid": n, "noise": n}`). Pro Version keine neuen Spalten.
- **E10:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`. Tests
  benutzen den Mock-Runner bzw. einen gefakten Runner und ein temporäres Ledger
  (`ADW_LEDGER`); kein Test ruft ein echtes Modell oder berührt `~/.adw/ledger`.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Wochenschleife `adw improve` (W15), Gruppierung/Clustering der Retros.
- Retro bei Gate-Pause, Retro nachträglich für alte Läufe, Retro neu erzeugen.
- Effort für den Retro-Agenten (W2).
- Feedback in der GUI, Feedback ohne Ledger.
- Neue Spalten pro Version in `adw kpis`.

## Akzeptanzkriterien

1. Ein Lauf, der mit einem Mock-/Fake-Runner bis `done` läuft, ruft den
   `retro_agent` genau einmal mit `cwd` = Run-Ordner auf und schreibt `retro.json`
   und `retro.md`; das `agent.run`-Span der Retro liegt in der `run`-Spanne.
2. Ein eskalierter Lauf bekommt ebenfalls eine Retro; ein an einem Gate pausierter
   Lauf und ein Lauf mit `AgentRunError` bekommen **keine**.
3. Exit-Code und Phase sind identisch mit und ohne Retro — auch wenn die Retro
   `AgentRunError` wirft oder ungültiges JSON liefert (dann `retro`-Event mit
   `outcome: "failed"`, kein `retro.json`).
4. Einträge mit `seq:` auf eine existierende bzw. nicht existierende Seq und mit
   gültigem bzw. ungültigem Dateipfad werden nach E4 behalten bzw. verworfen;
   `dropped` stimmt.
5. Dry-Run → keine Retro.
6. Export enthält `retro.json` und `retro.md`.
7. `adw rate <id> bad --note x` hängt eine Zeile an `feedback.jsonl` an, committet;
   ein anschließender Export des Laufs lässt `feedback.jsonl` byte-gleich stehen.
8. `adw feedback` mit eindeutigem Präfix schreibt den vollen `finding_key`; mit
   mehrdeutigem oder unbekanntem Präfix Exit 1, nichts geschrieben.
9. `adw rate` für einen nicht exportierten Lauf → Exit 1, nichts geschrieben.
10. `adw kpis --json` enthält `retro_causes`, `retro_runs`, `ratings` (letzte
    Bewertung zählt) und `finding_feedback` mit den Werten eines Fixture-Ledgers.
11. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 12–16 neue Tests. Deutlich mehr ist ein Zeichen für
Gold-Plating.

**Contract:** Single-Lane — Contract nur für extern beobachtbare Flächen (CLI
`adw rate`/`adw feedback`, Dateien `retro.json`/`retro.md`/`feedback.jsonl` mit
Schema, `retro`-Event, neue Felder in `adw kpis --json`), keine internen
Helper-Signaturen.
