# Spec — W15.1: Der Wochenbericht liefert seinen Brief, die Retro behält ihre Belege

## Ziel

Auf Basis von 0.37.0 behebt W15.1 die Befunde F19 und F20 aus Abschnitt 0 von
`ONBOARDING-SPEC-PLAN.md`:

- `adw improve` verliert keinen Brief-Entwurf mehr an fehlerhaftes JSON. Der Agent liefert
  reines Markdown; wird ein Entwurf fachlich verworfen, bleibt die Rohantwort im Ledger
  erhalten (F19).
- Der Retro-Auftrag sagt eindeutig, dass Belege die Event-Seq meinen und nicht den
  repo-weiten State-Zähler aus `state.saved`-Payloads (F20).

## Scope

### A1 (F19, E1) — Der Brief kommt als reines Markdown

- Auftrag in `_brief` (`adw/improve.py`) und Rollentext in `REGISTRY["improve_agent"]`
  (`adw/agents.py`) verlangen ausdrücklich reines Markdown (sinngemäß: „Respond with the
  brief as plain Markdown, no JSON, no code fence around the whole answer"); die erste
  Zeile ist eine H1-Überschrift mit dem Titel. Die Anweisung „Respond ONLY with JSON" bzw.
  „Antworte NUR mit JSON" entfällt.
- `_brief` nimmt `result.text` ohne JSON-Deserialisierung, entfernt höchstens **einen**
  umschließenden ```` ```markdown … ``` ````-Block, nimmt den Titel aus der ersten H1 und
  prüft weiter `_missing_headings`.
- Fehlt die H1, wird der Entwurf mit Grund verworfen.
- Die bestehende Prüfung der Pflichtüberschriften (`REQUIRED_HEADINGS`: Ausgangslage,
  Aufgabe, Vorentscheidungen, Deferred, Akzeptanzkriterien) bleibt in ihrer Semantik
  unverändert, einschließlich des Ausschlusses von Überschriften innerhalb von Codeblöcken.

### A2 (F19, E2) — Eine verworfene Antwort geht nicht verloren

Für eine ISO-Woche liegen im Ledger folgende Dateien; Jahr und zweistellige Woche folgen der
bisherigen Benennung:

| Datei | Inhalt |
|---|---|
| `reports/<Jahr>-W<Woche>.md` | Wochenbericht mit Brief-Vermerk |
| `reports/<Jahr>-W<Woche>-brief.md` | Angenommener Brief |
| `reports/<Jahr>-W<Woche>-brief.rejected.md` | Unveränderte Rohantwort eines verworfenen Entwurfs |

- Gründe für Verwerfen mit Ablage: fehlende H1, fehlende Pflichtüberschrift(en).
- Die Rohantwort wird unverändert abgelegt, wie vom Agenten geliefert (einschließlich
  eines etwaigen äußeren Codeblocks).
- Die `…-brief.rejected.md` wird zusammen mit dem Bericht im selben Ledger-Commit
  committet und gepusht. Für Commit und Push gilt das vorhandene Fehlerverhalten;
  lokale Berichtsartefakte bleiben bei Git-Fehlern wie heute erhalten.
- Der Bericht nennt den Grund **und** den Dateinamen der Ablehnungsdatei.
- Ausnahme des Agenten (keine Antwort): Verhalten unverändert — heutiger Vermerk im
  Bericht, keine `…-brief.rejected.md`.
- Ein gültiger Brief in derselben Woche entfernt eine vorhandene `…-brief.rejected.md`,
  lokal und im committeten Ledger-Stand (analog zum heutigen Entfernen eines veralteten
  `…-brief.md`).

### A3 (F20, E3) — Der Retro-Auftrag benennt die richtige `seq`

- `_task` in `adw/retro.py` ergänzt einen Satz: `seq:<n>` bezeichnet das **Top-Level-Feld
  `seq` des Event-Records** (jede Zeile von `events.jsonl` beginnt mit `{"seq": <n>, …`);
  `seq`-Werte innerhalb einer `payload` (z. B. bei `state.saved`) sind **keine** Belege.
- Keine Änderung an der Beleg-Prüfung (`_SEQ`, `_event_seqs`, `_valid_evidence`).

### A4 — Changelogs

- `CHANGELOG.md` und `CHANGELOG.de.md` erhalten synchron eine neue Sektion
  `## [0.37.1] — Unreleased` mit `### Fixed`. Die Einträge beschreiben inhaltlich
  gleichlautend: Brief als Markdown statt JSON, Sicherung verworfener Antworten,
  präzisierter Retro-Auftrag.
- Die Version in `pyproject.toml` bleibt `0.37.0`.

## Non-Goals

- Keine Änderung der Version in `pyproject.toml`.
- Keine Änderung an Pflichtüberschriften-Prüfung (`_missing_headings`), Berichtsauswertung,
  Gruppenauswahl oder den bisherigen Bedingungen für die Brief-Erzeugung (`--no-brief`,
  `BRIEF_CAUSES`).
- Keine Neugestaltung von Ablage, Commit oder Push von `adw improve` über die Aufnahme und
  Entfernung der Ablehnungsdatei hinaus.
- Keine Änderung oder Lockerung der Retro-Beleg-Prüfung (E4 aus W14).
- Keine Änderung am Event-Format, an der `state.saved`-Payload, am Ledger-Schema oder an
  der GUI.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Umbenennen des Payload-Felds `seq` bei `state.saved`.
- Ein Event-Log oder Span-Tracing für `adw improve`.
- Wiederholungsversuch des Agenten nach Verwerfen eines Entwurfs.

## Akzeptanzkriterien

1. **Markdown-Brief (A1/E1):** Ein gefakter `improve_agent` antwortet mit Markdown (H1 +
   alle Pflichtüberschriften) → `reports/<Jahr>-W<Woche>-brief.md` wird geschrieben, der
   Bericht nennt den Titel aus der H1.
2. **Umschließender Codeblock (A1/E1):** Derselbe gültige Brief in einem einzelnen
   umschließenden ```` ```markdown … ``` ````-Block → genauso angenommen; die Briefdatei
   enthält den Brief ohne den äußeren Block, der Bericht nennt denselben H1-Titel.
3. **Verwerfen mit Rohantwort (A2/E2):** Antwort ohne H1 sowie Antwort mit H1, aber ohne
   eine Pflichtüberschrift → kein Brief; `reports/<Jahr>-W<Woche>-brief.rejected.md`
   enthält die unveränderte Rohantwort, liegt im Ledger und ist zusammen mit dem Bericht
   committet; der Bericht nennt den jeweiligen Grund und den Dateinamen.
4. **Gültiger Folgeentwurf (A2/E2):** Gültiger Brief nach einem verworfenen in derselben
   Woche → Briefdatei und Titelvermerk liegen vor, die `…-brief.rejected.md` ist entfernt
   (lokal und im committeten Stand).
5. **Agentenausnahme (A2/E2):** Liefert der Agent wegen einer Ausnahme keine Antwort →
   Wochenbericht mit dem heutigen Fehlervermerk, keine `…-brief.rejected.md`
   (bestehendes Verhalten bleibt grün).
6. **Event-Seq im Auftrag (A3/E3):** Der von `_task` erzeugte Text enthält den Hinweis auf
   das Top-Level-Feld `seq` des Event-Records und den Ausschluss von Payload-`seq`-Werten
   wie bei `state.saved` — per Test auf den Text von `_task`.

Richtwert: 4–6 neue bzw. angepasste Tests; verwandte Antwortvarianten dürfen
parametrisiert geprüft werden.

## Definition of Done

- Alle Akzeptanzkriterien sind durch Tests mit gefakten Agentenantworten abgedeckt und
  grün; nachgewiesen wird über die Berichtsdateien unter `reports/`, den Vermerk im
  Bericht und den vom Produkt erzeugten Ledger-Commit sowie über den Text des
  Retro-Auftrags.
- `uv run ruff check .` und `uv run pytest -x -q` grün (E4).
- `CHANGELOG.md` und `CHANGELOG.de.md` inhaltlich synchron mit Sektion
  `## [0.37.1] — Unreleased` / `### Fixed`; Version in `pyproject.toml` unverändert
  `0.37.0`.
- Non-Goals und Deferred sind gewahrt: keine Änderung an Beleg-Prüfung, Event-Format,
  `state.saved`-Payload, Pflichtüberschriften-Prüfung.
- Contract: Single-Lane — geprüft werden nur extern beobachtbare Flächen (Dateien unter
  `reports/`, Vermerk im Bericht, Text des Retro-Auftrags).
