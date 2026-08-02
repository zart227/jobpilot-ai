#!/usr/bin/env bash
# Ensure Cursor SDK bridge is running on the host for Docker dev-agent.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo ".env not found — skip bridge setup"
  exit 0
fi

if ! grep -qE '^TELEGRAM_DEV_AGENT_ENABLED=(true|1|yes)$' .env 2>/dev/null; then
  echo "TELEGRAM_DEV_AGENT_ENABLED is off — skip bridge"
  exit 0
fi

if ! grep -qE '^CURSOR_API_KEY=.+' .env 2>/dev/null; then
  echo "CURSOR_API_KEY not set — skip bridge"
  exit 0
fi

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

PID_FILE="${ROOT}/data/cursor_bridge.pid"
LOG_FILE="${ROOT}/data/cursor_bridge.log"
PUBLIC_PORT="${CURSOR_BRIDGE_PORT:-9247}"
INTERNAL_PORT="${CURSOR_BRIDGE_INTERNAL_PORT:-19247}"
export CURSOR_WORKSPACE_HOST="${JOBPILOT_HOST_WORKSPACE:-$ROOT}"
export CURSOR_BRIDGE_PORT="$PUBLIC_PORT"
export CURSOR_BRIDGE_INTERNAL_PORT="$INTERNAL_PORT"

port_listening() {
  "$PYTHON" - <<PY
import socket
port = int("${INTERNAL_PORT}")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(0.5)
raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

if port_listening && [[ -f "${ROOT}/data/cursor_bridge.env" ]]; then
  echo "Cursor bridge already healthy on 127.0.0.1:${INTERNAL_PORT}"
  exit 0
fi

"${ROOT}/scripts/stop_cursor_bridge.sh" >/dev/null 2>&1 || true
systemctl --user stop jobpilot-cursor-bridge.service >/dev/null 2>&1 || true
mkdir -p "${ROOT}/data"

if systemctl --user is-active jobpilot-cursor-bridge.service >/dev/null 2>&1; then
  echo "Cursor bridge systemd service is active"
  exit 0
fi

echo "Starting Cursor bridge on 127.0.0.1:${INTERNAL_PORT}..."
nohup "$PYTHON" "${ROOT}/scripts/run_cursor_bridge.py" \
  --workspace "$CURSOR_WORKSPACE_HOST" \
  --host 127.0.0.1 \
  --port "$INTERNAL_PORT" \
  --public-port "$PUBLIC_PORT" \
  --force \
  --daemon \
  >>"$LOG_FILE" 2>&1 &
BRIDGE_PID=$!

for _ in $(seq 1 45); do
  if port_listening && [[ -f "${ROOT}/data/cursor_bridge.env" ]]; then
    echo "$BRIDGE_PID" >"$PID_FILE"
    echo "Cursor bridge ready (pid $BRIDGE_PID, docker :${PUBLIC_PORT} → :${INTERNAL_PORT})"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^jobpilot-telegram$'; then
      echo "Restarting telegram-bot to pick up bridge credentials..."
      docker compose restart telegram-bot >/dev/null
    fi
    exit 0
  fi
  if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
    echo "Cursor bridge failed to start — see $LOG_FILE" >&2
    tail -40 "$LOG_FILE" >&2 || true
    exit 1
  fi
  sleep 1
done

echo "Cursor bridge startup timeout — see $LOG_FILE" >&2
exit 1
