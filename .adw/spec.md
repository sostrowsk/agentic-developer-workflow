# Spec — `codex.author`-Span + Lese-Seite des Event-Logs (Reader, Span-Baum-Modell, Registry)

Normative Grundlage: `docs/GUI-SPEC.md`, insbesondere §4.2 (Record-Schema),
§4.4 (Event-Typen), §7.4 (URL/Slug) und §12 (Resume-Naht). Bei Widerspruch
gilt die GUI-SPEC. Dieser Lauf deckt GUI-SPEC §11 Schritte 6 und 7 ab —
**ausdrücklich ohne Web-Schicht**. Er liefert ausschließlich importierbare
Python-Bausteine.

## Goal

Zwei Ergebnisse:

- **A.** Die Dual-Authoring-Phase erzeugt für jeden Codex-Entwurf einen
  `codex.author`-Span, sodass die spätere Timeline dort keine Lücke mehr zeigt.
- **B.** Ein inkrementeller, fehlertoleranter Log-Leser (`reader`), ein
  Span-Baum-Modell mit der normativen zeitbasierten Waisen-Zuordnung (`model`)
  und eine persistente Repo-Registry mit stabilen URL-Slugs (`registry`) —
  die Datenschicht, auf der die spätere Web-App aufsetzt.

## Scope

- `adw/phases.py`: Der `codex.author`-Span wird an der Aufrufstelle
  (`_codex_draft`) um den bestehenden `ctx.codex.author(...)`-Aufruf gelegt
  (Muster wie `codex.review`, E4). Nur das Umschließen der Aufrufstelle,
  kein weiterer Eingriff.
- Neu: `adw/gui/reader.py`, `adw/gui/model.py`, `adw/gui/registry.py`
  (inkl. `adw/gui/__init__.py`, falls nötig).
- `adw run` trägt sein Ziel-Repo automatisch in die Registry ein (der eine
  Aufrufpunkt, der die Registry befüllt).
- Extern beobachtbare Fläche (Kontrakt): die unten festgelegte öffentliche
  API von `reader`, `model` und `registry`, die Struktur des gelieferten
  Span-Baums, das Format von `~/.adw/repos.json` und die Payload-Felder des
  `codex.author`-Spans. Interne Helper-Signaturen und Mechanik sind frei.

### Öffentliche API (Kontrakt)

Die folgenden Namen, Parameter und Felder sind Kontrakt; alles Übrige
(interne Helper, Parser-Mechanik, Datenhaltung) ist ausdrücklich
nicht-kontraktuell. Feldlisten sind Mindestangaben; Ergebnisobjekte machen
ihre Felder attributiv zugreifbar (dataclass oder pydantic, Wahl frei).

- `adw.gui.reader`:
  - `EventReader(path)` — initialisiert auf den Pfad eines `events.jsonl`.
  - `EventReader.read() -> ReadResult` — liefert nur das seit dem letzten
    bestätigten Offset Hinzugekommene.
  - `EventReader.offset` (int) — der bestätigte Byte-Offset, öffentlich
    lesbar.
  - `ReadResult`: `events` (geparste Records in Dateireihenfolge, als
    Mappings mit allen Originalfeldern) und `problems` (Liste von
    `ReadProblem`) — Events und Probleme sind damit eindeutig getrennt.
  - `ReadProblem`: `kind` (`"bad_line"` | `"seq_gap"`) und die betroffene
    Position (`line_no` und/oder `byte_offset`); bei `seq_gap` zusätzlich
    `expected` und `found`.
- `adw.gui.model`:
  - `build_tree(events) -> list[SpanNode]` — die Wurzeln in Log-Reihenfolge
    (siehe AC 13).
  - `SpanNode`: `span_id`, `type`, `seq` (Start-`seq`), `start_ts`,
    `end_ts` (`None` solange laufend), `duration` (`None` solange laufend),
    `running` (bool), `start_payload`, `end_payload` (`None` solange
    laufend), `children` (geordnete Liste aus Span- und Punkt-Knoten).
    Nur im End-only-Fall aus AC 19 dürfen `start_ts` und `start_payload`
    `None` sein (dann ist `seq` die End-`seq`).
  - `PointNode`: `type`, `seq`, `ts`, `payload` sowie `record`
    (der vollständige Original-Record).
