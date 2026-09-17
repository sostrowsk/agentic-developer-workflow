# GUI-Redesign 4: Ohne Maus bedienbar

Vierter Brief der Redesign-Reihe. Brief 1 hat das Fundament und die Zeitachse
gebaut, Brief 2 die Zahlen richtiggestellt, Brief 3 das Arbeitsfeld nach oben
geholt. Alle drei haben die Oberfläche **angesehen**. Dieser hier macht sie
**bedienbar**: die Hauptinteraktion der Anwendung — einen Trace-Knoten
auswählen und lesen — ist heute ausschließlich mit der Maus erreichbar.

**Abhängigkeit:** Setzt auf dem gemergten Stand **0.24.0** auf (Briefe 1 bis 3).
Tokens und Skalen stammen aus Brief 1, die Zahlen und ihr Vokabular aus Brief 2,
Blockreihenfolge und Timeline-Beschriftung aus Brief 3. Alles davon wird hier
**benutzt, nicht revidiert**. Die Ausgangslage ist am 2026-09-14 gegen genau
diesen Stand gemessen und vor dem Bauen gegen den dann aktuellen Stand zu
prüfen.

## Ausgangslage (im Code verifiziert, 2026-09-14, Stand 0.24.0)

### Die Hauptinteraktion hat keinen Tastaturpfad

Ein Knoten wird ausgewählt, indem man ihn anklickt: `app.js` registriert einen
`click`-Handler, der auf `.node[data-seq]` und `.tl-bar[data-seq]` schließt
(`adw/gui/static/app.js:250–258`). Die Auswahl schaltet den zugehörigen
Detail-Pane sichtbar — ohne sie zeigt die Seite nichts Knotenspezifisches.

Gemessen an der gerenderten Detailseite von `16f39431` (840 KB HTML):

| Element | Anzahl | anklickbar | per Tastatur erreichbar |
|---|---:|---|---|
| `li.node[data-seq]` — Knotenauswahl | **577** | ja | **nein** |
| `span.tl-bar[data-seq]` — Timeline-Balken | **31** | ja | **nein** |
| `summary` (Sammelknoten, Planned tasks, Change scope) | 263 | ja | ja (natives `<summary>`) |
| `<button>` | 87 | ja | ja |
| `<a href>` | 89 | ja | ja |

Im ganzen Dokument steht **`tabindex` null Mal**. `li` und `span` sind ohne
`tabindex` nicht fokussierbar; 608 der anklickbaren Elemente sind damit
tastaturlos. In `adw/gui/static/app.js` gibt es **keinen einzigen**
`keydown`-, `keyup`- oder `keypress`-Handler — registriert sind ausschließlich
`click`, `toggle`, `input` und `change`.

### Das Klickziel in der Timeline ist ein Schlitz

Seit 0.24.0 besteht eine Timeline-Zeile aus drei Elementen, die **alle**
`data-seq` tragen (`run_detail.html:585–588`):
`div.tl-bar-row` > `span.tl-bar-label` + `span.tl-track` > `span.tl-bar`.
Der Klick-Handler schließt aber weiterhin ausschließlich auf
`.tl-bar[data-seq]` (`app.js:256`) — den proportional bemessenen Balken.
**Auf `.tl-bar-row` und `.tl-bar-label` reagiert kein einziger Handler**
(null Fundstellen in `app.js`).

Damit ist die lesbare Beschriftung, die Release 0.24.0 gerade aus dem Balken
geholt hat, nicht anklickbar, und das einzige Klickziel ist der Balken selbst
— bei 24 der 31 Balken schmaler als ihre Beschriftung, im Extremfall **6 px**
breit. Ein Ziel dieser Größe ist auch mit der Maus kaum zu treffen. Die Zeile
`div.tl-bar-row` trägt bereits das richtige `data-seq` und umfasst
Beschriftung und Spur — sie ist die natürliche Einheit für Klick, Fokus und
Tastaturauslösung.

### Es gibt keinen sichtbaren Fokus

`adw/gui/static/app.css` enthält **keine einzige** `:focus`- oder
`:focus-visible`-Regel. Auch die 176 nativ fokussierbaren Elemente verlassen
sich damit auf den Standardring des Browsers — auf Hintergründen, die Brief 1
gerade neu gesetzt hat, und in einem Dark Mode, den es vorher nicht gab. Wer
mit der Tastatur navigiert, sieht nicht zuverlässig, wo er steht.

