# Plan — `codex.author`-Span + Lese-Seite des Event-Logs (Reader, Modell, Registry)

Maßgeblich bleibt `docs/GUI-SPEC.md` (§4.2, §4.4, §7.4, §12); bei Widerspruch
gilt die GUI-SPEC. Dieser Plan setzt `.adw/spec.md` um und baut strikt gegen
`.adw/contract.yaml`. Single-Lane: es gibt genau den Workstream **backend**.
Dieser Lauf liefert ausschließlich importierbare Python-Bausteine — **keine
Web-Schicht**.

## Leitplanken

- `adw/events.py`, `adw/snapshots.py` und der fachliche Orchestrator-Ablauf
  bleiben unverändert. KEINE Cross-Thread-Parent-API im Emitter (E2), kein
  Nachrüsten von `phase`/`lane` an den Aufrufstellen (E6). Die
  Waisen-Reparatur passiert ausschließlich beim Lesen im Modell.
- Kein Refactoring von `phases.py`: Aufgabe A umschließt einzig die
  `ctx.codex.author(...)`-Aufrufstelle mit einem Span, mehr nicht (E4).
- Keine neuen Laufzeit-Dependencies des Kernpakets — Stdlib und das bereits
  vorhandene pydantic genügen.
- Keine Persistenz außer `~/.adw/repos.json`: keine Index-, Cache- oder
  Offset-DATEIEN. Ein Byte-Offset im Speicher ist erlaubt und erwünscht.
- Reale Gates (E3): `uv run ruff check .` und `uv run pytest -x -q`.
  `flake8`, `isort`, `black` tauchen nirgends auf.

## Ausgangslage (verifiziert im Code)

- Record-Schema (`events.py:_append`): jeder Record trägt
  `seq, ts, type, kind ("start"|"end"|"point"), span, parent, phase, lane,
  round, payload`. `phase`/`lane` sind an den betroffenen Aufrufstellen
  faktisch `null` — die Enthaltungsregel darf sich nicht auf sie stützen (AC 15).
- Die `codex.review`-Instrumentierung (`phases.py:_codex_review`, Z. 126–133)
  ist das Muster für Aufgabe A: die argv wird über einen seiteneffektfreien
  Builder (`ctx.codex.effective_argv(...)`) VOR dem Öffnen des Spans gebaut und
  steht vollständig im Start-Event; der Runner füllt nur das von der
  Aufrufstelle gereichte Span-Handle (`end_payload`).
- Die `codex.author`-Aufrufstelle ist `phases.py:_codex_draft` (Z. 706–738,
  `files = ctx.codex.author(kind, task, cwd=ctx.repo)`). Sie läuft im
  Pool-Worker (`_draft_stage`, `ThreadPoolExecutor`), dessen Span-Stack leer
  ist — der `codex.author`-Span erhält daher `parent: null` (belegter
  Waisen-Fall, E2/E6).
- `CodexRunner.author` (`codex.py`, Z. 303–310) baut die argv heute intern über
  `_build_author_prompt` (enthält einen per-Call-`marker_id`-Nonce) und
  `_argv`; sie gibt `dict[name -> content]` zurück, ohne `raw_stdout`/`parse_ok`
  zu exponieren. Für AC 2/3 muss die argv VOR dem Span vorliegen und mit der
  ausgeführten identisch sein — der Nonce muss also einmal an der Aufrufstelle
  festgelegt und in Builder wie Ausführung geteilt werden (Mechanik, s. u.).
- `cli.py:run` löst das Ziel-Repo mit `repo = repo.resolve()` auf (Z. 146) —
  der eine natürliche Punkt für die Registry-Eintragung (AC 22).
- Der Reader liest `.adw/runs/<run_id>/events.jsonl` (`state.py:RUNS_RELPATH`).
  Das Log ist append-only, `\n`-terminiert; eine crash-truncatierte
  Schlusszeile ist möglich (`events.py:_append` heilt sie beim nächsten
  Schreiben) — der Reader muss sie tolerieren (AC 8).

## Workstream: backend

### Aufgabe A — `codex.author` instrumentieren

Reines Umschließen der Aufrufstelle nach dem `codex.review`-Muster (E4). KEIN
Refactoring von `phases.py` darüber hinaus.

A1. In `phases.py:_codex_draft` den `ctx.codex.author(...)`-Aufruf mit einem
    `ctx.emitter.span("codex.author", start)` umschließen. Der Span liegt an der
    Aufrufstelle; der Inhalt (Ausführung, Roh-Output, Parse-Ergebnis) kommt aus
    `codex.py`.
