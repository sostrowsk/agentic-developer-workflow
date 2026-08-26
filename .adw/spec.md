# Spec: Eskalation als Recovery-Karte am verursachenden Knoten

## Ziel (Goal)

Braucht ein Lauf menschliches Eingreifen, führt das Run-Detail den Nutzer von
der bloßen Zustandsanzeige zum konkreten nächsten Schritt: eine Recovery-Karte
nennt genau EIN passendes nächstes Kommando als kopierbaren, shell-sicheren
Text — mit echtem Repo-Pfad aus der Registry und echter `run_id`. Im
Eskalationsfall ist die Karte am `escalation`-Knoten verankert und zeigt
zusätzlich Grund, betroffene Phase und die unmittelbar vorausgehenden
Abbruch-Ereignisse (`limit.hit`/`circuit_breaker`). Damit entfällt das
Zusammensuchen von Kommando und Repo-Pfad aus dem Handbuch. Die Karte ist eine
rein abgeleitete Projektion des bereits geladenen Zustands (`state.phase`, die
bestehende Status-Ableitung des Run-Spans, Event-Strom, aufgelöste `RepoRef`);
kein neues Event, kein neuer Reader, keine neue Route, keine Persistenz. Der
ausführliche Bericht bleibt `escalation.md` im bestehenden Artifacts-Tab. Die
GUI bleibt strikt read-only: Kommandos werden ANGEZEIGT, niemals ausgeführt.

Lebenszyklus-Grundlage (im Code geprüft): `escalate()` setzt `state.phase`
final auf `escalated` und emittiert erst dann das `escalation`-Event mit der
Ursprungs-Phase. Ein Lauf mit `escalation`-Event ist also IMMER endgültig
eskaliert; Approval-Pausen (`state.phase` = `awaiting_*`) und
Abbrüche/Crashes (Arbeitsphase bleibt erhalten, z. B. `AgentRunError` in
`adw/cli.py`) erzeugen KEIN `escalation`-Event. Die Karte triggert deshalb auf
den Handlungsbedarf des Laufs (`state.phase` + bestehende Status-Ableitung),
nicht auf das `escalation`-Event; das Event liefert im Eskalationsfall Anker
und Kontext.

## Scope

- Single-Lane: `backend`.
- Genau eine Recovery-Karte pro Run-Detail (E5), wenn der Lauf menschliches
  Eingreifen braucht:
  - endgültig eskaliert (`state.phase` = `escalated`) → verankert am Knoten
    des letzten `escalation`-Ereignisses, mit Grund (`reason`), betroffener
    Phase (`phase` aus dem Event = Ursprungs-Phase) und den zu dieser
    Eskalation gehörenden `limit.hit`/`circuit_breaker`-Ereignissen; KEIN
    Fortsetzungskommando, sondern der klare Hinweis, dass ein NEUER Lauf
    nötig ist. (A1, A2)
  - pausiert am Approval-Gate (`state.phase` = `awaiting_spec_approval` oder
    `awaiting_approval`) → Karte mit `adw approve`-Kommando. (A2)
  - abgebrochen/gecrasht (Arbeitsphase, aber laut bestehender
    Status-Ableitung nicht mehr `running`) → Karte mit `adw resume`-Kommando.
    (A2)
- Kommandotext als kopierbarer, POSIX-shell-sicherer Klartext mit echtem
  Repo-Pfad aus der Registry (`RepoRef.path`, `_resolve_repos`) und echter
  `run_id`. (A2)
- Verlinkung auf `escalation.md` im Artifacts-Tab statt Duplikat seines
  Inhalts (Eskalationsfall). (A3)
- Neue, rein abgeleitete Recovery-Felder in der Antwort von
  `GET /api/runs/{repo}/{run_id}` (Contract-Fläche): Kommando-Kind nach der
  Auswahlregel, fertiger Kommandotext (bzw. dessen Abwesenheit plus
  Neu-Lauf-Kennzeichen), im Eskalationsfall Grund, betroffene Phase, die
  zugehörigen `limit.hit`/`circuit_breaker`-Ereignisse und der Verweis auf
  `escalation.md`. (Contract)
- Labels beidsprachig in `adw/gui/i18n.py`; die Kommandozeile selbst wird
  nicht übersetzt. (A4)
- Doku: `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` (§7.2) sowie
  `CHANGELOG.md` + `CHANGELOG.de.md` (`Unreleased`).

