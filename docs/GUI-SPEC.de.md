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
- **Waisen-Spans und die Enthaltungsregel.** `parent` wird aus einem
  thread-lokalen Stack abgeleitet. Ein in einem Worker-Thread geöffneter Span
  trägt deshalb `parent: null`, obwohl er logisch verschachtelt ist. Das
  passiert bei **jedem** Lauf (Dual Authoring schreibt beide Entwürfe in einem
  `ThreadPoolExecutor`) und zusätzlich bei parallelen Lanes. Der Emitter wird
  bewusst **nicht** um ein explizites Parent-Argument erweitert — der Baum wird
  stattdessen beim Lesen repariert, und zwar nach genau dieser Regel, die jeder
  Konsument identisch umsetzen MUSS:

  > Eine Waise (ein Span mit `parent: null`, der nicht die `run`-Wurzel ist)
  > gehört zu dem **innersten** Span, dessen Intervall das Intervall der Waise
  > `[start ts, end ts]` echt enthält — also unter allen enthaltenden
  > Kandidaten der mit dem spätesten Start; bei Gleichstand entscheidet die
  > höhere `seq`. Ein noch laufender Span gilt als enthaltend für alles nach
  > seinem Start. Eine Waise, die nichts enthält, bleibt Kind der
  > `run`-Wurzel.

  Die Konsequenz, klar benannt: Der Baum ist **nicht** allein aus `parent`
  ableitbar, und das Log beschreibt sich an dieser Stelle nicht selbst. Das ist
  der akzeptierte Preis dafür, den Emitter unverändert zu lassen.

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
Kosten · Event-Zahl. Sortierbar, Filter nach Repo und Status. Läufe sind nach
Status gruppiert: `awaiting_approval` zuerst, dann `running`, dann der Rest — der
handlungsbedürftige Lauf bleibt oben, statt unter neuere fertige Läufe zu
rutschen. Innerhalb jeder Gruppe bleibt die bestehende Reihenfolge „neueste
zuerst". Live aktualisiert.

Ein Trockenlauf (`dry_run: true` im `run`-Start-Payload) trägt ein kurzes
`Dry-Run`-Label in seiner Zeile, damit eine inhaltsarme Simulation nie mit einem
echten Lauf mit wenig Ausgabe verwechselt wird. Das Label folgt der gewählten
Sprache; es ist nur eine Kennzeichnung — es ändert weder Status noch Reihenfolge,
Filter oder Retention. Fehlt das Feld (ältere Logs) oder der `run`-Span, gilt der
Lauf als normaler Lauf.

Ein Lauf, dessen `run`-Span noch offen ist, aber an einem Approval-Gate pausiert,
meldet `awaiting_approval` — nicht `running` — sowohl in der Statusspalte der
Run-Liste als auch im Run-Detail-Kopf (beide Endpunkte sind stets einig).
Abgeleitet wird das aus dem Event-Log: das jüngste `approval`-Event ist `awaited`
ohne späteres `granted` (ein späteres `granted` bringt den weiterhin offenen Lauf
wieder auf `running`); ein beendeter `run`-Span behält den terminalen Status aus
seinem End-Payload unverändert. Für einen Lauf ohne Trace ist die State-Phase der
Fallback — `awaiting_approval` / `awaiting_spec_approval` → `awaiting_approval`.
`awaiting_approval` ist der einzige Zustand, in dem ein Mensch handeln muss, und
wird optisch am stärksten hervorgehoben. Die JSON-Statuswerte bleiben
sprachneutral (`waiting`, `awaiting`, `awaiting_approval`); nur ihre Labels werden
übersetzt.

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

0. **Trockenlauf-Banner** (Kopf): ein Trockenlauf trägt zusätzlich ein
   durchgehendes `Dry-Run`-Banner im Kopf, das beim Scrollen im Trace-Baum am
   oberen Rand des Viewports angeheftet bleibt (sticky Kopf), damit der Lauf auch
   weit unten im Baum nicht mit einem echten verwechselt wird. Es stammt aus
   demselben `dry_run`-Feld wie die Run-Liste, folgt der gewählten Sprache und
   erscheint nur bei einem Trockenlauf; der Kopf eines normalen Laufs bleibt
   unverändert.
