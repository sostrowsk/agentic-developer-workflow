# Plan: Dry-Run-Läufe in der GUI unverwechselbar kennzeichnen

Single-Lane-Projekt: es gibt nur den Workstream **backend**, keinen
Frontend-Lane. Die HTML-, CSS- und i18n-Anpassungen gehören zum
Backend-Workstream, da die GUI serverseitig gerendert wird und weder ein
separates Frontend-Paket noch einen eigenen Build-Schritt besitzt.

Gebaut wird strikt gegen `.adw/contract.yaml`. Der Contract pinnt nur die
extern beobachtbare Fläche: das neue Antwortfeld `dry_run` in beiden
Run-Endpunkten samt Default-Verhalten, die sichtbare Dry-Run-Kennzeichnung auf
beiden HTML-Routen (im Detail viewport-persistent) und die Reihenfolgezusage
der Run-Liste. Interne Helper-Signaturen und konkreter Markup-/CSS-Wortlaut
sind NICHT Teil des Contracts.

Ausgangspunkte im Code (im Ist geprüft; die Zeilenangaben des Issues sind
teilweise veraltet):
- `_run_start_payload` schreibt `dry_run` bereits ins `run`-Start-Payload
  (`adw/cli.py:251`) — Event-Schema bleibt unangetastet (E1).
- `_summary()` (`adw/gui/app.py:420`) liest heute `issue`, aber nicht
  `dry_run`; liefert das gemeinsame Run-Datensatz-Dict für Liste UND Detail
  (`_run_detail` app.py:1105 → `"run": _summary(...)` app.py:1115,
  `_list_runs` app.py:1202). Ein einziger Ort ⇒ beide Endpunkte liefern
  denselben Wert (AC1-Konsistenz).
- Run-Liste-Sortierung: `adw/gui/app.py:1245`
  (`entries.sort(key=lambda e: 0 if e.get("status") == "running" else 1)`),
  dokumentiert als „Running runs first" (`docs/GUI-SPEC.md:329`).
- HTML: Run-Zeile in `adw/gui/templates/run_list.html`; Detail-Kopf
  `adw/gui/templates/run_detail.html:198` (`<header class="run-header">`).
- i18n-Katalog `adw/gui/i18n.py` (`_EN`/`_DE`, identische Key-Sets).

Die Quelle für `dry_run` bleibt ausschließlich das vorhandene Feld im
Start-Payload des `run`-Spans. Event-Schema, Instrumentierung und Orchestrator
bleiben unverändert.

## Workstream: backend

### B1 — `dry_run` in `_summary()` (AC1, A1, E1, E4)
- In `_summary()` (`adw/gui/app.py:420`) aus dem `run`-Start-Payload das Feld
  `dry_run` lesen und als `bool` in das zurückgegebene Datensatz-Dict
  aufnehmen.
- Quelle ausschließlich das `run`-Start-Payload. Fehlt der `run`-Span oder das
  Feld, ist der Wert `false` — nie ein Fehler, keine Ableitung aus fehlenden
  `usage`-/Token-/Totals-Daten.
- Kein neuer Parameter, kein neuer Datenpfad; kein State-Fallback (anders als
  bei `issue`): `dry_run` gibt es nur im Log.
- Beide Endpunkte (`GET /api/runs`, `GET /api/runs/{repo}/{run_id}`) erben das
  Feld automatisch, da `_list_runs` und `_run_detail` dasselbe `_summary()`
  verwenden — für denselben Event-Log-Inhalt derselbe Wert.

### B2 — Statuspriorität der Run-Liste (AC4, A5, E2)
- Sortierschlüssel in `_list_runs` (`adw/gui/app.py:1245`) auf drei Gruppen
  erweitern: `awaiting_approval` (0) vor `running` (1) vor allen übrigen (2).
- Die vorgelagerte Sortierung „neueste zuerst" bleibt unverändert; weil
  Pythons `sort` stabil ist, bleibt sie innerhalb jeder Gruppe erhalten.
- `dry_run` fließt NICHT in Gruppe oder Reihenfolge ein (E2). Keine
  konfigurierbare Sortier- oder Filteroption.
- Den „Running runs first"-Kommentar an der Sortierstelle an die neue
  Reihenfolge anpassen.

### B3 — Dry-Run-Labels in i18n (A4)
- In `adw/gui/i18n.py` je einen kurzen Label-Key für EN und DE ergänzen
  (identische Key-Sets in `_EN`/`_DE`, non-empty). Beide Kataloge enthalten
  den Key und liefern ihn je nach gewählter Sprache; die gerenderten Werte
  müssen nicht verschieden sein (ein konventionelles Kurzlabel wie „Dry Run"
  darf in beiden Sprachen identisch lauten).
- Es ist ein kurzes Label (kein übersetzter Fließtext) und folgt der gewählten
  Sprache.

### B4 — Kennzeichnung in der Run-Liste (AC2, A2)
- In `adw/gui/templates/run_list.html` für Run-Zeilen mit `dry_run: true` ein
  deutlich sichtbares kurzes Dry-Run-Label rendern; bei `dry_run: false` keine
  Kennzeichnung.