- `adw.gui.registry`:
  - `register_repo(path) -> RepoEntry` — legt den Eintrag an bzw.
    aktualisiert `last_seen`.
  - `load_registry() -> Registry`; `Registry.repos` (Liste von `RepoEntry`)
    und `Registry.resolve(slug) -> RepoEntry | None`.
  - `RepoEntry`: `path`, `slug`, `last_seen`, `exists` (bool — ob der Pfad
    aktuell existiert).
- Format der Registry (`~/.adw/repos.json`), Teil des Kontrakts:

  ```json
  {
    "version": 1,
    "repos": [
      {
        "path": "/absoluter/kanonischer/pfad",
        "slug": "stabiler-url-tauglicher-slug",
        "last_seen": "UTC-Zeitstempel im ISO-8601-Format"
      }
    ]
  }
  ```

## Non-goals (Scope-Deckel — in diesem Lauf NICHT gebaut)

- Keine Web-Schicht: kein FastAPI, uvicorn, HTTP-Endpoint, Jinja2, Template,
  CSS, JavaScript, SSE, kein `adw gui`. Alles Web ist der nächste Lauf.
- Keine Diff-Berechnung aus Snapshot-Refs; der Reader liefert höchstens die
  Ref-Namen aus den `snapshot`-Events.
- Kein Prunen, keine Retention, kein gzip, kein `trace:`-Config-Key (Lauf 5).
- Keine Änderung an `adw/events.py`, `adw/snapshots.py` oder am fachlichen
  Orchestrator-Ablauf. Insbesondere KEINE Cross-Thread-Parent-API im Emitter
  (E2) und kein Nachrüsten von `phase`/`lane` an den Aufrufstellen (E6) — die
  Waisen-Reparatur passiert ausschließlich beim Lesen im Modell.
- Kein Refactoring von `phases.py`; Aufgabe A umschließt die Aufrufstelle,
  mehr nicht.
- Keine neuen Laufzeit-Dependencies des Kernpakets — Stdlib und das bereits
  vorhandene pydantic genügen.
- Keine Persistenz außer `~/.adw/repos.json`: keine Index-, Cache- oder
  Offset-Dateien. Ein Byte-Offset im Speicher ist erlaubt und erwünscht.

## Acceptance criteria

### A — `codex.author` instrumentieren

1. Für jeden ausgeführten `ctx.codex.author(...)`-Aufruf der Dual-Authoring-
   Phase enthält das Event-Log genau ein zusammengehöriges `codex.author`-
   Start-/End-Paar. Der Span liegt an der Aufrufstelle in `phases.py`, der
   Inhalt kommt aus `codex.py` (E4).
2. Der `start`-Payload trägt die Felder nach §4.4: `kind`, `argv[]`, `cwd`,
   `task`. Das `argv` liegt vor dem Öffnen des Spans vollständig vor und steht
   unverändert im `start`-Record — identisch zu dem, was der Runner ausführt,
   nicht nachträglich rekonstruiert.
3. Der `end`-Payload trägt die Felder nach §4.4: `artifacts[]` (die
   zurückgegebenen Dateinamen), `raw_stdout`, `parse_ok`.
4. Der Span wird auch bei gescheitertem oder leerem Codex-Entwurf mit einem
   vollständigen `end`-Record geschlossen (`parse_ok: false`, verfügbarer
   Roh-Output). Das bestehende Degradations-Verhalten der Phase bleibt
   unverändert; der Lauf bricht dadurch nicht ab.
5. Ein Dry-Run bzw. Mock-Runner erzeugt den `codex.author`-Span ebenfalls,
   weil der Span an der Aufrufstelle liegt und nicht im Runner.

### B1 — `adw/gui/reader.py`

6. Der Reader liest `.adw/runs/<run_id>/events.jsonl` und liefert die neu
   gelesenen gültigen Event-Records in Dateireihenfolge. Das `ReadResult`
   trennt Events und gemeldete Probleme (siehe API-Kontrakt).
