# Implementierungsplan: Änderungsumfang eines Laufs sichtbar machen

Single-Lane-Projekt (`backend`). Alles — Lane-Ableitung, Diff-Projektion,
Contract-Scope-Leser, additives Detail-Feld, Template-Anzeige, i18n, Doku,
Tests — läuft in der einen Lane `backend`. Es gibt keine getrennte Frontend-Lane;
die serverseitig gerenderte Anzeige gehört zum Backend-Workstream.

Gebaut wird strikt gegen `.adw/contract.yaml`: extern zugesagt sind nur (a) die
Gestalt des additiven `change_scope`-Felds in `GET /api/runs/{repo}/{run_id}`
(`lanes` + `declared_scope`), (b) die Lane-Menge/-Reihenfolge und die drei
kanonischen Lane-Formen (S1/S2/S3), (c) Inhalt und Fallback von `declared_scope`
(S4), (d) die Urteilsfreiheit (S5) und (e) das read-only Template-Verhalten (S6).
Interne Helper-Signaturen, Markup und CSS sind **nicht** Teil des Contracts.
Die Implementierungs-Constraints (Wiederverwendung der bestehenden
Snapshot-/Diff-/Numstat-Logik, unveränderte Diff-Route, `adw/phases.py`
unangetastet, keine neue Dependency) sind bewusst **Plan-/DoD-Ebene**, nicht
Contract-Ebene — sie gelten unverändert und werden hier durchgesetzt.

## Ist-Stand (im Code verankert, verifiziert)
- `GET /api/runs/{repo}/{run_id}` wird in `_run_detail()` (`adw/gui/app.py:1592`)
  zusammengesetzt; die Antwort ist ein Dict mit `run`, `phases`, `tree`,
  `latest_context`, `problems`, `raw` und optional `recovery`/`plan_skeleton`.
  `events` und der Trace sind dort bereits geladen;
  `snaps = _snapshots_by_lane(events, run_id)` steht ebenfalls schon zur
  Verfügung (`app.py:1600`).
- `_snapshots_by_lane(events, run_id)` (`app.py:753`) gruppiert die
  Snapshot-Refs des Laufs je `payload.lane`, jede Liste nach `seq` sortiert, und
  behält **nur** Refs mit exakter Struktur `refs/adw/<run_id>/<seq>`
  (`_is_snapshot_ref`, `app.py:1641`) — dieselbe Strukturvalidierung wie beim
  Bracketing. Das ist die einzige Quelle gültiger Snapshot-Paare.
- Roh-Events tragen `kind` (`start`/`end`/point), `span`, `type`, `seq`,
  `payload`. Ein `lane`-Span-Start ist `kind=="start"`, `type=="lane"`, mit
  Lane-Name unter `payload.name`.
- `_git_diff(repo_path, frm, to)` (`app.py:1687`) führt die bestehende
  Diff-/Numstat-Logik aus (list-argv, `core.hooksPath=/dev/null`, `safe_env()`,
  Timeout), gibt `{"files": _parse_numstat(...), "patch": ...}` zurück und
  **wirft `HTTPException(404)`** (`app.py:1710`), wenn einer der beiden
  Git-Aufrufe fehlschlägt; `subprocess.TimeoutExpired` propagiert dagegen
  **ungefangen** aus `_git_diff` heraus — der Aufrufer muss beides behandeln.
  `_parse_numstat` (`app.py:1668`) liefert je Datei
  `path`/`additions`/`deletions`, Binärdatei → `null`.
- `contract.yaml` steht auf der Whitelist `_ARTIFACT_TOP_LEVEL` (`app.py:1352`)
  und wird über `_resolve_artifact(run_dir, "contract.yaml")` (`app.py:1367`)
  aufgelöst; ein die Run-Verzeichnisgrenze verlassender Symlink gilt über
  `_contained()`/`_artifact_present()` (`app.py:339`/`1386`) als abwesend.
- PyYAML ist bereits Laufzeit-Abhängigkeit (`adw/config.py` importiert `yaml`);
  die GUI darf `yaml` nutzen, ohne eine neue Dependency einzuführen.
- Das Detail-Template `adw/gui/templates/run_detail.html` rendert das Detail
  serverseitig; i18n-Labels liegen in `adw/gui/i18n.py` (`_EN`/`_DE`, identische
  Schlüsselmengen).

