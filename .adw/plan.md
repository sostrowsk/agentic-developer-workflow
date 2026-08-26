# Implementierungsplan: Breakpoints — konfigurierbare Haltepunkte als verallgemeinerte Approval

Single-Lane-Projekt (`backend`). Alles — Config-Schlüssel, State-Feld,
Orchestrator-Gate, CLI-Verdrahtung, Events, read-only GUI-Anzeige, Doku, Tests —
läuft in der einen Lane `backend`. Es gibt keine getrennte Frontend-Lane.

Gebaut wird strikt gegen `.adw/contract.yaml`: extern zugesagt sind nur die
beobachtbaren Flächen — der Config-Schlüssel `breakpoints` (erlaubte Werte,
Default, Validierungsfehler), das Pausenverhalten (State-Phase
`awaiting_approval`, Prozess-Exit-Code 2, Fortsetzung per `adw approve`), das
State-Feld `pending_breakpoint`, das `approval`-Event samt `gate`/`event` sowie
die Wirkung von `--no-approval`. **Keine internen Helper-Signaturen, keine
Schrittfolgen, keine Dictionary-Schlüssel** stehen im Contract. Der Verzicht auf
ein neues `Phase`-Literal (E3b), auf einen zweiten Pausen-Mechanismus (E3) und
das Unangetastet-Lassen von Diff-/Snapshot-/Retention-Pfad sind bewusst
**Plan-/DoD-Ebene** — sie gelten unverändert und werden hier durchgesetzt.

## Ist-Stand (im Code verankert, verifiziert)
- **Config** ist streng validiert: `AdwConfig` (`adw/config.py:110`) und alle
  Untermodelle tragen `model_config = ConfigDict(extra="forbid")`; YAML wird über
  `_StrictLoader` gelesen. Ein Validierungsfehler wird in `AdwConfig.load` als
  `ConfigError` gehoben (`adw/config.py:143`) — der Lauf startet dann nicht.
  Enumerierte Werte laufen bereits über `Literal` (z. B. `LaneName`,
  `CiConfig.provider`).
- **`Phase`** (`adw/state.py:20`) ist ein geschlossenes `Literal` mit genau elf
  Werten; `RunState` (`adw/state.py:82`) trägt `extra="forbid"` und pinnt
  `skip_approval`, `spec_approval`, `approval_granted`, `spec_approval_granted`
  als überlebende Boolean-Felder. Ein fehlendes neues Feld lädt via Default
  problemlos aus Alt-States.
- **`run_spec_and_plan`** (`adw/phases.py:331`) behandelt `awaiting_approval`
  (`:349`) als **Plan-Gate**: bei `approval_granted`/`skip` → `build`, sonst
  `raise AwaitingApproval`. Da `approval_granted` an einem Breakpoint immer
  bereits `True` ist (Plan wurde früher freigegeben), würde ein `resume` ohne
  Gegenmaßnahme hier fälschlich nach `build` übergehen — siehe B6. Es ist die
  **erste** Phasenfunktion, die `_execute` aufruft.
- **`run_build_phase`** (`adw/phases.py:1293`) setzt nach Abschluss aller
  Build-Lanes `ctx.state.phase = "integration" if parallel else "codex_review"`
  und speichert (`:1307`). Eigener Guard `if phase != "build": return`.
- **`run_final_review_phase`** (`adw/phases.py:2328`) setzt am Ende
  `ctx.state.phase = "ci"` (`:2437`). **`run_ci_phase`** (`adw/phases.py:2447`)
  beginnt die erste CI-Arbeit — im Parallel-Modus `_integration_loop` (Merge +
  E2E-Vorbereitung), dann `_push_branch` und Forge-Polling.
- **`AwaitingApproval`** (`adw/phases.py:186`) ist die vorhandene Pausen-Exception.
- **`_execute`** (`adw/cli.py:708`) ruft die Phasen der Reihe nach auf und fängt
  `AwaitingApproval` (`:717`): berechnet `gate` = `spec`/`plan`, emittiert
  `approval`/`awaited`, gibt eine Hinweiszeile aus und wirft `typer.Exit(2)`
  (`EXIT_AWAITING_APPROVAL`).