- Label aus i18n (B3), sprachabhängig. Die Kennzeichnung bleibt unabhängig von
  Token-/`usage`-Daten sichtbar und benötigt keine zusätzliche Datenzeile.
- Kein neuer Status, keine Auswirkung auf Retention, Auswahlverhalten oder
  Tabellenstruktur. Ggf. minimales CSS in `adw/gui/static/app.css`.

### B5 — Viewport-persistente Kennzeichnung im Run-Detail (AC3, A3)
- Im Detail-Kopf (`adw/gui/templates/run_detail.html`, `run-header` ab :198)
  für `dry_run: true` eine nicht wegklickbare Kennzeichnung mit
  Banner-Charakter ergänzen, die beim Scrollen im Trace-Baum im Viewport
  sichtbar bleibt (viewport-persistent, z. B. sticky Kopf/Banner via
  `adw/gui/static/app.css`). Ein statischer Hinweis nur am Dokumentanfang
  erfüllt AC3 nicht.
- Nutzt dasselbe kurze i18n-Label (B3). Bei `dry_run: false` keine
  Kennzeichnung. Der Banner hängt ausschließlich am Feld `dry_run`, nicht an
  Token-, `usage`- oder Inhaltsdaten.
- Die Darstellungsstruktur laufender und fertiger Läufe bleibt identisch
  (GUI-SPEC §2); nur das Dry-Run-Chrome kommt hinzu.

### B6 — Doku & Changelog
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` synchron (§7.2): Feld `dry_run`
  samt Default `false`, Kennzeichnung in Run-Liste und Run-Detail (samt
  Persistenz im Detail), neue Reihenfolge `awaiting_approval` vor `running`
  vor dem Rest.
- Bisherige „Running runs first"-Zusage (`docs/GUI-SPEC.md:329` und dt.
  Pendant) entsprechend anpassen; „neueste zuerst" innerhalb der Gruppen
  festhalten.
- `Unreleased`-Sektionen in `CHANGELOG.md` und `CHANGELOG.de.md` synchron um
  Dry-Run-Kennzeichnung und neue Statuspriorität ergänzen.

## Tests (unter `tests/`, `test_gui_*.py`) — Richtwert ~8, > ~13 = Scope-Drift
- AC1: `dry_run: true` → `true`; explizites `false` → `false`; fehlendes Feld
  → `false`; fehlender `run`-Span → `false`; Listen- und Detail-Endpunkt
  liefern für denselben Event-Log denselben Wert; fehlende `usage`-/Token-Daten
  ändern den Wert nicht.
- AC2: Run-Zeile mit `dry_run: true` trägt das sprachabhängige Label;
  `dry_run: false` trägt keine Kennzeichnung.
- AC3: Detail-Kopf eines `dry_run: true` zeigt die Kennzeichnung; ein Test
  belegt den Mechanismus der Viewport-Persistenz (ohne konkreten
  Markup-/CSS-Wortlaut vorzuschreiben); `false` zeigt keine Kennzeichnung;
  fehlende Token-Daten bleiben zulässig.
- AC4 (~2 Tests): `awaiting_approval` vor `running` vor Rest; innerhalb jeder
  Gruppe „neueste zuerst"; `dry_run` beeinflusst die Reihenfolge nicht.
- Nahe verwandte Fälle dürfen in einer Testfunktion zusammengefasst werden.
- AC5 wird NICHT durch eigene neue Tests belegt, sondern über den
  Änderungsumfang (Review) und die bestehende Regressionssuite (892 Tests).

## Gates (Definition of Done)
- AC1–AC4 durch die Tests oben belegt; AC5 über Änderungsumfang und
  Regressionssuite (Timeline, Artifacts, Raw und SSE unverändert).
- `uv run ruff check .` grün.
- `uv run pytest -x -q` grün.
- EN/DE-Doku und Changelog-Einträge synchron.
- Keine neue Laufzeit-Dependency, kein CDN, keine neue Route, kein neues Tab,
  kein neues Event, keine neue Persistenz, keine Konfigurationsoption.
- Kein unter „Deferred (bewusst nicht gebaut)" genannter Mechanismus ist
  Bestandteil der Änderung.

## Deferred (bewusst nicht gebaut — bindet auch den Review-/Fix-Zyklus)
Die folgenden Ideen sind vertretbar, aber unverhältnismäßig zum Issue. Sie
werden nicht gebaut — auch nicht im Codex-/Fix-Zyklus:
- Filterung, Sortierung oder Gruppierung der Run-Liste nach `dry_run`.
- Vergleichsansicht zwischen einem Dry-Run und einem echten Lauf.
- Eigene Retention-Klasse oder andere Aufbewahrungsregeln für Dry-Runs,
  automatisches Ausblenden oder Pruning.
- Eigene Route, eigenes Tab oder separate Dry-Run-Übersicht.
- Heuristische Dry-Run-Erkennung anhand fehlender `usage`, fehlender Tokens
  oder auffällig kurzer Traces (ausdrücklich ausgeschlossen durch E4).
