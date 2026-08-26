# Plan: Vom Knoten in den Raw-Log springen + Prompt-Diff gegen die Vorrunde

Single-Lane-Projekt: Es gibt nur den Workstream **backend**, keinen separaten
Frontend-Lane. Die GUI ist eine FastAPI-+-Jinja-+-Vanilla-JS-App; Template-
und Client-Verhaltensanpassungen gehören deshalb zum Backend-Workstream.
Sowohl die JSON-Route `GET /api/runs/{repo}/{run_id}` als auch die HTML-Seite
`GET /runs/{repo}/{run_id}` konsumieren dasselbe `_run_detail(...)`-Dict — eine
einzige Ableitung speist beide Flächen.

Gebaut wird strikt gegen `.adw/contract.yaml`. Beide Funktionen sind rein
abgeleitete Projektionen des bereits geladenen Event-Stroms: kein neuer Reader,
keine neue Route, kein neues Event, keine Persistenz, keine neue Dependency
(`difflib` ist Standardbibliothek). Der Contract pinnt nur die extern
beobachtbare Fläche: das Seq-Bereichsfilter-Verhalten des Raw-Tabs samt
Komposition mit `q`/`type`/`limit` und Verhalten bei ungültigen Werten, die
neuen abgeleiteten Antwortfelder `prompt_diff`/`previous_prompt_seq` an
serialisierten `agent.run`-Knoten in `GET /api/runs/{repo}/{run_id}`, sowie das
beobachtbare Verhalten von Absprung, Bereichsanzeige/-aufhebung und
Prompt-Diff-Anzeige auf der bestehenden Run-Detail-Seite. Die Events-Route
`GET /api/runs/{repo}/{run_id}/events` wird NICHT angefasst (E1). Interne
Helper-Signaturen, interne Dictionary-Schlüssel und konkretes Markup/CSS sind
nicht Teil des Contracts.

## Grounding (im Code verifiziert)

- Der Raw-Tab wird von `_raw_view(events, limit, *, q=None, type_filter=None)`
  (`adw/gui/app.py:870`) gebaut: er filtert serverseitig über den vollständig
  serialisierten Payload (`q`) und den Ereignistyp (`type`), fenstert über
  `limit` und meldet `total` als Größe der VOLLSTÄNDIGEN Treffermenge vor der
  Fensterung. `types` ist die volle Typmenge des Logs, unabhängig von den
  aktiven Filtern. Einen Seq-Bereichsfilter hat er nicht.
- `_raw_view` wird aus `_run_detail(...)` (`adw/gui/app.py:1289`) mit `raw_q`,
  `raw_type`, `limit` aufgerufen. Die HTML-Seite `run_detail_page`
  (`adw/gui/app.py:1622`) liest `raw_q`/`raw_type` aus den Query-Parametern
  (`app.py:1640`); die JSON-Route `api_run_detail` (`app.py:1538`) ruft
  `_run_detail` ohne Raw-Filter auf — der Seq-Bereich betrifft daher nur die
  HTML-Seite.
- `_parse_limit` (`app.py:55`) und `_parse_offset` (`app.py:74`) definieren die
  bestehende Toleranz für Raw-Parameter: nicht-numerische Werte fallen still
  auf einen Default zurück, kein 5xx. Die Seq-Grenzen folgen exakt dieser
  Konvention (E4, AC 4).
- Die Bereichsinformation je Knoten existiert bereits: `_span_seq_ranges()`
  (`app.py:715`) und `_subtree_seq_range()` (`app.py:781`); jeder serialisierte
  Span-Knoten exponiert sein Subtree-Maximum bereits als `end_seq`
  (`_serialize`, `app.py:796`). Der Absprungbereich `[seq, end_seq]` ist damit
  ohne neue Ableitung verfügbar (AC 5).
