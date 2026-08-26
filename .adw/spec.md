# Spec: Änderungsumfang eines Laufs sichtbar machen (Dateien + deklarierter Scope)

## Ziel
Das Run-Detail der GUI zeigt an einer Stelle nebeneinander, (a) welche Dateien ein
Lauf tatsächlich geändert hat — mit `+/-`-Zahlen je Datei, gruppiert je Lane — und
(b) den im Contract deklarierten Scope so, wie er dort steht. Beide Fakten stehen
unbewertet nebeneinander; die Beurteilung, ob eine Änderung „im Scope" liegt, macht
der Mensch. Grundlage ist ausschließlich die bereits vorhandene Snapshot- und
Diff-Logik; es entsteht keine neue Git-Operation, keine neue Route, keine neue
Dependency und kein neuer Zustand.

## Umfang (Scope)
- Erweiterung der Antwort von `GET /api/runs/{repo}/{run_id}` um ein additives
  Top-Level-Feld `change_scope` mit den Lane-Dateilisten und dem deklarierten
  Contract-Scope.
- Genau ein Vergleich je Lane: erster gegen letzten gültigen Snapshot dieser Lane
  (aus `_snapshots_by_lane`, dieselbe Strukturvalidierung der Refs
  `refs/adw/<run_id>/<seq>` wie heute). Je Datei `additions`/`deletions` in der
  Form, die die bestehende Numstat-Auswertung liefert (Binärdatei → `null`).
- Lesen von `contract.yaml` über den bestehenden whitelist-basierten Artefakt-Pfad
  mit dem bereits vorhandenen `yaml`-Modul; Anzeige aller Top-Level-Blöcke, deren
  Schlüssel mit `x-adw-` beginnt, als lesbarer YAML-Text — ohne Vereinheitlichung
  oder Interpretation.
- Erklärte Zustände für fehlende/unbrauchbare Lane-Snapshots, fehlenden Lauf-Diff
  insgesamt und fehlenden deklarierten Scope.
- Darstellung im Run-Detail (Single-Lane `backend`), read-only wie der Rest der
  GUI. Interne Helper-Signaturen, Markup und CSS sind nicht Teil des Contracts.
- Doku: `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` (§7.2), `CHANGELOG.md` +
  `CHANGELOG.de.md` (`Unreleased`).

## Nicht-Ziele
- **Kein automatisches Urteil.** Keine Datei wird als „im Scope" oder „außerhalb"
  markiert; es gibt keine Verletzungsprüfung. Die Datengrundlage dafür existiert
  nicht (kein Contract nennt Dateien/Pfadmuster) — ein Finding „es fehlt die
  Verletzungsprüfung" ist gegenstandslos, auch im Review-Loop (E1).
- Keine Ableitung von Dateien oder Pfadmustern aus fachlichen Scope-Texten und
  keine Normalisierung der uneinheitlichen `x-adw-*`-Formen (`surfaces`,
  `externally_observable_surfaces`, `invariants`, …).
- Keine Änderung an `contract.yaml` oder an der Contract-Erzeugung in
  `adw/phases.py`; ein strukturierter Datei-Scope ist Deferred (E3).
- Kein OpenAPI-Validator, keine Schema-Prüfung des Contracts, keine neue
  Dependency (E2).
- Kein zweiter Git-Aufrufpfad, keine neue Git-Operation, keine Änderung an der
  Diff-Route `GET …/diff` oder an `_git_diff`/`_parse_numstat` (E5).
- Kein Scope-Check je Schritt/Fix-Runde, keine Historie (E4).
- Kein Gate, keine Durchsetzung, keine Konfigurationsoption, kein Export (E6).
- Keine Bewertung der Contract-Qualität, keine Cross-Run-Auswertung.
- Keine Änderung an Timeline, Raw, Trace, Diff-Tab oder SSE; kein
  Persistenz-Zustand.

## Akzeptanzkriterien
Alle Kriterien beschreiben beobachtbares Verhalten der Antwort von
`GET /api/runs/{repo}/{run_id}` und ihrer Darstellung im Run-Detail.

**AC-1 (Lane-Menge, Reihenfolge und Dateiliste — A1, E4):** Die Antwort enthält
additiv ein Top-Level-Feld `change_scope`; dessen Feld `lanes` ist eine Liste
mit genau einem Eintrag je beobachteter Lane. Beobachtet ist eine Lane genau
dann, wenn das Event-Log dieses Laufs (a) einen `lane`-Span mit nicht-leerem
Namen (`payload.name`) enthält oder (b) ein Snapshot-Event, das die bestehende
Strukturvalidierung besteht (Ref-Form `refs/adw/<run_id>/<seq>`, wie in
`_snapshots_by_lane`), diese Lane deklariert. Snapshot-Events, die diese
Validierung nicht bestehen, tragen weder zur Lane-Menge noch zu Snapshot-Paaren
bei — dieselbe Behandlung wie heute beim Bracketing. Eine nur über gültige
Snapshots beobachtete Lane (ohne `lane`-Span) erscheint mit ihrem Diff; mehrere
Beobachtungen desselben Lane-Namens ergeben genau einen Eintrag. Die Reihenfolge
der Liste ist die der ersten Beobachtung im Event-Log (kleinstes Seq über beide
Quellen) — deterministisch aus den bereits geladenen Events ableitbar.

