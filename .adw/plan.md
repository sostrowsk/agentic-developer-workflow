# Plan — Trace-Baum verdichten: Werkzeug-Rauschen falten statt paginieren

Single-Lane-Projekt (`backend`, siehe `.adw/config.yaml`). Es gibt keine
`frontend`-Lane; Templates, CSS und Vanilla-JS der GUI sind Teil des
Python-Pakets und werden in derselben Lane geändert. Gebaut wird strikt gegen
`.adw/contract.yaml`; der Contract pinnt nur extern beobachtbare Flächen: die
`GET /api/runs/{repo}/{run_id}`-Antwort (insbesondere `tree`, unverändert), das
beobachtbare Seitenverhalten der Baum-Spalte (A1–A6) und die i18n-Anforderung.

## Architektur-Entscheidung (bindend für die Umsetzung)

Die Verdichtung ist eine **seitenlokale Darstellungsschicht** und setzt genau an
einer Stelle an: auf der bereits berechneten, gefensterten Flach-Liste
`tree_window["rows"]` (Liste von `{node, depth}` in Pre-Order) in
`run_detail_page` (`adw/gui/app.py`, um Zeile 2102). Weder `build_tree`
(`adw/gui/model.py`) noch `_serialize` noch `detail["tree"]` (die API-Fläche)
werden angefasst.

Warum diese Stelle:
- Adjazenz in `tree_window["rows"]` ist exakt die Pre-Order-Nachbarschaft, die
  A1/A2/A3 fordern (Aufruf und Ergebnis stehen als Point-Events konsekutiv als
  Kinder ihres `agent.run`). „Direkte Nachbarschaft“ (E4) = Nachbarschaft in
  dieser Zeilenliste. Zusätzlich gilt: Wiederholungen und Gruppen verbinden
  keine getrennten strukturellen Bereiche — eine Strukturgrenze (Wechsel des
  Klapp-/Baum-Kontexts, z. B. ein dazwischenliegender `agent.run`- oder
  `round`-Knoten) beendet jede laufende Wiederholung und Gruppe; nichts wird
  umsortiert oder umgehängt.
- Das Fenster ist bereits geschnitten (`?offset`, Größe ≤ `_DISPLAY_WINDOW`
  = 100), also greift die Verdichtung automatisch nur innerhalb der geladenen
  Seite; eine Gruppe endet an der Seitengrenze (E3), weil die Rows dort enden.
- Das API-`tree` in `detail["tree"]` bleibt die unverdichtete Quelle für
  `_tool_window`, `_pane_nodes`, `_focus_index` — alle unverändert.

Die A4-Relativierung geschieht **ausschließlich in dieser Darstellungsschicht**,
nicht in `_node_label`/`_tool_call_label`: das serialisierte `label` in
`detail["tree"]` bleibt der absolute Wert (unveränderte API), die Baum-Spalte
zeigt darüber die repo-relative Form mit vollem Pfad im `title`. Der
Ziel-Vergleich für A2/A3 nutzt den **rohen** `input.file_path` / `input.pattern`
aus dem Knoten-Payload, nie das (gekürzte oder relativierte) Label.

Die drei Klappebenen (Phase, Gruppe, Wiederholung) und die Default-Faltung sind
reines Client-Verhalten ohne Persistenz; der Server liefert nur die Struktur
(synthetische Gruppen-/Wiederholungsknoten mit Kindern, angehängte Ergebnisse)
und einen Marker, welche Phase per Default offen ist (der „in Baumreihenfolge
erste Fehler“ bzw. die „zuletzt begonnene Phase“ sind über den ganzen Baum
bestimmt, den nur der Server vollständig kennt).

## Workstream: backend

Reihenfolge so gewählt, dass jeder Schritt für sich testbar grün ist.

### B1 — Verdichtungsschicht über der Fensterliste (A1/A2/A3, Kern)
Neue reine Funktion in `adw/gui/app.py`, die `tree_window["rows"]` (die flache
`{node, depth}`-Liste **nach** dem Fenster) in eine Darstellungsstruktur für die
Baum-Spalte überführt. Kein neuer Reader, keine neue Route, keine Änderung an
`_serialize`. Darstellungseinträge unterscheiden nur für die Template-Ausgabe
zwischen: unverändertem Originalknoten, Tool-Aufruf mit angehängtem Ergebnis,
synthetischem Wiederholungsknoten, synthetischem Gruppenknoten.
- **A1 — Ergebnisse falten:** Ein `agent.tool.result` mit vorhandener
  `tool_use_id`, die exakt der `tool_use_id` des **unmittelbar** vorangehenden
  Tool-Aufrufs entspricht, wird nicht als eigene Zeile geführt, sondern an die
  Aufrufzeile gehängt (Ausgang aus dem vorhandenen Ergebnis-Label via
  `_tool_result_label`; Dauer = `ts(result) − ts(call)` nur wenn beide
  Zeitstempel parsebar und Differenz ≥ 0, sonst keine Dauer). Fehlt die
  `tool_use_id`, ist sie ungleich oder trennt die Seitengrenze das Paar, bleibt
  das Ergebnis eigene Zeile (kein Event verschwindet). Keine Zuordnung über
  Werkzeugname, Zeitnähe oder Position.
