# Plan — Ein sprechender Schalter `--gates` für die Freigabe-Gates

Dieser Plan implementiert `.adw/spec.md` und baut strikt gegen
`.adw/contract.yaml`. Bei Konflikten gilt die Spec, insbesondere die
Scope-Tabelle (vier Modi ↔ vier Altflag-Kombinationen) und AC1–AC9; die
vorentschiedenen Punkte **E1–E6** und die Scope-Deckel des Issues stechen
jede ältere Gewohnheit.

**Single-Lane.** Es gibt genau einen Workstream, **backend** — die CLI-/
Orchestrator-Schicht. Eine Frontend-Lane existiert nicht. Der gesamte Lauf
ist eine **sprechende Bedienoberfläche über bereits funktionierender
Mechanik**: `adw run` bekommt einen Schalter `--gates none|spec|plan|both`,
der auf die bestehende 4-Wege-Gate-Matrix abbildet (heute aufgespannt durch
die beiden unabhängigen Booleans `--no-approval` und `--spec-approval`).
Die Gate-Mechanik selbst — Phasenreihenfolge, Exit-Codes, `adw approve`,
`adw resume`, Wiederaufnahme-Semantik — **ändert sich nicht** (E3).

## Die Abbildung (aus der Spec, die eine Quelle der Wahrheit)

| `--gates` | Spec-Gate | Plan-Gate | äquivalenter Altflag-Aufruf        |
|-----------|-----------|-----------|-------------------------------------|
| `none`    | nein      | nein      | `--no-approval`                     |
| `spec`    | STOPP     | nein      | `--no-approval --spec-approval`     |
| `plan`    | nein      | STOPP     | (keine Flags — **Default**)         |
| `both`    | STOPP     | STOPP     | `--spec-approval`                   |

Altflag-Auflösung für die Widerspruchsregel (AC5): die **vollständige**
Altflag-Kombination löst zu genau einem Modus auf — `--no-approval` →
`none`, `--spec-approval` → `both`, beide → `spec`, keins → `plan`. Keine
Altflag-Kombination löst zu `plan` auf; `plan` ist nur über `--gates plan`
oder gar kein Flag erreichbar.

## Guardrails

- **Nur CLI/Orchestrator.** Änderungen betreffen die Optionsfläche von
  `adw run` und den Vorprüf-/Ausgabepfad vor der Run-Anlage (`adw/cli.py`,
  `run`-Kommando um `cli.py:247`–`:319`) sowie — nur falls die gewählte
  Abbildung es erfordert — die Gate-Auflösung in `adw/phases.py:337`–`:344`.
  Die Gate-**Mechanik** — Phasenreihenfolge, Exit-Codes (0 = done,
  2 = awaiting_approval, 1 = Eskalation/Fehler), `adw approve`, `adw resume`,
  Wiederaufnahme-Semantik — bleibt exakt wie sie ist (E3). **Tabu und
  unverändert** (Scope-Deckel): `adw/events.py`, `adw/snapshots.py`,
  `adw/gui/**`. Ein dort gefundener Bug ist ein **Finding im Report, kein
  Diff.**
- **Altflags bleiben gültig und äquivalent** (E2). `--no-approval` und
  `--spec-approval` werden weder entfernt noch als deprecated markiert,
  solange sie widerspruchsfrei benutzt werden; bestehende Skripte und
  Gewohnheiten laufen unverändert. Die drei Alias-Äquivalenzen (AC4) und
  die heute beobachtbaren Verhaltensweisen bleiben erhalten.
- **Default bleibt `plan`** (E1). `adw run` ohne Flags stoppt weiterhin vor
  dem Build und NICHT nach der Spec — beobachtbar identisch zu
  `--gates plan` und zu heute (AC3).
