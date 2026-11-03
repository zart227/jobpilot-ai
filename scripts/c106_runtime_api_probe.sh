#!/usr/bin/env sh
# C106 runtime probe: public health OK; data/control routes reject unauthenticated callers.
set -eu

BASE_URL="${JOBPILOT_API_URL:-http://127.0.0.1:8000}"

echo "== c106_runtime_api_probe ($(date -Iseconds)) =="
echo "API: $BASE_URL"

health=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health" || echo 000)
ready=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/ready" || echo 000)
jobs=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/jobs" || echo 000)

echo "GET /health HTTP $health"
echo "GET /ready HTTP $ready"
echo "GET /jobs (no Authorization) HTTP $jobs"

failed=0
case "$health" in 200) ;; *) echo "WARN: expected /health 200" >&2; failed=1 ;; esac
case "$jobs" in
  401|403|503) ;;
  *) echo "WARN: expected /jobs 401/403/503 without token, got $jobs" >&2; failed=1 ;;
esac

if [ "$failed" -eq 0 ]; then
  echo "OK: c106_runtime_api_probe"
else
  echo "c106_runtime_api_probe completed with warnings" >&2
  exit 1
fi
