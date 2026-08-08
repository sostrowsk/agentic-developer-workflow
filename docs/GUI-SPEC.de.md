# ADW Run Inspector — Spezifikation

[English](GUI-SPEC.md) | **Deutsch**

Status: Entwurf · Ziel-Release: 0.4.0 · Verwandt: [`SPEC.de.md`](SPEC.de.md), [`PLAN.de.md`](PLAN.de.md)

Eine lokale Web-GUI, die **jeden einzelnen Schritt jedes ADW-Runs** sichtbar
macht — live während der Run läuft und hinterher zur Forensik. Zweck: Debugging
(warum ist dieser Run eskaliert, wo oszilliert der Loop) und Optimierung
(welcher Prompt hat welches Verhalten erzeugt, wo gehen Zeit und Geld hin).

---

## 1. Problem

Der Orchestrator ist heute eine Blackbox. Die Beobachtbarkeit besteht aus:

| Quelle | Was sie liefert | Was fehlt |
| --- | --- | --- |
| `state.json` | Aktuelle Phase, Zähler, offenes Feedback | Nur Snapshot — wird bei **jedem Save überschrieben**, keine Historie |
| `spec.md`, `plan.md`, `contract.yaml`, `issue.md` | Endergebnis-Artefakte | Nicht der Weg dorthin |
| `escalation.md` | Grund des Abbruchs | Nur im Fehlerfall, nur der Endzustand |
| `followups.md` | Zurückgestellte Findings | — |
| stdout von `adw run` | Grober Fortschritt | Nicht persistiert, nicht strukturiert |

Es fehlt entscheidend:

- **Agent-Transkripte.** `agents.py:SdkAgentRunner._collect()` konsumiert den
  SDK-Message-Stream im RAM und gibt nur `AgentResult(text, session_id)` zurück.
  Jeder Tool-Call, jede Zwischen-Message, alle Token-/Kosten-Daten werden
  verworfen.
- **Prompts.** Die in `phases.py` zusammengebauten Task-Strings (inklusive der
  Content-Rules aus `_SPEC_CONTENT_RULES` etc. und dem System-Prompt-Append)
  existieren nur für die Dauer des Aufrufs.
- **Gate-Output.** `GateFailure.output` lebt im Prozess; nur eine gekürzte Form
  erreicht den nächsten Fix-Task.
- **Eine Zeitachse.** Nichts trägt einen Zeitstempel. Dauern, Wartezeiten,
  Parallelität der Lanes sind nicht rekonstruierbar.
- **Zwischenstände im Code.** Agents committen nicht — der Orchestrator committet
  einmal je Lane am Ende. Was ein einzelner Agent-Lauf verändert hat,
  hinterlässt in git keine Spur.

Deshalb ist dieses Feature zu rund 70 % **Instrumentierung des Orchestrators**
und zu 30 % Viewer.

## 2. Ziel und Nicht-Ziele

### Ziel

1. Jeder ADW-Run schreibt ein vollständiges, strukturiertes **Event-Log** — bis
   hinunter zum einzelnen Tool-Call eines einzelnen Agents.
2. `adw gui` startet eine lokale Web-App, die dieses Log rendert: Run-Liste,
   Run-Detail mit Phasen-Landkarte + Trace-Baum + Detail-Pane, Timeline,
   Artefakte.
3. Live-Ansicht eines laufenden Runs, identisches Rendering für einen fertigen.
4. Vier Debug-Fragen sind ohne Blick in den Quellcode beantwortbar:
   *Wo hakt der Loop? Was hat der Agent gesehen? Wo gehen Zeit und Geld hin?
   Was hat jeder Schritt am Code geändert?*

### Nicht-Ziele (v1, bewusst zurückgestellt)

- **Steuerung aus der GUI.** Kein approve/resume/abort/start — v1 ist read-only.
  Begründung: kein Schreibpfad in State oder Repo heißt, die GUI kann einen Run
  nicht beschädigen.
- **Cross-Run-Statistiken** (Fehlerquoten je Gate, Kostentrends,
  Finding-Kategorien). Braucht erst einen Korpus an Runs.