- **`approve`** (`adw/cli.py:447`) verlangt Phase in
  (`awaiting_approval`,`awaiting_spec_approval`) — sonst sauberer Fehler `_fail`
  (`:456`). Setzt `approval_granted` bzw. `spec_approval_granted`, emittiert
  `approval`/`granted` mit `gate` (`:480`) und ruft `_execute`.
- **Event-Format** (verifiziert): `emitter.emit("approval", {"gate": <name>,
  "event": "awaited"|"granted"}, span=...)`.
- **GUI** (verifiziert): `_awaiting_gate_phase` (`adw/gui/app.py:426`)
  normalisiert Gate-Namen auf `("spec", "plan")` — unbekannte Gates werden zu
  `None` (`app.py:441`). Der Run-Status `awaiting_approval` selbst ist
  gate-agnostisch (`_latest_approval_event`, `app.py:480`), und die
  Recovery-Karte arbeitet state-phasen-basiert (`_APPROVAL_PHASES`,
  `app.py:1527`) — Status und Handlungshinweis funktionieren für Breakpoints
  also unverändert; nur die Gate-Zuordnung braucht eine minimale Erweiterung
  (B8).

## Workstream: backend

### B1 — Config-Schlüssel `breakpoints` (AC1, AC2, AC3, E2, E5)
In `adw/config.py` ein enumeriertes `Literal` der zwei erlaubten Haltepunkte
(`before_integration`, `before_push`) einführen und `AdwConfig` um ein
optionales Listenfeld ergänzen (Default: leere Liste). Dadurch:
- **AC1:** `breakpoints:` akzeptiert eine Liste; einzige zulässige Elemente sind
  die zwei Zeichenketten (über `Literal` erzwungen). Kein freies Schema, keine
  Aliase, keine Bedingungen (E2).
- **AC2:** Fehlt der Schlüssel oder ist die Liste leer, ist das Feld leer —
  keine zusätzlichen Haltepunkte, heutiges Verhalten unverändert.
- **AC3/E5:** Ein unbekannter Wert (`after_round:2`, Tippfehler), ein falscher
  Typ (Mapping statt Liste, Boolean als Element) ist ein `ValidationError` und
  wird von `AdwConfig.load` als `ConfigError` mit klarer Meldung gehoben — der
  Lauf startet nicht, keine stille Ignorierung. **Kein neuer**
  Validierungs-Mechanismus: nur ein weiteres strikt getyptes Feld im
  bestehenden pydantic-Modell.
- Reihenfolge oder Mehrfachnennung in der Liste ist kein Fehler und führt nicht
  zu mehrfachen Halten: die Haltepunkte wirken als aktivierte **Menge** (folgt
  ohnehin aus dem Fortschalt-Mechanismus in B7).

### B2 — State-Feld `pending_breakpoint` (AC6, E3b)
`RunState` (`adw/state.py`) um **ein** optionales Feld ergänzen, das den
wartenden Haltepunkt hält: Wert `before_integration`, `before_push` oder `None`
(Default). Es referenziert dasselbe enumerierte `Literal` wie B1.
- Das `Phase`-`Literal` wird **NICHT** erweitert (E3b): `PHASES`, Phasenleiste,
  `_TERMINAL_PHASES`/Retention und die Recovery-Karte bleiben unangetastet.
- `extra="forbid"` bleibt; Alt-States ohne das Feld laden über den Default.
  Das Feld wird wie `skip_approval` im State persistiert und überlebt damit
  Crash + `resume` + `approve`.
- **Kein** zusätzliches „Freigabe-Nachweis"-Feld: die Idempotenz (AC8) folgt
  daraus, dass `approve` die Phase über die Grenze hinaus fortschaltet (B7) —
  ein freigegebener Haltepunkt wird danach schlicht nie wieder erreicht.

### B3 — Breakpoint-Gate an der Phasengrenze (AC4, AC5, AC6, AC14, E3)
Einen **internen** Orchestrator-Helfer in `adw/phases.py` anlegen (Signatur
intern, **nicht** im Contract), der an einer Phasengrenze über den bestehenden
Approval-Pfad pausiert — kein neuer Mechanismus (E3):
- Ist der übergebene Haltepunktname **nicht** in `ctx.config.breakpoints`:
  nichts tun, zurückkehren (kein Halt, kein Event).