- **A2 — Wiederholungen zählen:** ≥ 2 unmittelbar aufeinanderfolgende Aufrufe
  gleichen Tools (`Read`/`Grep`/`Glob`) mit exakt gleichem, vorhandenem Rohziel
  (exakter Stringvergleich, keine Normalisierung; `Read` → `input.file_path`,
  `Grep`/`Glob` → `input.pattern`) werden zu einem aufklappbaren
  Wiederholungsknoten mit Zähler und aufsummierter bestimmbarer Dauer (keine
  bestimmbare Dauer → keine Summendauer). Enthält die Einzelaufrufe (inkl.
  ihrer A1-Ergebnisse) in ursprünglicher Reihenfolge. Bricht am ersten nicht
  passenden Ereignis, bei unterschiedlichem/fehlendem Ziel, bei `Write`/`Edit`,
  bei `Bash`/unbekanntem Tool, an Struktur- und Seitengrenze und bei bestimmtem
  Fehler-Ausgang; ein Fehler-Aufruf wird nie aufgenommen und bleibt eigener
  Knoten. Ein unbestimmter Ausgang ist kein Fehler und verhindert keine
  Zusammenfassung.
- **A3 — Gruppieren:** Eine ununterbrochene Folge von `Read`/`Grep`/`Glob`
  (nach A1/A2) wird zu einem aufklappbaren Gruppenknoten mit Anzahl und den
  tatsächlich vorkommenden Operationsarten. Die Anzahl zählt die enthaltenen
  Aufrufe (Einzelaufrufe innerhalb von Wiederholungen eingeschlossen); für die
  Mindestgröße zählt ein Wiederholungsknoten als ein Kindknoten. Die Folge
  bricht an erster Nachricht, jeder Schreiboperation (`Write`/`Edit`), jedem
  Artefakt, jedem bestimmten Fehler, bei `Bash`/unbekanntem Tool sowie an
  Struktur- und Seitengrenze; das brechende Ereignis bleibt eigener,
  ungefalteter Knoten und gehört nicht zur Gruppe.
- **Mindestgröße:** Wiederholungsknoten ≥ 2 Aufrufe, Gruppenknoten ≥ 2
  Kindknoten (nach A1/A2). Was die Schwelle nicht erreicht, bleibt ohne
  Sammel-Hülle stehen (auch ein einzelner Wiederholungsknoten).
- **Fehler-Definition:** bestehende Label-Regel (`is_error: true`, sonst
  `exit_code != 0`); ohne gültiges Outcome-Signal ist der Ausgang unbestimmt
  und wird nie als Erfolg dargestellt.
- Synthetische Knoten sind Auswahl-/Pane-neutral (E9): sie tragen keine
  `data-seq`; nur die weiterhin einzeln existierenden Original-Knoten behalten
  ihre `data-seq`-Auswahl- und Pane-Semantik.

### B2 — Repo-relative Pfaddarstellung (A4)
- Der Repo-Root ist `ref.path` (bereits in `run_detail_page` vorhanden); an die
  Verdichtungsschicht durchreichen.
- Für Tool-Knoten mit Dateiziel: sichtbarer Text = Pfad ohne Repo-Präfix,
  `title`-Attribut = voller Pfad. Pfade außerhalb des Repos und nicht als
  Repo-Pfad erkennbare Werte bleiben im sichtbaren Text unverändert (nicht
  fälschlich relativiert).
- Nur die Baum-Spalte betroffen; `detail["tree"]`-`label` bleibt absolut. Die
  Ausgabe bleibt HTML-escaped durch Jinja; kein neuer Markup-Vertrag.

### B3 — Zeilenbilanz (A6)
- Serverseitig je geladener Seite aus der Verdichtungsstruktur bilden, unabhängig
  vom Client-Klappzustand:
  - **Zeilen:** Knoten der verdichteten Liste außerhalb jedes Sammelknotens
    (Original-Knoten + synthetische Gruppen-/Wiederholungsknoten). Kinder von
    Sammelknoten und angehängte Ergebnisse zählen nicht.
  - **Eingefaltete Events:** Events der ungefalteten Seitenliste minus der
    Zeilen, die selbst ein Original-Event sind; erfasst angehängte Ergebnisse
    und alle Sammel-Mitglieder inkl. deren Ergebnisse, jedes Event genau einmal,
    auch bei Verschachtelung.