- **A/B-Vergleich zweier Runs** für Prompt-Optimierung.
- **Redaction** von Secrets im Log (explizite Entscheidung, siehe §9).
- **Remote-/Mehrbenutzer-Betrieb.** Bindet auf Loopback, keine Auth, kein TLS.
- **Ablösung von `adw status`.** Die CLI bleibt die primäre Steuerfläche.

## 3. Architektur

Drei trennbare Teile:

```
adw/
  events.py          NEU  Emitter: append-only JSONL, Span-IDs, fail-open
  snapshots.py       NEU  Tree-Snapshots je Schritt als git-Refs (Diff-Basis)
  gui/               NEU  read-only Web-App
    __init__.py
    app.py                FastAPI-App-Factory
    reader.py             JSONL-Reader (tail-fähig, Byte-Offset-basiert)
    model.py              Events -> Span-Baum (pydantic)
    registry.py           ~/.adw/repos.json — welche Repos angezeigt werden
    i18n.py               de/en-Label-Wörterbuch
    templates/*.html      Jinja2
    static/*              vendored htmx + eigenes CSS (KEIN CDN)
  phases.py          MOD  ~40-60 emit()-Aufrufe
  agents.py          MOD  SDK-Stream wird ins Log gespiegelt
  gates.py           MOD  Gate-Start/Ende inkl. vollem Output
  codex.py           MOD  Review-Start/Ende inkl. rohem stdout
  cli.py             MOD  neue Kommandos: gui, runs prune
```

Datenfluss:

```
adw run ──emit()──> .adw/runs/<run_id>/events.jsonl   (append-only, 0600)
        └─snapshot─> refs/adw/<run_id>/<seq>          (git-Objekte für Diffs)

adw gui ──lesen───> Registry ~/.adw/repos.json
        ──tail────> events.jsonl  (Poll per Byte-Offset)
        ──git─────> Diff zwischen Snapshot-Refs
        ──SSE─────> Browser
```

Der Orchestrator spricht nie mit der GUI, die GUI schreibt nie in den Run. Die
einzige Schnittstelle ist das Dateiformat aus §4.

## 4. Event-Log

### 4.1 Datei

- Pfad: `.adw/runs/<run_id>/events.jsonl` im **Ziel-Repo**, neben `state.json`.
- Format: JSON Lines, UTF-8, ein Objekt je Zeile, `\n`-terminiert,
  **append-only** — eine bestehende Zeile wird nie verändert.
- Rechte: `0600` (wie `state.json` heute).
- `ensure_runs_gitignored(repo)` (existiert bereits in `worktrees.py`, schreibt
  `.adw/runs/.gitignore` mit `*`) MUSS vor dem ersten Write laufen.

### 4.2 Record-Schema

```jsonc
{
  "seq": 412,                     // monoton je Run, lückenlos
  "ts": "2026-08-05T14:02:20.117Z",// UTC, Millisekunden
  "type": "agent.tool.call",      // siehe §4.4
  "kind": "point",                // "start" | "end" | "point"
  "span": "01J9…",                // ID des Spans, zu dem das Event gehört
  "parent": "01J9…",              // Eltern-Span; null beim Run
  "phase": "build",               // RunState.Phase zum Emit-Zeitpunkt, oder null
  "lane": "backend",              // Lane-Name oder null
  "round": 2,                     // Loop-Runde oder null
  "payload": { }                  // typspezifisch, siehe §4.4
}
```

Regeln:

- `seq` vergibt der Emitter unter dem Datei-Lock, lückenlos — eine Lücke
  bedeutet abgeschnittenes/korruptes Log, und die GUI sagt das, statt still
  etwas Falsches zu rendern.
- `kind: "start"` öffnet einen Span, `kind: "end"` schließt ihn (gleiche
  `span`). Dauer = Differenz der `ts`. `point`-Events haben keine Dauer.
- Unbekannte `type`-Werte MUSS die GUI generisch rendern (Icon + rohes Payload)
  statt sie zu verwerfen oder daran zu scheitern — das Format bleibt
  vorwärtskompatibel.

### 4.3 Schreiben: Locking, Reihenfolge, Fail-open