## Non-Goals / Scope-Deckel

- Keine Ausführung, kein Subprozess, kein „Retry“-Button, kein Schreibpfad in
  State, Run-Artefakte oder Repo. Kommandos werden nur angezeigt. Die GUI
  bleibt read-only (GUI-SPEC §2, „Control from the GUI“). (E1)
- Keine Zwischenablage-Integration; „kopierbar“ bedeutet auswählbarer,
  vollständig sichtbarer Text.
- Der Repo-Pfad erscheint ausschließlich im Text der Karte, nie in einer URL;
  die Slug-Regel aus §7.4 bleibt unangetastet. (E2)
- Keine Heuristik über den Eskalationsgrund, keine Vorschlagsliste mehrerer
  Kommandos, keine Fehlerklassifikation, keine Ursachen- oder
  Behebungsvorschläge. (E3)
- Keine neue Prozess-/Liveness-Erkennung: ob ein Lauf noch läuft, entscheidet
  ausschließlich die bestehende Status-Ableitung des Run-Detail (offener
  Run-Span = `running`). Ein Crash, den diese Ableitung nicht erkennt, zeigt
  auch keine Karte — bewusst kein neuer Mechanismus.
- Keine Änderung an `escalate()` in `adw/phases.py`, am `escalation`-Event, an
  `limit.hit`/`circuit_breaker` oder am Format von `escalation.md`. (E4)
- Höchstens eine Karte pro Lauf; keine Historie mehrerer Eskalationen. (E5)
- Keine Cross-Run-Statistik über Eskalationsgründe, kein neuer persistenter
  Zustand, kein neues Event, kein neuer Reader, keine neue Route.
- Interne Helper-Signaturen sowie konkretes Markup/CSS sind nicht Teil des
  externen Vertrags.

## Akzeptanzkriterien (Acceptance Criteria)

1. Die Antwort von `GET /api/runs/{repo}/{run_id}` trägt genau dann genau
   eine Recovery-Struktur, wenn der Lauf menschliches Eingreifen braucht,
   bestimmt aus `state.phase` und der bestehenden Status-Ableitung:
   - `state.phase` = `escalated` → Kommando-Kind `none` (neuer Lauf nötig).
   - `state.phase` = `awaiting_spec_approval` oder `awaiting_approval` →
     Kommando-Kind `approve`.
   - `state.phase` ist eine Arbeitsphase (`spec`, `plan`, `build`,
     `integration`, `codex_review`, `final_review`, `ci`) UND der abgeleitete
     Run-Status ist nicht `running` → Kommando-Kind `resume`
     (abgebrochen/gecrasht).
   - `state.phase` = `done`, Arbeitsphase mit Status `running`, oder kein
     ladbarer State → keine Recovery-Struktur (kein leeres Objekt erzwungen).
   Jeder Zweig hat einen realen Erzeuger im heutigen Lebenszyklus:
   `escalate()` → `none`; Approval-Pause (Exit 2) → `approve`; transienter
   Agent-Abbruch (Arbeitsphase bleibt, Run-Span endet nicht `done`) bzw.
   Crash mit geschlossenem Span → `resume`. Der Rest der Antwort behält seine
   bisherige Semantik. (A2, E3; Review-P1)

2. Die Auswahl folgt ausschließlich der Regel aus AC 1, ausgewertet auf
   `state.phase` — NICHT auf dem `phase`-Feld des `escalation`-Events, das
   stets die Ursprungs-Phase trägt und nie `escalated` sein kann. Der
   Eskalationsgrund, `limit.hit` und `circuit_breaker` beeinflussen die
   Auswahl nicht; es gibt nie mehrere Kommandovorschläge. Für einen
   `escalated`-Lauf wird niemals `adw resume` oder `adw approve`
   vorgeschlagen — konsistent damit, dass `adw resume` einen eskalierten Lauf
   selbst verweigert und auf einen neuen Lauf verweist. (A2, E3)

3. Ergibt die Regel `approve` oder `resume`, enthält die Recovery-Struktur
   den fertigen, kopierbaren Kommandotext in der bestehenden CLI-Signatur —
   `adw approve <run_id> --repo <pfad>` bzw. `adw resume <run_id> --repo
   <pfad>` — mit der echten `run_id` des Laufs und dem echten, serverseitig
   aufgelösten Repo-Pfad aus der Registry (`RepoRef.path`), nicht dem Slug.
   Ergibt die Regel `none`, enthält die Struktur keinen Kommandotext, sondern
   das maschinenlesbare Kennzeichen, dass ein neuer Lauf nötig ist. (A2, E2)

