#!/usr/bin/env sh
# Cursor bridge: policy tests (required) + optional live HTTP probe when bridge is running.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== cursor_bridge_drill ($(date -Iseconds)) =="

echo "--- policy tests ---"
"$PY" -m pytest -q tests/test_cursor_bridge_policy.py

echo "--- live probe (optional) ---"
"$PY" - <<'PY'
from __future__ import annotations

import socket
import sys
from pathlib import Path

import httpx

ROOT = Path.cwd()
INTERNAL_PORT = int(__import__("os").environ.get("CURSOR_BRIDGE_INTERNAL_PORT", "19247"))
ENV_FILE = ROOT / "data" / "cursor_bridge.env"


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


if not port_open("127.0.0.1", INTERNAL_PORT):
    print(f"SKIP live probe: nothing listening on 127.0.0.1:{INTERNAL_PORT}")
    print("Start: python scripts/run_cursor_bridge.py  (or ./scripts/docker-up.sh)")
    sys.exit(0)

base_internal = f"http://127.0.0.1:{INTERNAL_PORT}"
try:
    response = httpx.get(f"{base_internal}/", timeout=3.0)
    print(f"GET {base_internal}/ without auth → HTTP {response.status_code}")
    if response.status_code == 200:
        print("FAIL: unauthenticated bridge returned HTTP 200", file=sys.stderr)
        sys.exit(1)
except httpx.HTTPError as exc:
    print(f"GET {base_internal}/ without auth → error ({type(exc).__name__})")

if not ENV_FILE.is_file():
    print(f"SKIP authenticated probe: {ENV_FILE} missing")
    sys.exit(0)

values: dict[str, str] = {}
for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()

url = values.get("CURSOR_BRIDGE_BASE_URL", "").replace("host.docker.internal", "127.0.0.1")
token = values.get("CURSOR_BRIDGE_AUTH_TOKEN", "")
if not url or not token:
    print("SKIP authenticated probe: bridge env incomplete")
    sys.exit(0)

try:
    response = httpx.get(
        url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    print(f"GET {url.rstrip('/')} with bridge token → HTTP {response.status_code}")
except httpx.HTTPError as exc:
    print(f"GET bridge URL with token → error ({type(exc).__name__})")

print("OK: cursor_bridge_drill (policy + probe)")
PY