7. Tail-fähig über Byte-Offset: ein erster Aufruf liefert alle vollständigen
   gültigen Records, ein Aufruf ohne Dateizuwachs liefert nichts erneut, nach
   Anhängen liefert der nächste Aufruf ausschließlich das Neue. Offset und
   Tail-Zustand leben ausschließlich im Speicher; der aktuelle bestätigte
   Offset ist über `EventReader.offset` lesbar. Reader-Aufrufe verändern
   weder das Log noch erzeugen sie Dateien.
8. Es werden ausschließlich vollständige, `\n`-terminierte Zeilen geparst.
   Eine angeschnittene letzte Zeile wird weder geparst noch als kaputt
   gemeldet, ihr Anfang nicht als bestätigt verbraucht; sobald sie durch
   angehängte Bytes vollständig ist, wird sie genau einmal verarbeitet.
9. Eine Lücke in der `seq`-Folge wird als `seq_gap`-Problem gemeldet — mit
   `expected` und `found` —, nicht still überlesen; ein truncatiertes Log
   geht nicht als vollständig durch (§4.2). Die lesbaren Events vor und nach
   der Lücke bleiben verfügbar.
10. Eine vollständige, aber kaputte (nicht parsbare) Zeile wird als
    `bad_line`-Problem für diese Zeile gemeldet; alle nachfolgenden
    vollständigen Zeilen werden weiterhin geliefert.
11. `snapshot`-Events werden unverändert als Events geliefert; damit sind die
    Ref-Namen verfügbar, ohne dass der Reader Diffs berechnet.

### B2 — `adw/gui/model.py` (Span-Baum)

12. `build_tree` überführt eine Folge gelesener Event-Records in einen
    Span-Baum. `start`/`end` mit derselben Span-ID werden zu genau einem
    Knoten mit Dauer aus der `ts`-Differenz; ein Span ohne `end` hat
    `running: true` und weder `end_ts` noch `duration`.
13. Verschachtelung folgt dem `parent`-Feld, soweit gesetzt. Die Wurzeln des
    Ergebnisses sind die `run`-Spans in Log-Reihenfolge. Nach einem Resume
    enthält dieselbe Datei einen weiteren `run`-Start (`resumed_from_seq`
    markiert die Naht, `seq` zählt durch — GUI-SPEC §12); das Modell liefert
    dann mehrere Wurzeln, je Lauf-Segment eine. Ein Log ohne `run`-Start
    (degeneriert/truncatiert) bricht das Modell nicht ab; elternlose Spans
    werden dann selbst Wurzeln.
14. **Enthaltungsregel für Waisen-Spans** (GUI-SPEC §4.2, normativ): Eine
    Waise (Span mit `parent: null`, der nicht die `run`-Wurzel ist) gehört zu
    dem innersten Span, dessen Intervall das Intervall der Waise
    `[start ts, end ts]` echt enthält — unter allen enthaltenden Kandidaten
    also der mit dem spätesten Start; bei Gleichstand entscheidet die höhere
    `seq`. Ein noch laufender Span gilt als enthaltend für alles nach seinem
    Start. `run`-Spans nehmen als Kandidaten regulär teil; eine Waise eines
    Resume-Segments fällt dadurch an dessen `run`-Span (späterer Start
    gewinnt gegen einen noch offenen früheren `run`). Eine Waise, die nichts
    enthält, bleibt Kind der `run`-Wurzel — bei mehreren Wurzeln: der
    spätesten Wurzel, deren Start nicht nach dem Start der Waise liegt,
    andernfalls der ersten.
15. Die Regel wird ausschließlich zeitbasiert (mit `seq` als Tiebreaker)
    umgesetzt; sie hängt nicht von `phase`/`lane` ab und funktioniert, wenn
    diese auf allen Events `null` sind. Belegter Fall: der `agent.run`-Span
    der Spec-Phase mit `parent: null` landet unter dem ihn zeitlich
    enthaltenden offenen `phase`-Span, nicht unter der `run`-Wurzel.