- Als Kontext an das Template geben; unter dem Baum **neben** dem bestehenden
  `window_nav` rendern (dieses bleibt unverändert und funktional).

### B4 — Template: verdichtete Baum-Spalte + drei Klappebenen (A1–A4, A6)
`adw/gui/templates/run_detail.html`, Abschnitt `<ul class="trace-list">`
(Z. 399–408):
- Statt flacher `tree_window.rows`-Schleife die Verdichtungsstruktur rendern:
  Original-Knoten (mit `data-seq`, wie heute), angehängte Ergebnisse am rechten
  Rand der Aufrufzeile (keine zweite auswählbare Zeile), Gruppen-/
  Wiederholungsknoten als aufklappbare, nicht auswählbare Hüllen.
- Genau drei Klappebenen: Phase, Gruppenknoten, Wiederholungsknoten. Andere
  Knoten (`agent.run`, `round`, …) bekommen keine eigene Klappmechanik; ihr
  Teilbaum ist innerhalb einer aufgeklappten Phase sichtbar.
- Repo-relativer Pfadtext + `title`=voller Pfad (B2). Zähler-/Gruppen-Labels aus
  i18n (B6). Zeilenbilanz (B3) neben `window_nav`.
- Bestehende Statusdarstellung/Sekundärtypografie nutzen (E6);
  `adw/gui/static/app.css` nur um die nötige Einrückungs-, Toggle- und
  Metadaten-Darstellung ergänzen — kein Farbsystem-Refactoring, keine neue
  Dependency, kein CDN.

### B5 — Client: Default-Faltung, `?focus`-Aufklappen, Ergebnis-Umlenkung (A5)
`adw/gui/static/app.js` und die Server-`?focus`-Behandlung in
`run_detail_page`:
- Default-Faltung (Client, ohne Persistenz): Baum öffnet mit zugeklappten
  Phasen; aufgeklappt startet ausschließlich die Phase mit dem in Baumreihenfolge
  ersten bestimmten Fehler, sonst die zuletzt begonnene Phase. Der Marker dafür
  wird serverseitig gesetzt (voller Baum nötig), die Klappwirkung ist Client.
- `?focus=<seq>` (bestehendes Feature): klappt unabhängig von der Default-Faltung
  alle Klapp-Vorfahren des Zielknotens auf, **die in der geladenen Seite liegen**
  (Phase, ggf. Gruppe, ggf. Wiederholung), sodass der Knoten sichtbar und über
  die bestehende Auswahlmechanik auswählbar ist. Knoten, deren Klapp-Elternteil
  vor der geladenen Seite liegt, werden aufgeklappt dargestellt, nicht versteckt;
  keine seitenfremden Vorfahren materialisieren (E3).
- Zeigt `?focus` auf ein nach A1 gefaltetes `agent.tool.result`, wird auf den
  zugehörigen Aufruf-Knoten (gleiche `tool_use_id`) umgelenkt — serverseitig für
  die Fensterpositionierung, clientseitig für die Auswahl: dessen `seq` wird
  ausgewählt, dessen Pane gezeigt. `_focus_index` / Auswahl adressieren weiterhin
  ausschließlich Original-`seq`s; der Ergebnis-Inhalt bleibt über den Tools-Tab
  der Detail-Panes erreichbar (E8). Unbekannte oder nicht zuordenbare
  Fokuswerte behalten das bestehende Verhalten.
- Gruppen-/Wiederholungsüberschriften schalten nur ihren eigenen
  Aufklappzustand; Klicks auf Originalknoten, Timeline-Deep-Links, Pane-Auswahl
  und Run-Kontext bleiben unverändert.

### B6 — i18n (A7)
`adw/gui/i18n.py`: Labels für A2 (Wiederholungszähler), A3 (Gruppen: Anzahl +
Operationsarten) und A6 (Zeilenbilanz) in `_EN` und `_DE`, Pluralformen korrekt,
identische Schlüsselmengen (die bestehende i18n-Paritätsprüfung bleibt grün).
Event-, Pfad-, Werkzeug- und Payload-Inhalte werden nicht übersetzt.