1. **Phasen-Landkarte** (Kopf): die sieben Phasen als Statusleiste — erledigt /
   aktiv / ausstehend / **wartet** / gescheitert, mit jeweiliger Dauer. Klick
   scrollt den Baum zu dieser Phase. Ein an einem Approval-Gate pausierter Lauf
   zeigt seine wartende Fachphase (`spec` bzw. `plan`) als `awaiting` statt
   `active`; keine andere Phase ist dann aktiv, und `awaiting` entfällt, sobald
   das Gate freigegeben ist. Das ist die Orientierungsebene; sie spiegelt den
   Flowchart aus `docs/adw-flowchart.excalidraw`.
2. **Trace-Baum** (links): der Span-Baum aus §4.2, aufklappbar, chronologisch.
   Je Knoten: Icon (Status), Label, Dauer, bei Loops `n/cap`. Lanes sind
   Geschwister — Parallelität ist als zwei offene Äste sichtbar. Auto-Scroll zum
   aktiven Knoten im Live-Modus (abschaltbar). Ein offener Span, der reines
   **Warten** ist (eine `ci.wait`-Poll-Schleife oder eine `gate`-Laufzeit — die
   gemeinsame `_WAITING_TYPES`), liest `waiting` statt `running`, sodass leeres
   Pollen von echter Arbeit unterschieden wird; derselbe Span, den die Timeline
   als wartend zeichnet, liest auch hier `waiting`. Ein beendeter
   `gate`/`ci.wait`-Span behält sein Ergebnis (`passed`/`failed`, sonst `done`).
3. **Detail-Pane** (rechts): abhängig vom gewählten Knoten. Für `agent.run` vier
   Reiter:
   - **Prompt** — der vollständige Task-String plus System-Append, Monospace,
     kopierbar (der Hebel für Prompt-Optimierung). Zusätzlich ein **Unified Diff**
     dieses Prompts gegen den Prompt des *vorherigen* `agent.run` **desselben
     Agenten in derselben Lane** innerhalb dieses Laufs — so wird der angehängte
     Findings-Block einer Fix-Runde sichtbar, ohne zwei Prompt-Reiter
     nebeneinanderzulegen. Der Vorgänger wird rein strukturell bestimmt (gleicher
     Agent-String, gleiche Lane, größte `seq` kleiner als die dieses Knotens),
     *bevor* die Verwertbarkeit des Prompts eine Rolle spielt; andere Läufe,
     Agenten oder Lanes werden nie herangezogen. Der Diff entsteht serverseitig
     ausschließlich mit der Standardbibliothek `difflib` (`splitlines()`,
     `unified_diff(prev, cur, n=3, lineterm="")`, mit `\n` verbunden); ein
     Unterschied allein im abschließenden Zeilenumbruch gilt als identisch. Der
     Reiter zeigt genau einen unterscheidbaren Zustand: *kein Vorgänger* (beide
     abgeleiteten Felder `prompt_diff`/`previous_prompt_seq` null), *identischer
     Prompt* (`prompt_diff` `""` mit gesetztem `previous_prompt_seq` — vom
     Null-Fall unterscheidbar) oder der sichtbare Diff. Beide Felder sind additiv,
     rein aus dem bereits geladenen Event-Strom abgeleitet und hängen nur an
     `agent.run`-Knoten von `GET /api/runs/{repo}/{run_id}`.
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
     funktioniert, auch für Event-Typen, die die GUI noch nicht kennt. Neben den
     bestehenden Filtern für Freitext (`raw_q`) und Ereignistyp (`raw_type`) und
     der `limit`-Fensterung akzeptiert er einen **inklusiven Seq-Bereich**
     (`raw_from_seq`/`raw_to_seq`, jede Grenze optional und einseitig einsetzbar).
     Der Bereich wird serverseitig (logisches UND) mit den übrigen Filtern
     komponiert; `limit` fenstert erst die vollständig gefilterte Treffermenge und
     der ausgewiesene `total` bleibt die Größe vor der Fensterung; die angebotene
     `types`-Liste bleibt die volle Typmenge des Logs. Eine nicht-numerische Grenze
     gilt als fehlend (diese Grenze inaktiv), eine obere Grenze kleiner als die
     untere ergibt eine definierte leere Menge — nie ein 5xx. Jeder **Span-Knoten**
     im Trace-Baum bietet einen Absprung in diesen Raw-Reiter, vorgefiltert auf den
     bereits exponierten Teilbaum-Bereich `[seq, end_seq]` des Knotens (ein reiner
     Seq-Bereichsfilter — verschränkte Events paralleler Spans innerhalb des
     Intervalls werden *nicht* ausgeschlossen); der Absprung erhält die aktuellen
     `raw_q`/`raw_type`/`limit` und aktiviert den bestehenden Raw-Reiter (kein
     zweites Raw-Widget). Ein aktiver Bereich wird mit seinen Grenzen angezeigt und
     isoliert aufgehoben — das Aufheben entfernt nur den Seq-Bereich und behält
     `raw_q`/`raw_type`/`limit`. Die schreibgeschützte Events-Route (`…/events`)
     bleibt unverändert: weiterhin nur `from_seq`/`to_seq`, kein `type`, keine
     Paginierung.