A2. **Start-Payload (§4.4):** `kind`, `argv` (list), `cwd`, `task`. Die `argv`
    liegt VOR dem Öffnen des Spans vollständig vor und steht unverändert im
    Start-Record — identisch zu dem, was der Runner ausführt, nicht nachträglich
    rekonstruiert (AC 2). Dazu wird — analog `effective_argv` für Review — eine
    seiteneffektfreie Author-argv-Konstruktion auf dem Runner-Protokoll nutzbar
    gemacht. Weil `_build_author_prompt` einen per-Call-`marker_id` enthält,
    muss dieser Nonce einmal bestimmt und zwischen Builder und Ausführung
    geteilt werden (z. B. ein an der Aufrufstelle erzeugter `marker_id`, der an
    Builder und `author(...)` weitergereicht wird, ODER ein `author(...)`, das
    ein vorab gebautes argv/Prompt entgegennimmt). Die genaue Signatur ist
    Mechanik und NICHT im Kontrakt; verbindlich ist nur: Start-`argv` ==
    ausgeführtes argv.
A3. **End-Payload (§4.4):** `artifacts[]` (die zurückgegebenen Dateinamen),
    `raw_stdout`, `parse_ok`. Das Befüllen erfolgt im Runner in das von der
    Aufrufstelle gereichte Span-Handle — deterministische Defaults VOR der
    Ausführung (`artifacts: []`, `raw_stdout: ""`, `parse_ok: false`), sodass
    auch ein Fehlerpfad ein vollständiges End-Event schreibt (Muster
    `codex.py:_review_into`). `codex.py` wird dafür span-fähig gemacht
    (erlaubt: `codex.py` steht nicht auf der No-Change-Liste; verboten sind nur
    `events.py`, `snapshots.py`, der Orchestrator-Ablauf und ein `phases.py`-
    Refactoring).
A4. **Degradation unverändert (AC 4):** Der Span wird auch bei gescheitertem
    oder leerem Codex-Entwurf mit vollständigem `end`-Record geschlossen
    (`parse_ok: false`, verfügbarer Roh-Output). Das bestehende
    Degradations-/Marker-Verhalten von `_codex_draft` bleibt unangetastet; der
    Lauf bricht nicht ab. Der Span umschließt den `ctx.codex.author(...)`-Aufruf
    so, dass sein `end` auch im `except`-Pfad geschrieben wird (Span-`finally`).
A5. **Dry-Run/Mock (AC 5):** Weil der Span an der Aufrufstelle liegt, erzeugt
    auch der Mock-Runner (`mock.py`) den Span. Der Mock liefert dazu denselben
    seiteneffektfreien Author-argv-Builder-Wert und befüllt das gereichte
    Handle mit `artifacts`/`raw_stdout`/`parse_ok` (Muster wie
    `mock.py:review`), damit ein Dry-Run geschlossene `codex.author`-Spans mit
    den festgelegten Payload-Feldern erzeugt.

Tests A:
- Dry-Run erzeugt für jeden `codex.author`-Aufruf genau ein geschlossenes
  Start/End-Paar gleicher Span-ID; der Test prüft die Start-Felder (`kind`,
  `argv`, `cwd`, `task`) UND die End-Felder (`artifacts`, `raw_stdout`,
  `parse_ok`), nicht nur die Schließung (AC 1–3).
- Start-`argv` ist vollständig und identisch mit dem, was der Runner ausführen
  würde (Builder-Wert == ausgeführtes argv; AC 2).
- Fehlerpfad: gescripteter Author-Fehler/leerer Entwurf → Span trotzdem
  geschlossen, `parse_ok: false`, Roh-Output vorhanden, Lauf/Degradation
  unverändert (AC 4).

### Aufgabe B1 — `adw/gui/reader.py`

Neues Paket `adw/gui/` (mit `__init__.py`) und Modul `reader.py`.

B1-1. `EventReader(path)` initialisiert auf einen `events.jsonl`-Pfad;
    `EventReader.offset` (int) ist der öffentlich lesbare bestätigte
    Byte-Offset, Startwert 0. Offset und Tail-Zustand leben ausschließlich im
    Speicher (keine Offset-Datei). Reader-Aufrufe verändern das Log nicht und
    erzeugen keine Dateien (AC 7).
