# Spec — GUI-Redesign 3: Das Arbeitsfeld zuerst

Setzt auf dem gemergten Stand **0.23.0** auf. Tokens, Skalen, Signalfarbe,
Dark Mode und die Zeitachse (Brief 1) sowie die Zeitgrößen und ihr Vokabular
(Brief 2) werden **benutzt, nicht revidiert**. Die im Issue genannten Messwerte
(vom 2026-09-13, Lauf `16f39431`, 1440 px) sind die Vergleichsbasis und vor dem
Bauen gegen den dann aktuellen Stand zu prüfen. Rein darstellend — kein
Vertragswechsel.

## Goal

Die Run-Detail-Seite so umräumen, dass das eigentliche Instrument — der
Trace-Baum — unmittelbar unter dem Seitenkopf beginnt und mehr Platz bekommt,
während die zwei bisher davorstehenden Zusammenfassungen darunter rücken und
zugeklappt aussagekräftig bleiben. Die Timeline lernt dieselbe Regel wie die
Zeitachse: Geometrie in die Spur, Wörter an eine Stelle, deren Lesbarkeit nicht
von der Balkendauer abhängt. Die vollständige, ungefensterte Baum-Ausgabe wird
als bewusste Entscheidung festgeschrieben und durch einen Test fixiert.

## Scope

- **A1 — Reihenfolge:** „Planned tasks" und „Change scope" rücken **hinter**
  das dreispaltige Arbeitsfeld (Trace-Baum │ Detail-Panes │ Run-Kontext). Der
  Trace-Baum beginnt damit unmittelbar unter dem Seitenkopf. Bindende
  Endreihenfolge: Kopf (Titel, Zeitachse, Registerkarten) → Arbeitsfeld →
  „Planned tasks" (zugeklappt) → „Change scope" (zugeklappt). Keine weitere
  Umstellung.
- **A1 — Zugeklappt:** Beide Blöcke rendern als native `<details>` **ohne**
  `open`-Attribut, mit `<summary>` — wie die Sammelknoten des Trace-Baums.
  Kein JavaScript, kein Client-Zustand, keine Persistenz, kein Query-Parameter.
  Ein Klick auf die Zusammenfassungszeile ist die einzige Bedienung.
- **A2 — Zusammenfassungszeile „Planned tasks":** Die `<summary>` nennt je Lane
  deren Name, die Zahl der Aufgaben und den bestehenden Lane-Zustand, ohne
  Aufklappen; der Zustand wird nicht neu hergeleitet. Beim Öffnen bleiben die
  bisherigen Aufgaben je Lane zugänglich. Gibt es kein Plan-Skelett, wird der
  Block **gar nicht** gerendert (wie heute) — kein leerer aufklappbarer Block.
- **A2 — Zusammenfassungszeile „Change scope":** Die `<summary>` nennt die Zahl
  der geänderten Dateien über alle beobachteten Lanes sowie die Summen der
  Plus- und Minuszeilen. Binärdateien zählen bei der Dateizahl mit, tragen aber
  nichts zu den Zeilensummen bei. Liegt kein verwertbarer Diff vor, sagt die
  Zeile das (erklärende Zeile statt einer Null); ein verwertbarer Diff ohne
  geänderte Dateien bleibt davon unterscheidbar. Der bisherige Blockinhalt
  bleibt beim Öffnen zugänglich.
- **A3 — Baum-Spalte bekommt Platz:** Das Verhältnis des `trace-layout`-Grids
  wird so verschoben, dass die Baum-Spalte mindestens so breit ist wie die
  Panes-Spalte. Die Kontext-Spalte behält ihre `minmax`-Untergrenze und bleibt
  die schmalste. Spaltenzahl bleibt drei, Reihenfolge bleibt, `min-width: 0`
  und die Überlauf-Regeln bleiben. Genaue Werte sind Gestaltungsspielraum.
