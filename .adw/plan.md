# Plan: `waiting` / `awaiting_approval` / `awaiting` sichtbar unterscheiden

Single-Lane-Projekt (`backend`). Es gibt nur den Workstream **backend**; kein
Frontend-Lane. Die GUI-Änderungen (CSS, Templates, i18n) liegen ganz im
Backend-Workstream, da sie serverseitig gerendert bzw. als Datenattribute
ausgeliefert werden — kein separater Build-Schritt, kein Frontend-Paket.

Gebaut wird strikt gegen `.adw/contract.yaml`: die drei observablen
`status`-Wertebereiche (`RunStatus`, `PhaseStatus`, `NodeStatus`) und die
Ableitungsregeln R1–R4. Interne Helper-Signaturen sind bewusst nicht gepinnt und
dürfen frei umgesetzt werden.

Betroffene Dateien (Ist-Stand geprüft):
- `adw/gui/app.py` — `_node_status()` (Z. 550), `_summary()` (Z. 373),
  `_phase_bar()` (Z. 408), `_WAITING_TYPES` (Z. 831, bleibt inhaltlich gleich).
- `adw/gui/static/app.css` — additive Statusklassen.
- `adw/gui/i18n.py` — Labels EN+DE (`_EN`/`_DE`, identische Schlüsselmenge).
- `adw/gui/templates/run_detail.html`, `run_list.html` — nur soweit nötig, damit
  die neuen Statuswerte ihre CSS-Klasse/Label erhalten (kein Umbau).
- `docs/GUI-SPEC.md` / `docs/GUI-SPEC.de.md` §7.2; `CHANGELOG.md` /
  `CHANGELOG.de.md` (`Unreleased`).

Kontext (nicht neu zu erfinden): das `approval`-Event trägt
`{gate: spec|plan, event: awaited|granted}` (cli.py:480/721), am `run`-Span-Id.
Die State-Phasen `awaiting_approval` / `awaiting_spec_approval` kommen aus
`phases.py`. `_summary()` liefert `awaiting_approval` heute schon als *terminalen*
Wert aus dem End-Payload — neu ist nur die Ableitung bei **offenem** `run`-Span.

---

## Workstream: backend

### B1 — `waiting` im Trace-Baum (AC1 / Contract R1)
`_node_status()` liefert für einen **offenen** Span, dessen Typ in
`_WAITING_TYPES` (`ci.wait`, `gate`) liegt, `waiting` statt `running`.
- Jeder andere offene Span (`agent.run`, Codex-Spans, `run`, `phase`, `lane`,
  `round`) bleibt `running`.
- Ein **beendeter** `ci.wait`/`gate`-Span behält sein bisheriges Ergebnis
  (`gate` → `passed`/`failed`, sonst `done`) — `waiting` nur im offenen Fall.
- `_WAITING_TYPES` bleibt die eine gemeinsame Quelle für Timeline und Baum; die
  Timeline-Ableitung (`_timeline`, `state: waiting|active`) wird nicht
  angefasst. Keine zweite Liste wartender Typen.
- Der Node trägt den Wert im vorhandenen `status`-Feld der Baum-Serialisierung
  (`_serialize`, app.py:712) — kein neues Feld. Nicht-Span-Knoten bleiben
  statuslos (`null`).

### B2 — `awaiting_approval` als Run-Status (AC2 / Contract R2)
Run-Status-Ableitung in `_summary()` (wirkt zugleich auf `GET /api/runs`,
`GET /api/runs/{repo}/{run_id}`, Run-Liste und Detail-Kopf, da beide über
`_summary` laufen).
- Bei **offenem** `run`-Span (heute → `running`): ist das jüngste
  `approval`-Event `event: awaited` ohne späteres `event: granted`, dann
  `awaiting_approval` statt `running`. Folgt ein späteres `granted`, wieder
  `running`.
- Ein **beendeter** `run`-Span behält den Status aus dem End-Payload
  (`done`/`escalated`/`awaiting_approval`) unverändert — keine terminale
  Ableitung wird angefasst.
- Fallback **ohne Trace** (E4): kein Event-Log, State-Phase
  `awaiting_approval` oder `awaiting_spec_approval` → `awaiting_approval`. Bei
  vorhandenem Trace ist das Event-Log maßgeblich; die State-Phase überschreibt
  kein nachfolgendes `granted`.
- State-only-Lauf außerhalb der beiden Approval-Phasen bleibt statuslos
  (`null`) — kein falsches `running`/`awaiting_approval`.
- Beide Endpunkte liefern denselben Wert im vorhandenen `status`-Feld.
- Regressionshinweis (kein eigener Schritt, nicht Teil des Contracts):
  GUI-SPEC §7.2 A dokumentiert „Running runs first" (docs/GUI-SPEC.md:329),
  implementiert in app.py:1173 über `status == "running"`. Beim Umstellen des
  Statuswerts das bestehende dokumentierte Verhalten nicht brechen — keine
  neue Sortier- oder Filteroption einführen.

