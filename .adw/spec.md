# Spec: Trace-Baum und Run-Liste unterscheiden „arbeitet" / „wartet" / „wartet auf Menschen"

## Goal
Die GUI macht drei heute ununterscheidbare Situationen sichtbar auseinander:
ein Span, der **arbeitet** (`running`), ein Span, der **technisch wartet**
(CI-Poll, Gate-Laufzeit → `waiting`), und ein Lauf, der **auf eine menschliche
Freigabe wartet** (Approval-Gate → `awaiting_approval`). `awaiting_approval` ist
der einzige Zustand, in dem der Mensch handeln muss, und wird optisch am
stärksten hervorgehoben. Die Unterscheidung wird ausschließlich aus dem
vorhandenen Event-Log abgeleitet (State-Phase nur als Fallback für Läufe ohne
Trace) — ohne neue Events, Instrumentierung, Routen oder Persistenz.

## Scope (in scope)
- Trace-Baum: laufende `ci.wait`- und `gate`-Spans erhalten den fünften
  Statuswert `waiting`; andere laufende Spans bleiben `running`; beendete Spans
  behalten ihre bisherigen Ergebniszustände.
- Run-Status-Ableitung `awaiting_approval` für `GET /api/runs`,
  `GET /api/runs/{repo}/{run_id}`, die Run-Liste und den Run-Detail-Kopf — auch
  solange der `run`-Span offen ist.
- Phasenleiste: die am Approval-Gate wartende Fachphase (`spec` bzw. `plan`)
  erhält den eigenen Status `awaiting` statt `active`.
- Darstellung: additive CSS-Kennzeichnung der neuen Zustände in
  `adw/gui/static/app.css`; Labels EN+DE in `adw/gui/i18n.py`.
- Doku: `docs/GUI-SPEC.md` / `docs/GUI-SPEC.de.md` (§7.2) und die
  `Unreleased`-Sektion in `CHANGELOG.md` / `CHANGELOG.de.md` synchron nachziehen.

Extern beobachtbare, contract-relevante Fläche (Single-Lane `backend`): die
Statuswerte in den JSON-Antworten von `GET /api/runs` und
`GET /api/runs/{repo}/{run_id}` sowie das beobachtbare Template-Verhalten
(Run-Liste, Detail-Kopf, Phasenleiste, Trace-Baum). Keine internen
Helper-Signaturen, keine Dictionary-Schlüssel jenseits der Antwortfelder, kein
Markup-/CSS-Wortlaut.

## Non-Goals / Scope-Deckel
- Keine neuen Event-Typen, keine Änderung an Instrumentierung oder
  Event-Schema (E1).
- `_WAITING_TYPES` bleibt inhaltlich `{"ci.wait", "gate"}`; keine Erweiterung um
  Agent-/Codex- oder sonstige Arbeitsspans (E2).
- Kein Farbsystem-Refactoring; die bestehende CSS-Statusdarstellung wird
  ergänzt, nicht ersetzt (E3).
- Keine neue Zustandsdatei, kein Persistenz-Zustand (E4).
- Kein Zeitlimit-/Timeout-Alarm, keine Benachrichtigung (E5).
- Keine neue Route, kein neues Tab, keine Filter-Erweiterung der Run-Liste,
  kein neuer Polling-Mechanismus; der SSE-Pfad bleibt unverändert.
- Keine Änderung an Ablauf, Freigabelogik oder Semantik der Approval-Gates.
- Die sieben Fachphasen in `PHASES` bleiben unverändert; die Approval-Phasen
  werden nicht als achte/neunte Phase aufgenommen.
- Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN — Vanilla JS
  und handgeschriebenes CSS.

## Acceptance Criteria

**AC1 — `waiting` im Trace-Baum (A1).**
Ein noch laufender (offener) Span, dessen Typ in `_WAITING_TYPES` liegt
(`ci.wait`, `gate`), trägt im serialisierten Baum den Status `waiting` statt
`running`.
- Ein laufender Span jedes anderen Typs (z. B. `agent.run`, Codex-Spans) bleibt
  `running`.
- Ein beendeter `ci.wait`-/`gate`-Span behält sein bisheriges Ergebnis
  (`gate` → `passed`/`failed`, sonst `done`) — `waiting` gilt nur für den
  offenen Fall.
- `_WAITING_TYPES` bleibt die eine gemeinsame Quelle für Timeline und Baum:
  derselbe offene Span ist in der Timeline `waiting` und im Baum `waiting`,
  die Timeline-Ableitung (`waiting|active`) bleibt unverändert. Es entsteht
  keine zweite, abweichende Liste wartender Typen.