- **A4 — Timeline-Beschriftung:** Die Beschriftung eines Balkens verlässt den
  proportional bemessenen Balken. Der Balken wird reine Geometrie; sein Name
  (`_timeline_bar_label`) erscheint an einer Stelle, deren Lesbarkeit **nicht**
  von der Balkenbreite abhängt (über, unter oder neben dem Balken —
  Gestaltungsspielraum). In einer Spur mit vielen kurzen Balken überlagern sich
  die Namen nicht, und die Zuordnung Name↔Balken bleibt erkennbar. Das
  `title`-Attribut, die Spurbeschriftung links (`.tl-lane-label`, feste 7 rem),
  `left`/`width` in Prozent als Geometriequelle und die Unterscheidung
  aktiv / wartend / noch laufend bleiben unverändert.
- **A5 — Baum-Größe ausgesagt und geprüft:** Ein Test fixiert je Knoten des
  serialisierten Baums genau ein `data-tree-entry` in der gerenderten
  Baum-Spalte, keine Dopplung, über mehrere Fixture-Größen — darunter ein Baum
  jenseits von 200 Knoten. Ein der Verdichtung an seinen Aufruf gehängtes
  Ergebnis behält seinen eigenen Marker (heutiger korrekter Stand: 844 Knoten =
  844 Marker). Die Spec sagt ausdrücklich: die Baum-Spalte rendert vollständig,
  die Lesbarkeit kommt von der Verdichtung, nicht von einem Schnitt, und die
  Schranke „höchstens 200 Marker je Sammlung" gilt nur noch für die
  Tools-Einträge (`data-tool-entry`). Der Kommentar in
  `tests/test_gui_bounded_dom.py` wird auf diesen Stand gebracht.
- **A6 — i18n:** Neue Beschriftungen (Zusammenfassungszeilen aus A2, etwaige
  Timeline-Beschriftung aus A4) in `adw/gui/i18n.py` in beiden Sprachen,
  identische Schlüsselmengen, Pluralformen korrekt.
- **A7 — Doku und Changelog:** `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md`
  synchron: neue Blockreihenfolge, die zugeklappten Zusammenfassungen, die
  Timeline-Beschriftung, die Aussage zur Baum-Größe aus A5. `CHANGELOG.md` und
  `CHANGELOG.de.md` synchron ergänzen.

## Non-Goals / Scope-Deckel

- Keine neue Route, kein neues Tab, kein neuer Query-Parameter.
- Keine Änderung an Auswahl-, Pane-, `?focus`- oder Deep-Link-Verhalten, an den
  Registerkarten Artefakte und Raw, am Recovery-Karten-Verhalten, an der
  Run-Liste, an der Zeitachse oder an den Kennzahlen aus Brief 2.
- Keine Persistenz, kein Polling, kein neues Zustands-Subsystem, kein
  JavaScript für das Auf-/Zuklappen.
- **E1** Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
  Webfont.
- **E2** Der Trace-Baum bekommt kein Fenster zurück: keine Blätterung, kein
  Lazy-Rendering, keine Knotenobergrenze für die Baum-Spalte. `?offset` bleibt
  für den Baum inert. Bindet auch den Review-Loop.
- **E3** Die Verdichtung des Baums (Faltung, Wiederholungen, Gruppen, drei
  Klappebenen, Standard-Faltung, `?focus`-Verhalten) wird nicht angefasst.
- **E4** Keine neuen Farben, Schriftgrößen oder Abstandswerte; die Gestaltung
  aus Brief 1 wird benutzt.
- **E5** Zahlen und Vokabular aus Brief 2 werden benutzt, nicht revidiert; die
  Run-Liste wird nicht angefasst.
- **E6** Das Tools-Fenster (`_tool_entries` / `_tool_window` / `?tools_offset`),
  seine 200er-Schranke und seine Blätter-Navigation bleiben unverändert.
- **E7** Kein Responsive-Umbau, keine Breakpoints, keine Mobilansicht.
- **E8** Keine Änderung an Ereignistypen, Instrumentierung, Event-Payloads,
  `build_tree`, dem SSE-Pfad oder der Retention.