Hat eine Lane mindestens zwei gültige Snapshots, enthält ihr Eintrag `lane`
(Name), `diff_available: true` und `files`: die Dateien des Diffs zwischen dem
Snapshot mit dem niedrigsten und dem mit dem höchsten Seq dieser Lane, in der
Reihenfolge der bestehenden Numstat-Auswertung. Jeder Datei-Eintrag trägt `path`,
`additions` und `deletions`; bei Binärdateien sind `additions`/`deletions`
`null`. Snapshots anderer Lanes werden nie einbezogen.

**AC-2 (Bestehende Diff-Logik als einzige Datenquelle — E5):** Die Dateilisten
entstehen mit derselben Git-Diff- und Numstat-Logik, die bereits die Diff-Route
versorgt, inklusive der bestehenden Struktur- und Laufzugehörigkeitsvalidierung
der Snapshot-Refs. Es entsteht keine neue Art von Git-Operation, Worktree, Index
und Refs bleiben unberührt, und Anfrage wie Antwort von
`GET /api/runs/{repo}/{run_id}/diff` bleiben unverändert.

**AC-3 (Contract-Scope als Text — A2, E2):** `change_scope` enthält
`declared_scope`, entweder als lesbaren YAML-Text oder `null`. Ist ein lesbares
`contract.yaml` vorhanden, gibt der Text alle Top-Level-Einträge mit
Schlüssel-Präfix `x-adw-` in Dokumentreihenfolge wieder — Schlüssel, Werte und
Verschachtelung inhaltlich unverändert, ohne Umbenennung, Zusammenführung oder
Interpretation. Andere Top-Level-Einträge des Contracts erscheinen nicht.

**AC-4 (Fehlender/leerer Scope — A2):** Fehlt `contract.yaml`, ist es nicht über
den bestehenden Artefakt-Pfad lesbar, kein YAML-Mapping oder nicht sicher als
YAML lesbar, oder enthält es keinen Top-Level-Schlüssel mit Präfix `x-adw-`, ist
`declared_scope: null` und die Ansicht sagt klar „kein deklarierter Scope" — ohne
Fehler, ohne 5xx, ohne erfundene Bewertung. Das Fehlen ist ein neutraler
Abwesenheitszustand, keine Scope-Verletzung.

**AC-5 (Kein Urteil — A3, E1):** API und Oberfläche stellen Dateilisten und
Scope-Text lediglich nebeneinander. Es gibt kein Feld und keine Markierung für
„im Scope", „außerhalb", „Verletzung", Konformität oder Contract-Qualität, keine
Zuordnung eines Dateipfads zu einem `x-adw-*`-Eintrag und keine Warnung, Wertung
oder daraus abgeleitete Laufentscheidung.

**AC-6 (Lane ohne verwertbaren Diff — A4):** Hat eine beobachtete Lane (AC-1)
keinen oder nur genau einen gültigen Snapshot, enthält ihr Eintrag
`diff_available: false` und `files: null` — kanonisch genau diese Form, nicht
ein weggelassenes Feld und nicht `[]`; `files: []` ist ausschließlich dem
verfügbaren Diff ohne geänderte Dateien (AC-7) vorbehalten. Die Ansicht zeigt
für diese Lane klar „kein Diff verfügbar" statt einer leeren Tabelle. Das gilt
insbesondere für eine nur über ihren `lane`-Span beobachtete Lane ganz ohne
gültige Snapshots — ihr Eintrag entfällt nicht, sondern ist mit
`diff_available: false` vorhanden. (Ein Diff eines einzelnen Snapshots gegen
sich selbst wäre garantiert leer und würde fälschlich „keine Änderungen"
behaupten — er wird nicht dargestellt.) Nicht verfügbare Diffs einer Lane
verhindern nicht die Darstellung verfügbarer Diffs anderer Lanes.