- Parallele Lanes laufen als Threads in einem Prozess (`phases.py` nutzt
  `threading`), ein `resume` startet aber einen neuen Prozess. Der Emitter
  serialisiert deshalb über ein **`fcntl.flock` auf `events.jsonl`** (analog zu
  `state._repo_lock`): `open("a")` → `LOCK_EX` → `seq` vergeben → `write` →
  `flush` → unlock.
- `flush()` ja, `fsync()` **nein** — ein fsync je Event würde die Laufzeit eines
  Runs dominieren. Trade-off: ein harter Crash kann die letzten ungeflushten
  Zeilen verlieren. Akzeptiert: das Log ist ein Debug-Artefakt, `state.json`
  bleibt die Resume-Wahrheit.
- Leser parsen ausschließlich **vollständige Zeilen** (mit `\n` terminiert).
  Eine angeschnittene letzte Zeile wird ignoriert und beim nächsten Poll erneut
  gelesen.
- **Fail-open ist Pflicht.** Jeder Emitter-Aufruf ist so gekapselt, dass keine
  Exception (Disk voll, Rechte, Encoding) den Orchestrator erreicht. Im
  Fehlerfall: **einmal pro Run und Prozess** `logger.warning`, danach
  schweigt jeder Emitter dieses Runs für den Rest des Prozesses. Der
  Prozess-Scope ist Absicht — prozessübergreifend wäre die Garantie nur mit
  persistentem Sidecar-Zustand durchsetzbar, den §4.1 verbietet; ein `resume`
  in einem frischen Prozess darf also erneut einmal warnen. **Ein kaputtes
  Event-Log darf nie einen Run abbrechen.** Das ist die wichtigste Invariante
  des ganzen Features.

### 4.4 Event-Typen

Span-bildend (`start`/`end`):

| `type` | Payload (start) | Payload (end) |
| --- | --- | --- |
| `run` | `issue`, `parallel`, `dry_run`, `repo`, `base_branch`, `adw_version`, `lanes[]` | `status` (`done`\|`escalated`\|`awaiting_approval`), `totals` (Dauer, Kosten, Tokens) |
| `phase` | `name`, `from_phase` | `name`, `to_phase` |
| `lane` | `name`, `branch`, `worktree`, `base_sha`, `ports` | `completed`, `gate_iterations`, `fix_cycles` |
| `round` | `loop` (`authoring`\|`gates`\|`integration`\|`codex_review`\|`final_review`), `n`, `cap` | `outcome` |
| `agent.run` | `agent`, `model`, `tools[]`, `allowed_tools[]`, `cwd`, `resume_session`, **`prompt`** (voller Task-String), `system_append` | `session_id`, **`result_text`**, `usage` (`input`, `output`, `cache_read`, `cache_creation`), `cost_usd`, `is_error` |
| `gate` | `name`, `cmd`, `timeout`, `cwd` | `passed`, `exit_code`, `timed_out`, **`output`** |
| `codex.review` | `kind`, `argv[]`, `cwd`, `custom_prompt` | `findings[]` (vollständige `Finding`-Objekte), `raw_stdout`, `parse_ok` |
| `codex.author` | `kind`, `argv[]`, `cwd`, `task` | `artifacts[]` (zurückgelieferte Dateinamen), `raw_stdout`, `parse_ok` — Dual Authoring ist ein erheblicher Zeit- und Kostenblock; ohne diesen Span zeigt die Timeline dort eine Lücke, wo der Codex-Entwurf entstand |
| `ci.wait` | `provider`, `pipeline_ref` | `status`, `polls`, `duration` |

Punkt-Events:

