# Changelog

Alle nennenswerten Änderungen am ADW-Orchestrator werden in dieser Datei
dokumentiert. Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
Versionierung nach [SemVer](https://semver.org/lang/de/) (0.x: Minor =
Features, Patch = Doku/Fixes).

**Release-Prozess:** Jeder Push nach `main` ist ein Release — er bekommt
einen Versions-Bump in `pyproject.toml`, einen Eintrag hier und ein Git-Tag
`vX.Y.Z` (`git push && git push --tags`). Die Versionen bis 0.2.1 wurden
rückwirkend aus der Push-Historie vergeben; ihre Tags zeigen auf die
gepushten Stände.

English edition: [CHANGELOG.md](CHANGELOG.md)

## [0.18.0] — 2026-09-02

### Geändert
- **Die Trace-Baum-Spalte blättert nicht mehr.** Die linke `section.trace` rendert
  jeden Knoten eines Laufs auf einmal — das bewegliche Fenster (`?offset`, 100 Knoten
  pro Seite) und seine Navigation entfallen. Die Verdichtung greift damit über den
  ganzen Lauf statt an der Seitengrenze zu enden: eine ununterbrochene
  Lese-/Suchfolge wird zu einer einzigen Gruppe. `?offset` wird angenommen und
  ignoriert, damit ein gemerkter Link aus der Blätter-Zeit weiterhin den vollen Baum
  zeigt. Die Werkzeug-Einträge in den Detail-Panes behalten ihr eigenes Fenster
  (`?tools_offset`), das JSON-`tree` bleibt unverändert.
- **Punkt-Knoten bekommen keine eigene Detail-Pane mehr.** Werkzeugaufrufe/-ergebnisse,
  Nachrichten und Snapshots teilen sich eine serverseitig gerenderte Pane-Hülle, die
  der Client bei der Auswahl umhängt und aus der Events-Route füllt; nur Span-Knoten
  (Phase, Lane, Runde, `agent.run`, Gate, Review) behalten ihre eigene Pane. Ohne das
  stellte die ungeblätterte Spalte genau den DOM-Knotenzahl-Engpass wieder her, gegen
  den die Darstellungsgrenzen existieren: bei Lauf `7fe9d702` (1 296 Events) ≈ 11 600
  Elemente, jetzt ≈ 6 400.
- **Der Roh-Payload-Block in der Pane wird eingerückt ausgegeben** — mehrzeiliges,
  lesbares JSON statt einer einzigen Zeile.

## [0.17.0] — 2026-09-02

### Hinzugefügt
- **Verdichtete Trace-Baum-Spalte — Werkzeug-Rauschen gefaltet statt paginiert.**
  Im Trace-Reiter des Run-Details faltet die Baum-Spalte ein `agent.tool.result`
  jetzt in seinen zugehörigen Aufruf (Ausgang + Dauer an der Aufrufzeile), fasst
  unmittelbar wiederholte, zielgleiche `Read`/`Grep`/`Glob`-Aufrufe zu einem
  gezählten Wiederholungsknoten zusammen und gruppiert ununterbrochene
  Lese-/Suchfolgen zu einem aufklappbaren Gruppenknoten (abgebrochen an jeder
  Nachricht, Schreiboperation, jedem Artefakt, Fehler oder `Bash`/unbekanntem
  Werkzeug). Pfade erscheinen repo-relativ mit vollem Pfad im `title`, der Baum
  öffnet mit zugeklappten Phasen (offen: die erste Fehler-Phase, sonst die zuletzt
  begonnene), und eine Zeilenbilanz je Seite zeigt Zeilen gegenüber eingefalteten
  Events. Rein seitenlokale Darstellung: das `tree` von
  `GET /api/runs/{repo}/{run_id}` und das Blätter-Fenster bleiben unverändert.

## [0.16.3] — 2026-09-02

### Behoben
- **Die Haltepunkt-Menge wird beim Run-Start gepinnt** (Follow-up [P2] des Laufs
  `f4942ef3`). `_config_for_continuation()` lud `.adw/config.yaml` bei jedem
  `resume`/`approve` neu, während der State keinen Schnappschuss der aktiven
  Haltepunkte hielt — wer die Datei mitten im Lauf bearbeitete, fügte damit einen
  *künftigen* Halt hinzu oder entfernte ihn, was die Spezifikation verbietet. Die
  wirksame Menge steht jetzt im neuen State-Feld `pinned_breakpoints` (analog
  `pinned_base_branch`), und jede Fortsetzung hält an genau diesen Punkten. Eine
  leere Pinnung heißt „keine Haltepunkte", nicht „ungepinnt"; ein State aus der
  Zeit vor dem Feld liest sich als `null` und folgt weiterhin der Config.

## [0.16.2] — 2026-09-01

### Hinzugefügt
- **Handout zum ADW-Flow am Beispiel eines realen Laufs**
  ([`docs/adw-flow-handout.de.md`](docs/adw-flow-handout.de.md), nur deutsch).
  Zehn Kapitel vom Gesamt-Flow über die Phasen des Laufs `b65f5d75` bis zu
  Telemetrie-Modell, Worktree-Isolation und Crash-Sicherheit, mit Kennzahlen aus
  dem Event-Log dieses Laufs (Wall-Clock, Kosten, Token, Event-Verteilung).

## [0.16.1] — 2026-08-29

