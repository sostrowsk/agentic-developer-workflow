# Implementierungsplan — Vier Robustheits-Fixes an den Fehlerpfaden des ADW-Orchestrators

Quelle: `.adw/spec.md`; gebaut wird strikt gegen `.adw/contract.yaml`. Vier
Bugfixes (A–D) an bereits existierenden Fehlerpfaden. TDD ist bindend: pro Fix
zuerst ein RED-Test, der das Fehlerbild reproduziert, dann der minimale Fix.
Die vorentschiedenen Punkte E1–E5, die Scope-Deckel und der
`Deferred`-Abschnitt sind bindend und werden unverändert durchgereicht — ein
Review-Finding, das einen Deferred- oder vorentschiedenen Punkt einführen
will, wird abgewiesen und dokumentiert, nicht umgesetzt (Deferred-Ventil,
gilt auch für den Codex-Review-Loop).

Single-Lane-Projekt: Es existiert nur der Workstream **backend**, keine
`frontend`-Lane. Der Kontrakt pinnt ausschließlich die extern beobachtbare
Fläche (CLI-Verhalten der vier Fehlerpfade, `codex.timeout`, die
Nie-Eskalation-/Nie-Verwerfen-Garantien) — keine internen Helper-Signaturen,
keine Callback- oder Marker-Mechanik.

## Betroffene Module (Orientierung, keine Contract-Fläche)

- `adw/codex.py` — `CODEX_TIMEOUT` (harte Modul-Konstante, `:36`); der
  Subprozess-Timeout in `CodexRunner._execute` (`:427/:429`).
- `adw/config.py` — Pydantic-Config-Modelle; `PositiveSeconds` (`:11`)
  existiert bereits und trägt genau die geforderte Validierung
  (int, `gt=0`, `strict`).
- `adw/phases.py` — `_codex_draft` (Degradations-/Marker-Pfad, `:707`),
  `_draft_stage` (`:576`), `_reviewed_authoring_loop` (Synthese-Loop, `:819`;
  Vollständigkeits-Check `:879`), die getrackt-dirty-Prüfung im Authoring-Pfad
  (`:355`–`:371`), die Build-Lane-Session-Persistenz (`:1347`),
  `_agent_run` (`:115`).
- `adw/agents.py` — `SdkAgentRunner._collect` (Session-ID erscheint im
  Stream, `:551`), `AgentResult` (`:161`).
- `adw/state.py` — `RunState.authoring_session` (`:128`),
  `LaneState.session_id` (`:44`).
- `adw/cli.py` — `run` (`:118`), `resume` (`:190`), `approve` (`:217`); Ort des
  neuen Vorflugs. Maßgeblich sind die DREI Aufrufstellen von `_execute(ctx)`:
  `:186` (run), `:213` (resume), `:249` (approve).
- `adw/mock.py` — Mock-Runner muss den Session-ID-Vertrag mittragen.
- `README*.md` / `docs/handbuch/ADW-USER-HANDBUCH*.md` — Doku (B5).

---

# Workstream: backend

Reihenfolge A → B → C → D; die Fixes sind unabhängig, aber A liefert den
Config-Key, an dem sich B/C/D beim Testen nicht stören. Jeder Schritt endet
mit grünen Gates (`uv run ruff check .`, `uv run pytest -x -q`).

## A — Codex-Ausfall wird kontrollierter Ausfall (F1 + F2)

Erfüllt A1–A5. Zwei Teile: Degradation (F1) und konfigurierbarer Timeout (F2).

### A.1 — RED-Tests zuerst

