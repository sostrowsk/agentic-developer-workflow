# Spec — Web-App (Run-Liste, Run-Detail mit Trace-Baum + Detail-Pane, SSE-Live-Stream) + Home-Isolation-Bugfix

Normative Grundlage: `docs/GUI-SPEC.md`, insbesondere §7 (Web-App) und §8
(Sicherheit); bei Widerspruch gilt die GUI-SPEC. Dieser Lauf deckt GUI-SPEC §11
Schritte 8 und 9 ab (FastAPI-App mit Run-Liste und Run-Detail; SSE-Live-Stream).
Schritte 10–12 (Timeline/Artefakte/Raw, Diff, i18n) sind spätere Läufe.

Ausgangslage: Emitter, Instrumentierung, Snapshots, Reader (`adw/gui/reader.py`),
Span-Baum-Modell (`adw/gui/model.py`) und Registry (`adw/gui/registry.py`) sind
gebaut und auf `main`; 614 Tests sind grün. Die Lese-Datenschicht wird in diesem
Lauf **nur konsumiert, nicht verändert**.

## Goal

Zwei Ergebnisse:

- **A (Übernahme-Bugfix).** Kein Test verschmutzt mehr das echte
  Home-Verzeichnis. Die Auto-Registrierung von `adw run` schreibt beim Testlauf
  nicht länger in das reale `~/.adw/repos.json`; der Registry-Pfad ist für
  Tests isolierbar, und die bestehenden Tests nutzen diese Isolation. Das
  produktive Verhalten von `adw run` bleibt unverändert.
- **B (Hauptteil).** `adw gui` startet eine lokale, **strikt read-only**
  FastAPI-Web-App, die die Event-Logs aller registrierten Repos rendert:
  Run-Liste über alle Repos, Run-Detail mit Phasen-Statusleiste, aufklappbarem
  Trace-Baum aus dem Modell und knotenabhängigem Detail-Pane, sowie ein
  SSE-Live-Tail laufender Runs. Ein später geöffneter, fertiger Run rendert
  identisch zu einem live beobachteten.

## Scope

- **Aufgabe A:** Der Registry-Pfad wird für Tests isolierbar gemacht
  (überschreibbare Pfad-Quelle oder HOME-Monkeypatching in einer geteilten
  Fixture); die bestehende Test-Suite nutzt die Isolation flächendeckend.
  Format und Laufzeitverhalten von `adw/gui/registry.py` bleiben unverändert;
  erlaubt ist allein, den Pfad-Bezug überschreibbar zu machen.
- **Aufgabe B, neu:** `adw/gui/app.py` (FastAPI-App) samt Jinja2-Templates und
  statischen Eigen-Assets (nur handgeschriebenes CSS und Vanilla JS — E5).
- **Aufgabe B:** `adw gui` als neues CLI-Kommando.
- **Verpackung:** FastAPI, uvicorn und Jinja2 ausschließlich als optionales
  Extra `adw[gui]` in `pyproject.toml`; das Kernpaket bleibt frei von
  Web-Abhängigkeiten (E7).
- Die Oberfläche ist in diesem Lauf einsprachig **Englisch**.

### Öffentliche Fläche (Kontrakt)

Kontraktuell sind nur: die HTTP-Routen mit ihren Antwortformaten, das
SSE-Format, das CLI-Interface von `adw gui` und die Zusicherung, dass das
Kernpaket ohne das `gui`-Extra unverändert funktioniert. **Nicht** kontraktuell:
interne Helper-Signaturen, Template-/CSS-/JS-Dateistruktur, Markup-Details.
Feldlisten sind Mindestangaben; JSON-Objekte dürfen weitere Felder tragen.

**CLI**

```
adw gui [--repo PATH]... [--host 127.0.0.1] [--port 8765] [--open] [--i-know]
```

- `--repo PATH` (mehrfach): fügt Repos ad hoc hinzu; ohne Angabe zeigt die App
  die Repos aus der Registry.
- `--host` default `127.0.0.1`, `--port` default `8765`.
- `--open`: öffnet den Browser auf der lokalen Adresse.
- `--i-know`: das explizite Opt-in (GUI-SPEC §7.1/§8), das eine
  Nicht-Loopback-`--host`-Adresse überhaupt erst zulässt.

