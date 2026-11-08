# Plan — W14: Jeder Lauf bekommt einen Rückblick — und du ein Urteil

Maßgeblich ist `.adw/spec.md` (Scope §1–§9, AC 1–11). Single-Lane-Projekt
(`.adw/config.yaml`): nur der Workstream **backend**; er umfasst Code, Tests, beide
Handbücher und beide Changelogs. Kein Frontend-Workstream, keine Übergabe.

## Ausgangslage

Basis: Stand **0.34.0** (`e4e7522`), am 2026-11-08 gegen den Code geprüft. Vor dem
Bauen gegen den dann aktuellen Stand erneut prüfen.

- `adw/cli.py`: `_EXIT_STATUS` `:144`; `_run_span` `:148` setzt `end_payload` erst im
  `except`/`else` um das `yield` und ruft dort `totals()` neu auf — Kosten eines
  Agentenlaufs, der noch im `with`-Körper läuft, landen also in `totals`.
  `_export_after_execution` `:185`. `_execute(ctx)` im `with _run_span(...)` bei `run`,
  `resume`, `approve`. `resume` kehrt bei Phase `done` vor der Spanne zurück und lehnt
  `escalated` mit `_fail` ab — für einen fertigen Lauf entsteht ohnehin keine neue Spanne.
  `_execute` `:914`: Gate-Pause → `typer.Exit(2)`, Eskalation → `typer.Exit(1)` mit Phase
  `escalated`, `AgentRunError` → `typer.Exit(1)` mit unveränderter Phase. **Exit 1 ist
  mehrdeutig**; ob die Retro läuft, entscheidet die Phase im State.
  `_build_context` `:1124` (Dry-Run: `_dry_run_runners`, sonst
  `SdkAgentRunner(emitter, totals=run_totals)`). `_require_repo` `:580`, `_repo_lock`
  `:1057`, `_fail` `:53`, `ledger_export` `:682`, `kpis` `:776` (`--since`,
  `--repo-slug`, `--json`), `_format_kpis` `:748`.
- `adw/agents.py`: `OPUS = "claude-opus-5-5"` `:23`, `READ_ONLY_TOOLS` `:28`,
  `SCOPED_READ_RULES` `:34`, `REGISTRY`, `final_reviewer` `:346` als Muster.
- `adw/context.py:66` `_agent_run(ctx, agent, task, cwd, …)` erzeugt den
  `agent.run`-Span am Aufrufort (Mock und echter Runner gleich).