**AC-7 (Kein Lauf-Diff insgesamt und echte Leerergebnisse — A4):** Hat keine
Lane ein verwertbares Snapshot-Paar, entfällt die Tabellenansicht mit einer
klaren Aussage („kein Lauf-Diff verfügbar"); der Contract-Scope-Teil (AC-3/AC-4)
bleibt davon unberührt darstellbar. Ergibt ein verfügbarer Vergleich keine
geänderten Dateien, bleibt das von „nicht verfügbar" unterscheidbar: der
Eintrag trägt `diff_available: true` mit `files: []`, und die Ansicht sagt klar,
dass keine geänderten Dateien gefunden wurden — nie eine leere Tabelle ohne
Erklärung.

**AC-8 (Robustheit gegen Git-Fehler — A4, E5):** Schlägt der Diff einer Lane
trotz vorhandenem Snapshot-Paar fehl (z. B. fehlendes Snapshot-Objekt, Timeout),
wird diese Lane wie in AC-6 als „kein Diff verfügbar" behandelt — in derselben
kanonischen Form `diff_available: false` mit `files: null`, nicht als
erfolgreicher leerer Diff (`files: []`). Die Antwort `GET /api/runs/{repo}/{run_id}` bleibt
ein gültiger 200-Response; nie ein 5xx, nie ein einseitiger/erfundener Diff.

**AC-9 (Additiv, read-only — E6):** Der Änderungsumfang-Block ist eine reine
Projektion bereits geladener Events und Artefakte: keine neue Route, kein
Schreibzugriff, kein neuer Zustand, keine Aktion, die Lauf oder Repository
verändert. Bestehende Felder der Run-Detail-Antwort (`run`, `phases`, `tree`,
`raw`, `problems`, `latest_context`, ggf. `recovery`/`plan_skeleton`) bleiben
unverändert. Im Run-Detail sind je Datei Pfad und `+/-`-Zahlen sichtbar; `null`
bei Binärdateien wird verständlich als „nicht numerisch verfügbar" dargestellt.

## Definition of Done
- AC-1 … AC-9 sind erfüllt und durch Tests unter `tests/` als `test_gui_*.py`
  abgedeckt; Git-Aufrufe laufen gegen temporäre Repos nach dem Muster von
  `tests/test_gui_diff_endpoint.py` / `tests/test_gui_diff_pairing.py`.
- Abgedeckt sind mindestens: mehrere Dateien mit numerischen `+/-`-Werten;
  Binärdatei → `null`; erster-gegen-letzter Snapshot; Trennung der Lanes; genau
  ein Snapshot; keine Snapshots; nur über den `lane`-Span beobachtete Lane ohne
  gültige Snapshots (Eintrag vorhanden, `diff_available: false`, `files:
  null`); nur über gültige Snapshots beobachtete Lane; strukturell ungültige
  Snapshot-Events tragen nichts bei; mehrfache Beobachtung desselben
  Lane-Namens → genau ein Eintrag; nicht lieferbarer Snapshot-Diff (`files:
  null`, nicht `[]`); kein Lauf-Diff insgesamt; verfügbarer Diff ohne geänderte
  Dateien (`diff_available: true`, `files: []`); vorhandene uneinheitliche
  `x-adw-*`-Blöcke; fehlender Scope-Block; fehlendes/unlesbares `contract.yaml`;
  keine Scope-Markierung.
- Richtwert ~14 neue Tests (Bestand: 978); mehr als ~22 ist Scope-Drift.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
- Doku aktualisiert: `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` (§7.2, inkl.
  additivem API-Feld, fehlender automatischer Bewertung und Fallback-Zuständen)
  und `CHANGELOG.md` + `CHANGELOG.de.md` (`Unreleased`).
- Keine Änderung an `contract.yaml`, `adw/phases.py`, der Diff-Route,
  `_git_diff`/`_parse_numstat`, Timeline/Raw/Trace/Diff-Tab/SSE oder an
  Persistenz-Zuständen; keine neue Dependency (YAML über das vorhandene
  `yaml`-Modul).

## Deferred (bewusst nicht gebaut)
Die folgenden Ideen sind defensibel, aber für diese Aufgabe unverhältnismäßig.
Sie sind keine Akzeptanzkriterien und werden auch im Codex-/Fix-Zyklus **nicht**
nachgebaut:
- Strukturierter Datei-Scope (`x-adw-scope.files` bzw. Pfadmuster) in der
  Contract-Erzeugung (Orchestrator-Änderung).
- Eine darauf aufbauende echte Scope-Verletzungsprüfung, die Dateien gegen
  deklarierte Pfadmuster abgleicht und als „im Scope"/„außerhalb" markiert.
- Scope-Check je Fix-Runde bzw. je Schritt; Historie über mehrere Vergleiche.
- Scope-Verletzung als Gate, Laufabbruch oder andere Durchsetzung.
- Contract-Schema-Prüfung, OpenAPI-Validierung, Bewertung der Contract-Qualität.
- Historisierung oder Persistenz von Scope-Bewertungen; Cross-Run-Auswertungen,
  Trends oder Vergleiche des Änderungsumfangs.
