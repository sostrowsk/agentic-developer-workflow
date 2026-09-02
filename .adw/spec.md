# Spec — Trace-Baum verdichten: Werkzeug-Rauschen falten statt paginieren

## Ziel
Die Baum-Spalte des Trace-Tabs stellt einen Lauf so dar, dass Phasen,
Nachrichten, Schreiboperationen, Artefakte und Fehler hervortreten und
Werkzeug-Rauschen gefaltet ist statt Zeile für Zeile paginiert:
Ergebnis-Events hängen an ihrem Aufruf, wiederholte und benachbarte
Lese-/Such-/Glob-Operationen werden zu aufklappbaren Sammelknoten, Pfade sind
repo-relativ, und der Baum öffnet mit zugeklappten Phasen. Die Verdichtung ist
reine Darstellung auf dem bereits gebauten Baum und wird vollständig aus dem
vorhandenen Event-Log abgeleitet; kein Event verschwindet, jedes bleibt über
höchstens zwei Klicks ab aufgeklappter Phase erreichbar. Unbestimmte
Werkzeugausgänge bleiben unbestimmt und werden nirgends als Erfolg dargestellt.

## Scope
- Wie die Baum-Spalte (`<ul class="trace-list">`) im Trace-Tab ihre Knoten aus
  dem serialisierten Baum bildet, faltet, gruppiert und beschriftet:
  - Falten eines `agent.tool.result` in den unmittelbar vorangehenden
    `agent.tool.call` bei gleicher vorhandener `tool_use_id` (A1).
  - Wiederholungsknoten für unmittelbar aufeinanderfolgende, zielgleiche
    Aufrufe von `Read`/`Grep`/`Glob` (A2).
  - Gruppenknoten für ununterbrochene Folgen von `Read`/`Grep`/`Glob` (A3).
- Auf-/Zuklapp-Mechanik im Baum als reines Client-Verhalten (kein
  Server-Zustand, keine Persistenz), inklusive Default-Faltung und
  `?focus=<seq>`-Aufklapppfad (A5).
- Repo-relative Pfaddarstellung mit vollem Pfad im `title`-Attribut (A4).
- Zeilenbilanz unter dem Baum neben der bestehenden Blätterangabe (A6).
- i18n-Labels für Zähler, Gruppen und Bilanz in beiden Sprachen (A7).
- Die Verdichtung ist eine seitenlokale Darstellungsschicht der Baum-Spalte;
  die API-Antwort unter `tree` bleibt unverändert (siehe Contract).
- Synchrone Nachführung von `docs/GUI-SPEC.md`, `docs/GUI-SPEC.de.md` und der
  `Unreleased`-Sektion in `CHANGELOG.md` / `CHANGELOG.de.md`.

## Nicht-Ziele / Scope-Deckel
- Keine neue Route, kein neues Tab, keine Änderung an Timeline, Artefakten,
  Rohansicht, SSE-Pfad oder Run-Liste; keine Änderung an der API-Antwort von
  `GET /api/runs/{repo}/{run_id}` einschließlich `tree` (siehe Contract).
- Keine neuen Event-Typen, keine Änderung an Instrumentierung oder
  Event-Payloads. (E1)
- Keine Änderung an `build_tree` in `adw/gui/model.py` — die Verdichtung setzt
  auf dem gebauten Baum auf.
- Keine Änderung an der Blätterung: dasselbe bewegliche Fenster über die flache
  Knotenliste, `?offset`, Fenstergröße 100. Gefaltet wird innerhalb der
  geladenen Seite; eine Gruppe endet an der Seitengrenze und greift nicht auf
  die nächste Seite vor. (E3)
- Kein neues Layout: dreispaltige Trace-Ansicht, Knoten-Auswahl per Klick,
  Detail-Panes, Run-Kontext-Panel und `?focus`-Deep-Links bleiben genau wie sie
  sind. (E5)
- Keine Änderung am zweiten Fenster über die Werkzeug-Einträge in den
  Detail-Panes (`_tool_entries` / `_tool_window` / `?tools_offset`): dort
  bleiben `agent.tool.call` und `agent.tool.result` getrennte Einträge; A1 gilt
  ausschließlich für die Baum-Spalte. (E8)
