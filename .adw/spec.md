# Spec — GUI-Politur Lauf 5 (sieben Korrekturen an der Run-Inspector-Web-App)

## Goal

Sieben eng umrissene Korrekturen an der in Lauf 4b gebauten Web-App
(`adw gui`): zwei Defekte und fünf Anzeige-Mängel, alle aus einer manuellen
Browser-Durchsicht. Nach diesem Lauf leitet die Oberfläche den Laufstatus
korrekt ab, blockiert beim Auswählen eines Knotens nicht mehr den Browser,
macht Tool-Calls im Baum überfliegbar, zeigt `agent.run`-Details in Reitern,
formatiert Zahlen und Zeiten lesbar, scrollt nie horizontal auf Seitenebene und
listet auch Läufe ohne Event-Log. Maßgeblich ist `docs/GUI-SPEC.md`, besonders
§7.2 (Views) und §9 (Performance); bei Widerspruch gilt die GUI-SPEC.

## Scope

- Ausschließlich die Web-Schicht der GUI (Templates, CSS, JS, Web-Views/-Routen,
  die statusableitende Modell-Anbindung *in der Web-Schicht*). Behoben werden
  die sieben Aufgaben A–G aus dem Issue.
- Die GUI bleibt strikt read-only.
- Korrektur der Statusableitung (A) und der Darstellung (B–G) an vorhandenen
  Views; keine neuen Views, keine neue Informationsarchitektur.
- Neue HTTP-Routen nur, soweit Aufgabe B sie zwingend braucht, und
  ausschließlich read-only; sonst keine neuen Routen.

## Non-Goals

Bindend übernommen aus dem Scope-Deckel und den vorentschiedenen Punkten des
Issues:

- Keine Timeline, kein Artefakte-Reiter, kein Raw-Reiter, kein Diff-Reiter,
  kein Diff-Endpoint, keine i18n, kein Prunen/Retention, kein
  `trace:`-Config-Key.
- Keine Änderung an `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py`,
  `adw/gui/model.py` oder am Orchestrator. Alle sieben Aufgaben sind in der
  Web-Schicht lösbar; ist eine es nicht, ist das ein Befund für den Bericht,
  keine Ausweitung.
- Keine neuen Laufzeit-Dependencies, kein Frontend-Fremdasset (Vanilla JS,
  handgeschriebenes CSS, System-Fonts; kein CDN, keine node-Toolchain).
- Keine Schreibpfade; kein Redesign; kein Umbau der Navigation.
- Die Grenze des `run`-Spans bleibt wie sie ist (E1). Waisen-Spans bleiben wie
  sie sind, repariert wird im fertigen Modell (E2). Spans liegen an der
  Aufrufstelle, der Inhalt im Runner (E4).
- Web-Stack nur als optionales Extra `adw[gui]` (E7).

## Acceptance Criteria

### A — Statusableitung (Defekt)

- A1. Für einen Lauf mit mehreren `run`-Spans in derselben `events.jsonl` zeigen
  Run-Liste **und** Run-Detail den Status des **letzten** `run`-Spans, nicht des
  ersten.
- A2. Hat der letzte `run`-Span noch kein `end`, wird der Lauf als `running`
  angezeigt.
- A3. Für das Beispiel-Log aus dem Issue (drei `run`-Spans, letzter `status=done`)
  erscheint der Lauf als `done`, nicht als `awaiting_approval` — in Liste und
  Detail identisch.
- A4. Regressionstest mit einem Log aus mehreren `run`-Spans deckt A1–A3 ab.

### B — Auswahl eines Knotens blockiert die Oberfläche nicht (Defekt)

- B1. **Referenzfall (deterministische Fixture):** eine im Testbaum abgelegte
  bzw. deterministisch erzeugte `events.jsonl` mit einem `agent.run`-Span, der
  **mindestens 40** Tool-Call-/Tool-Result-Paare mit vollen Ein-/Ausgaben
  enthält; die Payloads umfassen zusammen **mindestens 5 MB**, darunter
  mindestens ein einzelnes Tool-Ergebnis von **mindestens 1 MB** (damit
  vergleichbar mit dem Defekt-Log; deutlich unterhalb der 200-MB-Grenze aus
  §9). Dieselbe Fixture dient dem manuellen Check und den automatisierten
  Tests aus B4. Anforderung: Nach dem Auswählen dieses `agent.run`-Knotens
  bleibt die Oberfläche bedienbar — Messgrenze **höchstens 2 Sekunden** gemäß
  dem in B4 festgelegten Ablauf (großzügige Obergrenze gegenüber dem
  bisherigen 35-Sekunden-Freeze).
