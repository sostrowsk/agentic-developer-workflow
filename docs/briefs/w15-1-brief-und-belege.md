# W15.1: Der Wochenbericht liefert seinen Brief, die Retro behält ihre Belege

Kleiner Nachzug zu W14/W15 (Befunde F19 und F20 aus `ONBOARDING-SPEC-PLAN.md`,
Abschnitt 0). Setzt auf dem gemergten Stand **0.37.0** auf. Ausgangslage am
2026-11-08 gemessen.

## Ausgangslage (gemessen, 2026-11-08, Stand 0.37.0)

### F19 — Der Brief-Entwurf scheitert am JSON

Der erste echte `adw improve --since 7` (Bericht `reports/2026-W45.md` im Ledger, 15
Läufe, 6 Problemgruppen) endete mit „Brief-Entwurf verworfen: Antwort ist kein JSON".
`_brief` (`adw/improve.py:242–267`) verlangt vom `improve_agent` eine Antwort
`{"title": str, "brief_markdown": str}` und parst sie mit `json.loads(result.text)`
(`:257`). Ein mehrere hundert Zeilen langer Markdown-Brief als JSON-String (Zeilenumbrüche,
Backticks, Anführungszeichen) ist fehleranfällig; nach ~5 min Fable-Lauf war die Arbeit
verloren. Der Rollentext in `REGISTRY["improve_agent"]` (`adw/agents.py:~393–397`)
verlangt ebenfalls „Respond ONLY with JSON". Die Rohantwort wird nirgends gesichert
(`adw improve` hat kein Event-Log).

### F20 — Die Retro zitiert die falsche `seq`

Die Retro von Lauf `a71e3162` (W15) verwarf **alle 4** Einträge (`dropped: 4`, `items: []`).
Der Agent zitierte `seq:1723`, `seq:1741` …; das Event-Log dieses Laufs endet bei Seq 691.
Die Zahlen stammen aus der Payload der `state.saved`-Events (`adw/state.py:219`:
`{"seq": self.seq, "phase": …}` — der repo-weite **State**-Zähler, nicht die Event-Seq).
Ein Grep nach `"seq":` trifft beide. Die Beleg-Prüfung (`adw/retro.py`, `_SEQ =
re.compile(r"seq:(\d+)")`) hat korrekt verworfen; der Auftrag (`_task`, `adw/retro.py:58–71`)
sagt nur „Evidence is 'seq:<n>' of an event in events.jsonl". Die vier anderen Retros des
Tages hatten `dropped: 0`.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

- Prüfung der Pflichtüberschriften (`_missing_headings`), Ablage, Commit/Push von
  `adw improve` bleiben.
- Beleg-Prüfung der Retro (E4 aus W14) bleibt unverändert streng.
- Das Event-Format und die Payload von `state.saved` bleiben (Umbenennen würde die GUI
  und das Ledger-Schema berühren).

## Aufgabe

**A1 (F19)** Der `improve_agent` antwortet mit reinem Markdown statt JSON.
**A2 (F19)** Eine verworfene Antwort geht nicht verloren.
**A3 (F20)** Der Retro-Auftrag sagt eindeutig, welche `seq` gemeint ist.
**A4** `CHANGELOG.md`/`CHANGELOG.de.md` synchron: neue Sektion
`## [0.37.1] — Unreleased` mit `### Fixed`. Version in `pyproject.toml` **nicht** ändern.

## Vorentscheidungen (bindend — kein Finding, auch nicht im Review-Loop)

- **E1 (F19):** Der Agent liefert den Brief als **reines Markdown** (erste Zeile eine
  H1-Überschrift = Titel). Auftrag und Rollentext sagen das ausdrücklich („Respond with
  the brief as plain Markdown, no JSON, no code fence around the whole answer").
  `_brief` nimmt `result.text`, entfernt höchstens einen umschließenden
  ```` ```markdown … ``` ````-Block, nimmt den Titel aus der ersten H1 und prüft weiter
  `_missing_headings`. Fehlt die H1 → verworfen mit Grund.
- **E2 (F19):** Wird ein Entwurf verworfen (fehlende H1, fehlende Überschriften), wird
  die Rohantwort als `reports/<Jahr>-W<Woche>-brief.rejected.md` abgelegt und mit
  committet; der Bericht nennt Grund **und** Dateinamen. Bei Ausnahmen des Agenten
  (keine Antwort) bleibt es beim heutigen Vermerk. Ein gültiger Brief in derselben Woche
  entfernt eine vorhandene `…-brief.rejected.md` (wie heute der veraltete Brief).
- **E3 (F20):** `_task` in `adw/retro.py` ergänzt einen Satz: Gemeint ist das
  **Top-Level-Feld `seq` des Event-Records** (jede Zeile von `events.jsonl` beginnt mit
  `{"seq": <n>, …`); `seq`-Werte in einer `payload` (z. B. bei `state.saved`) sind
  **keine** Belege. Keine Änderung an der Prüfung.
- **E4:** Toolchain ist `uv run ruff check .` und `uv run pytest -x -q`.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Umbenennen des Payload-Felds `seq` bei `state.saved`.
- Ein Event-Log oder Span-Tracing für `adw improve`.
- Wiederholungsversuch des Agenten nach Verwerfen.

## Akzeptanzkriterien

1. Gefakter `improve_agent` antwortet mit Markdown (H1 + Pflichtüberschriften) → Brief
   geschrieben, Titel aus der H1 im Bericht.
2. Antwort in einem umschließenden ```` ```markdown ````-Block → genauso angenommen.
3. Antwort ohne H1 bzw. ohne Pflichtüberschrift → kein Brief, `…-brief.rejected.md` mit
   der Rohantwort liegt im Ledger und ist committet, Bericht nennt Grund und Datei.
4. Gültiger Brief nach verworfenem in derselben Woche → `…-brief.rejected.md` entfernt.
5. Der Retro-Auftrag enthält den Hinweis aus E3 (Test auf den Text von `_task`).
6. `uv run ruff check .` und `uv run pytest -x -q` grün.

**Richtwert Testzahl:** 4–6 neue bzw. angepasste Tests.

**Contract:** Single-Lane — nur extern beobachtbare Flächen (Dateien unter `reports/`,
Vermerk im Bericht).
