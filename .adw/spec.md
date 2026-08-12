# Spezifikation — Vier Robustheits-Fixes an den Fehlerpfaden des ADW-Orchestrators

Quelle: `.adw/issue.md` (Aufgaben A–D, Fehlerbilder F1–F5). Jeder Fix ist ein
Bugfix im Sinne der TDD-Regel: zuerst ein Test, der das Fehlerbild reproduziert
und RED ist, dann der Fix. Die vorentschiedenen Punkte E1–E5 und die
Scope-Deckel des Issues sind bindend; ein Review-Finding, das einen Deferred-
oder vorentschiedenen Punkt einführen will, wird abgewiesen und mit Begründung
dokumentiert, nicht umgesetzt (Deferred-Ventil — gilt ausdrücklich auch für den
Codex-Review-Loop).

## Goal

Der Orchestrator übersteht die dokumentierten Fehlerbilder F1–F5 ohne manuelle
Recovery und ohne Verlust wiederaufnehmbarer Läufe, ohne jemals fremde
uncommittete Änderungen zu verwerfen:

1. Ein Ausfall des Codex-Autors im Dual-Authoring (einschließlich Timeout)
   wird kontrollierte Degradation: Die Phase läuft mit dem verbliebenen
   Claude-Entwurf regulär weiter statt mit Traceback und Exit 1 zu crashen
   (F1). Das Zeitlimit der `codex exec`-Subprozesse wird über
   `.adw/config.yaml` konfigurierbar (F2).
2. Die Arbeitsbaum-Prüfung eskaliert nie einen Lauf — sie verweigert höchstens
   die Ausführung und lässt den Lauf resumierbar (F3). ADWs eigene
   Authoring-Reste heilen sich selbst; jede fremde Änderung blockiert weiterhin
   und wird nie verworfen.
3. Ein partieller Synthese-Ausfall (ein Pflicht-Artefakt fehlt oder ist leer)
   wird genau ein In-Session-Schritt-Retry statt einer permanenten Eskalation
   der ganzen Phase (F4).
4. Die Session-ID eines Agent-Laufs wird persistiert, sobald sie bekannt ist,
   sodass `adw resume` nach einem Abbruch mitten im Lauf an die begonnene
   Session anknüpfen kann, statt bereits verbrauchte Tokens zu verlieren (F5).

## Scope

- Der Codex-**Autor**-Pfad des Dual-Authorings (`_draft_stage`) für Spec sowie
  Plan/Contract: Degradation auf den FAILED-Marker-Pfad bei jedem `CodexError`.
- Der neue optionale Config-Key `codex.timeout` in `.adw/config.yaml`; er
  steuert die `codex exec`-Subprozesse (Autor und Review teilen den
  Ausführungspfad, nur der Timeout-Wert ist gemeinsam — die Review-Semantik
  bleibt unberührt).
- Die Arbeitsbaum-Prüfung vor `adw run` und `adw resume`, einschließlich
  Selbstheilung exakt dieser sechs ADW-eigenen Dateien im Haupt-Checkout:
  `.adw/issue.md`, `.adw/spec.md`, `.adw/plan.md`, `.adw/contract.yaml`,
  `.adw/spec-summary.md`, `.adw/plan-summary.md`.
- Die Vollständigkeitsprüfung nach dem Synthese-Agent-Lauf und ein einmaliger
  Reparaturaufruf über dessen bestehende Session.
- Das frühzeitige Persistieren der im SDK-Message-Stream bekannt gewordenen
  Agent-Session-ID in den Run-State (nur der Zeitpunkt ändert sich, E5).
- Dokumentation der Konvention im README/Handbuch, dass die sechs genannten
  Authoring-Artefakte ADW-eigen sind und ausschließlich darauf beschränkte
  uncommittete Änderungen automatisch zurückgesetzt werden.
- Etwaige Protokollierung neuer Ereignisse (z. B. Synthese-Retry) über die
  BESTEHENDE Emitter-API; neue Event-Typen sind zulässig (vorwärtskompatibles
  Format), neue Emitter-Fähigkeiten nicht.

## Non-goals

- Keine Verlagerung des Authorings in einen Scratch-Worktree, kein
  Spec-Amendment-Schritt (Struktur-Paket, eigener späterer Lauf).
- Kein Off-limits-Enforcement, keine Skill-/Template-Änderungen.
- Keine Änderung am Codex-**Review**-Pfad: Schlägt Codex im Review-Loop fehl,
  gilt die heutige Semantik unverändert.
- Keine Änderung an `adw/events.py`, `adw/snapshots.py`, `adw/gui/**`; keine
  neuen Emitter-Fähigkeiten.