## Workstream: backend

### B1 — Beobachtete Lanes: Menge und Reihenfolge (AC-1, E4)
Internen Helfer in `adw/gui/app.py` anlegen, der aus den bereits geladenen
`events` und `snaps` die geordnete Lane-Menge ableitet (Signatur intern, nicht
im Contract).

- Eine Lane ist **beobachtet**, wenn (a) ein `lane`-Span mit nicht-leerem
  `payload.name` existiert **oder** (b) `_snapshots_by_lane` sie führt (d. h. ein
  strukturell gültiges Snapshot-Event `refs/adw/<run_id>/<seq>` deklariert sie).
  Snapshot-Events, die `_is_snapshot_ref` nicht bestehen, tragen weder zur
  Lane-Menge noch zu Snapshot-Paaren bei — sie sind in `snaps` bereits
  ausgefiltert; **kein** zweiter Ableitungspfad.
- `lane`-Span-Beobachtung aus den Roh-Events: `e.get("type") == "lane"` und
  `kind == "start"` mit nicht-leerem `payload.name`; die Erstbeobachtungs-`seq`
  ist die `seq` dieses Start-Events.
- Erstbeobachtungs-`seq` je Lane = kleinstes `seq` über **beide** Quellen
  (`lane`-Span-Starts und die `(seq, ref)`-Einträge aus `snaps`). Reihenfolge der
  Ausgabeliste: aufsteigend nach dieser `seq` — deterministisch.
- Je Lane-Name genau ein Eintrag; mehrfache Beobachtung desselben Namens ergibt
  einen Eintrag. Eine nur über gültige Snapshots beobachtete Lane (ohne
  `lane`-Span) ist enthalten; eine nur über ihren `lane`-Span beobachtete Lane
  (ohne gültige Snapshots) ist ebenfalls enthalten (Diff-Zustand → B2).

### B2 — Diff je Lane: erster gegen letzten Snapshot, kanonische Formen (AC-1, AC-2, AC-6, AC-7, AC-8, E5)
Je beobachteter Lane (B1) genau **einen** Vergleich bilden — ausschließlich mit
der bestehenden Diff-Logik, kein zweiter Git-Aufrufpfad:

- Hat die Lane in `snaps` **≥ 2** gültige Snapshots: `frm` = Ref mit niedrigstem
  `seq`, `to` = Ref mit höchstem `seq` dieser Lane (die Liste ist bereits
  sortiert). Über den **bestehenden** `_git_diff(ref.path, frm, to)` die
  `files`-Liste beziehen (`_parse_numstat`-Reihenfolge, Binärdatei → `null`).
  Ergebnis: `{"lane": name, "diff_available": true, "files": [...]}` — bei einem
  erfolgreichen Vergleich ohne geänderte Dateien kanonisch `files: []`
  (AC-7, S3(1)).
