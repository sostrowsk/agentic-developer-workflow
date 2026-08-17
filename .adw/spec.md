# Spec — Ein sprechender Schalter `--gates` für die Freigabe-Gates

## Goal

`adw run` bekommt einen einzigen, sprechenden Schalter `--gates <modus>` mit
vier Werten (`none` | `spec` | `plan` | `both`), der die heute schon
existierende 4-Wege-Gate-Matrix auffindbar und eindeutig bedienbar macht.
Dies ist eine reine Bedienoberfläche über bestehende Funktion: die
Gate-Mechanik selbst (Phasenreihenfolge, Exit-Codes, `adw approve`,
`adw resume`, Wiederaufnahme-Semantik) ändert sich nicht. Bestehende Aufrufe
mit `--no-approval` und `--spec-approval` bleiben unverändert lauffähig.

## Scope

- Neue CLI-Option `--gates <modus>` an `adw run` mit exakt vier zulässigen
  Werten, abgebildet auf die vier bestehenden Matrix-Zeilen:

  | `--gates` | Spec-Gate | Plan-Gate | äquivalenter Altflag-Aufruf |
  |-----------|-----------|-----------|------------------------------|
  | `none`    | nein      | nein      | `--no-approval`              |
  | `spec`    | STOPP     | nein      | `--no-approval --spec-approval` |
  | `plan`    | nein      | STOPP     | (keine Flags — **Default**)  |
  | `both`    | STOPP     | STOPP     | `--spec-approval`            |

- Default `plan`, wenn weder `--gates` noch ein Altflag angegeben wird —
  identisch zum heutigen `adw run` ohne Flags.
- Die Altflags `--no-approval` und `--spec-approval` bleiben gültig, mit
  unveränderter Bedeutung; die drei Alias-Äquivalenzen der Tabelle gelten.
- Widerspruchsregel bei Kombination von `--gates` mit Altflags: Sobald
  mindestens ein Altflag angegeben ist, wird die VOLLSTÄNDIGE
  Altflag-Kombination (nicht gesetzte Altflags zählen als nicht gesetzt)
  gemäß der Tabelle oben zu genau einem Modus aufgelöst. Die Angabe ist nur
  zulässig, wenn dieser aufgelöste Modus dem expliziten `--gates`-Wert
  entspricht; andernfalls Ablehnung VOR dem Start (Nichtnull-Exit, klare
  Meldung, kein persistenter Run). `--gates` ohne Altflags ist immer
  widerspruchsfrei.
- Ein ungültiger `--gates`-Wert wird VOR dem Start abgelehnt; die Meldung
  nennt die vier zulässigen Werte; es entsteht kein Run.
- Beim Start eines gültigen Runs gibt die CLI den wirksamen Gate-Modus aus —
  auch bei Altflag-Aufrufen und beim impliziten Default.
- `adw run --help` erklärt die Matrix (vier Modi, Default, Haltepunkte,
  weiterhin gültige Altflags) statt zwei unkommentierter Booleans.
- Bestandsschutz: Run-States im heutigen Format (`skip_approval`,
  `spec_approval`) bleiben ohne Migration resumier- und approvebar.

## Non-Goals

- E1: Kein Default-Wechsel. `adw run` ohne Flags stoppt weiterhin vor dem
  Build und NICHT nach der Spec.
- E2: Die Altflags werden weder entfernt noch als deprecated markiert,
  solange sie widerspruchsfrei benutzt werden. Kein Bruch bestehender
  Aufrufe.
- E3: Die Gate-Mechanik bleibt unangetastet — Phasenreihenfolge, Exit-Codes
  (0 = done, 2 = awaiting_approval, 1 = Eskalation/Fehler), `adw approve`,
  `adw resume` und die Wiederaufnahme-Semantik ändern sich nicht.
- E4: Ob `--gates` intern auf die beiden bestehenden State-Felder abgebildet
  wird oder ein eigenes Feld bekommt, ist Umsetzungsentscheidung — mit der
  harten Bedingung, dass bestehende `state.json`-Dateien weiter
  funktionieren (AC7). Die Spec legt das nicht fest.
- Kein `gates`-Key in `.adw/config.yaml`.
- Keine weiteren Gates jenseits von Spec und Plan; keine zusätzlichen
  Haltepunkte (Push, Merge, Integration).
- Keine Änderung an `adw/events.py`, `adw/snapshots.py`, `adw/gui/**`.
- Keine interaktive Rückfrage und keine Bestätigungsabfrage.
- Keine Änderung an Limits, Circuit-Breaker oder Review-Loop-Policy.
- Kein Gate-Wechsel mitten im Lauf (insbesondere kein `adw resume --gates`).
- Keine Benachrichtigung beim Erreichen eines Gates.
- E6: Keine neuen Laufzeit-Dependencies.