- Kein genereller Retry-Mechanismus, keine Retry-Zähler in der Config, kein
  Backoff — genau die vier beschriebenen Fixes. Kein automatischer Retry des
  Codex-Autors (E1); der Synthese-Retry ist genau einer (E4).
- Die Selbstheilungs-Liste ist weder konfigurierbar noch glob-basiert (E2);
  keine Heilung weiterer Dateiklassen.
- Keine neuen Laufzeit-Dependencies.
- Keine Änderung an Limits, Circuit-Breaker, Review-Loop-Policy oder
  Phasenreihenfolge; keine Änderung der Resume-Logik (`expected_head`,
  Orchestrator-only-Commits, Gate-Wiederholung) — Aufgabe D ändert nur den
  Zeitpunkt der Session-Persistierung (E5).
- Keine Einführung von `flake8`, `isort` oder `black` als Dependency,
  Konfiguration oder Kommando; das Projekt nutzt `ruff` (E3).

## Acceptance criteria

### A — Codex-Ausfall wird kontrollierter Ausfall (F1 + F2)

- **A1.** Wirft der Codex-**Autor** im Dual-Authoring einen `CodexError`
  (jeder Subprozess-Fehlschlag, einschließlich Timeout), bricht der Lauf NICHT
  ab: Der `<kind>.codex.FAILED`-Marker wird geschrieben (wie heute), danach
  läuft die Phase mit dem verbliebenen Claude-Entwurf regulär weiter — über
  denselben Pfad, der heute schon greift, wenn der Marker aus einem früheren
  Anlauf vorliegt. Kein Python-Traceback, kein Exit 1, keine manuelle
  Recovery; der Run-State wird durch den Codex-Ausfall allein nicht auf
  `escalated` gesetzt.
- **A2.** Ergebnis der degradierten Phase ist regulär: Die Pflicht-Artefakte
  und die Summary existieren, und die Synthese arbeitet auf Einquellen-Basis
  (bestehender „kein Codex-Entwurf“-Zweig). Der Codex-Autor wird für diesen
  Draft-Schritt nicht automatisch erneut ausgeführt — ein Ausfall kostet den
  Gegenentwurf, mehr nicht (E1).
- **A3.** `.adw/config.yaml` akzeptiert den optionalen Key `codex.timeout`:
  ganzzahlige Sekunden, größer als 0, Default 900. Er gilt für die
  `codex exec`-Subprozesse. Fehlt `codex` oder `codex.timeout`, bleibt das
  effektive Zeitlimit unverändert 900 Sekunden.
- **A4.** Ein ungültiger Wert (≤ 0, nicht-ganzzahlig, Boolean) wird über die
  bestehende Config-Fehlerbehandlung abgelehnt, bevor der Lauf startet.
- **A5.** Regressionstests: Ein CodexRunner, der `CodexError` wirft, lässt den
  Lauf mit einem Entwurf zu Ende laufen; der Marker existiert; das
  Phasenergebnis ist regulär (Artefakte + Summary vorhanden). Tests decken
  außerdem Default, gültigen Override und ungültigen Wert von `codex.timeout`
  ab.

### B — Blocker bleibt Blocker, eigene Reste heilen sich (F3)

- **B1.** Die Arbeitsbaum-Prüfung ESKALIERT NIE einen Lauf — weder bei
  `adw run` noch bei `adw resume`. Sie verweigert höchstens die Ausführung mit
  klarer Meldung und Nichtnull-Exit-Code; der Run-State bleibt inhaltlich
  unverändert (kein `escalated`, kein Eskalations-Report), ein späterer
  `adw resume` bleibt möglich. Eine Verweigerung ist eine Verweigerung, keine
  Eskalation.
- **B2.** Betreffen die uncommitteten Änderungen AUSSCHLIESSLICH die sechs im
  Scope genannten ADW-eigenen Authoring-Artefakte, setzt ADW sie selbst
  zurück — getrackte Dateien per Checkout, ungetrackte per Löschen — und
  setzt den angeforderten Lauf bzw. Resume regulär fort.
- **B3.** Die Selbstheilung greift nur, wenn sämtliche dirty Pfade exakt zur
  festgelegten Liste gehören; die Liste ist weder konfigurierbar noch
  glob-basiert (E2).
- **B4.** Jede andere dirty Datei blockiert weiterhin mit Meldung (nie
  Eskalation). Ein gemischter Zustand — mindestens ein ADW-eigenes Artefakt
  UND mindestens eine fremde Datei — wird verweigert, nicht geheilt: ADW
  verwirft dabei nichts, weder die fremde noch die ADW-eigene Datei.
