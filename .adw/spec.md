# Spec: Vom Knoten in den Raw-Log springen + Prompt-Diff gegen die Vorrunde

## Ziel (Goal)

Das Run-Detail macht die Rohereignisse eines einzelnen Trace-Teilbaums direkt
auffindbar und zeigt bei jedem `agent.run`, wie sich dessen Prompt gegenüber
dem vorherigen Lauf desselben Agenten in derselben Lane verändert hat. Damit
entfallen die manuelle Suche nach Seq-Grenzen im Raw-Log und das
Nebeneinanderlegen zweier Prompt-Tabs. Beide Funktionen sind rein abgeleitete
Projektionen des bereits geladenen Event-Stroms — kein neuer Reader, keine
neue Route, kein neues Event, keine Persistenz.

## Scope

- Single-Lane: `backend`.
- Erweiterung des bestehenden Raw-Tabs um einen inklusiven Seq-Bereichsfilter
  mit unterer und oberer Grenze, serverseitig komponiert mit den bestehenden
  Raw-Filtern `q`, `type` und der `limit`-Fensterung. (A1)
- Absprung von jedem Span-Knoten des Trace-Baums in den bestehenden Raw-Tab,
  vorgefiltert auf den Teilbaum-Bereich `[seq, end_seq]` dieses Knotens
  (`end_seq` ist das bereits exponierte Subtree-Maximum). Sichtbare Anzeige
  eines aktiven Bereichsfilters und eine Bedienmöglichkeit, ausschließlich
  diesen aufzuheben. (A2)
- Serverseitige Ermittlung des Vorgänger-`agent.run` innerhalb DESSELBEN
  Laufs (gleicher `agent`-String, gleiche Lane, größte kleinere `seq`) und
  Erzeugung eines Unified Diffs mit `difflib` aus der Standardbibliothek.
  (A3, A4, E3)
- Neue, rein abgeleitete Antwortfelder an serialisierten `agent.run`-Knoten
  in `GET /api/runs/{repo}/{run_id}` (Contract-Fläche):
  - `prompt_diff` — `null`, wenn kein verwertbarer Vorgänger existiert;
    andernfalls der Unified Diff als String, wobei `""` einen identischen
    Prompt bezeichnet.
  - `previous_prompt_seq` — die `seq` des verwendeten Vorgänger-`agent.run`
    oder `null`, wenn keiner verwendet wurde.
- Anzeige des Diffs zusätzlich zum vollen Prompt im bestehenden Prompt-Tab,
  mit unterscheidbaren Leerzuständen für „kein Vorgänger" und „identischer
  Prompt". (A3)
- Doku: `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` (§7.2) sowie
  `CHANGELOG.md` + `CHANGELOG.de.md` (`Unreleased`).

## Non-Goals / Scope-Deckel

- Die JSON-Route `GET /api/runs/{repo}/{run_id}/events` wird NICHT verändert:
  sie behält genau `from_seq` und `to_seq`; kein `type`-Parameter, keine
  Paginierung, keine neuen Antwortfelder. Filter-Komposition ist
  ausschließlich Sache des Raw-Tabs. Diese Frage ist entschieden — kein
  Finding, auch nicht im Review-Loop. (E1)
- Keine neue Dependency: nur `difflib`; keine Diff-Bibliothek, kein
  Syntax-Highlighter, kein Frontend-Paket. (E2)
- Kein Vergleich über Läufe, Agenten oder Lanes hinweg, keine
  Ähnlichkeitsheuristik, kein geratener Ersatz-Vorgänger. (E3)
- Kein zweites Raw-Widget im Detail-Pane; der Absprung ist ein normaler
  Wechsel in den bestehenden Raw-Tab mit gesetztem Filter. (E5)
- Kein neues Tab, kein Prompt-Editor, kein Zwischenablage-Subsystem, keine
  Prompt-Historie über Läufe, keine Persistenz des Filterzustands.
- Keine Änderung an Timeline, Artifacts, dem bestehenden Snapshot-Diff-Tab
  oder SSE; kein neues Event, kein neuer persistenter Zustand.
- Interne Helper-Signaturen sowie konkretes Markup/CSS sind nicht Teil des
  externen Vertrags.

## Akzeptanzkriterien (Acceptance Criteria)

1. Der bestehende Raw-Tab akzeptiert eine optionale untere und obere
   Seq-Grenze. Sind beide gesetzt, enthält die Treffermenge nur Events mit
   `untere Grenze ≤ seq ≤ obere Grenze`; beide Grenzen sind inklusiv. Ist nur
   eine Grenze gesetzt, filtert sie einseitig. Events ohne ganzzahlige `seq`
   erfüllen einen aktiven Bereichsfilter nicht. Ohne Bereichsangabe verhält
   sich der Raw-Tab exakt wie bisher. (A1)

