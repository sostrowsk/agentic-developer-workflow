# Spec — Breakpoints: konfigurierbare Haltepunkte als verallgemeinerte Approval

## Goal (Ziel)
ADW hält heute nur an zwei fest verdrahteten Stellen der Authoring-Phase an
(nach Spec, nach Plan). Die teuren, schwer umkehrbaren Schritte — Integration/Merge
und Push/CI — laufen ohne Halt durch. Dieses Vorhaben macht genau zwei zusätzliche
Haltepunkte per Config aktivierbar (`before_integration`, `before_push`), die den
Lauf an denselben, bereits vorhandenen Approval-Pfad übergeben: Pause mit
Exit-Code 2, Fortsetzung über `adw approve <run_id> --repo <pfad>`, idempotent über
Crash und `resume` hinweg. Kein neuer Pausen-Mechanismus, kein neuer Phasenwert,
keine GUI-Steuerung. Default: das heutige Verhalten bleibt unverändert.

## Scope (Umfang)
- Neuer optionaler Config-Schlüssel `breakpoints:` in `.adw/config.yaml` als Liste
  aus einer festen Menge zulässiger Werte.
- Genau zwei Haltepunkte: `before_integration` (nach Abschluss aller Build-Lanes,
  bevor Integrations-/Merge-Arbeit oder nachfolgende Review-Arbeit beginnt) und
  `before_push` (nach dem finalen Review, bevor jegliche CI-Phasen-Arbeit
  einschließlich Push beginnt).
- Wiederverwendung des bestehenden Approval-Pausenpfads: State-Phase
  `awaiting_approval`, Exit-Code 2, `approval_granted`-Semantik, Fortsetzung mit
  `adw approve <run_id> --repo <pfad>`.
- Neues State-Feld (z. B. `pending_breakpoint`), das festhält, welcher Haltepunkt
  wartet — statt eines neuen `Phase`-Literals.
- `approval`-Event für Anzeige/Timeline, mit dem Haltepunktnamen als `gate`.
- Wirkung von `--no-approval` (`skip_approval`) auf die Haltepunkte.
- Doku (SPEC, GUI-SPEC soweit die Anzeige betroffen ist, Handbuch,
  `examples/config.yaml`, CHANGELOG).

## Non-Goals (Nicht-Ziele)
- Kein Debugger, kein Step-/Einzelschritt-Modus, kein „ab Knoten weiterlaufen",
  kein Rücksprung, kein Abbruch-Kommando (E4). Fortsetzen heißt immer: weiter ab
  dem Haltepunkt.
- Kein freies Schema, keine Ausdruckssprache, keine Bedingungen, keine Haltepunkte
  innerhalb einer Lane, keine Haltepunkte je Lane oder je Runde (E2).
- Keine Laufzeit-Änderung von Breakpoints eines laufenden Runs, keine neue
  CLI-Subcommand-Familie.
- Keine GUI-Steuerung: die GUI bleibt read-only, kein Schreibpfad aus dem Browser,
  kein Non-Goal aus GUI-SPEC §2 wird aufgehoben (E1).
- Kein neuer Pausen-Mechanismus: der bestehende Approval-Pfad wird verallgemeinert,
  nicht dupliziert (E3).
- Das `Phase`-Literal in `adw/state.py` wird NICHT erweitert (E3b). `PHASES`,
  Phasenleiste, Retention (`_TERMINAL_PHASES`) und die Recovery-Karte bleiben
  unverändert.
- Der Diff-, Snapshot- und Retention-Pfad bleibt unangetastet.

## Acceptance Criteria (Akzeptanzkriterien)

### Konfiguration
- **AC1** — `.adw/config.yaml` akzeptiert einen optionalen Schlüssel `breakpoints:`
  als Liste. Zulässige Elemente sind ausschließlich die Zeichenketten
  `before_integration` und `before_push`. (A1, E2)
