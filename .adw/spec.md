# Spec — GUI-Redesign 4: Ohne Maus bedienbar

Setzt auf dem gemergten Stand **0.24.0** auf (Briefe 1 bis 3). Tokens und Skalen
(Brief 1), Zahlen und Vokabular (Brief 2), Blockreihenfolge und
Timeline-Beschriftung (Brief 3) werden **benutzt, nicht revidiert**. Die im
Issue genannten Messwerte (2026-09-14, Lauf `16f39431`, 840 KB HTML, 577
Baumzeilen, 31 Timeline-Balken) sind Referenzwerte genau dieses Laufs — keine
allgemeingültigen Fixture-Größen — und vor dem Bauen gegen den dann aktuellen
Stand zu prüfen. Rein bedienend: kein Vertragswechsel, keine Änderung daran,
was die Seite zeigt.

## Goal

Die Hauptinteraktion der Run-Detail-Seite — einen Trace-Knoten auswählen und
lesen — ohne Maus erreichbar machen und diesen Weg sichtbar führen. Vier
verifizierte Lücken werden geschlossen: der Trace-Baum bekommt einen
Tastaturpfad, die Timeline-Zeile (statt des bis zu 6 px schmalen Balkens) wird
die bedienbare Einheit, ein einheitlicher sichtbarer Fokusindikator entsteht in
beiden Themes, und das mit `role="tablist"` bereits angekündigte
Registerkarten-Muster wird eingelöst. Die Knotenauswahl wird zusätzlich
maschinenlesbar ausgezeichnet. Was die Seite zeigt und welche Daten sie
liefert, ändert sich nicht.

## Scope

- **A1 — Tastaturpfad im Trace-Baum:** Die Baum-Spalte bekommt einen
  Tastaturpfad mit der bindenden Tastenbelegung (siehe unten): Auf/Ab bewegt
  zwischen **sichtbaren** Zeilen (zugeklappte Inhalte übersprungen),
  Rechts/Links bedient die vorhandenen Faltungen, Enter und Leertaste wählen
  den Knoten aus — mit demselben Ergebnis wie ein Klick —, Pos1/Ende springen
  zur ersten/letzten sichtbaren Zeile. Die reine Auf-/Ab-Bewegung löst keine
  Auswahl aus. Es entstehen keine neuen auswählbaren Knoten; navigiert wird
  über die vorhandenen Zeilen. Die Baum-Spalte ist genau **ein** Halt in der
  Tab-Reihenfolge, nicht 577 (E2).
- **A2 — Timeline-Zeile als bedienbare Einheit:** Die ganze Zeile
  (`div.tl-bar-row`, die bereits das richtige `data-seq` trägt und Beschriftung
  wie Spur umfasst) wird anklickbar, per Tastatur fokussierbar und auslösbar.
  Enter und Leertaste bewirken dasselbe wie ein Klick, einschließlich der
  bestehenden `?focus`-Umleitung für Knoten, die die Seite nicht zeigen kann.
  Die Fokus-Reihenfolge folgt der Darstellung. Ein Klick auf den Balken selbst
  wirkt weiterhin; seine Geometrie bleibt unverändert.
- **A3 — Sichtbarer Fokus, überall:** Ein einheitlicher, deutlich sichtbarer
  Fokusindikator für jedes fokussierbare Element — native Links, Knöpfe,
  Eingabefelder und `summary` ebenso wie die neuen Tastaturziele —, in beiden
  Themes. Er benutzt ein eigenes Token (Quelle darf `--busy` sein) und
  **nicht** `--signal` (E5). Bei Navigation über den zentralen Baum-Einstieg
  bleibt die aktuell angesteuerte Zeile sichtbar erkennbar.