### B3 — `awaiting` in der Phasenleiste (AC3 / Contract R3)
`_phase_bar()` gibt für einen an einem Approval-Gate pausierten Lauf der
wartenden Fachphase den Status `awaiting` statt `active`:
- Spec-Gate (`approval`-Event `gate: spec`; Fallback State-Phase
  `awaiting_spec_approval`) → Phase `spec` = `awaiting`.
- Plan-Gate (`approval`-Event `gate: plan`; Fallback State-Phase
  `awaiting_approval`) → Phase `plan` = `awaiting`.
- Die wartende Phase ist nicht zugleich `active`/`pending`/`completed`; keine
  andere Phase ist gleichzeitig `active`; übrige Phasen behalten ihre bisher
  abgeleiteten Zustände. Nach `granted` entfällt `awaiting`.
- `PHASES` bleibt bei den sieben Fachphasen — keine achte/neunte Phase.

### B4 — Darstellung und Labels (AC4 / Contract R4)
- Additive CSS-Klassen in `adw/gui/static/app.css` für Baum-Knoten `waiting`,
  Run-Status `awaiting_approval`, Phasen-Status `awaiting`. Bestehende
  Statusklassen bleiben (E3, kein Farbsystem-Refactoring).
- `awaiting_approval` optisch am stärksten hervorgehoben — deutlicher als
  `waiting`/`running`, weil nur dieser Zustand menschliches Handeln verlangt.
- Labels für die neu sichtbaren Zustände in `adw/gui/i18n.py`, EN+DE,
  sinngleich, identische Schlüsselmenge in beiden Katalogen.
- Templates (`run_list.html`, `run_detail.html`) nur so anpassen, dass die neuen
  Statuswerte ihre Klasse/ihr Label erhalten (Statusicon, `phase-{{ ph.status }}`,
  `node-{{ node.status }}`); kein Umbau, kein neuer Markup-Wortlaut über das
  Nötige hinaus.
- Die technischen JSON-Statuswerte bleiben sprachneutral und werden nicht
  übersetzt. Kein neues Client-Polling, keine Navigation; SSE-Pfad unverändert.

### B5 — Tests
Flach unter `tests/` als `test_gui_*.py`, **~10 neue Tests** (deutlich >~15 wäre
Scope-Drift). Abdeckung entlang AC1–AC4:
- Baum: offener `ci.wait` → `waiting`; offener `gate` → `waiting`; offener
  `agent.run` → weiterhin `running`; beendeter `gate` → `passed`/`failed`;
  beendeter `ci.wait` → `done`; derselbe offene Waiting-Span ist in Timeline
  (`waiting`) und Baum (`waiting`) konsistent.
- Run-Status: offener `run` + `approval awaited` → `awaiting_approval`;
  späteres `granted` → wieder `running`; beendeter `run` → End-Payload-Status
  unverändert; State-Fallback ohne Trace (`awaiting_spec_approval` /
  `awaiting_approval`) → `awaiting_approval`; State-only außerhalb der
  Approval-Phasen → `null`; beide Endpunkte gleicher Wert.
- Phasenleiste: Spec-Gate → `spec` = `awaiting`; Plan-Gate → `plan` =
  `awaiting`; nach `granted` normaler Verlauf; keine zweite `active`-Phase.
- Labels: die neuen i18n-Schlüssel existieren in EN und DE (sofern nicht schon
  durch den bestehenden Katalog-Paritätstest abgedeckt).
(Nah verwandte Fälle dürfen in einer Testfunktion zusammengefasst werden, um im
Richtwert zu bleiben.) Keine Tests für Deferred-Mechanismen.

### B6 — Doku & Changelog
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` §7.2 synchron: `waiting` für
  offene `ci.wait`/`gate`-Spans, `awaiting_approval` für Runs mit offenem
  Approval, Phasen-Status `awaiting`, Event-Log-Priorität mit State-Fallback,
  visuelle Priorität von `awaiting_approval`.
- `Unreleased`-Sektion in `CHANGELOG.md` und `CHANGELOG.de.md` synchron
  ergänzen.

---

## Gates (Definition of Done)
- `uv run ruff check .` grün.
- `uv run pytest -x -q` grün.
- AC1–AC4 durch die Tests aus B5 belegt; beide Endpunkte liefern für denselben
  Lauf denselben Status.
- Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN — Vanilla JS,
  handgeschriebenes CSS.
- Keine neuen Events, Routen, Persistenz, Fachphasen, Filter oder
  Polling-Mechanismen; Approval-Ablauf und -Semantik unverändert.
- Kein unter „Deferred" genannter Mechanismus ist Teil der Änderung.

## Deferred (bewusst nicht gebaut) — unverändert aus der Spec, bindet auch den Review-/Fix-Zyklus
- Wartezeit-Aggregation (Summe/Anteil „wie lange gewartet") je Lauf oder Phase.
- Anzeige „wartet seit X" oder vergleichbare Wartezeit-Metriken.
- Ampel/Alarm „wartet seit X zu lange" (Zeitlimit, Schwellwert, Eskalation).
- Desktop-/Browser-/Push-Notification bei Erreichen eines Approval-Gates.
- Neue Filter, Sortierung, Route oder eigene Übersicht für wartende bzw.
  freigabebedürftige Läufe.
