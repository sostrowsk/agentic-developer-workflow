# Spec — W14: Jeder Lauf bekommt einen Rückblick — und du ein Urteil

Basis: Stand 0.34.0 (`main`, e4e7522), am 2026-11-08 gegen den Code geprüft. Vor dem
Bauen ist die Ausgangslage gegen den dann aktuellen Stand erneut zu prüfen.

- `run`, `resume` und `approve` (`adw/cli.py`) führen `_execute(ctx)` innerhalb von
  `with _run_span(...)` aus. `_run_span` (`adw/cli.py:148`) schreibt das `run`-End-Event
  mit `status` aus dem Exit-Code (`_EXIT_STATUS`: 0 → `done`, 1 → `escalated`,
  2 → `awaiting_approval`) und `totals` aus `RunTotals`. Danach, außerhalb der Spanne,
  läuft `_export_after_execution` (`adw/cli.py:185`), dann der Auto-Prune.
- **Exit 1 ist mehrdeutig:** Eskalation und `AgentRunError` enden beide mit Exit 1 und
  damit mit `status: "escalated"` im `run`-End-Event. Nur die Phase im `state.json`
  unterscheidet sie. Ob eine Retro läuft, entscheidet deshalb die **Phase**, nicht der
  Exit-Code oder der Event-Status.
- `READ_ONLY_TOOLS = ("Read", "Grep", "Glob")`, `SCOPED_READ_RULES` workspace-relativ
  (`adw/agents.py:28`, `:34`); `final_reviewer` (`:347`) ist das Muster eines lesenden
  JSON-Agenten. Aufruf über `context._agent_run(ctx, agent, task, cwd, …)`
  (`adw/context.py:66`) mit `agent.run`-Span.
- `adw/ledger.py`: `_ARTIFACTS` (`:38`) ohne Retro-Dateien; `_export` (`:402`) löscht
  einen vorhandenen Lauf-Ordner im Ledger per `shutil.rmtree(dest)` (`:419`) und
  schreibt ihn neu. Ledger-Ordner ist `runs/<slug>/<run_id>/` mit `slug` aus
  `_repo_slug(repo)` (`/` → `__`). Commit-Nachricht des Exports
  `run <id> <phase> (<slug>)`, Push über `_push` (fail-open).
- `compute_kpis(records, since=None, repo_slug=None)` (`adw/ledger.py:607`) liefert
  `groups`, `total`, `candidates`, `skipped` und arbeitet nur auf `run-record.json`.
- Handbuch: `docs/ADW-HANDBOOK.md` / `.de.md` haben eine CLI-Referenz (§7) und einen
  Abschnitt „Ledger" (§11.1).

## Ziel

Das Lauf-Protokoll (`run-record.json`) sagt heute nur, **was** in einem Lauf passiert
ist, nicht **warum** etwas schiefging und was besser gewesen wäre. Ohne diese
Information lassen sich Prompt-, Kontext- oder Gate-Schwächen nicht über Läufe hinweg
erkennen, und das Urteil des Menschen über einen Lauf oder ein Finding geht verloren.

Zwei neue Signale, beide im Ledger und in `adw kpis` sichtbar:

1. ein Rückblick (Retro) eines nur lesenden Agenten am Ende jedes fertigen Laufs
   (`done`/`escalated`), mit im Code geprüften Belegen;
2. das Urteil des Menschen per `adw rate` (Lauf) und `adw feedback` (Finding).

Die Retro beeinflusst weder Laufentscheidungen noch Phase oder Exit-Code.

## Scope

### 1. Retro-Agent (A1, E1)

Neuer Eintrag `retro_agent` in `REGISTRY` (`adw/agents.py`): `model=OPUS`,
`max_turns=60`, `timeout_s=900`, `tools=READ_ONLY_TOOLS`,
`allowed_tools=SCOPED_READ_RULES`, `permission_mode="default"`. Kein Effort-Setting.

Kurzer Rollentext im Stil der übrigen Agenten: bewertet einen abgeschlossenen ADW-Lauf,
ändert nichts, antwortet nur mit JSON nach Schema, jeder Eintrag braucht Belege.