- **A4 — Registerkarten-Muster eingelöst:** Die `tab-btn`-Knöpfe jeder
  `role="tablist"`-Gruppe bekommen `role="tab"`, gepflegtes `aria-selected` und
  die Verknüpfung zu ihrem Panel (`aria-controls`); das Panel bekommt
  `role="tabpanel"`. Links/Rechts wechselt innerhalb einer Gruppe, nur die
  aktive Karte ist ein Tab-Halt. `aria-selected` wandert bei jedem Wechsel mit
  — auch bei einer serverseitigen Vorauswahl (etwa Landung auf dem Raw-Tab
  über einen `raw_from_seq`-Link). Klasse `active` und serverseitige
  Vorauswahl bleiben, wie sie sind; die ARIA-Auszeichnung tritt daneben, sie
  ersetzt nichts. Die Rolle wird nicht entfernt (E4).
- **A5 — Auswahl maschinenlesbar:** Der ausgewählte Knoten wird zusätzlich zur
  unverändert weiterverwendeten CSS-Klasse `selected` maschinenlesbar
  ausgezeichnet, damit unterstützende Technik den Zustand kennt.
- **A6 — i18n:** Jeder neue sichtbare oder vorgelesene Text (Bezeichnung der
  Baum-Region, etwaige Bedienhinweise) liegt in `adw/gui/i18n.py` in beiden
  Sprachen mit identischer Schlüsselmenge vor. Ein neuer sichtbarer
  Hinweisblock ist nicht erforderlich (E7).
- **A7 — Doku und Changelog:** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
  beschreiben synchron den Tastaturpfad des Baums, die Tastenbelegung, den
  Fokusindikator und das Registerkarten-Muster. `CHANGELOG.md` und
  `CHANGELOG.de.md` synchron ergänzt.

### Bindende Tastenbelegung im Trace-Baum (A1)

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
abgefangen. Auf einer Zeile ohne Klappmechanik bewirken Rechts/Links keine
Zustandsänderung und keinen Fehler.

### Bindendes DOM-Gewicht (A1)

Die **serverseitig gerenderte** Baum-Spalte bekommt höchstens **einen** neuen
Tastatur-Einstiegspunkt; alles Weitere, was das Muster je Zeile braucht, setzt
der Client zur Laufzeit (E3). Die Zahl der `data-tree-entry`-Marker und die
Zahl der gerenderten Elemente der Baum-Spalte bleiben unverändert —
Fortschreibung von Brief 1, E4: keine neue Hülle je Eintrag.

### Bindender Fokusindikator (A3)

- Eigenes Token, in beiden Themes definiert, mit mindestens **3:1** Kontrast
  gegen `--paper` **und** gegen `--surface`. `--busy` (6,41 hell, 6,92 dunkel)
  darf die Quelle sein.
- Nicht allein Farbe: eigene Kontur, die auch bei Farbenblindheit trägt.
- Erscheint bei Tastaturnutzung zuverlässig; Unterdrückung bei reiner
  Mausnutzung (`:focus-visible`) ist Gestaltungsspielraum.
- Nirgends `outline: none` ohne Ersatz.

## Non-Goals / Scope-Deckel

- Keine neue Route, kein neues Tab, keine Änderung an Layout, Kennzahlen,
  Verdichtung, Run-Liste oder Timeline-Geometrie.
- Keine Persistenz, kein Polling, kein neues Zustands-Subsystem, keine neuen
  Query-Parameter. Der flüchtige Navigationszustand des Tastaturpfads gehört
  zur vorhandenen Client-Bedienung und ist kein neues Subsystem.
- Keine Änderung am `?focus`-Verhalten außer der Erreichbarkeit über die
  Tastatur.
- **E1** Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
  Webfont, keine Barrierefreiheits-Bibliothek. Vanilla JS, handgeschriebenes
  CSS.
- **E2** Die Baum-Spalte ist **ein** Tab-Halt, nicht 577; ein eigener Tab-Halt
  je Zeile ist ausdrücklich nicht gewollt.
- **E3** Das serverseitige Markup wächst **nicht** um ein Attribut je
  Baumzeile; was das Muster je Zeile braucht, setzt der Client zur Laufzeit.
- **E4** Das Registerkarten-Muster wird umgesetzt, nicht durch Entfernen von
  `role="tablist"` aufgelöst.
