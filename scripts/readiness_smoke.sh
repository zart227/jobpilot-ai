#!/usr/bin/env sh
set -eu

BASE_URL="${JOBPILOT_API_URL:-http://127.0.0.1:8000}"

echo "Liveness: $BASE_URL/health"
curl -fsS "$BASE_URL/health" | head -c 200
echo ""

echo "Readiness: $BASE_URL/ready"
code=$(curl -s -o /tmp/jobpilot_ready.json -w "%{http_code}" "$BASE_URL/ready")
cat /tmp/jobpilot_ready.json
echo ""
echo "HTTP $code (200=ready/degraded, 503=not_ready)"

case "$code" in
  200|503) exit 0 ;;
  *) exit 1 ;;
esac