B1-2. `EventReader.read() -> ReadResult`: liest ab dem bestätigten Offset,
    parst ausschließlich vollständige, `\n`-terminierte Zeilen in
    Dateireihenfolge und rückt den Offset genau bis hinter die letzte
    vollständig verarbeitete Zeile vor. Ein zweiter Aufruf ohne Dateizuwachs
    liefert nichts erneut; nach Anhängen liefert der nächste Aufruf
    ausschließlich das Neue (AC 6/7).
B1-3. **Angeschnittene letzte Zeile (AC 8):** eine nicht `\n`-terminierte
    Schlusszeile wird weder geparst noch als Problem gemeldet, ihr Anfang nicht
    als Offset bestätigt; sobald sie durch angehängte Bytes vollständig ist,
    wird sie genau einmal verarbeitet.
B1-4. `ReadResult`: `events` (geparste Records in Dateireihenfolge, als
    Mappings mit ALLEN Originalfeldern) und `problems` (Liste `ReadProblem`) —
    Events und Probleme sind eindeutig getrennt (AC 6).
B1-5. `ReadProblem`: `kind` (`"bad_line"` | `"seq_gap"`), betroffene Position
    (`line_no` und/oder `byte_offset`); bei `seq_gap` zusätzlich `expected` und
    `found`.
    - **`seq_gap` (AC 9):** eine Lücke in der `seq`-Folge wird gemeldet, nicht
      still überlesen; die lesbaren Events vor und nach der Lücke bleiben
      verfügbar. Die `seq`-Kontinuität wird über Read-Aufrufe hinweg geführt
      (in-memory), damit eine Lücke auch inkrementell erkannt wird.
    - **`bad_line` (AC 10):** eine vollständige, nicht parsbare Zeile wird für
      diese Zeile gemeldet; alle nachfolgenden vollständigen Zeilen werden
      weiterhin geliefert.
B1-6. `snapshot`-Events und Records unbekannten Typs werden unverändert als
    Events geliefert (Ref-Namen verfügbar); der Reader berechnet KEINE Diffs
    (AC 11, Non-Goal).

### Aufgabe B2 — `adw/gui/model.py` (Span-Baum)

B2-1. `build_tree(events) -> list[SpanNode]`: überführt eine Folge gelesener
    Event-Records in einen Span-Baum. `start`/`end` mit derselben Span-ID
    werden zu genau einem Knoten; `duration` aus der `ts`-Differenz. Ein Span
    ohne `end` hat `running: true`, `end_ts`/`end_payload`/`duration` = `None`
    (AC 12).
B2-2. `SpanNode`-Felder: `span_id`, `type`, `seq` (Start-`seq`), `start_ts`,
    `end_ts`, `duration`, `running`, `start_payload`, `end_payload`,
    `start_record` (vollständiger Original-Start-Record; `None` nur im
    End-only-Fall aus AC 19), `end_record` (vollständiger Original-End-Record;
    `None` solange laufend), `children` (geordnete Liste aus Span- und
    Punkt-Knoten). `PointNode`-Felder: `type`, `seq`, `ts`, `payload`,
    `record` (vollständiger Original-Record).
B2-3. **Wurzeln & Resume (AC 13):** Verschachtelung folgt dem `parent`-Feld,
    soweit gesetzt. Die Wurzeln des Ergebnisses sind die `run`-Spans in
    Log-Reihenfolge. Ein Resume-Log (§12: zweiter `run`-Start in derselben
    Datei, `resumed_from_seq` markiert die Naht, `seq` zählt durch) ergibt
    mehrere Wurzeln, je Lauf-Segment eine. Ein Log ohne `run`-Start
    (degeneriert/truncatiert) bricht nicht ab; elternlose Spans werden dann
    selbst Wurzeln.
B2-4. **Enthaltungsregel für Waisen-Spans (AC 14, GUI-SPEC §4.2, normativ):**
    Eine Waise (Span mit `parent: null`, der nicht die `run`-Wurzel ist) gehört
    zu dem INNERSTEN Span, dessen Intervall das Intervall der Waise
    `[start ts, end ts]` echt enthält — unter allen enthaltenden Kandidaten der
    mit dem spätesten Start; bei Gleichstand entscheidet die höhere `seq`. Ein
    noch laufender Span gilt als enthaltend für alles nach seinem Start.
    Intervalle je Knotenart: gepaarter Span `[start ts, end ts]`, laufender
    Span `[start ts, ∞)`, End-only-Span `[end ts, end ts]`, Punkt-Event
    `[ts, ts]`. `run`-Spans nehmen als Kandidaten regulär teil (eine Waise
    eines Resume-Segments fällt an dessen `run`-Span: späterer Start gewinnt
    gegen einen noch offenen früheren `run`). Eine Waise, die nichts enthält,
    bleibt Kind der `run`-Wurzel — bei mehreren Wurzeln: der spätesten Wurzel,
    deren Start nicht nach dem Start der Waise liegt, sonst der ersten.
