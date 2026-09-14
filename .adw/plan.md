# Plan — GUI-Redesign 4: Ohne Maus bedienbar

Single-Lane-Projekt (`.adw/config.yaml`): nur der Workstream **backend**. Er
umfasst hier auch Templates, Client-JS, CSS, i18n, den JS-Harness und die Doku —
die GUI-Assets sind Teil des Python-Pakets; es gibt keinen frontend-Lane.

Setzt auf dem gemergten Stand **0.24.0** auf (Briefe 1 bis 3). Tokens und Skalen
(Brief 1), Zahlen und Vokabular (Brief 2), Blockreihenfolge und
Timeline-Beschriftung (Brief 3) werden **benutzt, nicht revidiert** (E6). Rein
bedienend — kein Vertragswechsel; was die Seite zeigt und welche Daten sie
liefert, ändert sich nicht (E7/E8).

Betroffene Dateien (aus der Spec, abschließend):
`adw/gui/static/app.js` (Tastaturpfad Baum, Timeline-Zeile, Registerkarten,
maschinenlesbare Auswahl, client-gesetzte Per-Zeile-Attribute),
`adw/gui/static/app.css` (Fokus-Token + `:focus`/`:focus-visible`-Regeln),
`adw/gui/templates/run_detail.html` (höchstens **ein** neuer serverseitiger
Tastatur-Einstiegspunkt der Baum-Spalte; `role`/`aria-*` der Registerkarten und
`role="tabpanel"` — server- oder client-seitig, kein Zwang), `adw/gui/i18n.py`
(neue Strings), `tests/gui_js_harness.js` / `tests/gui_js_harness.py` (um
Tastatur-Dispatch und neue Szenarien erweitert — Entwicklungswerkzeug, keine
Laufzeit-Dependency), neue `tests/test_gui_*.py`, `docs/GUI-SPEC.md`,
`docs/GUI-SPEC.de.md`, `CHANGELOG.md`, `CHANGELOG.de.md`.
`adw/gui/app.py` dient nur zur Prüfung der bestehenden HTTP- und Render-Pfade;
Änderungen an Datenableitung oder API sind nicht erforderlich.

## Contract-Fläche (`.adw/contract.yaml`)

Der Contract pinnt für diesen Brief **die Unveränderlichkeit** der beiden
JSON-Routen `/api/runs` und `/api/runs/{repo}/{run_id}` in allen Feldern, Typen
und Werten (AC 13) — keine neue Route, kein neuer Query-Parameter — sowie das
extern beobachtbare **Bedien-Verhalten** der Run-Detail-Seite
`GET /runs/{repo}/{run_id}`: Knotenauswahl per Tastatur ohne Maus, die
Timeline-**Zeile** (nicht nur der Balken) als Klick-/Fokus-/Auslöse-Einheit
einschließlich `?focus`-Umleitung, ein sichtbarer Fokusindikator in beiden
Themes, das eingelöste Registerkarten-Muster und die maschinenlesbare Auswahl.