- `_serialize` (`app.py:796`) erhält beim rekursiven Abstieg die aktuelle
  `lane` (aus dem umschließenden `lane`-Span mit `payload.name`). Der
  `agent`-String und der `prompt` eines `agent.run` liegen im Start-Payload
  (der Prompt-Tab zeigt `payload.prompt`). Lane und Prompt eines `agent.run`
  sind damit während der Serialisierung bekannt.
- Die HTML-Seite rendert die Knoten serverseitig; `static/app.js` schaltet die
  Detail-Pane-Tabs und die `.selected`-Auswahl. Der bestehende Raw-Tab ist ein
  Tab dieses Panes — der Absprung ist ein Wechsel dorthin, kein zweites Widget
  (E5). Clientseitiges Verhalten ist über den JS-Harness
  (`tests/gui_js_harness.js` + `tests/gui_js_harness.py`, plain `node`) testbar.

## Workstream: backend

### B1 — Seq-Grenzen parsen und durchreichen (AC 1, 4; E4)

- Die HTML-Detail-Route liest die zwei neuen optionalen Query-Parameter
  `raw_from_seq` und `raw_to_seq` (Namen im Contract fixiert), tolerant nach
  dem Muster von `_parse_limit`/`_parse_offset`: eine fehlende ODER
  nicht-numerische Grenze ist eine inaktive Grenze (einseitig oder gar kein
  Bereich), nie ein Fehler, nie ein 5xx.
- Die geparsten Grenzen an `_run_detail`/`_raw_view` durchreichen, analog zu
  `raw_q`/`raw_type`. Die JSON-Route und die Events-Route bleiben unberührt.

### B2 — Seq-Bereichsfilter im Raw-Tab (AC 1, 2, 3, 4; E4)

- `_raw_view` um den inklusiven Seq-Bereichsfilter erweitern: ein Event bleibt
  in der Treffermenge nur, wenn seine ganzzahlige `seq` — sofern eine Grenze
  aktiv ist — `untere Grenze ≤ seq ≤ obere Grenze` erfüllt. Bei aktivem Bereich
  erfüllt ein Event OHNE ganzzahlige `seq` den Filter nicht.
- Der Bereich wird MIT den bestehenden Filtern komponiert (logisches UND): ein
  Event erscheint nur, wenn es zugleich Bereich, `q` (weiterhin über den
  vollständigen Payload) und `type` erfüllt. Die `limit`-Fensterung wird erst
  auf die vollständig gefilterte Menge angewandt; `total` bleibt die Größe der
  Treffermenge VOR der Fensterung.
- `types` bleibt unverändert die volle Typmenge des Logs, auch bei aktivem
  Bereich (AC 3). Vollständige Payloads bleiben über die Events-Route
  erreichbar (AC 2).
- Ist nur eine Grenze gesetzt, filtert sie einseitig. Ist die obere Grenze
  kleiner als die untere, ergibt sich eine definierte leere Treffermenge mit
  `total` 0 — kein Sonderfall-Code, natürliche Folge des Prädikats (AC 4).
- Ohne Bereichsangabe verhält sich `_raw_view` byte-identisch wie bisher.

### B3 — Absprung, Bereichsanzeige und -aufhebung im Raw-Tab (AC 5, 6, 7; E5)

- Jeder Span-Knoten im Trace-Baum bekommt eine Bedienmöglichkeit, die in den
  bestehenden Raw-Tab wechselt und dessen Seq-Bereich auf die bereits
  exponierten Knotenwerte `[seq, end_seq]` setzt. Es entsteht kein zweites
  Raw-Widget; der bestehende Raw-Tab wird aktiviert. Der Filter ist ein reiner
  Seq-Bereichsfilter — keine strukturelle Teilbaum-Zugehörigkeitsprüfung;
  Events verschränkter/paralleler Spans innerhalb des Intervalls werden nicht
  ausgeschlossen (AC 5).
- Beim Absprung bleiben vorhandene `q`-/`type`-/`limit`-Werte erhalten; nur der
  Seq-Bereich wird zusätzlich gesetzt (AC 6). Umgesetzt über die bestehende
  query-parameter-getriebene Render-Mechanik der Seite (Absprung-Ziel trägt die
  bestehenden Raw-Parameter mit den zusätzlichen Seq-Grenzen).