`cwd` ist der Run-Ordner `.adw/runs/<run_id>/`; dort liest er `events.jsonl`,
`state.json`, die Artefakte sowie `escalation.md`/`followups.md`. Die Retro-Dateien
schreibt ausschließlich der Orchestrator (Deny-Regel für `.adw/runs/**` bleibt).

### 2. Auslösung (E2)

- In `run`, `resume` und `approve` **innerhalb** der `run`-Spanne, nach `_execute` in
  einem `try/finally` — also auch, wenn `_execute` mit `typer.Exit` endet
  (Eskalation).
- Die Retro läuft genau dann, wenn nach `_execute` gilt: Phase im State ist `done`
  oder `escalated`, kein Dry-Run, im Run-Ordner existiert noch kein `retro.json`.
- Kein Aufruf bei Gate-Pause (Exit 2), nach `AgentRunError` (Exit 1, Phase bleibt
  fortsetzbar), im Dry-Run und bei vorhandenem `retro.json`. Ein erneuter CLI-Aufruf
  für einen bereits fertigen Lauf erzeugt keine nachträgliche oder zweite Retro.
- Aufruf über den normalen Agentenpfad (`_agent_run`); dadurch entsteht ein
  `agent.run`-Span innerhalb der `run`-Spanne, und Kosten/Tokens fließen über
  `RunTotals` in die `totals` des `run`-End-Events.
- Die Retro ändert **nie** Exit-Code, Phase oder den `status` des `run`-End-Events.
  Jede Ausnahme (inkl. `AgentRunError`, kein parsebares JSON, Schemaverletzung) wird
  gefangen, auf stderr gemeldet und als Punkt-Event `retro` mit
  `{"outcome": "failed", "reason": <text>}` festgehalten; dann wird weder `retro.json`
  noch `retro.md` geschrieben. Ein anstehender `typer.Exit` aus `_execute` propagiert
  unverändert. Kein zusätzlicher Logging-Kanal, kein persistenter Fehlerzustand.
- Erfolg: Punkt-Event `retro` mit `{"outcome": "written", "items": <Anzahl behaltener
  Einträge>}`.
- Reihenfolge: Retro → Ende der `run`-Spanne → Export. Der Export bleibt unverändert
  und sieht die Retro-Dateien.

### 3. Auftrag und Antwort-Schema (E3)

- Der Auftrag enthält das zum Aufrufzeitpunkt aktuelle `build_run_record(repo, run_id)`
  als JSON und den Hinweis, Details per Grep in `events.jsonl` des Run-Ordners zu
  suchen.
- Antwort: JSON, geparst wie bei den bestehenden Reviewern (JSON aus dem Antworttext,
  strikt; kein neuer Parser-Stil), validiert mit pydantic, `extra="forbid"` auf
  Antwort- und Eintragsebene:

  ```json
  {"summary": "str",
   "items": [{"problem": "str", "resolution": "str", "better": "str",
              "cause": "prompt|context_missing|gate|orchestrator|issue_quality|model|target_repo",
              "lever": "str", "evidence": ["seq:42", "escalation.md"]}]}
  ```

- Höchstens 10 Einträge; `items: []` ist gültig (glatter Lauf). Mehr als 10 Einträge,
  unbekannte Schlüssel, unbekannte `cause` oder kein parsebares JSON → Fehlschlag
  nach §2 (`outcome: "failed"`). Fehlende oder ungültige Belege eines ansonsten
  schemakonformen Eintrags sind **kein** Schemafehler, sondern Fall für §4.

### 4. Beleg-Prüfung (E4)

Ein Beleg ist gültig, wenn er

- die Form `seq:<n>` hat und `events.jsonl` des Laufs einen Record mit `seq == n`
  enthält, **oder**
- ein relativer Pfad ist (kein absoluter Pfad, keine `..`-Komponente), der im
  Run-Ordner als Datei existiert.