## Acceptance Criteria

- **AC1 (A1):** Die vier `--gates`-Werte erzeugen genau die vier
  Matrix-Zeilen — je ein Test über die CLI, der prüft, wo der Lauf anhält:
  `none` hält an keinem Gate; `spec` hält nach der Spec und vor dem Plan im
  bestehenden Spec-Approval-Zustand, nach der Spec-Freigabe kein Halt am
  Plan-Gate; `plan` hält ohne Spec-Halt vor dem Build; `both` hält zuerst
  nach der Spec und nach deren Freigabe erneut vor dem Build.
- **AC2 (A2):** Bei `--gates none` erreicht ein fehlerfreier Lauf `done`
  (Exit-Code 0), ohne an einem Gate zu stoppen. Eine Eskalation hält ihn
  weiterhin an (Exit-Code 1) — sie ist der einzige Halt in diesem Modus.
- **AC3 (A3):** `adw run` OHNE jedes Flag ist beobachtbar identisch zu
  `--gates plan` und damit identisch zum heutigen Verhalten: kein Spec-Halt,
  ein Plan-Halt vor dem Build.
- **AC4 (A4):** Die drei Alias-Äquivalenzen sind je durch einen Test belegt:
  `--no-approval` == `--gates none`, `--spec-approval` == `--gates both`,
  `--no-approval --spec-approval` == `--gates spec`. Erreichte Gate-Zustände
  und weiterer Laufverlauf stimmen mit dem jeweiligen `--gates`-Modus
  überein; für bestehende Altflag-Aufrufe ändert sich nichts.
- **AC5 (A5):** Widerspruch ist über die Auflösungsregel definiert: Wird
  `--gates` mit mindestens einem Altflag kombiniert, wird die vollständige
  Altflag-Kombination zu einem Modus aufgelöst (`--no-approval` → `none`,
  `--spec-approval` → `both`, beide → `spec`); stimmt dieser Modus nicht mit
  dem `--gates`-Wert überein, wird die Angabe mit Nichtnull-Exit und klarer
  Meldung abgelehnt, BEVOR ein Run angelegt oder persistenter Run-State
  geschrieben wird — unabhängig von der Reihenfolge der Optionen, ohne
  Vorrangregel und ohne "das letzte gewinnt". Akzeptiert werden genau die
  übereinstimmenden Kombinationen, insbesondere `--gates none --no-approval`,
  `--gates spec --no-approval --spec-approval`, `--gates both --spec-approval`.
  Abgelehnt werden damit auch die mehrdeutig wirkenden Teilkombinationen,
  belegt durch repräsentative Tests: `--gates spec --spec-approval` (Altflags
  lösen zu `both` auf), `--gates none --spec-approval`,
  `--gates both --no-approval` sowie `--gates plan` mit jedem Altflag (keine
  Altflag-Kombination löst zu `plan` auf).
- **AC6 (A6):** Jeder andere Wert als `none`, `spec`, `plan`, `both` wird
  mit Nichtnull-Exit abgelehnt; die Meldung nennt alle vier zulässigen
  Werte; es wird kein Run angelegt und kein persistenter State geschrieben.
- **AC7 (A7 — Bestandsschutz):** Ein Run-State im heutigen Format
  (`skip_approval`, `spec_approval`) wird ohne Migration oder manuelle
  Änderung geladen und bleibt mit `adw resume` und `adw approve`
  fortsetzbar; sein Gate-Verhalten entspricht weiterhin den gespeicherten
  Boolean-Werten. Regressionstest mit einem State im heutigen Format.
- **AC8 (A8):** Beim Start jedes gültigen neuen Runs wird der wirksame
  Gate-Modus als einer der Werte `none`, `spec`, `plan`, `both` ausgegeben —
  bei `--gates`, bei Altflag-Aufrufen und beim impliziten Default.
- **AC9 (Aufgabe 6):** `adw run --help` beschreibt `--gates` mit seinen
  vier Werten, `plan` als Default, je Modus die Haltepunkte (Spec-/Plan-Gate)
  und die weiterhin gültige Bedeutung der beiden Altflags.

## Definition of Done

- Alle Acceptance Criteria sind durch automatisierte Tests über die CLI
  belegt (Richtwert: ca. 12–18 neue Tests), inklusive des
  Bestandsschutz-Regressionstests (AC7).
- Die bestehende Testsuite bleibt ohne Regression grün.
- `uv run ruff check .` und `uv run pytest -x -q` sind grün.
- `flake8`, `isort` und `black` tauchen weder als Dependency noch als
  Konfiguration oder Kommando auf (E5).
- Keine neuen Laufzeit-Dependencies; keine Änderungen außerhalb des Scopes
  (insbesondere `adw/events.py`, `adw/snapshots.py`, `adw/gui/**`
  unverändert).

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
