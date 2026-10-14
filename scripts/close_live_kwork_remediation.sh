#!/usr/bin/env sh
# Owner workflow after real Kwork test (see docs/owner-live-checklist-one-pager.md).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

SIGNOFF="${JOBPILOT_LIVE_SIGNOFF_PATH:-$ROOT/data/live_kwork_signoff.json}"

if [ ! -f "$SIGNOFF" ]; then
  echo "Missing signoff: $SIGNOFF" >&2
  echo "Copy data/live_kwork_signoff.json.example and fill." >&2
  exit 1
fi

export JOBPILOT_LIVE_SIGNOFF_PATH="$SIGNOFF"

echo "== validate signoff =="
"$PY" scripts/validate_live_signoff.py

ORDER_ID="$("$PY" -c "import json, os; print(json.load(open(os.environ['JOBPILOT_LIVE_SIGNOFF_PATH']))['kwork_order_id'])")"
ATTEMPT_ID="$("$PY" -c "import json, os; print(json.load(open(os.environ['JOBPILOT_LIVE_SIGNOFF_PATH']))['send_attempt_id'])")"
NOTES="$("$PY" -c "import json, os; print(json.load(open(os.environ['JOBPILOT_LIVE_SIGNOFF_PATH'])).get('notes','verified'))")"

echo "== journal =="
./scripts/record_live_kwork_acceptance.sh "$ORDER_ID" "$ATTEMPT_ID" "$NOTES"

echo "== mark checklist [x] =="
./scripts/mark_live_checklist_complete.sh

echo "== final gate =="
REMEDIATION_MAX_OPEN=0 "$PY" scripts/remediation_checklist_status.py
"$PY" scripts/remediation_checklist_report.py

echo "Done. Commit docs/remediation-checklists.md and remediation-progress.md if updated."