| `type` | Payload |
| --- | --- |
| `agent.message` | `role`, `text` (Assistant-Text-Block) |
| `agent.tool.call` | `tool`, `tool_use_id`, `input` |
| `agent.tool.result` | `tool_use_id`, `is_error`, `content` |
| `snapshot` | `lane`, `tree`, `ref`, `label` (`before_agent`\|`after_agent`\|`after_gates`\|`red`) |
| `red.check` | `confirmed`, `test_paths[]`, `gates[]` |
| `commit` | `lane`, `sha`, `subject` |
| `merge` | `lane`, `target`, `conflicts[]` |
| `ci.poll` | `provider`, `status`, `job` |
| `ci.reentry` | `n`, `reason` |
| `triage.decision` | `finding_key`, `severity`, `action`, `reason` |
| `limit.hit` | `limit`, `value`, `cap` |
| `circuit_breaker` | `keys[]`, `scope` |
| `escalation` | `reason`, `phase` |
| `approval` | `gate` (`spec`\|`plan`), `event` (`awaited`\|`granted`) |
| `artifact` | `name`, `path`, `bytes`, `sha256` |
| `followup` | `finding_key`, `text` |
| `state.saved` | `seq` (RunState.seq), `phase` |
| `log` | `level`, `message` (Orchestrator-Warnungen, z. B. aus `logger.warning`) |

Der `agent.run`-Start trägt den **vollständigen Prompt**, `agent.message` /
`agent.tool.*` den vollen Stream, das `agent.run`-Ende den finalen Text. Das ist
die „Was hat der Agent gesehen?"-Ansicht in voller Tiefe.

### 4.5 Retention

Roher Mitschnitt ohne Kappung (Entscheidung, §9) heißt: das Log wächst mit dem
Volumen der Tool-Ausgaben. Gegenmittel ist ausschließlich Retention:

- Neue CLI: `adw runs prune [--repo PATH] [--keep N] [--older-than DAYS] [--gzip]`
  - Default `--keep 20` je Repo (älteste Runs zuerst)
  - `--gzip` komprimiert `events.jsonl` zu `events.jsonl.gz` statt zu löschen
    (der Reader behandelt beides transparent)
  - Prune löscht auch die zugehörigen Snapshot-Refs (`refs/adw/<run_id>/*`)
- Optionales automatisches Prune nach erfolgreichem Run, konfigurierbar in
  `.adw/config.yaml`:

  ```yaml
  trace:
    enabled: true      # Default true; false = gar kein Event-Log
    keep_runs: 20      # 0 = nie automatisch prunen
  ```

- `adw runs list` zeigt Run-ID, Phase, Datum, Event-Zahl und Log-Größe — damit
  sichtbar ist, wann Prune fällig wird.

## 5. Snapshots und Schritt-Diffs

Damit „was hat dieser Schritt geändert?" beantwortbar wird, wird der
Worktree-Stand an jeder Schrittgrenze festgehalten:

1. `snapshots.capture(ctx, worktree, label)` baut — exakt wie das existierende
   `phases.py:_worktree_tree_hash()` — über einen temporären Index
   (`read-tree HEAD` → `add -A` → `write-tree`) ein **Tree-Objekt**.
2. `git commit-tree <tree> -p <base_sha> -m "adw snapshot <label>"` macht daraus
   einen Commit, `git update-ref refs/adw/<run_id>/<seq> <commit>` hält ihn
   gegen `git gc` am Leben.
3. Das `snapshot`-Event speichert `tree` und `ref`.

Schritt-Diff in der GUI = `git diff <ref_vorher> <ref_nachher>` — lazy auf
Anfrage berechnet, also **kein Patch-Text im Event-Log** und auch Monate später
noch exakt. Snapshot-Punkte: vor und nach jedem Agent-Lauf, nach dem
TDD-RED-Test-Only-Lauf, nach jeder Gate-Iteration.

Kosten: ein `write-tree` + `commit-tree` je Grenze (beides billig, kein
Worktree-Write). Ein fehlgeschlagener Snapshot ist fail-open wie jedes Emit —
die GUI zeigt für diesen Schritt dann „kein Diff verfügbar".

## 6. Instrumentierungs-Punkte

Explizite `emit()`-Aufrufe (Entscheidung), fail-open, keine Magie:

