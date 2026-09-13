# Plan — GUI-Redesign 3: Das Arbeitsfeld zuerst

Single-Lane-Projekt (`.adw/config.yaml`): nur der Workstream **backend**. Er
umfasst hier auch Templates, CSS, i18n und Doku — die GUI-Assets sind Teil des
Python-Pakets; es gibt keinen frontend-Lane.

Setzt auf dem gemergten Stand **0.23.0** auf. Tokens, Skalen, Signalfarbe, Dark
Mode, die Zeitachse (Brief 1) und die Zeitgrößen samt Vokabular (Brief 2) werden
**benutzt, nicht revidiert** (E4/E5). Rein darstellend — kein Vertragswechsel.

Betroffene Dateien (aus der Spec, abschließend):
`adw/gui/templates/run_detail.html`, `adw/gui/app.py` (nur render-seitige
Zusammenfassungswerte und die Balkenbeschriftung), `adw/gui/i18n.py`,
`adw/gui/static/app.css`, `tests/test_gui_*.py` (neue Tests) sowie der Kommentar
in `tests/test_gui_bounded_dom.py`, `docs/GUI-SPEC.md`, `docs/GUI-SPEC.de.md`,
`CHANGELOG.md`, `CHANGELOG.de.md`.

## Contract-Fläche (`.adw/contract.yaml`)

Der Contract pinnt für diesen Brief **die Unveränderlichkeit** der beiden
JSON-Routen `/api/runs` und `/api/runs/{repo}/{run_id}` in allen Feldern, Typen
und Werten (AC 12) sowie das extern beobachtbare Template-Verhalten der
HTML-Seite `GET /runs/{repo}/{run_id}`: das reine Auf-/Zuklappen ohne
JavaScript, die dauerunabhängig lesbare Balkenbeschriftung bei unveränderter
Balkengeometrie und die vollständige, ungefensterte Baum-Spalte (ein
`data-tree-entry` je Knoten, `?offset` inert; 200er-Schranke nur für
`data-tool-entry`). **Nicht** Contract: Klassennamen, Reihenfolge der Blöcke im
Markup, Markup-/CSS-Wortlaut, Grid-Verhältnisse, interne Helfer-Signaturen.

**Bindender Architektur-Fakt (AC 12):** Das `detail`-Objekt aus `_run_detail`
(`adw/gui/app.py:2344`) speist die JSON-Antwort (`api_run_detail`,
Zeile 2830 ff.) UND den Template-Kontext (`run_detail_page`, Zeile 2933 ff.).
Die zwei neuen Zusammenfassungswerte aus A2 dürfen deshalb **nicht** in
`detail` (und damit nicht in die API) geschrieben werden; sie werden als reine
**Render-Kontext**-Variablen in `run_detail_page` berechnet und übergeben — wie
`compact`, `timeline`, `pane_nodes` es heute schon tun. `change_scope`,
`plan_skeleton`, `tree`, `phases`, `raw`, `latest_context`, `problems` und die
Kennzahlen aus Brief 2 bleiben wörtlich.

## Workstream: backend

### B1 — Messbasis prüfen und Erwartungen sichern (vor dem Bauen)

Die Issue-Messwerte (2026-09-13, Lauf `16f39431`, 1440 px, Stand 0.23.0) sind
die Vergleichsbasis und vor dem Bauen gegen den dann aktuellen Stand zu prüfen:
Kopf 169 px, „Planned tasks" 257 px, „Change scope" 493 px, Baumbeginn bei
1023 px; Grid `1fr 1.4fr minmax(9rem, 0.7fr)` → 434/607/304 px; 103 von 577
Beschriftungen (17,8 %) umbrechen; 24 von 31 Balken schneiden ab (gemessen
`scrollWidth > clientWidth`); 844 Knoten = 844 Marker. Messung im echten
Browser, gleicher Faltungszustand wie die Ausgangsmessung; Revision, Viewport,
Faltungszustand und Methode zusammen mit den Werten protokollieren.
Abweichungen ausdrücklich festhalten, bevor AC 6/7 gemessen werden.

Zusätzlich für den API-Regressionstest (AC 12): vollständige erwartete
Antworten beider JSON-Routen mit deterministischen Fixtures und festem
Auswertungszeitpunkt **vor** der Änderung sichern. Erwartungswerte dürfen nicht
aus dem gerade getesteten Antwortpfad erzeugt werden; keine Normalisierung darf
featurebedingte Abweichungen verdecken.

### B2 — Tests zuerst (`tests/test_gui_*.py`)