**Ausdrücklich NICHT Contract (frei änderbar, Spec „Nicht Teil des Contracts"):**
Klassennamen, **ARIA-Attribute**, Markup- und CSS-Wortlaut, **Tastenbelegung**.
Der Contract beschreibt darum das *beobachtbare Bedienergebnis*, nicht
Attribut-, Token- oder Tastennamen; die bindende Tastenbelegung und die
konkreten ARIA-Namen stehen in Spec und Plan, nicht im Vertrag.

**Bindender Architektur-Fakt (AC 13, E3):** Die JSON-Antworten bleiben
wörtlich; es kommen keine neuen Bedien-, Auswahl- oder Fokus-Daten hinzu. Das
serverseitige Markup der Baum-Spalte wächst **nicht** um ein Attribut je
Baumzeile: die Zahl der `data-tree-entry`-Marker und der gerenderten Elemente
bleibt gegenüber dem Stand vor diesem Issue unverändert; die Baum-Spalte erhält
höchstens **einen** neuen Tastatur-Einstiegspunkt. Alles Weitere je Zeile
(Rollen, `tabindex`, Auswahl-Markierung) setzt der Client zur Laufzeit.

## Workstream: backend

### B1 — Messbasis prüfen (vor dem Bauen)

Die Issue-Messwerte (2026-09-14, Lauf `16f39431`, 840 KB HTML, 577 Baumzeilen,
31 Timeline-Balken, `tabindex` null Mal, keine `:focus`-Regel, keine
`keydown`-Handler) sind **Referenzwerte genau dieses Laufs**, keine
allgemeingültigen Fixture-Größen. Vor dem Bauen gegen den dann aktuellen Stand
prüfen: die Zahlen der `.node[data-seq]`, `.tl-bar-row[data-seq]`, `tab-btn`
sowie das Fehlen von `tabindex`/`:focus`/Tastatur-Handlern protokollieren und
Abweichungen festhalten. Ist der Referenzlauf lokal nicht verfügbar, diese
Einschränkung dokumentieren und **keine Messwerte erfinden**.

Für AC 11 vor der Änderung mit deterministischen Fixtures die Zahl der
serverseitig gerenderten Elemente und `data-tree-entry`-Marker der Baum-Spalte
als unabhängige Vorher-Basis sichern.

Für AC 13 existiert bereits `tests/test_gui_display_only_contract.py`: beide
JSON-Routen werden vor **und nach** einem HTML-Abruf gegen eine unabhängige,
eingefrorene Baseline (Revision `cc201f1`, 0.23.0, in
`tests/fixtures/contract_baseline_api.json`) verglichen. Vor dem Bauen
bestätigen, dass diese Baseline gegen 0.24.0 noch grün ist; Erwartungswerte
niemals aus dem gerade geänderten Antwortpfad neu erzeugen. Braucht AC 13 eine
Ergänzung, gewinnt sie ihre Erwartungen ausschließlich aus dieser geprüften
Basis und bleibt im Testbudget.

### B2 — Tests zuerst (`tdd: true`)

Das `pytest`-Gate trägt `tdd: true` (`.adw/config.yaml`): neue/geänderte
Verhaltenstests erst RED bestätigen, dann implementieren; Invarianten-Tests
(DOM-Gewicht, Bestand, API-Regression) dürfen sofort grün sein. Tastatur- und
Auswahlverhalten (AC 1–5, 8–10) gehört in den Node-Harness
`tests/gui_js_harness.js` / `tests/gui_js_harness.py`; die ARIA-, DOM- und
CSS-Aussagen (AC 6, 7, 11, 12) und der Contract (AC 13) in die üblichen
`tests/test_gui_*.py`. Keine Tests für Deferred-Themen. Deckt die
automatisierbaren Akzeptanzkriterien:

1. **Auswahl ohne Maus (AC 1).** Harness-Szenario: Fokus in die Baum-Spalte,
   Ab-Taste, dann Enter wählt einen Knoten aus und macht den zugehörigen
   Detail-Pane sichtbar — **ohne** ein `click`-Ereignis, auch kein synthetisch
   erzeugtes. Die Leertaste erreicht dieselbe Auswahl wie Enter und scrollt die
   Seite nicht (verhinderte Standardaktion belegt); die reine Auf-/Ab-Bewegung
   löst **keine** Auswahl aus.
2. **Bewegung überspringt Verborgenes (AC 2).** Auf einer zugeklappten Phase
   führt die Ab-Taste zur nächsten **sichtbaren** Zeile, nicht in den
   verborgenen Teilbaum; auch Pos1/Ende verwenden ausschließlich sichtbare
   Zeilen. Fixture mit Phase/Gruppe/Wiederholung in zugeklapptem Zustand.
3. **Auf- und Zuklappen (AC 3).** Rechts öffnet eine geschlossene Falt-Zeile;
   ist sie offen, bewegt Rechts zur ersten Kindzeile. Links schließt eine
   geöffnete; ist sie zu, bewegt Links zur übergeordneten Falt-Zeile. Auf einer
   Zeile ohne Klappmechanik bleibt der Zustand unverändert und es entsteht kein
   Fehler. Kombinationen mit Strg/Alt/Meta werden nicht abgefangen.
4. **Ein Tab-Halt (AC 4).** Nach Client-Initialisierung ist die Zahl der
   Elemente mit sequenziellem Tab-Halt (`tabindex >= 0` bzw. nativ, ohne
   `tabindex="-1"`) in der Baum-Spalte **1**, nicht 577; auch bereits nativ
   fokussierbare Elemente innerhalb der Spalte (etwa `summary`) erzeugen keinen
   zusätzlichen sequenziellen Tab-Halt.
5. **Timeline (AC 5).** Eine Timeline-Zeile (`.tl-bar-row`) ist als Ganzes per
   Tastatur fokussierbar (Reihenfolge = Darstellung) und mit Enter/Leertaste
   auslösbar; Beschriftung, Balken und Tastaturauslösung wählen denselben
   Knoten — ohne Doppel-Auslösung beim Klick auf Balken innerhalb der Zeile —
   einschließlich der `?focus`-Umleitung für einen Knoten ohne eigene
   Pane/Baumzeile, auch bei Tastaturauslösung.
6. **Fokus sichtbar (AC 6).** `app.css` enthält mindestens eine `:focus`- oder
   `:focus-visible`-Regel mit sichtbarem Indikator (heute: keine); nirgends
   steht `outline: none` ohne kompensierenden Ersatz. Quelltext-/CSS-Assertion.
7. **Fokusfarbe (AC 7).** Der Fokusindikator benutzt **nicht** `--signal`, ist
   in **beiden** Themes als eigenes Token definiert und erreicht rechnerisch
   ≥ 3:1 gegen `--paper` **und** gegen `--surface`. Der Kontrast wird aus den
   Token-Werten berechnet (reiht sich in die bestehenden Token-Tests,
   vgl. `tests/test_gui_design_tokens.py`); `--busy` als Quelle ist zulässig.
8. **Registerkarten-Zustand (AC 8).** Jeder Knopf einer `role="tablist"`-Gruppe
   trägt `role="tab"` und verweist per `aria-controls` auf sein Panel mit
   `role="tabpanel"`; genau ein Knopf je Gruppe trägt `aria-selected="true"`
   (die übrigen `"false"`), und der Wert wandert beim Wechsel mit — auch bei
   serverseitiger Vorauswahl (Landung auf dem Raw-Tab über `raw_from_seq`).
   Klasse `active`, sichtbares Panel und ARIA-Zustand stimmen überein.
9. **Pfeiltasten in den Karten (AC 9).** Links/Rechts wechselt die aktive
   Registerkarte nur innerhalb ihrer nächstgelegenen Gruppe (verschachtelte
   Gruppen bleiben unabhängig); nur die aktive Karte ist ein sequenzieller
   Tab-Halt, und Auswahlzustand, Tab-Halt, Klasse `active` und sichtbares
   Panel wechseln gemeinsam — auch bei einem Wechsel per Klick.
10. **Auswahl ausgezeichnet (AC 10).** Der ausgewählte Knoten ist
    maschinenlesbar (eigenes ARIA-/State-Attribut, nicht nur Klasse
    `selected`). Ein Wechsel nimmt die Auszeichnung am vorherigen Knoten
    zurück; sie greift über Baum **wie** Timeline, per Maus **wie** Tastatur,
    bei der Initialauswahl und bei `?focus`-Landung. Bloßer Navigationsfokus
    wird **nicht** als Auswahl ausgezeichnet.
11. **DOM-Gewicht (AC 11).** Für gleiche Eingabe sind die Zahl der
    `data-tree-entry`-Marker und die Zahl der gerenderten Elemente der
    **serverseitigen** Baum-Spalte unverändert gegenüber der in B1 gesicherten
    Vorher-Basis; höchstens ein neuer Tastatur-Einstiegspunkt, kein neues
    Attribut je Baumzeile (E3).
12. **Bestand erhalten (AC 12).** `<label>` am Typfilter, `aria-label` am
    Suchfeld, `aria-expanded` am Falt-Knopf und `aria-hidden` an den
    Statusglyphen sind wörtlich unverändert vorhanden.
13. **Contract-Regression (AC 13).** Die in B1 verifizierte API-Regression
    (`tests/test_gui_display_only_contract.py`) bleibt grün: beide JSON-Routen
    unverändert in allen Feldern, Typen und Werten, auch nach vorherigem
    HTML-Abruf; keine featurebedingten Unterschiede weg-normalisieren. Keine
    neue Route, kein neuer Query-Parameter.

### B3 — Harness um Tastatur-Dispatch erweitern

Der Node-Harness stubt heute nur `click`/`toggle`/`input`/`change` und hat
einen No-op-`addEventListener` auf Elementen (`gui_js_harness.js:73`). Für
AC 1–3, 5, 9 bekommt der Harness (Entwicklungswerkzeug, **keine**
Laufzeit-Dependency, kein Browser) das Minimum: Registrieren und Auslösen von
`keydown` auf dem Einstiegspunkt/`document`, ein
`document.activeElement`-Äquivalent samt sequenzieller Tab-Navigation und
`preventDefault`-Beobachtung (für den „Leertaste scrollt nicht"-Beleg). Der
Harness belegt Ereignis- und Fokuszustände sowie verhinderte Standardaktionen —
keine Layout-Engine, keine simulierten Layout-Assertions, kein neues
Browser-Test-Subsystem. Neue Szenarien reihen sich in den bestehenden
`SCENARIO`-Dispatch (z. B. `tree-keyboard`, `timeline-activate`, `tab-arrows`,
`selection-marked`).

### B4 — Tastaturpfad im Trace-Baum (A1, AC 1–4)

In `app.js` einen Tastaturpfad für die Baum-Spalte ergänzen. Die Baum-Spalte
erhält genau **einen** sequenziellen Tab-Halt (Roving-Tabindex oder ein
Container-Einstiegspunkt mit intern nachgeführter „aktueller Zeile"); die 577
Zeilen werden **nicht** je einzeln in die Tab-Reihenfolge genommen (E2). Nativ
fokussierbare Elemente in der Spalte (`summary`) werden client-seitig aus der
sequenziellen Reihenfolge genommen (`tabindex="-1"` o. ä.), ohne ihr
Aufklappverhalten zu ändern. Die bindende Tastenbelegung (Spec-Tabelle):
Auf/Ab zwischen **sichtbaren** Zeilen (zugeklappte Inhalte übersprungen),
Rechts/Links bedient die vorhandenen Faltungen (auf klapplosen Zeilen
wirkungslos und fehlerfrei), Enter/Leertaste wählen aus, Pos1/Ende springen zur
ersten/letzten sichtbaren Zeile. Reine Auf-/Ab-Bewegung wählt **nicht** aus;
der Navigationscursor bleibt von `selectedSeq` getrennt und ist flüchtiger
Teil der vorhandenen Client-Bedienung — kein neues Subsystem, keine
Persistenz, kein Query-Parameter. Die Tastaturauswahl ruft den bestehenden
Auswahlpfad direkt auf (identisches Ergebnis wie ein Klick, inkl.
`adw:select`-Instrumentierung und Detail-Pane), ohne ein Klick-Ereignis zu
synthetisieren. Die Leertaste verhindert das Seiten-Scrollen
(`preventDefault`), wenn sie auswählt; Kombinationen mit Strg/Alt/Meta werden
**nicht** abgefangen. Die „sichtbar"-Berechnung nutzt den bestehenden
Faltungszustand (`build_tree`/Verdichtung unangetastet, E8); nach einer
Faltung bleibt der Cursor auf einer sichtbaren Zeile. Falt-Zeilen sind
navigierbar, aber es entstehen keine neuen auswählbaren Knoten. Die aktuell
angesteuerte Zeile ist sichtbar erkennbar (B6), unabhängig von ihrer Auswahl.

### B5 — Timeline-Zeile als bedienbare Einheit (A2, AC 5)

Die ganze Zeile `div.tl-bar-row` (trägt bereits das richtige `data-seq`,
umfasst Beschriftung und Spur) wird anklickbar, per Tastatur fokussierbar und
mit Enter/Leertaste auslösbar — dasselbe Ergebnis wie der heutige Balkenklick
über denselben Auswahlpfad, einschließlich der bestehenden `?focus`-Umleitung
für Knoten ohne eigene Pane bzw. Baumzeile (`app.js:256–270`), auch bei
Tastaturauslösung. Der delegierte Click-Handler schließt zusätzlich auf
`.tl-bar-row[data-seq]` (statt nur `.tl-bar`); Beschriftung und Balken
erreichen denselben Pfad **ohne doppelte Auslösung**, wenn der Balkenklick
durch die Zeile bubbelt. Der Balken selbst bleibt anklickbar, seine Geometrie
unverändert (E7); der bestehende Schutz gegen überholte asynchrone Auswahl
bleibt erhalten. Die Fokus-Reihenfolge folgt der Darstellung; den
Fokus-Einstieg je Zeile setzt der Client.

### B6 — Sichtbarer Fokus, überall (A3, AC 6, AC 7)

In `app.css` ein **neues** Fokus-Token in beiden Themes definieren (Quelle darf
`--busy` sein, nicht `--signal`, E5) und `:focus`/`:focus-visible`-Regeln für
**jedes** fokussierbare Element ergänzen — native Links, Knöpfe, Eingabefelder,
`summary` sowie die neuen Tastaturziele (Baum-Einstieg, Timeline-Zeile). Der
Indikator ist nicht allein Farbe: eigene Kontur (Outline/Box-Shadow), ≥ 3:1
gegen `--paper` und `--surface` in beiden Themes. `:focus-visible` zur
Unterdrückung bei reiner Mausnutzung ist erlaubt. Nirgends `outline: none`
ohne Ersatz. Bei Navigation über den zentralen Baum-Einstieg bleibt die
aktuell angesteuerte Zeile in derselben visuellen Sprache sichtbar erkennbar.
Vorhandene Größen- und Abstandsskalen verwenden; keine neuen Farben außer dem
Fokus-Token, keine neuen Schriftgrößen/Abstände (E6).

### B7 — Registerkarten-Muster eingelöst (A4, AC 8, AC 9)

Die `tab-btn`-Knöpfe jeder `role="tablist"`-Gruppe bekommen `role="tab"`,
gepflegtes `aria-selected` und `aria-controls` auf ihr Panel; die Panels
bekommen eindeutige IDs und `role="tabpanel"`. Ob diese Attribute serverseitig
im Template (`run_detail.html`) oder client-seitig gesetzt werden, ist
Gestaltungsspielraum (E3 begrenzt nur die **Baum-Spalte**, nicht die ~30
Tab-Gruppen). Links/Rechts wechselt innerhalb der nächstgelegenen Gruppe
(verschachtelte Gruppen bleiben getrennt, wie der bestehende
`data-tabs`-Scope in `activateTab`); nur die aktive Karte ist sequenzieller
Tab-Halt (Roving-Tabindex), Pfeiltasten bewegen den Fokus auf die neu aktive
Karte. `aria-selected`, Klasse `active`, Tab-Halt und sichtbares Panel wandern
bei jedem Wechsel gemeinsam — bei Klick **wie** Pfeiltaste. Die serverseitige
Vorauswahl bleibt maßgeblich und wird übernommen (Landung auf dem Raw-Tab über
`raw_from_seq`); **keine** pauschale Initialisierung auf den ersten Tab. Die
Initialisierung der neuen Auszeichnung schließt an die vorhandenen
Initialisierungsstellen an, einschließlich bestehender DOM-Ersetzungen beim
Live-Refresh, ohne den SSE-Pfad zu ändern (E8). Klasse `active` und
serverseitige Vorauswahl bleiben unverändert; die ARIA-Auszeichnung tritt
daneben und ersetzt nichts (E4). `role="tablist"` wird **nicht** entfernt.

### B8 — Auswahl maschinenlesbar (A5, AC 10)

Der ausgewählte Knoten wird zusätzlich zur unveränderten Klasse `selected`
maschinenlesbar ausgezeichnet (eigenes, zum gewählten Rollenmodell passendes
ARIA-/State-Attribut, client-seitig gesetzt). Der Wechsel nimmt die
Auszeichnung am vorherigen Knoten zurück; sie greift überall dort, wo
`selectedSeq`/`applySelection` heute die Klasse setzt — Auswahl über Baum wie
Timeline, per Maus wie Tastatur, Initialauswahl und `?focus`-Landung. Bloßer
Navigationsfokus im Baum (Auf/Ab ohne Enter) wird **nicht** als Auswahl
ausgezeichnet.

### B9 — i18n (A6)

Jeder neue sichtbare oder vorgelesene Text (Bezeichnung der Baum-Region,
etwaige Bedienhinweise) in `adw/gui/i18n.py` in **beiden** Sprachen mit
identischer Schlüsselmenge. Ein neuer sichtbarer Hinweisblock ist **nicht**
erforderlich (E7). Lane-Namen, Knotenlabels und Balkennamen sind Inhalte und
werden nicht übersetzt. Der bestehende Sprach-Paritätstest
(`tests/test_gui_language.py`) bleibt grün und wird weiterverwendet.

### B10 — Doku, Changelog, Gates (A7, AC 14, DoD)

`docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron: Tastaturpfad des Baums
samt bindender Tastenbelegung, die Timeline-Zeile als bedienbare Einheit, der
Fokusindikator (eigenes Token, beide Themes) und das eingelöste
Registerkarten-Muster. `CHANGELOG.md` und `CHANGELOG.de.md` synchron ergänzen.
Der veraltete „flake8 + isort"-Hinweis (Abnahmepunkt 10 in `docs/GUI-SPEC.md`)
begründet **keine** zusätzlichen Gates und wird nicht befolgt. Gates grün:
`uv run ruff check .` und `uv run pytest -x -q`. Keine neue
Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine Webfont, keine
Barrierefreiheits-Bibliothek (E1).

## Testumfang

Richtwert **~12 neue Tests** unter `tests/`; deutlich mehr als **~17** ist
Scope-Drift. Parametrisierte Fälle zählen zum Budget; zusammengehörige Fälle
in Szenarien bündeln, keine kombinatorische Vervielfachung.
Tastatur-/Auswahlverhalten (AC 1–5, 8–10) im Node-Harness gegen das
ausgelieferte `app.js`; ARIA-/DOM-/CSS-Aussagen (AC 6, 7, 11, 12) und der
Contract (AC 13) in `tests/test_gui_*.py`. Fixtures reproduzierbar über die
bestehenden `tests/gui_app_helpers.py`-Konventionen, nicht aus lokalen
Run-Verzeichnissen (Retention). Bestehende GUI-Tests bleiben grün, ohne
inhaltlich umgeschrieben zu werden.

## Grenzen

Keine neue Route, kein neues Tab, keine Änderung an Layout, Kennzahlen,
Verdichtung, Run-Liste oder Timeline-Geometrie. Keine Persistenz, kein Polling,
kein neues Zustands-Subsystem, keine neuen Query-Parameter. Keine Änderung am
`?focus`-Verhalten außer der Erreichbarkeit über die Tastatur. Keine Änderung
an Ereignistypen, Instrumentierung, `build_tree`, der Verdichtung, der
Blätterung, dem SSE-Pfad oder der Retention (E8). Was die Seite zeigt, ändert
sich nicht (E7). `<label>` um den Typfilter, `aria-label` am Suchfeld,
`aria-expanded` am Falt-Knopf, `aria-hidden` an den Statusglyphen und
`aria-label` am Run-Kontext-Panel bleiben wörtlich erhalten. Die
Vorentscheidungen E1–E9 sind entschieden — kein Finding, auch nicht im
Review-Loop.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Vollständiges WCAG-2.2-Audit, Screenreader-Testprotokoll, Zertifizierung
  (E9).
- Sprungmarken („zum Inhalt springen"), Landmark-Überarbeitung der ganzen
  Seite, Überschriftenhierarchie-Revision.
- Tastaturkürzel jenseits der Navigation im Baum (etwa „springe zum ersten
  Fehler", Schnellsuche, Befehlspalette).
- Tastaturpfad für die Run-Liste über die vorhandenen Links hinaus.
- Anpassbare Tastenbelegung, Vim-artige Bewegungen.
- `aria-live`-Ansagen für den SSE-Pfad.
- Hoher-Kontrast-Modus oder `prefers-contrast`.
