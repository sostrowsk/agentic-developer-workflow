# Plan — W15.1: Der Wochenbericht liefert seinen Brief, die Retro behält ihre Belege

Quelle: `.adw/spec.md`, Stand `0.37.0`. Single-Lane: nur Workstream **backend**.
Contract: `.adw/contract.yaml` — pinnt ausschließlich extern beobachtbare Flächen (Dateien
unter `reports/`, Vermerk im Bericht, Ledger-Commit, Text des Retro-Auftrags). Interne
Rückgabetypen und Helper-Signaturen (`_brief`, `_commit`, …) sind frei. Es entstehen keine
HTTP-Routen, Events oder neuen Schnittstellen.

## Workstream: backend

**Betroffene Dateien:** `adw/improve.py`, `adw/agents.py`, `adw/retro.py`,
`tests/test_improve.py`, `tests/test_retro.py`, `CHANGELOG.md`, `CHANGELOG.de.md`.

### B1 — Brief als reines Markdown (A1/E1) — `adw/improve.py`, `adw/agents.py`

1. `_brief`: Auftragsende `'Antworte NUR mit JSON: …'` ersetzen durch die ausdrückliche
   Vorgabe für reines Markdown (sinngemäß „Antworte mit dem Brief als reinem Markdown, kein
   JSON, kein Codeblock um die ganze Antwort; erste Zeile ist eine H1-Überschrift mit dem
   Titel").
2. `REGISTRY["improve_agent"]` in `adw/agents.py`: `system_append` analog anpassen
   („Respond with the brief as plain Markdown, no JSON, no code fence around the whole
   answer; first line is an H1 heading with the title"); die JSON-Anweisung entfällt.
3. `_brief` verarbeitet `result.text` ohne `json.loads`; die Rohantwort bleibt unverändert
   erhalten:
   - höchstens **einen** umschließenden ```` ```markdown … ``` ````-Block entfernen — nur
     wenn die gesamte, getrimmte Antwort von genau diesem Block umschlossen ist; keine
     rekursive Entpackung, keine Entfernung innerer Codeblöcke;
   - Titel = Text der ersten H1 (`# …`) außerhalb von Codeblöcken; fehlt sie → verworfen
     mit Grund (z. B. „Brief-Entwurf verworfen: H1-Überschrift fehlt");
   - `_missing_headings` unverändert auf den (entpackten) Brief anwenden; Grundtext wie
     heute („Brief-Entwurf verworfen: Überschriften fehlen: …");
   - Erfolgsvermerk wie heute: `Brief-Entwurf (<cause>): <Titel>` (Titel via `_cut`).
4. Rohantwort und Ablehnungsgrund für B2 verfügbar machen (interne Datenübergabe frei).
5. `import json` in `adw/improve.py` entfernen, falls danach unbenutzt (ruff).

### B2 — Verworfene Rohantwort sichern (A2/E2) — `adw/improve.py`

1. `_improve` unterscheidet drei Ausgänge von `_brief`: angenommen (Brief) / fachlich
   verworfen (Rohantwort + Grund) / Agentenausnahme (nur Vermerk, keine Rohantwort).
2. Neben `brief_path`: `rejected_path = reports / f"{week}-brief.rejected.md"`
   (Jahr und zweistellige ISO-Woche wie bisher über `week`).
   - Fachlich verworfen (fehlende H1, fehlende Pflichtüberschrift): Rohantwort
     **unverändert** nach `rejected_path` schreiben — einschließlich eines äußeren
     Codeblocks und der ursprünglichen Zeilenumbrüche, keine Normalisierung wie beim
     angenommenen Brief. Der Vermerk im Bericht beginnt wie heute mit
     „Brief-Entwurf verworfen: " und nennt Grund **und** Dateinamen
     `<week>-brief.rejected.md`. Wie bisher bleibt kein veralteter `…-brief.md` übrig.
   - Gültiger Brief: `rejected_path.unlink(missing_ok=True)`, Brief schreiben.
   - Agentenausnahme / `--no-brief` / keine geeignete Gruppe / zu wenige Läufe: keine
     Ablehnungsdatei; Vermerk wie heute (bei Ausnahme weiterhin mit `str(exc)`).
3. `_commit(...)`-Pfadliste um `f"reports/{week}-brief.rejected.md"` ergänzen. Die
   bestehende `ls-files`/`add -A`-Logik nimmt damit Anlage **und** Entfernung der Datei in
   denselben `improve <week>`-Commit auf. Commit-/Push-Fehlerverhalten unverändert
   (Warnung, lokale Artefakte bleiben).

### B3 — Retro-Auftrag präzisieren (A3/E3) — `adw/retro.py`

1. `_task` um einen Satz ergänzen: `seq:<n>` bezeichnet das Top-Level-Feld `seq` des
   Event-Records (jede Zeile von `events.jsonl` beginnt mit `{"seq": <n>, …`); `seq`-Werte
   innerhalb einer `payload` (z. B. bei `state.saved`) sind keine Belege.
2. Keine Änderung an `_SEQ`, `_event_seqs`, `_valid_evidence`.

### B4 — Changelogs (A4)

1. `CHANGELOG.md` und `CHANGELOG.de.md`: neue Sektion `## [0.37.1] — Unreleased` mit
   `### Fixed`, inhaltlich gleichlautend: Brief als Markdown statt JSON; Sicherung
   verworfener Antworten in `…-brief.rejected.md`; präzisierter Retro-Auftrag (Event-Seq).
2. `pyproject.toml` bleibt `0.37.0`.

### Tests (4–6 neu/angepasst) — `tests/test_improve.py`, `tests/test_retro.py`

Vorhandene Fake-Runner- und Ledger-Fixtures weiterverwenden. Die JSON-Briefantworten
(`GOOD`, Parametrisierungen, zweite Fassung im Commit-Lebenszyklus-Test) auf Markdown mit
H1 umstellen; `json.dumps` für andere Testdaten (run-record, retro, events) bleibt.

1. **AK1+AK2 (parametrisiert):** gültiger Markdown-Brief, einmal roh, einmal in einem
   umschließenden ```` ```markdown ````-Block → `…-brief.md` ohne äußeren Block, Bericht
   nennt den H1-Titel, keine `…-brief.rejected.md`.
2. **AK3 (parametrisiert):** ohne H1 / mit H1, aber ohne Pflichtüberschrift (bestehende
   Varianten „missing-heading", „combined-heading", „fenced", „long-fence" auf Markdown
   umstellen) → kein `…-brief.md`; `…-brief.rejected.md` enthält exakt die Rohantwort;
   Datei ist mit dem Bericht in `HEAD` (`git show --name-only`); Bericht nennt
   „Brief-Entwurf verworfen: ", Grund und Dateinamen.
3. **AK4:** verworfener Lauf, dann gültiger Lauf in derselben Woche → Brief + Titelvermerk;
   `…-brief.rejected.md` lokal weg und in `HEAD` nicht mehr vorhanden.
4. **AK5:** bestehender Ausnahme-/Timeout-Test bleibt grün; zusätzlich Assert: keine
   `…-brief.rejected.md`; einmaliger Agentenaufruf bleibt.
5. **Commit-Lebenszyklus:** bestehender Test `test_one_commit_per_call_overwrite_and_brief_removal`
   nur auf Markdown-Antworten umgestellt, Aussagen zu Briefentfernung und Commit erhalten.
6. **AK6 (`tests/test_retro.py`):** Text von `_task` enthält den Hinweis auf das
   Top-Level-Feld `seq` des Event-Records und den Ausschluss von Payload-`seq`
   (`state.saved`).

Geprüft werden nur Dateien unter `reports/`, Berichtsvermerke, der vom Produkt erzeugte
Ledger-Commit und der Retro-Auftragstext — keine Tests auf neue interne Rückgabetypen.

### Gates (E4)

- `uv run ruff check .` und `uv run pytest -x -q` grün.
- Abschlussprüfung der Non-Goals: `_missing_headings`, Berichtsauswertung, Gruppenauswahl,
  `--no-brief`, `BRIEF_CAUSES`, Retro-Beleg-Prüfung, Event-Format, `state.saved`-Payload,
  Ledger-Schema und GUI unverändert.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Umbenennen des Payload-Felds `seq` bei `state.saved`.
- Ein Event-Log oder Span-Tracing für `adw improve`.
- Wiederholungsversuch des Agenten nach Verwerfen eines Entwurfs.