### Das Registerkarten-Muster wird angekündigt, aber nicht umgesetzt

`run_detail.html:217` setzt `role="tablist"` auf den Knopfbehälter (30-mal im
gerenderten Dokument, einmal je Agenten-Pane). Die Knöpfe darin sind
`<button type="button" class="tab-btn" data-tab="…">` — **ohne `role="tab"`,
ohne `aria-selected`, ohne `aria-controls`**, und es gibt keine
Pfeiltasten-Navigation. Unterstützende Technik bekommt damit eine Struktur
angesagt, die das Verhalten nicht einlöst; der aktive Zustand steht allein in
der CSS-Klasse `active`.

### Was bereits gut ist und bleiben muss

Die Anwendung ist an anderen Stellen sorgfältig, und das ist zu erhalten:

- Der Typfilter des Raw-Tabs steckt in einem `<label>` (`run_detail.html:295`),
  das Suchfeld trägt `aria-label` (Zeile 301).
- Der Falt-Knopf der Phasen ist ein echter `<button>` mit gepflegtem
  `aria-expanded` (`class="fold-toggle" data-fold-toggle aria-expanded="true"`).
- Dekorative Statusglyphen tragen `aria-hidden`.
- Das Run-Kontext-Panel ist ein `<aside>` mit `aria-label`.

### Bestand, der NICHT Gegenstand dieses Issues ist (aber existiert)

Gestaltung, Farben, Skalen und Dark Mode (Brief 1), die Kennzahlen und die
Run-Liste (Brief 2), Blockreihenfolge, Spaltenverhältnis und
Timeline-Beschriftung (Brief 3), die Verdichtung des Trace-Baums, die
Detail-Panes samt Tools-Fenster, `?focus`-Deep-Links, der SSE-Pfad und die
Ereignisverarbeitung bleiben **unverändert**. Dieses Issue fügt Bedienbarkeit
hinzu und ändert nichts daran, was die Seite zeigt.

## Aufgabe

**A1 — Der Trace-Baum wird mit der Tastatur bedienbar.** Die Baum-Spalte
bekommt einen Tastaturpfad mit den Bewegungen, die zum Inhalt passen:
Auf/Ab bewegt zwischen den **sichtbaren** Zeilen (zugeklappte Inhalte werden
übersprungen), Rechts/Links klappt eine Falt-Zeile auf und zu (auf einer
Zeile ohne Klappmechanik passiert nichts), Enter und Leertaste wählen den
Knoten aus — mit demselben Ergebnis wie ein Klick —, Pos1 und Ende springen
zur ersten und letzten sichtbaren Zeile. Die Baum-Spalte ist genau **ein**
Halt in der Tab-Reihenfolge, nicht 577.

**A2 — Die Timeline-Zeile wird die bedienbare Einheit.** Nicht der 6 px schmale
Balken, sondern die ganze Zeile (`div.tl-bar-row`, die bereits das richtige
`data-seq` trägt und Beschriftung wie Spur umfasst) ist anklickbar, per Tastatur
fokussierbar und auslösbar. Enter und Leertaste bewirken dasselbe wie ein Klick,
einschließlich der bestehenden Umleitung über `?focus` für Knoten, die die Seite
nicht zeigen kann. Die Reihenfolge folgt der Darstellung. Ein Klick auf den
Balken selbst wirkt weiterhin.

**A3 — Sichtbarer Fokus, überall.** Ein einheitlicher, deutlich sichtbarer
Fokusindikator für jedes fokussierbare Element, in beiden Themes. Er benutzt
ein eigenes Token und **nicht** `--signal` — das bedeutet nach Brief 1, E5,
ausschließlich „ein Mensch muss handeln" und darf nicht zusätzlich „hier steht
der Cursor" heißen.

**A4 — Das Registerkarten-Muster wird eingelöst.** Die Knöpfe bekommen
`role="tab"`, gepflegtes `aria-selected` und die Verknüpfung zu ihrem Panel;
Links/Rechts wechselt innerhalb einer Gruppe, und nur die aktive Karte ist ein
Tab-Halt. Die Panels bekommen die zugehörige Rolle. Alternativ wäre, die Rolle
zu entfernen — das ist **nicht** die gewählte Lösung, siehe E4.

