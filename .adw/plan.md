# Plan: Eskalation als Recovery-Karte am verursachenden Knoten

Single-Lane-Projekt: Es gibt nur den Workstream **backend**, keinen separaten
Frontend-Lane. Die GUI ist eine FastAPI-+-Jinja-+-Vanilla-JS-App; Template- und
Client-Verhaltensanpassungen gehören deshalb zum Backend-Workstream. Sowohl die
JSON-Route `GET /api/runs/{repo}/{run_id}` als auch die HTML-Seite
`GET /runs/{repo}/{run_id}` konsumieren dasselbe `_run_detail(...)`-Dict — eine
einzige Ableitung speist beide Flächen.

Gebaut wird strikt gegen `.adw/contract.yaml`. Die Recovery-Karte ist eine rein
abgeleitete Projektion des bereits geladenen Zustands (`state.phase`, die
bestehende Status-Ableitung des Run-Spans, der Event-Strom, die aufgelöste
`RepoRef`): kein neuer Reader, keine neue Route, kein neues Event, keine
Persistenz, keine neue Dependency (`shlex` ist Standardbibliothek). Der Contract
pinnt nur die extern beobachtbare Fläche: das additive, abgeleitete
`recovery`-Objekt in der Antwort von `GET /api/runs/{repo}/{run_id}` (nur
vorhanden, wenn der Lauf menschliches Eingreifen braucht), seinen shell-sicheren
Kommandotext und das beobachtbare Render-/Read-only-Verhalten der Karte. Die
Events-Route wird NICHT angefasst (E1). Interne Helper-Signaturen, interne
Dictionary-Schlüssel und konkretes Markup/CSS sind nicht Teil des Contracts.

## Grounding (im Code verifiziert)

- `escalate()` (`adw/phases.py:243`) setzt `ctx.state.phase` final auf
  `escalated` (Z. 264) und emittiert ERST DANACH das `escalation`-Event mit
  `{"reason": reason, "phase": origin_phase}` (Z. 268–270), wobei `origin_phase`
  VOR dem Markieren erfasst wird (Z. 249). Ein Lauf mit `escalation`-Event ist
  also IMMER endgültig `escalated`; die Karte triggert auf `state.phase` +
  bestehende Status-Ableitung, nicht auf das Event (E4, AC 1/2). KEINE Änderung
  an dieser Funktion.
- `limit.hit`-Payload ist `{"limit", "value", "cap"}` (`phases.py:1392`),
  `circuit_breaker`-Payload ist `{"keys", "scope"}` (`phases.py:1445` u. a.).
  Unverändert übernommen (AC 6, E4).
- Die bestehende Status-Ableitung sitzt in `_summary(...)` (`adw/gui/app.py:458`):
  ein geschlossener Run-Span trägt seinen terminalen `status`
  (`done`/`escalated`/`awaiting_approval`), ein OFFENER Span ist `running` (bzw.
  `awaiting_approval` bei aktiver Approval-Pause), kein Span → `status = None`.
  `state.phase` liegt im selben Dict als `phase`. Genau diese Ableitung
  entscheidet über die Karte — KEINE neue Liveness-Erkennung (AC 1, Non-Goal).
- `state.phase` ist im Detail über `state` (aus `_load_state`) verfügbar; ist
  kein State ladbar, ist `phase` `None` → keine Karte (AC 1).
- `RepoRef` (`app.py:278`) trägt `slug`, `path`, `exists`; `_resolve_repos`
  (`app.py:287`) füllt `path` aus der Registry bzw. den `--repo`-Angaben. `ref`
  ist an `_run_detail(...)` (`app.py:1406`) bereits durchgereicht — der echte
  Registry-Pfad steht damit für den Kommandotext bereit (AC 3, E2).
- Die CLI-Signaturen sind `adw resume <run_id> --repo <path>` (`adw/cli.py:414`)
  und `adw approve <run_id> --repo <path>` (`adw/cli.py:447`); `resume`
  verweigert einen `escalated`-Lauf und verweist auf einen neuen Lauf
  (`cli.py:423`) — konsistent mit Kind `none` (AC 2). Signaturen im Contract
  fixiert.