- **Bestandsschutz ist harte Bedingung** (AC7/E4). Run-States im heutigen
  Format (`skip_approval`-/`spec_approval`-Booleans) laden ohne Migration
  oder manuelle Änderung und bleiben mit `adw resume` / `adw approve`
  fortsetzbar; ihr Gate-Verhalten folgt weiterhin den gespeicherten
  Booleans. Das bindet jede gewählte interne Abbildung.
- **Ablehnung vor jeder Persistenz.** Ein ungültiger `--gates`-Wert (AC6)
  und ein Widerspruch `--gates`↔Altflag (AC5) werden mit **Nichtnull-Exit**
  und **klarer Meldung** abgelehnt, **bevor** `RunState.new`,
  Run-Verzeichnis, Event-Log oder anderer persistenter Run-State entstehen —
  reihenfolgeunabhängig, keine Vorrangregel, kein „das letzte gewinnt".
- **Keine neue Laufzeit-Dependency** (E6); keine neue Konfigurationsfläche —
  insbesondere **kein `gates`-Key in `.adw/config.yaml`** (Scope-Deckel /
  Deferred). Keine interaktive Rückfrage, keine Bestätigungsabfrage. Kein
  Gate-Wechsel mitten im Lauf (kein `adw resume --gates`). Keine Änderung an
  Limits, Circuit-Breaker oder Review-Loop-Policy.
- Reale Gates (E5): `uv run ruff check .` und `uv run pytest -x -q`.
  `flake8`, `isort`, `black` tauchen nirgends auf — nicht als Dependency,
  Konfiguration, Skript oder Kommando.

## Ausgangspunkt (im Code verifiziert)

- **Die Matrix existiert bereits.** `adw run` (`cli.py:247`) stellt die
  beiden Booleans `--no-approval` (`no_approval`, `cli.py:261`) und
  `--spec-approval` (`spec_approval`, `cli.py:264`) bereit. Beim Start
  werden sie in den State gepinnt: `state.spec_approval = spec_approval`
  (`cli.py:301`), durchgereicht via
  `_build_context(..., skip_approval=no_approval, spec_approval=spec_approval)`
  (`cli.py:314`). `run_spec_and_plan` löst die wirksamen Gates als
  `skip = ctx.skip_approval or ctx.state.skip_approval` und
  `spec_approval = ctx.spec_approval or ctx.state.spec_approval` auf
  (`phases.py:343`–`:344`) und pinnt sie beim ersten Kontakt in den State
  (`phases.py:337`–`:342`). Diese Mechanik treibt der Schalter an; sie wird
  nicht neu entworfen.
- **Die Start-Ausgabe existiert bereits.** `run` druckt
  `Run {run_id} gestartet (Phase: {phase})` (`cli.py:318`); AC8 verlangt den
  **wirksamen Gate-Modus** (`none`/`spec`/`plan`/`both`) beim Start jedes
  gültigen neuen Runs — das ist der natürliche Ort dafür.
- **Die Vorprüf-Reihenfolge ist etabliert.** `run` validiert bereits „genau
  eine Issue-Quelle", lädt/validiert die Config und führt
  `_preflight_worktree` aus, **bevor** `RunState.new` und das erste
  `state.save` laufen (`cli.py:274`–`:313`). Wertprüfung und
  Widerspruchsprüfung für `--gates` gehören in genau dieses Fenster vor
  `RunState.new`, sodass eine Ablehnung keinen persistenten Run hinterlässt
  (AC5/AC6) — im Stil des bestehenden `_fail(...)`-Pfads.
- **19 Alt-Run-States liegen in `.adw/runs/`.** Sie tragen
  `skip_approval`/`spec_approval`-Booleans und sind das
  AC7-Regressionsziel; eine State-Fixture im heutigen Format muss
  unverändert resumier- und approvebar bleiben.

## Workstream: backend

### 1. `--gates`-Option ergänzen und vor jeder Run-Anlage auflösen

