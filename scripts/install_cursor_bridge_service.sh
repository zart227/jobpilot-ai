#!/usr/bin/env bash
# Install systemd user service for persistent Cursor bridge.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_SRC="${ROOT}/deploy/jobpilot-cursor-bridge.service"
SERVICE_DST="${HOME}/.config/systemd/user/jobpilot-cursor-bridge.service"

mkdir -p "${HOME}/.config/systemd/user"
sed "s|%h/jobpilot-ai|${ROOT}|g" "$SERVICE_SRC" >"$SERVICE_DST"

systemctl --user daemon-reload
systemctl --user enable --now jobpilot-cursor-bridge.service

echo "Installed and started: jobpilot-cursor-bridge.service"
echo "Status: systemctl --user status jobpilot-cursor-bridge"
echo "Logs:   journalctl --user -u jobpilot-cursor-bridge -f"