2. Der Seq-Bereich wird serverseitig mit den bestehenden Raw-Filtern
   komponiert: ein Event erscheint nur, wenn es zugleich den Bereich, den
   Freitextfilter `q` und den Typfilter `type` erfüllt. Die `limit`-Fensterung
   wird erst auf die vollständig gefilterte Treffermenge angewandt; der
   ausgewiesene `total`-Wert beschreibt weiterhin die Treffermenge vor der
   Fensterung. Ein aktiver Bereich verändert weder die Volltextsuche über den
   vollständigen Payload noch die Erreichbarkeit vollständiger Payloads. (A1)

3. Die im Raw-Tab angebotene `types`-Liste bleibt die volle Typmenge des Logs
   (wie heute), auch wenn ein Bereichsfilter aktiv ist. (A1)

4. Ungültige Bereichswerte ergeben eine definierte Antwort — nie ein
   Traceback, nie ein 5xx; gleichzeitig gesetzte `q`-/`type`-/`limit`-Werte
   bleiben wirksam: eine nicht-numerische Grenze wird wie eine fehlende
   Grenze behandelt (diese Grenze inaktiv — konsistent mit der Toleranz der
   bestehenden Raw-Parameter, vgl. `_parse_limit`/`_parse_offset`); eine
   obere Grenze kleiner als die untere ergibt eine definierte leere
   Treffermenge mit `total` 0. (E4)

5. Jeder Span-Knoten im Trace-Baum bietet einen Absprung in den bestehenden
   Raw-Tab. Der Absprung aktiviert den Raw-Tab und setzt den inklusiven
   Bereich auf die bereits exponierten Werte `[seq, end_seq]` des Knotens;
   der Raw-Tab zeigt damit alle Events, deren `seq` in diesem Intervall
   liegt — unter den übrigen Raw-Filtern und im Rahmen von `limit`. Der
   Filter ist ein reiner Seq-Bereichsfilter: Events verschränkter/paralleler
   Spans, deren `seq` in das Intervall fällt, werden nicht ausgeschlossen;
   eine strukturelle Teilbaum-Zugehörigkeitsprüfung findet nicht statt. Es
   entsteht kein zweites Raw-Widget im Detail-Pane. (A2, E5)

6. Beim Absprung bleiben vorhandene Raw-Werte für `q`, `type` und `limit`
   unverändert erhalten; der Seq-Bereich wird zusätzlich gesetzt. (A2)

7. Ein aktiver Seq-Bereich ist im Raw-Tab mit seinen aktiven Grenzen sichtbar,
   und der Raw-Tab bietet ein Aufheben an. Das Aufheben entfernt
   ausschließlich die Seq-Grenzen; `q`, `type` und `limit` behalten ihre
   aktuellen Werte. Ohne aktiven Bereich wird kein Bereichszustand
   behauptet. (A2)

8. Der Vorgänger wird rein strukturell bestimmt, BEVOR die Verwertbarkeit
   seines Prompts eine Rolle spielt: unter den früheren `agent.run`-Starts
   DESSELBEN Laufs mit demselben `agent`-String und derselben Lane ist der
   Vorgänger der mit der größten `seq` kleiner als die des betrachteten
   `agent.run`. Die Prompt-Verwertbarkeit filtert die Kandidaten NICHT — ein
   unmittelbarer Vorgänger mit fehlendem oder nicht-String `prompt` wird
   nicht übersprungen, ein älterer gültiger Lauf nie als Ersatz verwendet.
   Andere Läufe, Agenten oder Lanes werden nie als Vorgänger verwendet.
   (A3, E3)

9. Der Normalfall „kein Vorgänger" (der Knoten trägt `prompt_diff: null` und
   `previous_prompt_seq: null`; die Prompt-Anzeige benennt klar, dass kein
   Vorgänger verfügbar ist — nie ein geratener Ersatz, nie ein Fehler) gilt
   genau dann, wenn: der betrachtete `agent.run` keinen verwertbaren String
   für `agent` oder `prompt` hat; oder kein Kandidat nach AC 8 existiert;
   oder der nach AC 8 bestimmte unmittelbare Vorgänger keinen verwertbaren
   String-`prompt` hat. (A3, E3)

10. Existiert ein Vorgänger, enthält `previous_prompt_seq` dessen ganzzahlige
    `seq` und `prompt_diff` den serverseitig erzeugten Diff-String; ein
    identischer Prompt ergibt `prompt_diff: ""`, nicht `null`. Die drei Fälle
    „kein Vorgänger", „identischer Prompt" und „Unterschied" sind sowohl in
    der JSON-Antwort als auch sichtbar in der Oberfläche unterscheidbar.
    (A3)