16. Resume-Beispiel (Akzeptanzfall): Ein Log aus Erst-Lauf und Resume (zwei
    `run`-Starts, durchgezählte `seq`) ergibt zwei Wurzeln; eine Waise aus
    dem Resume-Segment hängt unter der zweiten Wurzel bzw. deren
    enthaltenden Spans, nie unter der ersten.
17. Punkt-Events hängen an dem Span, dessen ID sie im Feld `span` tragen.
18. Unbekannte `type`-Werte werden generisch als Knoten übernommen — nie
    verworfen, nie als Fehler behandelt (§4.2). Ihr ursprünglicher Record
    einschließlich Payload bleibt über die Knotenfelder zugänglich;
    unbekannte Span-Typen nehmen regulär an Spanbildung und Verschachtelung
    teil, unbekannte Punkt-Typen werden regulär ihrem `span` zugeordnet.
19. Hängende Referenzen — realistische Folge von AC 9–10 (kaputte oder
    angeschnittene Zeilen) — brechen den Baumbau nie ab und verwerfen keinen
    ansonsten gültigen Record; die Behandlung ist deterministisch:
    - Ein `end`-Record ohne bekannten `start` ergibt einen Span-Knoten allein
      aus dem End-Record: `start_ts`, `start_payload`, `duration` sind
      `None`, `running: false`, `end_ts`/`end_payload`/`seq` aus dem
      End-Record. Eingeordnet wird er über sein `parent`, falls auflösbar,
      sonst nach AC 14 mit dem Intervall `[end ts, end ts]`.
    - Ein Span, dessen nicht-nulles `parent` im Eventbestand unbekannt ist,
      wird wie eine Waise nach AC 14 behandelt.
    - Ein Punkt-Event, dessen `span` fehlt, `null` oder unbekannt ist, wird
      wie eine Waise nach AC 14 mit dem Intervall `[ts, ts]` eingehängt.
20. Die Kinder jedes Knotens sind chronologisch nach `ts`, bei Gleichstand
    nach `seq` geordnet. Das Modell liefert für einen vollständigen und für
    einen inkrementell erweiterten Eventbestand denselben beobachtbaren Baum.

### B3 — `adw/gui/registry.py`

21. Die Registry liegt ausschließlich in `~/.adw/repos.json` im oben
    festgelegten Format; je kanonischem Repo-Pfad existiert höchstens ein
    Eintrag mit Pfad, Slug und UTC-`last_seen`-Zeitstempel.
22. `adw run` registriert sein aufgelöstes Ziel-Repo automatisch bzw.
    aktualisiert dessen `last_seen`. Ein Registry-Fehler (z. B.
    Schreibfehler) verhindert den eigentlichen Run nicht — die Registry
    dient nur der späteren Anzeige.
23. Je Repo gibt es einen stabilen, URL-tauglichen Slug: derselbe Pfad ergibt
    stets denselben Slug, verschiedene kanonische Pfade verschiedene Slugs
    (auch bei gleichem Verzeichnisnamen), und ein Slug enthält nie einen
    rohen Dateisystempfad (§7.4). Ein Slug ist über `Registry.resolve` zu
    seinem Eintrag auflösbar.
24. Nicht mehr existierende Repo-Pfade werden beim Laden/Auflisten über
    `RepoEntry.exists` als fehlend gekennzeichnet; sie bleiben auflistbar
    und per Slug auflösbar und führen nie zu einem Fehler.
25. Eine noch nicht vorhandene oder unlesbare Registry-Datei führt zu einer
    leeren, nutzbaren Registry, nicht zu einem ungefangenen Fehler; das
    nächste erfolgreiche Registrieren schreibt wieder ein gültiges Format.
26. Jeder Registry-Schreibvorgang schreibt die vollständige neue Registry in
    eine temporäre Datei im selben Verzeichnis und ersetzt `repos.json`
    atomar (Semantik von `os.replace`). Ein vor dem Ersetzen scheiternder
    oder abgebrochener Schreibvorgang lässt den vorherigen Registry-Inhalt
    unversehrt. Cross-Prozess-Locking, Journaling und Backup-Recovery
    bleiben deferred.