4. Der Kommandotext ist POSIX-shell-sicher: `run_id` und Repo-Pfad werden
   deterministisch nach `shlex.quote`-Semantik dargestellt (Werte ohne
   Sonderzeichen unverändert; andernfalls einfach gequotet, eingebettete
   einfache Anführungszeichen als `'\''`). Beim Parsen durch eine
   POSIX-Shell ergibt der angezeigte Text exakt die intendierten Argumente —
   der Pfad bleibt EIN `--repo`-Argument, auch mit Leerzeichen, einfachen
   Anführungszeichen oder Shell-Metazeichen — und führt zu keinem
   zusätzlichen Kommando. Die Kommandozeile wird unabhängig von der
   GUI-Sprache nicht übersetzt. (A2, A4; Review-P2)

5. Im Eskalationsfall (Kind `none`) nennt die Recovery-Struktur den Grund
   (`reason`) und die betroffene Phase (`phase`) unverändert aus dem Payload
   des `escalation`-Ereignisses mit der größten `seq` (E5). Es wird nichts
   umgeschrieben, klassifiziert oder interpretiert. (A1, E3, E4)

6. Im Eskalationsfall führt die Recovery-Struktur die zu dieser Eskalation
   gehörenden `limit.hit`- und `circuit_breaker`-Ereignisse desselben Laufs
   auf: genau die mit `seq` kleiner als die des maßgeblichen
   `escalation`-Ereignisses und größer als die `seq` eines etwaigen
   vorherigen `escalation`-Ereignisses (die unmittelbar vorausgehenden). Ihre
   Payloads (`{limit, value, cap}` bzw. `{keys, scope}`) werden unverändert
   übernommen. Gibt es keine solchen Ereignisse, ist die Liste leer. (A1, E4)

7. Fehlende oder unerwartete Daten — ein eskalierter Lauf ohne Event-Log,
   fehlende oder untypische Payload-Felder von `escalation`, `limit.hit`
   oder `circuit_breaker` — verursachen weder einen 5xx-Fehler noch erfundene
   Ersatzwerte; die übrigen verwertbaren Recovery-Daten (insbesondere
   Kommando-Kind und Neu-Lauf-Hinweis) bleiben sichtbar. (A1; bestehender
   Event-Log als Backstop)

8. Der Repo-Pfad erscheint ausschließlich im Kommandotext der
   Recovery-Struktur bzw. -Karte. Keine bestehende oder neue URL/Route trägt
   den realen Pfad; die Slug-Regel aus §7.4 bleibt unverändert. (E2)

9. Im Eskalationsfall verweist die Recovery-Struktur auf `escalation.md` als
   Artefakt des Artifacts-Tabs (Verweis/Kennung, kein eingebetteter Inhalt);
   der Inhalt wird nicht dupliziert. Fehlt das Artefakt wider Erwarten,
   bleibt das bestehende „fehlend“-Verhalten des Artifacts-Tabs maßgeblich
   und das Run-Detail bleibt nutzbar. (A3, E4)

10. Das Run-Detail rendert genau eine Recovery-Karte: im Eskalationsfall
    verankert am Knoten des letzten `escalation`-Ereignisses, mit Grund,
    betroffener Phase, den zugehörigen Abbruch-Ereignissen, dem
    Neu-Lauf-Hinweis und dem Link auf `escalation.md`; im `approve`-/
    `resume`-Fall (es existiert kein `escalation`-Knoten) auf Run-Ebene des
    Detail-Panes, mit `state.phase` und dem Kommandotext. Ohne
    Recovery-Struktur erscheint keine Karte. Übrige Detail-Pane-Bereiche
    behalten ihr bisheriges Verhalten. (A1, A2, A3, E5)

11. Die GUI führt kein Kommando aus: die Recovery-Funktion löst keinen
    Subprozess aus und schreibt weder in State noch in Run-Artefakte noch in
    das Repo. Der Kommandotext ist reine Anzeige. Diese Zusage ist Teil der
    beobachtbaren Vertragsfläche. (E1)