**A5 — Die Auswahl ist auch programmatisch ausgewiesen.** Welcher Knoten
ausgewählt ist, steht heute nur in der CSS-Klasse `selected` (Fettdruck und
Unterstreichung). Der Zustand wird zusätzlich maschinenlesbar ausgezeichnet,
damit unterstützende Technik ihn kennt.

**A6 — i18n.** Jeder neue sichtbare oder vorgelesene Text (Bezeichnung der
Baum-Region, etwaige Bedienhinweise) liegt in `adw/gui/i18n.py` in beiden
Sprachen vor, identische Schlüsselmengen.

**A7 — Doku und Changelog.** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
beschreiben synchron den Tastaturpfad des Baums, die Tastenbelegung, den
Fokusindikator und das Registerkarten-Muster. `CHANGELOG.md` und
`CHANGELOG.de.md` synchron ergänzen.

## Normative Definitionen (bindend, nicht neu herzuleiten)

### Tastenbelegung im Trace-Baum (A1)

| Taste | Wirkung |
|---|---|
| ↓ / ↑ | nächste / vorige **sichtbare** Zeile |
| → | Falt-Zeile aufklappen; ist sie offen, zur ersten Kindzeile |
| ← | Falt-Zeile zuklappen; ist sie zu, zur übergeordneten Falt-Zeile |
| Enter, Leertaste | Knoten auswählen (wie ein Klick) |
| Pos1 / Ende | erste / letzte sichtbare Zeile |

„Sichtbar" heißt: nicht innerhalb einer zugeklappten Phase, Gruppe oder
Wiederholung. Die Leertaste darf die Seite nicht scrollen, wenn sie im Baum
etwas auswählt. Tastenkombinationen mit Strg, Alt oder Meta werden nicht
abgefangen.

### DOM-Gewicht (bindend)

Der Baum hat auf einer realen Seite 577 Zeilen. Die **serverseitig gerenderte**
Baum-Spalte bekommt höchstens **einen** neuen Tastatur-Einstiegspunkt; alles
Weitere, was das Muster je Zeile braucht, setzt der Client zur Laufzeit. Die
Zahl der `data-tree-entry`-Marker und die Zahl der gerenderten Elemente bleiben
unverändert. Das ist die Fortschreibung von Brief 1, E4: keine neue Hülle je
Eintrag.

### Fokusindikator (A3)

- Ein eigenes Token, in beiden Themes definiert, mit mindestens **3:1**
  Kontrast gegen `--paper` **und** gegen `--surface`. Das Token `--busy` aus
  Brief 1 erfüllt das bereits (6,41 hell, 6,92 dunkel) und darf die Quelle sein.
- Der Indikator ist nicht allein eine Farbänderung: er hat eine eigene
  Kontur, die auch bei Farbenblindheit trägt.
- Er erscheint bei Tastaturnutzung zuverlässig; ob er bei reiner Mausnutzung
  unterdrückt wird (`:focus-visible`), ist Gestaltungsspielraum.
- Er wird **nicht** durch `outline: none` ohne Ersatz entfernt — an keiner
  Stelle.

### Registerkarten (A4)

- Innerhalb einer Gruppe ist genau ein Knopf Tab-Halt; Links/Rechts wechselt
  zwischen den Knöpfen und aktiviert die gewählte Karte.
- `aria-selected` steht auf genau einem Knopf je Gruppe und wird bei jedem
  Wechsel nachgeführt — auch bei einem Wechsel, den der Server beim Laden
  vorgibt (etwa die Landung auf dem Raw-Tab über einen `raw_from_seq`-Link).
- Die bestehende Klasse `active` und die serverseitige Vorauswahl bleiben, wie
  sie sind; die ARIA-Auszeichnung tritt daneben, sie ersetzt nichts.

### Was unangetastet bleibt

`<label>` um den Typfilter, `aria-label` am Suchfeld, `aria-expanded` am
Falt-Knopf, `aria-hidden` an den Statusglyphen und `aria-label` am
Run-Kontext-Panel bleiben wörtlich erhalten.