- Ergänze `adw run` um `--gates <modus>` mit den exakt vier zulässigen
  Werten `none | spec | plan | both`, **Default `plan`** (E1/AC3). Die
  Option steht neben den weiterhin vorhandenen `--no-approval` /
  `--spec-approval` (E2).
- Löse im Fenster vor `RunState.new` (neben der bestehenden Issue-Quellen-/
  Config-/Worktree-Vorprüfung, `cli.py:274`–`:296`) den **wirksamen Modus**
  aus `--gates` und den beiden Altflags auf:
  - **AC6 — ungültiger Wert.** Jeder andere `--gates`-Wert als die vier
    zulässigen wird mit Nichtnull-Exit und einer **klaren Meldung, die alle
    vier zulässigen Werte nennt**, abgelehnt, bevor ein Run angelegt oder
    persistenter State geschrieben wird. Bestehenden `_fail(...)`-Pfad
    wiederverwenden.
  - **AC5 — Widerspruch.** Ist `--gates` zusammen mit **mindestens einem**
    Altflag angegeben, löse die **vollständige** Altflag-Kombination zu
    einem Modus auf (`--no-approval` → `none`, `--spec-approval` → `both`,
    beide → `spec`; nicht gesetzte Altflags zählen als nicht gesetzt).
    Akzeptiere **nur**, wenn dieser aufgelöste Modus dem expliziten
    `--gates`-Wert entspricht; sonst Ablehnung mit Nichtnull-Exit und
    klarer Meldung, **bevor** Run/State geschrieben wird —
    reihenfolgeunabhängig, ohne Vorrang, ohne „das letzte gewinnt".
    Explizit akzeptiert: `--gates none --no-approval`,
    `--gates spec --no-approval --spec-approval`,
    `--gates both --spec-approval`. Explizit abgelehnt (repräsentativ):
    `--gates spec --spec-approval` (Altflags lösen zu `both` auf),
    `--gates none --spec-approval`, `--gates both --no-approval` sowie
    `--gates plan` mit **jedem** Altflag (keine Altflag-Kombination löst zu
    `plan` auf). `--gates` **ohne** Altflags ist immer widerspruchsfrei.
  - Der aufgelöste wirksame Modus ist genau ein Wert aus
    `{none, spec, plan, both}` und wird sowohl für die Start-Ausgabe
    (Schritt 3) als auch zum Treiben der Gates (Schritt 2) verwendet.
- **Weder `--gates` noch Altflag → wirksamer Modus `plan`** (E1/AC3),
  beobachtbar identisch zu heute.

### 2. Die beiden bestehenden Gate-Entscheidungen aus dem Modus treiben (E4, gebunden durch AC7)

- Bilde den wirksamen Modus auf die beiden Gate-Entscheidungen ab, die die
  Mechanik bereits konsumiert, sodass jeder Modus exakt seine Matrix-Zeile
  erzeugt (AC1). In der Semantik der bestehenden Booleans:

  | wirksamer Modus | Spec-Gate | Plan-Gate | bestehende Boolean-Bedeutung |
  |-----------------|-----------|-----------|------------------------------|
  | `none`          | nein      | nein      | `skip_approval=true`, `spec_approval=false` |
  | `spec`          | ja        | nein      | `skip_approval=true`, `spec_approval=true`  |
  | `plan`          | nein      | ja        | `skip_approval=false`, `spec_approval=false` |
  | `both`          | ja        | ja        | `skip_approval=false`, `spec_approval=true`  |

- **Ob** dafür die beiden bestehenden State-Felder wiederverwendet werden
  oder ein eigenes Feld hinzukommt, ist Umsetzungsentscheidung (E4). Die
  **harte Bedingung** ist AC7: bestehende `state.json`-Dateien im heutigen
  Format laden und verhalten sich unverändert. Die einfachste Abbildung —
  dieselben zwei Booleans ableiten, die die Auflösung heute schon pinnt
  (`phases.py:337`–`:344`) — hält AC7 automatisch ein; ein neues Feld muss
  bei Abwesenheit auf die gespeicherten Booleans zurückfallen. Pinne den
  wirksamen Modus beim Run-Start so, wie `--spec-approval` heute gepinnt
  wird (`cli.py:301`), damit ein späteres `adw resume` / `adw approve` —
  das die CLI-Flags nie sieht — dieselben Gates rekonstruiert.