- Ist `ctx.skip_approval` **oder** `ctx.state.skip_approval` gesetzt: nichts tun,
  zurückkehren — der Haltepunkt wird übersprungen (AC14) und es entsteht **kein**
  fingiertes `awaited`/`granted` (AC12).
- Sonst: `ctx.state.pending_breakpoint` auf den Namen setzen, `ctx.state.phase`
  auf `awaiting_approval` setzen, EINMAL (atomar) speichern und
  `AwaitingApproval` werfen. Die persistierte Phase ist beim Halt
  `awaiting_approval` (AC6); welcher Haltepunkt wartet, steht ausschließlich in
  `pending_breakpoint`.
- Der Helfer emittiert **selbst kein Event**: State-Save (`state.json`) und
  Event-Append (`events.jsonl`) sind zwei getrennte Schreibvorgänge und nicht
  gemeinsam atomar. Das `awaited`-Event entsteht deshalb **log-geprüft an genau
  einer Stelle** — im `AwaitingApproval`-Zweig von `_execute` (B8): emittiert
  wird nur, wenn `events.jsonl` noch KEIN `approval`/`awaited` mit diesem
  `gate` enthält. Da jeder Haltepunkt je Lauf höchstens einmal hält (AC8/B7),
  ist der Gate-Name als Dedup-Schlüssel eindeutig. Ergebnis: genau EIN
  logisches `awaited` je tatsächlich eingetretenem Warten — auch wenn der
  Prozess zwischen State-Save und Log-Write abstürzt (Nachholung beim nächsten
  `resume`), und ohne Duplikat bei wiederholtem `resume` (AC12).

### B4 — `before_integration` an der Grenze Build → Integration/Review (AC4)
In `run_build_phase` nach Abschluss **aller** Build-Lanes und **vor** dem
Fortschalten der Phase das Gate für `before_integration` aufrufen. Die Grenze
ist über beobachtbare Arbeit definiert: alle Build-Lanes haben ihre Gates grün
bestanden, und es hat weder Integrations-/Merge-Arbeit noch nachfolgende
Review-Arbeit begonnen. Danach die Phase wie heute auf `integration` (Parallel)
bzw. `codex_review` (Single-Lane) setzen und speichern.
- **Single-Lane** (keine eigene Merge-Arbeit): Halt nach Abschluss der
  Build-Lane, bevor `codex_review`-Arbeit beginnt.
- Resume mitten im Build (noch nicht freigegeben): fertige Lanes werden wie
  gehabt übersprungen, das Gate pausiert erneut (Wartezustand bleibt, AC9).

### B5 — `before_push` an der Grenze Final-Review → CI (AC5)
In `run_final_review_phase` nach Abschluss des finalen Reviews und **vor** dem
Fortschalten der Phase auf `ci` das Gate für `before_push` aufrufen — die
Grenze wird also **vor** dem Eintritt in `run_ci_phase` durchgesetzt, nicht erst
innerhalb des bereits begonnenen CI-Ablaufs. Pausiert der Lauf hier, hat
**JEGLICHE** CI-Phasen-Arbeit noch nicht begonnen: kein Push, keine
CI-Vorbereitung (einschließlich der Integrations-/E2E-Vorbereitung, die
`run_ci_phase` im Parallel-Modus zu Beginn über `_integration_loop` ausführt),
kein Forge-/CI-Polling. Beim Halt ist nichts gepusht. Das gilt für Single-Lane-
**wie** Parallel-Modus und wird für beide Pfade getestet, soweit sie den
Haltepunkt erreichen.

