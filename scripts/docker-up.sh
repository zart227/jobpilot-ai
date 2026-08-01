#!/usr/bin/env bash
# Start JobPilot AI in Docker with host Cursor bridge for dev-agent.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export JOBPILOT_HOST_WORKSPACE="$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

"${ROOT}/scripts/ensure_cursor_bridge.sh" || true

echo "Starting Docker services..."
docker compose up -d --build "$@"

echo ""
echo "JobPilot AI is up."
echo "  API:      http://localhost:8000/health"
echo "  Telegram: /dev — remote dev agent"
echo ""
if [[ -f data/cursor_bridge.env ]]; then
  echo "Cursor bridge: configured (data/cursor_bridge.env)"
else
  echo "Cursor bridge: not running (dev-agent disabled until bridge starts)"
fi