Ein Eintrag mit mindestens einem gültigen Beleg bleibt erhalten. Ein Eintrag ohne
Belege oder mit ausschließlich ungültigen Belegen wird vollständig verworfen. Die Zahl
verworfener Einträge steht in `retro.json` als `dropped`. Werden alle Einträge
verworfen, bleibt es eine erfolgreiche Retro mit `items: []` (Event `written`).

### 5. `retro.json` und `retro.md` (E5)

- `retro.json` im Run-Ordner:
  `{"schema_version": 1, "run_id", "phase", "summary", "items": [...], "dropped": n,
  "model"}` — `phase` ist die Endphase `done` oder `escalated`, `items` die behaltenen
  Einträge im Antwortschema, `dropped` deren verworfene Gegenmenge, `model` das Modell
  des `retro_agent`. Sortierte Schlüssel, Einrückung 2.
- `retro.md` wird vom Code deterministisch aus `retro.json` gerendert: Überschrift mit
  Run-ID und Phase, Zusammenfassung, je Eintrag ein Abschnitt mit allen Feldern
  (`problem`, `resolution`, `better`, `cause`, `lever`, `evidence`). Kein zweiter
  Agententext.

### 6. Export (A2, E6, E8)

- `retro.json` und `retro.md` werden in `_ARTIFACTS` aufgenommen und landen damit,
  sofern vorhanden, im Fall-Bündel.
- `_export` sichert vor dem Löschen des Ledger-Lauf-Ordners eine vorhandene
  `feedback.jsonl` und schreibt sie nach dem Neuschreiben des Bündels byte-gleich
  zurück. Sie braucht keine Kopie im lokalen Run-Ordner. Sonst keine Änderung an der
  Export-Logik (Commit, Push fail-open, Idempotenz, Dry-Run-Skip).

### 7. Kommandos `adw rate` und `adw feedback` (A3, E7)

```
adw rate <RUN_ID> good|bad [--note TEXT] [--repo PFAD]
adw feedback <RUN_ID> --finding <PRÄFIX> valid|noise [--note TEXT] [--repo PFAD]
```

- `--repo` Default: aktuelles Verzeichnis. Ledger-Auflösung wie beim Export
  (`ADW_LEDGER`, Default `~/.adw/ledger`); Ziel ist `runs/<slug>/<run_id>/` mit dem
  Slug des Repos wie beim Export.
- Kein Ledger oder Lauf-Ordner im Ledger nicht vorhanden (Lauf nicht exportiert) →
  Meldung auf stderr, Exit 1, nichts geschrieben, kein Ordner angelegt, kein Commit.
- Ungültiger Wert (`rate`: nicht `good`/`bad`; `feedback`: nicht `valid`/`noise`) →
  Exit ≠ 0, nichts geschrieben.
- `adw feedback`: `--finding` ist ein Präfix, der **genau einen** `finding_key` aus
  `findings[]` des `run-record.json` im Ledger-Lauf-Ordner treffen muss. Kein oder
  mehrere Treffer → Exit 1, Meldung mit gekürzter Kandidatenliste, nichts geschrieben.
  Bei eindeutigem Treffer wird der **volle** `finding_key` gespeichert.
- Jeder erfolgreiche Aufruf hängt genau eine JSON-Zeile an `feedback.jsonl` im
  Ledger-Lauf-Ordner an (Datei wird bei Bedarf angelegt, bestehende Zeilen bleiben):
  `{"ts": <ISO-8601, UTC>, "kind": "rate"|"finding", "value": <Wert>,
  "note": <Text oder null>, "finding_key": <voller Key>}` — `finding_key` nur bei
  `kind: "finding"`.
- Danach Ledger-Commit mit Nachricht `feedback <run_id> <kind>` (`kind` = `rate` bzw.
  `finding`) und Push wie beim Export (fail-open: ein fehlgeschlagener Push ändert
  weder Exit-Code noch das lokal committete Feedback). Exit 0. Dieser Commit ist
  Verhalten des Produkts gegenüber dem Ledger, nicht Workflow-Prozess.