- Die Gate-**Mechanik** (Phasenreihenfolge, Exit-Codes, Approval-Zustände,
  `adw approve`, `adw resume`) bleibt unangetastet (E3): nur die beiden
  Boolean-Eingänge werden jetzt aus dem Modus gespeist. `adw resume` und
  `adw approve` bekommen weder neue Optionen noch geänderte Hilfetexte;
  dort wird kein `--gates` eingeführt.

### 3. Wirksamen Gate-Modus beim Start ausgeben (AC8)

- Gib beim Start jedes gültigen neuen Runs den wirksamen Modus als einen
  der Werte `none`/`spec`/`plan`/`both` aus — bei `--gates`-Aufrufen, bei
  reinen Altflag-Aufrufen, bei zulässigen Mischformen und beim impliziten
  Default — als Erweiterung oder Begleitung der bestehenden Ausgabe
  `Run {run_id} gestartet (Phase: {phase})` (`cli.py:318`). Der genaue
  Wortlaut ist nicht gepinnt; der Kontrakt verlangt nur, dass der wirksame
  Modus beim Start beobachtbar ist.

### 4. `adw run --help` aktualisieren (AC9)

- Ersetze die zwei unkommentierten Booleans im Hilfetext durch eine
  Beschreibung der Matrix: die vier `--gates`-Werte, **`plan` als Default**,
  die Haltepunkte je Modus (Spec-Gate / Plan-Gate) und die weiterhin
  gültige Bedeutung der beiden Altflags. Keine Verhaltensänderung — nur
  Hilfetext.

### 5. Tests über die CLI (AC1–AC9; ca. 12–18 neue Tests)

Alle Acceptance Criteria werden durch automatisierte Tests **über die CLI**
belegt (Aufruf von `run`/`resume`/`approve` wie in den bestehenden
CLI-Tests) mit Assertions darauf, wo ein Lauf stoppt, welchen Exit-Code er
hat und ob ein persistenter Run entstanden ist. Keine Tests jenseits der
Acceptance Criteria.

- **AC1 — die vier Zeilen.** Je ein Test pro `--gates`-Wert über die CLI,
  der prüft, wo der Lauf anhält: `none` hält an keinem Gate; `spec` hält
  nach der Spec und vor dem Plan im bestehenden Spec-Approval-Zustand und
  hält nach der Spec-Freigabe **nicht** am Plan-Gate; `plan` hält ohne
  Spec-Halt vor dem Build; `both` hält zuerst nach der Spec und nach deren
  Freigabe erneut vor dem Build.
- **AC2 — `--gates none` erreicht `done`.** Ein fehlerfreier Lauf erreicht
  `done` (Exit 0), ohne an einem Gate zu stoppen; eine Eskalation hält ihn
  weiterhin an (Exit 1) — der einzige Halt in diesem Modus.
- **AC3 — Default == `plan`.** `adw run` ohne jedes Flag ist beobachtbar
  identisch zu `--gates plan`: kein Spec-Halt, ein Plan-Halt vor dem Build.
- **AC4 — die drei Alias-Äquivalenzen**, je ein Test: `--no-approval` ==
  `--gates none`, `--spec-approval` == `--gates both`,
  `--no-approval --spec-approval` == `--gates spec`. Erreichte
  Gate-Zustände und weiterer Laufverlauf stimmen mit dem jeweiligen
  `--gates`-Modus überein; Altflag-Aufrufe verhalten sich unverändert.
