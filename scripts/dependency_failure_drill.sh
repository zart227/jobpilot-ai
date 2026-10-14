#!/usr/bin/env sh
# Local drill: stop Postgres briefly and confirm /ready reports not_ready, then recover.
# Requires: docker compose postgres service, API on JOBPILOT_API_URL (default :8000).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE_URL="${JOBPILOT_API_URL:-http://127.0.0.1:8000}"
COMPOSE="${COMPOSE:-docker compose}"

echo "== dependency_failure_drill ($(date -Iseconds)) =="
echo "API: $BASE_URL"

echo "--- baseline /ready ---"
before=$(curl -s -o /tmp/jobpilot_drill_ready.json -w "%{http_code}" "$BASE_URL/ready" || echo 000)
cat /tmp/jobpilot_drill_ready.json
echo ""
echo "HTTP $before"

echo "--- stop postgres (compose) ---"
$COMPOSE stop postgres

sleep 3

echo "--- /ready while postgres down (expect 503 not_ready) ---"
down=$(curl -s -o /tmp/jobpilot_drill_ready_down.json -w "%{http_code}" "$BASE_URL/ready" || echo 000)
cat /tmp/jobpilot_drill_ready_down.json
echo ""
echo "HTTP $down"

echo "--- start postgres ---"
$COMPOSE start postgres

TRIES=30
while [ "$TRIES" -gt 0 ]; do
  if $COMPOSE exec -T postgres pg_isready -U jobpilot -d jobpilot_ai >/dev/null 2>&1; then
    break
  fi
  TRIES=$((TRIES - 1))
  sleep 1
done

if [ "$TRIES" -eq 0 ]; then
  echo "postgres did not become ready in time" >&2
  exit 1
fi

sleep 2

echo "--- /ready after recovery (expect 200 ready or degraded) ---"
after=$(curl -s -o /tmp/jobpilot_drill_ready_after.json -w "%{http_code}" "$BASE_URL/ready" || echo 000)
cat /tmp/jobpilot_drill_ready_after.json
echo ""
echo "HTTP $after"

failed=0
case "$down" in
  503) ;;
  *) echo "WARN: expected HTTP 503 while postgres down, got $down" >&2; failed=1 ;;
esac
case "$after" in
  200) ;;
  *) echo "WARN: expected HTTP 200 after recovery, got $after" >&2; failed=1 ;;
esac

if [ "$failed" -eq 0 ]; then
  echo "OK: dependency_failure_drill passed"
else
  echo "dependency_failure_drill completed with warnings (record in docs/staging-drill-log.md)" >&2
  exit 1
fi