### B7 — Doku & Changelog (synchron)
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` im Trace-Baum-Abschnitt synchron:
  verdichtete Baum-Spalte, Ergebnis-Faltung, Wiederholungen, Gruppen,
  repo-relative Pfade, drei Klappebenen, Seitengrenzen-Semantik (E3),
  Zeilenbilanz, Deep-Link-/Umlenk-Verhalten (A5).
- `Unreleased`-Sektion in `CHANGELOG.md` und `CHANGELOG.de.md` synchron ergänzen.
- Keine anderen Produkt-, API- oder Bedienflächen dokumentieren, da unverändert.

## Tests (Richtwert ~12, deutlich > ~18 wäre Scope-Drift)
Neu unter `tests/test_gui_*.py`, Muster der bestehenden GUI-Tests; für reines
Client-Verhalten steht der bestehende JS-Harness
(`tests/gui_js_harness.js` / `tests/gui_js_harness.py`) zur Verfügung.
Mindestens:
1. A1 — Ergebnis ohne zuordenbaren Vorgänger (fehlende/ungleiche `tool_use_id`)
   bleibt eigener Knoten; zugeordnetes Ergebnis erscheint an der Aufrufzeile.
2. A1 — unbestimmter Ausgang wird nicht als Erfolg dargestellt; Dauer nur bei
   parsebaren Zeitstempeln mit Differenz ≥ 0.
3. A2 — Zähler bei gemischten Zielen (nur zielgleiche Nachbarn zählen).
4. A2 — bestimmter Fehler zwischen zielgleichen Aufrufen bricht die Wiederholung
   (Beispiel `Read X` ok / Fehler / ok → drei eigene Knoten).
5. A3 — Gruppenabbruch an Nachricht.
6. A3 — Gruppenabbruch an Schreiboperation (`Write`/`Edit`).
7. A3 — Gruppenabbruch an Fehler; brechendes Ereignis bleibt eigener Knoten.
8. A3 — keine Gruppen-Hülle unterhalb von zwei Kindknoten (auch nicht um einen
   einzelnen Wiederholungsknoten).
9. A4 — Pfad außerhalb des Repos bleibt unverändert; Repo-Pfad wird relativiert,
   voller Pfad im `title`, kein absoluter Repo-Pfad im sichtbaren Text.
10. A5 — Default-Faltung mit Fehler (Fehler-Phase offen) und ohne Fehler
    (zuletzt begonnene Phase offen).
11. A5 — `?focus` auf ein gefaltetes Ergebnis (Umlenkung auf Aufruf-Knoten,
    dessen Pane) und auf einen Knoten, dessen Phase vor der geladenen Seite
    beginnt (bleibt sichtbar und auswählbar).
12. E3 — Gruppenabbruch an der Seitengrenze; Fensterinhalt, `?offset`-Navigation
    und Blättertotal bleiben unverändert.
13. A6 — Zeilenbilanz nach den definierten Formeln (Referenzbeispiel der Spec:
    10 Events → 4 Zeilen, 7 eingefaltet).
14. Contract-Regression — `GET /api/runs/{repo}/{run_id}` liefert unter `tree`
    für denselben Event-Log exakt die bisherige unverdichtete Struktur (keine
    synthetischen/entfernten Knoten, keine neuen Felder).

Bestehende Trace-Tests (Auswahl, Panes, `?focus`, `?tools_offset`) und die
i18n-Paritätsprüfung bleiben grün, ohne inhaltlich umgeschrieben zu werden.

## Messbare Gesamtaussagen (Referenzlauf `d0bdb365`, Prüfziele)
- Beim Öffnen ≤ so viele Baum-Knoten wie Phasen (12) + Knoten der einen offenen
  Phase.
- Erster `agent.run` (Spec-Agent) aufgeklappt ≤ 10 direkte Kindzeilen
  (durchgerechnet 9: 5 `agent.message`, 3 Gruppen, 1 `Write`). Die Schranke wird
  nicht durch Falten von Nachrichten oder Gruppen über Nachrichten hinweg
  unterboten (A3/E4) — entschieden.
- Zu jedem Knoten der ungefalteten Liste eine über ≤ 2 Klicks ab aufgeklappter
  Phase erreichbare Repräsentation (E2): eigener Knoten, angehängtes Ergebnis
  oder Einzelknoten in geöffneter Gruppe/Wiederholung.

## Gates
`uv run ruff check .` und `uv run pytest -x -q` grün. Keine neue
Laufzeit-Dependency, kein Frontend-Paket, kein CDN — Vanilla JS,
handgeschriebenes CSS.

## Deferred (bewusst nicht gebaut — unverändert aus der Spec)
Diese Ideen sind defensibel, aber für dieses Issue unverhältnismäßig; sie
gehören nicht in die Akzeptanzkriterien und werden auch im Review-/Fix-Zyklus
nicht nachgebaut:
- Suche und Filterchips über dem Baum.
- Automatisches Aufspringen zum ersten Fehler über die A5-Default-Faltung und
  `?focus`-Deep-Links hinaus.
- Aggregation gleicher Aufrufe über die ganze Phase oder über nicht benachbarte
  Ereignisse hinweg (nur direkte Nachbarschaft, E4).
- Rendern des Issue-Payloads als Markdown.
- Auslagern der Reader-Probleme in einen eigenen Bereich.
- Persistieren des Aufklappzustands.