### B6 — Resume bleibt am ungefreigegebenen Haltepunkt wartend (AC9, E6)
`run_spec_and_plan` ist die erste Funktion in `_execute` und deutet
`awaiting_approval` heute als Plan-Gate — mit `approval_granted == True` (an
einem Breakpoint immer der Fall) würde `resume` dort fälschlich nach `build`
übergehen und den Build wiederholen. Deshalb **vor** dieser Deutung abfangen:
ist `phase == "awaiting_approval"` **und** `pending_breakpoint` gesetzt, sofort
`AwaitingApproval` werfen — **nicht** die Plan-Gate-Logik ausführen und **nicht**
nach `build` übergehen. Damit führt `resume` an einem noch nicht freigegebenen
Haltepunkt keine Arbeit hinter dem Haltepunkt aus; der Lauf bleibt im
Wartezustand wie an den bestehenden Approval-Gates.
- Diese Wiederentdeckung eines **bereits persistierten** Wartens läuft in
  denselben `AwaitingApproval`-Zweig wie der initiale Eintritt; ob dabei noch
  ein `awaited`-Event zu schreiben ist, entscheidet ausschließlich die
  log-geprüfte Emission in B8: im Normalfall steht das Event bereits im Log und
  es wird nichts angehängt; nach einem Crash zwischen State-Save und Log-Write
  wird es genau einmal nachgeholt. Wiederholte `resume`-Aufrufe am
  ungefreigegebenen Haltepunkt enden jeweils mit Exit-Code 2 und Hinweiszeile,
  hängen aber keine Duplikat-Events an (AC12).

### B7 — `approve` gibt den Haltepunkt frei und setzt fort (AC7, AC8, AC10, AC11)
Im `approve`-Kommando (`adw/cli.py`) den bestehenden Phasen-Guard beibehalten
(Phase muss `awaiting_approval`/`awaiting_spec_approval` sein, sonst sauberer
Fehler ohne Zustandsänderung — AC10). Ist `pending_breakpoint` gesetzt:
- Die Fortsetzungsphase aus dem Haltepunkt ableiten: `before_integration` →
  `integration` (falls `parallel`) sonst `codex_review`; `before_push` → `ci`.
- `phase` auf diese Fortsetzungsphase setzen **und** `pending_breakpoint` leeren
  (im selben atomaren Save), `approval`/`granted` mit `gate` = Haltepunktname
  emittieren, dann `_execute` — der Lauf läuft ab genau diesem Punkt weiter,
  ohne bereits abgeschlossene Phasen zu wiederholen (AC7).
- **Idempotenz (AC8):** Weil die Phase über die Grenze hinaus fortgeschaltet ist,
  wird der freigegebene Haltepunkt nie erneut erreicht — ein späterer
  Crash + `resume` findet die Phase bereits jenseits der Grenze, und die
  Guards von `run_build_phase`/`run_final_review_phase` kehren zurück, ohne das
  Gate erneut zu berühren. `resume` nach erteilter Freigabe läuft durch.
- **AC11:** Sind beide Haltepunkte aktiv, läuft der Lauf nach der Freigabe von
  `before_integration` bis `before_push` weiter und hält dort erneut mit Exit 2;
  nach dessen Freigabe durch. Kein Haltepunkt hält doppelt.
- Ein inkonsistenter Wartezustand wird nicht durch Vermutung fortgesetzt,
  sondern schlägt klar fehl.
- Ist `pending_breakpoint` **None**, bleibt der bestehende Spec-/Plan-Approval-
  Pfad unverändert (Regressions-Pin für AC2); `approval_granted` und
  `spec_approval_granted` behalten ihre heutige Bedeutung.

### B8 — `approval`-Event, CLI-Hinweis und minimale GUI-Gate-Zuordnung (AC12, AC13)
Im `AwaitingApproval`-Zweig von `_execute` (`adw/cli.py:717`) den Fall
`pending_breakpoint` gesetzt unterscheiden: dann ist `gate` der Haltepunktname
und die Emission ist **log-geprüft** — `approval`/`awaited` wird nur emittiert,
wenn `events.jsonl` des Laufs noch kein `approval`/`awaited` mit diesem `gate`
enthält (Dedup-Schlüssel Gate-Name, eindeutig, da jeder Haltepunkt höchstens
einmal hält). Damit ist der Übergang crash-sicher: stürzt der Prozess zwischen
dem State-Save (B3) und dem Log-Write ab, findet der nächste `resume` den
persistierten Wartezustand vor, landet in demselben Zweig und holt das Event
genau einmal nach; ein wiederholter `resume` bei bereits geloggtem Warten hängt
nichts an (AC12). Fehlt ein Event-Log (Trace deaktiviert), ist die Emission
ohnehin folgenlos — Duplikate können nicht entstehen. Der Zweig gibt zusätzlich
die klare Hinweiszeile mit `adw approve <run_id> --repo <pfad>` aus, die den
wartenden Haltepunkt nennt, und wirft Exit 2. Ist `pending_breakpoint` **None**,
bleibt der Zweig unverändert (heutiges `spec`/`plan`-Verhalten inklusive
Emission — Regressions-Pin AC2).
- Das Event-Format ist identisch zu den Gates `spec`/`plan`
  (`{"gate", "event"}`); GUI und Timeline stellen den Haltepunkt damit ohne
  Sonderfall dar (AC12).