- **AC2** — Fehlt `breakpoints:` oder ist die Liste leer, verhält sich ADW exakt wie
  heute: es entstehen keine zusätzlichen Haltepunkte. Die bestehenden Spec- und
  Plan-Approvals behalten ihre Phasen, Exit-Codes, CLI-Fortsetzung und ihre
  `approval`-Events mit den Gates `spec` bzw. `plan`. (A1)
- **AC3** — Ein unbekannter Wert in `breakpoints:` (z. B. `after_round:2`,
  Tippfehler, falscher Typ) ist ein Config-Fehler mit klarer Meldung, konsistent mit
  der übrigen strengen Validierung; der Lauf startet nicht. Keine stille
  Ignorierung. (A1, E5)

### Pausenverhalten
- **AC4** — Ist `before_integration` aktiv, pausiert der Lauf genau einmal an der
  Grenze, die sich über die beobachtbare Arbeit definiert: alle Build-Lanes haben
  ihre Gates grün bestanden, und es hat noch keine Integrations-/Merge-Arbeit und
  keine nachfolgende Review-Arbeit begonnen. Die persistierte State-Phase ist beim
  Halt `awaiting_approval` (gemäß AC6). Im Single-Lane-Betrieb (in dem keine eigene
  Merge-Arbeit anfällt) heißt das: Halt nach Abschluss der Build-Lane, bevor
  `codex_review`-Arbeit beginnt. (A1, A2)
- **AC5** — Ist `before_push` aktiv, pausiert der Lauf genau einmal an der Grenze
  nach Abschluss des finalen Reviews und bevor JEGLICHE Arbeit der `ci`-Phase
  beginnt: kein Push, keine CI-Vorbereitung (einschließlich der Integrations-/
  E2E-Vorbereitung, die der Parallel-Modus zu Beginn der CI-Phase ausführt), kein
  Forge-/CI-Polling. Beim Halt ist noch nichts gepusht. Das gilt für Single-Lane-
  wie für Parallel-Modus-Orchestrierung und wird für beide Pfade getestet, soweit
  sie den Haltepunkt erreichen. (A1, A2)
- **AC6** — An einem aktiven Haltepunkt pausiert der Lauf über den bestehenden
  Approval-Pfad: State-Phase `awaiting_approval`, Prozess-Exit-Code 2, Fortsetzung
  mit `adw approve <run_id> --repo <pfad>`. Es wird KEIN neues `Phase`-Literal
  eingeführt; welcher Haltepunkt wartet, steht in einem eigenen State-Feld (z. B.
  `pending_breakpoint`) mit dem Wert `before_integration` bzw. `before_push`.
  (A2, E3, E3b)
- **AC7** — `adw approve <run_id>` auf einen an einem Haltepunkt wartenden Lauf gibt
  den Haltepunkt frei und setzt den Lauf ab genau diesem Punkt fort (keine
  Wiederholung bereits abgeschlossener Phasen). (A2)

### Idempotenz
- **AC8** — Ein bereits freigegebener Haltepunkt hält den Lauf kein zweites Mal an —
  auch nicht, wenn der Lauf danach abstürzt und per `adw resume` fortgesetzt wird.
  `resume` nach einer erteilten Freigabe läuft ohne erneuten Halt durch. (A2, E6)
- **AC9** — `adw resume` auf einen Lauf, der an einem noch NICHT freigegebenen
  Haltepunkt wartet, führt keine Arbeit hinter dem Haltepunkt aus: der Lauf bleibt
  im Wartezustand (wie bei den bestehenden Approval-Gates). (A2, E6)
- **AC10** — `adw approve` auf einen Lauf, der NICHT auf eine Freigabe wartet, ist
  ein sauberer Fehler mit klarer Meldung (wie heute für Nicht-Approval-Phasen),
  ohne den Lauf zu verändern. (E6)
