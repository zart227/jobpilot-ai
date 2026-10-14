#!/usr/bin/env sh
# One-shot owner-less remediation: verify, integration DB, drills, audit matrix (no live Kwork UI).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "======== autonomous_remediation_run ($(date -Iseconds)) ========"

export REMEDIATION_MAX_OPEN=0

echo "--- ruff ---"
"$PY" -m ruff check app tests scripts

echo "--- checklist gate ---"
"$PY" scripts/remediation_checklist_status.py

echo "--- remediation_verify (unit) ---"
JOBPILOT_INTEGRATION_TESTS= ./scripts/remediation_verify.sh

echo "--- integration database ---"
./scripts/bootstrap_integration_db.sh

unset DATABASE_URL REDIS_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND
# shellcheck disable=SC1091
set -a
. "${ROOT}/.jobpilot_integration_test.env"
set +a
export JOBPILOT_INTEGRATION_TESTS=1
export API_RATE_LIMIT_ENABLED=false

echo "--- integration pytest (full) ---"
"$PY" -m pytest -q

echo "--- audit matrix ---"
"$PY" scripts/audit_remediation_status.py --write docs/audit-remediation-matrix.md

echo "--- staging drills ---"
export RUN_DEPENDENCY_DRILLS="${RUN_DEPENDENCY_DRILLS:-1}"
./scripts/staging_drill_automated.sh

if [ -f data/live_kwork_signoff.json ]; then
  echo "--- live signoff validate ---"
  "$PY" scripts/validate_live_signoff.py
fi

echo "--- production rollout (test DB already migrated; smoke API) ---"
JOBPILOT_INTEGRATION_TESTS= ./scripts/production_rollout.sh || echo "rollout smoke: skipped if API down"

echo ""
echo "OK: autonomous_remediation_run finished."
echo "Not automated: real Kwork UI in browser (PLATFORM_DRY_RUN=false on a test order)."