**HTTP-Routen** (`{repo}` = stabiler Registry-Slug, `{run_id}` nach
`RUN_ID_RE` aus `adw/state.py`):

| Route | Antwort |
| --- | --- |
| `GET /` | HTML — Run-Liste |
| `GET /runs/{repo}/{run_id}` | HTML — Run-Detail |
| `GET /api/runs` | JSON — Liste von Run-Zusammenfassungen |
| `GET /api/runs/{repo}/{run_id}` | JSON — Metadaten + Span-Baum + gemeldete Leseprobleme |
| `GET /api/runs/{repo}/{run_id}/events?from_seq=N` | JSON — Roh-Event-Records ab `seq >= N` |
| `GET /api/runs/{repo}/{run_id}/stream` | SSE — Live-Tail |

- `GET /api/runs` liefert je Run mindestens: `run_id`, `repo` (Slug),
  `repo_exists` (bool), `issue` (gekürzt), `phase`, `status`, `start`,
  `duration`, `cost`, `event_count`. Laufende Runs stehen zuerst.
  Einzelne Runs eines Repos sind nur auflistbar, solange dessen
  `.adw/runs/`-Verzeichnis lesbar ist; für ein registriertes Repo, dessen Pfad
  nicht (mehr) erreichbar ist, enthält die Antwort stattdessen **einen
  Repo-Platzhalter-Eintrag** mit mindestens `repo` (Slug), `repo_exists: false`
  und einem darstellbaren Hinweis (z. B. dem registrierten Pfad) — ohne
  Run-Felder, ohne rekonstruiertes Run-Metadatum, ohne persistentes Caching.
- `GET /api/runs/{repo}/{run_id}` liefert mindestens: Run-Metadaten, die
  Phasen-Statusleiste, den Span-Baum aus `adw.gui.model.build_tree`
  (serialisiert, Knoten mit Typ/Label/Dauer/Payload und Kindern) und die vom
  Reader gemeldeten Probleme (`seq`-Lücken, kaputte Zeilen).
- `GET /api/runs/{repo}/{run_id}/events` liefert die vom Reader akzeptierten
  Roh-Event-Records mit allen Originalfeldern in Dateireihenfolge, ab dem per
  `from_seq` angegebenen `seq` (default: von Beginn). Unbekannte Event-Typen
  werden nicht gefiltert.
- Fehlerfälle: unbekannter Slug → `404`; formal ungültige `run_id` → `400`;
  formal gültige, aber nicht vorhandene `run_id` → `404`. Nie ein Serverfehler,
  nie ein Zugriff außerhalb von `.adw/runs/<run_id>/`.

**SSE-Format** (`text/event-stream`)

- Als SSE-Nachricht gesendet wird jede neue vollständige Zeile, die der Reader
  als Event-Record mit gültiger ganzzahliger `seq` akzeptiert: `id:` trägt die
  `seq` des Events, `data:` den vollständigen Event-Record (dieselben Felder
  wie im Log).
- Eine kaputte Zeile oder ein Record ohne gültige `seq` erzeugt **keine**
  Event-Nachricht und beendet den Stream nicht. Der Server überspringt sie,
  tailt weiter und sendet stattdessen eine SSE-Nachricht `event: problem`
  **ohne** `id:`-Feld (damit bleibt `Last-Event-ID` unberührt), deren `data:`
  die Problembeschreibung in derselben Form trägt wie die Reader-Probleme im
  Run-Detail-JSON. So wird auch live auftretende Korruption ohne Reload
  sichtbar (AC 15), nicht erst beim nächsten Seitenaufbau.
- Erstverbindung (kein `Last-Event-ID`-Header — natives `EventSource` kann
  initial keinen Header setzen): der Stream beginnt **am Dateianfang** und
  liefert alle akzeptierten Events. Der Client merged über die ganzzahlige
  `seq` und ignoriert Records, die sein initialer Snapshot schon enthält — so
  geht ein zwischen Snapshot-Abruf und Stream-Start angehängtes Event nicht
  verloren und wird nicht doppelt gerendert.
