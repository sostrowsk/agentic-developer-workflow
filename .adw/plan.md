# Implementierungsplan: Plan-Skelett im Trace-Baum

Single-Lane-Projekt (`backend`). Alles — Parser, Detail-Feld, Template-Anzeige,
i18n, Doku, Tests — läuft in der einen Lane `backend`. Es gibt keine getrennte
Frontend-Lane; die serverseitig gerenderte Anzeige gehört zum Backend-Workstream.

Gebaut wird strikt gegen `.adw/contract.yaml`: extern zugesagt sind nur (a) die
Gestalt des additiven `plan_skeleton`-Felds in `GET /api/runs/{repo}/{run_id}`,
(b) die zwei Parse-Regeln aus AC1, (c) das Fallback aus AC5 und (d) das
read-only Template-Verhalten neben dem Trace. Interne Helper-Signaturen, Markup
und CSS sind **nicht** Teil des Contracts.

## Ist-Stand (im Code verankert)
- `GET /api/runs/{repo}/{run_id}` wird in `_run_detail()` (`adw/gui/app.py:1502`)
  zusammengesetzt; die Antwort ist ein Dict mit `run`, `phases`, `tree`,
  `latest_context`, `problems`, `raw` und optional `recovery`. `recovery` wird nur
  gesetzt, wenn nicht `None` (`app.py:1537-1539`) — dasselbe additive Muster nutzt
  `plan_skeleton`.
- `plan.md` steht auf der Whitelist `_ARTIFACT_TOP_LEVEL` (`app.py:1352`) und wird
  ausschließlich über `_resolve_artifact(run_dir, "plan.md")` (`app.py:1367`)
  aufgelöst. Ein Symlink, der die Run-Verzeichnisgrenze verlässt, gilt über
  `_contained()`/`_artifact_present()` als abwesend.
- Lane-Abschluss ist beobachtbar: das `lane`-Ende trägt `payload.completed`
  (`_aggregate_outcome`, `app.py:701`), der Lane-Name steht im `lane`-Start
  bzw. -Ende unter `payload.name` (`model.py`).
- `_run_detail` lädt `events` und den Trace (`roots = build_tree(events)`,
  `app.py:1517`) bereits; daraus wird der Lane-Status abgeleitet — kein neuer
  Reader, keine neue Route.
- Das Detail-Template `adw/gui/templates/run_detail.html` rendert den Trace in der
  `trace-layout`. i18n-Labels liegen in `adw/gui/i18n.py` (`_EN`/`_DE`,
  identische Schlüsselmengen).

## Workstream: backend

### B1 — Parser: Skelett aus plan.md (AC1, AC5, E3, E4)
Neuen internen Helfer in `adw/gui/app.py` nahe `_resolve_artifact`/`_artifacts_*`
anlegen, der aus dem für den Lauf vorhandenen `plan.md` die Workstream-Abschnitte
und ihre Aufgaben ableitet (Signatur intern, nicht im Contract).

- `plan.md` **ausschließlich** über `_resolve_artifact(run_dir, "plan.md")` auflösen
  und wie `_artifact_present` über `_contained(...)` gegen `run_dir` absichern; ein
  ausbrechender Symlink zählt als abwesend (AC5, E4). Keine Pfadkonstruktion aus
  URL-Bestandteilen, kein neuer Dateizugriffspfad, keine neue Route.
- Datei zeilenweise mit genau **zwei Regeln** parsen (E3, kein Markdown-Parser,
  keine Dependency):
  1. Abschnittsbeginn: eine Zeile `## Workstream: <name>`. `<name>` ist der Text
     nach dem exakten Präfix `## Workstream: `. Abschnittsende ist die nächste
     `##`-Überschrift (jede Zeile, die mit `##` beginnt, aber kein `###`) oder das
     Dateiende.
  2. Aufgabe: jede Zeile innerhalb des Abschnitts, die mit dem exakten Präfix
     `### ` (Markierung plus ein Leerzeichen) beginnt. Aufgabentext = der Text
     nach diesem Präfix — Markierung und genau ein Trennleerzeichen entfernt,
     sonst **wortgetreu** (kein weiteres Trimmen, keine Zerlegung in
     Kennung/Titel, kein Muster für `B1`, `1.`, `A.1`, `Aufgabe A` …). Eine
     Zeile, die nur aus `###` ohne Folgetext besteht, ergibt keine Aufgabe.
     `###`-Zeilen außerhalb eines Abschnitts und `###`-Zeilen nach der
     abschließenden `##`-Überschrift zählen nicht (AC1).