11. Der Diff wird byte-genau nach diesem fixierten Format erzeugt: beide
    Prompts via `splitlines()` (ohne Zeilenenden) zerlegt;
    `difflib.unified_diff` mit `n=3`, `lineterm=""` und den Standardwerten
    für Dateinamen/Zeitstempel; die Zeilen mit `"\n"` verbunden. Ein
    Unterschied allein im abschließenden Zeilenumbruch gilt als identisch
    (leerer Diff). (A4, E2, E6)

12. Der bestehende Prompt-Tab zeigt weiterhin den vollständigen Prompt
    (`payload.prompt`, unverändert) und ergänzt den Diff beziehungsweise
    genau einen der definierten Leerzustände; die übrigen Detail-Pane-Tabs
    behalten ihr bisheriges Verhalten. (A3, Nicht-Ziele)

13. `GET /api/runs/{repo}/{run_id}` exponiert `prompt_diff` und
    `previous_prompt_seq` nur als abgeleitete Felder der serialisierten
    `agent.run`-Knoten; bestehende Felder behalten ihre Semantik. Die
    Ermittlung nutzt ausschließlich den bereits geladenen Event-Strom.
    `GET /api/runs/{repo}/{run_id}/events` bleibt unverändert: genau
    `from_seq`/`to_seq`, kein `type`, keine Paginierung, keine neuen
    Antwortfelder. (E1, E2, E3)

14. `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` dokumentieren in §7.2 den
    Seq-Bereichsfilter (Komposition, Aufhebung), den Absprung vom Span-Knoten
    sowie Vorgängerauswahl, Ausgabeformat und Leerzustände des Prompt-Diffs;
    `CHANGELOG.md` und `CHANGELOG.de.md` führen die Änderung unter
    `Unreleased`. (Doku)

## Deferred (bewusst nicht gebaut)

Nachvollziehbar, aber für die Ausgangslage unverhältnismäßig. KEINE
Akzeptanzkriterien — und bindend auch für den Review-/Codex-/Fix-Zyklus: was
hier steht, wird dort nicht nachgebaut.

- Diff zwischen beliebig gewählten Trace-Knoten oder Prompts.
- Wort- oder Zeichen-Level-Diff.
- Prompt-Vorlagen-Extraktion (Trennung stabiler Rahmen vom variablen
  Findings-Block).
- Syntax-Highlighting des Diffs.

## Definition of Done

- Alle Akzeptanzkriterien erfüllt und durch Tests unter `tests/` als
  `test_gui_*.py` abgedeckt; die Tests decken mindestens ab: inklusive
  beidseitige und einseitige Seq-Grenzen; Komposition mit `q`, `type` und
  `limit` samt `total` vor Fensterung; nicht-numerische Grenze als inaktiv
  und widersprüchlicher Bereich als leere Menge ohne 5xx; Absprung eines
  Span-Knotens mit `[seq, end_seq]` ohne zweites Raw-Widget (die Tests
  prüfen die Intervallgrenzen und fordern KEINEN Ausschluss verschränkter
  Events innerhalb des Intervalls); sichtbaren
  Bereich und dessen isolierte Aufhebung bei erhaltenen übrigen Filtern;
  Vorgängerauswahl nach Agent, Lane und größter kleinerer `seq`; „kein
  Vorgänger" bei fehlenden oder nicht-String-Daten (`prompt_diff: null`),
  darunter ein gezielter Test, dass bei einem unmittelbaren Vorgänger mit
  unverwertbarem `prompt` KEIN älterer gültiger `agent.run` als Ersatz
  verwendet wird (`prompt_diff: null` statt Diff gegen N−2);
  `prompt_diff: ""` bei identischem Prompt; byte-genaues Unified-Diff-Format
  einschließlich Gleichheit bei ausschließlich abweichendem abschließendem
  Zeilenumbruch. Clientseitiges Verhalten (Absprung, Anzeige/Aufheben) wird,
  soweit erforderlich, mit dem vorhandenen Plain-Node-Harness
  `tests/gui_js_harness.js` + `tests/gui_js_harness.py` geprüft.
- Richtwert ~14 neue Tests (Bestand: 936); mehr als ~22 gilt als Scope-Drift.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
- `GET /api/runs/{repo}/{run_id}/events` ist unverändert und weiterhin nur
  über `from_seq`/`to_seq` filterbar (E1).
- Keine neue Dependency (E2), keine Persistenz, kein neues Tab.
- Doku aktualisiert: `docs/GUI-SPEC.md`/`docs/GUI-SPEC.de.md` (§7.2) und
  `CHANGELOG.md`/`CHANGELOG.de.md` (`Unreleased`).
