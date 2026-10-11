#!/usr/bin/env bash
#
# Startet die read-only Web-GUI des ADW-Orchestrators.
#
#   ./start-adw-gui.sh                 # http://127.0.0.1:8765/
#   ./start-adw-gui.sh 8790            # anderer Port
#   ./start-adw-gui.sh 8790 --open     # und Browser aufmachen
#
# Die GUI bindet ausschliesslich an Loopback: das Event-Log enthaelt rohe,
# unredigierte Agent-Ausgaben. Beenden mit Strg+C.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${ADW_GUI_HOST:-127.0.0.1}"
PORT="${ADW_GUI_PORT:-8765}"

if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    PORT="$1"
    shift
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv nicht gefunden. Installieren: https://docs.astral.sh/uv/" >&2
    exit 1
fi

if ss -ltnH "sport = :${PORT}" | grep -q .; then
    echo "Port ${PORT} ist schon belegt — laeuft die GUI bereits?" >&2
    echo "Anderen Port waehlen: ./start-adw-gui.sh 8790" >&2
    exit 1
fi

echo "ADW GUI:  http://${HOST}:${PORT}/"
exec uv run --directory "${REPO_ROOT}" \
    adw gui --repo "${REPO_ROOT}" --host "${HOST}" --port "${PORT}" "$@"