- Hat die Lane **0 oder genau 1** gültigen Snapshot: `{"lane": name,
  "diff_available": false, "files": null}` — kanonisch genau diese Form, **nicht**
  `[]`, **nicht** weggelassen (AC-6, S3(2)). Ein Self-Diff eines einzelnen
  Snapshots wird nicht gebildet (wäre garantiert leer und würde fälschlich „keine
  Änderungen" behaupten).
- **Diff-Fehler trotz Paar** (AC-8, S3(3)): Die Fehlergrenze je Lane deckt die
  **gesamte** enge Menge erwartbarer operativer Fehler des bestehenden
  Diff-Aufrufs ab, nicht nur Einzelfälle: `HTTPException(404)` (von `_git_diff`
  bei fehlendem Snapshot-Objekt / einseitigem Fehler geworfen) sowie
  `subprocess.SubprocessError` (inkl. `TimeoutExpired`) und `OSError`
  (Ausführungsfehler wie ENOENT/EACCES), die aus `subprocess.run` ungefangen
  propagieren. Jede dieser Ausnahmen je Lane **abfangen** und die Lane exakt wie
  B2-Fall „kein Diff" behandeln: `diff_available: false`, `files: null` — nie ein
  erfolgreicher leerer Diff (`files: []`). Kein pauschales `except Exception`;
  Programmierfehler sollen weiterhin sichtbar scheitern. Der abgefangene Fehler
  darf **nicht** aus `_run_detail` heraus propagieren: bei ansonsten
  erfolgreicher Detail-Anfrage bleibt `GET /api/runs/{repo}/{run_id}` ein
  gültiger 200-Response, nie ein 5xx, nie ein einseitiger/erfundener Diff. Ein
  fehlgeschlagener Diff einer Lane verhindert nicht die Darstellung verfügbarer
  Diffs anderer Lanes.
- `_git_diff`, `_parse_numstat`, die Diff-Route und deren Anfrage/Antwort bleiben
  **unverändert** (E5). Worktree, Index und Refs bleiben unberührt.

### B3 — Deklarierter Contract-Scope als Text (AC-3, AC-4, E2)
Internen Helfer anlegen, der `declared_scope` (YAML-Text oder `null`) liefert:

- `contract.yaml` **ausschließlich** über `_resolve_artifact(run_dir,
  "contract.yaml")` auflösen und wie `_artifact_present` über `_contained(...)`
  gegen `run_dir` absichern; ein ausbrechender Symlink zählt als abwesend. Kein
  neuer Dateizugriffspfad, keine neue Route.
- Datei mit dem vorhandenen `yaml`-Modul **sicher** laden (`yaml.safe_load`).
  Ist die Datei fehlend/leer/unlesbar, kein YAML-Mapping oder nicht sicher als
  YAML lesbar (Lese-/Parse-Fehler abfangen) → `declared_scope = null`.
- Aus dem geladenen Mapping in **Dokumentreihenfolge** (Insertion-Order von
  `safe_load`) alle Top-Level-Einträge sammeln, deren Schlüssel **ein String
  ist und** mit `x-adw-` beginnt; andere Top-Level-Einträge weglassen. Ein
  legal geladenes Mapping kann nicht-String-Schlüssel tragen (numerisch,
  boolesch, `null`, …) — diese werden **ignoriert**, nie einer
  Präfix-Operation unterzogen (kein `startswith` auf Nicht-Strings, kein
  Fehler). Enthält das Mapping keinen passenden String-Schlüssel →
  `declared_scope = null` (AC-4), ohne Fehler.
- Die gesammelten Einträge als lesbaren YAML-Text zurückgeben
  (`yaml.safe_dump(..., sort_keys=False, allow_unicode=True)`): Schlüssel, Werte
  und Verschachtelung inhaltlich unverändert, ohne Umbenennung, Zusammenführung,
  Normalisierung oder Interpretation (AC-3). Zugesagt ist **semantische
  Äquivalenz** der ausgewählten Einträge, nicht Text-Treue: Kommentare, Quoting,
  Skalar-Stile, Anker und Formatierung der Quelldatei bleiben durch
  Load+Dump **nicht** erhalten und sind laut Contract (S4) auch nicht
  versprochen. Schlägt auch das **Serialisieren** fehl (nicht sicher als YAML
  ausgebbar), gilt ebenfalls `declared_scope = null` statt eines Fehlers. Kein
  Schema-/OpenAPI-Validator, keine neue Dependency.

### B4 — Additives Feld `change_scope` in der Detail-Antwort (AC-1, AC-5, AC-9, Contract)
In `_run_detail()` (`app.py:1592`) nach dem Aufbau von `detail` das
`change_scope`-Objekt aus B1–B3 setzen — gespeist aus den bereits geladenen
`events`, `snaps`, `ref`/`run_id` und `run_dir`:

- `detail["change_scope"] = {"lanes": [<B1/B2>], "declared_scope": <B3>}`.
  Das Feld ist **immer** präsent (anders als das bedingte `recovery`/
  `plan_skeleton`); `lanes` kann `[]` sein (keine beobachtete Lane),
  `declared_scope` kann `null` sein.
- Reine Addition: `run`, `phases`, `tree`, `latest_context`, `problems`, `raw`,
  `recovery`, `plan_skeleton` bleiben unverändert (AC-9). Kein neues Endpoint,
  kein neues Event, keine neue Persistenz, kein Schreibzugriff.
- **Kein Urteil** (AC-5, S5): kein Feld/Marker für „im Scope"/„außerhalb"/
  „Verletzung"/Konformität, keine Datei↔`x-adw-*`-Zuordnung, keine abgeleitete
  Wertung. Die Lane- und File-Objekte tragen **genau** die im Contract
  festgelegten Schlüssel (`additionalProperties: false`).

### B5 — Anzeige im Run-Detail (§7.2) (AC-3, AC-4, AC-6, AC-7, AC-9, S6)
Im Detail-Template `run_detail.html` einen read-only Änderungsumfang-Block
rendern, der Dateilisten und Contract-Scope unbewertet nebeneinanderstellt:

- **Dateilisten je Lane:** über `detail.change_scope.lanes` iterieren. Je Lane
  mit `diff_available: true` die Dateien mit `path` und `+/-`-Zahlen ausgeben;
  `null` (Binärdatei) verständlich als „nicht numerisch verfügbar" darstellen
  (AC-9). `files: []` klar als „keine geänderten Dateien gefunden" (AC-7), **nie**
  eine leere Tabelle ohne Erklärung.
- Je Lane mit `diff_available: false` klar „kein Diff verfügbar" statt leerer
  Tabelle (AC-6) — auch für die nur über ihren `lane`-Span beobachtete Lane.
- Hat **keine** Lane einen verwertbaren Diff (alle `diff_available: false`),
  entfällt die Tabellenansicht mit klarer Aussage „kein Lauf-Diff verfügbar"
  (AC-7); der Contract-Scope-Teil bleibt davon unberührt darstellbar.
- **Contract-Scope:** `declared_scope` als lesbaren Text darstellen; ist er
  `null`, klar „kein deklarierter Scope" — ohne Fehler, ohne erfundene Bewertung
  (AC-4). Keine Scope-Markierung, kein „im Scope"/„außerhalb" (AC-5).
- In die vorhandene serverseitige Rendering-/Live-Refresh-Fläche integrieren;
  kein neues Tab, keine Änderung an Timeline/Raw/Trace/Diff-Tab/SSE. Read-only.
  Scope-Text und Dateipfade sind CONTENT und werden **nicht** übersetzt. Styles
  nur für den neuen Block ergänzen; Markup und CSS sind nicht Teil des Contracts.

### B6 — i18n-Labels für die Block-Chrome (S6)
In `adw/gui/i18n.py` die neuen Chrome-Labels (Blocküberschrift, Spalten +/-,
„nicht numerisch verfügbar", „kein Diff verfügbar", „keine geänderten Dateien
gefunden", „kein Lauf-Diff verfügbar", „deklarierter Scope", „kein deklarierter
Scope") in `_EN` und `_DE` mit identischer Schlüsselmenge ergänzen; deutsche
Werte keine bloße Kopie der englischen. Nur Chrome wird übersetzt; Scope-Text und
Dateipfade bleiben unangetastet.

### B7 — Doku (DoD)
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` §7.2: der Änderungsumfang-Block,
  das additive API-Feld `change_scope` (`lanes` + `declared_scope`), die fehlende
  automatische Bewertung (AC-5) und die Fallback-Zustände (AC-4/AC-6/AC-7/AC-8).
- `CHANGELOG.md` und `CHANGELOG.de.md` Abschnitt `Unreleased`: neuer additiver
  Eintrag.
- Bilinguale Konvention einhalten (EN + DE).

### B8 — Tests unter tests/ (test_gui_*.py) (DoD, AC-1…AC-9)
Richtwert **~14 neue Tests** (Bestand: 978); mehr als ~22 ist Scope-Drift.
Git-Aufrufe laufen gegen temporäre Repos nach dem Muster von
`tests/test_gui_diff_endpoint.py` / `tests/test_gui_diff_pairing.py`. Mindestens
abzudecken:
- **AC-1:** mehrere Dateien mit numerischen `+/-`-Werten; erster-gegen-letzter
  Snapshot; Trennung der Lanes (Snapshots anderer Lanes nie einbezogen);
  Reihenfolge nach Erstbeobachtung; nur über gültige Snapshots beobachtete Lane
  (Eintrag vorhanden); mehrfache Beobachtung desselben Lane-Namens → genau ein
  Eintrag; strukturell ungültige Snapshot-Events tragen nichts bei.
- **AC-2/E5:** dieselbe Numstat-Logik; Diff-Route (Anfrage/Antwort) unverändert.
- **AC-6:** genau ein Snapshot → `diff_available: false`, `files: null`; keine
  Snapshots; nur über den `lane`-Span beobachtete Lane ohne gültige Snapshots →
  Eintrag vorhanden, `diff_available: false`, `files: null` (nicht `[]`, nicht
  weggelassen).
- **AC-7:** kein Lauf-Diff insgesamt (alle Lanes `false`); verfügbarer Diff ohne
  geänderte Dateien → `diff_available: true`, `files: []` (unterscheidbar von
  „nicht verfügbar").
- **AC-8:** Diff schlägt trotz Snapshot-Paar fehl (z. B. entferntes
  Snapshot-Objekt) → `diff_available: false`, `files: null`, Antwort bleibt 200
  (kein 5xx); andere Lanes weiterhin darstellbar. Zusätzlich ein gezielter
  Fall für einen **Nicht-Timeout-Ausführungsfehler** des Subprozesses (z. B.
  `OSError`/`SubprocessError` beim Diff-Aufruf, etwa via Monkeypatch) →
  dieselbe kanonische Form und weiterhin 200.
- **AC-3:** vorhandene uneinheitliche `x-adw-*`-Blöcke → inhaltlich
  unveränderter, semantisch äquivalenter YAML-Text (Schlüssel/Werte/
  Verschachtelung; keine Text-Treue bei Kommentaren/Quoting), nur
  `x-adw-*`-Top-Level-Einträge, Dokumentreihenfolge, andere Einträge fehlen.
- **AC-4:** fehlendes/unlesbares/nicht-Mapping/ausbrechendes `contract.yaml`
  sowie Contract ohne `x-adw-*`-Schlüssel → `declared_scope: null`, kein Fehler.
  Darunter ein sicher ladbares Mapping mit **nicht-String-Schlüsseln** (und
  ohne passenden String-Schlüssel) → `declared_scope: null`, kein 5xx.
- **AC-5:** keine Scope-Markierung, kein Urteilsfeld irgendwo in `change_scope`.
- **AC-9:** additiv/read-only — bestehende Felder unverändert, `change_scope`
  immer präsent. Template-Fallbacks stichprobenartig mitprüfen („kein Diff
  verfügbar", „keine geänderten Dateien", „kein deklarierter Scope") — im
  Rahmen des Richtwerts, kein eigener Template-Testblock.

## Gates / Definition of Done
- `uv run ruff check .` und `uv run pytest -x -q` grün.
- AC-1…AC-9 durch `tests/test_gui_*.py` abgedeckt (inkl. der oben genannten
  Pflichtfälle).
- Keine Änderung an `contract.yaml` (dem Lauf-Artefakt), `adw/phases.py`, der
  Diff-Route, `_git_diff`/`_parse_numstat`, Timeline/Raw/Trace/Diff-Tab/SSE oder
  an Persistenz-Zuständen; keine neue Dependency (YAML über das vorhandene
  `yaml`-Modul).
- Doku (GUI-SPEC §7.2 EN+DE, CHANGELOG EN+DE `Unreleased`) aktualisiert.

## Deferred (bewusst nicht gebaut)
Die folgenden Ideen sind defensibel, aber für diese Aufgabe unverhältnismäßig.
Sie sind keine Akzeptanzkriterien und werden **auch im Codex-/Fix-Zyklus nicht
nachgebaut**:
- Strukturierter Datei-Scope (`x-adw-scope.files` bzw. Pfadmuster) in der
  Contract-Erzeugung (Orchestrator-Änderung).
- Eine darauf aufbauende echte Scope-Verletzungsprüfung, die Dateien gegen
  deklarierte Pfadmuster abgleicht und als „im Scope"/„außerhalb" markiert.
- Scope-Check je Fix-Runde bzw. je Schritt; Historie über mehrere Vergleiche.
- Scope-Verletzung als Gate, Laufabbruch oder andere Durchsetzung.
- Contract-Schema-Prüfung, OpenAPI-Validierung, Bewertung der Contract-Qualität.
- Historisierung oder Persistenz von Scope-Bewertungen; Cross-Run-Auswertungen,
  Trends oder Vergleiche des Änderungsumfangs.
