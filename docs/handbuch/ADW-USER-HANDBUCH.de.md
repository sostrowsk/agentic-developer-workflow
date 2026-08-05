<div>

<div class="wrap">

<span class="badge">USER-HANDBUCH · Stand 2026-07-15 · adw 0.1.0</span>

# Agentic Developer Workflow (ADW)

Ein Issue rein, ein geprüfter Feature-Branch mit grüner CI raus — vollautomatisch durch sieben Phasen.

</div>

</div>

<div role="main">

<div id="tldr" class="section tldr">

## ⚡ Auf einen Blick

- **Was:** `adw run` führt ein Issue durch **Spec → Plan+Kontrakt → Build → Integration/E2E → Codex-Review → finaler Review → Push/CI** — Agenten liefern Urteilsvermögen, der Kontrollfluss ist deterministischer Code.
- **Start:** `uv sync`, dann `.adw/config.yaml` im Ziel-Repo anlegen, dann `uv run adw run --repo <pfad> --issue "…"`.
- **Gefahrlos testen:** `--dry-run` fährt den kompletten Ablauf mit Mocks — 0 Tokens, kein Netz, kein Push.
- **Kontrolle behalten:** Nach dem Plan pausiert der Run (Exit 2). Plan lesen, dann `adw approve <run_id>`. Abschaltbar mit `--no-approval`.
- **Wenn etwas schiefgeht:** Exit 1 + Report unter `.adw/runs/<run_id>/escalation.md`. Nach Crash: `adw resume <run_id>` macht exakt dort weiter.

</div>

<div id="kernaussagen" class="section">

## Kernaussagen

<div class="kern">

<div class="card">

**Du gibst ein Issue, ADW liefert einen Branch.** Am Ende steht ein gepushter Feature-Branch, dessen Pipeline inklusive Staging-Deploy grün ist.

</div>

<div class="card">

**Reviewer fixen nie.** Jeder Fix läuft durch die Build-Agents und erneut durch alle Gates — es gibt keinen Sonderweg für „triviale" Fixes.

</div>

<div class="card">

**Alles ist begrenzt.** Feste Limits (10 Gate-Iterationen, 3 Fix-Zyklen, 1 CI-Re-Entry) und ein Circuit-Breaker verhindern Endlosschleifen; danach übernimmt ein Mensch.

</div>

<div class="card">

**Jeder Zustand überlebt einen Crash.** Der Run-State wird laufend persistiert; `adw resume` setzt in derselben Phase fort, ohne Ergebnisse zu verlieren.

</div>

</div>

</div>