- **E5** Der Fokusindikator benutzt **nicht** `--signal`; diese Farbe bleibt
  für „ein Mensch muss handeln" reserviert.
- **E6** Gestaltung (Brief 1), Zahlen (Brief 2), Anordnung (Brief 3) werden
  benutzt, nicht revidiert: keine neuen Farben außer dem Fokus-Token, keine
  neuen Schriftgrößen, keine neuen Abstandswerte.
- **E7** Was die Seite **zeigt**, ändert sich nicht: keine neue Information,
  keine neue Spalte, kein neuer Block, keine geänderte Reihenfolge.
- **E8** Keine Änderung an Ereignistypen, Instrumentierung, `build_tree`, der
  Verdichtung, der Blätterung, dem SSE-Pfad oder der Retention.
- **E9** Kein vollständiges WCAG-Audit, keine Zertifizierungsaussage; dieses
  Issue schließt die vier benannten Lücken, weitergehende Prüfungen sind
  Deferred.
- **Unangetastet:** `<label>` um den Typfilter, `aria-label` am Suchfeld,
  `aria-expanded` am Falt-Knopf, `aria-hidden` an den Statusglyphen und
  `aria-label` am Run-Kontext-Panel bleiben wörtlich erhalten.
- Nicht Teil des Contracts (frei änderbar): Klassennamen, ARIA-Attribute,
  Markup- und CSS-Wortlaut, Tastenbelegung.

Die Vorentscheidungen E1–E9 sind entschieden — kein Finding, auch nicht im
Review-Loop.

## Acceptance Criteria (messbar)

1. **Auswahl ohne Maus:** Im Harness wählt eine Folge aus Tab bis in die
   Baum-Spalte, Ab-Taste und Enter einen Knoten aus, und der zugehörige
   Detail-Pane wird sichtbar — ohne ein einziges Klick-Ereignis. Die Leertaste
   erreicht dieselbe Auswahl wie Enter, ohne die Seite zu scrollen; die reine
   Auf-/Ab-Bewegung löst keine Auswahl aus.
2. **Bewegung überspringt Verborgenes:** Steht der Cursor auf einer
   zugeklappten Phase, führt die Ab-Taste zur nächsten sichtbaren Zeile, nicht
   in den verborgenen Teilbaum.
3. **Auf- und Zuklappen:** Rechts öffnet eine geschlossene Falt-Zeile; ist sie
   offen, bewegt Rechts zur ersten Kindzeile. Links schließt eine geöffnete;
   ist sie zu, bewegt Links zur übergeordneten Falt-Zeile. Auf einer Zeile
   ohne Klappmechanik bleibt der Zustand unverändert und es entsteht kein
   Fehler.
4. **Ein Tab-Halt:** Die Zahl der Elemente mit einem sequenziellen Tab-Halt in
   der Baum-Spalte ist nach Client-Initialisierung 1, nicht 577 (E2); auch
   bereits nativ fokussierbare Elemente innerhalb der Spalte (etwa `summary`)
   erzeugen keine zusätzlichen sequenziellen Tab-Halte.
5. **Timeline:** Eine Timeline-Zeile ist als Ganzes per Tastatur fokussierbar
   und mit Enter/Leertaste auslösbar, und ein Klick auf ihre **Beschriftung**
   wählt denselben Knoten aus wie ein Klick auf den Balken — einschließlich
   der `?focus`-Umleitung für einen Knoten, den die Seite nicht zeigen kann,
   auch bei Tastaturauslösung. Das Klickziel ist nicht mehr auf den 6 px
   schmalen Balken beschränkt; der Balken selbst bleibt anklickbar.
6. **Fokus sichtbar:** `app.css` enthält mindestens eine `:focus`- oder
   `:focus-visible`-Regel, die einen sichtbaren Indikator setzt (heute:
   keine); nirgends steht `outline: none` ohne Ersatz.
7. **Fokusfarbe:** Der Fokusindikator benutzt nicht `--signal` (E5) und
   erreicht mindestens 3:1 gegen `--paper` und gegen `--surface`, in beiden
   Themes.