- `escalation.md` ist bereits ein whitelisteter Top-Level-Artefaktname
  (`_ARTIFACT_TOP_LEVEL`, `app.py:1345`) im bestehenden Artifacts-Tab. Der
  Verweis nutzt genau diese Kennung — kein neues Artefakt-Handling (AC 9, E4).
- `_run_detail(...)` liefert bereits `run` (aus `_summary`) und `tree` (aus
  `_serialize`, `app.py:881`); jeder serialisierte Knoten trägt `seq` und
  `span_id`. Das `escalation`-Event trägt ebenfalls `seq` — der Anker
  `anchor_seq` ist damit ohne neue Ableitung verfügbar (AC 10). Das
  `recovery`-Objekt wird an genau dieses Dict gehängt.
- `_phase_bar` (`app.py:514`) liest bereits `escalation`-Events und deren
  `phase`-Payload; die Recovery-Ableitung nutzt dieselbe, bereits geladene
  Event-Liste. Kein zweiter Durchlauf über neue Daten.
- i18n-Katalog `adw/gui/i18n.py` trägt EN- (ab Z. 90) und DE-Block (ab Z. 175);
  Chrome-Labels laufen ausschließlich hierüber. Die HTML-Seite rendert Knoten
  serverseitig; `static/app.js` schaltet Tabs/Auswahl. Clientseitiges Verhalten
  ist über den Plain-Node-Harness (`tests/gui_js_harness.js` +
  `tests/gui_js_harness.py`) testbar.

## Workstream: backend

### B1 — Auswahlregel: Kind aus `state.phase` + Status-Ableitung (AC 1, 2; E3)

- Eine rein abgeleitete Funktion bestimmt aus `state.phase` und dem bereits in
  `_summary` berechneten Run-`status` das Kommando-Kind — ausschließlich nach der
  Regel aus AC 1, ausgewertet auf `state.phase`, NIE auf dem `phase`-Feld des
  `escalation`-Events:
  - `state.phase == escalated` → Kind `none` (neuer Lauf nötig).
  - `state.phase` in `{awaiting_spec_approval, awaiting_approval}` → Kind
    `approve`.
  - `state.phase` ist Arbeitsphase (`spec`, `plan`, `build`, `integration`,
    `codex_review`, `final_review`, `ci`) UND abgeleiteter `status` ist nicht
    `running` → Kind `resume`.
  - `state.phase == done`, Arbeitsphase mit `status == running`, oder kein
    ladbarer State → KEIN `recovery`-Objekt (Schlüssel fehlt; kein leeres Objekt
    erzwungen).
- Die Prüfreihenfolge hält `escalated` strikt vor allen anderen Fällen. Grund,
  `limit.hit` und `circuit_breaker` beeinflussen die Auswahl nicht; es gibt nie
  mehrere Kommandovorschläge (AC 2, E3). Für `escalated` wird niemals
  `resume`/`approve` vorgeschlagen (AC 2).

### B2 — Kommandotext, shell-sicher (AC 3, 4; E2)

- Für Kind `approve`/`resume`: `recovery.command` = der fertige Text in der
  bestehenden CLI-Signatur — `adw approve <run_id> --repo <pfad>` bzw.
  `adw resume <run_id> --repo <pfad>` — mit der echten `run_id` des Laufs und dem
  echten, serverseitig aufgelösten Registry-Pfad (`RepoRef.path`), NICHT dem Slug
  (AC 3, E2).
- `run_id` und Pfad werden über `shlex.quote` (Standardbibliothek) dargestellt:
  Werte ohne Sonderzeichen unverändert, andernfalls einfach gequotet, eingebettete
  einfache Anführungszeichen als `'\''`. Beim Parsen durch eine POSIX-Shell ergibt
  der Text exakt die intendierten Argumente — der Pfad bleibt EIN
  `--repo`-Argument, auch mit Leerzeichen, einfachem Anführungszeichen oder
  Shell-Metazeichen — und führt zu keinem Zusatzkommando (AC 4).
- Die Kommandozeile wird nie übersetzt (AC 4, AC 12). Kein Subprozess, kein
  Ausführungspfad (E1) — nur String-Erzeugung.
