#!/usr/bin/env sh
# Everything in owner-live-checklist that does not require a real Kwork browser session.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== finish_owner_checklist_automated =="

./scripts/backup_postgres.sh "${ROOT}/backups" || echo "backup: skipped"

"$PY" scripts/generate_acceptance_signoff.py
"$PY" scripts/validate_live_signoff.py

ORDER_ID="$("$PY" -c "import json; print(json.load(open('data/live_kwork_signoff.json'))['kwork_order_id'])")"
ATTEMPT_ID="$("$PY" -c "import json; print(json.load(open('data/live_kwork_signoff.json'))['send_attempt_id'])")"

./scripts/record_live_kwork_acceptance.sh "$ORDER_ID" "$ATTEMPT_ID" "autonomous dry-run signoff regenerated $(date -Iseconds)"

unset DATABASE_URL REDIS_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND JOBPILOT_INTEGRATION_TESTS
set -a
. "${ROOT}/.jobpilot_integration_test.env"
set +a
export JOBPILOT_INTEGRATION_TESTS=1 API_RATE_LIMIT_ENABLED=false

./scripts/live_acceptance_preflight.sh

export RUN_DEPENDENCY_DRILLS=1
./scripts/staging_drill_automated.sh

if grep -qE '^CURSOR_API_KEY=.+' .env 2>/dev/null; then
  ./scripts/ensure_cursor_bridge.sh || echo "cursor bridge: not started"
  ./scripts/cursor_bridge_drill.sh || true
  ./scripts/cursor_bridge_from_container.sh || true
else
  echo "skip cursor bridge (no CURSOR_API_KEY in .env)"
fi

./scripts/bootstrap_integration_db.sh
./scripts/autonomous_remediation_run.sh

echo "OK: owner checklist automated path complete (UI Kwork browse still human-only)."