### Hinzugefügt
- **Release Notes zur Run-Inspector-Serie 0.9.0 – 0.16.0**
  ([`docs/RELEASE-NOTES.de.md`](docs/RELEASE-NOTES.de.md), englisch:
  [`docs/RELEASE-NOTES.md`](docs/RELEASE-NOTES.md)). Das Changelog hält fest,
  was sich geändert hat; die Release Notes ordnen die acht Releases ein und
  begründen die nicht offensichtlichen Entscheidungen — warum das Plan-Skelett
  kein Kennungsmuster parst, warum der Änderungsumfang kein Scope-Urteil fällt,
  warum die Recovery-Karte auf `state.phase` statt auf das Eskalations-Ereignis
  triggert und warum die Kontextableitung ein einzelner Präfix-Durchlauf ist.
  Enthält die bekannte Einschränkung von 0.16.0 und den CI-Stand je Release.

## [0.16.0] — 2026-08-27

### Hinzugefügt
- **Konfigurierbare Haltepunkte als verallgemeinerte Approval.** Eine neue
  optionale Liste `breakpoints:` in `.adw/config.yaml` aktiviert bis zu zwei
  Halte vor den teuren, schwer umkehrbaren Schritten: `before_integration` (nach
  Abschluss aller Build-Lanes, vor Integrations-/Merge- oder Review-Arbeit) und
  `before_push` (nach dem finalen Review, vor jeglicher Push-/CI-Arbeit). An
  einem aktiven Haltepunkt pausiert der Lauf exakt wie an den bestehenden
  Spec-/Plan-Approval-Gates — persistierte Phase `awaiting_approval`,
  Prozess-Exit-Code 2, Fortsetzung mit `adw approve <run_id> --repo <pfad>`.
  Welcher Haltepunkt wartet, steht im neuen State-Feld `pending_breakpoint`,
  NICHT in einem neuen Phasenwert; das `Phase`-Modell, die Phasenleiste und die
  Retention bleiben unverändert. Jeder Haltepunkt wird als `approval`-Event
  geloggt (`gate` = Haltepunktname, `event` = awaited/granted), sodass GUI und
  Timeline ihn ohne Sonderfall darstellen; die Halte sind idempotent über
  Crash + `resume`. `--no-approval` (auch über `--gates none`) überspringt auch
  die Haltepunkte — EIN Schalter für „keine menschliche Freigabe in diesem
  Lauf". Default (kein Schlüssel oder leere Liste): heutiges Verhalten,
  unverändert.

## [0.15.0] — 2026-08-26

### Hinzugefügt
- **Änderungsumfang eines Laufs im Run Inspector.** Das Run-Detail zeigt jetzt
  nebeneinander, welche Dateien ein Lauf tatsächlich geändert hat — gruppiert je Lane,
  mit `+/-`-Zahlen je Datei — und den im Contract deklarierten Scope, so wie er
  dasteht. Die Dateilisten stammen aus der bestehenden Snapshot-/Diff-Logik: je
  beobachteter Lane genau ein Vergleich zwischen ihrem ersten und letzten Snapshot
  (`refs/adw/<run_id>/<seq>`), eine Binärdatei als „nicht numerisch verfügbar"; keine
  neue Git-Operation, die Diff-Route bleibt unverändert. Der deklarierte Scope ist
  eine lesbare, semantisch äquivalente YAML-Wiedergabe der Top-Level-`x-adw-*`-Blöcke
  des Contracts, gelesen über den bestehenden Whitelist-Artefakt-Pfad mit dem bereits
  vorhandenen `yaml`-Modul; ein fehlendes, unlesbares, kein-Mapping- oder
  `x-adw-`-loses `contract.yaml` zeigt stattdessen klar „kein deklarierter Scope".
  **Es wird kein automatisches Urteil gefällt** — keine Datei wird als „im Scope" oder
  „außerhalb" markiert; die Fakten stehen nebeneinander, die Bewertung macht der
  Mensch. Robust gegen fehlende Daten: eine Lane ohne verwertbares Snapshot-Paar zeigt
  „kein Diff verfügbar", ein Lauf ganz ohne verwertbaren Diff lässt die Tabelle mit
  klarer Aussage entfallen — nie ein 5xx, nie eine leere Tabelle ohne Erklärung.
  Beobachtbar als additives, abgeleitetes `change_scope`-Objekt (`lanes` +
  `declared_scope`) an `GET /api/runs/{repo}/{run_id}`; alle bestehenden
  Antwortfelder bleiben unverändert (additiv, read-only).

## [0.14.0] — 2026-08-26

### Hinzugefügt
- **Plan-Skelett in der Trace-Ansicht des Run Inspector.** Ist für einen Lauf
  `plan.md` vorhanden, leitet das Run-Detail je `## Workstream:`-Abschnitt eine
  read-only Liste der geplanten Aufgaben ab (jede `###`-Überschrift, Text wortgetreu)
  und zeigt sie neben bzw. über dem Trace-Baum derselben Lane — so liegen „geplant"
  (Skelett) und „geleistet" (Trace) in einer Ansicht. Der Parser kennt genau zwei
  Regeln (Abschnitt = `## Workstream: <name>` bis zur nächsten `##`-Überschrift;
  Aufgabe = jede `### `-Zeile), ohne Kennungs-Muster und ohne Markdown-Abhängigkeit,
  sodass die über die Läufe hinweg uneinheitlichen Überschriftenformen alle erhalten
  bleiben. Jede Liste trägt einen groben Status auf Lane-Ebene: `done`, sobald die
  zugehörige Lane mit `completed: true` endet, sonst `pending` (auch bei noch nicht
  gestarteter Lane, ohne dafür einen Trace-Knoten zu erfinden). `plan.md` wird nur
  über den bestehenden Whitelist-Artefakt-Pfad gelesen; fehlt es, ist es leer,
  unlesbar oder unpassend, entfällt das Skelett (kein leerer Kasten, keine Änderung
  am bisherigen Verhalten). Beobachtbar als additives, abgeleitetes
  `plan_skeleton`-Feld in `GET /api/runs/{repo}/{run_id}`; die Chrome-Labels sind
  zweisprachig (`adw/gui/i18n.py`), die Aufgabentexte sind Inhalt und werden nicht
  übersetzt (GUI-SPEC §7.2).