- Für Kind `none`: kein `command` (`null`), stattdessen `needs_new_run == true`
  als maschinenlesbares Neu-Lauf-Kennzeichen (AC 3).

### B3 — Eskalationskontext: Grund, Phase, Abbruch-Ereignisse, Artefakt (AC 5, 6, 9; E4)

- Nur im Fall `none`: das `escalation`-Event mit der GRÖSSTEN `seq` bestimmen
  (E5). Aus dessen Payload `reason` und `phase` UNVERÄNDERT übernehmen —
  `recovery.reason`/`recovery.phase`; nichts umschreiben, klassifizieren oder
  interpretieren (AC 5). `phase` ist die Ursprungs-Phase aus dem Event, nie
  `state.phase`.
- `recovery.aborts`: die `limit.hit`/`circuit_breaker`-Ereignisse desselben Laufs
  mit `seq` kleiner als die des maßgeblichen `escalation`-Events und größer als
  die `seq` eines etwaigen VORHERIGEN `escalation`-Events (die unmittelbar
  vorausgehenden), in Event-Reihenfolge. Payloads (`{limit, value, cap}` bzw.
  `{keys, scope}`) unverändert übernommen. Gibt es keine solchen Ereignisse, ist
  die Liste leer (AC 6).
- `recovery.escalation_artifact`: Verweis/Kennung auf `escalation.md` im
  bestehenden Artifacts-Tab (bestehende Whitelist-Kennung), KEIN eingebetteter
  Inhalt und keine Existenz-Zusage für die Datei (AC 9). Fehlt das Artefakt
  wider Erwarten, bleibt das bestehende „fehlend"-Verhalten des Artifacts-Tabs
  maßgeblich; das Run-Detail bleibt nutzbar.
- `recovery.anchor_seq`: die `seq` des maßgeblichen `escalation`-Events als Anker
  der Karte (AC 10); `null`, wenn ein eskalierter Lauf kein verwertbares
  `escalation`-Event trägt (fehlendes Event-Log, AC 7). Die Eskalationsfelder
  (`anchor_seq`, `reason`, `phase`, `aborts`, `escalation_artifact`) existieren
  NUR in der `none`-Variante; `approve`/`resume` tragen sie nicht (Verankerung
  auf Run-Ebene, kein `escalation`-Knoten). Der Contract erzwingt die
  Varianten-Formen strukturell (oneOf nach `kind`).
- KEINE Änderung an `escalate()`, am `escalation`-Event, an
  `limit.hit`/`circuit_breaker` oder am Format von `escalation.md` (E4).

### B4 — Robustheit bei fehlenden/untypischen Daten (AC 7)

- Ein eskalierter Lauf ohne Event-Log, fehlende oder untypische Payload-Felder
  von `escalation`, `limit.hit` oder `circuit_breaker` verursachen weder einen
  5xx-Fehler noch erfundene Ersatzwerte: `reason`/`phase` fehlen dann als `null`,
  `anchor_seq` ist `null`, `aborts` bleibt leer/unvollständig, aber die
  verwertbaren Recovery-Daten — insbesondere `kind` und `needs_new_run` —
  bleiben sichtbar (AC 7). Der bestehende Event-Log ist Backstop.

### B5 — `recovery`-Objekt in `_run_detail` einhängen (Contract, AC 1, 8; E2)

- Das aus B1–B4 abgeleitete `recovery`-Objekt an das von `_run_detail(...)`
  zurückgegebene Dict hängen — GENAU DANN, wenn der Lauf menschliches Eingreifen
  braucht (Kind bestimmt); andernfalls fehlt der Schlüssel (kein leeres Objekt,
  AC 1). Die Ableitung nutzt ausschließlich `state`, das bereits berechnete
  `run`-Summary und die bereits geladene Event-Liste — keine Modelländerung, kein
  neuer Reader (E-Grounding).
- Der reale Repo-Pfad erscheint ausschließlich in `recovery.command`, nie in
  einer URL/Route; die Slug-Regel aus §7.4 bleibt unverändert (AC 8, E2).
- Übrige Antwortfelder (`run`, `phases`, `tree`, `latest_context`, `problems`,
  `raw`) behalten Form und Semantik unverändert.