- **GUI, read-only, minimal:** Die Gate-Normalisierung in
  `_awaiting_gate_phase` (`adw/gui/app.py:441`, heute Whitelist
  `("spec", "plan")`) um die zwei Haltepunktnamen erweitern, damit die
  `awaited`/`granted`-Paarung je Gate korrekt bleibt; für die Phasenleiste den
  wartenden Haltepunkt auf die Fortsetzungsphase abbilden
  (`before_integration` → `integration` bzw. `codex_review`, `before_push` →
  `ci`); ohne verwertbaren Trace dient `pending_breakpoint` aus dem State als
  Fallback. Run-Status (`awaiting_approval`) und Recovery-Karte
  (`adw approve <run_id> --repo <pfad>`) funktionieren bereits unverändert —
  **kein** neuer Schreibpfad, **keine** neue Route, **kein** neuer Phasenwert,
  `PHASES`/`_TERMINAL_PHASES`/Retention unangetastet (AC13, E1, E3b).

### B9 — Doku (DoD)
- `docs/SPEC.md` + `docs/SPEC.de.md`: Approval-/Kontrollfluss um die zwei
  konfigurierbaren Haltepunkte ergänzen (Config, `awaiting_approval` +
  `pending_breakpoint`, Exit 2, `adw approve`, Wirkung von `--no-approval`,
  unverändertes `Phase`-Modell).
- `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md`: **nur** dort, wo die Anzeige
  betroffen ist (zusätzliche zulässige `approval.gate`-Werte, wartender Lauf,
  bestehender read-only Recovery-Hinweis); keine GUI-Steuerung.
- Handbuch `docs/handbuch/ADW-USER-HANDBUCH*.md`: Konfiguration und
  Bedienablauf mit `adw approve`.
- `examples/config.yaml`: den `breakpoints:`-Block mit den zwei erlaubten Werten
  zeigen (auskommentiert/als Beispiel, Default bleibt leer).
- `CHANGELOG.md` + `CHANGELOG.de.md` Abschnitt `Unreleased`: neuer Eintrag.
- Bilinguale Konvention (EN + DE) einhalten.

### B10 — Tests unter tests/ (DoD, AC1…AC14)
Richtwert **~20 neue Tests** (Bestand: 978); deutlich über ~30 ist Scope-Drift.
Orchestrator-Pausen-/Fortsetzungsverhalten wie bisher gegen den Mock-Runner
(Muster: `tests/test_e2e_dry_run.py`). Mindestens abzudecken:
- **AC1/AC3/E5:** beide gültigen Werte werden akzeptiert; ein unbekannter Wert,
  ein Tippfehler und ein falscher Container-/Elementtyp ergeben je einen
  `ConfigError`, der Lauf startet nicht. Fehlender Schlüssel und leere Liste
  sind gültig.
- **AC2:** ohne `breakpoints:` unverändertes Verhalten; die bestehenden Spec-/
  Plan-Approvals behalten Phasen, Exit-Codes, CLI-Fortsetzung und ihre
  `approval`-Events mit `gate` = `spec` bzw. `plan` (Regressionsnachweis).
- **AC4:** `before_integration` pausiert genau einmal an der Grenze; Single-Lane
  (Halt vor `codex_review`) und — soweit er den Haltepunkt erreicht — der
  Parallel-Pfad; Phase beim Halt `awaiting_approval`.
- **AC5:** `before_push` pausiert genau einmal nach dem finalen Review; beim Halt
  ist nichts gepusht, keine CI-Vorbereitung/keine Parallel-E2E-Vorbereitung/kein
  Polling gelaufen; Single-Lane **und** Parallel.
- **AC6:** Halt nutzt `awaiting_approval`, Exit-Code 2, `pending_breakpoint` trägt
  den Haltepunktwert; kein neues `Phase`-Literal.