| Datei | Stelle | Events |
| --- | --- | --- |
| `cli.py` | `run`/`resume`/`approve`: sobald Run-Identität und Emitter existieren, bis zu jedem Kommando-Ausgang | `run` start/end, `approval` |
| `phases.py` | Eintritt/Austritt jeder Phasen-Funktion | `phase` start/end |
| `phases.py` | `_reviewed_authoring_loop` | `round`, `codex.review`, `artifact` |
| `phases.py` | jede `ctx.agents.run(...)`- / `ctx.codex.review(...)`-Aufrufstelle, inkl. `_draft_stage`, `_claude_draft`, `_codex_draft` | `agent.run`, `codex.review` (die **Spans** liegen hier, nicht in den Runnern — nur so erzeugen auch Mock-Runner sie und der Dry-Run bleibt ein brauchbarer Abnahmepfad), `artifact` (Dual Authoring: beide Entwürfe und die Synthese sind einzeln sichtbar) |
| `phases.py` | `_run_lane`, `_run_lane_gates` | `lane`, `round`, `snapshot`, `commit` |
| `phases.py` | `_confirm_red`, `_run_test_only_pass`, `_require_red_tests` | `red.check`, `snapshot` |
| `phases.py` | `escalate()`, Limit- und Circuit-Breaker-Prüfungen | `escalation`, `limit.hit`, `circuit_breaker` |
| `phases.py` | Integration/Merge, `_record_followup` | `merge`, `followup` |
| `agents.py` | `SdkAgentRunner.run` / `_collect` | der **Inhalt** des von der Aufrufstelle geöffneten `agent.run`-Spans: `agent.message`, `agent.tool.call`, `agent.tool.result`, dazu Usage/Kosten in dessen End-Payload. Mock-Runner steuern hier nichts bei — zu Recht, sie haben keine Tool-Calls |
| `gates.py` | `run_gates` je Gate | `gate` start/end |
| `codex.py` | Review-Subprozess | `codex.review` start/end |
| `triage.py` | Entscheidungsfunktion | `triage.decision` |
| `ci.py` / `github.py` | Poll-Schleife | `ci.wait`, `ci.poll`, `ci.reentry` |
| `state.py` | `save`/`update` | `state.saved` |

`agents.py` ist der tiefste Eingriff: `_collect()` extrahiert heute nur `text`
und `session_id`. Dort kommt je Message ein Zweig dazu, der `ToolUseBlock`,
`ToolResultBlock`, `AssistantMessage.usage`, `ResultMessage.total_cost_usd` und
`model_usage` ins Log spiegelt. Vertrag: der Rückgabewert von `_collect` bleibt
bit-identisch — die Instrumentierung darf das Verhalten nicht ändern. Das wird
regressionsgetestet.

## 7. Die Web-App

### 7.1 Start und Registry

```
adw gui [--repo PATH]... [--host 127.0.0.1] [--port 8765] [--open] [--lang de|en]
```

- Bindet nur auf Loopback. Ein `--host` mit Nicht-Loopback-Adresse verlangt ein
  explizites `--i-know`-Flag — das Log enthält rohe Agent-Ausgaben (§9).
- Repos kommen aus `~/.adw/repos.json`; **jedes `adw run` registriert sein Repo
  dort automatisch** (Pfad + Last-seen-Zeitstempel). `--repo` ergänzt ad hoc.
  Nicht mehr existierende Repos werden ausgegraut angezeigt und bringen die App
  nie zum Absturz.
- Stack: FastAPI + uvicorn + Jinja2 als optionales Extra `adw[gui]` — eine reine
  `adw run`-Installation bleibt frei von Web-Abhängigkeiten.
- **Kein CDN — und gar kein Frontend-Fremdasset.** Vanilla JS (`fetch`,
  natives `EventSource` für SSE), handgeschriebenes CSS, System-Fonts.
  Eine Bibliothek zu vendoren hiesse, sie einmal aus dem Netz zu holen, und
  gebraucht wird hier keine: der ganze Client ist eine Run-Liste, ein
  aufklappbarer Baum, ein Detail-Pane und ein Event-Stream. Keine Lieferkette,
  nichts nachzuziehen.

### 7.2 Views

**A — Run-Liste (`/`)**

Tabelle über alle registrierten Repos: Run-ID · Repo · Issue (gekürzt) · Phase ·
Status (läuft / wartet auf Approval / fertig / eskaliert) · Start · Dauer ·
Kosten · Event-Zahl. Sortierbar, Filter nach Repo und Status. Laufende Runs
zuerst, live aktualisiert.

