# Spec: Kontext-Panel „Lauf-Zustand zu diesem Knoten" im Run-Detail

## Ziel (Goal)

Im Run-Detail steht neben dem Detail-Pane ein read-only Kontext-Panel zur
Verfügung, das den Lauf-Zustand **zum Stand des ausgewählten Trace-Knotens**
als feste Feldliste zeigt — rein abgeleitet aus allen Events bis zur `seq` des
Knotens. Damit sind Phase, Schleifenposition, getroffene Limits und
Circuit-Breaker, kumulierte Kosten und Follow-ups sichtbar, ohne den Baum
hoch- und runterzuklicken oder in den Raw-Tab zu wechseln.

## Scope

- Serverseitige Ableitung des Lauf-Kontexts in `adw/gui/app.py` beim
  bestehenden Aufbau der Antwort von `GET /api/runs/{repo}/{run_id}`, unter
  Wiederverwendung der vorhandenen Seq-Zuordnung (`_span_seq_ranges()`,
  `_subtree_seq_range()`) und Kostenlogik (`_subtree_cost`/`_events_cost`).
- Neue, rein abgeleitete Antwortfelder (Contract-Fläche):
  - pro Trace-Knoten ein Feld `context` mit genau:
    - `phase` — die zum Seq-Stand des Knotens geltende Phase
    - `round` — die umgebende Schleife, `null` oder Objekt `{loop, n, cap}`
    - `limit_hits` — Anzahl bis hier getroffener `limit.hit`-Ereignisse
    - `circuit_breakers` — Anzahl bis hier getroffener `circuit_breaker`-Ereignisse
    - `cost_usd` — kumulierte Kosten (USD) bis hier
    - `followups` — Anzahl bis hier erfasster `followup`-Einträge
  - auf oberster Ebene `latest_context` mit derselben Struktur für den Stand
    des jüngsten Events (Live-Lauf ohne Auswahl).
  - Fehlende Daten sind durchgehend `null`, nie ein erfundenes `0`.
- Read-only Anzeige als Feldliste im Run-Detail neben dem Detail-Pane:
  bei Knotenauswahl der `context` des Knotens (Zeitreise), ohne Auswahl
  `latest_context`; leere Felder werden leer angezeigt.
- Doku: `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` (§7.2) sowie
  `CHANGELOG.md` + `CHANGELOG.de.md` (`Unreleased`).

## Non-Goals / Scope-Deckel

- Keine Änderung an `adw/state.py`, an der Instrumentierung oder am
  Event-Schema; `state.saved` wird NICHT erweitert — das Panel ist rein
  abgeleitet. (E1)
- Kein neuer Reader, keine neue Route, kein zusätzlicher Datei-Zugriff; die
  Ableitung nutzt ausschließlich die für die Detail-Antwort ohnehin geladenen
  Events. (E2)
- Kein konfigurierbares Feld-Set, keine Nutzereinstellung, keine Persistenz
  der Auswahl; das Panel ist read-only. (E3)
- Keine zweite Kosten-Rechenart neben `_subtree_cost`/`_events_cost`. (E4)
- Kein Diagramm, keine Verlaufskurve — eine Feldliste. (E5)
- Keine Änderung an Timeline, Artifacts, Raw, Diff oder am SSE-Protokoll;
  kein neues Event, kein neuer Persistenz-Zustand, keine
  Cross-Run-Aggregation.
- Keine neue Laufzeit-Dependency, kein CDN.
- Interne Helper-Signaturen, Dictionary-Schlüssel jenseits der Antwortfelder
  sowie Markup/CSS sind nicht Teil des externen Vertrags.

## Akzeptanzkriterien (Acceptance Criteria)

1. `GET /api/runs/{repo}/{run_id}` liefert pro Trace-Knoten ein `context` und
   auf oberster Ebene ein `latest_context`, jeweils mit genau den sechs
   Feldern `phase`, `round`, `limit_hits`, `circuit_breakers`, `cost_usd`,
   `followups` — nicht mehr und nicht weniger. Bestehende Antwortfelder und
   Routen behalten ihre Semantik. (A1, A2, E3)