5. **Kontext-Panel „Lauf-Zustand"** (neben dem Detail-Pane): eine read-only
   Feldliste, die den Lauf-Zustand **zum Stand des ausgewählten Knotens** zeigt —
   sie beantwortet, *warum* ein Knoten so ausging, ohne den Baum hoch- und
   runterzuklicken oder in den Raw-Reiter zu wechseln. Rein abgeleitet aus dem
   Event-Strom, den die Detail-Antwort ohnehin lädt (kein neues Event, kein neuer
   Reader, keine neue Route, keine Persistenz, keine Änderung am State-Schema);
   jeder Knoten trägt seinen sechsfeldrigen Kontext im Render mit, die Auswahl
   projiziert ihn nur — keine clientseitige Neu-Ableitung. Die sechs festen Felder:
   `phase`, `round` (`{loop, n, cap}` der umgebenden Schleife, falls vorhanden),
   `limit_hits`, `circuit_breakers`, `cost_usd` (kumuliert, über die bestehende
   Kostenlogik) und `followups`.
   - **Cutoff / Zeitreise**: der Cutoff eines Knotens ist seine eigene `seq`
     (Punkt-Ereignis) bzw. sein exponiertes `end_seq` (Span — das Subtree-Maximum,
     sodass ein abgeschlossener/laufender Span passende Ereignisse *innerhalb* nach
     seinem Start einschließt). Es zählen nur Ereignisse mit `seq ≤ Cutoff`, daher
     spiegelt ein früherer Knoten nie ein späteres Ereignis. Die Auswahl eines
     anderen Knotens zeigt dessen historischen Zustand.
   - **Ohne Auswahl / live**: ohne Knotenauswahl zeigt das Panel `latest_context`,
     abgeleitet bis zur höchsten beobachteten `seq` — die Live-Ansicht. Sie
     aktualisiert sich über die bestehenden Mechanismen; das SSE-Protokoll bleibt
     unverändert.
   - **Leer-Semantik**: jeder fehlende Wert bleibt **leer** — `null`, nie ein
     erfundenes `0`. Zähl- und Kostenfelder sind bis zum ersten Vorkommen `null`;
     eine Phase ohne gültige Beobachtung und ein Knoten außerhalb jeder Runde sind
     `null`. Ein Lauf ohne Trace hat keinen Knoten-Kontext und kein top-level
     `context`-Feld — nur ein `latest_context` mit sechs `null`-Feldern — und
     niemals einen Fehler.
   - `phase` stammt aus genau zwei bestehenden Quellen — einem nichtleeren `name`
     eines `phase`-Span-Starts und einem nichtleeren `phase` eines
     `state.saved`-Payloads — wobei die gültige Beobachtung mit der höchsten `seq`
     bis einschließlich Cutoff gewinnt (`state.saved` wird nur gemäß seinem
     bestehenden `{seq, phase}`-Payload gelesen, nie erweitert). Das Panel ist eine
     einfache Feldliste — kein Diagramm, keine Verlaufskurve — ohne konfigurierbares
     Feld-Set und ohne Persistenz der Auswahl.