- Rückgabe: geordnete Liste, **ein Eintrag je Workstream-Abschnitt mit ≥1 Aufgabe**,
  in `plan.md`-Reihenfolge. Ein Abschnitt ohne `###` erzeugt keinen Eintrag. Jeder
  Eintrag: `{"workstream": <name>, "status": <s. B2>, "tasks": [<text>, …]}`.
- Fallback (AC5): fehlt/leer/unlesbar `plan.md`, oder kein `## Workstream:`-Abschnitt
  mit ≥1 `###`, dann liefert der Helfer eine leere Liste → kein Feld (B3). Fehler
  beim Lesen werden abgefangen (kein 5xx, kein leerer Kasten).

### B2 — Grober Lane-Status pending/done (AC4, E1)
Je Workstream-Eintrag genau einen Status auf **Lane-Ebene** setzen, abgeleitet aus
den in `_run_detail` bereits geladenen `events`/dem Trace — kein neuer Reader:

- `done`, sobald es zum Workstream einen `lane`-Span mit `payload.name == workstream`
  gibt, dessen **Ende** `completed: true` trägt (`_aggregate_outcome(node) ==
  "completed"` auf dem Lane-Knoten, bzw. das entsprechende `lane`-End-Event).
- Sonst `pending` — auch bei `lane`-Ende ohne `completed: true` (`false` oder
  fehlend) und wenn die Lane noch nicht gestartet ist (kein `lane`-Knoten).
- **Kein** Status je Einzelaufgabe, kein Status je Trace-Knoten, keine geratene
  Aufgabe↔Knoten-Zuordnung (E1).

### B3 — Additives Feld in der Detail-Antwort (AC2, AC5, AC6, Contract)
In `_run_detail()` (`app.py:1522-1540`) nach dem Aufbau von `detail` das Skelett
berechnen (Helfer aus B1/B2, gespeist aus `run_dir`, `events` und dem bereits
gebauten `roots`) und **nur bei nicht-leerer Liste** `detail["plan_skeleton"] = …`
setzen — analog zum bestehenden `recovery`-Muster.

- Reine Addition: `run`, `phases`, `tree` (Hierarchie, Reihenfolge, Status,
  Payloads), `latest_context`, `problems`, `raw`, `recovery` bleiben unverändert
  (AC2, AC6). Kein neues Endpoint, kein neues Event, keine neue Persistenz (E5).
- Leere Liste → Schlüssel abwesend (keine erzwungene leere Liste, AC5).

### B4 — Anzeige neben/über dem Trace (AC3, AC7, E5)
Im Detail-Template `run_detail.html` die Skelett-Aufgabenliste je Lane innerhalb der
`trace-layout` rendern, neben bzw. über dem Trace-Baum derselben Lane; Zuordnung
Workstream → Lane über den gleichen Namen (aktuell `backend`).

- Über `detail.plan_skeleton` iterieren (nur vorhanden, wenn nicht leer). Je Eintrag
  Workstream-Name, Status-Marker (`pending`/`done`) und die Aufgabentexte als
  read-only Liste ausgeben.
- Die Anzeige **nicht** an einen existierenden Lane-Trace-Knoten binden: hat die Lane
  noch keinen Knoten (nicht gestartet), erscheint das Skelett trotzdem (`pending`) —
  ohne einen leeren oder künstlichen Trace-Knoten zu erzeugen (AC3). Der Trace-Baum
  und seine Knotenstruktur bleiben unverändert.
- In die vorhandene serverseitige Rendering- und Live-Refresh-Fläche integrieren;
  keine neue Client-seitige Ableitung, kein neues Tab, keine Änderung an
  Timeline/Artifacts/Raw/Diff/SSE.