## [0.13.0] — 2026-08-26

### Hinzugefügt
- **Recovery-Karte am verursachenden Knoten im Run-Inspector.** Braucht ein Lauf
  menschliches Eingreifen, leitet das Run-Detail nun genau eine Recovery-Karte ab,
  die das eine passende nächste Kommando als kopierbaren, POSIX-shell-sicheren Text
  nennt — mit dem echten Repo-Pfad aus der Registry und der echten `run_id` (nie dem
  URL-Slug). Das Kommando folgt strikt `state.phase`: Pause am Approval-Gate →
  `adw approve`, abgebrochene/gecrashte Arbeitsphase → `adw resume`, endgültig
  eskalierter Lauf → kein Fortsetzungskommando, sondern der klare Hinweis, dass ein
  NEUER Lauf nötig ist. Im Eskalationsfall ist die Karte am maßgeblichen
  `escalation`-Knoten verankert und zeigt Grund, betroffene Phase und die unmittelbar
  vorausgehenden `limit.hit`/`circuit_breaker`-Ereignisse; sie verlinkt auf
  `escalation.md` im Artefakte-Reiter, statt dessen Inhalt zu duplizieren. Die GUI
  bleibt strikt read-only: das Kommando wird angezeigt, niemals ausgeführt.
  Kartenlabels beidsprachig (`adw/gui/i18n.py`); Kommandozeile, Eventwerte, `run_id`
  und Repo-Pfad werden nicht übersetzt. Beobachtbar als additives, abgeleitetes
  `recovery`-Objekt in `GET /api/runs/{repo}/{run_id}` (GUI-SPEC §7.2).

## [0.12.0] — 2026-08-26

### Hinzugefügt
- **Absprung vom Knoten in den Raw-Log und Prompt-Diff im Run-Inspector.** Jeder
  Span-Knoten im Trace-Baum bietet nun einen Absprung in den bestehenden Raw-Reiter,
  vorgefiltert auf den exponierten Teilbaum-Bereich `[seq, end_seq]` des Knotens —
  so werden die Rohereignisse eines Teilbaums ohne manuelle Suche nach Seq-Grenzen
  gefunden. Der Raw-Reiter erhielt einen inklusiven Seq-Bereichsfilter
  (`raw_from_seq`/`raw_to_seq`, jede Grenze optional/einseitig), serverseitig mit
  den bestehenden `raw_q`/`raw_type`/`limit` komponiert; `total` bleibt die
  Treffermenge vor der Fensterung und `types` die volle Typmenge des Logs. Eine
  nicht-numerische Grenze ist inaktiv, ein umgekehrter Bereich ergibt eine
  definierte leere Menge — nie ein 5xx. Ein aktiver Bereich wird mit seinen Grenzen
  angezeigt und isoliert aufgehoben (unter Erhalt von `raw_q`/`raw_type`/`limit`).
  Der **Prompt**-Reiter eines `agent.run` zeigt zusätzlich einen Unified Diff seines
  Prompts gegen den vorherigen Lauf desselben Agenten in derselben Lane innerhalb
  dieses Laufs (Vorgänger strukturell nach Agent + Lane + größter kleinerer `seq`);
  `GET /api/runs/{repo}/{run_id}` führt an `agent.run`-Knoten die additiven,
  abgeleiteten Felder `prompt_diff`/`previous_prompt_seq` und unterscheidet „kein
  Vorgänger" (beide null) von „identischer Prompt" (`""` mit gesetzter `seq`). Der
  Diff entsteht ausschließlich mit der Standardbibliothek `difflib`. Die
  schreibgeschützte `…/events`-Route bleibt unverändert (weiterhin nur
  `from_seq`/`to_seq`).

## [0.11.0] — 2026-08-26

### Hinzugefügt
- **Kontext-Panel „Lauf-Zustand" im Run-Inspector.** Neben dem Run-Detail-Pane
  zeigt eine read-only Feldliste den Lauf-Zustand **zum Stand des ausgewählten
  Knotens** — `phase`, die umgebende `round` (`{loop, n, cap}`), die Anzahl bis
  hier getroffener `limit.hit`- und `circuit_breaker`-Ereignisse, die kumulierten
  `cost_usd` und die Anzahl der `followup`-Einträge — sodass sichtbar wird, *warum*
  ein Knoten so ausging, ohne den Baum hoch- und runterzuklicken oder in den
  Raw-Reiter zu wechseln. Rein abgeleitet aus den Events, die die Detail-Antwort
  ohnehin lädt: `GET /api/runs/{repo}/{run_id}` liefert jetzt pro Trace-Knoten ein
  sechsfeldriges `context` und auf oberster Ebene ein `latest_context`. Der Cutoff
  eines Knotens ist seine eigene `seq` (Punkt) bzw. sein `end_seq` (Span, das
  Subtree-Maximum); es zählen nur Ereignisse bis einschließlich Cutoff, sodass die
  Knotenauswahl eine Zeitreise ist. Ohne Auswahl zeigt das Panel `latest_context`
  (die Live-Ansicht). Jeder fehlende Wert bleibt leer — `null`, nie ein erfundenes
  `0` — und ein Lauf ohne Trace liefert nur ein `latest_context` mit sechs
  `null`-Feldern, niemals einen Fehler. Kein neues Event, kein neuer Reader, keine
  neue Route, keine Persistenz, keine Laufzeit-Dependency, keine SSE-Änderung;
  `state.saved` bleibt unverändert.