## Deferred (bewusst nicht gebaut)

Weitergehende Härtungs- oder Erweiterungsideen — auch Befunde aus den
Codex-Review-Runden — gehören hierher, nicht in die Akzeptanzkriterien. Ein
Finding, das einen dieser Punkte oder einen vorentschiedenen Punkt (E1–E6)
einführen will, wird abgewiesen und mit Begründung dokumentiert.

- Web-Schicht insgesamt (FastAPI, uvicorn, HTTP-API, Jinja2, Templates, CSS,
  JS, SSE, `adw gui`, i18n) — nächster Lauf.
- Diff-Berechnung oder Validierung der in Snapshot-Events genannten Git-Refs.
- Prunen, Retention, gzip-Unterstützung, `trace:`-Config-Key (Lauf 5).
- Cross-Thread-Parent-API im Emitter bzw. `parent` an der Quelle setzen (E2);
  `phase`/`lane` an den Orchestrator-Aufrufstellen mitgeben (E6) — die
  Waisen-Reparatur bleibt beim Lesen.
- Persistente Offset-, Index- oder Cache-Dateien; Caching über Prozessläufe;
  Dateisystem-Watcher (späteres Live-Tailing pollt den Reader).
- Reparatur oder Umschreiben beschädigter bzw. lückenhafter Event-Logs.
- Registry-Locking für konkurrierende Prozesse über den atomaren
  Einzel-Schreibvorgang aus AC 26 hinaus; Journal-/Backup-Dateien und deren
  Recovery.
- Automatisches Entfernen oder Tombstones für verschwundene Repos;
  Identitätserkennung nach Verschieben eines Repos (neuer kanonischer Pfad =
  neuer Eintrag).
- Redaction/Maskierung von Secrets im Log.

## Definition of Done

1. Aufgabe A und B sind gebaut; der `codex.author`-Span und die drei Module
   `reader`, `model`, `registry` erfüllen die Akzeptanzkriterien und die im
   API-Kontrakt festgelegte öffentliche Fläche.
2. Reine, importierbare Python-Bausteine: `adw.gui.reader`, `adw.gui.model`
   und `adw.gui.registry` importieren und funktionieren ohne Web- oder neue
   Laufzeit-Abhängigkeiten; keine unerlaubten persistenten Dateien.
3. Ein Dry-Run erzeugt geschlossene `codex.author`-Spans mit den festgelegten
   Payload-Feldern und trägt sein Ziel-Repo in `~/.adw/repos.json` ein, ohne
   den fachlichen Ablauf der Dual-Authoring-Phase zu verändern.
4. Ein realitätsnahes Fixture (verschachtelte Spans, Worker-Thread-Waisen,
   Punkt-Events, unbekannte Typen, kaputte Zeile, `seq`-Lücke, zunächst
   unvollständige Schlusszeile, Resume-Naht mit zweitem `run`-Start, hängende
   Referenzen nach AC 19: `end` ohne `start`, Punkt-Event mit unbekanntem
   `span`, Span mit unauflösbarem `parent`) ist ohne Abbruch lesbar und
   ergibt über das Modell den festgelegten Baum mit korrekt eingehängten
   Waisen; jeder ansonsten gültige Record bleibt darin beobachtbar.
5. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`
   (`flake8`, `isort`, `black` tauchen nirgends auf — E3).
6. Richtwert: rund 18–25 neue Tests für A und B zusammen; maßgeblich ist die
   Abdeckung der Akzeptanzkriterien (Tail-Wiederlesen, angeschnittene Zeile,
   `seq`-Lücke, kaputte Zeile, Waisen-Einhängung inkl. Gleichstand und
   laufendem Container, Resume-Log mit zwei Wurzeln, hängende Referenzen,
   Punkt-Event-Zuordnung,
   unbekannter `type`, Slug-Stabilität und -Kollision, fehlendes Repo,
   fehlende/unlesbare Registry-Datei, abgebrochener Schreibvorgang lässt die
   alte Registry intakt, `codex.author`-Payload inkl. Fehlerpfad), nicht die
   exakte Anzahl.