6. **Recovery-Karte** (wenn der Lauf menschliches Eingreifen braucht): das
   Run-Detail zeigt genau eine Karte, die von der bloßen Zustandsanzeige zum
   konkreten nächsten Schritt führt. Sie ist eine rein abgeleitete Projektion des
   bereits geladenen Zustands (`state.phase`), der bestehenden Status-Ableitung, des
   Event-Stroms und des serverseitig aufgelösten Repo-Pfads (`RepoRef.path`) — kein
   neues Event, kein neuer Reader, keine neue Route, keine Persistenz, keine neue
   Liveness-Erkennung. Beobachtbar als additives `recovery`-Objekt in
   `GET /api/runs/{repo}/{run_id}`, vorhanden **genau dann**, wenn der Lauf
   Eingreifen braucht, sonst **abwesend** (nie ein leeres Objekt).
   - **Trigger und Auswahl** folgen *nur* `state.phase` (nie dem `phase`-Feld des
     `escalation`-Events, das stets die Ursprungs-Phase trägt und nie `escalated`
     sein kann). Lebenszyklus-Grundlage (im Code geprüft): `escalate()` setzt
     `state.phase` auf `escalated` und emittiert *erst dann* das `escalation`-Event;
     ein Lauf mit `escalation`-Event ist also *immer* endgültig eskaliert —
     Approval-Pausen und transiente Abbrüche/Crashes erzeugen kein solches Event.
     Daher: `escalated` → Kind `none` (ein NEUER Lauf nötig, kein
     Fortsetzungskommando); `awaiting_spec_approval`/`awaiting_approval` → Kind
     `approve`; eine Arbeitsphase (`spec`, `plan`, `build`, `integration`,
     `codex_review`, `final_review`, `ci`), deren abgeleiteter Run-Status nicht
     `running` ist → Kind `resume`; `done`, eine laufende Arbeitsphase oder kein
     ladbarer State → keine Karte. Die `escalated`-Prüfung geht strikt allen anderen
     voraus, sodass einem eskalierten Lauf nie `resume`/`approve` angeboten wird —
     konsistent damit, dass `adw resume` einen eskalierten Lauf selbst verweigert.
   - **Kommando** (Kind `approve`/`resume`): der fertige, kopierbare Text in der
     bestehenden CLI-Signatur — `adw approve <run_id> --repo <pfad>` bzw.
     `adw resume <run_id> --repo <pfad>` — mit der echten `run_id` und dem echten,
     serverseitig aufgelösten Registry-Pfad, **nicht** dem Slug. `run_id` und Pfad
     werden POSIX-shell-sicher nach `shlex.quote`-Semantik dargestellt, sodass ein
     Pfad mit Leerzeichen, einfachen Anführungszeichen oder Shell-Metazeichen genau
     EIN `--repo`-Argument bleibt und kein Zusatzkommando ergibt. Kind `none` trägt
     kein Kommando, aber das maschinenlesbare `needs_new_run`-Kennzeichen.
   - **Eskalationskontext** (Kind `none`): `reason` und die betroffene `phase`
     unverändert aus dem `escalation`-Event mit der größten `seq`; die zugehörigen
     `limit.hit`/`circuit_breaker`-Abbruch-Ereignisse (die zwischen einer etwaigen
     vorherigen Eskalation und der maßgeblichen liegen, Payloads unverändert); und
     ein Verweis auf `escalation.md` im Artefakte-Reiter — die Karte verlinkt darauf,
     statt dessen Inhalt zu duplizieren. Die Karte ist am maßgeblichen
     Eskalationsknoten verankert (`anchor_seq`); fehlt einem eskalierten Lauf ein
     verwertbares Event-Log, ist der Kontext `null`/leer (nie erfunden) und die
     weiterhin verwertbare Karte fällt auf Run-Ebene zurück.
   - **Read-only** (E1/§2): das Kommando wird angezeigt, nie ausgeführt — das Rendern
     startet keinen Subprozess und schreibt nichts. Der echte Repo-Pfad erscheint
     *nur* im Kommandotext, nie in einer URL (die Slug-Regel aus §7.4 bleibt
     unangetastet). Alle Kartenlabels sind beidsprachig (`adw/gui/i18n.py`); die
     Kommandozeile, Eventwerte, `run_id` und Repo-Pfad werden nicht übersetzt.