- Reconnect: liegt der Header `Last-Event-ID` vor, setzt der Server **nach**
  dieser `seq` fort — keine Dopplung, keine Lücke.
- Nach dem `run`-End-Event wird der Stream serverseitig geschlossen.

## Non-goals (Scope-Deckel — in diesem Lauf NICHT gebaut)

- **Kein Schreibpfad.** Die GUI ist strikt read-only: kein approve, resume,
  abort, Run-Start; kein Schreiben in `state.json`, ins Repo oder ins
  Event-Log. Kein Codepfad der GUI verändert eine Datei des Runs.
- Keine Timeline, kein Artefakte-Reiter, kein Raw-Reiter, kein Diff-Reiter und
  kein Diff-Endpoint (`/diff`, `/artifacts/{name}` aus §7.4 bleiben aus) —
  alles Lauf 5. Die GUI führt in diesem Lauf **kein** externes Programm aus
  (auch kein `git diff`).
- Keine i18n/Sprachumschaltung, kein `--lang` (Lauf 5). Oberfläche Englisch.
- Kein Prunen, keine Retention, kein gzip, kein `trace:`-Config-Key.
- **Keine Änderung** an `adw/events.py`, `adw/snapshots.py`,
  `adw/gui/reader.py` oder `adw/gui/model.py`. Reichen deren APIs nicht aus,
  ist das ein Befund für den Bericht, keine stille Erweiterung.
- Kein Refactoring von `phases.py`; außer Aufgabe A keine Änderung am
  Orchestrator. Keine zusätzlichen `phase`/`lane`-Felder (E6).
- Keine Authentifizierung, kein TLS, kein Mehrbenutzerbetrieb (GUI-SPEC §2).
- Kein Fremd-Frontend-Asset: kein htmx, kein CDN, keine node-Toolchain, nichts
  aus dem Netz, kein Vendoring einer Bibliothek (E5).

## Acceptance criteria

### A — Home-Isolation-Bugfix

1. Ein kompletter Testlauf lässt die reale Datei `~/.adw/repos.json` des
   Nutzers unverändert: existiert sie vorher nicht, wird sie nicht angelegt;
   existiert sie, bleibt ihr Inhalt bitgleich. Temporäre pytest-Repo-Pfade
   erscheinen dort nie. Ein Regressionstest belegt dies.
2. Der von der Registry benutzte Pfad ist für Tests isolierbar (überschreibbare
   Pfad-Quelle oder HOME-Monkeypatching in einer geteilten Fixture); die
   Isolation greift für **jeden** Test, der `adw run` oder die
   Auto-Registrierung auslöst — nicht nur für die Registry-eigenen Tests.
3. Außerhalb der Testisolation ändert sich das Verhalten von `adw run` nicht:
   die Auto-Registrierung schreibt weiterhin fail-open nach `~/.adw/repos.json`
   im bestehenden Format; ein Registry-Fehler verhindert keinen Run.

### B1 — CLI `adw gui` und Sicherheit (§8)

4. `adw gui` existiert mit dem im Kontrakt festgelegten Interface und startet
   die FastAPI-App über uvicorn auf `--host`/`--port` (default
   `127.0.0.1:8765`). `--repo PATH` (mehrfach) macht die genannten Repos
   zusätzlich zu den Registry-Repos verfügbar. `--open` öffnet den Browser auf
   der lokalen Adresse; ohne `--open` wird kein Browser geöffnet.
5. Ohne `--i-know` bindet die App ausschließlich auf Loopback. Eine
   Nicht-Loopback-`--host`-Adresse wird ohne `--i-know` mit verständlicher
   Fehlermeldung abgelehnt (kein Bind, kein Serverstart); mit `--i-know` wird
   sie zugelassen. Grund: das Log enthält rohe, unredigierte Agent-Ausgaben.
6. Run-Daten liest die App ausschließlich unterhalb des aufgelösten
   `.adw/runs/<run_id>/`-Verzeichnisses; kein anderer Inhalt der Ziel-Repos
   wird gelesen. Davon ausgenommen sind: die Registry-Datei (lesend), reine
   Dateisystem-Metadaten-Prüfungen für Registry-Auflösung und `repo_exists`
   (Existenz/Lesbarkeit von Pfaden, kein Inhalt) sowie die mit dem Paket
   installierten eigenen Ressourcen (Templates, statisches CSS/JS). Die App
   führt kein externes Programm aus, hat keinen Codepfad, der `state.json`,
   das Repo oder das Event-Log schreibt, und stellt keine schreibende
   HTTP-Route bereit.