**B — Run-Detail (`/runs/{repo}/{run_id}`)** — die Hauptansicht:

```
┌─ Run 1789dbd5 · leasing · ● running · 12:04 ─────────────┐
│ [Spec✓][Plan✓][Build◐][Integr][Codex][Final][CI]        │
├──────────────────────┬───────────────────────────────────┤
│ ▾ Build         4:12 │ Agent: builder (opus-4.8)         │
│   ▾ lane backend     │ Runde 2/10 · 1.203s · 84k tok     │
│     ▾ Runde 1        │                                   │
│       ✓ RED          │ [Prompt][Antwort][Tools][Diff]    │
│       ▸ agent   2:01 │ ───────────────────────────────── │
│       ✗ gate lint    │ 14:02:11 Read  models.py          │
│     ▸ Runde 2 ●      │ 14:02:14 Edit  models.py          │
│   ▸ lane frontend    │ 14:02:20 Bash  pytest -q          │
│                      │          → exit 1, 3 failed       │
└──────────────────────┴───────────────────────────────────┘
```

1. **Phasen-Landkarte** (Kopf): die sieben Phasen als Statusleiste — erledigt /
   aktiv / ausstehend / gescheitert, mit jeweiliger Dauer. Klick scrollt den
   Baum zu dieser Phase. Das ist die Orientierungsebene; sie spiegelt den
   Flowchart aus `docs/adw-flowchart.excalidraw`.
2. **Trace-Baum** (links): der Span-Baum aus §4.2, aufklappbar, chronologisch.
   Je Knoten: Icon (Status), Label, Dauer, bei Loops `n/cap`. Lanes sind
   Geschwister — Parallelität ist als zwei offene Äste sichtbar. Auto-Scroll zum
   aktiven Knoten im Live-Modus (abschaltbar).
3. **Detail-Pane** (rechts): abhängig vom gewählten Knoten. Für `agent.run` vier
   Reiter:
   - **Prompt** — der vollständige Task-String plus System-Append, Monospace,
     kopierbar (der Hebel für Prompt-Optimierung).
   - **Antwort** — finaler Text plus alle Zwischen-Assistant-Messages.
   - **Tools** — chronologische Tool-Call-Liste, jeder aufklappbar auf vollen
     Input und volles Ergebnis.
   - **Diff** — `git diff` zwischen den Snapshot-Refs, die diesen Schritt
     klammern, syntaxhervorgehoben, mit `+/-`-Zahlen je Datei.

   Für `gate`: Kommando, Exit-Code, voller Output. Für `codex.review`: Findings
   als Tabelle (Severity, Key, Datei, Message) plus rohes stdout. Für `phase` /
   `lane` / `round`: Aggregation der Kinder (Dauer, Kosten, Ergebnis).

4. **Reiter auf Run-Ebene**: `Trace` (Default) · `Timeline` · `Artefakte` · `Raw`.
   - **Timeline**: horizontale Swimlanes (Orchestrator, Spec, Plan, je Lane,
     Codex, CI) als CSS-Balken — aktiv vs. wartend (CI-Polling, Gate-Laufzeit)
     unterschiedlich dargestellt. Beantwortet „wo geht die Zeit hin". Der Kopf
     zeigt Gesamtdauer, Gesamtkosten, Tokens je Modell.
   - **Artefakte**: `issue.md`, `spec.md`, `plan.md`, `contract.yaml`,
     `escalation.md`, `followups.md`, die Entwürfe aus dem Dual Authoring — als
     Markdown gerendert, die Entwürfe nebeneinander gegen die Synthese.
   - **Raw**: das Event-Log als filterbare JSON-Liste — der Fallback, der immer
     funktioniert, auch für Event-Typen, die die GUI noch nicht kennt.

### 7.3 Live-Update

- `GET /api/runs/{repo}/{run_id}/stream` — SSE. Der Server tailt `events.jsonl`
  per Byte-Offset (Poll-Intervall 500 ms; keine Filesystem-Watch-Abhängigkeit)
  und schickt jede neue vollständige Zeile als Event.
- Der Client patcht den Baum inkrementell; die GUI rendert nie die ganze Seite
  neu. Reconnect über `Last-Event-ID` = letzte `seq`.