7. **Plan-Skelett** (wenn die `plan.md` des Laufs geplante Aufgaben ergibt): die
   Trace-Ansicht zeigt je Workstream eine read-only Liste der *noch geplanten*
   Aufgaben neben bzw. über dem Trace-Baum derselben Lane — so liegen „geplant"
   (Skelett) und „geleistet" (Trace) in einer Ansicht. Es ist eine rein abgeleitete
   Projektion aus der `plan.md` des Laufs (nur über den bestehenden
   Whitelist-Artefakt-Pfad gelesen) und dem bereits geladenen Event-Strom (für den
   groben Lane-Status) — kein neues Event, kein neuer Reader, keine neue Route, keine
   Persistenz. Beobachtbar als additives `plan_skeleton`-Feld in
   `GET /api/runs/{repo}/{run_id}`, vorhanden **genau dann**, wenn `plan.md`
   mindestens einen `## Workstream:`-Abschnitt mit einer `###`-Aufgabe ergibt, sonst
   **abwesend** (nie eine erzwungene leere Liste).
   - **Parse-Regeln** (genau zwei, kein Kennungs-Muster, kein Markdown-Parser, keine
     Abhängigkeit): ein *Abschnitt* beginnt bei einer Zeile `## Workstream: <name>`
     und endet bei der nächsten `##`-Überschrift (jede Zeile, die mit `##` beginnt,
     aber kein `###`) oder am Dateiende; `<name>` ist der Text nach dem exakten
     Präfix `## Workstream: `. Eine *Aufgabe* ist jede Zeile im Abschnitt mit dem
     exakten Präfix `### ` — der Aufgabentext ist der Rest **wortgetreu** (Markierung
     und genau ein Trennleerzeichen entfernt, keine Zerlegung in Kennung und Titel,
     kein weiteres Trimmen). Ein bloßes `###` ohne Folgetext ist keine Aufgabe;
     `###`-Zeilen außerhalb eines Abschnitts oder nach dessen abschließender
     `##`-Überschrift zählen nicht. Die über die Läufe hinweg uneinheitlichen Formen
     (`### B1 — …`, `### 1. …`, `### A.1 — …`, `### Aufgabe A — …`, `### Aufgabe B1 —
     …`) bleiben alle erhalten, je eine Aufgabe. Ein Eintrag je Abschnitt mit ≥1
     Aufgabe, in Dokumentreihenfolge; ein Abschnitt ohne Aufgabe erzeugt keinen
     Eintrag.
   - **Status** ist grob und nur auf **Lane-Ebene**: `done`, wenn die `lane`-Span,
     deren Name dem Workstream gleicht, mit `completed: true` endet; sonst `pending`
     — auch bei einem `lane`-Ende ohne `completed: true`, einer laufenden und einer
     noch nicht gestarteten Lane. Es gibt keinen Status je Aufgabe und je Knoten und
     keine geratene Aufgabe↔Knoten-Zuordnung. Eine noch nicht gestartete Lane zeigt
     ihre `pending`-Liste trotzdem, **ohne** einen leeren oder künstlichen
     Trace-Knoten zu erzeugen; der Trace-Baum und seine Knotenstruktur bleiben
     unverändert.
   - **Fallback** (Robustheit): eine `plan.md`, die fehlt, leer, unlesbar oder über
     den Artefakt-Pfad abwesend ist (ein aus der Run-Verzeichnisgrenze ausbrechender
     Symlink) oder keinen passenden Abschnitt trägt, ergibt **kein** Skelett —
     `plan_skeleton` ist abwesend, kein Fehler, kein leerer Kasten, und die übrige
     Detail-Antwort sowie die bisherige Ansicht bleiben unverändert.
   - **Read-only** (E5): reine Anzeige — kein Abhaken, keine Bearbeitung, keine
     Schreibroute, keine neue Persistenz. Die Chrome-Labels (Listenüberschrift und die
     `pending`/`done`-Marker) sind beidsprachig (`adw/gui/i18n.py`); die Aufgabentexte
     sind Inhalt und werden nicht übersetzt.