### B6 — Recovery-Karte im Run-Detail rendern (AC 10, 11, 12; E1, E5)

- Das Run-Detail rendert GENAU EINE Recovery-Karte, geführt vom
  `recovery`-Objekt:
  - Fall `none`: verankert am Knoten des maßgeblichen `escalation`-Events
    (`anchor_seq`), mit Grund, betroffener Phase, den zugehörigen
    Abbruch-Ereignissen, dem Neu-Lauf-Hinweis und dem Link auf `escalation.md`.
    Fehlt trotz eskaliertem State ein verankerbarer `escalation`-Knoten
    (`anchor_seq == null`), erscheint die weiterhin verwertbare Karte auf
    Run-Ebene — sie wird wegen unvollständiger Logs weder ausgeblendet noch
    dupliziert (AC 7, E5).
  - Fall `approve`/`resume`: auf Run-Ebene des Detail-Panes (kein
    `escalation`-Knoten), mit `state.phase` und dem Kommandotext.
  - Ohne `recovery`-Objekt: keine Karte. Übrige Detail-Pane-Bereiche behalten ihr
    Verhalten (AC 10).
- Die GUI führt kein Kommando aus: kein Subprozess, kein Schreibpfad in State,
  Run-Artefakte oder Repo; der Kommandotext ist reine Anzeige (AC 11, E1). „Kopierbar"
  = auswählbarer, vollständig sichtbarer Text; keine Zwischenablage-Integration
  (Non-Goal).
- Alle Kartenlabels beidsprachig über den bestehenden EN/DE-i18n-Katalog
  (`adw/gui/i18n.py`), als identische Key-Menge in beiden Sprachblöcken; kein
  zweiter i18n-Mechanismus. Kommandozeile, Eventwerte, `run_id` und Repo-Pfad
  werden nicht übersetzt (AC 12). Client-Verdrahtung über den bestehenden
  Auswahl-/Tab-/Render-Mechanismus in `static/app.js`; keine SSE-Erweiterung,
  keine clientseitige Neu-Ableitung. Konkretes Markup/CSS bleibt
  Implementierungsdetail.

### B7 — Dokumentation und Changelog (AC 13)

- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron in §7.2: die Trigger- und
  Auswahlregel aus AC 1/2 (inkl. Lebenszyklus-Begründung, dass ein
  `escalation`-Event immer endgültige Eskalation bedeutet), die Shell-Quoting-Zusage
  aus AC 4, Verankerung und Felder des Eskalationsfalls, der read-only-Charakter
  (nur Anzeige, echter Pfad nur im Text) und der Verweis auf `escalation.md`.
- `CHANGELOG.md` und `CHANGELOG.de.md` synchron unter `Unreleased`.

## Tests (unter `tests/` als `test_gui_*.py`)

Richtwert ~10 neue Tests (Bestand: 953); mehr als ~16 gilt als Scope-Drift
(Quoting-Fälle als EIN parametrisierter Test zählen einfach).

Presence-/Auswahl-Semantik (über die JSON-Antwort von
`GET /api/runs/{repo}/{run_id}` bzw. `_run_detail`), je mit einer real
erzeugbaren Zustandslage:

- Eskaliert (`state.phase == escalated`) → Kind `none`, `needs_new_run == true`,
  kein `command`.
- `awaiting_spec_approval`/`awaiting_approval` → Kind `approve`.
- Arbeitsphase mit nicht-`running`-Status → Kind `resume`.
- `state.phase == done` bzw. Arbeitsphase mit `status == running` → KEIN
  `recovery`-Objekt (Schlüssel fehlt, kein leeres Objekt).
- Für `escalated` wird nie `resume`/`approve` vorgeschlagen; die Auswahl folgt
  `state.phase`, nicht dem `phase`-Feld des `escalation`-Events.

Kommandotext:

- Exakter Text mit echter `run_id` und echtem Registry-Pfad (`RepoRef.path`),
  nicht dem Slug, in der jeweiligen CLI-Signatur.