- Die aktiven Grenzen werden beim Rendern des bestehenden Raw-Filterformulars
  und des bestehenden „mehr laden"-Mechanismus mitgeführt, sodass eine Änderung
  an `q`, `type` oder `limit` den aktiven Bereich nicht unbeabsichtigt
  entfernt — nur das explizite Aufheben entfernt ihn (AC 7).
- Ein aktiver Seq-Bereich wird im Raw-Tab mit seinen aktiven Grenzen sichtbar
  angezeigt (einseitige Bereiche entsprechend einseitig), samt einer
  Bedienmöglichkeit zum Aufheben. Das Aufheben entfernt ausschließlich die
  Seq-Grenzen; `q`, `type` und `limit` behalten ihre Werte (AC 7). Ohne
  aktiven Bereich wird kein Bereichszustand behauptet.
- Feld-/Steuerbeschriftungen laufen wie alle Chrome-Texte über den bestehenden
  i18n-Katalog (`adw/gui/i18n.py`) — bestehende Konvention, kein neuer
  Mechanismus.

### B4 — Vorgänger-Ermittlung des `agent.run` (AC 8, 9; E3)

- Aus dem bereits geladenen Baum/Event-Strom einen Index aller `agent.run`-
  Starts bilden, jeweils mit `seq`, `agent`-String, serialisierungszeitlicher
  Lane und `prompt`. Die Lane stammt aus dem umschließenden `lane`-Span (wie in
  `_serialize` bereits geführt), NICHT aus einer neuen Quelle.
- Der Vorgänger eines betrachteten `agent.run` wird rein STRUKTURELL bestimmt,
  BEVOR Prompt-Verwertbarkeit eine Rolle spielt: unter den früheren
  `agent.run`-Starts DESSELBEN Laufs mit demselben `agent`-String und derselben
  Lane ist der Vorgänger der mit der größten `seq` kleiner als die des
  betrachteten Knotens. Andere Läufe, Agenten oder Lanes werden nie als
  Vorgänger verwendet (E3).
- Die Prompt-Verwertbarkeit filtert die Kandidaten NICHT: ein unmittelbarer
  Vorgänger mit fehlendem oder nicht-String-`prompt` wird nicht übersprungen,
  ein älterer gültiger Lauf nie als Ersatz verwendet (AC 8; gezielter Test).
- Hat der betrachtete `agent.run` selbst keinen verwertbaren String für
  `agent` oder `prompt`, existiert kein Kandidat, oder hat der so bestimmte
  unmittelbare Vorgänger keinen verwertbaren String-`prompt`, dann gilt der
  Normalfall „kein Vorgänger": `prompt_diff: null` und
  `previous_prompt_seq: null` — insbesondere wird die `seq` eines unbrauchbaren
  unmittelbaren Vorgängers nie als „verwendet" ausgewiesen. Nie ein geratener
  Ersatz, nie ein Fehler (AC 9).

### B5 — Diff-Erzeugung und Antwortfelder (AC 10, 11, 13; E2, E6)

- Existiert ein verwertbarer Vorgänger, `previous_prompt_seq` = dessen
  ganzzahlige `seq` und `prompt_diff` = der serverseitig erzeugte Diff-String;
  `previous_prompt_seq` bleibt auch bei leerem Diff gesetzt.
- Der Diff wird byte-genau nach fixiertem Format erzeugt: beide Prompts via
  `splitlines()` (ohne Zeilenenden) zerlegt; `difflib.unified_diff` mit dem
  Vorgänger-Prompt als erstem und dem aktuellen Prompt als zweitem Argument,
  `n=3`, `lineterm=""` und den Standardwerten für Dateinamen/Zeitstempel; die
  Zeilen mit `"\n"` verbunden. Ein identischer Prompt ergibt `prompt_diff: ""`
  (nicht `null`); ein Unterschied allein im abschließenden Zeilenumbruch gilt
  als identisch (leerer Diff) (AC 10, 11; E6).