- `adw/findings.py`: `FindingsParseError` `:68`, `_last_json_fence_content` `:182`,
  `extract_review_result` `:226` (strikter Parser: letzter ```` ```json ````-Fence, sonst
  ganzer Text; Duplikat-Schlüssel, Nicht-Objekt, Tiefe → `FindingsParseError`).
- `adw/ledger.py`: `_ARTIFACTS` `:38`, `_repo_slug` `:183`, `build_run_record` `:247`
  (`findings[].finding_key`), `_ledger_path` `:315`, `_git_ok` `:341`, `_write_bundle`
  `:364`, `_push` `:390`, `_export` `:402` (Pfad-Wächter, `rmtree` `:419`),
  `_read_ledger_records` `:498`, `compute_kpis` `:607`.
- `adw/mock.py:22` `MockAgentRunner`: `script(agent_name, *responses)`, zeichnet jeden
  Aufruf in `calls` (Agent, Task, `cwd`) auf.
- Tests: autouse `_isolate_ledger`, Fixtures `ledger` (echter Klon mit bare `origin`)
  und `target_repo` (`tests/conftest.py`); Läufe über `--dry-run` mit gepatchtem
  `cli_mod._dry_run_runners` (z. B. `tests/test_cli.py:400`).
- Handbuch: §7 „CLI reference" (`docs/ADW-HANDBOOK.md:350`, DE `:357`), §11.1 „Ledger"
  (`:610`, DE `:625`). Changelog oben `## [0.34.0] — 2026-11-08`.

Betroffene Dateien (abschließend): `adw/agents.py`, neu `adw/retro.py`, `adw/cli.py`,
`adw/ledger.py`, ggf. `adw/findings.py` (nur Herauslösen des JSON-Objekt-Parsers, B2);
neu `tests/test_retro.py`, neu `tests/test_feedback.py`, `tests/test_kpis.py` (neue
Tests + Anpassung der zwei bestehenden exakten `total`-Vergleiche), `tests/test_ledger.py`
(Export-Test); `docs/ADW-HANDBOOK.md`, `docs/ADW-HANDBOOK.de.md`, `CHANGELOG.md`,
`CHANGELOG.de.md`. GUI, `run-record.json`-Schema (Version 1), Phasen/Gates/Exit-Codes,
`adw/dry_run.py` und `pyproject.toml` (bleibt `0.34.0`) unverändert.

Contract: `.adw/contract.yaml` pinnt nur extern beobachtbare Flächen (CLI `adw rate` /
`adw feedback`, `retro.json`, `retro.md`, `feedback.jsonl`, `retro`-Event,
Ledger-Commit-Nachricht, neue `total`-Felder von `adw kpis --json`). Modul-, Helfer-
und Parameternamen in diesem Plan sind Empfehlungen.

## Workstream backend

### B1 — Retro-Agent in der Registry (Scope §1)

`adw/agents.py`, neuer `REGISTRY`-Eintrag nach `final_reviewer`:

```python
"retro_agent": AgentSpec(
    name="retro_agent", model=OPUS, max_turns=60, timeout_s=900,
    tools=READ_ONLY_TOOLS, allowed_tools=SCOPED_READ_RULES,
    system_append=(...), permission_mode="default",
),
```

Rollentext (englisch wie die übrigen, kurz): bewertet einen abgeschlossenen ADW-Lauf
strikt lesend, ändert nichts, antwortet nur mit dem JSON nach Schema, jeder Eintrag
braucht Belege (`seq:<n>` aus `events.jsonl` oder relativer Dateipfad im Run-Ordner).
Kein Effort-Feld. Keine Änderung an Deny-Regeln (`.adw/runs/**` bleibt gesperrt; die
Retro-Dateien schreibt ausschließlich der Orchestrator).

### B2 — Modul `adw/retro.py`: Schema, Beleg-Prüfung, Dateien (Scope §3–§5)

1. **Antwortschema** (pydantic, beide Modelle `model_config = ConfigDict(extra="forbid")`):
   - `RetroItem`: `problem: str`, `resolution: str`, `better: str`,
     `cause: Literal["prompt", "context_missing", "gate", "orchestrator",
     "issue_quality", "model", "target_repo"]`, `lever: str`,
     `evidence: list[str] = []` (fehlender Schlüssel ist kein Schemafehler, sondern
     „keine Belege" → Schritt 3).
   - `RetroAnswer`: `summary: str`, `items: list[RetroItem] = Field(max_length=10)`.
   - Mehr als 10 Einträge, unbekannte Schlüssel, unbekannte `cause`, kein parsebares
     JSON → Fehlschlag nach Schritt 6.
2. **Parsen ohne neuen Parser-Stil:** aus `extract_review_result` den Teil bis
   einschließlich „ist ein dict / nicht zu tief" als `extract_json_object(text) -> dict`
   in `adw/findings.py` herauslösen; `extract_review_result` ruft ihn auf und validiert
   danach wie bisher (Verhalten unverändert, bestehende Tests bleiben grün). `retro.py`:
   `RetroAnswer.model_validate(extract_json_object(text))`. `FindingsParseError` und
   `ValidationError` sind Fehlschläge nach Schritt 6.
3. **Beleg-Prüfung** `_valid_evidence(run_dir, seqs, ev) -> bool`:
   - `re.fullmatch(r"seq:(\d+)", ev)` → gültig, wenn `int(n)` in der Menge der `seq`
     aller parsebaren Zeilen von `run_dir/events.jsonl` (`isinstance(seq, int)`; Datei
     fehlt/unlesbar → leere Menge; unparsebare Zeilen ignorieren). Menge einmal je
     Retro lesen.
   - sonst Pfad: `PurePosixPath(ev)`; ungültig bei leerem String, `is_absolute()` oder
     `".." in parts`; gültig, wenn `(run_dir / ev).is_file()`.
   - Eintrag behalten, wenn mindestens ein Beleg gültig ist; sonst verwerfen
     (inkl. `evidence == []`). `dropped = len(answer.items) - len(kept)`. Behaltene
     Einträge unverändert (alle Belege, wie geliefert). Keine inhaltliche Belegprüfung.
4. **`retro.json`** (Run-Ordner): `{"schema_version": 1, "run_id", "phase", "summary",
   "items": [item.model_dump() …], "dropped", "model": REGISTRY["retro_agent"].model}`,
   `json.dumps(..., sort_keys=True, indent=2) + "\n"`, UTF-8. `phase` ist die Endphase
   `done` oder `escalated`.
5. **`retro.md`** deterministisch aus genau diesem Dict gerendert: `# Retro <run_id>
   (<phase>)`, Zusammenfassung, je Eintrag `## <n>. <problem>` mit allen sechs Feldern
   (Belege als Liste); bei `items: []` eine Zeile „keine Einträge". Kein Agententext
   außer den Feldwerten.
6. **Ablauf** `run_retro(ctx) -> None` (aufgerufen aus B3):
   - Vorbedingungen (sonst sofort `return`, kein Event): `ctx.state.phase in ("done",
     "escalated")`, `not ctx.state.dry_run`, `not (run_dir / "retro.json").exists()`.
     `run_dir = ctx.repo / RUNS_RELPATH / ctx.state.run_id`.
   - `try:` Auftrag = kurzer Einleitungstext + `json.dumps(build_run_record(ctx.repo,
     run_id), ensure_ascii=False, indent=2)` + Hinweis, Details per Grep in
     `events.jsonl` dieses Ordners zu suchen, + Schema-Beschreibung (Felder, erlaubte
     `cause`-Werte, max. 10 Einträge, Belegformen). `result = _agent_run(ctx, spec,
     task, cwd=run_dir)`; parsen, Belege prüfen, `retro.json` schreiben, `retro.md`
     schreiben, dann Punkt-Event `retro` `{"outcome": "written", "items": len(kept)}`
     mit `span=ctx.emitter.current_span_id()`.
   - **Dateien dieses Versuchs verfolgen:** vor dem Schreiben `created: list[Path] = []`;
     unmittelbar vor jedem `write_text` den Zielpfad (`retro.json`, dann `retro.md`)
     anhängen, sodass auch eine teilweise geschriebene Datei erfasst ist.
   - `except Exception as exc:` — in dieser Reihenfolge, jeder Schritt einzeln gegen
     Ausnahmen abgeschirmt, damit nie eine Ausnahme aus `run_retro` herausläuft und
     den `typer.Exit` aus `_execute` ersetzt:
     1. **Bereinigung:** für jeden Pfad in `created` `path.unlink(missing_ok=True)` in
        eigenem `try/except Exception: pass` (beide Dateien oder keine; nur Dateien
        dieses Versuchs, kein Recovery-System). Ein fehlgeschlagenes Löschen wird dem
        `reason` als Zusatz angehängt (z. B. `"; cleanup failed: <name>"`).
     2. Meldung auf stderr (`typer.echo(..., err=True)`) in `try/except Exception: pass`.
     3. Event `retro` `{"outcome": "failed", "reason": str(exc) or type(exc).__name__}`
        in `try/except Exception: pass`.
   - Keine Änderung an `ctx.state`, kein `state.save`.

### B3 — Auslösung in `run`/`resume`/`approve` (Scope §2; AC 1–3, 5)

`adw/cli.py`, an den drei Stellen `_execute(ctx)` ersetzen durch

```python
try:
    _execute(ctx)
finally:
    retro.run_retro(ctx)
```

— innerhalb des `with _run_span(...)`, sodass die Reihenfolge Ausführung → Retro →
Ende der `run`-Spanne (Totals inkl. Retro-Kosten über `RunTotals`) →
`_export_after_execution` → Auto-Prune entsteht. Ein `typer.Exit` aus `_execute`
propagiert unverändert, weil `run_retro` nie wirft; Phase, Exit-Code und
`run`-End-`status` bleiben unverändert. Die Retro nutzt den bestehenden Kontext
(Runner, Emitter, `RunTotals`); `_EXIT_STATUS`, `_run_span`, `_execute`, Export und
Auto-Prune bleiben unverändert. Die bestehende Rückkehr/Ablehnung für fertige Läufe in
`resume` bleibt — kein nachträglicher Retro-Lauf. Kein Eintrag für `retro_agent` in
`adw/dry_run.py` (Dry-Run ruft nie).

### B4 — Export: Retro-Dateien und Erhalt von `feedback.jsonl` (Scope §6; AC 6, 7)

`adw/ledger.py`:
1. `_ARTIFACTS` um `"retro.json"`, `"retro.md"` ergänzen.
2. `_export`: vor `shutil.rmtree(dest)` `kept = (dest / "feedback.jsonl").read_bytes()`
   falls `is_file()`, sonst `None`; nach `_write_bundle(...)` bei `kept is not None`
   `(dest / "feedback.jsonl").write_bytes(kept)`. Keine Kopie im lokalen Run-Ordner.
   Sonst nichts (Commit-Text, Push, `unchanged`-Erkennung, Dry-Run-Skip bleiben).

### B5 — `adw rate` und `adw feedback` (Scope §7; AC 7–9)

Ledger-Seite in `adw/ledger.py`, CLI in `adw/cli.py` (Top-Level-Kommandos).

1. **Ledger-Helfer** (Empfehlung) `ledger_run_dir(repo, run_id) -> (ledger, rel, dest)`:
   `_ledger_path()`; `None` → Fehler „kein Ledger"; `RuntimeError`/`OSError`/
   `subprocess.SubprocessError` → Fehler „Ledger nicht lesbar". `rel = Path("runs") /
   _repo_slug(repo).replace("/", "__") / run_id`; Pfad-Wächter wie `_export`
   (`dest.resolve()` innerhalb `runs/`, nicht gleich `runs/`). `dest.is_dir()` falsch
   → Fehler „Lauf <id> nicht im Ledger (erst exportieren)". Kein `mkdir`.
2. **`append_feedback(ledger, rel, entry, kind)`:** eine Zeile
   `json.dumps(entry, ensure_ascii=False) + "\n"` im Modus `"a"` an
   `dest/feedback.jsonl`; endet eine vorhandene Datei nicht auf `\n`, vorher `\n`
   schreiben (bestehende Zeilen unangetastet). Dann `_git_ok(ledger, "add", "--",
   (rel / "feedback.jsonl").as_posix())`, `_git_ok(ledger, "commit", "-m",
   f"feedback {run_id} {kind}")`, `_push(ledger)` (Ergebnis ignoriert, fail-open:
   Push-Fehler ändert weder Exit 0 noch den lokalen Commit). Ein Fehler bei add/commit
   → Meldung auf stderr, Exit 1 (die Zeile bleibt lokal).
3. **Einträge:** `ts` = `datetime.now(UTC).isoformat()` (enthält `+00:00`), Schlüssel
   genau `ts, kind, value, note` plus `finding_key` nur bei `kind == "finding"`;
   `note` `None` ohne `--note`.
4. **CLI:**
   - `adw rate RUN_ID VALUE [--note] [--repo .]`, `VALUE` als `Enum("good","bad")`
     → Typer lehnt andere Werte mit Exit 2 ab, bevor etwas passiert.
   - `adw feedback RUN_ID VALUE --finding PRÄFIX [--note] [--repo .]`, `VALUE` als
     `Enum("valid","noise")`, `--finding` Pflicht.
   - Beide: `repo = _require_repo(repo)` (wie `ledger export`), dann
     `with _repo_lock(repo):` Ledger-Auflösung (B5.1) → bei Fehler `_fail(...)` (Exit 1,
     nichts geschrieben, kein Ordner, kein Commit).
   - `feedback`: `run-record.json` aus `dest` lesen (unlesbar → `_fail`); Kandidaten =
     **eindeutige** `str`-`finding_key` aus `findings[]`, die mit dem Präfix beginnen
     (wiederholte Vorkommen desselben Keys sind ein Kandidat). Genau einer → voller
     Key; 0 oder > 1 → `_fail` mit Meldung und höchstens den ersten 5 Kandidaten
     (sortiert) plus „…" — nichts geschrieben.
   - Erfolg: kurze Bestätigung auf stdout, Exit 0. Kein Modellaufruf.

### B6 — `adw kpis` um Retro und Feedback (Scope §8; AC 10)

`adw/ledger.py` und `adw/cli.py`.

1. **Lesen** (Empfehlung): `_read_ledger_runs(ledger) -> (records, signals)` über
   denselben sortierten Glob wie `_read_ledger_records` (diese bleibt bzw. ruft den
   neuen Helfer); je Lauf-Ordner `signals[i] = {"retro": <dict|None>, "feedback":
   [<dict> …]}`:
   - `retro.json`: `json.loads`; nur ein Objekt mit `items` als Liste gilt als
     lesbar, sonst `None` (jede Ausnahme → `None`).
   - `feedback.jsonl`: Zeilen einzeln `json.loads`; nur `dict`-Zeilen behalten,
     unlesbare Zeilen überspringen (lesbare weitere Zeilen zählen); unlesbare Datei → `[]`.
2. **`compute_kpis(records, since=None, repo_slug=None, signals=None)`**: `signals`
   parallel zu `records` (fehlend → keine Signale). Die Filterung (`--since`,
   `--repo-slug`, ungültige Records) arbeitet auf Paaren, damit dieselben Läufe zählen.
   Nur in `total` (nicht in `_metrics`, `groups[]` unverändert) zusätzlich:
   - `retro_causes`: `Counter` über `item["cause"]` (nur `dict`-Items mit
     `str`-`cause`) aller lesbaren Retros → `dict`;
   - `retro_runs`: Anzahl Läufe mit lesbarem `retro.json` (auch `items: []`);
   - `ratings`: `{"good": 0, "bad": 0}`; je Lauf die letzte Zeile in Dateireihenfolge
     mit `kind == "rate"` und `value in ("good", "bad")` zählt;
   - `finding_feedback`: `{"valid": 0, "noise": 0}` über alle Zeilen mit
     `kind == "finding"` und `value in ("valid", "noise")`.
   Leeres Ergebnis: `{}`, `0`, Nullzähler. `skipped` (nur ungültige `run-record.json`),
   `candidates`, Gruppen unverändert.
3. **CLI** `kpis`: neuen Lese-Helfer nutzen, `signals` übergeben. `_format_kpis`: direkt
   nach der Tabelle (unter der `gesamt`-Zeile) eine Zeile mit `retro_runs`,
   `retro_causes` (sortierte `k=v`), `ratings`, `finding_feedback` — nur Formatierung,
   Layout nicht gepinnt. Weiterhin keine Schreibzugriffe.

### B7 — Doku und Changelog (Scope §9)

- Beide Handbücher synchron (EN englisch, DE deutsch, inhaltlich gleich), §11.1
  „Ledger": Retro (Auslösung nur bei Endphase `done`/`escalated`, nicht bei Gate-Pause,
  `AgentRunError`, Dry-Run oder vorhandenem `retro.json`; Schema von `retro.json`,
  `cause`-Werte, max. 10 Einträge, Beleg-Regel, `retro.md`, `retro`-Event mit
  `written`/`failed`, Fehlerverhalten ohne Einfluss auf Exit/Phase, Kosten in
  `totals`); `adw rate`/`adw feedback` (Aufruf, `feedback.jsonl`-Zeilenformat,
  Exit-Codes, Ledger-Voraussetzung „erst exportieren", Commit-Nachricht, Push
  fail-open, Überleben beim Re-Export); `adw kpis`-Felder `retro_causes`,
  `retro_runs`, `ratings` („letzte Bewertung je Lauf zählt"), `finding_feedback`.
  §7 um `adw rate` und `adw feedback` mit Verweis auf §11.1.
- `CHANGELOG.md`/`.de.md`: oberste Sektion `## [0.35.0] — Unreleased` mit
  `### Added` (Retro-Agent, `adw rate`, `adw feedback`, neue `kpis`-Felder), im Stil
  der vorhandenen Einträge. `pyproject.toml` unverändert.

## Reihenfolge

B1 → B2 → B3 → B4 → B5 → B6 → B7. Jeden neuen Test zuerst rot sehen.

## Tests (Richtwert 12–16 neue Tests; geplant 14)

Hermetik: kein echtes Modell, kein `~/.adw/ledger` (`_isolate_ledger` autouse, für
Ledger-Tests die bestehende Fixture `ledger`). CLI über `typer.testing.CliRunner`.

**Seam für Läufe mit Retro** (Retro läuft nie im Dry-Run): Test-Helfer in
`tests/test_retro.py`, der `cli_mod._build_context` per `monkeypatch` durch eine
Funktion ersetzt, die für einen **Nicht**-Dry-Run-State denselben Kontext wie der
Dry-Run baut (`dry.config(config)`, `cli_mod._dry_run_runners()` mit zusätzlich
`agents.script("retro_agent", <Antwort>)` bzw. einem Runner, der für `retro_agent`
`AgentRunError` wirft, `dry.glab`/`dry.gh`, `sleep=lambda s: None`, übergebener
`emitter`). Lauf mit `adw run --issue … --gates none --repo <target_repo>`. Aufrufe
(Agent, `cwd`) über `MockAgentRunner.calls` prüfen; den Runner dafür im Helfer
festhalten. Eskalation/`AgentRunError`/Gate-Pause wie in den bestehenden
`tests/test_cli.py`-Tests über gepatchte Runner bzw. `--gates plan` erzeugen. Reicht
ein schmalerer Seam (z. B. direkter Aufruf von `retro.run_retro(ctx)` innerhalb einer
echten `run`-Spanne) für einzelne Fälle, ist das zulässig — AC 1–3 und 5 müssen aber
über den CLI-Pfad laufen.

| AC | Test (Kurzbeschreibung) | Ort |
|----|-------------------------|-----|
| 1 | Lauf bis `done`: `retro_agent` genau einmal, `cwd` = Run-Ordner; `retro.json` mit allen §5-Schlüsseln (`schema_version 1`, `phase "done"`, `model == OPUS`), `retro.md` vorhanden und enthält alle Feldwerte; `agent.run`-Span der Retro hat `parent` = `run`-Span-ID und liegt vor dessen End-Event; Event `retro` `{"outcome": "written", "items": n}` | `tests/test_retro.py` |
| 2 | Eskalierter Lauf → Retro mit `phase "escalated"`, Exit 1 (der `typer.Exit` propagiert) | dto. |
| 2 | Parametrisiert: Gate-Pause (Exit 2) und `AgentRunError` im Build (Exit 1, Phase bleibt) → kein `retro_agent`-Aufruf, kein `retro.json` | dto. |
| 3 | Parametrisiert: Retro wirft `AgentRunError` / liefert Nicht-JSON / 11 Einträge / unbekannte `cause` / Zusatzschlüssel / `retro.md`-Schreiben scheitert nach teilweisem Schreiben (gepatchter Renderer bzw. `write_text`, `retro.json` liegt schon) / Schreiben scheitert **und** `unlink` wirft (gepatcht) → Exit-Code und Phase wie beim Lauf ohne Retro-Fehler, `run`-End-`status` unverändert, Event `retro` `failed` mit `reason` (im Unlink-Fall mit Bereinigungs-Zusatz), stderr-Meldung, weder `retro.json` noch `retro.md` vorhanden (im Unlink-Fall: Lauf endet trotzdem normal) | dto. |
| 3 | Eskalierter Lauf mit fehlschlagender Retro → Exit 1, Phase `escalated`, `status "escalated"` | dto. |
| 4 | Beleg-Prüfung direkt: Einträge mit existierender/nicht existierender `seq`, existierendem/nicht existentem/absolutem/`..`-Pfad, ohne Belege, gemischt (ein gültiger reicht) → richtige Einträge behalten, `dropped` stimmt; alle verworfen → `items: []`, Event `written` mit `items 0` | dto. |
| 5 | Dry-Run bis `done` → kein `retro_agent`-Aufruf, kein `retro.json` | dto. |
| §2 | Vorhandenes `retro.json` → kein zweiter Aufruf, Datei byte-gleich (z. B. Lauf bis Plan-Gate, `retro.json` vorlegen, `approve` bis `done`) | dto. |
| 6 | Export eines Laufs mit `retro.json`/`retro.md` im Run-Ordner → beide im Ledger-Bündel | `tests/test_ledger.py` |
| 7 | `adw rate <id> bad --note x` nach Export: genau eine Zeile mit `ts` (UTC), `kind "rate"`, `value "bad"`, `note "x"`, ohne `finding_key`; Ledger-HEAD-Nachricht `feedback <id> rate`; erneuter Export → `feedback.jsonl` byte-gleich; zweites `rate` hängt an (zwei Zeilen) | `tests/test_feedback.py` |
| 8 | `adw feedback` mit eindeutigem Präfix → voller `finding_key`, `kind "finding"`, Commit `feedback <id> finding`; parametrisiert mehrdeutig/unbekannt → Exit 1, `feedback.jsonl` nicht angelegt bzw. unverändert, kein neuer Commit | dto. |
| 9 | Parametrisiert: Lauf nicht exportiert / kein Ledger → Exit 1, kein Lauf-Ordner, keine Datei, kein Commit; ungültiger Wert (`rate … meh`) → Exit ≠ 0, nichts geschrieben | dto. |
| 10 | Fixture-Ledger (Läufe mit Retro inkl. `items: []`, ohne Retro/Feedback, mehrfache `rate`-Zeilen good→bad, `finding`-Zeilen) → `--json` `total.retro_causes`, `retro_runs`, `ratings` (letzte zählt), `finding_feedback` exakt; Textausgabe enthält die Werte; `groups[]`-Schlüssel unverändert | `tests/test_kpis.py` |
| 10 | Unlesbares `retro.json` und kaputte `feedback.jsonl`-Zeile (lesbare Zeilen daneben zählen) → ignoriert, Exit 0, `skipped` unverändert | dto. |
| 11 | `uv run ruff check .`, `uv run pytest -x -q` | Gate |

Bestehende Tests in `tests/test_kpis.py`, die `total` exakt vergleichen
(`test_metrics_per_version_and_total_match_hand_computed_values`,
`test_empty_ledger_gives_empty_result_without_invented_values`), um die vier neuen
Schlüssel mit Leerwerten ergänzen — keine neuen Tests. Keine weiteren Tests (keine
Matrix über Rollentext, `retro.md`-Layout, Zeitzonen, Push-Fehler oder einzelne
Feldtypen).

## Gates / Definition of Done

- `uv run ruff check .` und `uv run pytest -x -q` grün (in Build-Sandboxes ggf. direkt
  `.venv/bin/python -m pytest -x -q`); bestehende Suite grün.
- AC 1–11 durch Tests belegt; kein Test ruft ein echtes Modell oder berührt
  `~/.adw/ledger`.
- Retro ändert nie Exit-Code, Phase oder `run`-End-`status`; keine neue Dependency.
- Handbücher und Changelogs EN/DE synchron; `pyproject.toml` unverändert; GUI,
  `run-record.json`-Schema und Kontrollfluss unverändert.

## Deferred (bewusst nicht gebaut — bindet auch den Review-Loop)

- Wochenschleife `adw improve` (W15), Gruppierung/Clustering der Retros.
- Retro bei Gate-Pause, Retro nachträglich für alte Läufe, Retro neu erzeugen
  (Überschreiben eines vorhandenen `retro.json`).
- Effort für den Retro-Agenten (W2).
- Feedback in der GUI, Feedback ohne Ledger (z. B. im Run-Ordner).
- Neue Spalten pro Version in `adw kpis`.
- Validierung, dass Belege inhaltlich zum Eintrag passen; Prüfung über Existenz von
  `seq` bzw. Datei hinaus.
- Bearbeiten oder Löschen von Feedback-Zeilen, Deduplizierung von Feedback.
- Retry-Queues, persistente Abschlussmarker oder ein Transaktions-/Recovery-System für
  Retro oder Ledger-Schreibvorgänge. Agentenlimits (W1), Events, Repo-Lock und der
  fail-open Export sind die vorgesehenen Backstops.