## [0.10.0] — 2026-08-26

### Hinzugefügt
- **Trockenläufe sind im Run-Inspector unverwechselbar.** Ein Trockenlauf (rein
  aus dem vorhandenen `dry_run`-Feld im `run`-Start-Payload abgeleitet — kein
  neues Event, keine neue Route, keine Persistenz) trägt ein kurzes
  `Dry-Run`-Label in seiner Run-Listen-Zeile und ein durchgehendes
  `Dry-Run`-Banner im Run-Detail-Kopf, das beim Scrollen im Trace-Baum am oberen
  Viewport-Rand angeheftet bleibt (sticky Kopf), damit eine inhaltsarme Simulation
  nie mit einem echten Lauf verwechselt wird. Das boolesche `dry_run` erscheint
  jetzt auch im Run-Datensatz von `GET /api/runs` und
  `GET /api/runs/{repo}/{run_id}`; ein fehlendes Feld oder ein fehlender
  `run`-Span gilt als `false`.

### Geändert
- **Die Run-Liste gruppiert nach Status-Priorität.** Läufe sind in der Reihenfolge
  `awaiting_approval`, dann `running`, dann der Rest sortiert (bisher wurde nur
  `running` nach vorn gezogen, sodass ein auf einen Menschen wartender Lauf unter
  neuere fertige Läufe rutschte). Innerhalb jeder Gruppe bleibt die Reihenfolge
  „neueste zuerst" unverändert.

## [0.9.0] — 2026-08-26

### Hinzugefügt
- **Der Run-Inspector unterscheidet „arbeitet" von „wartet" von „wartet auf
  Menschen".** Drei bisher ununterscheidbare Situationen sind jetzt getrennt,
  rein aus dem vorhandenen Event-Log abgeleitet (keine neuen Events, Routen oder
  Persistenz):
  - Der Trace-Baum gibt einem offenen `ci.wait`-/`gate`-Span den Status
    `waiting` (leeres CI-Pollen oder Gate-Laufzeit) statt `running`; derselbe
    Span, den die Timeline schon als wartend zeichnet, stimmt nun im Baum überein.
    Ein beendeter `gate`/`ci.wait`-Span behält sein Ergebnis (`passed`/`failed`,
    sonst `done`).
  - Ein an einem Approval-Gate pausierter Lauf meldet `awaiting_approval` — nicht
    `running` — in `GET /api/runs`, `GET /api/runs/{repo}/{run_id}`, der Run-Liste
    und im Run-Detail-Kopf, auch solange sein `run`-Span offen ist. Abgeleitet
    wird das aus dem jüngsten `approval`-Event (`awaited` ohne späteres
    `granted`); ein Lauf ohne Trace fällt auf seine State-Phase zurück. Ein
    beendeter `run`-Span behält seinen terminalen End-Payload-Status unverändert.
  - Die Phasenleiste zeigt die wartende Fachphase (`spec` bzw. `plan`) als
    `awaiting` statt `active`.
  - `awaiting_approval` — der einzige Zustand, in dem ein Mensch handeln muss —
    wird optisch am stärksten hervorgehoben; neues CSS und EN/DE-Labels sind
    additiv, die JSON-Statuswerte bleiben sprachneutral.

## [0.8.0] — 2026-08-20

### Hinzugefügt
- **`adw runs list` und `adw runs prune`** machen die Lauf-Retention bedienbar.
  `list` zeigt Run-ID, Phase, Datum, Ereigniszahl und Log-Größe, damit sichtbar
  wird, wann Pruning fällig ist. `prune [--keep N] [--older-than DAYS] [--gzip]`
  behält per Default die 20 jüngsten Läufe und arbeitet die ältesten zuerst ab.
  - Das Löschen eines Laufs entfernt seinen Ordner, seine Snapshot-Refs
    (`refs/adw/<run_id>/*`) **und seine registrierten Git-Worktrees** — letztere
    über die Git-Worktree-Verwaltung statt per `rmtree`, sodass keine verwaiste
    Registrierung zurückbleibt und der Lane-Branch erhalten bleibt. Das ist
    wesentlich: 96 % der 595 MB Laufdaten dieses Repos liegen in diesen
    Worktrees; ein Pruning, das sie ausspart, gäbe rund 3 % frei.
  - **Nichts wird je gewaltsam entfernt.** Ein Lauf, der nicht `done` oder
    `escalated` ist, wird nie gepruned — sein State ist die Grundlage seiner
    Fortsetzbarkeit. Ein Lauf mit uncommitteten Änderungen in *irgendeinem*
    seiner Worktrees wird komplett übersprungen, und jeder übersprungene Lauf
    wird benannt statt stillschweigend ausgelassen.
  - `--gzip` ist die *behaltende* Form: Sie komprimiert `events.jsonl` und lässt
    Laufordner, State, Worktrees und Snapshot-Refs unangetastet, sodass der Lauf
    vollständig benutzbar bleibt, Diff-Reiter eingeschlossen. Die Kompression ist
    atomar (temporäre Datei plus Rename), und die `.gz` erbt die 0600-Rechte des
    Logs — es enthält ungeschwärzte Prompts und Tool-Ausgaben.
  - Gelöscht wird in fester, fortsetzbarer Reihenfolge (Worktrees, dann Refs,
    dann Ordner). Eine verweigerte Worktree-Entfernung behält das Erreichte,
    benennt es, lässt die übrigen sicheren Kandidaten weiterhin bearbeiten und
    endet mit Nichtnull-Exit; ein Fehler beim Entfernen der Refs oder des Ordners
    bricht den Durchlauf ab und meldet den erreichten Zustand, sodass ein
    späteres Pruning fortsetzen kann.