## Vorentscheidungen (entschieden — kein Finding, auch nicht im Review-Loop)

**E1** — Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
Webfont, **keine Barrierefreiheits-Bibliothek**. Vanilla JS, handgeschriebenes
CSS (`docs/GUI-SPEC.md:316`). Diese Frage ist entschieden.

**E2** — Die Baum-Spalte ist **ein** Tab-Halt, nicht 577. Eine Umsetzung, die
jeder Zeile einen eigenen Tab-Halt gibt, ist ausdrücklich nicht gewollt: sie
macht die Seite mit der Tastatur unbenutzbar. Diese Frage ist entschieden.

**E3** — Das serverseitige Markup wächst **nicht** um ein Attribut je
Baumzeile. Was das Muster je Zeile braucht, setzt der Client. Diese Frage ist
entschieden.

**E4** — Das Registerkarten-Muster wird **umgesetzt**, nicht durch Entfernen
von `role="tablist"` aufgelöst. Es sind echte Registerkarten; die Rolle ist
richtig, nur unvollständig eingelöst. Diese Frage ist entschieden.

**E5** — Der Fokusindikator benutzt **nicht** `--signal`. Diese Farbe ist nach
Brief 1, E5 für „ein Mensch muss handeln" reserviert und bleibt es. Diese Frage
ist entschieden.

**E6** — Die Gestaltung aus Brief 1, die Zahlen aus Brief 2 und die Anordnung
aus Brief 3 werden benutzt, nicht revidiert. Es entstehen keine neuen Farben
außer dem Fokus-Token, keine neuen Schriftgrößen, keine neuen Abstandswerte.
Diese Frage ist entschieden.

**E7** — Was die Seite **zeigt**, ändert sich nicht: keine neue Information,
keine neue Spalte, kein neuer Block, keine geänderte Reihenfolge. Diese Frage
ist entschieden.

**E8** — Keine Änderung an Ereignistypen, Instrumentierung, `build_tree`, der
Verdichtung, der Blätterung, dem SSE-Pfad oder der Retention. Diese Frage ist
entschieden.

**E9** — Kein vollständiges WCAG-Audit, keine Zertifizierungsaussage. Dieses
Issue schließt die vier benannten Lücken; weitergehende Prüfungen sind
Deferred. Diese Frage ist entschieden.

## Nicht-Ziele / Scope-Deckel

