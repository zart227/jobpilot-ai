#!/usr/bin/env sh
set -eu

BASE_URL="${JOBPILOT_API_URL:-http://127.0.0.1:8000}"
READY_FILE=$(mktemp "${TMPDIR:-/tmp}/jobpilot_ready.XXXXXXXX")
HEALTH_FILE=$(mktemp "${TMPDIR:-/tmp}/jobpilot_health.XXXXXXXX")
trap 'rm -f "$READY_FILE" "$HEALTH_FILE"' EXIT HUP INT TERM

echo "Liveness: $BASE_URL/health"
curl -fsS "$BASE_URL/health" -o "$HEALTH_FILE"
head -c 200 "$HEALTH_FILE"
echo ""

echo "Readiness: $BASE_URL/ready"
code=$(curl -sS -o "$READY_FILE" -w "%{http_code}" "$BASE_URL/ready")
cat "$READY_FILE"
echo ""
echo "HTTP $code (200=ready/degraded, 503=not_ready)"

if [ "$code" != 200 ]; then
  echo "Readiness failed: HTTP $code" >&2
  exit 1
fi
python3 - "$READY_FILE" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as source:
        result = json.load(source)
    valid = isinstance(result, dict) and result.get("ready") is True and result.get("status") in {"ready", "degraded"}
except (OSError, ValueError):
    valid = False
if not valid:
    print("Readiness failed: invalid or not-ready response", file=sys.stderr)
    sys.exit(1)
PY