### B2 — Adressierung und Path-Traversal-Schutz (§7.4)

7. In allen Routen ist `{repo}` der stabile Registry-Slug und wird nie als
   Dateisystempfad interpretiert. Nur Registry-bekannte (bzw. per `--repo`
   hinzugefügte) Repos sind auflösbar; ein unbekannter Slug ergibt `404`.
8. Nur Run-IDs, die `RUN_ID_RE` vollständig erfüllen, werden akzeptiert: formal
   ungültig → `400`, formal gültig aber nicht vorhanden → `404`. Weder Slug
   noch `run_id` können aus `.adw/runs/<run_id>/` herausführen.

### B3 — Run-Liste (§7.2 A)

9. `GET /` und `GET /api/runs` listen die Runs **aller** registrierten (und per
   `--repo` hinzugefügten) Repos mit je Run-ID, Repo, gekürztem Issue, Phase,
   Status, Start, Dauer, Kosten und Event-Zahl. Laufende Runs stehen zuerst.
10. Ein registriertes Repo, dessen Pfad nicht mehr existiert, bleibt als
    **Repo-Platzhalter-Eintrag** sichtbar (Kontrakt: `repo_exists: false`, ohne
    Run-Felder) und ist in der HTML-Liste eindeutig ausgegraut gekennzeichnet.
    Seine Runs sind nicht auflistbar (das Run-Verzeichnis ist unerreichbar,
    persistentes Caching ist deferred); das ist kein Fehler — weder die
    HTML-Liste noch das JSON schlagen fehl, die übrigen Repos bleiben normal
    gelistet.
11. Im Log fehlende Werte (z. B. Kosten in Dry-Runs ohne `usage`) werden als
    leer/`null` dargestellt, nicht als Fehler.

### B4 — Run-Detail (§7.2 B)

12. **Kopf:** die sieben Phasen (spec, plan, build, integration, codex_review,
    final_review, ci) in Workflow-Reihenfolge als Statusleiste, je Phase mit
    Status (erledigt / aktiv / ausstehend / gescheitert) und Dauer, soweit aus
    den Daten bestimmbar.
13. **Links — Trace-Baum:** der Span-Baum aus `adw.gui.model.build_tree`,
    aufklappbar und chronologisch, je Knoten Icon (Status), Label und Dauer;
    bei Loop-Knoten (`round`) zusätzlich `n/cap`. Der Baum wird unverändert aus
    dem Modell übernommen (Waisen-Einhängung nach der Enthaltungsregel bleibt,
    wie das Modell sie liefert — E2; keine eigene Rekonstruktion in der GUI).
14. **Rechts — Detail-Pane**, abhängig vom gewählten Knoten:
    - `agent.run`: genau die Reiter **Prompt** (vollständiger Task-String inkl.
      System-Append), **Answer** (finaler Text plus Zwischen-Assistant-
      Messages) und **Tools** (chronologische Tool-Call-Liste, je Eintrag mit
      vollem Input und vollem Result). Ein **Diff**-Reiter wird in diesem Lauf
      **nicht** gebaut.
    - `gate`: Kommando, Exit-Code und voller Output.
    - `codex.review`: Findings als Tabelle (mindestens Severity, Key, Datei,
      Message) plus rohes `stdout`.
    - `phase` / `lane` / `round`: Aggregation der Kinder (mindestens Dauer,
      Kosten soweit vorhanden, Outcome).
15. Vom Reader gemeldete Probleme (`seq`-Lücken, kaputte Zeilen) werden dem
    Nutzer **sichtbar** gemacht — im Run-Detail-HTML und im Run-Detail-JSON,
    mit der vorhandenen Positions-/Sequenzinformation — nicht verschwiegen.
16. Unbekannte Event-Typen bleiben im Trace erhalten und werden generisch
    dargestellt (Typ-Label + rohes Payload), nie verworfen (§4.2,
    Vorwärtskompatibilität).
