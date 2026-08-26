# Plan: Kontext-Panel „Lauf-Zustand zu diesem Knoten" im Run-Detail

Single-Lane-Projekt: Es gibt nur den Workstream **backend**, keinen separaten
Frontend-Lane. Die GUI ist eine FastAPI-+-Jinja-+-Vanilla-JS-App; Template-,
und Client-Verhaltensanpassungen gehören deshalb zum Backend-Workstream.
Sowohl die JSON-Route `GET /api/runs/{repo}/{run_id}` als auch die HTML-Seite
`GET /runs/{repo}/{run_id}` konsumieren dasselbe `_run_detail(...)`-Dict — eine
einzige Ableitung speist beide Flächen.

Gebaut wird strikt gegen `.adw/contract.yaml`. Der Contract pinnt nur die
extern beobachtbare Fläche: `context` an jedem bestehenden Trace-Knoten,
`latest_context` auf oberster Ebene, deren Cutoff-/`null`-Semantik sowie das
Auswahl-, Zeitreise- und Leer-Verhalten des read-only Panels auf der
bestehenden Run-Detail-Seite. Interne Helper-Signaturen, interne
Dictionary-Schlüssel und konkretes Markup/CSS sind nicht Teil des Contracts.

Die Ableitung nutzt ausschließlich den Event-Strom, den `_run_detail()` in
`adw/gui/app.py` bereits lädt. Vorhandene Seq-Zuordnung und Kostenlogik werden
wiederverwendet; es entstehen weder Reader, Route, Event, Persistenz noch
Dateizugriff oder Laufzeit-Dependency.

## Grounding (im Code verifiziert)

- `_run_detail(...)` (`adw/gui/app.py:1110`) baut das Detail-Dict (`run`,
  `phases`, `tree`, `problems`, `raw`), berechnet bereits `events` und
  `own_ranges = _span_seq_ranges(events)` (`app.py:1117`) und serialisiert
  jeden Baum-Knoten über `_serialize(...)` (`app.py:770`).
- `_serialize` exponiert pro Span-Knoten bereits `seq` und `end_seq`
  (Subtree-Maximum via `_subtree_seq_range`, `app.py:755`), pro Punkt-Knoten
  `seq`. `end_seq` ist exakt der von der Spec geforderte Span-Cutoff (AC 2).
- Kosten-Semantik existiert für die zwei vorhandenen Formen: `_subtree_cost`
  (`app.py:637`, über Modell-Knoten) und `_events_cost` (`app.py:894`, über
  eine Event-Liste: `agent.run`/`kind==end`/`cost_usd`, `null` statt `0`).
  Die Cutoff-Ableitung ist event-listen-förmig und wendet daher die
  `_events_cost`-Semantik auf die gefilterte Liste an — eine Rechenart,
  keine zweite (E4, AC 7).
- `round`-Spans tragen `start_payload` `{loop, n, cap}` (genutzt ab
  `app.py:805`).
- `state.saved` trägt nur `{seq, phase}` (Issue, verifiziert) — wird rein als
  dieses Payload ausgewertet, nie als Zustands-Dump, nie erweitert (E1, AC 4).
- Die HTML-Seite (`run_detail_page`, `app.py:1432`) rendert die Knoten
  serverseitig; `static/app.js` toggelt `.selected` bei Auswahl. Keine
  clientseitige Neu-Ableitung — die Panel-Daten reisen pro Knoten im Render mit.

## Workstream: backend

### B1 — Kontextmodell und Cutoff-Ableitung in `adw/gui/app.py` (AC 1, 2, 10, 11)

- Eine rein abgeleitete Kontextstruktur mit genau den sechs Feldern `phase`,
  `round`, `limit_hits`, `circuit_breakers`, `cost_usd`, `followups` erzeugen.
- Für Punkt-Ereignisse deren `seq` als Cutoff verwenden. Für Span-Knoten das
  bereits in der Baum-Antwort exponierte `end_seq` (Subtree-Maximum; bei
  laufenden Spans die höchste bislang im Subtree beobachtete `seq`).