B2-5. **Rein zeitbasiert (AC 15):** die Regel nutzt `ts` (+ `seq` als
    Tiebreaker) und hängt NICHT von `phase`/`lane` ab; sie funktioniert, wenn
    diese überall `null` sind. Belegter Fall: der `agent.run`-Span der
    Spec-Phase mit `parent: null` landet unter dem ihn zeitlich enthaltenden
    offenen `phase`-Span, nicht unter der `run`-Wurzel.
B2-6. **Punkt-Events (AC 17):** hängen an dem Span, dessen ID sie im Feld `span`
    tragen.
B2-7. **Unbekannte `type` (AC 18):** werden generisch als Knoten übernommen —
    nie verworfen, nie als Fehler behandelt. Der Original-Record inkl. Payload
    bleibt über stabile Knotenfelder zugänglich: `SpanNode.start_record`/
    `end_record` bzw. `PointNode.record`, jeweils mit ALLEN Originalfeldern;
    unbekannte Span-Typen nehmen regulär an Spanbildung/Verschachtelung teil,
    unbekannte Punkt-Typen werden regulär ihrem `span` zugeordnet.
B2-8. **Hängende Referenzen (AC 19), deterministisch, nie abbrechend, kein
    Recordverlust:**
    - `end` ohne bekannten `start`: Span-Knoten allein aus dem End-Record —
      `start_ts`/`start_payload`/`start_record`/`duration` = `None`,
      `running: false`, `end_ts`/`end_payload`/`end_record`/`seq` aus dem
      End-Record. Einordnung über `parent`, falls auflösbar, sonst nach AC 14
      mit Intervall `[end ts, end ts]`.
    - Span mit nicht-nullem, im Bestand unbekanntem `parent`: wie eine Waise
      nach AC 14.
    - Punkt-Event mit fehlendem/`null`/unbekanntem `span`: wie eine Waise nach
      AC 14 mit Intervall `[ts, ts]`.
B2-9. **Kinder-Ordnung & Stabilität (AC 20):** Kinder jedes Knotens
    chronologisch nach `ts`, bei Gleichstand nach `seq`. Das Modell liefert für
    einen vollständigen und für einen inkrementell erweiterten Eventbestand
    denselben beobachtbaren Baum.

### Aufgabe B3 — `adw/gui/registry.py`

B3-1. `register_repo(path) -> RepoEntry`: legt den Eintrag an bzw. aktualisiert
    `last_seen`. Je kanonischem Repo-Pfad höchstens ein Eintrag mit `path`,
    `slug`, `last_seen` (UTC, ISO-8601). Registry-Datei ausschließlich
    `~/.adw/repos.json` im festgelegten Format (AC 21).
B3-2. **`adw run`-Hook (AC 22):** In `cli.py:run` nach `repo = repo.resolve()`
    das aufgelöste Ziel-Repo registrieren. Ein Registry-Fehler (z. B.
    Schreibfehler) verhindert den Run nicht — fail-open, die Registry dient nur
    der späteren Anzeige.
B3-3. **Stabiler Slug (AC 23, §7.4):** derselbe Pfad ergibt stets denselben
    Slug — ein vorhandener Eintrag behält beim Aktualisieren seinen Slug;
    verschiedene kanonische Pfade verschiedene Slugs (auch bei gleichem
    Verzeichnisnamen); ein Slug enthält nie einen rohen Dateisystempfad.
    (Mechanik frei, z. B. lesbarer Basename + kurzer Hash-Suffix des
    kanonischen Pfads.)
B3-4. `load_registry() -> Registry`; `Registry.repos` (Liste `RepoEntry`),
    `Registry.resolve(slug) -> RepoEntry | None`. `RepoEntry`: `path`, `slug`,
    `last_seen`, `exists` (bool — ob der Pfad aktuell existiert). Nicht mehr
    existierende Pfade werden über `RepoEntry.exists` als fehlend
    gekennzeichnet, bleiben auflistbar und per Slug auflösbar, nie ein Fehler
    (AC 24).
B3-5. **Robustheit (AC 25):** eine fehlende oder unlesbare Registry-Datei führt
    zu einer leeren, nutzbaren Registry, nicht zu einem ungefangenen Fehler; das
    nächste erfolgreiche Registrieren schreibt wieder ein gültiges Format.
