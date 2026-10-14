#!/usr/bin/env sh
# After live Kwork: validate signoff; mark live checklist items [x] if still open (115/115 may already be closed).
set -eu

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

SIGNOFF_PATH="${JOBPILOT_LIVE_SIGNOFF_PATH:-$ROOT/data/live_kwork_signoff.json}"
export JOBPILOT_LIVE_SIGNOFF_PATH="$SIGNOFF_PATH"

if ! "$PY" scripts/validate_live_signoff.py; then
  echo "Fix $SIGNOFF_PATH first." >&2
  exit 1
fi

ORDER_ID="$("$PY" -c "import json, os; d=json.load(open(os.environ['JOBPILOT_LIVE_SIGNOFF_PATH'])); print(d['kwork_order_id'])")"
ATTEMPT_ID="$("$PY" -c "import json, os; d=json.load(open(os.environ['JOBPILOT_LIVE_SIGNOFF_PATH'])); print(d['send_attempt_id'])")"
APPROVED_AT="$("$PY" -c "import json, os; d=json.load(open(os.environ['JOBPILOT_LIVE_SIGNOFF_PATH'])); print(d['approved_at'])")"

CHECKLIST="$ROOT/docs/remediation-checklists.md"
PROGRESS="$ROOT/docs/remediation-progress.md"

LINE1="- [x] Получено отдельное разрешение на конкретную реальную проверку (заказ ${ORDER_ID}, ${APPROVED_AT})."
LINE2="- [x] После разрешённой проверки подтверждены внешний результат, attempt ${ATTEMPT_ID} и DB snapshot (${APPROVED_AT})."

if [ "$DRY_RUN" = 1 ]; then
  echo "DRY RUN — would update:"
  echo "  $CHECKLIST"
  grep '^- \[ \] Получено отдельное разрешение' "$CHECKLIST" || true
  echo "  -> $LINE1"
  grep '^- \[ \] После разрешённой проверки' "$CHECKLIST" || true
  echo "  -> $LINE2"
  echo "  $PROGRESS (сквозная 10/10)"
  exit 0
fi

if grep -q '^- \[ \] Получено отдельное разрешение' "$CHECKLIST"; then
  sed -i "s/^- \\[ \\] Получено отдельное разрешение.*/$(echo "$LINE1" | sed 's/[\/&]/\\&/g')/" "$CHECKLIST"
fi
if grep -q '^- \[ \] После разрешённой проверки' "$CHECKLIST"; then
  sed -i "s/^- \\[ \\] После разрешённой проверки.*/$(echo "$LINE2" | sed 's/[\/&]/\\&/g')/" "$CHECKLIST"
fi

sed -i 's/\*\*2 open\*\*/\*\*0 open — live signed off\*\*/' "$CHECKLIST" || true
sed -i 's/Чеклист сквозной | \*\*8\/10\*\*/Чеклист сквозной | **10\/10**/' "$PROGRESS" || true

REMEDIATION_MAX_OPEN=0 "$PY" scripts/remediation_checklist_status.py || {
  echo "Checklist gate failed after mark — review docs manually." >&2
  exit 1
}

"$PY" scripts/remediation_checklist_report.py

echo "Checklist live items marked [x]. Gate: REMEDIATION_MAX_OPEN=0 remediation_checklist_status.py"