- Für jeden Cutoff ausschließlich Events mit `seq <= cutoff` berücksichtigen.
  Dadurch enthalten abgeschlossene und laufende Span-Kontexte auch passende
  Ereignisse innerhalb ihres Subtrees nach dem Span-Start, aber keine
  späteren Ereignisse.
- Die vorhandene Seq-Zuordnung (`_span_seq_ranges()`, `_subtree_seq_range()`)
  wiederverwenden; keine alternative Span- oder Cutoff-Semantik einführen.
- „Nie gesehen" von „null-mal gesehen" unterscheiden: Zähl- und Kostenfelder
  starten als `None` und werden erst beim ersten Vorkommen zur Zahl — nie ein
  erfundenes `0`.
- Unvollständige oder ungültige Payloads defensiv behandeln: Nur das jeweils
  betroffene Feld bleibt leer; die Detail-Antwort darf deshalb nicht mit
  HTTP 5xx scheitern.

### B2 — Phasenstand bis zum Cutoff (AC 4, 10)

- `phase` ausschließlich aus zwei vorhandenen Quellen ableiten: dem
  nichtleeren Start-Payload-Feld `name` eines `phase`-Spans und dem
  nichtleeren Payload-Feld `phase` eines `state.saved`-Ereignisses.
  Leere/fehlende Werte zählen nicht als Beobachtung.
- Von allen gültigen Beobachtungen bis einschließlich Cutoff gewinnt die mit
  der höchsten Event-`seq` (Seqs sind eindeutig — kein Gleichstand).
- `state.saved` ausschließlich gemäß seinem bestehenden Payload `{seq, phase}`
  auswerten; weder als Zustands-Dump behandeln noch erweitern (E1).
- Ohne gültige Phasenbeobachtung `phase: null`.

### B3 — Umgebende Runde am Cutoff (AC 5, 10)

- `round` ist die innerste `round`, deren Span-Seq-Bereich den Cutoff enthält
  (eigener `round`-Knoten eingeschlossen), als `{loop, n, cap}` aus dem
  `start_payload` dieses Spans. Fehlende Einzelwerte bleiben `null`; nichts
  ergänzen oder schätzen.
- Die bestehenden Span-Bereiche aus `_span_seq_ranges()` nutzen; „enthält"
  heißt Start- bis End-Event einschließlich — eine gerade startende oder
  gerade endende Runde wird noch gezeigt.
- Für `latest_context` ist der Cutoff die höchste Event-`seq`; dasselbe
  Containment liefert `round: null`, sobald das jüngste Event außerhalb jeder
  Runde liegt (etwa nach deren Ende).

### B4 — Zähl- und Kostenfelder bis zum Cutoff (AC 6, 7, 8, 10)

- `limit_hits`, `circuit_breakers`, `followups` als Anzahl der bis
  einschließlich Cutoff protokollierten `limit.hit`-, `circuit_breaker`- bzw.
  `followup`-Ereignisse; ohne ein solches Ereignis `null`, nicht `0`.
- `cost_usd` als kumulierte Summe der bis einschließlich Cutoff
  protokollierten Kosten abgeschlossener `agent.run`-Ereignisse: die
  Event-Liste auf `seq <= cutoff` filtern und darauf dieselbe Kosten-Semantik
  wie `_events_cost` anwenden — keine zweite Kosten-Rechenart (E4).
- Ohne verwertbaren Kostenwert `cost_usd: null`; ungültige Werte werden nicht
  in erfundene Kosten umgewandelt.

### B5 — Antwort von `GET /api/runs/{repo}/{run_id}` erweitern (AC 1, 2, 3, 10, 11)

- In `_serialize` jedem Knoten sein `context` anhand seines Cutoffs anhängen
  (`end_seq` für Spans, `seq` für Punkt-Ereignisse); die Ableitung so
  durchreichen, wie `own_ranges`/`snaps` bereits durchgereicht werden — keine
  Modelländerung.