Das `pytest`-Gate trägt `tdd: true` (`.adw/config.yaml`): Tests zuerst. Für
**geändertes** Verhalten (Reihenfolge, Zuklappen, Summaries,
Timeline-Beschriftung) RED bestätigen, dann implementieren; Tests, die schon
heute gültige Invarianten fixieren (Zähldefinition, `?offset` inert,
Tools-Fenster, API-Regression), dürfen sofort grün sein. Keine Tests für
Deferred-Themen. Deckt die automatisierbaren Akzeptanzkriterien:

1. **Reihenfolge (AC 1).** Im gerenderten Dokument steht `.trace-list` **vor**
   dem Plan-Skelett-Block und dem Change-scope-Block; zwischen Seitenkopf und
   Arbeitsfeld steht keiner der beiden. (Positionsvergleich im HTML-String.)
2. **Zugeklappt (AC 2).** Beide Blöcke rendern als `<details>` **ohne**
   `open`-Attribut, mit `<summary>`; der bisherige Blockinhalt bleibt unterhalb
   der Summary zugänglich. Kein Query-Parameter, kein Skript steuert das.
3. **Summary „Planned tasks" (AC 3).** Die `<summary>` nennt je Lane deren
   Namen, die Aufgabenzahl und den bestehenden Lane-Zustand — ohne Aufklappen
   ablesbar. Ohne Plan-Skelett wird der Block **gar nicht** gerendert (kein
   leerer `<details>`). Pluralfall für 0/1/mehrere Aufgaben.
