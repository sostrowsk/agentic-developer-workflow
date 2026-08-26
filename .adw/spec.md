# Spezifikation: Plan-Skelett im Trace-Baum — was noch kommt, nicht nur was war

## Ziel
Ist für einen Lauf `plan.md` vorhanden, zeigt das Run-Detail je Workstream ein
read-only **Skelett** der geplanten Aufgaben — die Liste der `###`-Überschriften
unter dem jeweiligen `## Workstream: <name>`-Abschnitt. So liegen im Run-Detail
„geleistet" (Trace-Baum) und „geplant" (Skelett) in einer Ansicht. Der Status ist
grob auf Lane-Ebene: `pending`, solange die Lane läuft oder noch nicht gestartet
ist; `done`, sobald die Lane abgeschlossen ist. Fehlt oder passt `plan.md` nicht,
entfällt das Skelett ersatzlos, ohne jede Änderung am bisherigen Verhalten.

## Umfang (Scope)
- Ableitung des Skeletts aus dem für den Lauf vorhandenen `plan.md`, gelesen
  ausschließlich über den bestehenden whitelist-basierten Artefakt-Pfad.
- Ein neues, additives Skelett-Feld in der Antwort von
  `GET /api/runs/{repo}/{run_id}` (Contract: Single-Lane `backend`).
- Anzeige der Aufgabenliste je Lane neben bzw. über dem Trace-Baum derselben Lane
  im Run-Detail; Zuordnung Workstream → Lane über den gleichen Namen.
- Grober Lane-Status (`pending`/`done`) je Aufgabenliste.
- Doku: `docs/GUI-SPEC.md`/`docs/GUI-SPEC.de.md` (§7.2) und
  `CHANGELOG.md`/`CHANGELOG.de.md` (`Unreleased`).

### Bindende Randbedingungen
- **Parser mit genau zwei Regeln** (E3): Abschnittsbeginn ist eine Zeile
  `## Workstream: <name>`, Abschnittsende die nächste `##`-Überschrift (oder das
  Dateiende); Aufgabe ist jede `###`-Zeile innerhalb des Abschnitts. Kein Muster
  für Kennungen (`B1`, `1.`, `A.1`, `Aufgabe A` …), kein Markdown-Parser, keine
  neue Dependency.
- **Artefakt-Pfad** (E4): `plan.md` wird über den bestehenden, whitelist-basierten
  Artefakt-Pfad gelesen (`_resolve_artifact`): kein neuer Dateizugriff, keine neue
  Route, keine Pfadkonstruktion aus URL-Bestandteilen.
- **Read-only** (E5): reine Anzeige, kein Abhaken, keine Bearbeitung, keine neue
  Persistenz; es werden weder Artefakte noch Events noch Run-State erzeugt oder
  verändert.
- Keine Änderung an Orchestrator, Event-Schema oder Instrumentierung (E2).

## Nicht-Ziele
- **Keine Zuordnung einzelner Trace-Knoten, Agent-Läufe, Runden oder Gates zu
  einzelnen Plan-Aufgaben** (E1). Es gibt im Log kein Aufgaben-Feld; jede
  Zuordnung wäre geraten. Status ausschließlich `pending`/`done` auf Lane-Ebene,
  keine Zwischenzustände je Einzelaufgabe.
- Keine Zerlegung des Überschriftentextes in Kennung und Titel.
- Keine Fortschrittsprozente, kein Fortschrittsbalken, keine Restzeitschätzung,
  kein Gantt.
- Kein neues Tab, kein neuer Persistenz-Zustand.
- Keine Änderung an Timeline/Artifacts/Raw/Diff/SSE.
- Keine Änderung am Trace-Baum, seiner Knotenstruktur oder den übrigen Feldern
  der Detail-Antwort.

## Akzeptanzkriterien

### AC1 — Parser: Abschnitte und Aufgaben (A1, E3)
Aus `plan.md` wird das Skelett nach genau zwei Regeln abgeleitet:
- Ein Workstream-Abschnitt beginnt bei einer Zeile `## Workstream: <name>` und
  endet bei der nächsten `##`-Überschrift (oder am Dateiende). `<name>` ist der
  Text nach `## Workstream: `.
- Aufgabe ist jede `###`-Zeile innerhalb des Abschnitts. Der Aufgabentext ist der
  Überschriftentext unverändert (führende `###`-Markierung entfernt, sonst
  wortgetreu; keine Zerlegung in Kennung und Titel).

Die über die Läufe hinweg uneinheitlichen Formen werden nicht gefiltert:
`### B1 — …`, `### 1. …`, `### A.1 — …`, `### Aufgabe A — …` und
`### Aufgabe B1 — …` ergeben jeweils genau eine Aufgabe mit dem vollständigen
Überschriftentext. `###`-Zeilen außerhalb jedes `## Workstream:`-Abschnitts
zählen nicht; `###`-Zeilen nach der abschließenden `##`-Überschrift werden dem
Workstream nicht mehr zugerechnet.

### AC2 — Skelett-Feld in der Detail-Antwort (A2, Contract)
Die Antwort von `GET /api/runs/{repo}/{run_id}` trägt zusätzlich ein additives
Top-Level-Feld `plan_skeleton`: eine geordnete Liste, ein Eintrag je
Workstream-Abschnitt mit mindestens einer Aufgabe, in der Reihenfolge des
`plan.md`. Jeder Eintrag enthält:
- `workstream`: der Name aus `## Workstream: <name>`;
- `status`: `pending` oder `done` (Lane-Ebene, AC4);
- `tasks`: die Aufgabentexte in Dokumentreihenfolge, wortgetreu.