- Nicht Teil des Contracts (frei änderbar): Klassennamen, Reihenfolge der
  Blöcke im Markup, Markup- und CSS-Wortlaut, Grid-Verhältnisse.

## Acceptance Criteria (messbar)

1. **Reihenfolge:** Im gerenderten Dokument steht der Trace-Baum
   (`.trace-list`) vor „Planned tasks" und „Change scope"; zwischen Seitenkopf
   und Arbeitsfeld steht keiner der beiden Blöcke. Weitere Bereiche werden
   nicht umgeordnet.
2. **Zugeklappt:** Beide Blöcke rendern als `<details>` ohne `open`; im
   Ausgangszustand ist ihr Inhalt nicht sichtbar. Kein JavaScript, kein
   Client-Zustand, keine Persistenz, kein Query-Parameter dafür.
3. **Summary „Planned tasks":** Die `<summary>`-Zeile nennt Lane-Name,
   Aufgabenzahl und Lane-Zustand, ohne dass der Block aufgeklappt werden muss;
   der Zustand wird nicht neu hergeleitet. Ohne Plan-Skelett wird der Block
   nicht gerendert.
4. **Summary „Change scope":** Die `<summary>`-Zeile nennt die Zahl der
   geänderten Dateien und die Summen der Plus- und Minuszeilen; Binärdateien
   zählen bei der Dateizahl mit, nicht bei den Zeilensummen. Ein Lauf ohne
   verwertbaren Diff bekommt eine erklärende Zeile statt einer Null; ein
   verwertbarer Diff ohne geänderte Dateien bleibt davon unterscheidbar.
5. **Spaltenbreite:** Die Baum-Spalte ist im `trace-layout`-Grid mindestens so
   breit wie die Panes-Spalte (heute 434 px gegen 607 px bei 1440 px); die
   Kontext-Spalte behält ihre `minmax`-Untergrenze und bleibt die schmalste.
   Drei Spalten, Reihenfolge, `min-width: 0` und Überlauf-Regeln bleiben.
6. **Umbrüche:** Der Anteil der Knotenbeschriftungen, die auf mehr als eine
   Zeile umbrechen, sinkt messbar gegenüber dem heutigen Stand von 17,8 %
   (103 von 577 bei `16f39431`, 1440 px, Stand 0.23.0). Verglichen wird bei
   gleichem Faltungszustand; Beschriftungen oder Knoten zu entfernen zählt
   nicht als Verbesserung.
7. **Timeline lesbar:** Für `16f39431` ist die Beschriftung jedes der 31 Balken
   lesbar, unabhängig von der Balkenbreite — insbesondere die 24 heute
   abgeschnittenen (gemessen `scrollWidth > clientWidth`; u. a. `pytest` mit
   6 px gegen 43 px Bedarf). In einer Spur mit vielen kurzen Balken überlagern
   sich die Namen nicht. Das `title`-Attribut allein erfüllt dieses Kriterium
   nicht.
8. **Timeline-Geometrie unverändert:** `left` und `width` der Balken in Prozent
   sind für identische Eingangsdaten unverändert; die Unterscheidung
   aktiv / wartend / noch laufend, das `title`-Attribut und die
   Spurbeschriftung links bleiben.
9. **Zähldefinition:** Für mindestens drei verschiedene Fixture-Größen —
   darunter ein Baum mit mehr als 200 Knoten — gilt: die Zahl der
   `data-tree-entry`-Marker in der Baum-Spalte ist gleich der Zahl der Knoten
   des serialisierten Baums — kein Knoten fehlt, keiner zählt doppelt. Ein an
   seinen Aufruf gefaltetes Ergebnis behält seinen eigenen Marker.
10. **Kein Fenster:** `?offset` verändert die Baum-Spalte nicht, und für sie
    wird keine Blätter-Navigation gerendert (E2).