8. **Änderungsumfang** (immer vorhanden): das Run-Detail zeigt nebeneinander, welche
   Dateien der Lauf tatsächlich geändert hat — gruppiert je Lane, mit `+/-`-Zahlen je
   Datei — und den im Contract deklarierten Scope, so wie er dasteht. Beide Fakten
   stehen **unbewertet** nebeneinander; ob eine Änderung „im Scope" liegt, entscheidet
   der Mensch. Es ist eine rein abgeleitete Projektion der bereits geladenen Events,
   der bestehenden Snapshots und der gewhitelisteten `contract.yaml` — keine neue
   Git-Operation, Route, kein neues Event, keine Persistenz. Beobachtbar als additives
   `change_scope`-Objekt (`lanes` + `declared_scope`) an
   `GET /api/runs/{repo}/{run_id}`; der Schlüssel ist **immer** vorhanden.
   - **Dateilisten** (je Lane): eine Lane ist *beobachtet*, wenn das Event-Log einen
     `lane`-Span mit nicht-leerem Namen **oder** ein strukturell gültiges
     Snapshot-Event (Ref-Form `refs/adw/<run_id>/<seq>`) trägt, das sie deklariert — die
     Lanes erscheinen in Erstbeobachtungs-Reihenfolge (kleinstes Seq), ein Eintrag je
     Name. Bei **≥ 2** gültigen Snapshots trägt der Eintrag den Diff zwischen ihrem
     Snapshot mit niedrigstem und höchstem Seq — erzeugt durch die **bestehende**
     Snapshot-/Diff-/Numstat-Logik, genau ein Vergleich je Lane, andere Lanes nie
     einbezogen — als `diff_available: true` mit `files` (je `path`, `additions`,
     `deletions`; Binärdatei → `null`, dargestellt als „nicht numerisch verfügbar").
     Ein erfolgreicher Diff ohne Änderungen ist `files: []` („keine geänderten Dateien
     gefunden"), unterscheidbar von „nicht verfügbar".
   - **Unbrauchbare / fehlgeschlagene Diffs** (Robustheit): eine Lane mit **0 oder 1**
     gültigem Snapshot oder deren Diff trotz Paar fehlschlägt (fehlendes
     Snapshot-Objekt, Timeout, Ausführungsfehler) ist `diff_available: false` mit
     `files: null` — kanonisch genau diese Form, nie `[]`, nie ein weggelassenes Feld —
     dargestellt als „kein Diff verfügbar" statt einer leeren Tabelle. Eine
     fehlgeschlagene Lane blockiert andere Lanes nie und macht aus der sonst
     erfolgreichen Detail-Anfrage nie ein 5xx. Hat **keine** Lane einen verwertbaren
     Diff, entfällt die Tabellenansicht mit klarer Aussage „kein Lauf-Diff verfügbar",
     der deklarierte Scope bleibt darstellbar.
   - **Deklarierter Scope**: `declared_scope` ist eine lesbare, **semantisch
     äquivalente** YAML-Serialisierung aller Top-Level-`x-adw-*`-Blöcke der
     `contract.yaml` (gelesen nur über den bestehenden Whitelist-Artefakt-Pfad mit dem
     bereits vorhandenen `yaml`-Modul), in Dokumentreihenfolge, Werte und
     Verschachtelung unverändert — ohne Umbenennung, Zusammenführung, Normalisierung
     oder Interpretation, textliche Details (Kommentare, Quoting) bleiben nicht
     erhalten. Ein fehlendes, unlesbares, kein-Mapping-, nicht sicher ladbares oder
     `x-adw-`-loses Contract (ein Nicht-String-Top-Level-Schlüssel wird ignoriert, nie
     ein Absturz) oder ein ausbrechender Symlink ergibt `declared_scope: null`, klar
     als „kein deklarierter Scope" dargestellt — eine neutrale Abwesenheit, keine
     Verletzung.
   - **Kein Urteil** (E1): es gibt kein Feld und keine Markierung für „im
     Scope"/„außerhalb"/„Verletzung"/Konformität, keine Datei↔`x-adw-*`-Zuordnung und
     keine abgeleitete Wertung — `change_scope`, Lane- und Datei-Objekte tragen genau
     die gelisteten Schlüssel. Ein strukturierter Datei-Scope und eine echte
     Verletzungsprüfung sind bewusst Deferred.
   - **Read-only** (E6): rein additive Projektion — keine neue Route, kein Event, kein
     Schreibzugriff, kein Zustand; alle bestehenden Antwortfelder bleiben unverändert.
     Die Chrome-Labels (Überschrift, `+/-`-Spaltenköpfe, die Fallback-Texte) sind
     beidsprachig (`adw/gui/i18n.py`); Dateipfade und der deklarierte Scope sind Inhalt
     und werden nicht übersetzt.

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
| `GET /api/runs/{repo}/{run_id}/events?from_seq=N&to_seq=M` | Roh-Events, nur Seq-Bereich |
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