2. Der maßgebliche Seq-Stand eines Knotens (im Folgenden: Cutoff) ist bei
   Punkt-Ereignissen dessen `seq`; bei Span-Knoten dessen bereits in der
   Baum-Antwort exponiertes `end_seq` (Subtree-Maximum), bei laufenden Spans
   also die höchste bisher beobachtete `seq` des Subtrees. Der `context` wird
   ausschließlich aus Events mit `seq` kleiner oder gleich dem Cutoff
   abgeleitet — die Auswahl eines abgeschlossenen Spans umfasst damit auch
   Kosten und Ereignisse innerhalb des Spans nach dessen Start-`seq`; spätere
   Events beeinflussen den Kontext nicht. Die Auswahl eines anderen Knotens
   zeigt dessen historischen Kontext (Zeitreise). (A1, A3)
3. `latest_context` wird aus allen Events bis einschließlich der höchsten
   `seq` abgeleitet; im Live-Lauf zeigt das Panel ohne Knotenauswahl diesen
   jüngsten Stand. Die Aktualisierung nutzt die bestehenden Mechanismen; das
   SSE-Protokoll bleibt unverändert. (A3)
4. `phase` wird aus genau zwei bestehenden Quellen abgeleitet: dem
   Start-Payload-Feld `name` von `phase`-Spans und dem Payload-Feld `phase`
   von `state.saved`-Ereignissen; leere oder fehlende Werte zählen nicht als
   Beobachtung. Maßgeblich ist die Beobachtung mit der höchsten Event-`seq`
   bis einschließlich des Cutoffs (Seqs sind eindeutig, es gibt keinen
   Gleichstand). Beispiel: nach `state.saved` mit `phase="spec"` (seq 30) und
   `phase`-Span-Start `name="plan"` (seq 40) zeigt ein Cutoff ≥ 40 `plan`,
   ein Cutoff von 30–39 `spec`. `state.saved` wird nur gemäß seinem
   vorhandenen Payload `{seq, phase}` ausgewertet und weder als Zustands-Dump
   behandelt noch erweitert. Ist bis zum Cutoff keine der Quellen
   beobachtbar, ist `phase` `null`. (A2, A4, E1)
5. Liegt der Knoten innerhalb eines `round`-Spans oder ist er selbst der
   `round`-Knoten, enthält `round` die protokollierten Werte der nächsten
   umgebenden Runde als `{loop, n, cap}`. Außerhalb einer protokollierten
   Runde ist `round` `null`; fehlende einzelne Round-Werte bleiben `null`
   und werden nicht ergänzt oder geschätzt. Für `latest_context` gilt
   dasselbe Containment-Modell relativ zum Event mit der höchsten `seq`:
   `round` ist die innerste `round`, deren Span dieses Event enthält —
   Start- und End-Event der Runde zählen zu ihrem Span, sodass eine gerade
   startende oder gerade endende Runde noch gezeigt wird; ist das jüngste
   Event außerhalb jeder Runde (etwa nach deren Ende), ist `round` `null`.
   (A2, A3, A4)
6. `limit_hits` und `circuit_breakers` zählen die bis einschließlich des
   Cutoffs tatsächlich protokollierten `limit.hit`- bzw.
   `circuit_breaker`-Ereignisse; wurde der jeweilige Ereignistyp bis dahin
   nicht protokolliert, ist das Feld `null`, nicht `0`. (A2, A4)
7. `cost_usd` ist die kumulierte Summe der bis einschließlich des Cutoffs
   protokollierten Kosten abgeschlossener `agent.run`-Ereignisse und folgt
   derselben Kosten-Semantik wie `_subtree_cost`/`_events_cost`; es existiert
   keine zweite Rechenart. Liegt kein verwertbarer Kostenwert vor, ist
   `cost_usd` `null`, nicht `0`; ungültige Werte werden nicht als Kosten
   erfunden. (A2, A4, E4)