- **AC5 — Widerspruch.** Repräsentative abgelehnte Kombinationen
  (`--gates spec --spec-approval`, `--gates none --spec-approval`,
  `--gates both --no-approval` sowie `--gates plan` mit jedem Altflag)
  enden mit Nichtnull-Exit und klarer Meldung und erzeugen **keinen**
  persistenten Run-State; die akzeptierten redundanten Kombinationen
  (`--gates none --no-approval`, `--gates spec --no-approval
  --spec-approval`, `--gates both --spec-approval`) laufen an.
  Reihenfolgeunabhängigkeit für mindestens ein Paar belegen.
- **AC6 — ungültiger Wert.** Ein `--gates`-Wert außerhalb der vier wird mit
  Nichtnull-Exit abgelehnt; die Meldung nennt alle vier zulässigen Werte;
  es entsteht kein Run und kein persistenter State.
- **AC7 — Bestandsschutz (Regression).** Eine Run-State-Fixture im heutigen
  Format (`skip_approval`, `spec_approval`) lädt ohne Migration und bleibt
  mit `adw resume` resumierbar und mit `adw approve` approvebar; ihr
  Gate-Verhalten folgt weiterhin den gespeicherten Booleans.
- **AC8 — Modus-Ausgabe beim Start.** Für einen `--gates`-Aufruf, einen
  Altflag-Aufruf und den impliziten Default wird der wirksame Modus
  (`none`/`spec`/`plan`/`both`) beim Run-Start ausgegeben.
- **AC9 — `--help`.** Die Ausgabe von `adw run --help` beschreibt `--gates`
  mit seinen vier Werten, `plan` als Default, die Haltepunkte je Modus und
  die weiterhin gültige Bedeutung der beiden Altflags.

### 6. Verifikation und Übergabe

- Führe `uv run ruff check .` und `uv run pytest -x -q` aus (E5); die
  bestehende Suite bleibt ohne Regression grün.
- Prüfe den Diff: `adw/events.py`, `adw/snapshots.py`, `adw/gui/**`
  unverändert; Gate-Mechanik (Phasenreihenfolge, Exit-Codes,
  `adw approve`/`adw resume`) unverändert; Altflags weiterhin gültig und
  äquivalent; kein `gates`-Key in `.adw/config.yaml`; keine neue
  Laufzeit-Dependency; `flake8`/`isort`/`black` nirgends vorhanden.

## Definition of Done

1. AC1–AC9 sind durch automatisierte Tests **über die CLI** abgedeckt,
   einschließlich des AC7-Bestandsschutz-Regressionstests — ungefähr
   **12–18 neue Tests**.
2. Die bestehende Testsuite bleibt ohne Regression grün.
3. `uv run ruff check .` und `uv run pytest -x -q` sind grün.
4. `flake8`, `isort`, `black` tauchen weder als Dependency noch als
   Konfiguration, Kommando oder Prüfschritt auf (E5).
5. Keine neue Laufzeit-Dependency und keine Änderung außerhalb des Scopes —
   insbesondere `adw/events.py`, `adw/snapshots.py`, `adw/gui/**`
   unverändert (E6 / Scope-Deckel).

## Deferred (bewusst nicht gebaut)

Diese Punkte sind defensibel, aber in diesem Lauf außerhalb des Scopes. Ein
Review-Finding, das einen davon oder einen vorentschiedenen Punkt (E1–E6)
fordert, wird mit Begründung abgewiesen, nicht umgesetzt.

- Ein Default für `--gates` in `.adw/config.yaml` (pro Repo
  vorkonfigurierbar).
- Entfernen oder Deprecaten der Altflags.
- Weitere Haltepunkte (Stopp vor dem Push, vor dem Merge, nach der
  Integration) oder ein frei konfigurierbarer Gate-Satz.
- Ein Gate-Wechsel mitten im Lauf, etwa über `adw resume --gates ...`.
- Benachrichtigung beim Erreichen eines Gates.