- **B5.** README/Handbuch dokumentieren die ADW-eigen-Konvention für die sechs
  Artefakte am bestehenden Ort der Projektdokumentation. Das Repo führt jede
  Doku als Sprachpaar (`README.md`/`README.de.md`,
  `docs/handbuch/ADW-USER-HANDBUCH.md`/`.de.md` usw.); die Konvention wird
  daher in BEIDEN Fassungen des gewählten Dokuments ergänzt, inhaltlich
  gleichwertig. Welches Dokument (README oder Handbuch) der Ort ist, bleibt
  Umsetzungsentscheidung; eine einsprachige Ergänzung ist nicht ausreichend.
- **B6.** Regressionstests: (a) dirty `.adw/spec.md` + `adw resume` → Lauf
  läuft weiter, Datei zurückgesetzt, keine Eskalation; (b) dirty fremde
  Datei → Ausführung verweigert, Run-State unverändert, Resume danach möglich;
  (c) gemischter Zustand → verweigert, nichts verworfen.

### C — Partieller Synthese-Ausfall wird Schritt-Retry (F4)

- **C1.** Fehlt nach einem Synthese-Lauf ein Pflicht-Artefakt oder ist es leer
  (Pflicht-Artefakte umfassen die Artefakte des Schritts und seine Summary),
  wird GENAU EINMAL derselbe Synthese-Schritt wiederholt — über die vorhandene
  Session, mit dem Hinweis, welches Artefakt fehlt bzw. leer ist.
- **C2.** Liefert der Reparaturaufruf das fehlende Artefakt, wird das
  Authoring regulär fortgesetzt; bereits korrekt erzeugte Artefakte bleiben
  erhalten. Liefert auch der Retry das Artefakt nicht, eskaliert der Lauf wie
  heute — es gibt keinen zweiten Reparaturversuch (E4).
- **C3.** Der Retry verbraucht keine Authoring-Runde und keine Review-Runde —
  er ist Reparatur, kein Review-Zyklus; Rundenzähler und Review-Loop-Policy
  (Severity-Schwelle, Circuit-Breaker, Rundendeckel) bleiben unberührt.
- **C4.** Eine etwaige Protokollierung des Retries nutzt ausschließlich die
  bestehende Emitter-API (neuer Event-Typ zulässig, keine neuen
  Emitter-Fähigkeiten).
- **C5.** Regressionstests: Ein Mock-Agent, der beim ersten Aufruf nur eines
  von zwei Artefakten schreibt und beim zweiten das fehlende (dieselbe
  Session), führt zu einem regulär abgeschlossenen Authoring; einer, der es
  zweimal nicht liefert, führt nach genau zwei Aufrufen zur Eskalation.

### D — Session-ID sofort checkpointen (F5)

- **D1.** Die Session-ID eines Agent-Laufs wird im persistenten Run-State
  gespeichert, SOBALD sie bekannt ist (sie erscheint früh im
  SDK-Message-Stream) — nicht erst nach Abschluss des Laufs. Der Mechanismus
  (Callback aus dem Runner, Zwischenspeichern im State) ist
  Umsetzungsentscheidung und wird hier nicht festgeschrieben.
- **D2.** Wirkung: Nach einem Abbruch mitten im Agent-Lauf kann `adw resume`
  an die begonnene Session anknüpfen, statt den Agent-Lauf von vorn zu
  beginnen. Die bestehende Resume-Semantik (`expected_head`,
  Orchestrator-only-Commits, Gate-Wiederholung) bleibt unverändert (E5).
- **D3.** Regressionstests: (a) Ein Runner-Abbruch nach Erscheinen der
  Session-ID und vor Abschluss des Laufs hinterlässt die ID im persistierten
  State; (b) ein anschließendes `adw resume` übergibt genau diese persistierte
  Session-ID an den fortgesetzten Agent-Lauf — nachgewiesen über die
  bestehende Resume-Semantik, ohne neue Resume-Mechanik (E5).

### Kontraktfläche (Single-Lane-Projekt)

Der Kontrakt pinnt nur die extern beobachtbare Fläche — keine internen
Helper-Signaturen, keine Callback- oder Marker-Mechanik:

- das CLI-Verhalten in den vier Fehlerpfaden: Exit-Codes, Meldungen,
  State-Wirkung (A1/A2, B1, C1/C2, D2);
- den Config-Key `codex.timeout` (A3/A4);
- die Zusicherungen aus B1/B4: Die Arbeitsbaum-Prüfung eskaliert nie, fremde
  Dateien werden nie verworfen.

## Definition of Done