1. **F1-Regression** (`tests/test_phases.py`): Ein Codex-Fake, dessen
   Autor-Aufruf bei jedem Aufruf `CodexError` wirft, lässt eine
   Authoring-Phase über `_draft_stage` → `_reviewed_authoring_loop` regulär zu
   Ende laufen: der `<kind>.codex.FAILED`-Marker existiert im
   `drafts/`-Ordner, Pflicht-Artefakte und Summary existieren, der Run-State
   steht NICHT auf `escalated`, kein Traceback/Exit 1. Nachweis der
   Einquellen-Synthese: der Synthese-Task enthält den „kein
   Codex-Entwurf“-Zweig. Zusätzlich nachweisen, dass derselbe Draft-Schritt
   den Codex-Autor wegen des Markers nicht automatisch erneut startet (E1).
   Den Test für Spec UND Plan/Contract an den gemeinsamen Draft-Pfad
   anbinden, ohne den unveränderten Review-Fehlerpfad umzudeuten.
2. **F2-Config-Tests** (`tests/test_config.py`): (a) fehlender `codex`-Key →
   effektiver Timeout 900; (b) `codex.timeout: 120` → effektiver Timeout 120;
   (c) ungültige Werte (`0`, `-5`, `1.5`, `true`) → `ConfigError` vor
   Laufstart.
3. **F2-Durchreich-Test** (`tests/test_codex.py`): der konfigurierte Wert
   kommt am gemeinsamen `codex exec`-Subprozesspfad als effektives Timeout an.

### A.2 — Fix Degradation (F1)

- Sicherstellen, dass jeder `CodexError` des **Autors** (inkl.
  `subprocess.TimeoutExpired` → `CodexError` in `_execute`, `:428`) im
  `_codex_draft`-`except CodexError`-Zweig (`:743`) endet: Marker schreiben,
  `_log_warning`, `return` — der Draft-Pool-Worker eskaliert nie durch einen
  Codex-Autor-Ausfall. Prüfen, dass `codex_future.result()` (`:635`) den
  degradierten Worker nicht doch mit einer propagierten Exception quittiert.
- KEIN automatischer Retry des Codex-Autors (E1). Der bestehende
  Einquellen-Zweig (`_synthesis_task`, `:787`–`:793`) bleibt der Weiterlauf;
  das Gesamtergebnis des CLI-Kommandos folgt dem normalen Phasenpfad,
  einschließlich eines gegebenenfalls regulären Approval-Stopps.
- Unerwartete Nicht-`CodexError`-Ausnahmen sowie der Claude-Autor behalten
  ihre bestehende Semantik: der Claude-Autor degradiert nicht, er eskaliert
  (`DraftSet`-Doku, `:538`).

### A.3 — Fix konfigurierbarer Timeout (F2)

- Neues optionales Config-Modell in `adw/config.py`: `CodexConfig` mit
  `timeout: PositiveSeconds = 900`, als `codex: CodexConfig` mit
  `default_factory` an `AdwConfig` (analog `ci: CiConfig`, `:96`),
  `extra="forbid"` wie die übrigen Modelle. Damit greift die bestehende
  Fehlerbehandlung (`ConfigError`) für ungültige Werte automatisch (A4) —
  Default 900, `> 0`, ganzzahlig, `strict` (kein YAML-Boolean).
- `CODEX_TIMEOUT` bleibt als Default-Wert `900` in `adw/codex.py` erhalten,
  wird aber nicht mehr als effektives Limit hartverdrahtet: der effektive
  Timeout wird dem `CodexRunner` zur Laufzeit aus der Config gereicht
  (Konstruktor-Argument oder pro Aufruf — interne Umsetzungsentscheidung,
  nicht Contract). Autor und Review teilen nur den Timeout-WERT; die
  Review-Fehlerbehandlung und -Policy bleiben unberührt (Scope, Non-goals).
- Verdrahtung dort, wo `CodexRunner` instanziiert wird (`RunContext`-Aufbau
  in `adw/cli.py`): `config.codex.timeout` durchreichen.

**Abnahme A:** A.1-Tests grün; simulierter `CodexError` hinterlässt Marker,
Phase schließt einquellig regulär ab; `codex.timeout` optional/validiert.

## B — Blocker bleibt Blocker, eigene Reste heilen sich (F3)