17. Der Client nutzt ausschließlich Vanilla JS (`fetch`, natives
    `EventSource`); neue SSE-Events werden inkrementell in die bestehende
    Ansicht übernommen, ohne die Seite neu zu laden (§7.3). Der Client merged
    Stream-Records über die ganzzahlige `seq` und ignoriert Records, die sein
    initialer Snapshot schon abdeckt (keine Dopplung, keine Lücke — auch für
    ein Event, das zwischen Snapshot-Abruf und Stream-Start angehängt wurde).
    `event: problem`-Nachrichten werden ohne Reload in die bestehende
    Problem-Anzeige (AC 15) übernommen. Die ausgelieferten Seiten und Assets
    referenzieren keine externe Ressource.

### B5 — SSE-Live-Stream (§7.3)

18. `GET /api/runs/{repo}/{run_id}/stream` tailt `events.jsonl` per Byte-Offset
    (Poll-Intervall 500 ms, **keine** Filesystem-Watch-Abhängigkeit) und
    schickt jede neue vollständige, vom Reader akzeptierte Zeile mit gültiger
    ganzzahliger `seq` als SSE-Nachricht im Kontrakt-Format (`id:` = `seq`,
    `data:` = Event-Record). Eine unvollständige Schlusszeile wird nicht
    gesendet, sondern beim nächsten Poll erneut geprüft. Kaputte Zeilen und
    Records ohne gültige `seq` werden übersprungen, ohne den Stream zu beenden;
    der Stream sendet für sie eine `event: problem`-Nachricht ohne `id:` (siehe
    Kontrakt), sodass live auftretende Korruption ohne Reload sichtbar wird.
    Der Stream schreibt keinerlei Zustand (Cursor, Cache) auf die Platte.
19. Erstverbindung ohne `Last-Event-ID`: der Stream beginnt am Dateianfang
    (Kontrakt); zusammen mit dem `seq`-Merge des Clients (AC 17) geht kein
    zwischen Snapshot und Stream-Start angehängtes Event verloren und keines
    wird doppelt gerendert. Reconnect über `Last-Event-ID` = letzte `seq`: der
    Server setzt genau nach dieser `seq` fort, ohne Dopplung und ohne Lücke.
20. Ein fertiger Run schließt den Stream nach dem `run`-End-Event; zuvor werden
    alle bis dahin vollständigen Events ausgeliefert. Ein später geöffneter,
    bereits abgeschlossener Run rendert identisch zu einem live beobachteten
    (gleicher Rendering-Pfad).

### B6 — Verpackung (§7.1, E7)

21. FastAPI, uvicorn und Jinja2 sind ausschließlich als optionales Extra
    `adw[gui]` deklariert; das Kernpaket bleibt frei von Web-Abhängigkeiten,
    und der `adw run`-Importpfad importiert keinen dieser Web-Stacks.
22. Eine Installation ohne das `gui`-Extra lässt `adw run` und die übrigen
    Kernkommandos unverändert funktionieren: kein Import-Fehler, keine fehlende
    Abhängigkeit. Ein Test belegt das. Der Aufruf von `adw gui` ohne das Extra
    endet kontrolliert mit einer verständlichen Installationsanweisung für
    `adw[gui]` statt mit einem Traceback.

## Deferred (bewusst nicht gebaut)

Weitergehende Härtungs- oder Erweiterungsideen — auch Befunde aus den
Codex-Review-Runden — gehören hierher, nicht in die Akzeptanzkriterien. Ein
Finding, das einen dieser Punkte oder einen vorentschiedenen Punkt (E1–E7)
einführen will, wird abgewiesen und mit Begründung dokumentiert.

- Timeline, Artefakte-Reiter, Raw-Reiter, Diff-Reiter, Diff-/Artefakt-Endpoint
  (`/diff`, `/artifacts/{name}`) samt `git diff`-Ausführung — Lauf 5.
- i18n/Sprachumschaltung, `--lang`, Einsatz von `adw/gui/i18n.py` — Lauf 5.
- Prunen, Retention, gzip-Reader-Unterstützung, `trace:`-Config-Key,
  `adw runs list`/`prune` — Lauf 5.
