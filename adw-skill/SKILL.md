---
name: adw-skill
description: Führt Entwicklungs-Issues vollautomatisch mit dem Agentic Developer Workflow (ADW) aus — 7 Phasen von Spec über Build und Reviews bis Push/CI, gegen ein beliebiges Git-Ziel-Repo (GitLab oder GitHub). Verwenden, wenn ein Issue/Feature/Bugfix per ADW umgesetzt werden soll ("lass ADW das Issue umsetzen", "adw run", "starte den Workflow für Issue #42"), ein ADW-Run fortgesetzt oder freigegeben werden soll (resume/approve), ein Run-Status oder eine Eskalation zu analysieren ist, oder ein Ziel-Repo ADW-fähig gemacht werden soll (.adw/config.yaml).
---

# ADW — Agentic Developer Workflow bedienen

ADW ist ein 7-Phasen-Orchestrator (Spec → Plan+Kontrakt → Build → Integration/E2E
→ Codex-Review → finaler Review → Push/CI). Kontrollfluss ist deterministischer
Code; Agenten (Claude-Code-CLI via Agent SDK, Codex-CLI) liefern nur Urteilsvermögen.
Abrechnung läuft über den Claude-Plan (stored-login-only), nie token-by-token.

Der Orchestrator liegt unter `$ADW_HOME` (Default:
`~/PycharmProjects/agentic-developer-workflow`). Alle Kommandos von dort ausführen:

```bash
cd "$ADW_HOME" && uv run adw <kommando>
```

## Workflow

### 1. Preflight

Vor dem ersten Lauf gegen ein Ziel-Repo prüfen:

```bash
scripts/preflight.sh <ziel-repo> [gitlab|github]
```

Meldet fehlende Werkzeuge (uv, git, codex, glab/gh), fehlenden Claude-Login und
fehlende `.adw/config.yaml`. Alle `FEHLT:`-Zeilen beheben, bevor ein Run startet.

### 2. Ziel-Repo ADW-fähig machen (einmalig)

Fehlt `.adw/config.yaml`: `assets/config-template.yaml` nach
`<ziel-repo>/.adw/config.yaml` kopieren und an das Projekt anpassen
(base_branch, Gates je Lane, optional e2e/ci). Schema und Regeln:
[references/config.md](references/config.md). Config committen.

### 3. Dry-Run vor dem ersten echten Lauf

Immer zuerst den Trockenlauf fahren — er verifiziert Config, Gates und den
kompletten Kontrollfluss mit Mocks (0 Tokens, kein Netz, kein Push):

```bash
uv run adw run --repo <ziel-repo> --issue "Demo" --dry-run --no-approval
```

Exit 0 = Setup steht. Exit 1 = zuerst `.adw/runs/<run_id>/escalation.md` lesen
(häufig: rote Gates in der Config).

### 4. Echter Lauf

```bash
uv run adw run --repo <ziel-repo> --issue "Issue-Text ..."       # Text direkt
uv run adw run --repo <ziel-repo> --gitlab-issue <id>            # GitLab via glab
uv run adw run --repo <ziel-repo> --github-issue <nr>            # GitHub via gh
```

Genau EINE Issue-Quelle angeben. Optionen: `--parallel` (FE+BE-Lanes, verlangt
beide Lanes in der Config; verbraucht Plan-Kontingent schneller),
`--no-approval` (Plan-Freigabe überspringen), `--base-branch <name>` (Override,
wird ab Run-Start gepinnt).

### 5. Exit-Code auswerten und handeln

| Exit | Bedeutung | Aktion |
|---|---|---|
| 0 | `done` — Branch gepusht, CI + Staging grün | Ergebnis melden, MR/PR vorschlagen |
| 2 | `awaiting_approval` — Plan-Freigabe-Pause | `.adw/runs/<run_id>/plan.md` + `contract.yaml` dem Nutzer zur Freigabe vorlegen; nach OK: `uv run adw approve <run_id> --repo <ziel-repo>` |
| 1 | Eskalation oder Fehler | Siehe [references/troubleshooting.md](references/troubleshooting.md) |

Die run_id steht in der CLI-Ausgabe; Übersicht: `uv run adw status --repo <ziel-repo>`.

### 6. Fortsetzen

- **Nach Crash oder Plan-Limit-Abbruch** („Agent-Lauf abgebrochen … adw resume"):
  `uv run adw resume <run_id> --repo <ziel-repo>` — setzt exakt am Checkpoint
  fort. Bei erschöpftem Claude-Plan-Fenster erst den Limit-Reset abwarten.
- **Nach Approval-Pause:** `approve`, nicht `resume` (resume pausiert erneut).
- **Eskalierte Runs** (`phase: escalated`) sind endgültig: Ursache aus dem
  Report klären, dann NEUEN Run starten.

## Wichtige Grenzen

- Plan-Freigabe (Exit 2) nie eigenmächtig per `--no-approval`/`approve`
  umgehen — die Freigabe ist Sache des Nutzers, außer er hat sie explizit
  delegiert.
- Niemals manuell in `.adw/runs/<id>/trees/…`-Worktrees committen oder
  Branches wechseln — der Orchestrator erkennt Fremd-Commits und eskaliert.
- Erster echter Lauf gegen ein neues Ziel-Repo nur nach grünem Dry-Run.
- Ein echter Lauf verbraucht Claude-Plan-Kontingent (Fable 5 + Opus 4.8 +
  Sonnet 5) und Codex-Abo-Kontingent — vor großen `--parallel`-Läufen den
  Nutzer auf den Verbrauch hinweisen.

## Eskalationen und Fehlerbilder

Für Exit 1, Eskalations-Reports, typische Fehlermeldungen und deren Behebung:
[references/troubleshooting.md](references/troubleshooting.md) lesen.