- **`trace:`-Config-Block**: `enabled` (Default `true`) schaltet das Ereignis-Log
  ganz ab, `keep_runs` (Default `20`, `0` deaktiviert) steuert automatisches
  Pruning nach einem erfolgreichen Lauf. Auto-Pruning ist fail-open — es ändert
  nie Phase oder Exit-Code des fertigen Laufs —, meldet aber, was es getan oder
  nicht geschafft hat.
- **Der Reader liest `events.jsonl.gz` transparent**, ein komprimierter Lauf
  liefert dieselben Ereignisse, dieselbe Reihenfolge und dieselben `seq`-Werte
  wie sein unkomprimiertes Original.
- **GUI auf Deutsch und Englisch.** Die Sprache wird pro Request als `?lang=` →
  Cookie → `Accept-Language` → `en` bestimmt; der Default bleibt Englisch.
  Übersetzt wird nur die Chrome — Prompts, Agent-Ausgaben, Findings,
  Artefakt-Körper und Gate-Output sind in beiden Sprachen byte-identisch. Im
  Header steht ein Umschalter, der den in der URL getragenen Seitenzustand
  erhält — gefensterter Ausschnitt und fokussierter Knoten. Auch die vom Client
  injizierten
  Hinweise (Laden, leerer Diff, Ladefehler) werden mit der Seite ausgeliefert,
  sodass eine deutsche Seite keine englischen Reste zeigt.

## [0.7.0] — 2026-08-17

### Hinzugefügt
- **`adw run --gates none|spec|plan|both`** — ein sprechender Schalter über die
  Freigabe-Gates. `none` läuft voll autonom: nichts hält den Lauf an außer einer
  Eskalation. `spec` hält nach der Spec, vor dem Plan; `plan` hält vor dem Build;
  `both` hält an beiden Gates. Der wirksame Modus wird beim Start ausgegeben,
  damit ein unbeaufsichtigter Lauf keine Ratesache ist.
  - Die 4-Wege-Matrix war über die zwei alten Booleans schon erreichbar, aber
    nicht auffindbar: `--no-approval` liest sich wie „braucht keine Freigabe"
    statt „läuft autonom durch", und „nur am Spec-Gate halten" verlangte die
    widersinnige Kombination `--no-approval --spec-approval`, die in 19 Läufen
    nie jemand benutzt hat.
  - Die Altflags bleiben gültig und äquivalent — `--no-approval` == `--gates
    none`, `--spec-approval` == `--gates both`, beide zusammen == `--gates spec`
    —, bestehende Skripte und Gewohnheiten laufen unverändert weiter.
  - Ein Widerspruch zwischen `--gates` und einem Altflag wird abgelehnt, bevor
    der Lauf angelegt wird: reihenfolgeunabhängig und ohne stille Vorrangregel.
    Eine redundante, aber widerspruchsfreie Angabe ist zulässig. Ein ungültiger
    Wert wird unter Nennung der vier erlaubten abgelehnt.
  - Der Default ist unverändert: `adw run` ohne Flags hält weiterhin nur am
    Plan-Gate. Jeder Modus bildet auf die beiden State-Felder ab, die die
    Mechanik schon konsumiert — von älteren Versionen geschriebene Run-States
    bleiben damit resumierbar.

### Behoben
- CI ist bezüglich gefärbter Ausgaben deterministisch (`NO_COLOR`), und der
  Hilfetext-Test entfernt ANSI-Escapes vor der Prüfung. Rich färbt Optionsnamen
  und zerlegt sie in Style-Segmente — das erste `-` wird ein eigenes Segment —,
  sodass ein rohes `--gates` bei aktiver Färbung nicht als Teilstring überlebt.
  Rich färbt unter `GITHUB_ACTIONS`, lokal bei nicht terminalgebundener Ausgabe
  nicht; genau deshalb war ein Test lokal grün und in CI rot.

## [0.6.0] — 2026-08-17

### Hinzugefügt
- **Gedeckelte Eintragsknoten im Run Inspector.** Der gemessene Engpass hinter
  der „Reaktion ≤ 2 s"-Zusage war die *Anzahl* der DOM-Eintragsknoten, nicht
  deren Inhalt (Lauf `bf831719` blockierte über 40 s hinaus). Beide Sammlungen —
  Trace-Baum und Tools-Reiter — rendern jetzt über ein globales Budget von
  höchstens **200 Einträgen pro Sammlung**, unabhängig von der Gesamtgröße des
  Laufs, und der Deckel hält über die Navigation hinweg, nicht nur beim ersten
  Rendern. Jeder gerenderte Eintrag trägt einen maschinenlesbaren Marker
  (`data-tree-entry`, `data-tool-entry`), damit die Zahl geprüft statt geschätzt
  werden kann.