- `type`-Filter und Pagination auf `/events` (§7.4 volle Form) — in diesem
  Lauf nur `from_seq`.
- Sortier-/Filter-Bedienelemente und Live-Update der Run-**Liste** (§7.2 A
  "sortable, filter, live-updating") — über „laufende zuerst" hinaus nichts.
- Jegliche Schreib-/Steuerfunktion in der GUI (approve/resume/abort/start);
  Redaction/Maskierung von Secrets im Log (GUI-SPEC §2/§8).
- Authentifizierung, TLS, Mehrbenutzerbetrieb, Remote-Zugriff über die
  Loopback-Bindung hinaus (nur `--i-know` schaltet Nicht-Loopback frei).
- Lazy-Rendering großer Logs (> 200 MB, §9), LRU-/Cross-Prozess-Caching,
  Dateisystem-Watcher statt Polling, persistente Indizes/Tail-Cursor/Caches.
- Neue Schutzmechanismen für Risiken, die vorhandene Backstops
  (Registry-Auflösung, `RUN_ID_RE`, Loopback-Default, Vollzeilen-Lesen,
  sichtbare Reader-Probleme) bereits abdecken.
- Änderungen an `events.py`, `snapshots.py`, `reader.py`, `model.py` oder eine
  Cross-Thread-Parent-API im Emitter (E2) — die Datenschicht wird nur
  konsumiert; eine tatsächlich fehlende Fähigkeit wird als Befund dokumentiert.

## Definition of Done

1. Aufgabe A und B sind gebaut; die Akzeptanzkriterien und die im Kontrakt
   festgelegte öffentliche Fläche (CLI, Routen, Antwortformate, SSE-Format)
   sind erfüllt.
2. Ein vollständiger Testlauf lässt das reale `~/.adw/repos.json` nachweislich
   unverändert (Regressionstest); die Isolation greift für alle Tests, die
   `adw run`/Auto-Registrierung auslösen.
3. Die Web-App ist gegen Fixture-Logs mit FastAPIs `TestClient` getestet:
   Run-Liste (inkl. Repo-Platzhalter für ein fehlendes Repo und
   Laufend-zuerst-Reihenfolge),
   Run-Detail mit Phasen-Kopf, Trace-Baum und Detail-Pane je Knotentyp
   (`agent.run` mit Prompt/Answer/Tools, `gate`, `codex.review`,
   `phase`/`lane`/`round`), sichtbare Reader-Probleme, generische Darstellung
   unbekannter Typen sowie der SSE-Stream (neue Zeilen, unvollständige
   Schlusszeile, Erstverbindung ab Dateianfang — ein zwischen Snapshot-Abruf
   und Stream-Start angehängtes Event erscheint genau einmal, kaputte Zeile
   nach Verbindungsaufbau wird als `event: problem` ohne Stream-Abbruch und
   ohne Reload sichtbar, `Last-Event-ID`-Reconnect, Schließen nach `run`-End).
4. Der Path-Traversal-Schutz ist getestet: unbekannter Slug → `404`, ungültige
   `run_id` → `400`, nicht vorhandene `run_id` → `404`; kein Zugriff außerhalb
   `.adw/runs/<run_id>/`.
5. Ein Test belegt, dass `adw run` ohne das `gui`-Extra unverändert importiert
   und läuft; `adw gui` ohne Extra endet mit der Installationsanweisung.
6. `adw/events.py`, `adw/snapshots.py`, `adw/gui/reader.py` und
   `adw/gui/model.py` sind unverändert; außer der Home-Isolation aus Aufgabe A
   ist der Orchestrator (`phases.py`, Run-Pfad in `cli.py`) nicht verändert.
   Reicht eine eingefrorene API nicht aus, ist das als Befund dokumentiert.
7. Gates grün: `uv run ruff check .` und `uv run pytest -x -q` (`flake8`,
   `isort`, `black` tauchen nirgends auf — E3).
8. Richtwert: rund 20–28 neue Tests für A und B zusammen; maßgeblich ist die
   Abdeckung der Akzeptanzkriterien, nicht die exakte Anzahl.