Erfüllt B1–B6. Kern: Die Arbeitsbaum-Prüfung darf NIE eskalieren; sie
verweigert höchstens (Nichtnull-Exit, Run-State unverändert) oder heilt genau
die sechs ADW-eigenen Artefakte selbst. Gilt für ALLE Kommandos, die den
Phasen-Executor betreten — das sind genau die drei `_execute(ctx)`-Aufrufstellen
in `adw/cli.py`: `adw run` (`:186`), `adw resume` (`:213`) und `adw approve`
(`:249`). `approve` ist der Pfad, der die Authoring-Phase nach einer
Gate-Pause wieder betritt; die Pause ist zugleich das Zeitfenster, in dem ein
Mensch den Checkout dirty machen kann. Ein Vorflug, der nur `run`/`resume`
abdeckt, ließe die heute eskalierende Prüfung in `adw/phases.py` als einzige
Instanz auf dem `approve`-Pfad zurück — B1 („eskaliert NIE") wäre dort verletzt.
Kein weiteres Kommando (`status`, `gui`) kommt in den Scope.

### B.1 — RED-Tests zuerst (`tests/test_cli.py`, ggf. Phasen-/Worktree-Tests)

1. **B6(a):** dirty getracktes `.adw/spec.md` + `adw resume` → Lauf läuft
   weiter, Datei zurückgesetzt (Selbstheilung), Phase NICHT `escalated`, kein
   `escalation.md`.
2. **B6(b):** dirty fremde Datei (z. B. `src/foo.py`) + `adw run`/`adw resume`
   → Ausführung verweigert, Nichtnull-Exit + klare Meldung, Run-State
   inhaltlich unverändert (kein `escalated`), fremde Datei bytegleich
   unangetastet, ein anschließender `adw resume` (nach manueller Bereinigung)
   bleibt möglich.
3. **B6(c):** gemischter Zustand (mind. ein ADW-Artefakt UND mind. eine
   fremde Datei dirty) → verweigert, NICHTS verworfen (weder eigen noch
   fremd), Run-State unverändert.
4. Ungetracktes ADW-Artefakt (z. B. `.adw/spec-summary.md` als neue Datei) im
   Alleinzustand → per Löschen geheilt, Lauf läuft weiter.
5. **`approve`-Pfad:** Run in `awaiting_spec_approval` (bzw.
   `awaiting_approval`) + dirty fremde Datei + `adw approve` → verweigert,
   Nichtnull-Exit, fremde Datei bytegleich, KEINE Eskalation, und die Zusage
   ist NICHT gesetzt (`spec_approval_granted`/`approval_granted` unverändert),
   der Run steht danach unverändert am selben Gate. Gegenprobe: derselbe Run
   mit ausschließlich dirty `.adw/spec.md` → geheilt, `approve` läuft durch.

### B.2 — Fix Arbeitsbaum-Vorflug

- Eine neue Vorflug-Prüfung des **Haupt-Checkouts** vor dem eigentlichen
  Phasenlauf, aufgerufen aus `adw run`, `adw resume` UND `adw approve`
  (`adw/cli.py` — EIN gemeinsamer Helper, drei Aufrufstellen), NICHT im
  Eskalations-Pfad: sie darf `escalate()`/`EscalationError` nie auslösen.
  Aufrufort je Kommando: VOR jeder State-Mutation und vor jedem `state.save()`,
  nicht bloß vor `_execute(ctx)`. Bei `adw approve` wird die Zusage
  (`spec_approval_granted`/`approval_granted`) heute gesetzt und gespeichert,
  BEVOR `_execute` läuft (`:240`–`:246`) — ein Vorflug danach würde die
  Kontrakt-Zusage `run_state_unchanged: true` bei einer Verweigerung brechen.
  Bei Verweigerung: klare Meldung + `typer.Exit`(Nichtnull), OHNE
  `ctx.save()`, OHNE Phasenwechsel, OHNE `escalation.md`; bei `adw run`
  entsteht durch die Verweigerung kein neuer persistenter Run — der Run-State
  bleibt inhaltlich unverändert und resumierbar (B1).
- Erst die VOLLSTÄNDIGE dirty Pfadmenge ermitteln, dann entscheiden — vor
  einer Verweigerung wird nichts mutiert. Dirty-Ermittlung über
  `git status --porcelain -z` im Haupt-Checkout (nicht über `_git`, das
  selbst eskaliert — eigenes, escalation-freies Subprozess-Handling wie in
  `adw/phases.py:1554`).
- Selbstheilungs-Liste als feste Konstante (weder konfigurierbar noch
  glob-basiert, E2/B3): exakt `.adw/issue.md`, `.adw/spec.md`,
  `.adw/plan.md`, `.adw/contract.yaml`, `.adw/spec-summary.md`,
  `.adw/plan-summary.md`.
- Entscheidungslogik:
  - keine dirty Pfade → weiter.
  - ALLE dirty Pfade in der Liste → heilen: getrackte per
    `git checkout -- <pfad>`, ungetrackte per Löschen; dann weiter (B2).
  - sonst (mindestens ein Pfad außerhalb der Liste, inkl. Mischzustand) →
    verweigern, nichts verwerfen (B4).
- Die bestehende, HEUTE eskalierende getrackt-dirty-Prüfung im Authoring-Pfad
  (`adw/phases.py:355`–`:371`) an das neue Verhalten anpassen: sie darf über
  ein ADW-eigenes Artefakt keinen Lauf mehr eskalieren. Sie wird zur
  VERWEIGERUNG reduziert, NICHT ersatzlos entfernt — sie bleibt die letzte
  Instanz, falls der Checkout zwischen Vorflug und Phasenlauf dirty wird, und
  ihr Wegfall würde einen Nutzer-Edit an `.adw/spec.md` wortlos von
  `_archive_artifacts` (`:336`) zurücksetzen lassen. Schutzprüfungen gegen Manipulation während
  einer bereits laufenden Phase (Guard-Ausnahmen für den gecheckpointeten
  Plan-Loop, `:356`–`:363`) bleiben inhaltlich erhalten, soweit sie die
  Startprüfung nicht duplizieren.

### B.3 — Doku (B5)

- Die ADW-eigen-Konvention für die sechs Artefakte am bestehenden Doku-Ort
  ergänzen: die Dateien sind ADW-eigen; ausschließlich darauf beschränkte
  uncommittete Reste werden automatisch zurückgesetzt, jede fremde oder
  gemischte Änderung blockiert und wird nie automatisch verworfen. Ort
  (README ODER Handbuch) ist Umsetzungsentscheidung; die Ergänzung MUSS in
  BEIDEN Sprachfassungen des gewählten Dokuments erfolgen, inhaltlich
  gleichwertig (Paar-Konvention des Repos): `README.md`+`README.de.md` ODER
  `docs/handbuch/ADW-USER-HANDBUCH.md`+`.de.md`. Empfehlung: Handbuch, da es
  die Recovery-Abläufe beschreibt.

**Abnahme B:** B.1-Tests grün; keine Eskalation in `run`/`resume`/`approve`;
exakt die sechs Artefakte heilen sich, jede andere/gemischte Menge blockiert
ohne Verwerfen; eine Verweigerung bei `approve` lässt die Zusage ungesetzt;
Konvention in beiden Sprachfassungen dokumentiert.

## C — Partieller Synthese-Ausfall wird Schritt-Retry (F4)

Erfüllt C1–C5. Genau EIN In-Session-Retry des Synthese-Schritts (E4).

### C.1 — RED-Tests zuerst (`tests/test_phases.py`)

1. **C5(a):** Mock-Agent, der beim ERSTEN Synthese-Aufruf nur eines von zwei
   Pflicht-Artefakten schreibt (z. B. `plan.md` ja, `plan-summary.md`
   leer/fehlend) und beim ZWEITEN Aufruf das fehlende nachliefert — über
   DIESELBE Session (`resume` == die im ersten Aufruf gelieferte Session-ID)
   — führt zu regulär abgeschlossenem Authoring; das bereits korrekte
   Artefakt bleibt erhalten. Der Retry-Task benennt konkret, welches Artefakt
   fehlt bzw. leer ist.
2. Vollständigkeitsregel abdecken: fehlende Summary sowie ein nur aus
   Whitespace bestehendes Artefakt zählen als fehlend/leer.
3. **C5(b):** Mock-Agent, der das fehlende Artefakt ZWEIMAL nicht liefert →
   Eskalation nach genau ZWEI Aufrufen (ein Retry, kein zweiter, E4).
4. **C3-Nachweis:** Der Retry erhöht `authoring_rounds` NICHT und löst vor
   erfolgreicher Vollständigkeit keinen Codex-Review aus
   (Rundenzähler/Review-Loop-Policy unberührt).

### C.2 — Fix Synthese-Retry

- Im Vollständigkeits-Check von `_reviewed_authoring_loop`
  (`adw/phases.py:879`–`:888`): statt bei fehlendem/leerem Pflicht-Artefakt
  sofort zu eskalieren, GENAU EINMAL denselben Synthese-Schritt über die
  vorhandene Session wiederholen. Pflicht-Artefakte = `loop_artifacts`
  (Artefakte + Summary, `:840`); „leer“ schließt Nur-Whitespace ein.
- Reparatur-Task: derselbe Kontext plus expliziter Hinweis, welche Artefakte
  fehlen bzw. leer sind; er fordert ausschließlich deren Vervollständigung
  und lässt bereits korrekte Dateien bestehen. Aufruf via
  `_agent_run(..., resume=session)` mit der bekannten Session-ID.
- Der Retry ist Reparatur, kein Review-Zyklus (C3): er verbraucht KEINE
  Authoring-Runde (`rounds`/`authoring_rounds` unverändert) und keine
  Review-Runde; Severity-Schwelle, Circuit-Breaker und Rundendeckel bleiben
  unberührt. Nach erfolgreicher Reparatur läuft der bestehende Review-Pfad
  regulär weiter (C2).
- Genau EIN Retry (E4): liefert auch der Reparaturaufruf das Artefakt nicht,
  greift der bestehende Eskalations-Pfad (`escalate`, `:882`) wie heute —
  kein zweiter Versuch, kein Backoff, kein persistenter Retry-Zähler.
- Crash-Fenster: die Session ist bereits gecheckpointet
  (`authoring_session`); ob ein In-Memory-Flag genügt (kein neuer
  persistenter Zustand ohne dokumentiertes Schadensbild, siehe Deferred) ist
  Umsetzungsentscheidung — Default: In-Session-Retry ohne neuen State.
- Protokollierung (optional, C4): NUR über die bestehende Emitter-API; ein
  neuer Event-Typ (z. B. `synthesis_retry`) ist zulässig
  (vorwärtskompatibles Format), KEINE neuen Emitter-Fähigkeiten, keine
  Änderung an `adw/events.py`.

**Abnahme C:** C.1-Tests grün; ein Retry repariert und läuft weiter, zwei
Fehlversuche eskalieren nach genau zwei Aufrufen; keine Authoring-/
Review-Runde verbraucht.

## D — Session-ID sofort checkpointen (F5)

Erfüllt D1–D3. Nur der ZEITPUNKT der Persistierung ändert sich (E5).

### D.1 — RED-Tests zuerst (`tests/test_agents.py`, `tests/test_phases.py`, Resume-Testpfad)

1. **D3(a):** Ein Runner, der die Session-ID im Stream bekannt gibt und DANN
   VOR Abschluss abbricht (Exception nach der Session-ID-Meldung),
   hinterlässt die ID im persistierten Run-State — für den Authoring-Lauf in
   `RunState.authoring_session`, für einen Lane-Agent-Lauf in
   `LaneState.session_id` (persistentes `state.json`).
2. **D3(b):** Ein anschließendes `adw resume` übergibt genau diese
   persistierte Session-ID als `resume` an den fortgesetzten Agent-Lauf —
   nachgewiesen über die bestehende Resume-Semantik, ohne neue
   Resume-Mechanik.

### D.2 — Fix frühes Checkpointen

- Ein Callback-Mechanismus vom Runner zum Orchestrator:
  `SdkAgentRunner._collect` (`adw/agents.py:551`) ruft, SOBALD `session_id`
  erstmals im Message-Stream erscheint, einen optional übergebenen Callback
  (z. B. `on_session_id`) auf. `_agent_run` (`adw/phases.py:115`) reicht ihn
  durch; der Callback persistiert die ID unter `ctx.state_lock` in das
  passende State-Feld (`authoring_session` im Synthese-Loop,
  `LaneState.session_id` in der Build-/Integrations-/Review-Lane) und ruft
  `ctx.save()` (atomare State-Speicherung).
- Wiederholte Meldungen derselben ID sind idempotent und ändern keine
  Resume-Semantik. Die bestehenden Post-Run-Zuweisungen (`:871`, `:1347`,
  `:1547`) bleiben als idempotenter Fallback bestehen
  (`result.session_id or …`); `AgentResult` behält seine Session-ID, damit
  erfolgreiche Läufe unverändert funktionieren — der Callback verschiebt nur
  den frühesten Persistierungszeitpunkt nach vorn.
- Der Abbruchpfad bleibt Abbruch, keine Eskalation: nach bekannt gewordener
  und gespeicherter Session-ID endet ein transient abgebrochener Agent-Lauf
  kontrolliert mit Nichtnull-Code in seiner aktuellen, resumierbaren Phase.
- Der Mock-Runner (`adw/mock.py`) bedient den Callback ebenfalls, damit
  Dry-Runs und Tests denselben Vertrag zeigen.
- Resume-Semantik (`expected_head`, Orchestrator-only-Commits,
  Gate-Wiederholung) bleibt unverändert (E5) — keine neuen State-Felder,
  keine neue Resume-Verzweigung.

**Abnahme D:** D.1-Tests grün; Abbruch nach Session-ID lässt die ID im State;
`adw resume` knüpft an die persistierte Session an.

---

## Gates (Definition of Done)

- `uv run ruff check .` grün.
- `uv run pytest -x -q` grün.
- `flake8`, `isort`, `black` tauchen nirgends als Dependency, Konfiguration
  oder Kommando auf (E3).
- Keine neuen Laufzeit-Dependencies.
- `adw/events.py`, `adw/snapshots.py`, `adw/gui/**` unverändert (neue
  Event-Typen nur über die bestehende Emitter-API).
- Limits, Circuit-Breaker, Review-Loop-Policy, Phasenreihenfolge und
  Resume-Logik unverändert; Codex-Review-Semantik unverändert.
- Alle Akzeptanzkriterien A1–A5, B1–B6, C1–C5, D1–D3 durch Tests abgedeckt
  (TDD: pro Fix ein RED-Test zuerst). Richtwert 16–24 Tests (nicht bindend).
- Abschluss-Abgleich gegen `.adw/contract.yaml`: CLI-Ausgänge und
  State-Wirkung der vier Fehlerpfade, `codex.timeout`, exakt begrenzte
  Selbstheilung, einmaliger Synthese-Retry, frühe Session-Persistenz.

## Deferred (bewusst nicht gebaut) — unverändert aus der Spec übernommen

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

Ein Review-Finding, das einen dieser Punkte oder einen vorentschiedenen Punkt
(E1–E5) fordert, wird mit dieser Begründung abgewiesen und dokumentiert, nicht
umgesetzt — das Ventil bindet auch den Codex-Review-Loop.