- Keine eigene Auswahl-/Pane-Semantik für Gruppen-/Wiederholungsknoten. (E9)
- Kein Farbsystem-Refactoring; vorhandene Statusdarstellung und
  Sekundärtypografie genügen. (E6)
- Kein Frontend-Zustand jenseits von Auf-/Zuklappen: kein Filter, keine Suche,
  kein Sortieren. (E7)
- Keine Persistenz, kein Polling, kein neues Zustands- oder
  Persistenz-Subsystem. Die Statuswerte aus dem vorangegangenen Lauf
  (`waiting`, `awaiting_approval`) werden übernommen, nicht angefasst.
- Das Log wird nicht repariert: ein unbestimmter Ausgang (`is_error` null, kein
  `exit_code`) wird nirgends als Erfolg dargestellt und nirgends erfunden.
- Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN — Vanilla JS,
  handgeschriebenes CSS.

## Contract
Repräsentationsgrenze (entschieden): Die Verdichtung ist eine seitenlokale
Darstellungsschicht der Baum-Spalte und erreicht die API nicht. Sie ist
seitenabhängig (E3: Faltung innerhalb der geladenen Seite, Gruppen enden an
der Seitengrenze) und kann deshalb nicht in dem seitenunabhängigen `tree` der
API abgebildet werden.
- `GET /api/runs/{repo}/{run_id}` liefert unter `tree` weiterhin den
  unverdichteten serialisierten Baum in heutiger Struktur: keine synthetischen
  Knoten, keine neuen Felder, keine entfernten Knoten — keine
  Contract-Änderung. Blätter-Totale und `?offset`-Semantik speisen sich
  unverändert aus dem flachen Fenster über der unverdichteten Knotenliste.
