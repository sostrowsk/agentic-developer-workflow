# Spec: Dry-Run-Läufe in der GUI unverwechselbar kennzeichnen

## Goal
Dry-Runs sind in der Run-Liste und im Run-Detail jederzeit eindeutig als Simulation
erkennbar und können nicht mit echten, lediglich inhalts- oder verbrauchsarmen Läufen
verwechselt werden. Die Kennzeichnung stützt sich ausschließlich auf das bereits im
Start-Payload des `run`-Spans vorhandene Feld `dry_run` (`_run_start_payload`,
`adw/cli.py:251`); am Event-Schema, an der Instrumentierung und am Orchestrator
ändert sich nichts.

Zusätzlich priorisiert die Run-Liste Läufe, die auf eine menschliche Freigabe warten
(`awaiting_approval`), vor laufenden und übrigen Läufen, damit der aktuell
handlungsbedürftige Lauf nicht nach unten rutscht (A5).

## Scope
- `_summary()` liefert das zusätzliche Feld `dry_run` (bool) aus dem
  `run`-Start-Payload; fehlt der `run`-Span, ist es `false`.
- `GET /api/runs` und `GET /api/runs/{repo}/{run_id}` liefern im bestehenden
  Run-Datensatz zusätzlich `dry_run` als boolesches Feld.
- Die HTML-Run-Liste zeigt für jeden Dry-Run ein deutlich sichtbares kurzes Label
  in der zugehörigen Zeile.
- Der HTML-Run-Detail-Kopf zeigt für Dry-Runs eine durchgehende, nicht wegklickbare
  Kennzeichnung mit Banner-Charakter.
- Kurze Dry-Run-Labels für Englisch und Deutsch in `adw/gui/i18n.py`.
- Die Run-Liste gruppiert Status in der Reihenfolge `awaiting_approval`, danach
  `running`, danach alle übrigen Statuswerte; innerhalb jeder Gruppe bleibt die
  bestehende Sortierung „neueste zuerst" erhalten.