Ein Workstream-Abschnitt ohne `###`-Aufgabe erzeugt keinen Eintrag. Das Feld ist
ausschließlich additiv; die bestehenden Felder der Antwort und die Knoten des
Trace-Baums (Hierarchie, Reihenfolge, Status, Payloads) bleiben unverändert.

### AC3 — Anzeige neben dem Trace (A2)
Im Run-Detail steht die Skelett-Aufgabenliste je Lane neben bzw. über dem
Trace-Baum derselben Lane (Zuordnung über den gleichen Namen, aktuell `backend`),
so dass „geleistet" (Trace) und „geplant" (Skelett) ohne Tab-Wechsel in einer
Ansicht liegen. Die Anzeige darf nicht an einen existierenden Lane-Knoten im
Trace gebunden sein: Hat die Lane noch keinen Trace-Knoten erzeugt (Lane noch
nicht gestartet), wird das zugehörige Skelett trotzdem sichtbar dargestellt
(`pending`, AC4) — ohne dafür einen leeren oder künstlichen Trace-Knoten zu
erzeugen. Der Trace-Baum selbst und seine Knotenstruktur bleiben unverändert.

### AC4 — Grober Status auf Lane-Ebene (A3, E1)
Jede Aufgabenliste trägt genau einen Status auf Lane-Ebene:
- `done`, sobald die zum Workstream gehörende Lane (die `lane`-Span, deren Name
  dem Workstream-Namen gleicht) mit einem `lane`-Ende und `completed: true`
  abgeschlossen ist; die Liste gilt dann als abgearbeitet und wird entsprechend
  dargestellt.
- Sonst `pending` — auch bei einem `lane`-Ende ohne `completed: true`
  (einschließlich `completed: false` oder fehlendem Wert) und wenn die Lane noch
  nicht gestartet ist.

Kein Status je Einzelaufgabe, kein Status je Trace-Knoten, keine geratene
Aufgabe↔Knoten-Zuordnung.

### AC5 — Robustheit / Fallback (A4)
Fehlt `plan.md`, ist es leer oder unlesbar, oder enthält es keinen
`## Workstream:`-Abschnitt mit mindestens einer `###`-Aufgabe, so entfällt das
Skelett ersatzlos: `plan_skeleton` ist in der Antwort abwesend (keine leere Liste
erzwungen), es entsteht kein Fehler und kein leerer Kasten, und die übrige
Detail-Antwort sowie die bisherige Run-Detail-Ansicht bleiben unverändert. Ein
`plan.md`, das über den Artefakt-Pfad als abwesend gilt (nicht vorhanden oder ein
aus der Run-Verzeichnisgrenze ausbrechender Symlink), zählt hier als fehlend.

### AC6 — Contract-Fläche (Contract-Hinweis)
Extern beobachtbar zugesagt sind ausschließlich: (a) die Gestalt des
`plan_skeleton`-Felds in der Antwort von `GET /api/runs/{repo}/{run_id}` (AC2),
(b) die beiden Parse-Regeln aus AC1 als beobachtbare Zusage und (c) das
Fallback-Verhalten aus AC5 bei fehlendem/unpassendem `plan.md`. Single-Lane
(`backend`). Interne Helper-Signaturen und Markup/CSS sind nicht Teil des
Contracts; kein weiteres Endpoint, Tab oder Feld ändert sich.

### AC7 — Read-only (E5)
Das Skelett ist reine Anzeige: kein Abhaken, keine Bearbeitung, keine neue
Schreibroute, keine neue Persistenz. `plan.md` wird über den bestehenden,
whitelist-basierten Artefakt-Pfad gelesen; es entsteht kein neuer
Dateizugriffspfad, und URL-Bestandteile werden nicht als Dateipfad verwendet.

## Definition of Done
- Alle Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
- AC1–AC7 durch Tests unter `tests/` (`test_gui_*.py`) abgedeckt, darunter
  mindestens ein Test, der die uneinheitlichen Überschriftenformen belegt
  (`### B1 — …` und `### 1. …` ergeben je eine Aufgabe mit unverändertem Text),
  und mindestens ein Test für einen Lauf mit gültigem `backend`-Workstream in
  `plan.md`, aber ohne jedes `backend`-Lane-Event: die `pending`-Aufgabenliste
  ist sichtbar, ohne dass ein leerer oder künstlicher Trace-Knoten entsteht
  (AC3). Richtwert: ~16 neue Tests (Bestand 953); mehr als ~26 ist Scope-Drift.
- Keine neue Laufzeit-Dependency (kein Markdown-Paket), kein CDN.
- Doku aktualisiert: `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` (§7.2 —
  Anzeige, Parse-Muster, API-Feld, Fallback) sowie `CHANGELOG.md` und
  `CHANGELOG.de.md` (Abschnitt `Unreleased`).

## Deferred (bewusst nicht gebaut)
Die folgenden Ideen sind defensibel, aber für diese Aufgabe unverhältnismäßig.
Sie gehören nicht in die Akzeptanzkriterien und werden **auch im
Codex-/Fix-Zyklus nicht nachgebaut**:
- Eine Aufgaben-ID im `lane`-/`round`-/`agent.run`-Event mitschreiben, um echten
  Aufgabenfortschritt (Status je Einzelaufgabe statt grob je Lane) zu zeigen —
  das verlangte eine Änderung an Instrumentierung/Event-Schema (E2).
- Zuordnung einzelner Trace-Knoten zu einzelnen Plan-Aufgaben (E1).
- Plan-Skelett schon vor dem Build aus dem Spec-Gate ableiten.
- Fortschrittsbalken, Prozentanzeige, Restzeitschätzung, Gantt.
- Persistierte oder interaktiv veränderbare Aufgabenstände.
- Ein Muster für Aufgaben-Kennungen (`B1`, `1.`, `A.1`, `Aufgabe A` …) oder ein
  echter Markdown-Parser.