- In `_run_detail` auf oberster Ebene `latest_context` ergänzen, abgeleitet
  bis zur höchsten Event-`seq` (bzw. mit sechs `null`-Feldern, wenn keine
  Events existieren).
- Lauf ohne Trace: leerer `tree`, daher kein per-Knoten-`context` und KEIN
  top-level `context`-Feld — nur `latest_context` mit sechs `null`-Feldern;
  nie ein synthetischer Knoten, nie ein 5xx (AC 10).
- Jeder `context` hat EXAKT die sechs Schlüssel — nicht mehr, nicht weniger
  (E3). Bestehende Antwortfelder und Routen behalten ihre Semantik; Timeline,
  Artifacts, Raw, Diff und SSE bleiben unberührt.

### B6 — Read-only Kontext-Panel im Run-Detail (AC 3, 9, 10)

- In `run_detail.html` neben dem Detail-Pane eine feste read-only Feldliste
  für die sechs Kontextfelder rendern, pro Knoten aus dessen `context`
  gespeist. Nur eine Feldliste — kein Diagramm, keine Verlaufskurve (E5).
- Auswahl-Verdrahtung in `static/app.js` über den bestehenden
  Auswahlmechanismus (`.selected`-Toggle): Bei Auswahlwechsel aktualisiert
  das Panel alle Felder auf den `context` des neu gewählten Knotens
  (Zeitreise); ohne Auswahl zeigt es `latest_context` (Live-Lauf). Bestehende
  Aktualisierungsmechanismen verwenden; keine SSE-Erweiterung, keine
  clientseitige Neu-Ableitung. Die Verdrahtung bleibt — wie das übrige
  `app.js` — über den bestehenden JS-Harness in plain `node` ausführbar
  testbar (siehe Tests).
- `null`-Werte leer anzeigen — nie als numerische Null oder geschätzter Wert.
- Feldbeschriftungen laufen wie alle Chrome-Texte der Seite über den
  bestehenden i18n-Katalog (`adw/gui/i18n.py`, `CATALOG[lang]`, im Code
  verifiziert) — bestehende Konvention, kein neuer Mechanismus: hartkodierte
  Labels wären in einer der beiden Sprachen falsch.
- Die vorhandenen Detail-Pane-Tabs und deren Inhalte bleiben unverändert.
  Kein konfigurierbares Feld-Set, keine Nutzereinstellung, keine
  Auswahlpersistenz. Konkretes Markup/CSS bleibt Implementierungsdetail.

### B7 — Dokumentation und Changelog (AC 12)

- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron in §7.2: das Panel,
  die Zeitreise-Semantik (Cutoff = Knoten-`seq` bzw. Span-`end_seq`), der
  `latest_context`-Fallback ohne Auswahl und die Leer-Semantik
  (`null` statt `0`, leere Anzeige).
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron unter `Unreleased`.

## Tests (unter `tests/` als `test_gui_*.py`)

Richtwert ~12 neue Tests (Bestand: 892); mehr als ~20 gilt als Scope-Drift.

Abdeckung der Ableitungs-Semantik über die JSON-Antwort von
`GET /api/runs/{repo}/{run_id}` bzw. `_run_detail`:

- Kontext pro Punkt-Knoten berücksichtigt ausschließlich Events bis zu dessen
  `seq`.
- Span-Kontext verwendet `end_seq`/Subtree-Maximum und enthält ein Kosten-,
  `limit.hit`- und `followup`-Ereignis innerhalb des Spans nach dessen
  Start-`seq`.
- Ein späteres Ereignis beeinflusst einen früheren Knoten-Kontext nicht;
  Auswahlwechsel zeigt den jeweiligen historischen Kontext (Zeitreise).
- Phasen-Präzedenz zwischen `state.saved.phase` und `phase`-Span-Start nach
  höchster `seq` (Beispiel der Spec: seq 30 `spec` vs. seq 40 `plan`);
  fehlende oder leere Beobachtungen ergeben `phase: null`.