- Ein fertiger Run schließt den Stream nach dem `run`-Ende. Eine später
  geöffnete GUI merkt keinen Unterschied — gleicher Renderpfad.

### 7.4 API

| Endpoint | Zweck |
| --- | --- |
| `GET /api/runs` | Run-Liste (JSON) |
| `GET /api/runs/{repo}/{run_id}` | Metadaten + Span-Baum |
| `GET /api/runs/{repo}/{run_id}/events?from_seq=N&type=…` | Roh-Events, paginiert |
| `GET /api/runs/{repo}/{run_id}/stream` | SSE-Live-Tail |
| `GET /api/runs/{repo}/{run_id}/diff?from=REF&to=REF` | Schritt-Diff |
| `GET /api/runs/{repo}/{run_id}/artifacts/{name}` | Artefakt-Inhalt |

`{repo}` ist ein stabiler Slug aus der Registry, nie ein roher Dateisystempfad
in der URL. Path Traversal ist ausgeschlossen: nur Registry-bekannte Repos, nur
Run-IDs nach `RUN_ID_RE`, nur eine Whitelist von Artefaktnamen.

### 7.5 i18n

`adw/gui/i18n.py` hält ein `dict[str, dict[str, str]]` für `de` und `en`.
Label-Auswahl: `?lang=` → Cookie → `Accept-Language` → `en`. Übersetzt wird nur
das UI-Gerüst; Inhalte (Prompts, Ausgaben, Findings) werden nie angefasst. Der
Sprachumschalter ist ein Link im Kopf, ohne Verlust des Seitenzustands.

## 8. Sicherheit und Datenschutz

Konsequenz der Entscheidung „roher Mitschnitt, keine Redaction":

- `events.jsonl` enthält **ungeschwärzte** Agent-Ausgaben — Bash-Outputs,
  Dateiinhalte aus `Read`, Environment-Ausschnitte. Wenn ein Secret je für einen
  Agent sichtbar war, liegt es danach auf der Platte.
- Gegenmaßnahmen (alle verbindlich):
  1. `0600` auf `events.jsonl` — dieselbe Haltung wie bei `state.json`.
  2. `ensure_runs_gitignored()` vor dem ersten Write; zusätzlich eine Prüfung
     beim Run-Start, dass `.adw/runs/.gitignore` existiert und `*` enthält.
     Schlägt sie fehl → Warnung ins Log und auf stdout.
  3. Die GUI bindet auf Loopback; Nicht-Loopback verlangt ein explizites
     Opt-in-Flag.
  4. `docs/` und README sagen deutlich, dass Run-Verzeichnisse nicht geteilt
     werden dürfen.
- Die GUI öffnet **keine** Datei außerhalb von `.adw/runs/<run_id>/` und führt
  genau ein externes Programm aus: `git diff` auf dem Ziel-Repo, mit
  `core.hooksPath=/dev/null` und `safe_env()` — dieselben Schutzmaßnahmen, die
  der Orchestrator bereits benutzt.
- Read-only: der GUI-Prozess hat keinen Codepfad, der `state.json`, das Repo
  oder das Event-Log schreibt.

## 9. Performance und Grenzen

- Ziel-Overhead je Emit: < 1 ms im Normalfall (Lock + Append weniger KB). Ein
  Run erzeugt grob 10^3–10^4 Events.
- Erwartete Log-Größe: einstellige MB bei Dry-Runs, zweistellige MB bei echten
  Runs mit gesprächigen Test-Ausgaben. Kein Cap laut Entscheidung — Retention
  (§4.5) ist das Gegenmittel.
- Der Reader hält geparste Events je Run in einem LRU-Cache mit Byte-Offset als
  Schlüssel; ein Tail-Nachlesen parst nur das Neue.
- Schutzgrenze: bei einem Log > 200 MB rendert die GUI den Trace lazy (Kinder
  auf Anfrage), statt den ganzen Baum eifrig aufzubauen.

## 10. Definition of Done

