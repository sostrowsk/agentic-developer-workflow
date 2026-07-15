#!/usr/bin/env bash
# Preflight-Check für einen ADW-Lauf: Werkzeuge, Login, Ziel-Repo-Config.
# Aufruf: preflight.sh <ziel-repo> [gitlab|github]
# Exit 0 = startklar; Exit 1 = Befunde (stehen auf stdout, Präfix FEHLT/WARNUNG).
set -u

TARGET="${1:?Aufruf: preflight.sh <ziel-repo> [gitlab|github]}"
FORGE="${2:-auto}"
ADW_HOME="${ADW_HOME:-$HOME/PycharmProjects/agentic-developer-workflow}"
rc=0

fail() { echo "FEHLT: $*"; rc=1; }
warn() { echo "WARNUNG: $*"; }

command -v uv >/dev/null || fail "uv nicht installiert"
[ -f "$ADW_HOME/pyproject.toml" ] || fail "ADW-Orchestrator nicht unter $ADW_HOME (ADW_HOME setzen?)"
command -v git >/dev/null || fail "git nicht installiert"
command -v codex >/dev/null || fail "codex-CLI nicht installiert (Reviews)"

# Claude-Login: stored-login-only, ohne Login bricht ADW vor dem ersten Agent-Lauf ab.
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
if [ ! -f "$CLAUDE_DIR/.credentials.json" ] && [ "$(uname)" != "Darwin" ]; then
  fail "kein gespeicherter Claude-CLI-Login ($CLAUDE_DIR/.credentials.json) — einmal 'claude' interaktiv anmelden"
fi

[ -d "$TARGET/.git" ] || fail "$TARGET ist kein Git-Repo"
[ -f "$TARGET/.adw/config.yaml" ] || fail "$TARGET/.adw/config.yaml fehlt — Template: assets/config-template.yaml"

# Forge-Werkzeug passend zum Hosting
ORIGIN="$(git -C "$TARGET" remote get-url origin 2>/dev/null || true)"
if [ "$FORGE" = "auto" ]; then
  case "$ORIGIN" in
    *github*) FORGE=github ;;
    *gitlab*) FORGE=gitlab ;;
    *) FORGE=unbekannt ;;
  esac
fi
case "$FORGE" in
  github) command -v gh >/dev/null || fail "gh-CLI nicht installiert (GitHub-Projekt)" ;;
  gitlab) command -v glab >/dev/null || fail "glab-CLI nicht installiert (GitLab-Projekt)" ;;
  *) warn "Hosting nicht erkennbar (origin: ${ORIGIN:-keins}) — ci.provider in .adw/config.yaml setzen" ;;
esac

if [ -n "$(git -C "$TARGET" status --porcelain -- .adw/spec.md .adw/plan.md .adw/contract.yaml 2>/dev/null)" ]; then
  warn "uncommittete Änderungen an .adw-Artefakten im Ziel-Repo — ADW bricht damit ab (committen/stashen)"
fi

[ $rc -eq 0 ] && echo "OK: startklar für ADW-Läufe gegen $TARGET (Forge: $FORGE)"
exit $rc