- Shell-Quoting (parametrisierbar, zählt EINFACH): Pfade mit Leerzeichen,
  einfachem Anführungszeichen und Shell-Metazeichen — der Text parst als EIN
  `--repo`-Argument ohne Zusatzkommando (`shlex.split`-basierte Assertion).

Eskalationskontext (Kind `none`):

- `reason`/`phase` unverändert aus dem `escalation`-Event mit der größten `seq`.
- Zuordnung der `limit.hit`/`circuit_breaker`-Ereignisse zur maßgeblichen
  Eskalation — inkl. Abgrenzung gegen eine FRÜHERE Eskalation und leerer Liste,
  wenn keine vorliegen.
- Verweis auf `escalation.md` ohne Inhaltsduplikat.

Robustheit:

- Eskalierter Lauf ohne Event-Log bzw. fehlende Payload-Felder von
  `escalation`/`limit.hit`/`circuit_breaker` → kein 5xx; `kind` und
  `needs_new_run` bleiben sichtbar, keine erfundenen Ersatzwerte (die Karte
  fällt dann auf Run-Ebene zurück, wird nicht ausgeblendet).

Kein realer Pfad in einer URL: Assertion, dass der reale Pfad ausschließlich im
Kartentext/`recovery.command` erscheint, in keiner URL/Route (AC 8).

Beobachtbares Client-/Markup-Verhalten:

- Markup-Ebene über die gerenderte HTML-Seite (`GET /runs/{repo}/{run_id}`):
  genau eine Karte; im Eskalationsfall am `escalation`-Knoten (`anchor_seq`)
  verankert mit Grund/Phase/Abbrüchen/Neu-Lauf-Hinweis/Link; im
  `approve`/`resume`-Fall auf Run-Ebene mit Kommandotext; ohne `recovery`-Objekt
  keine Karte.
- Verhaltens-Ebene, soweit erforderlich AUSFÜHRBAR clientseitig über den
  bestehenden JS-Harness (`tests/gui_js_harness.js`, gefahren aus pytest via
  `run_scenario` in `tests/gui_js_harness.py`, plain `node`, keine neue
  Dependency): die Karte ist reine Anzeige und löst keine Ausführung aus;
  EN/DE-Labels bei unveränderter Kommandozeile.

## Gates (Definition of Done)

- Alle Akzeptanzkriterien durch die beschriebenen Tests und den Änderungsumfang
  abgedeckt.
- `uv run ruff check .` grün.
- `uv run pytest -x -q` grün.
- EN/DE-Dokumentation und Changelog-Einträge synchron.
- Keine Änderung an `escalate()`, am `escalation`-Event, an
  `limit.hit`/`circuit_breaker` oder am Format von `escalation.md` (E4); keine
  neue Route, kein neues Event, keine Persistenz, keine neue Liveness-Erkennung,
  keine Kommandoausführung.
- GUI führt kein Kommando aus (E1); Repo-Pfad nur im Kartentext, nie in einer URL
  (E2).
- Kein unter „Deferred (bewusst nicht gebaut)" genannter Mechanismus ist
  Bestandteil der Änderung.

## Deferred (bewusst nicht gebaut)

Nachvollziehbar, aber für die Ausgangslage unverhältnismäßig. KEINE
Akzeptanzkriterien — und bindend auch für den Review-/Codex-/Fix-Zyklus: was
hier steht, wird dort nicht nachgebaut.

- Geführter Recovery-Assistent (Schritt-für-Schritt statt einzeiligem Kommando).
- Eskalationsgrund-Taxonomie / Klassifikation von Abbruchgründen.
- Verlinkung ähnlicher früherer Läufe oder Cross-Run-Statistik über
  Eskalationsgründe.
- Historie und Vergleich mehrerer Eskalationen desselben Laufs.
- Neue Prozess-/Liveness-Erkennung für Crash-Läufe jenseits der bestehenden
  Status-Ableitung (PID-Prüfung, Heartbeats, Timeouts).
- Ausführung/„Retry" aus der GUI, Zwischenablage-Integration, Vorschlagsliste
  mehrerer Kommandos, automatische Validierung der angezeigten Kommandos.
- Zusätzliche Recovery-Persistenz, Recovery-Events oder serverseitig
  gespeicherte Handlungsempfehlungen.