- **AC11** — Sind beide Haltepunkte aktiv, hält der Lauf nacheinander an jedem
  einmal: nach dem Freigeben von `before_integration` läuft er bis `before_push`
  weiter und hält dort erneut mit Exit-Code 2; nach dessen Freigabe läuft er durch.
  Kein Haltepunkt hält doppelt. (A1, A2, E6)

### Ereignis-Log / Anzeige
- **AC12** — Jeder Haltepunkt wird als `approval`-Event geloggt: `gate` = Name des
  Haltepunkts (`before_integration` bzw. `before_push`), `event` = `awaited` beim
  Eintreten des Wartens und `event` = `granted` bei der Freigabe. Damit stellen GUI
  und Timeline den Haltepunkt ohne Sonderfall dar. Es werden nur tatsächlich
  eingetretene Zustände geloggt — kein fingiertes `awaited`/`granted` für
  Haltepunkte, an denen der Lauf nicht gewartet hat. (A3)
- **AC13** — Die GUI bildet den wartenden Lauf wie die bestehenden Approval-Gates ab
  (wartender Lauf sichtbar, Recovery-/Handlungshinweis `adw approve <run_id> --repo
  <pfad>`), ohne neuen Schreibpfad und ohne neuen Phasenwert. Die GUI bleibt
  read-only. (A3, E1, E3b)

### `--no-approval`
- **AC14** — `--no-approval` (`skip_approval`, auch wenn über einen `--gates`-Modus
  gesetzt) überspringt AUCH die Haltepunkte: ein Lauf mit diesem Schalter hält an
  keinem konfigurierten Breakpoint an. Es ist EIN Schalter für „keine menschliche
  Freigabe in diesem Lauf", nicht zwei getrennte. Das Überspringen überlebt
  Crash+Resume (wie das bestehende `skip_approval` im State gepinnt). (A4)

## Definition of Done
- Alle Akzeptanzkriterien sind durch Tests unter `tests/` abgedeckt; das
  Orchestrator-Pausen-/Fortsetzungsverhalten wird wie bisher gegen den Mock-Runner
  geprüft (Muster: `tests/test_e2e_dry_run.py`). Richtwert ~20 neue Tests, deutlich
  über ~30 wäre Scope-Drift.
- Beide Gates sind grün: `uv run ruff check .` und `uv run pytest -x -q`.
- Der bestehende Bestand (978 Tests) bleibt grün; das heutige Verhalten ohne
  `breakpoints:` ist unverändert (Regressionsnachweis für AC2).
- Doku aktualisiert: `docs/SPEC.md` + `docs/SPEC.de.md` (Approval-/Kontrollfluss),
  `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` nur dort, wo die Anzeige betroffen ist,
  das Handbuch (`docs/handbuch/ADW-USER-HANDBUCH*.md`), `examples/config.yaml`
  (zeigt den `breakpoints:`-Block mit den zwei erlaubten Werten) sowie
  `CHANGELOG.md` + `CHANGELOG.de.md` (Abschnitt `Unreleased`).

## Deferred (bewusst nicht gebaut)
Die folgenden Ideen sind nachvollziehbar, aber für diese Anforderung
unverhältnismäßig. Sie gehören NICHT in die Akzeptanzkriterien und werden auch im
Codex-/Fix-Zyklus nicht nachgebaut:

- **Bedingte Haltepunkte** (Ausdruckssprache, Bedingungen, `after_round:N` o. Ä.).
- **Haltepunkte je Lane oder je Runde** sowie Haltepunkte innerhalb einer Lane.
- **Freigabe aus der GUI** / jeglicher Schreibpfad aus dem Browser.
- **Step-Into in den Review-Loop**, Einzelschritt-Modus, „ab Knoten weiterlaufen",
  Rücksprung, Abbruch-Kommando.
- **Laufzeit-Änderung von Breakpoints** (Umkonfigurieren eines laufenden Runs) —
  einschließlich eines eigenen Mechanismus, der die Breakpoint-Konfiguration gegen
  spätere Config-Edits im Run-State pinnt.