- **Ein gleitendes Fenster hält jeden Eintrag erreichbar.** Die Query-Parameter
  `offset` (Baum) und `tools_offset` + `focus` (Tools) verschieben den
  gedeckelten Ausschnitt über `← previous` / `more →`, sodass ein später
  Eintrag erreichbar ist, ohne die vorangehenden erneut zu materialisieren.
  Beide Fenster blättern unabhängig voneinander, und der wirksame Ausschnitt
  überlebt den Live-Region-Swap — der Refresh lädt die Seite nach, die der
  Nutzer tatsächlich ansieht, nicht das Default-Fenster des Servers.
- **Letzte Interaktion gewinnt (Supersession).** Eine überholte Interaktion
  schreibt nichts ins DOM, setzt keinen End-Mark und erzeugt kein Measure;
  Marks verschiedener Auswahlen werden nie gepaart. Zwei schnelle Klicks lassen
  das Detail-Pane auf dem zuletzt geklickten Knoten stehen.
- **Drittes Reaktionszeit-Measure `adw:artifact`**, gebaut wie `adw:select` und
  `adw:tab` (Start-Mark am auslösenden Eingabe-Ereignis, End-Mark in einem Task,
  der aus einem `requestAnimationFrame`-Callback heraus geplant wird und damit
  nach dem Paint läuft). Beim Öffnen eines großen Artefakts wird nur ein
  gedeckelter Anfangsausschnitt eingefügt; der vollständige Inhalt bleibt über
  die Artefakt-Route erreichbar.
- **Navigation per Timeline-Balken**: Ein Klick auf einen Balken wechselt in den
  Trace-Reiter und wählt den zugehörigen Knoten aus, dessen `data-seq` der
  Balken trägt.
- **Ein abhängigkeitsfreier JS-Testharness** (`tests/gui_js_harness.js` / `.py`),
  der das *ausgelieferte* `app.js` in einem einfachen `node`-Prozess mit
  gestubbtem DOM, `fetch`, `performance` und Task-Scheduling fährt. Er ist reines
  Testwerkzeug, nie eine Laufzeit-Abhängigkeit, und kein Browser — kein
  Playwright, kein Selenium. Fehlt die `node`-Laufzeit, schlagen die Tests fehl,
  statt zu skippen.
- `docs/gui-response-time.md` dokumentiert die Marker-Selektoren, das gleitende
  Fenster und das manuelle Messverfahren.

## [0.5.1] — 2026-08-14

### Behoben
- `ONBOARDING.md` wird von git ignoriert. Die Datei ist das Sitzungs-Handover
  des `offboarding`-Skills und war nie committet; seit die Arbeitsbaum-Prüfung
  aus 0.5.0 scharf ist, zählte sie als fremde uncommittete Datei und
  verweigerte jedes `adw run`, `adw resume` und `adw approve`.
- Release-Datum von 0.5.0 in dieser Datei korrigiert (Release war am
  2026-08-14, nicht am 2026-08-12).

## [0.5.0] — 2026-08-14

Nachhol-Release: Die GUI-Arbeit (Läufe 1–5b) ist über mehrere Pushes nach
`main` gelangt, ohne eigene Versions-Bumps. Dieser Eintrag deckt alles seit
0.4.0 ab.

### Hinzugefügt
- **ADW Run Inspector (`adw gui`)** — eine read-only Web-Ansicht eines Laufs.
  Bindet ausschließlich an Loopback, sofern nicht `--i-know` gesetzt ist;
  `--repo` macht Repos über die Registry hinaus verfügbar, `--port`
  (Default 8765) und `--open` steuern die lokale Adresse. Der Web-Stack ist ein
  optionales Extra (`pip install adw[gui]`) und bleibt aus den Kern-
  Dependencies heraus: eine reine `adw run`-Installation importiert ihn nie.
  - Run-Liste und Run-Detail mit den Reitern **Trace**, **Timeline**,
    **Artifacts** und **Raw**; das Detail-Pane zeigt Prompt, Answer, Tools und
    Diff zum ausgewählten Knoten.
  - Diff-Endpunkt mit expliziter Ref-Allowlist.
  - Live-Tail über Server-Sent Events, solange ein Lauf läuft.
- **Ereignis-Log** (`adw/events.py`): Der Orchestrator hängt seine
  Lauf-Ereignisse als JSON Lines an `.adw/runs/<id>/events.jsonl`. Der Emitter
  ist **fail-open** — kein emitter-interner Fehler (Platte voll, Rechte, nicht
  serialisierbare Payload) erreicht je den Aufrufer oder bricht einen Lauf ab;
  `state.json` bleibt die Resume-Autorität.
- **Git-Snapshots** (`adw/snapshots.py`): Der Baum vor und nach jedem
  Agent-Lauf wird unter `refs/adw/<run>/<seq>` festgehalten — erst das macht
  den Diff pro Knoten in der GUI möglich, ohne Arbeitskopien vorzuhalten.
- **Orchestrator-Instrumentierung**: Spans an den Aufrufstellen für Run,
  Phase, Runde, Agent-Lauf, Tool-Nutzung, Gate und Codex-Schritte — für Mock
  und echten Runner gleichermaßen, sodass auch ein Dry-Run dieselbe
  Trace-Form erzeugt.
- **`codex.timeout`** als optionaler Key in `.adw/config.yaml` (ganzzahlige
  Sekunden, > 0, Default 900). Er gilt für die `codex exec`-Subprozesse; ohne
  den Key bleibt das effektive Limit unverändert. Ungültige Werte werden als
  `ConfigError` abgelehnt, bevor der Lauf startet.