12. Alle Labels der Recovery-Karte sind beidsprachig in `adw/gui/i18n.py`
    hinterlegt (EN/DE); die Kommandozeile selbst sowie Eventwerte, `run_id`
    und Repo-Pfad werden nicht übersetzt. (A4)

13. `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` dokumentieren in §7.2 die
    Recovery-Karte: die Trigger- und Auswahlregel aus AC 1/2 (inkl. der
    Lebenszyklus-Begründung, dass ein `escalation`-Event immer endgültige
    Eskalation bedeutet), die Shell-Quoting-Zusage aus AC 4, Verankerung und
    Felder des Eskalationsfalls, den read-only-Charakter (nur Anzeige,
    echter Pfad nur im Text) und den Verweis auf `escalation.md`.
    `CHANGELOG.md` und `CHANGELOG.de.md` führen die Änderung unter
    `Unreleased`. (Doku)

## Deferred (bewusst nicht gebaut)

Nachvollziehbar, aber für die Ausgangslage unverhältnismäßig. KEINE
Akzeptanzkriterien — und bindend auch für den Review-/Codex-/Fix-Zyklus: was
hier steht, wird dort nicht nachgebaut.

- Geführter Recovery-Assistent (Schritt-für-Schritt statt einzeiligem
  Kommando).
- Eskalationsgrund-Taxonomie / Klassifikation von Abbruchgründen.
- Verlinkung ähnlicher früherer Läufe oder Cross-Run-Statistik über
  Eskalationsgründe.
- Historie und Vergleich mehrerer Eskalationen desselben Laufs.
- Neue Prozess-/Liveness-Erkennung für Crash-Läufe jenseits der bestehenden
  Status-Ableitung (PID-Prüfung, Heartbeats, Timeouts).
- Ausführung/„Retry“ aus der GUI, Zwischenablage-Integration, Vorschlagsliste
  mehrerer Kommandos, automatische Validierung der angezeigten Kommandos.
- Zusätzliche Recovery-Persistenz, Recovery-Events oder serverseitig
  gespeicherte Handlungsempfehlungen.

## Definition of Done

- Alle Akzeptanzkriterien erfüllt und durch Tests unter `tests/` als
  `test_gui_*.py` abgedeckt; die Tests decken mindestens ab: alle vier
  Presence-Zweige aus AC 1 mit je einer real erzeugbaren Zustandslage
  (eskaliert → `none`; `awaiting_*` → `approve`; Arbeitsphase mit
  nicht-`running`-Status → `resume`; `done`/`running` → keine Struktur);
  unveränderte Übernahme von `reason` und `phase` aus dem letzten
  `escalation`-Event; Zuordnung der `limit.hit`/`circuit_breaker`-Ereignisse
  zur maßgeblichen Eskalation (inkl. Abgrenzung gegen eine frühere Eskalation
  und leerer Liste); Robustheit bei fehlendem Event-Log bzw. fehlenden
  Payload-Feldern (kein 5xx); exakter Kommandotext mit echter `run_id` und
  echtem Registry-Pfad; Shell-Quoting für Pfade mit Leerzeichen, einfachem
  Anführungszeichen und Shell-Metazeichen (parametrisierbar; der Text parst
  als EIN `--repo`-Argument ohne Zusatzkommando); kein realer Pfad in einer
  URL; Verweis auf `escalation.md` ohne Inhaltsduplikat; EN/DE-Labels bei
  unveränderter Kommandozeile. Clientseitiges Rendern der Karte wird, soweit
  erforderlich, mit dem vorhandenen Plain-Node-Harness
  `tests/gui_js_harness.js` + `tests/gui_js_harness.py` geprüft.
- Richtwert ~10 neue Tests (Bestand: 953); mehr als ~16 gilt als Scope-Drift
  (Quoting-Fälle als EIN parametrisierter Test zählen einfach).
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
- Keine Änderung an `escalate()`, am `escalation`-Event, an
  `limit.hit`/`circuit_breaker` oder am Format von `escalation.md` (E4);
  keine neue Route, kein neues Event, keine Persistenz, keine neue
  Liveness-Erkennung, keine Kommandoausführung.
- GUI führt kein Kommando aus (E1); Repo-Pfad nur im Kartentext, nie in einer
  URL (E2).
- Doku aktualisiert: `docs/GUI-SPEC.md`/`docs/GUI-SPEC.de.md` (§7.2) und
  `CHANGELOG.md`/`CHANGELOG.de.md` (`Unreleased`).