- **AC7:** `adw approve` setzt ab genau dem Haltepunkt fort, ohne abgeschlossene
  Phasen zu wiederholen, und emittiert das passende `granted`-Event.
- **AC8/E6:** freigegebener Haltepunkt hält kein zweites Mal — auch nach Crash +
  `resume`; `resume` nach Freigabe läuft durch.
- **AC9/E6:** `resume` am **nicht** freigegebenen Haltepunkt bleibt wartend, keine
  Arbeit dahinter (insbesondere kein Rückfall in die Plan-Gate-Deutung, B6).
- **AC9+AC12:** wiederholte `resume`-Aufrufe am ungefreigegebenen Haltepunkt
  enden jeweils mit Exit-Code 2, erzeugen aber **kein** Duplikat: im Event-Log
  steht genau EIN `awaited` für diesen Haltepunkt.
- **AC12 (Crash-Fenster):** simulierte Unterbrechung zwischen State-Save und
  Event-Emission (Wartezustand persistiert, `events.jsonl` ohne das `awaited`);
  anschließendes `resume` holt das Event nach — danach steht genau EIN
  `awaited` für diesen Haltepunkt im Log, und der Lauf wartet weiterhin.
- **AC10/E6:** `adw approve` auf einen nicht wartenden Lauf ist ein sauberer
  Fehler ohne Zustandsänderung.
- **AC11:** beide aktiv → nacheinander je ein Halt (Exit 2), dann durch; kein
  doppelter Halt.
- **AC12:** je Haltepunkt `approval`-Event `awaited` beim Warten und `granted` bei
  der Freigabe mit `gate` = Haltepunktname; unter `--no-approval` **kein**
  fingiertes Event.
- **AC13:** GUI-Auswertung zeigt den wartenden Lauf als `awaiting_approval`,
  ordnet das Haltepunkt-Gate korrekt zu und liefert weiterhin den read-only
  Approve-Recovery-Hinweis.
- **AC14:** `--no-approval` (`skip_approval`, auch über `--gates`) hält an keinem
  Breakpoint; das Überspringen überlebt Crash + `resume`.

## Gates / Definition of Done
- `uv run ruff check .` und `uv run pytest -x -q` grün.
- AC1…AC14 durch Tests unter `tests/` abgedeckt.
- Der bestehende Bestand (978 Tests) bleibt grün; das heutige Verhalten ohne
  `breakpoints:` ist unverändert (AC2).
- Das `Phase`-`Literal` in `adw/state.py` ist **unverändert** (E3b); es entsteht
  **kein** zweiter Pausen-Mechanismus (E3); `PHASES`, `_TERMINAL_PHASES`,
  Retention sowie Diff- und Snapshot-Pfad bleiben unangetastet.
- Keine neue GUI-Schreibfläche, keine neue CLI-Subcommand-Familie, keine neue
  Dependency.
- Doku aktualisiert: `docs/SPEC.md`/`docs/SPEC.de.md`,
  `docs/GUI-SPEC.md`/`docs/GUI-SPEC.de.md` (nur die betroffene Anzeige),
  `docs/handbuch/ADW-USER-HANDBUCH*.md`, `examples/config.yaml`,
  `CHANGELOG.md`/`CHANGELOG.de.md` (`Unreleased`).

## Deferred (bewusst nicht gebaut)
Die folgenden Ideen sind nachvollziehbar, aber für diese Anforderung
unverhältnismäßig. Sie gehören NICHT in die Akzeptanzkriterien und werden auch
im Codex-/Fix-Zyklus nicht nachgebaut:

- **Bedingte Haltepunkte** (Ausdruckssprache, Bedingungen, `after_round:N` o. Ä.).
- **Haltepunkte je Lane oder je Runde** sowie Haltepunkte innerhalb einer Lane.
- **Freigabe aus der GUI** / jeglicher Schreibpfad aus dem Browser.
- **Step-Into in den Review-Loop**, Einzelschritt-Modus, „ab Knoten weiterlaufen",
  Rücksprung, Abbruch-Kommando.
- **Laufzeit-Änderung von Breakpoints** (Umkonfigurieren eines laufenden Runs) —
  einschließlich eines eigenen Mechanismus, der die Breakpoint-Konfiguration gegen
  spätere Config-Edits im Run-State pinnt.