- Nur `difflib` aus der Standardbibliothek; keine Diff-Bibliothek, kein
  Syntax-Highlighter, kein Frontend-Paket (E2).
- In `_serialize` jedem `agent.run`-Knoten `prompt_diff` und
  `previous_prompt_seq` als rein abgeleitete Felder anhängen; die Ableitung so
  durchreichen, wie `own_ranges`/`snaps` bereits durchgereicht werden — keine
  Modelländerung. Andere Knotentypen erhalten die Felder nicht. Bestehende
  Felder behalten ihre Semantik. Die Ermittlung nutzt ausschließlich den
  bereits geladenen Event-Strom (AC 13).

### B6 — Prompt-Diff-Anzeige im Prompt-Tab (AC 10, 12; Nicht-Ziele)

- Der bestehende Prompt-Tab zeigt weiterhin den vollständigen Prompt
  (`payload.prompt`, unverändert) und ergänzt zusätzlich den Diff bzw. genau
  einen der drei unterscheidbaren Zustände:
  - „kein Vorgänger" (`prompt_diff: null`, `previous_prompt_seq: null`) —
    klare Aussage, dass kein Vorgänger verfügbar ist, kein Fehler, kein
    geratener Ersatz;
  - „identischer Prompt" (`prompt_diff: ""` mit ganzzahliger
    `previous_prompt_seq`) — als eigener, vom vorigen unterscheidbarer
    Leerzustand;
  - „Unterschied" (nichtleerer `prompt_diff` mit ganzzahliger
    `previous_prompt_seq`) — der Diff-String.
- Die übrigen Detail-Pane-Tabs (Timeline, Artifacts, Raw als Struktur, Diff,
  Kontext) behalten ihr bisheriges Verhalten. Kein neues Tab, kein
  Prompt-Editor, kein Zwischenablage-Subsystem, keine Persistenz.
- Client-Verdrahtung über den bestehenden Auswahl-/Tab-Mechanismus in
  `static/app.js`; keine clientseitige Neu-Ableitung, keine SSE-Erweiterung.
  Neue sichtbare Texte über den bestehenden EN/DE-i18n-Katalog. Konkretes
  Markup/CSS bleibt Implementierungsdetail.

### B7 — Dokumentation und Changelog (AC 14)

- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron in §7.2: der
  Seq-Bereichsfilter (Komposition mit `q`/`type`/`limit`, Aufhebung), der
  Absprung vom Span-Knoten auf `[seq, end_seq]` sowie Vorgängerauswahl,
  fixiertes Ausgabeformat und die drei Leer-/Diff-Zustände des Prompt-Diffs.
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron unter `Unreleased`.

## Tests (unter `tests/` als `test_gui_*.py`)

Richtwert ~14 neue Tests (Bestand: 936); mehr als ~22 gilt als Scope-Drift.

Serverseitige Semantik (über die gerenderte Raw-Ansicht bzw. `_raw_view`/
`_run_detail`):

- Inklusive beidseitige Seq-Grenzen sowie jeweils einseitige untere und obere
  Grenze.
- Komposition des Bereichs mit `q`, `type` und `limit`, samt `total` als Größe
  der Treffermenge VOR der `limit`-Fensterung; `types` bleibt die volle
  Typmenge auch bei aktivem Bereich.
- Nicht-numerische Grenze wird als inaktiv behandelt (verhält sich wie keine
  Grenze); widersprüchlicher Bereich (obere < untere) ergibt eine definierte
  leere Menge mit `total` 0 — kein 5xx; gleichzeitig gesetzte
  `q`/`type`/`limit` bleiben wirksam.
- Events ohne ganzzahlige `seq` erfüllen einen aktiven Bereichsfilter nicht.

Vorgänger und Prompt-Diff (über die JSON-Antwort von
`GET /api/runs/{repo}/{run_id}` bzw. `_run_detail`):