- Angehängte Ergebnisse, Wiederholungs- und Gruppenknoten existieren
  ausschließlich in der gerenderten Baum-Spalte der geladenen Seite. Ihre
  konkrete Markup-Form ist gemäß Issue kein Contract („kein Markup-/
  CSS-Wortlaut"); normativ ist das in A1–A6 beschriebene beobachtbare
  Verhalten der Seite.
- Die `?focus`-Umlenkung auf den Aufruf-Knoten (A5) ist Seitenverhalten, kein
  API-Feld; Auswahl und `?focus` adressieren weiterhin ausschließlich
  Original-`seq`s.
- Alle übrigen Antwortflächen, insbesondere die Statuswerte `waiting` und
  `awaiting_approval`, bleiben unverändert. Nicht Teil des Contracts: interne
  Helper-Signaturen, Dictionary-Schlüssel jenseits der Antwortfelder,
  Markup-/CSS-Wortlaut.
- Ein Regressionstest fixiert, dass die `tree`-Antwort der API durch dieses
  Feature strukturell unverändert bleibt.

## Normative Definitionen (bindend, nicht neu herzuleiten)
- **Zuordnung Aufruf→Ergebnis:** ausschließlich über gleiche `tool_use_id` bei
  direkter Nachbarschaft. Fehlt sie oder sind sie ungleich, wird nicht
  gefaltet; keine Zuordnung über Werkzeugname, Zeitnähe oder Position.
- **Dauer:** `ts(result) − ts(call)`, wenn beide Zeitstempel parsebar sind und
  die Differenz ≥ 0 ist; sonst unbestimmt und ohne Anzeige (kein Ersatzwert).
  Summen sind die Summe der bestimmbaren Dauern; existiert keine bestimmbare
  Dauer, wird keine Summendauer angezeigt.
- **Werkzeug-Kategorien:** zähl- und gruppierfähig sind genau `Read` (Ziel
  `input.file_path`), `Grep` und `Glob` (Ziel `input.pattern`). `Write` und
  `Edit` werden nie gefaltet und brechen eine Gruppe ab. `Bash` und jeder
  unbekannte Toolname bleiben eigene Knoten und beenden jede laufende
  Wiederholung/Gruppe.
- **Ziel-Vergleich:** exakter Stringvergleich des Rohwerts, keine
  Normalisierung. Fehlt das Ziel, findet keine Zusammenfassung statt.
- **Fehler:** nach bestehenden Label-Regeln (`is_error: true`, sonst
  `exit_code != 0`); ohne gültiges Outcome-Signal ist der Ausgang unbestimmt.
  Ein Aufruf mit bestimmtem Fehler-Ausgang beendet jede laufende Wiederholung
  und Gruppe, wird in keinen Sammelknoten aufgenommen und bleibt eigener,
  ungefalteter Knoten. Ein unbestimmter Ausgang ist kein Fehler und verhindert
  keine Zusammenfassung.
- **Mindestgröße von Sammelknoten:** ein Wiederholungsknoten braucht mindestens
  zwei Aufrufe, ein Gruppenknoten mindestens zwei Kindknoten nach A1/A2. Was
  diese Schwelle nicht erreicht, bleibt ohne Sammel-Hülle stehen.
- **Nachbarschaft:** Gruppierung und Wiederholungsbildung nur über direkte
  Nachbarschaft in der Knoten-Reihenfolge und nur innerhalb des geladenen
  Fensters — kein Zusammenfassen über dazwischenliegende Ereignisse oder
  Seitengrenzen, keine Sortierung, keine Umordnung. (E3, E4)
- **Aufklappebenen:** genau drei — Phase, Gruppenknoten, Wiederholungsknoten.
  Andere Knoten (`agent.run`, `round`, …) bekommen keine eigene Klappmechanik;
  innerhalb einer aufgeklappten Phase ist ihr Teilbaum sichtbar. Die
  „höchstens zwei Klicks"-Garantie (E2) zählt ab aufgeklappter Phase: Gruppe
  öffnen, gegebenenfalls Wiederholung öffnen.

## Akzeptanzkriterien

### A1 — Werkzeug-Ergebnisse falten
- Ein `agent.tool.result`, das über gleiche `tool_use_id` dem unmittelbar
  vorangehenden Tool-Event zugeordnet ist, erscheint in der Baum-Spalte nicht
  mehr als eigener Knoten. Erfolg/Fehler (Quelle: das vorhandene
  Ergebnis-Label) und die bestimmbare Dauer stehen am rechten Rand der
  Aufrufzeile.
- Ist die Dauer unbestimmt, wird keine Dauer angezeigt. Fehlt ein gültiges
  Outcome-Signal, wird der Ausgang nicht als Erfolg dargestellt (betrifft
  ~84 % der Ergebnisse des Referenzlaufs).
- Ein `agent.tool.result` ohne zuordenbaren Vorgänger (fehlende oder ungleiche
  `tool_use_id`) bleibt als eigener Knoten sichtbar — es geht kein Event
  verloren.

### A2 — Wiederholungen zählen
- Mindestens zwei unmittelbar aufeinanderfolgende Aufrufe gleichen Typs mit
  exakt gleichem vorhandenem Rohziel werden zu einem Knoten mit
  Wiederholungszähler und aufsummierter bestimmbarer Dauer zusammengefasst.
- Der Knoten ist aufklappbar und zeigt aufgeklappt die Einzelaufrufe in
  ursprünglicher Reihenfolge, einschließlich ihrer nach A1 gefalteten
  Ergebnisse, Ausgänge und Dauern.
- Aufeinanderfolgende Aufrufe gleichen Typs mit unterschiedlichem oder
  fehlendem Ziel bleiben getrennt (können aber Teil derselben A3-Gruppe sein).
  Eine Wiederholung endet am ersten nicht passenden Ereignis sowie bei `Bash`
  oder unbekanntem Toolnamen.
- Ein Aufruf mit bestimmtem Fehler-Ausgang beendet jede laufende Wiederholung
  und wird nie in einen Wiederholungsknoten aufgenommen. Beispiel: `Read X`
  (ok), `Read X` (Fehler), `Read X` (ok) ergibt drei eigene Knoten — die
  fehlerhafte Zeile bleibt ungefaltet, und vor wie nach ihr entsteht mangels
  zweiten Aufrufs kein Wiederholungsknoten.

### A3 — Datei- und Suchoperationen gruppieren
- Eine ununterbrochene Folge von `Read`/`Grep`/`Glob`-Operationen wird zu einem
  Gruppenknoten mit Anzahl und den tatsächlich vorkommenden Operationsarten
  zusammengefasst.
- Die Folge endet an der ersten Nachricht, jeder Schreiboperation (`Write`,
  `Edit`), jedem Artefakt, jedem Fehler sowie an `Bash`/jedem unbekannten
  Toolnamen. Das brechende Ereignis bleibt eigener, ungefalteter Knoten; ein
  fehlerhafter Werkzeugaufruf gehört selbst nicht zur Gruppe. Gruppen werden
  weder über Nachrichten noch über Schreiboperationen, Artefakte oder Fehler
  hinweg gebildet.
- Der Gruppenknoten ist aufklappbar und enthält die Einzelknoten aus A1/A2
  unverändert und in ursprünglicher Reihenfolge.
- Ein Gruppenknoten entsteht nur, wenn er nach A1/A2 mindestens zwei
  Kindknoten enthielte. Ein einzelner gruppierfähiger Knoten — auch ein
  einzelner Wiederholungsknoten — bleibt ohne Gruppen-Hülle stehen.
- Eine Gruppe endet an der Seitengrenze des Blätter-Fensters und greift nicht
  auf die nächste Seite vor; Fensterinhalt, Fenstergröße und Navigation bleiben
  gegenüber dem ungefalteten Baum unverändert. (E3)

### A4 — Pfade repo-relativ darstellen
- Der Repo-Präfix wird im sichtbaren Text abgeschnitten; der vollständige Pfad
  bleibt als `title`-Attribut am Element erhalten.
- Pfade außerhalb des Repos bleiben im sichtbaren Text unverändert und werden
  nicht fälschlich relativiert. Der Ziel-Vergleich für A2/A3 verwendet
  weiterhin den unveränderten Rohwert.
- Kein Baum-Knoten enthält den absoluten Repo-Pfad im sichtbaren Text.

### A5 — Default-Faltung
- Der Baum öffnet mit zugeklappten Phasen. Aufgeklappt startet ausschließlich
  die Phase, die den in Baumreihenfolge ersten Fehler enthält; gibt es keinen
  Fehler, die zuletzt begonnene Phase.
- Der Aufklappzustand ist reines Client-Verhalten ohne Persistenz.
- Ein `?focus=<seq>`-Aufruf (bestehendes Feature) klappt unabhängig von der
  Default-Faltung alle Klapp-Vorfahren des Zielknotens auf, die in der
  geladenen Seite liegen — Phase, gegebenenfalls Gruppe, gegebenenfalls
  Wiederholung — sodass der Knoten sichtbar und über die bestehende
  Auswahlmechanik auswählbar ist.
- Zeigt `?focus` auf ein nach A1 gefaltetes `agent.tool.result`, wird auf den
  zugehörigen Aufruf-Knoten (gleiche `tool_use_id`) umgelenkt: dessen `seq`
  wird ausgewählt, dessen Pane gezeigt. Der Ergebnis-Inhalt bleibt wie bisher
  über den Tools-Tab der Detail-Panes erreichbar (E8).
- Klappmechanik wirkt nur auf Klapp-Eltern, die in der geladenen Seite liegen.
  Es werden keine Vorfahren-Knoten von außerhalb der Seite materialisiert
  (E3); Knoten, deren Klapp-Elternteil (z. B. der Phasen-Knoten) vor der
  geladenen Seite liegt, werden aufgeklappt dargestellt und nicht versteckt.
- Klappmechanik gibt es ausschließlich für Phasen, Gruppen und Wiederholungen;
  `agent.run`, `round` und andere Knoten erhalten keine zusätzliche Klappebene.

### A6 — Zeilenbilanz sichtbar machen
- Unter dem Baum steht die Bilanz der geladenen Seite aus zwei Zahlen. Bewusst
  nicht „sichtbare Knoten": Die Bilanz wird serverseitig je geladener Seite
  gebildet, der clientseitige Aufklappzustand (A5) verändert sie nicht.
  - **Zeilen:** Anzahl der Knoten der verdichteten Liste außerhalb jedes
    Sammelknotens — eigene Original-Knoten plus synthetische Gruppen-/
    Wiederholungsknoten. Kinder von Sammelknoten und angehängte Ergebnisse
    zählen nicht.
  - **Eingefaltete Events:** Events der ungefalteten Seitenliste minus der
    Zeilen, die selbst ein Original-Event sind. Das erfasst angehängte
    Ergebnisse (A1) und alle Mitglieder von Gruppen/Wiederholungen
    einschließlich deren angehängter Ergebnisse; jedes Event zählt genau
    einmal, auch bei Verschachtelung.
- Beispiel: Seite mit 10 Events — `phase`, `agent.message`, `Read a`+Ergebnis,
  `Read a`+Ergebnis, `Grep p`+Ergebnis, `Write`+Ergebnis — ergibt 4 Zeilen
  (`phase`, `agent.message`, Gruppe{Wiederholung `Read a` ×2, `Grep p`},
  `Write`), davon 3 Original-Events. Bilanz: 4 Zeilen, 7 Events eingefaltet.
- Die bestehende Blätterangabe (`window_nav`) bleibt daneben erhalten und
  funktional.

### A7 — i18n
- Labels für A2, A3 und A6 liegen in `adw/gui/i18n.py` in beiden Sprachen vor,
  Pluralformen korrekt.

### Messbare Gesamtaussagen am Referenzlauf `d0bdb365`
- Beim Öffnen zeigt der Trace-Tab höchstens so viele Baum-Knoten wie der Lauf
  Phasen hat (12), plus die Knoten der einen aufgeklappten Phase.
- Der erste `agent.run`-Knoten (Spec-Agent) zeigt aufgeklappt höchstens **zehn
  direkte Kindzeilen** (heute 45; durchgerechnet 9: 5 `agent.message`,
  3 Gruppen, 1 `Write`). Die Schranke darf nicht dadurch unterboten werden,
  dass Nachrichten gefaltet oder Gruppen über eine Nachricht hinweg gebildet
  werden — das verstieße gegen A3/E4. Diese Frage ist entschieden.
- Zu jedem Knoten der ungefalteten Liste existiert eine über höchstens zwei
  Klicks erreichbare Repräsentation im gefalteten Baum: als eigener Knoten, als
  an einen Aufruf gebundenes Ergebnis oder als Einzelknoten innerhalb einer
  geöffneten Gruppe bzw. Wiederholung. (E2)

## Deferred (bewusst nicht gebaut)
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

## Definition of Done
- A1–A7 und die messbaren Gesamtaussagen am Referenzlauf sind erfüllt und durch
  Tests unter `tests/test_gui_*.py` belegt.
- Neue Tests decken mindestens ab: Faltung ohne Vorgänger (A1), Zähler bei
  gemischten Zielen (A2), Fehler zwischen zielgleichen Aufrufen bricht die
  Wiederholung (A2), Gruppenabbruch an Nachricht/Schreiboperation/Fehler (A3),
  keine Gruppen-Hülle unterhalb von zwei Kindknoten (A3), Pfad außerhalb des
  Repos (A4), Default-Faltung mit und ohne Fehler (A5), `?focus` auf ein
  gefaltetes Ergebnis und auf einen Knoten, dessen Phase vor der geladenen
  Seite beginnt (A5), Gruppenabbruch an der Seitengrenze (E3) sowie die
  Regression, dass die `tree`-Antwort der API unverändert bleibt (Contract).
  Richtwert ~12 neue Tests; deutlich mehr als ~18 ist Scope-Drift.
- Die bestehenden Trace-Tests (Auswahl, Panes, `?focus`, `?tools_offset`)
  bleiben grün, ohne inhaltlich umgeschrieben zu werden.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`. Keine neue
  Laufzeit-Dependency, kein Frontend-Paket, kein CDN.
- Die API-Antwort von `GET /api/runs/{repo}/{run_id}` ist unverändert,
  einschließlich `tree` (siehe Contract).
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` beschreiben synchron die
  verdichtete Baum-Spalte, die drei Klappebenen, die Seitengrenzen-Semantik und
  das Deep-Link-Verhalten; die `Unreleased`-Sektion in `CHANGELOG.md` und
  `CHANGELOG.de.md` ist synchron ergänzt.