- B2. Der Nutzer kommt an **jeden** vollständigen Inhalt heran (Prompt, Antwort,
  jeder Tool-Call-Input, jedes Tool-Ergebnis) — nicht zwingend sofort und alles
  auf einmal, aber erreichbar (z. B. Aufklappen, „alles anzeigen“, abschnitts-
  weises Nachladen). Wie das erreicht wird, ist Umsetzungsentscheidung.
- B3. Neue HTTP-Routen entstehen nur, soweit B sie zwingend braucht, und sind
  read-only; ohne zwingenden Bedarf kommt keine neue Route hinzu.
- B4. **Nachweis:** Die 2-Sekunden-Grenze aus B1 wird durch einen manuellen
  Browser-Check an der B1-Fixture belegt, mit eindeutigem Start- und
  Endsignal: Die Messung **beginnt mit dem Klick** auf den
  `agent.run`-Knoten; **unmittelbar danach** wird ein Reiterwechsel (z. B.
  auf **Tools**) ausgelöst; die Messung **endet, sobald der gewählte Reiter
  sichtbar aktiv ist** und seinen Inhalt zeigt. Gesamtzeit vom Knoten-Klick
  bis zu dieser sichtbaren Reaktion: höchstens 2 Sekunden. Ablauf und
  Ergebnis werden im Bericht dokumentiert. Automatisierte Tests nutzen
  dieselbe Fixture und decken den beobachtbaren Effekt des gewählten
  Mechanismus ab (z. B. dass die initiale Detail-Auslieferung nicht sämtliche
  vollen Payloads auf einmal enthält); der Mechanismus selbst bleibt
  Umsetzungsentscheidung.

### C — Lesbarkeit der Tool-Call-Einträge

- C1. Ein `agent.tool.call`-Knoten im Baum zeigt Werkzeugname und
  werkzeugspezifisches Hauptargument aus dem Payload. Feldpriorität für die
  im Issue genannten Fälle: **Read → Dateipfad**, **Bash → Kommandozeile**
  (ggf. gekürzt), **Grep → Suchmuster** (z. B. „Read models.py“,
  „Bash pytest -x -q“, „Grep RUN_ID_RE“). Für andere Werkzeuge: Werkzeugname
  plus Hauptargument, sofern eines im Payload eindeutig identifizierbar ist,
  sonst Werkzeugname allein. Es wird keine Unterstützung über den
  Payload-Inhalt hinaus verlangt.
- C2. Ein `agent.tool.result`-Knoten zeigt kompakt seinen Ausgang
  (Fehler/Erfolg; bei Bash der Exit-Code, sofern im Payload vorhanden).
- C3. Rückfall-Regeln — nichts wird erfunden: Fehlt der **Werkzeugname** im
  Payload, bleibt der Eintrag beim unveränderten Typnamen (`agent.tool.call`
  bzw. `agent.tool.result`). Ist der Werkzeugname vorhanden, aber das
  Hauptargument fehlt, wird der Werkzeugname allein gezeigt — kein
  Ersatzwert. Fehlen bei einem Ergebnis die Ausgangsfelder, bleibt es beim
  Typnamen.

### D — Reiter im Detail-Pane

- D1. Für `agent.run` bietet das Detail-Pane die Reiter **Prompt**, **Antwort**
  und **Tools** (statt gestapelter Abschnitte).
- D2. Der Diff-Reiter ist in diesem Lauf **nicht** vorhanden.

### E — Formatierung von Zahlen und Zeiten

- E1. Dauern werden lesbar formatiert (z. B. `2828.7s` → `47m 9s`) statt als rohe
  Sekunden.
- E2. Kosten werden lesbar als Geldbetrag formatiert (z. B.
  `5.795072500000001` → `$5.80`) statt als roher Float.
- E3. Zeitstempel werden als `YYYY-MM-DD HH:MM:SS` in **UTC** dargestellt —
  ohne `Z`-Suffix, ohne Sekundenbruchteile. UTC ist bewusst gewählt, damit
  die Ausgabe deterministisch und umgebungsunabhängig testbar ist (die
  Quelldaten liegen als ISO-UTC vor).