- Vorgängerauswahl nach `agent`-String, Lane und größter kleinerer `seq`;
  andere Läufe/Agenten/Lanes werden nie als Vorgänger verwendet.
- „kein Vorgänger" (`prompt_diff: null`, `previous_prompt_seq: null`) bei
  fehlenden oder nicht-String-Daten für `agent`/`prompt` bzw. fehlendem
  Kandidaten.
- Gezielter Test: ein unmittelbarer Vorgänger mit unverwertbarem `prompt`
  ergibt `prompt_diff: null` — KEIN älterer gültiger `agent.run` wird als
  Ersatz gegen N−2 verwendet.
- `prompt_diff: ""` bei identischem Prompt (mit gesetzter
  `previous_prompt_seq`), unterscheidbar vom Fall „kein Vorgänger".
- Die Felder `prompt_diff`/`previous_prompt_seq` erscheinen ausschließlich an
  `agent.run`-Knoten, an keinem anderen Knotentyp (AC 13; Assertion innerhalb
  der obigen Serialisierungs-Tests, kein eigener zusätzlicher Test).
- Byte-genaues Unified-Diff-Format (`splitlines()`, `unified_diff` mit `n=3`,
  `lineterm=""`, Join mit `"\n"`), einschließlich Gleichheit bei
  ausschließlich abweichendem abschließendem Zeilenumbruch.

Beobachtbares Client-/Markup-Verhalten:

- Markup-Ebene über die gerenderte HTML-Seite (`GET /runs/{repo}/{run_id}`):
  Absprung eines Span-Knotens setzt den Bereich auf `[seq, end_seq]` (die Tests
  prüfen die Intervallgrenzen und fordern KEINEN Ausschluss verschränkter
  Events innerhalb des Intervalls); der aktive Bereich ist mit seinen Grenzen
  sichtbar; die Bereichsaufhebung entfernt nur die Grenzen und erhält
  `q`/`type`/`limit`; es entsteht kein zweites Raw-Widget.
- Verhaltens-Ebene, soweit erforderlich AUSFÜHRBAR clientseitig über den
  bestehenden JS-Harness (`tests/gui_js_harness.js`, gefahren aus pytest via
  `run_scenario` in `tests/gui_js_harness.py`, plain `node`, keine neue
  Dependency): Absprung aktiviert den Raw-Tab mit gesetztem Bereich unter
  Erhalt der übrigen Filter; die Prompt-Anzeige unterscheidet die drei
  Zustände „kein Vorgänger", „identischer Prompt" und „Unterschied".

## Gates (Definition of Done)

- Alle Akzeptanzkriterien durch die beschriebenen Tests und den
  Änderungsumfang abgedeckt.
- `uv run ruff check .` grün.
- `uv run pytest -x -q` grün.
- EN/DE-Dokumentation und Changelog-Einträge synchron.
- `GET /api/runs/{repo}/{run_id}/events` unverändert und weiterhin nur über
  `from_seq`/`to_seq` filterbar (E1).
- Keine neue Dependency (E2, nur `difflib`), keine Persistenz, kein neues Tab,
  kein neuer Reader, keine neue Route, kein neues Event, keine
  SSE-Protokolländerung.
- Timeline, Artifacts, Diff-Tab und SSE unverändert.
- Kein unter „Deferred (bewusst nicht gebaut)" genannter Mechanismus ist
  Bestandteil der Änderung.

## Deferred (bewusst nicht gebaut)

Nachvollziehbar, aber für die Ausgangslage unverhältnismäßig. KEINE
Akzeptanzkriterien — und bindend auch für den Review-/Codex-/Fix-Zyklus: was
hier steht, wird dort nicht nachgebaut.

- Diff zwischen beliebig gewählten Trace-Knoten oder Prompts.
- Wort- oder Zeichen-Level-Diff.
- Prompt-Vorlagen-Extraktion (Trennung stabiler Rahmen vom variablen
  Findings-Block).
- Syntax-Highlighting des Diffs.