1. `uv run adw run --dry-run` erzeugt ein `events.jsonl`, aus dem sich der
   vollständige Kontrollfluss aller sieben Phasen rekonstruieren lässt —
   verifiziert durch einen Test, der den Span-Baum abläuft und Phasenreihenfolge,
   Lane-Parallelität und Loop-Runden prüft.
2. Jeder Event-Typ aus §4.4 wird mindestens einmal emittiert — durch den
   Dry-Run-E2E-Test oder einen gezielten Unit-Test.
3. Ohne das GUI-Extra (`pip install adw` ohne `[gui]`) ändert sich an `adw run`
   nichts — kein Import-Fehler, keine fehlende Abhängigkeit.
4. Ein künstlich kaputtes Event-Log (nicht schreibbarer Pfad, Disk-full-
   Simulation, korrupte Zeile) lässt nie einen Run scheitern —
   Regressionstest mit einem Emitter, der wirft.
5. `_collect()` liefert mit und ohne Instrumentierung bit-identische Ergebnisse
   — Regressionstest mit gemocktem SDK-Stream.
6. GUI: Run-Liste, Run-Detail, alle vier Detail-Reiter, Timeline, Artefakte, Raw
   und der SSE-Live-Stream sind gegen ein Fixture-Log getestet
   (FastAPI `TestClient`).
7. Der Schritt-Diff zwischen zwei Snapshot-Refs zeigt den korrekten Patch —
   Test mit echtem Temp-Repo.
8. `adw runs prune` behält exakt N Runs, entfernt deren Refs, `--gzip` läuft im
   Reader wieder ein.
9. Sprachumschaltung de/en deckt alle UI-Labels ab; kein unübersetzter Key
   (Test gleicht die Wörterbücher gegeneinander ab).
10. `flake8` + `isort` + `pytest` grün, `codex review --uncommitted` ohne offene
    P1.

## 11. Umsetzungsreihenfolge

Jeder Schritt ist ein TDD-Zyklus, ein Commit und für sich nützlich:

1. `adw/events.py` — Emitter, Schema, Locking, Fail-open, `seq`. Noch ohne Aufrufer.
2. Instrumentierung von `agents.py` (tiefster Nutzen: Prompts, Tool-Calls, Kosten).
3. Instrumentierung von `gates.py`, `codex.py`, `state.py`.
4. Instrumentierung von `phases.py` und `cli.py` (die ~40 Aufrufstellen).
5. `adw/snapshots.py` + Snapshot-Punkte in der Build-Phase.
6. `adw/gui/reader.py` + `model.py` — Events zu Span-Baum, tail-fähig.
7. `adw/gui/registry.py` + Auto-Registrierung in `adw run`.
8. FastAPI-App: Run-Liste + Run-Detail mit Trace-Baum und Detail-Pane.
9. SSE-Live-Stream.
10. Timeline, Artefakte, Raw-Reiter.
11. Diff-Endpoint und Diff-Reiter.
12. i18n de/en.
13. `adw runs list` / `adw runs prune` + `trace:`-Config-Sektion.

Ab Schritt 4 trägt allein das Log schon echten Debug-Wert (`jq` auf
`events.jsonl`) — die GUI ist ab da reiner Zugewinn.

## 12. Offene Punkte

- **Token-/Kostendaten im Dry-Run**: Mocks erzeugen kein `usage`. Die Timeline
  zeigt dann nur Dauern, Kostenfelder bleiben `null`. Akzeptabel.
- **`resume` über Prozessgrenzen**: das Event-Log läuft in derselben Datei
  weiter, `seq` zählt weiter (der Emitter liest beim Öffnen die höchste `seq`).
  Ein `run`-Start-Event mit `resumed_from_seq` markiert die Naht.
- **Codex-CLI-Transkripte**: `codex.py` behält heute nur die letzte Antwort. Ob
  auch dessen volles Tool-Transkript ins Log gehört, ist auf v1.1 verschoben —
  die Claude-Seite ist der größere Hebel.
- **Excalidraw-Flowchart**: `docs/adw-flowchart.excalidraw` könnte statt einer
  CSS-Statusleiste die eigentliche Grafik der Phasen-Landkarte liefern.
  Zurückgestellt; die Statusleiste kommt zuerst.