- E4. In der Run-Listen-Tabelle steht ein Zeitstempel auf **einer** Zeile und
  bricht nicht über zwei Zeilen um (Darstellungsanforderung, geprüft am
  Tabellen-Standardfall).
- E5. Fehlende Werte bleiben leer; es erscheint nie `0` oder `null` als Text.

### F — Kein horizontaler Überlauf

- F1. Breite Inhalte (Prompts, Tool-Ausgaben, Tabellen) scrollen innerhalb ihres
  eigenen Kastens oder brechen um.
- F2. Die Seite selbst scrollt nie horizontal; das Prompt-Pane läuft nicht mehr
  rechts aus dem Viewport, kein Text wird abgeschnitten.

### G — Läufe ohne Event-Log

- G1. Läufe mit `state.json`, aber ohne `events.jsonl` (z. B. `8f8dc4ff`,
  `e680e005`) erscheinen in der Run-Liste — mit den Angaben aus dem State.
- G2. Für solche Läufe zeigt die Oberfläche einen klaren Hinweis, dass kein Trace
  existiert.
- G3. Der Aufruf ihres Details führt nie zu einem Fehler.

## Definition of Done

1. Alle Akzeptanzkriterien A1–A4, B1–B4, C1–C3, D1–D2, E1–E5, F1–F2, G1–G3
   sind erfüllt. Alle sind durch automatisierte Tests abgedeckt — mit einer
   Ausnahme: die 2-Sekunden-Grenze aus B1 wird gemäß B4 durch den
   dokumentierten, reproduzierbaren manuellen Browser-Check belegt; der
   automatisierbare Teil von B ist durch Tests abgedeckt. (Richtwert aus dem
   Issue: rund 15–22 neue Tests für A–G zusammen; Richtwert, keine harte
   Grenze.)
2. `uv run ruff check .` ist grün.
3. `uv run pytest -x -q` ist grün (die bestehenden 653 Tests plus die neuen
   A–G-Tests).
4. Keine der unter Non-Goals genannten Dateien (`adw/events.py`,
   `adw/snapshots.py`, `adw/gui/reader.py`, `adw/gui/model.py`, Orchestrator)
   wurde geändert; ist eine Aufgabe ohne solche Änderung nicht lösbar, steht das
   als Befund im Bericht statt als stille Ausweitung.
5. Keine neue Laufzeit-Dependency und kein Frontend-Fremdasset ist hinzugekommen;
   neue HTTP-Routen nur die für Aufgabe B zwingend nötigen, alle read-only. Die
   GUI bleibt strikt read-only.

## Deferred (bewusst nicht gebaut)

Weitergehende Härtungs- oder Erweiterungsideen — auch aus den Codex-Review-Runden
— gehören hierher, nicht in die Akzeptanzkriterien. Ein Finding, das einen dieser
Punkte oder einen vorentschiedenen Punkt einführen will, wird abgewiesen und mit
Begründung dokumentiert, nicht umgesetzt.

- Timeline-Reiter, Artefakte-Reiter, Raw-Reiter, Diff-Reiter, Diff-Endpoint.
- i18n / Sprachumschaltung.
- Prunen / Retention / `trace:`-Config-Key.
- Änderungen an der `run`-Span-Grenze (E1) oder an den Waisen-Spans/`events.py`
  (E2).
- Kappung von Payloads im **Log**: Der Deferred-Punkt „keine Kappung von
  Payloads“ betrifft das Log, nicht die Darstellung (E8). Aufgabe B kürzt,
  klappt ein oder lädt abschnittsweise **in der Anzeige** und verstößt damit
  nicht gegen „keine Kappung“; ein Review-Finding, das B als solchen Verstoß
  wertet, wird abgewiesen.
- Redesign, neue Views, neue Informationsarchitektur, Umbau der Navigation.
- HTTP-Routen über die für Aufgabe B zwingend nötigen hinaus.
- Automatisierte Browser-/Responsiveness-Messung (Headless-Browser,
  Performance-Harness): würde eine neue Toolchain erfordern (E5) und ist für
  den Nachweis von B1 nicht nötig — der dokumentierte manuelle Check genügt.
- Zeitzonen-Umschaltung / Anzeige in Lokalzeit statt UTC.