1. Alle Akzeptanzkriterien A1–A5, B1–B6, C1–C5, D1–D3 sind umgesetzt und
   durch Tests abgedeckt (TDD: pro Fix zuerst ein RED-Test, der das
   Fehlerbild reproduziert, dann der Fix).
2. Ein simulierter `CodexError` im Autor-Pfad hinterlässt den FAILED-Marker,
   propagiert nicht mehr als Traceback/Exit 1 und die Phase schließt
   einquellig regulär ab. `codex.timeout` ist optional, validiert (> 0,
   ganzzahlig, Default 900); ohne den Key ist das Verhalten unverändert.
3. Die Arbeitsbaum-Prüfung eskaliert in keinem `run`- oder `resume`-Pfad.
   Exakt die sechs ADW-Artefakte heilen sich selbst; jede andere oder
   gemischte dirty Menge blockiert mit Meldung, ohne etwas zu verwerfen, und
   erhält Run-State und Resume-Fähigkeit. Die Konvention ist im
   README/Handbuch dokumentiert — in beiden Sprachfassungen des gewählten
   Dokuments (Paar-Konvention des Repos).
4. Der Synthese-Schritt wird bei fehlendem/leerem Pflicht-Artefakt genau
   einmal in derselben Session mit Fehlhinweis wiederholt, ohne eine
   Authoring-Runde zu verbrauchen; fortgesetzte Unvollständigkeit eskaliert
   wie heute.
5. Die Session-ID ist ab ihrem Erscheinen im Stream persistiert; ein Abbruch
   danach lässt `adw resume` an die Session anknüpfen. Tests decken sowohl
   die Persistierung als auch die Übergabe der persistierten ID an den
   fortgesetzten Agent-Lauf ab. Resume-Semantik unverändert.
6. Keine neuen Laufzeit-Dependencies; `adw/events.py`, `adw/snapshots.py`,
   `adw/gui/**` unverändert (neue Event-Typen nur über die bestehende
   Emitter-API); Limits, Circuit-Breaker, Review-Loop-Policy und
   Phasenreihenfolge unverändert.
7. Die realen Gates sind grün: `uv run ruff check .` und
   `uv run pytest -x -q`. `flake8`, `isort` und `black` tauchen nirgends als
   Dependency, Konfiguration oder Kommando auf (E3).

Hinweis (nicht bindend, keine Abnahmebedingung): Der Richtwert des Issues von
rund 16–24 neuen Tests für A–D dient der Planung; abgenommen wird über die
Abdeckung der spezifizierten Verhaltensweisen, nicht über eine Testzahl. Die
Regel, dass Review-Findings zu Deferred- oder vorentschiedenen Punkten
abgewiesen und dokumentiert werden, bleibt Review-Leitlinie (siehe Präambel
und Deferred), nicht Abnahmebedingung der Implementierung.

## Deferred (bewusst nicht gebaut)

Vertretbare, aber unverhältnismäßige oder ausdrücklich gedeckelte Ideen
gehören hierher, nicht in Akzeptanzkriterien. Ein Review-Finding, das einen
dieser Punkte fordert, wird mit dieser Begründung abgewiesen — das Ventil
bindet auch den Codex-Review-Loop.

- Ein generelles Retry-Framework: Retry-Zähler in der Config, Backoff,
  Timeout-Adaption, Telemetrie. Dieser Lauf baut genau die vier Fixes; der
  Synthese-Retry ist genau einer (E4), der Codex-Autor bekommt keinen (E1).
- Automatischer oder konfigurierbarer Retry des Codex-Autors: Ein Ausfall
  kostet den Gegenentwurf, mehr nicht (E1).
- Heilung weiterer Dateiklassen oder eine konfigurierbare/glob-basierte
  Selbstheilungs-Liste (E2); fremde Dateien werden nie automatisch
  zurückgesetzt, nur verweigert.
- Änderung der Codex-REVIEW-Fehlerbehandlung (z. B. Degradation statt
  Eskalation bei Review-Ausfall); `codex.timeout` berührt nur den geteilten
  Subprozess-Timeout-Wert.
- Verlagerung des Authorings in einen Scratch-Worktree und ein
  Spec-Amendment-Schritt (Struktur-Paket, eigener späterer Lauf);
  Off-limits-Enforcement; Skill-/Template-Änderungen.
- Neue Emitter-Fähigkeiten oder GUI-/Snapshot-Änderungen zur Visualisierung
  der neuen Ereignisse; zusätzliche persistente Retry-Zustände zur Absicherung
  weiterer Crash-Fenster ohne dokumentiertes Schadensbild.