- Round-Zuordnung: eigener bzw. umgebender `round`-Span, fehlende
  Einzelwerte, `round: null` außerhalb einer Runde sowie
  `latest_context.round` innerhalb vs. nach Ende einer Runde.
- `limit_hits`, `circuit_breakers`, `followups` zählen nur tatsächlich bis
  zum Cutoff protokollierte Ereignisse und sind ohne Beobachtung `null`,
  nicht `0`.
- `cost_usd` folgt der bestehenden Kosten-Semantik, summiert nur verwertbare
  abgeschlossene `agent.run`-Kosten bis zum Cutoff und ist ohne verwertbaren
  Wert `null`.
- `latest_context` ist ohne Auswahl maßgeblich und spiegelt den Stand bis zur
  höchsten Event-`seq`.
- Jeder Knoten-`context` und `latest_context` enthält genau die sechs
  vereinbarten Felder.
- Ein Lauf ohne Trace liefert ohne Fehler keinen synthetischen Knoten, kein
  top-level `context` und ein `latest_context` mit sechs `null`-Feldern.
- Fehlende Eventtypen und unvollständige Payloads verursachen weder erfundene
  Nullwerte noch HTTP 5xx.

Abdeckung des beobachtbaren Panel-Verhaltens:

- Markup-Ebene, über die gerenderte HTML-Seite (`GET /runs/{repo}/{run_id}`)
  im bestehenden GUI-Test-Stil: Die Seite enthält das Panel als read-only
  Feldliste neben dem Detail-Pane; pro gerendertem Knoten reisen dessen
  `context`-Daten mit, der `latest_context`-Fallback ist im Markup verfügbar.
- Verhaltens-Ebene, AUSFÜHRBAR clientseitig über den bestehenden JS-Harness
  (`tests/gui_js_harness.js`, gefahren aus pytest via `run_scenario` in
  `tests/gui_js_harness.py`): Der Harness treibt das SERVIERTE `app.js` in
  plain `node` gegen repräsentative DOM-Fixtures aus Panel und Knoten — kein
  Browser-Framework, keine neue Dependency; `node` ist bereits
  verpflichtendes Verifikationswerkzeug des Test-Gates. Neue Szenarien
  assertieren den Auswahl-Handler direkt:
  - initial, ohne Auswahl, zeigt das Panel `latest_context`;
  - Auswahl eines Knotens aktualisiert alle sechs Felder auf dessen `context`;
  - Auswahlwechsel aktualisiert erneut alle sechs Felder (Zeitreise);
  - Aufheben der Auswahl fällt auf `latest_context` zurück;
  - `null`-Werte bleiben auch nach diesen Übergängen leer — nie `0`, nie ein
    geschätzter Wert.

## Gates (Definition of Done)

- Alle Akzeptanzkriterien durch die beschriebenen Tests und den
  Änderungsumfang abgedeckt.
- `uv run ruff check .` grün.
- `uv run pytest -x -q` grün.
- EN/DE-Dokumentation und Changelog-Einträge synchron.
- Keine neue Laufzeit-Dependency, kein CDN.
- Keine neue Route, kein Reader, kein zusätzlicher Datei-Zugriff, kein Event,
  kein Persistenzzustand, keine SSE-Protokolländerung.
- Timeline, Artifacts, Raw, Diff und bestehende Detail-Pane-Tabs unverändert.
- Kein unter „Deferred (bewusst nicht gebaut)" genannter Mechanismus ist
  Bestandteil der Änderung.

## Deferred (bewusst nicht gebaut)

Nachvollziehbar, aber für die Ausgangslage unverhältnismäßig. KEINE
Akzeptanzkriterien — und bindend auch für den Review-/Codex-/Fix-Zyklus:
was hier steht, wird dort nicht nachgebaut.

- Verlauf/Zeitreihe einer Kennzahl über den gesamten Lauf (Kurve, Sparkline).
- Vergleich des Zustands zweier Knoten nebeneinander.
- Export des berechneten Lauf-Zustands (Datei/Clipboard/Route).
- Erweiterung des Panels um weitere Events, Kennzahlen oder frei wählbare
  Felder.