- Beide Kommandos laufen unter dem Repo-Lock (`_repo_lock(repo)`, R1) wie die übrigen
  Kommandos. Kein Modellaufruf.

### 8. `adw kpis` (A4, E9)

- `adw kpis` berücksichtigt zusätzlich je ausgewertetem Lauf, falls im
  Ledger-Lauf-Ordner vorhanden, `retro.json` und `feedback.jsonl`. Dieselben Filter
  (`--since`, `--repo-slug`) gelten; Läufe ohne diese Dateien bleiben auswertbar.
- Neu im `total`-Objekt (JSON) und in der Textausgabe unter der Gesamtzeile:
  - `retro_causes`: `{<cause>: Anzahl}` über alle Einträge aller Retros;
  - `retro_runs`: Anzahl Läufe mit lesbarem `retro.json` (auch mit `items: []`);
  - `ratings`: `{"good": n, "bad": n}` — je Lauf zählt nur die **letzte** `rate`-Zeile
    in Dateireihenfolge;
  - `finding_feedback`: `{"valid": n, "noise": n}` über alle `finding`-Zeilen (keine
    „letzte zählt"-Regel; Finding-Feedback ersetzt keine Laufbewertung).
- Unlesbare `retro.json` bzw. unlesbare Zeilen in `feedback.jsonl` werden ignoriert
  (kein Traceback, Exit unverändert); `skipped` zählt weiterhin nur ungültige
  `run-record.json`.
- Keine neuen Felder in den Versionsgruppen (`groups[]`); `candidates` und bestehende
  Kennzahlen unverändert. `adw kpis` schreibt weiterhin nichts.

### 9. Doku und Changelog (A5)

- `docs/ADW-HANDBOOK.md` und `docs/ADW-HANDBOOK.de.md` synchron, Abschnitt „Ledger":
  Retro (wann sie läuft, Schema von `retro.json`, Beleg-Regel, `retro`-Event,
  Fehlerverhalten), `adw rate`, `adw feedback` (Aufruf, `feedback.jsonl`-Format,
  Exit-Codes, Ledger-Voraussetzung, Überleben beim Export), neue Felder in `adw kpis`
  samt „letzte Bewertung zählt". CLI-Referenz (§7) um `adw rate` und `adw feedback`
  mit Verweis auf den Ledger-Abschnitt.
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron: neue oberste Sektion
  `## [0.35.0] — Unreleased` mit `### Added`.
- `pyproject.toml`-Version bleibt unverändert.

## Non-Goals

- Keine Änderung an Kontrollfluss, Phasen, Gates, Review-Loops, Exit-Codes; die Retro
  entscheidet nicht über `done`/`escalated`.
- Kein Wechsel des `run-record.json`-Schemas (bleibt Version 1); die Retro ist eine
  eigene Datei.
- Keine Änderung an der Export-Mechanik außer Aufnahme der Retro-Dateien in
  `_ARTIFACTS` und dem Erhalt von `feedback.jsonl`.
- GUI bleibt unverändert.
- Kein zweiter, agentengeschriebener Retro-Text; kein neuer JSON-Parser-Stil.
- Keine neue Dependency; Version in `pyproject.toml` unverändert.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Wochenschleife `adw improve` (W15), Gruppierung/Clustering der Retros.
- Retro bei Gate-Pause, Retro nachträglich für alte Läufe, Retro neu erzeugen
  (Überschreiben eines vorhandenen `retro.json`).
- Effort für den Retro-Agenten (W2).
- Feedback in der GUI, Feedback ohne Ledger (z. B. im Run-Ordner).
- Neue Spalten pro Version in `adw kpis`.
- Validierung, dass Belege inhaltlich zum Eintrag passen; Prüfung über Existenz von
  `seq` bzw. Datei hinaus.
- Bearbeiten oder Löschen von Feedback-Zeilen, Deduplizierung von Feedback.
- Retry-Queues, persistente Abschlussmarker oder ein Transaktions-/Recovery-System für
  Retro oder Ledger-Schreibvorgänge. Agentenlimits (W1), Events, Repo-Lock und der
  fail-open Export sind die vorgesehenen Backstops.

## Akzeptanzkriterien

1. Ein Lauf, der mit Mock-/Fake-Runner bis `done` läuft, ruft `retro_agent` genau
   einmal mit `cwd` = Run-Ordner auf und schreibt `retro.json` (Schema §5) und
   `retro.md`; das `agent.run`-Span der Retro liegt in der `run`-Spanne; ein
   `retro`-Event mit `outcome: "written"` und Anzahl Einträge wird geschrieben.
2. Ein eskalierter Lauf bekommt ebenfalls eine Retro; ein an einem Gate pausierter
   Lauf und ein Lauf mit `AgentRunError` bekommen **keine** (kein Aufruf von
   `retro_agent`, kein `retro.json`).
3. Exit-Code und Phase sind mit und ohne Retro identisch — auch wenn die Retro
   `AgentRunError` wirft oder ungültiges JSON liefert; dann gibt es ein `retro`-Event
   mit `outcome: "failed"` und Grund, eine Meldung auf stderr und kein `retro.json`.
4. Einträge mit `seq:` auf existierende bzw. nicht existierende Seq und mit gültigem
   bzw. ungültigem (nicht existent, absolut, mit `..`) Dateipfad werden nach §4
   behalten bzw. verworfen; ein Eintrag ohne Belege wird verworfen; `dropped` stimmt.
5. Dry-Run → keine Retro (kein Aufruf, kein `retro.json`).
6. Ein Export eines Laufs mit Retro enthält `retro.json` und `retro.md` im Bündel.
7. `adw rate <id> bad --note x` hängt eine Zeile nach §7 an `feedback.jsonl` an und
   erzeugt einen Ledger-Commit `feedback <id> rate`; ein anschließender erneuter Export
   des Laufs lässt `feedback.jsonl` byte-gleich stehen.
8. `adw feedback` mit eindeutigem Präfix schreibt den vollen `finding_key`; mit
   mehrdeutigem oder unbekanntem Präfix Exit 1, `feedback.jsonl` unverändert bzw.
   nicht angelegt.
9. `adw rate` für einen nicht exportierten Lauf → Exit 1, nichts geschrieben.
10. `adw kpis --json` enthält in `total` `retro_causes`, `retro_runs`, `ratings`
    (letzte Bewertung je Lauf zählt) und `finding_feedback` mit den Werten eines
    Fixture-Ledgers (mit mehrfachen Bewertungen und Läufen ohne Retro/Feedback); die
    Textausgabe zeigt sie unter der Gesamtzeile; `groups[]` hat keine neuen Felder.
11. `uv run ruff check .` und `uv run pytest -x -q` grün.

Richtwert: 12–16 neue Tests; deutlich mehr deutet auf Gold-Plating.

## Definition of Done

- Akzeptanzkriterien 1–11 erfüllt und durch automatisierte Tests belegt, die den
  Mock-Runner bzw. einen gefakten Runner und ein temporäres Ledger (`ADW_LEDGER`)
  verwenden; kein Test ruft ein echtes Modell auf oder berührt `~/.adw/ledger`.
- `uv run ruff check .` und `uv run pytest -x -q` grün.
- Handbuch EN+DE und `CHANGELOG.md`/`CHANGELOG.de.md` gemäß §9 synchron und
  entsprechen dem ausgelieferten Verhalten; `pyproject.toml`-Version unverändert.
- Keine neue Dependency; GUI, `run-record.json`-Schema (Version 1) und Kontrollfluss
  unverändert.
- Contract (Single-Lane) nur für extern beobachtbare Flächen: CLI `adw rate` und
  `adw feedback` (Argumente, Optionen, Exit-Codes), Dateien `retro.json`, `retro.md`
  (Struktur), `feedback.jsonl` (Zeilenschema), das `retro`-Event (Typ und Payload) und
  die neuen Felder im `total`-Objekt von `adw kpis --json`; keine internen
  Helper-Signaturen, kein exaktes Textlayout.