B3-6. **Atomarer Schreibvorgang (AC 26):** jeder Schreibvorgang schreibt die
    vollständige neue Registry in eine Temp-Datei im selben Verzeichnis und
    ersetzt `repos.json` atomar (`os.replace`). Ein vor dem Ersetzen
    scheiternder/abgebrochener Schreibvorgang lässt den vorherigen Inhalt
    unversehrt. Cross-Prozess-Locking, Journaling, Backup-Recovery bleiben
    deferred.

Tests B (Richtwert siehe Guardrail; Abdeckung ist maßgeblich):
- Reader: erstes Lesen liefert alle vollständigen Records; erneutes Lesen ohne
  Zuwachs liefert nichts; nach Anhängen nur das Neue; `offset` konsistent;
  Reader-Aufrufe verändern das Log nicht und erzeugen keine Dateien.
- Reader: angeschnittene Schlusszeile wird ignoriert und nach Vervollständigung
  genau einmal geliefert; `seq_gap` (mit `expected`/`found`) und `bad_line`
  gemeldet, Rest weiter lesbar; `snapshot`-Ref-Namen und unbekannte Records
  kommen unverändert durch.
- Modell: verschachtelte Spans über `parent`; laufender Span; Waisen-Einhängung
  inkl. Gleichstand (`seq`-Tiebreaker) und laufendem Container; belegter
  `agent.run`-unter-`phase`-Fall; Resume-Log ergibt zwei Wurzeln, Waise des
  Resume-Segments unter der zweiten Wurzel; degeneriertes Log ohne `run`-Start;
  Punkt-Event-Zuordnung; unbekannter Span- und Punkt-`type` mit Erhalt ALLER
  Originalfelder über `start_record`/`end_record` bzw. `record`; hängende
  Referenzen (`end` ohne `start`, Punkt mit unbekanntem `span`, Span mit
  unauflösbarem `parent`); Kinder-Ordnung; voll == inkrementell.
- Registry: Slug-Stabilität (auch über Aktualisierungen hinweg) und -Kollision
  (gleicher Verzeichnisname, verschiedene Pfade → verschiedene Slugs);
  `resolve`; exaktes persistiertes Version-1-Format; fehlendes Repo →
  `exists: false`, auflistbar/auflösbar, kein Fehler; fehlende/unlesbare Datei →
  leere Registry; abgebrochener Schreibvorgang lässt alte Registry intakt;
  `adw run`-Registrierung im Dry-Run; Registry-Fehler blockiert Run nicht.
- Realitätsnahes Fixture (DoD 4): verschachtelte Spans, Worker-Thread-Waisen,
  Punkt-Events, unbekannte Typen, kaputte Zeile, `seq`-Lücke, zunächst
  unvollständige Schlusszeile, Resume-Naht, hängende Referenzen — ohne Abbruch
  lesbar, ergibt den festgelegten Baum, jeder gültige Record beobachtbar.

## Guardrail Testumfang

Richtwert rund 18–25 neue Tests für A und B zusammen. Maßgeblich ist die
Abdeckung der Akzeptanzkriterien, nicht die exakte Anzahl. Deutlich mehr ist
ein Signal für Scope-Drift.

## Definition of Done

1. Aufgabe A und B sind gebaut; der `codex.author`-Span und die drei Module
   `reader`, `model`, `registry` erfüllen die Akzeptanzkriterien und die im
   Kontrakt festgelegte öffentliche Fläche.
2. `adw.gui.reader`, `adw.gui.model`, `adw.gui.registry` importieren und
   funktionieren ohne Web- oder neue Laufzeit-Abhängigkeiten; keine
   unerlaubten persistenten Dateien.
3. Ein Dry-Run erzeugt geschlossene `codex.author`-Spans mit den festgelegten
   Payload-Feldern und trägt sein Ziel-Repo in `~/.adw/repos.json` ein, ohne
   den fachlichen Ablauf der Dual-Authoring-Phase zu verändern.
4. Das realitätsnahe Fixture ist ohne Abbruch lesbar und ergibt über das
   Modell den festgelegten Baum mit korrekt eingehängten Waisen; jeder
   ansonsten gültige Record bleibt beobachtbar.
5. Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
6. `adw/events.py`, `adw/snapshots.py` und der fachliche Orchestrator-Ablauf
   sind unverändert; der Diff in `phases.py` beschränkt sich auf das
   Umschließen der `codex.author`-Aufrufstelle. Non-Goals und Deferred-Punkte
   sind nicht Bestandteil des Produkts geworden.

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