- Doku: `docs/GUI-SPEC.md` + `docs/GUI-SPEC.de.md` (§7.2 sowie Anpassung der
  bisherigen „Running runs first"-Zusage, `docs/GUI-SPEC.md:329`) und die
  `Unreleased`-Sektion in `CHANGELOG.md` + `CHANGELOG.de.md`.

Extern beobachtbare, contract-relevante Fläche (Single-Lane `backend`): das neue
Antwortfeld `dry_run` in beiden Run-Endpunkten inklusive Default-Verhalten, die
sichtbare Dry-Run-Kennzeichnung auf beiden HTML-Flächen und die
Reihenfolgezusage der Run-Liste. Interne Helper-Signaturen sowie konkreter
Markup- oder CSS-Wortlaut sind nicht Teil des Contracts.

## Non-Goals / Scope-Deckel
- Keine Änderung an Orchestrator, Instrumentierung oder Event-Schema; `dry_run`
  wird ausschließlich aus dem vorhandenen Start-Payload gelesen (E1).
- Kein Filter und keine Sortierung nach `dry_run`; keine konfigurierbare Sortier-
  oder Filteroption. Die Sortieränderung aus A5 betrifft ausschließlich die
  Status-Gruppierung (E2).
- Kein Ausblenden, kein Auto-Pruning, keine Sonderbehandlung von Dry-Runs in der
  Retention (E3).
- Keine Heuristik, die einen Dry-Run aus fehlenden `usage`- oder Token-Daten
  ableitet (E4).
- Keine neue Route, kein neues Tab; keine Änderung an Timeline, Artifacts, Raw
  oder SSE.
- Kein neuer Persistenz-Zustand, keine Konfigurationsoption, keine neue
  Laufzeit-Dependency, kein CDN.
- Die Darstellungsstruktur laufender und fertiger Läufe bleibt identisch
  (GUI-SPEC §2); nur das Dry-Run-Chrome kommt hinzu.

## Acceptance Criteria

**AC1 — `dry_run` in den Run-Antworten (A1, E1, E4).**
Jeder Run-Datensatz aus `GET /api/runs` und `GET /api/runs/{repo}/{run_id}`
enthält das boolesche Feld `dry_run`.
- Steht im Start-Payload des `run`-Spans `dry_run: true`, ist das Antwortfeld
  `true`; steht dort `dry_run: false`, ist es `false`.
- Fehlt das Feld oder fehlt der `run`-Span vollständig (ältere Läufe), ist das
  Antwortfeld `false` — niemals ein Fehler.
- Listen- und Detail-Endpunkt liefern für denselben Event-Log-Inhalt denselben Wert.
- Fehlende `usage`- oder Token-Daten beeinflussen den Wert nicht.

**AC2 — sichtbare Kennzeichnung in der Run-Liste (A2, A4).**
Eine Run-Zeile mit `dry_run: true` trägt ein deutlich sichtbares, kurzes
Dry-Run-Label; dadurch bleibt ein inhaltsarmer Dry-Run auch ohne Token-Zeile als
Simulation erkennbar.
- Eine Run-Zeile mit `dry_run: false` erhält keine Dry-Run-Kennzeichnung.
- Das Label kommt aus `adw/gui/i18n.py` (EN/DE) und folgt der gewählten Sprache.
- Die Kennzeichnung verändert weder Status noch Retention oder Auswahlverhalten.

**AC3 — dauerhafte Kennzeichnung im Run-Detail (A3, A4).**
Das Run-Detail eines Laufs mit `dry_run: true` zeigt im Kopf eine nicht
wegklickbare Kennzeichnung mit Banner-Charakter, die beim Scrollen im Trace-Baum
im Viewport sichtbar bleibt (viewport-persistent, z. B. als sticky Kopf oder
sticky Banner). Ein statischer Hinweis, der nur am Dokumentanfang steht und beim
Scrollen aus dem Sichtbereich verschwindet, erfüllt dieses Kriterium nicht.
- Ein Test belegt die Viewport-Persistenz der Kennzeichnung (den Mechanismus, mit
  dem sie beim Scrollen sichtbar bleibt), ohne konkreten Markup- oder
  CSS-Wortlaut vorzuschreiben.
- Sie verwendet das kurze, sprachabhängige Dry-Run-Label aus `adw/gui/i18n.py`.
- Bei `dry_run: false` wird keine Dry-Run-Kennzeichnung angezeigt.
- Fehlende Token-/`usage`-Daten bleiben wie bisher zulässig; die Kennzeichnung
  erfordert dafür keine zusätzliche Datenzeile.

**AC4 — Statuspriorität der Run-Liste (A5).**
Die Run-Liste ordnet Läufe nach den Statusgruppen `awaiting_approval` vor
`running` vor allen übrigen Statuswerten.
- Ein Lauf mit `awaiting_approval` steht vor jedem Lauf mit `running` und vor
  jedem Lauf der übrigen Gruppe; ein Lauf mit `running` vor jedem der übrigen.
- Innerhalb jeder Gruppe bleibt die bestehende Sortierung „neueste zuerst" erhalten.
- `dry_run` beeinflusst weder die Gruppe noch die Reihenfolge innerhalb der Gruppe.

**AC5 — bestehende Flächen bleiben unverändert (E1–E3, Scope-Deckel).**
Die Ergänzung beschränkt sich auf das neue Antwortfeld, die sichtbaren
Kennzeichnungen und die festgelegte Statusgruppierung.
- Timeline, Artifacts, Raw und SSE behalten Verhalten und Schnittstellen.
- Für Dry-Runs entstehen keine gespeicherten Zustände, Retention-Regeln, Filter
  oder Sortierregeln.
- AC5 ist eine Implementierungsschranke und wird über den Änderungsumfang (Review)
  und die bestehende Regressionssuite geprüft — es sind keine eigenen neuen Tests
  für unberührte Teilsysteme erforderlich.

## Definition of Done
- AC1–AC4 sind durch Tests unter `tests/` (`test_gui_*.py`) belegt, insbesondere:
  `dry_run: true`, explizites `false`, fehlendes Feld und fehlender `run`-Span;
  konsistentes Listen-/Detailverhalten; Kennzeichnung und Nicht-Kennzeichnung
  beider HTML-Flächen; die Viewport-Persistenz der Detail-Kennzeichnung; die
  Statusreihenfolge einschließlich „neueste zuerst" innerhalb der Gruppen.
- AC5 wird nicht durch eigene neue Tests belegt, sondern über den
  Änderungsumfang und die bestehende Regressionssuite (892 Tests) geprüft.
- Richtwert ~8 neue Tests (davon ~2 für die Sortierung); mehr als ~13 gilt als
  Scope-Drift.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` dokumentieren synchron (§7.2) das
  Feld `dry_run` samt Default `false`, die Kennzeichnung in Run-Liste und
  Run-Detail sowie die neue Reihenfolge `awaiting_approval` vor `running` vor dem
  Rest; die bisherige „Running runs first"-Zusage (`docs/GUI-SPEC.md:329`) ist
  entsprechend angepasst.
- Die `Unreleased`-Sektionen in `CHANGELOG.md` und `CHANGELOG.de.md` sind synchron
  ergänzt.
- Keine neue Laufzeit-Dependency, kein CDN.
- Kein unter „Deferred (bewusst nicht gebaut)" genannter Mechanismus ist
  Bestandteil der Änderung.

## Deferred (bewusst nicht gebaut)
Die folgenden Ideen sind vertretbar, aber unverhältnismäßig zum Issue. Sie werden
nicht gebaut — auch nicht im Codex-/Fix-Zyklus:
- Filterung, Sortierung oder Gruppierung der Run-Liste nach `dry_run`.
- Vergleichsansicht zwischen einem Dry-Run und einem echten Lauf.
- Eigene Retention-Klasse oder andere Aufbewahrungsregeln für Dry-Runs,
  automatisches Ausblenden oder Pruning.
- Eigene Route, eigenes Tab oder separate Dry-Run-Übersicht.
- Heuristische Dry-Run-Erkennung anhand fehlender `usage`, fehlender Tokens oder
  auffällig kurzer Traces (ausdrücklich ausgeschlossen durch E4).