1.  [Voraussetzungen & Installation](#voraussetzungen)
2.  [Das Ziel-Repo vorbereiten](#zielrepo)
3.  [Der erste Lauf (Dry-Run)](#erster-lauf)
4.  [CLI-Referenz](#cli)
5.  [Was passiert in den sieben Phasen?](#ablauf)
6.  [Das Plan-Approval-Gate](#approval)
7.  [Artefakte, Reports & Run-Verzeichnis](#artefakte)
8.  [Crash, Pause, Resume](#resume)
9.  [Eskalationen verstehen](#eskalation)
10. [Troubleshooting & FAQ](#troubleshooting)
11. [Glossar](#glossar)

## 1. Voraussetzungen & Installation

| Werkzeug                | Wozu                                                                                                               | Check                                    |
|-------------------------|--------------------------------------------------------------------------------------------------------------------|------------------------------------------|
| Python ≥ 3.12 + **uv**  | Laufzeitumgebung des Orchestrators                                                                                 | `uv --version`                           |
| **Claude-CLI-Login**    | Die Claude-Agenten (Spec, Plan, Build, Reviews) laufen über das Agent SDK mit deinem gespeicherten Login           | `claude` einmal interaktiv anmelden      |
| **codex** (CLI)         | Unabhängiger Reviewer für Spec, Plan und Code                                                                      | `codex login` einmalig                   |
| **glab** / **gh** (CLI) | Issue lesen und CI überwachen — glab für GitLab-Projekte, gh für GitHub-Projekte (nur das passende wird gebraucht) | `glab auth status` bzw. `gh auth status` |
| **git**                 | Worktrees, Branches, Merges, Push                                                                                  | —                                        |

    # Repo klonen und Abhängigkeiten installieren
    git clone git@gitlab.com:addvendo/agentic-developer-workflow.git
    cd agentic-developer-workflow
    uv sync

<div class="hint">

Ohne gespeicherten Claude-Login bricht ADW **vor** dem ersten Agent-Lauf mit einer klaren Meldung ab (fail fast) — es wird nie stillschweigend ein API-Key aus der Umgebung benutzt.

</div>

### Abrechnung: läuft über deinen Claude-Plan, nicht über API-Tokens

ADW spricht Claude **ausschließlich über die Claude-Code-CLI** an — das Claude Agent SDK startet sie unter der Haube als headless Subprocess. Es gibt keinen separaten API-Pfad:

- **Stored-Login-only, erzwungen:** ADW verlangt die gespeicherte CLI-Anmeldung (`~/.claude/.credentials.json` bzw. macOS-Keychain). Umgebungsvariablen wie `ANTHROPIC_API_KEY` werden für alle Agent-Prozesse aktiv geleert — ein API-Key wird selbst dann nie benutzt, wenn einer gesetzt ist. Versehentliches token-by-token-Payment ist damit ausgeschlossen.
- **Verbrauch = Abo-Limits:** Läufe zählen gegen die Plan-Kontingente (5-Stunden-Fenster + Wochenlimits), nicht gegen ein Dollar-Budget. Kostenrisiko null, dafür Verfügbarkeitsrisiko. Die Registry nutzt Fable 5, Opus 4.8 und Sonnet 5 — der Plan muss diese Modelle hergeben (praktisch: Max-Plan).
- **Limit erschöpft ≠ Datenverlust:** Schlägt ein Agent-Aufruf fehl (typisch: Fenster leer), stoppt der Run kontrolliert mit Exit 1 und Resume-Hinweis — er eskaliert *nicht* und bleibt am persistierten Checkpoint stehen. Nach dem Fenster-Reset: `adw resume <run_id>` macht exakt dort weiter (Sessions, offene Fix-Tasks und Zähler sind gespeichert). Ein automatisches „warten bis Reset" gibt es bewusst nicht.
- **`--parallel` verbraucht schneller:** Zwei gleichzeitige Opus-Build-Sessions plus Reviews schöpfen ein 5-Stunden-Fenster deutlich zügiger aus — im Plan-Betrieb ist Single-Lane das entspanntere Profil.
- **Codex ist ein eigenes Abo:** Die Codex-Reviews laufen über deinen ChatGPT/Codex-Login (isoliertes `CODEX_HOME`) — ebenfalls kein Token-Payment, aber ein separates Kontingent.

## 2. Das Ziel-Repo vorbereiten

ADW arbeitet **gegen ein beliebiges Git-Repo** („Ziel-Repo"). Die gesamte projektspezifische Konfiguration lebt dort in **einer Datei**: `.adw/config.yaml`. Eine Vorlage liegt unter `examples/config.yaml`.

    mkdir -p /pfad/zum/repo/.adw
    cp examples/config.yaml /pfad/zum/repo/.adw/config.yaml   # und anpassen

    # .adw/config.yaml — Minimalbeispiel (eine Lane)
    base_branch: staging
    lanes:
      backend:
        gates:                      # Reihenfolge = Ausführungsreihenfolge, fail fast
          - {name: black,  cmd: "black --check .",      timeout: 120}
          - {name: isort,  cmd: "isort --check-only .", timeout: 120}
          # tdd: true = mindestens ein markiertes Gate muss rot sein, bevor implementiert wird
          - {name: pytest, cmd: "pytest -x -q",         timeout: 1800, tdd: true}

Vollständige Config-Referenz (alle Schlüssel)

<div class="inner">

| Schlüssel                 | Pflicht         | Bedeutung                                                                                                                                                                   |
|---------------------------|-----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `base_branch`             | ja              | Branch, von dem die Lanes forken und gegen den Diffs gerechnet werden.                                                                                                      |
| `lanes.<name>.gates[]`    | ja (≥ 1 Lane)   | Gate-Liste je Lane. Jedes Gate braucht `name`, `cmd` und `timeout` (Sekunden). Gates laufen der Reihe nach; das erste rote Gate stoppt den Durchlauf.                       |
| `…gates[].tdd`            | optional (false) | Markiert ein Gate (typisch: das Test-Gate) als RED-Beweis: Im Initial-Build muss mindestens ein markiertes Gate nach dem reinen Test-Lauf rot sein, **vor** dem Implementierungs-Lauf (Abschnitt 5, Phase 3).                   |
| `lanes.frontend`          | optional        | Fehlt die Lane, läuft ADW im Single-Lane-Modus. `--parallel` verlangt `backend` **und** `frontend`.                                                                         |
| `e2e.cmd` / `e2e.timeout` | optional        | E2E-Kommando (z. B. `npx playwright test`) — läuft nur mit `--parallel` auf dem Integrations-Branch.                                                                        |
| `ci.poll_interval`        | optional (60)   | Sekunden zwischen zwei Pipeline-Abfragen.                                                                                                                                   |
| `ci.timeout`              | optional (2700) | Gesamtbudget fürs CI-Warten (45 min Default).                                                                                                                               |
| `ci.staging_job`          | optional        | Name eines Jobs (z. B. `deploy-staging`), der zusätzlich grün sein muss.                                                                                                    |
| `ci.provider`             | optional        | `gitlab` oder `github`. Ohne Angabe erkennt ADW das Hosting an der origin-Remote-URL; bei unbekanntem Host (z. B. Self-Hosted mit eigenem Domainnamen) ist der Key Pflicht. |

Fehlende oder kaputte Config (unbekannte Schlüssel, Lane ohne Gates, Gate ohne Timeout, doppelte Schlüssel) bricht **sofort** mit einer klaren Meldung ab — es werden keine Defaults geraten, außer den beiden dokumentierten (`poll_interval`, `timeout`).

</div>

<div class="warnbox">

**Gates sind deine Qualitätsgrenze.** ADW akzeptiert Build-Ergebnisse nur, wenn *alle* Gates grün sind. Was die Gates nicht prüfen, prüft in Phase 3 niemand — konfiguriere also mindestens Formatter/Linter und die Testsuite.

</div>

## 3. Der erste Lauf (Dry-Run)

Bevor Tokens fließen: Der Dry-Run fährt **den kompletten Kontrollfluss** mit gescripteten Mock-Agenten — 0 Tokens, kein Netz, kein Push. Er verifiziert deine Config, die Gates und den gesamten Ablauf:

    uv run adw run --repo /pfad/zum/repo --issue "Demo-Feature" --dry-run --no-approval
    uv run adw run --repo /pfad/zum/repo --issue "Demo-Feature" --dry-run --no-approval --parallel

Der Dry-Run ist bewusst kein reiner Schönwetterlauf: Er simuliert **einen Gate-Fail** (der Fix geht als Folge-Task an dieselbe Agent-Session) und im `--parallel`-Modus **einen roten E2E-Lauf**, den der Triage-Agent in eine Lane zurückroutet. Du siehst also genau die Loops, die auch ein echter Lauf nehmen würde.

Danach der echte Lauf:

    # Issue-Text direkt …
    uv run adw run --repo /pfad/zum/repo --issue "Bug: Login bricht ab, wenn …"
    # … oder aus GitLab/GitHub ziehen (Titel + Beschreibung via glab/gh)
    uv run adw run --repo /pfad/zum/repo --gitlab-issue 42 --parallel
    uv run adw run --repo /pfad/zum/repo --github-issue 42 --parallel

## 4. CLI-Referenz

    adw run --repo <pfad> (--issue "Text" | --gitlab-issue <id> | --github-issue <nr>)
            [--parallel] [--dry-run] [--no-approval] [--base-branch <name>]
    adw resume  <run_id> [--repo <pfad>]     # nach Crash; bei Approval-Pause → approve
    adw approve <run_id> [--repo <pfad>]     # Plan-Approval erteilen + fortsetzen
    adw status  [<run_id>] [--repo <pfad>]   # Runs + Phase anzeigen

| Option                 | Bedeutung                                                                                                                                                                                                                      |
|------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--issue "Text"`       | Issue-Beschreibung direkt auf der Kommandozeile. Genau **eine** Issue-Quelle ist Pflicht.                                                                                                                                      |
| `--gitlab-issue <id>`  | Holt Titel + Beschreibung des Issues via `glab issue view` aus dem GitLab-Projekt des Ziel-Repos.                                                                                                                              |
| `--github-issue <nr>`  | Holt Titel + Beschreibung des Issues via `gh issue view` aus dem GitHub-Projekt des Ziel-Repos.                                                                                                                                |
| `--parallel`           | Baut Frontend- und Backend-Workstream in **zwei parallelen Lanes** (eigene Worktrees, Sessions, Ports) und aktiviert Integration + E2E-Gate. Verlangt beide Lanes in der Config.                                               |
| `--dry-run`            | Mocks statt Agenten, Fake-CI statt glab, kein Push. Der Modus wird im Run-State gespeichert — auch ein `resume`/`approve` eines Dry-Runs bleibt tokenfrei.                                                                     |
| `--no-approval`        | Überspringt die Plan-Approval-Pause. Gilt für den ganzen Run (überlebt Crash + Resume).                                                                                                                                        |
| `--base-branch <name>` | Überschreibt `base_branch` aus der Config. Wird beim Run-Start **gepinnt**: spätere Änderungen an der Config verschieben einen laufenden Run nicht; ein Wechsel per Flag ist nur möglich, solange noch keine Lanes existieren. |

### Exit-Codes

| Code                                | Bedeutung                                                             | Nächster Schritt                          |
|-------------------------------------|-----------------------------------------------------------------------|-------------------------------------------|
| <span class="exitcode ec0">0</span> | Run abgeschlossen (`done`) — Branch gepusht, Pipeline + Staging grün. | Merge Request stellen / mergen.           |
| <span class="exitcode ec2">2</span> | `awaiting_approval` — der Run wartet auf deine Plan-Freigabe.         | Plan lesen, dann `adw approve <run_id>`.  |
| <span class="exitcode ec1">1</span> | Eskalation oder Fehler.                                               | `.adw/runs/<run_id>/escalation.md` lesen. |

## 5. Was passiert in den sieben Phasen?

<div class="phase-flow">

<span class="ph">1 Spec<span class="small">2 Entwürfe + Synthese + Codex</span></span><span class="arrow">→</span> <span class="ph">2 Plan + Kontrakt<span class="small">2 Entwürfe + Synthese · Approval</span></span><span class="arrow">→</span> <span class="ph">3 Build<span class="small">Opus 4.8 je Lane + Gates</span></span><span class="arrow">→</span> <span class="ph">4 Integration + E2E<span class="small">nur --parallel</span></span><span class="arrow">→</span> <span class="ph">5 Codex-Review<span class="small">Code-Diff</span></span><span class="arrow">→</span> <span class="ph">6 Finaler Review<span class="small">Fable 5 + Triage</span></span><span class="arrow">→</span> <span class="ph">7 Push + CI<span class="small">glab-/gh-Polling</span></span>

</div>

Phase 1–2: Spec und Plan entstehen — und werden unabhängig reviewt

<div class="inner">

Jedes der beiden Artefakte entsteht **zweimal unabhängig**: Der **Spec-Agent** (Opus 4.8) und **Codex** schreiben je einen eigenen Entwurf der Spezifikation nach fester Vorlage (Ziel, Scope, Nicht-Ziele, Akzeptanzkriterien, Definition of Done) — parallel, beide Entwürfe landen in `.adw/runs/<run_id>/drafts/`. Die **Spec-Synthese** (Fable 5) merged sie danach zu EINEM Best-of-`.adw/spec.md` (je Abschnitt gewinnt die stärkere Formulierung, nie die Vereinigungsmenge) und schreibt die kurze Zusammenfassung `.adw/spec-summary.md` — deine Entscheidungsgrundlage am Approval-Gate. **Codex** reviewt das gemergte Artefakt; Findings gehen als Folge-Task **an dieselbe Synthese-Session** zurück, bis das Verdict `ok` ist — maximal 5 Runden. Pro Runde sinkt die Severity-Schwelle (Runde 1: alle Findings, Runde 2: P1+P2, ab Runde 3: nur P1), und Codex bekommt ab Runde 2 die Vorrunden-Findings samt Disposition mit, damit er erledigte oder bewusst abgewiesene Punkte nicht erneut anmerkt. Danach erzeugen **Plan-Agent** + Codex + **Plan-Synthese** analog `.adw/plan.md` (Workstreams), `.adw/contract.yaml` (Schnittstellen-Kontrakt: OpenAPI/Typen/Events) und `.adw/plan-summary.md` — Codex prüft Plan und Kontrakt **gemeinsam gegen die Spec**. Fällt Codex als *Autor* aus, bricht der Run nicht ab: die Synthese arbeitet dann allein mit dem Claude-Entwurf und schreibt das in die Zusammenfassung. Anschließend pausiert der Run für dein Approval (Abschnitt 6).

</div>

Phase 3: Build in isolierten Lanes mit Gate-Loop

<div class="inner">

Je Lane entsteht ein eigener **Git-Worktree** unter `.adw/runs/<run_id>/trees/<lane>` mit eigenem Branch `adw/<run_id>/<lane>`, eigener Agent-Session und eigenen Ports (als `BACKEND_PORT`/`FRONTEND_PORT` in die Gates injiziert). Hast du mindestens ein Gate mit `tdd: true` markiert, beginnt der Initial-Build mit der **RED-Stufe**: Der Build-Agent schreibt zuerst **nur die Tests** (keinen Produktivcode), danach führt der Orchestrator selbst genau die markierten Gates aus. Mindestens eines rot = RED bewiesen, und dieselbe Agent-Session macht mit der Implementierung weiter — mit dem (gekürzten) roten Gate-Output als Task. Alle grün heißt: Die Tests decken das geforderte Verhalten nicht ab — das eskaliert, statt auf einem Beweis aufzubauen, den niemand hat. Der **Build-Agent (Opus 4.8)** implementiert seinen Workstream strikt gegen den Kontrakt. Danach laufen deine **Gates**. Rot? Die Fehlerausgabe geht als Folge-Task an dieselbe Session — maximal 10 Iterationen (der RED-Check verbraucht keine davon), bei zweimal identischem Fehler bricht der Circuit-Breaker sofort ab. Grün? **Der Orchestrator committet** (nie der Agent) — aber nur, solange die Tests, die RED bewiesen haben, noch da sind.

</div>

Phase 4: Integration + E2E (nur --parallel)

<div class="inner">

Der Orchestrator mergt beide Lane-Branches auf einen frischen Integrations-Branch `adw/<run_id>/integration` und fährt dein E2E-Kommando. Bei Rot ordnet der **E2E-Triage-Agent** (Sonnet 5, read-only) jeden Fehler einer Lane zu; der Fix läuft durch den regulären Lane-Loop (Gates!, Commit) und es wird neu integriert. Maximal 10 Runden. Ein Merge-Konflikt eskaliert sofort an dich — Konflikte löst kein Agent.

</div>

Phase 5–6: Zwei unabhängige Reviews + Triage

<div class="inner">

**Codex** reviewt den integrierten Diff (Findings mit Fix-Plan, geroutet per Lane, bis `ok` — gleiche Review-Loop-Policy wie in Phase 1–2: max. 5 Runden, sinkende Severity-Schwelle, Findings-Gedächtnis; unter die Schwelle gefallene P2/P3 wandern nach `followups.md`). Danach prüft der **finale Reviewer** (Fable 5, strikt read-only) die Implementierung gegen die Spec. Die **Triage ist Code**: Findings der Kategorie `scope_gap` („war nie Teil des Plans") landen als Follow-up in `followups.md` — sie lösen *keinen* Umbau aus. `implementation`/`trivial`-Findings gehen als Fix-Zyklus in die zuständige Lane (max. 3 Zyklen je Lane), inklusive erneutem Gate-Lauf und Re-Review. Wichtig: Build-Agents dürfen von Fix-Plänen **begründet abweichen** — der Reviewer beschreibt das Problem, der Builder entscheidet die Lösung. Lässt ein Fix-Zyklus den Worktree unverändert und waren die auslösenden Findings **ausschließlich P3**, ist das kein Fehler: der Befund wird ebenfalls nach `followups.md` vertagt und der Lauf geht weiter (Untätigkeit bei P1/P2 eskaliert dagegen weiterhin).

</div>

Phase 7: Push + CI-Überwachung

<div class="inner">

Der Feature-Branch (Single-Lane: der Lane-Branch; parallel: der Integrations-Branch) wird gepusht. ADW pollt die CI **dieses konkreten Pushes** — GitLab-Pipelines via glab oder GitHub Actions via gh, je nach Hosting des Ziel-Repos (SHA-gebunden — eine alte oder fremde Pipeline kann das Ergebnis nicht verfälschen) bis Pipeline und konfigurierter Staging-Job grün sind. Bei Rot liest der **Log-Analyst** (Sonnet 5) die Job-Logs und routet Findings in die Lanes — **genau ein** automatischer Re-Entry, danach eskaliert der Run mit den Logs im Report.

</div>

## 6. Das Plan-Approval-Gate

Nach Phase 2 hält der Run standardmäßig an (<span class="exitcode ec2">Exit 2</span>) — **bevor** irgendetwas gebaut wird und bevor nennenswert Tokens in die Implementierung fließen:

    $ uv run adw run --repo ~/projekte/shop --issue "Warenkorb-Rabatte"
    Run 3f9a12c4 gestartet (Phase: spec)
    Plan-Approval ausstehend: .adw/runs/3f9a12c4/plan-summary.md und .adw/runs/3f9a12c4/plan.md lesen, dann `adw approve 3f9a12c4`

    $ less ~/projekte/shop/.adw/runs/3f9a12c4/plan-summary.md # zuerst die Zusammenfassung der Synthese
    $ less ~/projekte/shop/.adw/runs/3f9a12c4/plan.md      # Plan prüfen
    $ less ~/projekte/shop/.adw/runs/3f9a12c4/contract.yaml # Kontrakt prüfen
    $ uv run adw approve 3f9a12c4 --repo ~/projekte/shop    # weiter mit Phase 3–7

<div class="hint">

Fang mit der Zusammenfassung an: Sie sagt in wenigen Zeilen, was warum gebaut werden soll, welche Entscheidungen gefallen sind, was bewusst wegblieb und wo die beiden Entwürfe auseinanderlagen — Plan und Kontrakt sind die Detailebene dahinter.

Das Approval-Gate ist der billigste Ort, um falsche Richtungen zu stoppen: Eine korrigierte Annahme auf Plan-Ebene kostet nichts, auf Code-Ebene kostet sie Build-, Review- und Fix-Zyklen. Für kleine, risikoarme Aufgaben: `--no-approval`.

</div>

## 7. Artefakte, Reports & Run-Verzeichnis

| Pfad (im Ziel-Repo)                                  | Inhalt                                                                                                                  | Git-Status                                                        |
|------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| `.adw/spec.md`, `.adw/plan.md`, `.adw/contract.yaml` | Spec, Plan, Kontrakt — werden in die Lane-Worktrees kopiert und **auf dem Feature-Branch mitcommittet** (Traceability). | auf dem Feature-Branch getrackt; der Haupt-Checkout bleibt sauber |
| `.adw/runs/<run_id>/state.json`                      | Kompletter Run-Zustand (Phase, Lanes, Sessions, Zähler) — Grundlage für `resume`.                                       | gitignored (ADW legt die Ignore-Regel selbst an)                  |
| `.adw/runs/<run_id>/spec.md` etc.                    | Archivierte, reviewte Artefakt-Stände dieses Runs.                                                                      |                                                                   |
| `.adw/runs/<run_id>/spec-summary.md`, `plan-summary.md` | Die Zusammenfassung der Synthese je Authoring-Phase — deine Entscheidungsgrundlage am Approval-Gate (Was/Warum, Kernentscheidungen, Deferred, welcher Entwurf was beisteuerte, offene Punkte). |                                                                   |
| `.adw/runs/<run_id>/drafts/`                         | Die beiden unabhängigen Entwürfe je Authoring-Phase (`spec.claude.md` / `spec.codex.md`, `plan.*`, `contract.*`), dazu ein `<kind>.codex.FAILED`-Marker, falls der Codex-Entwurf fehlschlug. |                                                                   |
| `.adw/runs/<run_id>/escalation.md`                   | Eskalations-Report: erreichter Stand, Phase, konkreter Grund.                                                           |                                                                   |
| `.adw/runs/<run_id>/followups.md`                    | Follow-up-Issues aus `scope_gap`-Findings und vertagten P3-Findings (dedupliziert).                                                               |                                                                   |
| `.adw/runs/<run_id>/trees/<lane>`                    | Lane-Worktrees (+ `trees/integration` bei `--parallel`).                                                                |                                                                   |

## 8. Crash, Pause, Resume

ADW checkpointet jeden Phasenübergang **und** jedes offene Zwischenergebnis (Gate-Feedback, Review-Session, Zähler) atomar in `state.json`. Stirbt der Prozess — Stromausfall, `Ctrl-C`, Netzwegfall — gilt:

    $ uv run adw status --repo ~/projekte/shop
    3f9a12c4  build              single    Warenkorb-Rabatte

    $ uv run adw resume 3f9a12c4 --repo ~/projekte/shop
    Run 3f9a12c4 wird fortgesetzt (Phase: build)

- Fertige Lanes werden **nicht neu gebaut** — ihr Ergebnis wird über einen Baum-Hash verifiziert und übernommen.
- Ein offener Fix (Gate-Feedback lag vor, Fix lief noch nicht) wird mit **derselben Agent-Session** nachgeholt.
- Limits (Iterationen, Runden, Zyklen) überleben den Crash — ein Neustart verschafft dem Run keine zusätzlichen Versuche.
- Dry-Run bleibt Dry-Run: der Modus steckt im State, ein Resume verdrahtet nie versehentlich echte Agenten.

<div class="warnbox">

Eskalierte Runs (`phase: escalated`) sind bewusst **nicht** fortsetzbar — erst Ursache klären (Report lesen), dann einen neuen Run starten.

</div>

## 9. Eskalationen verstehen

ADW gibt auf, **bevor** es Schaden anrichtet oder Budget verbrennt. Jede Eskalation beendet den Run mit Exit 1 und schreibt `escalation.md` mit dem erreichten Stand und dem konkreten Grund:

| Auslöser                   | Limit                                  | Typische Ursache                                                         |
|----------------------------|----------------------------------------|--------------------------------------------------------------------------|
| Gate-Loop einer Lane       | 10 Iterationen pro Task                | Anforderung und Gates widersprechen sich; Flaky Tests                    |
| Circuit-Breaker            | 2× exakt derselbe Fehler               | Der Agent dreht sich im Kreis — sofortiger Abbruch statt Limit ausreizen |
| Integration/E2E            | 10 Runden pro Run                      | Cross-Lane-Inkompatibilität, Kontrakt-Lücke                              |
| Merge-Konflikt             | sofort                                 | Lanes haben dieselben Dateien widersprüchlich geändert                   |
| Review-Loops               | 10 Codex-Runden / 3 Fix-Zyklen je Lane | Grundsatzproblem, das Reviews nicht „wegfixen" können                    |
| CI                         | 1 Re-Entry, 45 min Budget              | Infrastruktur-/Pipeline-Problem, Umgebungsunterschied                    |
| Unlesbare Reviewer-Antwort | sofort                                 | Agent/Codex hat das Findings-JSON-Schema nicht eingehalten               |

## 10. Troubleshooting & FAQ

`Fehler: .adw/config.yaml fehlt …`

<div class="inner">

Das Ziel-Repo hat keine (gültige) Workflow-Config. `examples/config.yaml` kopieren und anpassen — siehe [Abschnitt 2](#zielrepo). ADW rät bewusst keine Defaults.

</div>

Run steht auf `awaiting_approval` — `resume` „tut nichts"

<div class="inner">

Das ist das Approval-Gate: `resume` pausiert wieder mit Exit 2, weil die Freigabe fehlt. `adw approve <run_id>` erteilt sie und setzt fort. `resume` ist für Crash-Fortsetzung gedacht.

</div>

`--parallel` wird abgelehnt

<div class="inner">

`--parallel verlangt eine frontend- UND backend-Lane`: Die Config des Ziel-Repos definiert nur eine Lane. Entweder die zweite Lane (mit eigenen Gates) ergänzen oder ohne `--parallel` laufen lassen.

</div>

`.adw/spec.md ist getrackt und hat uncommittete Änderungen …`

<div class="inner">

Im Ziel-Repo liegen ungesicherte Änderungen an einem früheren ADW-Artefakt. ADW verwirft nie stillschweigend deine Edits: erst committen oder stashen, dann neu starten.

</div>

Eskalation „HEAD hat sich außerhalb des Orchestrators bewegt" / „Build-Agent hat selbst committet"

<div class="inner">

Schutzmechanismus: Commits macht ausschließlich der Orchestrator — nach nachweislich grünen Gates. Wenn im Lane-Worktree fremde Commits auftauchen (manuell hineingearbeitet? paralleles Tooling?), bricht ADW ab, statt ungeprüfte Änderungen weiterzureichen. Worktree-Stand klären, neuen Run starten.

</div>

Eskalation „Tests nach reinem Test-Lauf grün — RED nicht bestätigt"

<div class="inner">

Die Lane hat ein `tdd: true`-Gate, aber die markierten Gates waren direkt nach dem reinen Test-Lauf grün. Dann beweisen die neuen Tests nichts: Entweder decken sie das geforderte Verhalten nicht ab, oder das Verhalten existiert bereits. ADW dreht darauf keine Schleife, sondern übergibt an dich — Plan/Kontrakt schärfen (oder die Anforderung streichen) und einen neuen Run starten. Verwandte Eskalationen aus derselben Stufe: Der Test-Lauf hat den Worktree unverändert gelassen (keine Tests = kein Beweis), er hat Dateien gelöscht (rote Gates durch Löschen sind kein Beweis), oder die Implementierung hat die Tests entfernt, die RED bewiesen haben.

</div>

Pipeline rot „ohne verwertbare Job-Logs"

<div class="inner">

Die Pipeline war `canceled`/`skipped` oder scheiterte ohne fehlgeschlagene Jobs (z. B. YAML-Fehler in der CI-Config). Ohne Logs würde der Log-Analyst nur raten — deshalb geht das direkt an dich.

</div>

Warum sehe ich `adw_dry_run_*.md`-Dateien auf Dry-Run-Branches?

<div class="inner">

Das sind die Demo-Artefakte der Mock-Build-Agenten. Dry-Run-Branches (`adw/<run_id>/…`) sind rein lokal und werden nie gepusht — sie lassen sich gefahrlos löschen.

</div>

Was kostet ein Lauf?

<div class="inner">

Dry-Run: nichts (0 Tokens). Echter Lauf: **keine API-Kosten** — alles läuft über deinen Claude-Plan (siehe [Abrechnung](#abrechnung)), modellökonomisch verteilt: Fable 5 nur für Spec/Plan/finalen Review, Opus 4.8 baut, Sonnet 5 triagiert, Codex läuft über dein Codex-Abo. Die Limits (Abschnitt 9) deckeln den Worst Case strukturell.

</div>

`Agent-Lauf abgebrochen (z. B. Plan-Limit erschöpft) …`

<div class="inner">

Das Abo-Fenster ist leer oder die Claude-CLI konnte nicht antworten. Kein Handlungsbedarf am Run selbst: Er steht am letzten Checkpoint (Phase unverändert, kein `escalation.md`). Nach dem Limit-Reset einfach `adw resume <run_id>` — der Run setzt mit denselben Agent-Sessions fort.

</div>

## 11. Glossar

| Begriff             | Bedeutung                                                                                                                        |
|---------------------|----------------------------------------------------------------------------------------------------------------------------------|
| **Lane**            | Ein Workstream (backend/frontend) mit eigenem Git-Worktree, eigenem Branch, eigener Agent-Session und eigenen Ports.             |
| **Draft-Stage**     | Phase 1–2: Claude-Agent und Codex schreiben parallel je einen eigenen Entwurf des Artefakts nach `.adw/runs/<run_id>/drafts/`.   |
| **Synthese**        | Der Agent, der beide Entwürfe zu EINEM Best-of-Artefakt merged und die Zusammenfassung fürs Approval-Gate schreibt.              |
| **Gate**            | Ein konfiguriertes Prüfkommando (Linter, Tests, …) mit hartem Timeout. Alle Gates grün = Bedingung für jeden Commit.             |
| **RED-Stufe**       | Initial-Build einer Lane mit `tdd: true`-Gate: Der Agent bekommt die Anweisung, nur Tests zu schreiben, danach beweist der Orchestrator die markierten Gates rot — vor dem Implementierungs-Lauf. |
| **Kontrakt**        | `.adw/contract.yaml` — die vereinbarte Schnittstelle (OpenAPI/Typen/Events), gegen die beide Lanes unabhängig bauen.             |
| **Finding**         | Strukturiertes Review-Ergebnis (JSON): Severity P1–P3, Lane, Datei, Problem, Fix-Empfehlung, ggf. Kategorie.                     |
| **scope_gap**       | Finding-Kategorie „fehlt, war aber nie im Plan" → wird Follow-up-Issue, kein Auto-Umbau.                                         |
| **Circuit-Breaker** | Abbruchregel: liefert eine Fix-Iteration exakt dasselbe Fehlerbild wie zuvor, wird sofort eskaliert statt das Limit auszureizen. |
| **Eskalation**      | Kontrollierte Übergabe an den Menschen: Exit ≠ 0 + `escalation.md` mit Stand und Grund.                                          |
| **Session-Resume**  | Fix-Tasks gehen an die *bestehende* Agent-Session (voller Kontext) statt an einen frischen Agenten.                              |
| **Run-ID**          | 8-stellige Hex-ID eines Laufs; alle Artefakte liegen unter `.adw/runs/<run_id>/`.                                                |

[↑ nach oben](#tldr)

</div>

ADW User-Handbuch · generiert am 2026-07-15 · Quelle: Repo `agentic-developer-workflow` (README.de.md, docs/SPEC.de.md, docs/PLAN.de.md, Stand main `9b89dd6`) · Technische Details: `docs/spec/ADW-TECHNISCHE-SPEC.de.html`