11. **Tools-Fenster unberührt:** `?tools_offset`, die 200er-Schranke der
    Tools-Einträge (`data-tool-entry`) und deren Blätter-Navigation verhalten
    sich unverändert; die Schranke gilt nicht für `data-tree-entry`.
12. **Contract:** Die Antwort von `GET /api/runs/{repo}/{run_id}` ist durch
    dieses Feature in allen Feldern, Typen und Werten unverändert — inklusive
    `tree`, `phases`, `raw`, `latest_context`, `problems` und der Kennzahlen
    aus Brief 2; ebenso `GET /api/runs`. Keine neuen Routen, keine neuen
    Query-Parameter. Ein Regressionstest fixiert die Unveränderlichkeit.
13. **i18n:** Alle neuen Beschriftungen liegen in `adw/gui/i18n.py` in beiden
    Sprachen mit identischer Schlüsselmenge und korrekten Pluralformen vor
    (auch für null, eine und mehrere Aufgaben bzw. Dateien). Lane-Namen,
    Dateipfade und Balkennamen sind Inhalte und werden nicht übersetzt.
14. **Doku/Changelog:** `docs/GUI-SPEC.md`/`.de.md` beschreiben synchron die
    neue Blockreihenfolge, die zugeklappten Zusammenfassungen samt
    Leerzuständen, die dauerunabhängige Timeline-Beschriftung und die Aussage
    zur Baum-Größe aus A5 (vollständiges Rendern; Lesbarkeit aus der
    Verdichtung; 200er-Schranke nur für Tools). `CHANGELOG.md`/`.de.md` sind
    synchron ergänzt. Der Kommentar in `tests/test_gui_bounded_dom.py`
    entspricht diesem Stand.

## Definition of Done

- Alle Acceptance Criteria 1–14 erfüllt und, soweit automatisiert prüfbar,
  durch Tests belegt.
- Die Messgrößen aus AC 6 und AC 7 (Umbruch-Anteil, Abschneiden der
  Balkenbeschriftung) werden im **echten Browser** erhoben — Lauf `16f39431`,
  Referenz-Viewport 1440 px, gleicher Faltungszustand wie die Ausgangsmessung —
  und mit ihren Werten dokumentiert; eine protokollierte manuelle Messung
  genügt. Der Harness `tests/gui_js_harness.js` / `tests/gui_js_harness.py`
  läuft ohne Layout-Engine und bleibt reinem Client-Verhalten vorbehalten;
  simulierte Layout-Assertions oder ein neues Browser-Test-Subsystem entstehen
  nicht.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Kein flake8,
  kein isort, kein black; `ruff format` ist kein Gate — der veraltete Hinweis
  in `docs/GUI-SPEC.md` (Abnahmepunkt 10) begründet keine zusätzlichen Gates.
- Richtwert **~11 neue Tests** unter `tests/test_gui_*.py`; deutlich mehr als
  ~16 ist Scope-Drift. Bestehende GUI-Tests bleiben grün, ohne inhaltlich
  umgeschrieben zu werden — erlaubte Ausnahme: Tests, die die heutige
  Blockreihenfolge oder die heutige Timeline-Balkenbeschriftung festschreiben,
  werden mit begründendem Kommentar auf den neuen Stand gehoben.
- Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN, keine
  Webfont (E1).
- Doku und Changelog in beiden Sprachen synchron.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Volltextsuche oder Filterchips über dem Trace-Baum.
- Zusammenklappbare oder in der Breite ziehbare Spalten des Arbeitsfelds.
- Ein Lazy-Rendering des Baums oder irgendeine andere Knotenschranke (E2).
- Zoom, Schwenken oder Zeitlupe in der Timeline; Zusammenfassen von Balken.
- Zusammenführen von Zeitachse und Timeline zu einer einzigen Darstellung.
- Eine eigene Ansicht für die Reader-Probleme.
- Persistieren des Auf-/Zuklappzustands der beiden Zusammenfassungsblöcke.