- **Arbeitsbaum-Prüfung vor `adw run`, `adw resume` und `adw approve`**:
  Betreffen die uncommitteten Änderungen ausschließlich ADWs eigene sechs
  Authoring-Artefakte (`.adw/issue.md`, `spec.md`, `plan.md`,
  `contract.yaml`, `spec-summary.md`, `plan-summary.md`), setzt ADW sie selbst
  zurück und fährt fort. Jede fremde Datei — oder eine Mischung aus fremder
  Datei und ADW-Artefakt — lässt das Kommando stattdessen verweigern, ohne
  irgendetwas zu verwerfen. Im User-Handbuch dokumentiert (EN + DE).
- Spezifikations- und Messdokumente: `docs/GUI-SPEC.md` (+ `.de.md`) und
  `docs/gui-response-time.md`.

### Behoben
- Ein fehlschlagender **Codex-Autor** im Dual-Authoring bricht den Lauf nicht
  mehr ab. Der `FAILED`-Marker wird wie bisher geschrieben, und die Phase läuft
  einquellig mit dem verbliebenen Claude-Entwurf weiter — kein Traceback, kein
  Exit 1, keine manuelle Recovery. Ein Codex-Timeout hat den Orchestrator
  zuvor zum Absturz gebracht und ein manuelles Säubern des Arbeitsbaums
  erzwungen.
- Die Arbeitsbaum-Prüfung **eskaliert keinen Lauf mehr** — weder bei `run`
  noch bei `resume`. Sie verweigert höchstens; der Run-State bleibt unverändert
  und resumierbar. Zuvor eskalierte eine von ADWs eigenem Crash
  hinterlassene dirty `.adw/spec.md` den Lauf beim Resume dauerhaft und
  machte ihn unwiederbringlich verloren.
- Ein **partieller Synthese-Ausfall** (ein Pflicht-Artefakt fehlt oder ist
  leer) wird jetzt durch genau einen Retry desselben Schritts über die
  bestehende Session repariert, unter Nennung des fehlenden Artefakts. Erst
  wenn auch der Retry scheitert, eskaliert der Lauf. Zuvor tötete eine
  geschriebene `spec.md` plus fehlende `spec-summary.md` die ganze Phase.
- Die **Agent-Session-ID wird persistiert, sobald sie im Message-Stream
  erscheint**, statt erst nach Abschluss des Laufs. Ein Abbruch mitten im Lauf
  hinterlässt sie damit im State, und `adw resume` knüpft an die begonnene
  Session an, statt sie neu zu starten und die bereits verbrauchten Tokens zu
  verlieren.
- `test_measurement_guide_document_is_present_and_complete` prüfte nur den
  ersten Kandidaten, sodass jede unbeteiligte Markdown-Datei, die die beiden
  Messnamen nennt, den Test kippen konnte; jetzt muss *irgendein* Dokument
  vollständig sein.

## [0.4.0] — 2026-08-07