4. **Summary „Change scope" (AC 4).** Die `<summary>` nennt die Zahl der
   geänderten Dateien über alle beobachteten Lanes sowie die Plus- und
   Minus-Summen. Fixtures für: (a) Textdateien → Datei- und Zeilensummen
   stimmen; (b) Binärdatei → zählt bei der Dateizahl mit, nicht bei den
   Zeilensummen; (c) **kein verwertbarer Diff** → erklärende Zeile statt einer
   Null; (d) **verwertbarer Diff, null geänderte Dateien** → von (c)
   unterscheidbar (Dateizahl 0, keine „nicht verfügbar"-Aussage).
5. **Zähldefinition (AC 9).** Für ≥ 3 Fixture-Größen — darunter ein Baum
   **> 200 Knoten** mit angehängten Tool-Ergebnissen — ist die Zahl der
   `data-tree-entry`-Marker in der Baum-Spalte gleich der rekursiv gezählten
   Knotenzahl des serialisierten Baums; kein Knoten fehlt, keiner doppelt. Ein
   an seinen Aufruf gefaltetes Ergebnis behält seinen eigenen Marker;
   Verdichtungswrapper erzeugen keinen zusätzlichen.
6. **Kein Fenster (AC 10).** `?offset` verändert die Baum-Spalte nicht (gleiche
   Markerzahl mit und ohne `?offset`), und es wird keine Blätter-Navigation für
   den Baum gerendert (`window_nav` erscheint nur für `tools_offset`).
7. **Tools-Fenster unberührt (AC 11).** `?tools_offset`, die 200er-Schranke der
   `data-tool-entry` und deren Blätter-Navigation verhalten sich unverändert;
   die Schranke gilt **nicht** für `data-tree-entry`. (Der bestehende Test
   `test_tool_entry_markers_bounded_across_sizes` bleibt grün.)
8. **Timeline-Beschriftung im Markup (AC 7, automatisierbarer Teil).** Für ein
   Fixture mit vielen kurzen Balken trägt jeder Balken seinen Namen an der
   dauerunabhängigen Stelle (nicht ausschließlich im proportional bemessenen
   `.tl-bar`), das `title`-Attribut bleibt erhalten. Der Lesbarkeits-/
   Nichtüberlappungsbeleg im Layout ist der Browser-Messung (B9) vorbehalten.
9. **Timeline-Geometrie unverändert (AC 8).** `left`/`width` in Prozent, die
   Unterscheidung `bar-<state>` / `bar-running` und das `title`-Attribut sind
   für identische Eingangsdaten gegenüber heute unverändert (Golden gegen die
   heutige Geometrie-Ableitung).
10. **Contract-Regression (AC 12).** Die JSON-Antworten von `GET /api/runs` und
    `GET /api/runs/{repo}/{run_id}` entsprechen den in B1 gesicherten
    Erwartungen in allen Feldern, Typen und Werten — inklusive `change_scope`,
    `plan_skeleton`, `tree`, `phases`, `raw`, `latest_context`, `problems` und
    der Brief-2-Kennzahlen — auch nach einem vorherigen HTML-Abruf. Keine neue
    Route, kein neuer Query-Parameter.

Die visuell-metrischen Kriterien **AC 6** (Umbruch-Anteil) und **AC 7**
(Lesbarkeit/Abschneiden) werden nicht simuliert, sondern gemäß DoD im echten
Browser erhoben (B9).

### B3 — Reihenfolge und Zuklappen (A1, AC 1, AC 2)

In `run_detail.html` (`.trace-layout`, Zeile 429) das Arbeitsfeld (Trace-Baum │
Detail-Panes │ Run-Kontext) **vor** die beiden Zusammenfassungsblöcke ziehen.
Bindende Endreihenfolge: Kopf (Titel, Zeitachse, Registerkarten) → Arbeitsfeld
→ „Planned tasks" (zugeklappt) → „Change scope" (zugeklappt). Beide Blöcke als
native `<details>` **ohne** `open`, mit `<summary>` — wie die Sammelknoten des
Trace-Baums. Kein JavaScript, kein Client-Zustand, keine Persistenz, kein
Query-Parameter. Der bisherige Blockinhalt bleibt beim Öffnen unverändert
zugänglich. Kein anderer Bereich wird umgeordnet.

### B4 — Zusammenfassungszeilen (A2, AC 3, AC 4)

Die beiden `<summary>`-Zeilen tragen die Aussage, ohne dass man aufklappen muss.
Die dafür nötigen Aggregate werden **render-seitig** in `run_detail_page`
berechnet und als eigene Kontextvariablen übergeben — **nie** in `detail`
geschrieben (AC 12, siehe Contract-Fläche):

- **Planned tasks:** je Lane Name, Aufgabenzahl und der bestehende Lane-Zustand
  (nicht neu herleiten). Der Block wird nur gerendert, wenn
  `detail.plan_skeleton` existiert (heutige Bedingung, unverändert).
- **Change scope:** über alle beobachteten Lanes die Zahl der geänderten
  Dateien sowie die Summen der Plus-/Minuszeilen. Binärdateien zählen bei der
  Dateizahl mit, nicht bei den Zeilensummen. Keine neue laneübergreifende
  Diff- oder Deduplizierungslogik — gezählt wird, was `detail.change_scope`
  heute liefert. Liegt **kein** verwertbarer Diff vor, sagt die Zeile das
  (erklärende Zeile statt einer Null); ein verwertbarer Diff **ohne** geänderte
  Dateien bleibt davon unterscheidbar (Dateizahl 0, keine
  „nicht verfügbar"-Aussage).

### B5 — Baum-Spalte bekommt Platz (A3, AC 5, AC 6)

Das Verhältnis des `trace-layout`-Grids in `app.css` (heute
`1fr 1.4fr minmax(9rem, 0.7fr)`) so verschieben, dass die Baum-Spalte
**mindestens so breit** ist wie die Panes-Spalte. Die Kontext-Spalte behält
ihre `minmax`-Untergrenze (9 rem) und bleibt die schmalste. Spaltenzahl bleibt
drei, Reihenfolge bleibt, `min-width: 0` und die Überlauf-Regeln bleiben.
Genaue Werte sind Gestaltungsspielraum, anhand der Browsermessung wählen; keine
neuen Farben/Schriftgrößen/Abstände (E4), keine Breakpoints (E7).
Beschriftungen oder Knoten zu entfernen oder zu kürzen zählt nicht als
Verbesserung der Umbruchquote.

### B6 — Timeline-Beschriftung verlässt den Balken (A4, AC 7, AC 8)

Die Balkenbeschriftung verlässt den proportional bemessenen `.tl-bar`
(`run_detail.html:573`). Der Balken wird reine Geometrie; sein Name
(`_timeline_bar_label`, `adw/gui/app.py:1968`, unverändert als Quelle)
erscheint an einer Stelle, deren Lesbarkeit **nicht** von der Balkenbreite
abhängt (über, unter oder neben dem Balken — Gestaltungsspielraum, nur
Markup/CSS, kein JS). Ein gangbarer Weg ist eine eigene Beschriftungszeile je
Balken innerhalb seiner Spur; bindend ist nur: in einer Spur mit vielen kurzen
Balken überlagern sich die Namen nicht, und die Zuordnung Name↔Balken bleibt
erkennbar. Unverändert bleiben: das `title`-Attribut, die Spurbeschriftung
links (`.tl-lane-label`, feste 7 rem), `left`/`width` in Prozent als
Geometriequelle und die Unterscheidung aktiv / wartend / noch laufend
(`bar-<state>`/`bar-running`).

### B7 — Baum-Größe ausgesagt und Kommentar richtiggestellt (A5, AC 9–11)

Der Zähltest aus B2.5 fixiert die Beziehung (ein `data-tree-entry` je Knoten,
mehrere Größen inkl. > 200 Knoten). Kein Code am vollständigen Rendern des
Baums (`_tree_rows`, `adw/gui/app.py:131`) — es bleibt bewusst ungefenstert
(E2), die Verdichtung bleibt unangetastet (E3). Den veralteten Modulkommentar
und die `CAP`-Notiz in `tests/test_gui_bounded_dom.py` auf den Stand bringen:
die 200er-Schranke gilt **nur noch** für die Tools-Einträge
(`data-tool-entry`); die Baum-Spalte rendert vollständig, die Lesbarkeit kommt
aus der Verdichtung, nicht aus einem Schnitt.

### B8 — i18n (A6, AC 13)

Neue Beschriftungen (die zwei Zusammenfassungszeilen aus A2, etwaige
Timeline-Beschriftungs-Chrome aus A4) in `adw/gui/i18n.py` in **beiden**
Sprachen mit identischer Schlüsselmenge und korrekten Pluralformen (für null,
eine und mehrere Aufgaben bzw. Dateien). Lane-Namen, Dateipfade und Balkennamen
sind Inhalte und werden nicht übersetzt.

### B9 — Doku, Changelog, Browser-Messung, Gates (A7, AC 6, AC 7, AC 14, DoD)

Die Messung aus B1 nach der Änderung wiederholen — Lauf `16f39431`, Viewport
1440 px, gleicher Faltungszustand — und mit ihren Werten protokollieren
(protokollierte manuelle Messung genügt): Baum mindestens so breit wie Panes,
Kontext am schmalsten, Umbruchquote messbar unter der geprüften Ausgangsbasis,
alle 31 Balkennamen lesbar (`scrollWidth` gegen `clientWidth`), keine
Überlagerung in einer Spur mit vielen kurzen Balken. Der Harness
`tests/gui_js_harness.js` / `tests/gui_js_harness.py` läuft ohne Layout-Engine
und bleibt reinem Client-Verhalten vorbehalten; keine simulierten
Layout-Assertions, kein neues Browser-Test-Subsystem.

`docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron: neue Blockreihenfolge,
die zugeklappten Zusammenfassungen samt Leerzuständen, die dauerunabhängige
Timeline-Beschriftung, die Aussage zur Baum-Größe aus A5 (vollständiges
Rendern; Lesbarkeit aus der Verdichtung; 200er-Schranke nur für Tools).
`CHANGELOG.md` und `CHANGELOG.de.md` synchron ergänzen. Der veraltete
„flake8 + isort"-Hinweis (Abnahmepunkt 10) begründet keine zusätzlichen Gates.
Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine Webfont (E1).

## Testumfang

Richtwert **~11 neue Tests** unter `tests/test_gui_*.py`; deutlich mehr als ~16
ist Scope-Drift. Parametrisierte Fälle zählen zum Budget und werden nicht durch
kombinatorische Varianten vervielfacht. Fixtures werden reproduzierbar erzeugt
(bestehende `tests/gui_app_helpers.py`-Konventionen), nicht aus lokal
vorhandenen Run-Verzeichnissen (Retention). Bestehende GUI-Tests bleiben grün,
ohne inhaltlich umgeschrieben zu werden — **erlaubte Ausnahme:** Tests, die die
heutige Blockreihenfolge oder die heutige Timeline-Balkenbeschriftung
festschreiben, werden mit begründendem Kommentar auf den neuen Stand gehoben.
Bestehende Sprachtests prüfen weiterhin die Katalog-Schlüsselparität.

## Grenzen

Keine Änderungen an Run-Liste, Zeitachse, Kennzahlen, Artefakte-/Raw-Tabs,
Recovery-Karten, Auswahl, Pane-Verhalten, `?focus` oder Deep-Links. Keine
Änderung an `build_tree`, Verdichtung, Ereignistypen, Instrumentierung,
Event-Payloads, SSE oder Retention (E3/E8). Der Baum bleibt vollständig und
ungefenstert, `?offset` inert (E2); Tools-Fenster samt 200er-Schranke
unverändert (E6). Kein neues Zustands-Subsystem, keine Persistenz, kein
Polling.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Volltextsuche oder Filterchips über dem Trace-Baum.
- Zusammenklappbare oder in der Breite ziehbare Spalten des Arbeitsfelds.
- Ein Lazy-Rendering des Baums oder irgendeine andere Knotenschranke (E2).
- Zoom, Schwenken oder Zeitlupe in der Timeline; Zusammenfassen von Balken.
- Zusammenführen von Zeitachse und Timeline zu einer einzigen Darstellung.
- Eine eigene Ansicht für die Reader-Probleme.
- Persistieren des Auf-/Zuklappzustands der beiden Zusammenfassungsblöcke.
