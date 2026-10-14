#!/usr/bin/env sh
# HTTP probe to host Cursor bridge from a running compose service (default: telegram-bot).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE="${COMPOSE:-docker compose}"
SERVICE="${CURSOR_BRIDGE_DRILL_SERVICE:-telegram-bot}"
BRIDGE_URL="${CURSOR_BRIDGE_PROBE_URL:-http://host.docker.internal:9247}"

echo "== cursor_bridge_from_container ($(date -Iseconds)) =="
echo "service=$SERVICE url=$BRIDGE_URL"

if [ -z "$($COMPOSE ps --status running -q "$SERVICE" 2>/dev/null)" ]; then
  echo "SKIP: $SERVICE is not running (docker compose up -d $SERVICE)"
  exit 0
fi

echo "--- curl from container (no auth; expect non-2xx or SDK error) ---"
$COMPOSE exec -T "$SERVICE" curl -sS -o /tmp/cursor_bridge_probe_body.txt -w "HTTP %{http_code}\n" \
  --max-time 8 "${BRIDGE_URL%/}/" || echo "curl failed (bridge down or host-gateway blocked)"

if $COMPOSE exec -T "$SERVICE" test -f /tmp/cursor_bridge_probe_body.txt 2>/dev/null; then
  $COMPOSE exec -T "$SERVICE" head -c 200 /tmp/cursor_bridge_probe_body.txt 2>/dev/null || true
  echo ""
fi

echo "For authenticated dev-agent path, ensure data/cursor_bridge.env is mounted and run make cursor-bridge-drill on host."