### Added
- **RED-Gate in der Build-Phase**: Ein Gate lässt sich in `.adw/config.yaml`
  mit `tdd: true` markieren. Eine Lane mit mindestens einem markierten Gate
  fährt ihren Initial-Build zweistufig — ein Agent-Lauf mit der Anweisung,
  nur Tests zu schreiben („schreibe NUR die Tests, keinen Produktivcode"),
  danach führt der
  Orchestrator selbst genau die markierten Gates aus. Mindestens eines rot
  ist der RED-Beweis (`red_confirmed` plus die Test-Pfade im Lane-State);
  die Implementierung macht in **derselben Session** mit dem gekürzten roten
  Gate-Output weiter und mündet in den bestehenden Gate-Loop. Alle
  markierten Gates grün nach dem Test-Lauf eskaliert statt zu schleifen: Die
  Tests decken das geforderte Verhalten nicht ab oder es existiert bereits.
- Fälschungsschutz um den Beweis: Ein Test-Lauf, der Dateien löscht oder den
  Worktree unverändert lässt, eskaliert; grüne Gates zählen nur, solange die
  Tests, die RED bewiesen haben, noch da sind.
- Dry-Run deckt beide Pfade mit 0 Tokens ab — die Default-Config (ohne
  `tdd`-Gate) bleibt einstufig, eine Config mit `tdd`-Gate fährt den
  kompletten RED-Pfad über die CLI.

### Changed
- Der RED-Check verbraucht keine Gate-Iteration; alle Limits und der
  Circuit-Breaker bleiben unverändert. Fix-Dispatches aus den Review-/
  E2E-Phasen (`pending_task` gesetzt) und Lanes ohne markiertes Gate
  verhalten sich exakt wie bisher. `red_confirmed` überlebt Crash + Resume:
  Ab dem gecheckpointeten Test-Lauf wiederholt ein Crash vor dem RED-Check
  nur noch den Check.
- Doku (SPEC, User-Handbuch, Kontrollfluss-Handbuch, technische Spec, EN+DE
  inkl. HTML/DOCX-Exporte) beschreibt die RED-Stufe.

## [0.3.0] — 2026-08-03

### Added
- **Dual-Authoring mit Best-of-Synthese** für Spec- und Plan-Phase:
  Claude Opus (`spec_agent`/`plan_agent`) und Codex (`CodexRunner.author()`,
  read-only-Sandbox, Marker-Block-Output mit Nonce je Aufruf) schreiben zwei
  unabhängige Entwürfe **parallel** nach `.adw/runs/<id>/drafts/`; ein
  Fable-Synthese-Agent (`spec_synthesis`/`plan_synthesis`) mergt sie zum
  Best-of-Artefakt und schreibt zusätzlich eine Gate-Zusammenfassung
  (`spec-summary.md`/`plan-summary.md`), die archiviert und an den
  Freigabe-Gates vorgelegt wird. Die Synthese ist der erste Lauf des
  bestehenden Codex-Review-Loops — Policy v2, Runden-Cap, Circuit-Breaker
  und Crash-Resume unverändert.
- Codex-Entwurfsfehler **degradieren** statt zu eskalieren: Warnung +
  `<kind>.codex.FAILED`-Marker, die Synthese arbeitet einquellig weiter; ein
  fehlender Claude-Entwurf eskaliert weiterhin. Die Draft-Stage ist über
  Dateien idempotent (ein Resume wiederholt keinen fertigen Autor).
- Dry-Run deckt den neuen Kontrollfluss komplett ab (unterscheidbare
  Draft-Fixtures je Autor, Drafts + Summaries im Run-Ordner, 0 Tokens).
- Dieses Changelog, inkl. rückwirkender Versionen für alle gepushten Stände.

### Changed
- Entwurfs-Autoren von Fable auf Opus umgestellt; die geteilten
  Authoring-Inhaltsregeln liegen nur noch an einer Stelle (`adw/agents.py`)
  und werden von den Codex-Autoren-Prompts importiert — kein Drift zwischen
  den Maßstäben der beiden Autoren.
- Protocol `CodexReviewer` in `CodexClient` umbenannt (review + author).
- Doku (SPEC, User-Handbuch, Kontrollfluss-Handbuch, technische Spec, EN+DE
  inkl. HTML/DOCX-Exporte) auf den Dual-Authoring-Ablauf nachgezogen.

## [0.2.1] — 2026-07-30

### Changed
- HTML- und DOCX-Exporte der Handbücher/Spec auf Review-Loop-Policy v2
  nachgezogen.

## [0.2.0] — 2026-07-30

### Added
- **Review-Loop-Policy v2**: absteigende Severity-Schwelle je Runde (R1
  alles, R2 P1+P2, ab R3 nur P1), Findings-Gedächtnis mit Dispositionen ab
  Runde 2 als Codex-Kontext, hartes Cap von 5 Runden, verbleibende Findings
  als Known Limitations dokumentiert.
- Authoring-Härtung: Proportionalitäts-Gegenkraft in den Authoring-Prompts
  (A1–A3), Runden-Cap im Authoring-Loop, `--spec-approval`-Gate (Stopp nach
  Spec, vor Plan), Issue-Text als Review-Referenz `.adw/issue.md` (B1–B3).
- Prozess-Anforderungen (Commit-Messages, Branch-Topologie, Git-Historie)
  sind aus Specs verbannt; reine P3-Untätigkeit eines Fix-Laufs wird in den
  Follow-up-Report vertagt statt zu eskalieren (A4).

## [0.1.8] — 2026-07-21

### Changed
- Prompts und Docstrings durchgängig Englisch (Kommentare bleiben Deutsch).

## [0.1.7] — 2026-07-18

### Changed
- Zweisprachige Doku, Teil 3: restliche Dokumente in EN- + DE-Fassung
  aufgeteilt.

## [0.1.6] — 2026-07-18

### Changed
- Zweisprachige Doku, Teil 2 (Handbücher, technische Spec).

## [0.1.5] — 2026-07-18

### Changed
- Zweisprachige Doku, Teil 1 (README, SPEC).

## [0.1.4] — 2026-07-18

### Fixed
- Triage verliert keine Findings mehr: Lane-Labels werden tolerant
  behandelt.

## [0.1.3] — 2026-07-18

### Added
- Kontrollfluss-Handbuch; DOCX-/MD-Exporte der Doku.

## [0.1.2] — 2026-07-15

### Added
- MIT-Lizenz.

## [0.1.1] — 2026-07-15

### Changed
- README verweist auf den Claude-Skill (eigenes Repo
  `agentic-developer-workflow-skill`).

## [0.1.0] — 2026-07-15

Erstes Release.

### Added
- 7-Phasen-Orchestrator: Spec → Plan+Kontrakt → Build-Lanes →
  Integration/E2E → Codex-Code-Review → finaler Review → Push/CI.
  Kontrollfluss ist deterministischer Code; Agenten liefern nur
  Urteilsvermögen.
- `adw`-CLI mit `run`/`resume`/`approve`/`status`, Plan-Freigabe-Gate,
  resumefähigem State (atomare Persistenz, Repo-Lock, Crash-Checkpoints)
  und tokenfreiem `--dry-run`-Modus.
- Gehärteter Claude-Agent-SDK-Runner (Env-Whitelist, Secret-Store-Denies,
  sandboxtes Bash, artefakt-exakte Schreibrechte) und Codex-Reviewer als
  isolierter read-only-Subprocess mit striktem Findings-Parsing.
- Lane-Worktrees mit deterministischen Ports, Gate-Runner mit Timeouts und
  Prozessgruppen-Cleanup, Triage-Regeln, Iterations-Limits,
  Circuit-Breaker.
- GitLab- (glab) und GitHub-Support (gh) für Issues und CI-Monitoring.
- README, User-Handbuch, technische Spezifikation (HTML-Handouts),
  Beispiel-Config; ADW als Claude-Skill paketiert (in eigenes Repo
  ausgelagert).

[0.18.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.3...v0.17.0
[0.16.3]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.2...v0.16.3
[0.16.2]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.1...v0.16.2
[0.16.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.8...v0.2.0
[0.1.8]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/sostrowsk/agentic-developer-workflow/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sostrowsk/agentic-developer-workflow/releases/tag/v0.1.0