8. **Registerkarten:** Jeder Knopf einer `role="tablist"`-Gruppe trägt
   `role="tab"` und verweist per `aria-controls` auf sein Panel mit
   `role="tabpanel"`; genau ein Knopf je Gruppe trägt `aria-selected="true"`
   (die übrigen `"false"`), und der Wert wandert beim Wechsel mit — auch bei
   einer serverseitigen Vorauswahl (etwa Landung auf dem Raw-Tab über
   `raw_from_seq`). Klasse `active`, sichtbares Panel und ARIA-Zustand stimmen
   überein.
9. **Pfeiltasten in den Karten:** Links/Rechts wechselt die aktive
   Registerkarte innerhalb ihrer Gruppe; nur die aktive Karte ist ein
   sequenzieller Tab-Halt, und Auswahlzustand, Tab-Halt, Klasse `active` und
   sichtbares Panel wechseln gemeinsam — auch bei einem Wechsel per Klick.
10. **Auswahl ausgezeichnet:** Der ausgewählte Knoten ist maschinenlesbar als
    solcher erkennbar, nicht nur über die Klasse `selected`. Ein Wechsel nimmt
    die Auszeichnung am vorherigen Knoten zurück; sie greift bei Auswahl über
    Baum wie Timeline, per Maus wie Tastatur, sowie bei `?focus`-Landung.
    Bloßer Navigationsfokus wird nicht als Auswahl ausgezeichnet.
11. **DOM-Gewicht:** Die Zahl der `data-tree-entry`-Marker und die Zahl der
    gerenderten Elemente der Baum-Spalte sind gegenüber dem Stand vor diesem
    Issue bei gleicher Eingabe unverändert; die serverseitige Baum-Spalte hat
    höchstens einen neuen Tastatur-Einstiegspunkt (E3).
12. **Bestand erhalten:** `<label>` am Typfilter, `aria-label` am Suchfeld,
    `aria-expanded` am Falt-Knopf und `aria-hidden` an den Statusglyphen sind
    unverändert vorhanden.
13. **Contract:** Die Antworten von `GET /api/runs` und
    `GET /api/runs/{repo}/{run_id}` sind durch dieses Feature in allen
    Feldern, Typen und Werten unverändert; keine neuen Routen, keine neuen
    Query-Parameter. Ein Regressionstest fixiert diese Unveränderlichkeit.
14. **Gates grün:** `uv run ruff check .` und `uv run pytest -x -q`. Keine
    neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN.

## Definition of Done

- Alle Acceptance Criteria 1–14 erfüllt und, soweit automatisiert prüfbar,
  durch Tests belegt.
- Das Tastatur- und Auswahlverhalten (AC 1–5, 8–10) wird im vorhandenen
  Harness `tests/gui_js_harness.js` / `tests/gui_js_harness.py` gegen das
  ausgelieferte `app.js` geprüft; die ARIA-, DOM- und CSS-Aussagen
  (AC 6, 7, 11, 12) und der Contract (AC 13) in den üblichen
  `tests/test_gui_*.py`. Der Harness ist ein reiner `node`-Prozess ohne
  Browser und ohne Layout-Engine (Entwicklungswerkzeug, keine
  Laufzeit-Dependency); simulierte Layout-Assertions oder ein neues
  Browser-Test-Subsystem entstehen nicht.
- Richtwert **~12 neue Tests** unter `tests/`; deutlich mehr als ~17 ist
  Scope-Drift. Bestehende GUI-Tests bleiben grün, ohne inhaltlich
  umgeschrieben zu werden.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Kein flake8,
  kein isort, kein black; `ruff format` ist kein Gate — der veraltete Hinweis
  in `docs/GUI-SPEC.md` (Abnahmepunkt 10) begründet keine zusätzlichen Gates.
- Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
  Webfont, keine Barrierefreiheits-Bibliothek (E1).
- i18n vollständig in beiden Sprachen mit identischer Schlüsselmenge (A6);
  Doku und Changelog in beiden Sprachen synchron (A7).

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
