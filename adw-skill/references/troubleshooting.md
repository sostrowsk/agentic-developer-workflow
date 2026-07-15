# ADW-Troubleshooting: Exit 1, Eskalationen, Fehlermeldungen

## Erstdiagnose (immer in dieser Reihenfolge)

1. `uv run adw status --repo <ziel-repo>` — run_id und Phase feststellen.
2. Phase `escalated` → `.adw/runs/<run_id>/escalation.md` lesen: enthält
   erreichten Stand, Phase und den konkreten Grund (Gate-Output, Konflikt,
   Limit, Circuit-Breaker).
3. Phase NICHT `escalated` (z. B. `build`, `ci`) → der Run ist nur
   unterbrochen (Crash/Plan-Limit) und per `adw resume <run_id>` fortsetzbar.

## Eskalations-Typen und richtige Reaktion

| Grund im Report | Bedeutung | Reaktion |
|---|---|---|
| Gate-Iterationen erreicht (10) / Circuit-Breaker | Build-Agent kommt an den Gates nicht vorbei bzw. dreht sich im Kreis | Gate-Output im Report lesen; oft widersprechen sich Issue und Gates oder ein Test ist flaky. Ursache beheben, neuen Run starten |
| Integrations-Merge fehlgeschlagen | Lanes haben dieselben Dateien widersprüchlich geändert | Konflikt manuell sichten; Kontrakt/Plan war zu unscharf — Issue präziser schneiden oder Single-Lane fahren |
| E2E-/Review-Runden erreicht (10) / Fix-Zyklen (3) | Grundsatzproblem, das Fixes nicht lösen | Findings im Report bewerten; ggf. Issue-Scope reduzieren |
| „Review/Triage/Log-Analyst unlesbar" | Reviewer hat das Findings-JSON-Schema verletzt | Einfach neuen Run starten (transient); bei Wiederholung Modell-/CLI-Versionen prüfen |
| „HEAD hat sich bewegt" / „Agent hat selbst committet" / Branch-Wechsel | Fremdeingriff in einen Lane-Worktree | Nie manuell in `.adw/runs/<id>/trees/…` arbeiten; Worktree-Zustand klären, neuen Run starten |
| „ci.provider … setzen" | Hosting nicht aus origin-URL erkennbar | `ci.provider: gitlab|github` in `.adw/config.yaml` setzen |
| Pipeline rot nach Re-Entry / CI-Timeout / „rot ohne Job-Logs" | CI-Problem jenseits des Codes (Infra, CI-YAML) | Pipeline in GitLab/GitHub direkt ansehen; CI-Config fixen, neuen Run starten |
| „.adw/<artefakt> hat uncommittete Änderungen" | Nutzer-Edits würden verworfen | Änderungen committen oder stashen, dann erneut starten |

## Kein Eskalations-Report vorhanden (Exit 1 ohne escalation.md)

- **„Agent-Lauf abgebrochen (z. B. Plan-Limit erschöpft)"**: Claude-Plan-Fenster
  leer oder CLI-Fehler. Run steht am Checkpoint. Limit-Reset abwarten, dann
  `uv run adw resume <run_id>`. Kein Datenverlust: Sessions, offene Fix-Tasks
  und Zähler sind persistiert.
- **„Fehler: … config.yaml …"**: Config fehlt/ungültig — siehe
  [config.md](config.md).
- **„genau EINE Issue-Quelle angeben"**: `--issue`, `--gitlab-issue` und
  `--github-issue` schließen sich aus.
- **„Keine gespeicherte Claude-CLI-Anmeldung"**: einmal `claude` interaktiv
  anmelden (stored-login-only; API-Keys werden bewusst ignoriert).
- **Push fehlgeschlagen**: origin-Remote/Zugriffsrechte prüfen (SSH-Agent läuft?
  `SSH_AUTH_SOCK` wird nur dem Push durchgereicht).

## Wissenswertes für die Analyse

- Limits: 10 Gate-Iterationen je Task, 10 E2E-/Review-Runden, 3 Fix-Zyklen je
  Lane, 1 CI-Re-Entry. Circuit-Breaker: zweimal exakt derselbe Fehler →
  sofortige Eskalation. Ein Resume verschafft KEINE zusätzlichen Versuche.
- `followups.md` im Run-Ordner enthält scope_gap-Findings des finalen Reviews —
  das sind vorgeschlagene Follow-up-Issues, keine Fehler.
- Dry-Run-Artefakte (`adw_dry_run_*.md`, Branches `adw/<id>/…`) sind lokal und
  können gefahrlos gelöscht werden.
- Alle Run-Daten liegen gitignored unter `.adw/runs/<run_id>/`
  (state.json, archivierte Artefakte, Reports, Lane-Worktrees).
