#!/usr/bin/env bash
# Stop host Cursor bridge processes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTERNAL_PORT="${CURSOR_BRIDGE_INTERNAL_PORT:-19247}"

pkill -f "cursor-sdk-bridge.*--port ${INTERNAL_PORT}" 2>/dev/null || true
pkill -f "run_cursor_bridge.py.*--port ${INTERNAL_PORT}" 2>/dev/null || true
rm -f "${ROOT}/data/cursor_bridge.pid" "${ROOT}/data/cursor_bridge_socat.pid"

echo "Cursor bridge stopped."