- Read-only (AC7, E5): reine Anzeige, kein Abhaken, keine Bearbeitung, keine
  Schreibroute. Aufgabentexte sind CONTENT und werden **nicht** übersetzt.
- Fehlt das Feld, entfällt die Anzeige ersatzlos — kein leerer Kasten (AC5). Styles
  ausschließlich für die neue Skelettanzeige ergänzen; Markup und CSS sind nicht
  Teil des Contracts.

### B5 — i18n-Labels für die Skelett-Chrome (AC3)
In `adw/gui/i18n.py` die neuen Chrome-Labels (Überschrift der Skelett-Liste, Status
`pending`/`done` als Anzeige-Labels) in `_EN` und `_DE` mit identischer
Schlüsselmenge ergänzen; deutsche Werte keine bloße Kopie der englischen. Nur Chrome
wird übersetzt, die Aufgabentexte selbst bleiben unangetastet.

### B6 — Doku (DoD)
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` §7.2: Anzeige des Skeletts, die zwei
  Parse-Muster, das additive API-Feld `plan_skeleton`, Lane-Status und das
  Fallback-Verhalten.
- `CHANGELOG.md` und `CHANGELOG.de.md` Abschnitt `Unreleased`: neuer additiver
  Eintrag.
- Bilinguale Konvention einhalten (EN + DE).

### B7 — Tests unter tests/ (test_gui_*.py) (DoD, AC1–AC7)
Richtwert ~16 neue Tests (Bestand 953); mehr als ~26 ist Scope-Drift. Abzudecken:
- **AC1/E3:** die uneinheitlichen Überschriftenformen — mindestens ein Test belegt,
  dass `### B1 — …` und `### 1. …` je genau eine Aufgabe mit **unverändertem** Text
  ergeben; ebenso `### A.1 — …`, `### Aufgabe A — …`, `### Aufgabe B1 — …`.
  Aufgabentext ohne führendes Leerzeichen (exaktes Präfix `### ` entfernt); ein
  bloßes `###` ohne Folgetext ergibt keine Aufgabe. Abschnittsende an nächster
  `##`; `###` außerhalb/nach Abschnitt zählt nicht; Dokumentreihenfolge und
  mehrere Workstream-Abschnitte.
- **AC2/AC6:** `plan_skeleton` ist additiv, geordnet, ein Eintrag je Abschnitt mit
  ≥1 Aufgabe; Abschnitt ohne `###` erzeugt keinen Eintrag; übrige Felder und der
  Trace-Baum bleiben unverändert.
- **AC3:** ein Lauf mit gültigem `backend`-Workstream in `plan.md`, aber **ohne jedes
  `backend`-Lane-Event**: die `pending`-Aufgabenliste ist sichtbar, ohne dass ein
  leerer oder künstlicher Trace-Knoten entsteht.
- **AC4:** `done` bei `lane`-Ende `completed: true`; `pending` bei `completed:
  false`, fehlendem `completed`, laufender und nicht gestarteter Lane. Kein Status
  je Einzelaufgabe.
- **AC5:** fehlendes, leeres, unlesbares `plan.md`, ausbrechender Symlink und
  `plan.md` ohne passenden Abschnitt → `plan_skeleton` abwesend, kein Fehler, kein
  leerer Kasten, restliche Antwort unverändert.
- **AC7/E4/E5:** Lesen über den Artefakt-Pfad, kein neuer Dateizugriff, read-only
  Rendering (wortgetreue Aufgaben, kein leerer Kasten, keine Interaktion).

## Gates / Definition of Done
- `uv run ruff check .` und `uv run pytest -x -q` grün.
- AC1–AC7 durch `tests/test_gui_*.py` abgedeckt (inkl. der beiden oben genannten
  Pflichttests).
- Keine neue Laufzeit-Dependency (kein Markdown-Paket), kein CDN.
- Doku (GUI-SPEC §7.2 EN+DE, CHANGELOG EN+DE `Unreleased`) aktualisiert.

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