Keine neue Route, kein neues Tab, keine Änderung an Layout, Kennzahlen,
Verdichtung, Run-Liste oder Timeline-Geometrie. Keine Persistenz, kein
Polling, kein neues Zustands-Subsystem, keine neuen Query-Parameter. Keine
Änderung am `?focus`-Verhalten außer der Erreichbarkeit über die Tastatur.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Vollständiges WCAG-2.2-Audit, Screenreader-Testprotokoll, Zertifizierung.
- Sprungmarken („zum Inhalt springen"), Landmark-Überarbeitung der ganzen Seite,
  Überschriftenhierarchie-Revision.
- Tastaturkürzel jenseits der Navigation im Baum (etwa „springe zum ersten
  Fehler", Schnellsuche, Befehlspalette).
- Tastaturpfad für die Run-Liste über die vorhandenen Links hinaus.
- Anpassbare Tastenbelegung, Vim-artige Bewegungen.
- `aria-live`-Ansagen für den SSE-Pfad.
- Hoher-Kontrast-Modus oder `prefers-contrast`.

## Contract-Hinweis

Single-Lane-Projekt (`backend`, siehe `.adw/config.yaml`). Dieses Issue ist
**rein bedienend**: `GET /api/runs` und `GET /api/runs/{repo}/{run_id}` bleiben
in allen Feldern, Typen und Werten unverändert; es entstehen keine neuen Routen
und keine neuen Query-Parameter. Ein Regressionstest fixiert, dass die
API-Antwort durch dieses Feature unverändert bleibt. Nicht Teil des Contracts:
Klassennamen, ARIA-Attribute, Markup- und CSS-Wortlaut, Tastenbelegung.

## Toolchain (Fakten, nicht aus Allgemeinwissen ableiten)

Gates sind `uv run ruff check .` und `uv run pytest -x -q`. Es gibt **kein**
flake8, **kein** isort, **kein** black; `ruff format` ist bewusst kein Gate.
Tests liegen flach unter `tests/` als `test_gui_*.py`. Für Tastatur- und
Auswahlverhalten steht der Harness `tests/gui_js_harness.js` /
`tests/gui_js_harness.py` bereit — er ist der vorgesehene Ort für die
Tastaturtests. Der Abnahmepunkt 10 in `docs/GUI-SPEC.md` nennt noch
„flake8 + isort" — veralteter Spec-Text, wird nicht befolgt.

## Akzeptanzkriterien (messbar)

1. **Auswahl ohne Maus:** Im Harness wählt eine Folge aus Tab bis in die
   Baum-Spalte, Ab-Taste und Enter einen Knoten aus, und der zugehörige
   Detail-Pane wird sichtbar — ohne ein einziges Klick-Ereignis.
2. **Bewegung überspringt Verborgenes:** Steht der Cursor auf einer
   zugeklappten Phase, führt die Ab-Taste zur nächsten sichtbaren Zeile, nicht
   in den verborgenen Teilbaum.
3. **Auf- und Zuklappen:** Rechts öffnet eine geschlossene Falt-Zeile, Links
   schließt eine geöffnete; auf einer Zeile ohne Klappmechanik bleibt der
   Zustand unverändert und es entsteht kein Fehler.
4. **Ein Tab-Halt:** Die Baum-Spalte ist genau ein Halt in der
   Tab-Reihenfolge; die Zahl der Elemente mit einem Tab-Halt in der
   Baum-Spalte ist 1, nicht 577 (E2).
5. **Timeline:** Eine Timeline-Zeile ist als Ganzes per Tastatur fokussierbar
   und auslösbar, und ein Klick auf ihre **Beschriftung** wählt denselben Knoten
   aus wie ein Klick auf den Balken — einschließlich der `?focus`-Umleitung für
   einen Knoten, den die Seite nicht zeigen kann. Das Klickziel ist damit nicht
   mehr auf den 6 px schmalen Balken beschränkt.
6. **Fokus sichtbar:** `app.css` enthält mindestens eine `:focus`- oder
   `:focus-visible`-Regel, die einen sichtbaren Indikator setzt (heute: keine);
   nirgends steht `outline: none` ohne Ersatz.
7. **Fokusfarbe:** Der Fokusindikator benutzt nicht `--signal` (E5) und
   erreicht mindestens 3:1 gegen `--paper` und gegen `--surface`, in beiden
   Themes.
8. **Registerkarten:** Jeder Knopf einer `role="tablist"`-Gruppe trägt
   `role="tab"`; genau einer trägt `aria-selected="true"`, und der Wert wandert
   beim Wechsel mit — auch bei einer serverseitigen Vorauswahl.
9. **Pfeiltasten in den Karten:** Links/Rechts wechselt die aktive
   Registerkarte innerhalb ihrer Gruppe.
10. **Auswahl ausgezeichnet:** Der ausgewählte Knoten ist maschinenlesbar als
    solcher erkennbar, nicht nur über die Klasse `selected`.
11. **DOM-Gewicht:** Die Zahl der `data-tree-entry`-Marker und die Zahl der
    gerenderten Elemente der Baum-Spalte sind gegenüber dem Stand vor diesem
    Issue unverändert (E3).
12. **Bestand erhalten:** `<label>` am Typfilter, `aria-label` am Suchfeld,
    `aria-expanded` am Falt-Knopf und `aria-hidden` an den Statusglyphen sind
    unverändert vorhanden.
13. **Contract:** Die Antwort von `GET /api/runs/{repo}/{run_id}` ist durch
    dieses Feature unverändert.
14. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
    Laufzeit-Dependency, kein Frontend-Paket, kein CDN.

## Testumfang

Richtwert **~12 neue Tests**; deutlich mehr als ~17 ist Scope-Drift. Das
Tastaturverhalten gehört in den JS-Harness, die ARIA- und CSS-Aussagen in die
üblichen `tests/test_gui_*.py`. Die bestehenden GUI-Tests bleiben grün, ohne
inhaltlich umgeschrieben zu werden.