**AC2 — `awaiting_approval` als Run-Status (A2).**
Ein Lauf, dessen `run`-Span offen ist (kein passendes `run`-Ende) und dessen
jüngstes `approval`-Event `event: awaited` ist (kein späteres `granted`),
meldet in `GET /api/runs`, in `GET /api/runs/{repo}/{run_id}`, in der Run-Liste
und im Run-Detail-Kopf den Status `awaiting_approval` statt `running`. Beide
Endpunkte liefern für denselben Lauf denselben Status im vorhandenen
Statusfeld.
- Sobald ein späteres `approval`-Event `granted` folgt, gilt für den weiterhin
  offenen Run wieder `running`. Der Status eines beendeten `run`-Spans kommt
  unverändert aus dessen End-Payload (`done`/`escalated`/`awaiting_approval`);
  die neue Ableitung verändert keine terminalen Run-Ergebnisse.
- Fallback ohne Trace (E4): Ein Lauf ohne Event-Log, dessen State-Phase
  `awaiting_approval` oder `awaiting_spec_approval` ist, meldet ebenfalls
  `awaiting_approval`. Bei vorhandenem Trace ist das Event-Log maßgeblich; die
  State-Phase überschreibt insbesondere kein nachfolgendes `granted`.
- Ein State-only-Lauf außerhalb der beiden Approval-Phasen bleibt wie bisher
  statuslos (kein falsches `running`, kein falsches `awaiting_approval`).

**AC3 — `awaiting` in der Phasenleiste (A3).**
Für einen an einem Approval-Gate pausierten Lauf zeigt die Phasenleiste die
wartende Fachphase mit dem eigenen Status `awaiting` statt `active`:
- Spec-Gate (`approval`-Event `gate: spec`; Fallback State-Phase
  `awaiting_spec_approval`) → Phase `spec` erhält `awaiting`.
- Plan-Gate (`approval`-Event `gate: plan`; Fallback State-Phase
  `awaiting_approval`) → Phase `plan` erhält `awaiting`.
- Die wartende Phase ist dabei nicht zugleich `active`/`pending`/`completed`;
  die übrigen Phasen behalten ihre bisher abgeleiteten Zustände, keine andere
  Phase ist gleichzeitig `active`.
- Nach dem zugehörigen `granted` entfällt `awaiting`; die Leiste folgt wieder
  dem normalen Phasenverlauf.

**AC4 — Darstellung und Labels (A4).**
`waiting` und `awaiting_approval` sind in der Oberfläche klar von `running`
unterscheidbar gekennzeichnet:
- Additive CSS-Darstellung in `adw/gui/static/app.css` für die neuen Zustände
  (Baum-Knoten `waiting`, Run-Status `awaiting_approval`, Phasen-Status
  `awaiting`); die bestehenden Statusklassen bleiben erhalten (E3).
- Labels für die neu sichtbaren Zustände in EN und DE in `adw/gui/i18n.py`,
  sinngleich in beiden Sprachen.
- `awaiting_approval` wird optisch am stärksten hervorgehoben — deutlicher als
  `waiting` und `running`, weil nur dieser Zustand menschliches Handeln
  verlangt.
- Die technischen Statuswerte in den JSON-Antworten bleiben sprachunabhängig
  und werden nicht übersetzt.

## Deferred (bewusst nicht gebaut)
Die folgenden Ideen sind vertretbar, aber unverhältnismäßig zum Issue. Sie
werden NICHT gebaut — auch nicht im Codex-/Fix-Zyklus:
- Wartezeit-Aggregation (Summe/Anteil „wie lange gewartet") je Lauf oder Phase.
- Anzeige „wartet seit X" oder vergleichbare Wartezeit-Metriken.
- Ampel/Alarm „wartet seit X zu lange" (Zeitlimit, Schwellwert, Eskalation).
- Desktop-/Browser-/Push-Notification bei Erreichen eines Approval-Gates.
- Neue Filter, Sortierung, Route oder eigene Übersicht für wartende bzw.
  freigabebedürftige Läufe.

## Definition of Done
- AC1–AC4 erfüllt und durch Tests belegt: `waiting`-Ableitung im Baum,
  unveränderter `running`-Fall für Nicht-Waiting-Spans und beendete
  Gate-/CI-Spans, `awaiting_approval` aus dem Event-Log bei offenem `run`-Span,
  State-Phasen-Fallback ohne Trace, Rückkehr zu `running`/End-Status nach
  `granted`, Phasen-Status `awaiting` für Spec- und Plan-Gate.
- ~10 neue Tests (Richtwert; deutlich mehr als ~15 wäre Scope-Drift), flach
  unter `tests/` als `test_gui_*.py`.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.
- `docs/GUI-SPEC.md` und `docs/GUI-SPEC.de.md` §7.2 synchron aktualisiert:
  `waiting` für offene `ci.wait`-/`gate`-Spans, `awaiting_approval` für Runs
  mit offenem Approval, Phasen-Status `awaiting`, Event-Log-Priorität mit
  State-Fallback, visuelle Priorität von `awaiting_approval`.
- `Unreleased`-Sektion in `CHANGELOG.md` und `CHANGELOG.de.md` synchron
  ergänzt.
- Keine neue Laufzeit-Dependency, kein Frontend-Paket, kein CDN.
- Kein unter „Deferred (bewusst nicht gebaut)" genannter Mechanismus ist
  Bestandteil der Änderung.