8. `followups` zählt die bis einschließlich des Cutoffs protokollierten
   `followup`-Ereignisse; ohne solches Ereignis ist das Feld `null`,
   nicht `0`. (A2, A4)
9. Das Kontext-Panel erscheint im Run-Detail neben dem Detail-Pane als
   read-only Feldliste; ein Wechsel der Knotenauswahl aktualisiert alle
   Felder auf den `context` des neuen Knotens. Inhalte und Verhalten der
   bestehenden Detail-Pane-Tabs bleiben unverändert. (A1, A3, E3, E5)
10. Ein Lauf ohne Trace hat keine Trace-Knoten und damit kein per-Knoten-
    `context`; die Antwort enthält weder einen synthetischen Knoten noch ein
    top-level `context`-Feld, sondern nur `latest_context` mit allen sechs
    Feldern `null` — und niemals einen Fehler / HTTP 5xx. Fehlende Eventtypen
    oder unvollständige Payloads führen zu leeren betroffenen Feldern statt
    erfundener Nullwerte oder eines Fehlers. (A4)
11. Die Ableitung verwendet ausschließlich den bereits für die
    Run-Detail-Antwort geladenen Event-Strom; sie erzeugt keine neue Route,
    keinen neuen Reader, keinen zusätzlichen Datei-Zugriff, keine Persistenz
    und keine neue Laufzeit-Dependency. Timeline, Artifacts, Raw, Diff und
    SSE-Protokoll bleiben unverändert. (E1, E2, Nicht-Ziele)
12. `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` dokumentieren in §7.2 das
    Panel, die Zeitreise-Semantik und die Leer-Semantik (`null` statt `0`);
    `CHANGELOG.md` und `CHANGELOG.de.md` führen die Änderung unter
    `Unreleased`. (Doku)

## Deferred (bewusst nicht gebaut)

Nachvollziehbar, aber für die Ausgangslage unverhältnismäßig. KEINE
Akzeptanzkriterien — und bindend auch für den Review-/Codex-/Fix-Zyklus:
was hier steht, wird dort nicht nachgebaut.

- Verlauf/Zeitreihe einer Kennzahl über den gesamten Lauf (Kurve, Sparkline).
- Vergleich des Zustands zweier Knoten nebeneinander.
- Export des berechneten Lauf-Zustands (Datei/Clipboard/Route).
- Erweiterung des Panels um weitere Events, Kennzahlen oder frei wählbare
  Felder.

## Definition of Done

- Alle Akzeptanzkriterien erfüllt und durch Tests unter `tests/` als
  `test_gui_*.py` abgedeckt: Kontext pro Knoten bis Cutoff, Span-Cutoff mit
  einem Kosten-/`limit.hit`-/`followup`-Ereignis innerhalb des Spans nach
  dessen Start-`seq`, Phasen-Präzedenz zwischen `phase`-Span und
  `state.saved` nach `seq`, Zeitreise bei Knotenwechsel, `latest_context`
  ohne Auswahl, Round-Zuordnung inkl. `round=null` und
  `latest_context.round` innerhalb vs. nach Ende einer Runde, Kosten und
  Zählfelder `null` statt `0`, `phase=null`, Lauf ohne Trace ohne Fehler
  (nur `latest_context`, kein synthetischer Knoten).
- Richtwert ~12 neue Tests (Bestand: 892); mehr als ~20 gilt als Scope-Drift.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
- Keine neue Laufzeit-Dependency, kein CDN.
- Doku aktualisiert: `docs/GUI-SPEC.md`/`docs/GUI-SPEC.de.md` (§7.2) und
  `CHANGELOG.md`/`CHANGELOG.de.md` (`Unreleased`).
