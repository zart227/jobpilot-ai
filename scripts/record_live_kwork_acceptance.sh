#!/usr/bin/env sh
# Append a live Kwork acceptance record to remediation-checklists journal (owner-run).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECKLIST="$ROOT/docs/remediation-checklists.md"

ORDER_ID="${1:-}"
ATTEMPT_ID="${2:-}"
RESULT="${3:-}"
DATE="${4:-$(date -I)}"

if [ -z "$ORDER_ID" ] || [ -z "$ATTEMPT_ID" ] || [ -z "$RESULT" ]; then
  echo "Usage: $0 <kwork_order_id> <send_attempt_uuid> <result_summary> [date]" >&2
  echo "Optional: ./scripts/mark_live_checklist_complete.sh if checklist items still open" >&2
  exit 1
fi

ENTRY="
### Live Kwork $DATE

| Поле | Значение |
| ---- | -------- |
| Заказ | $ORDER_ID |
| send_attempt | $ATTEMPT_ID |
| Результат | $RESULT |
| Команда | REQUIRE_LIVE_KWORK=1 ./scripts/live_acceptance_preflight.sh |
"

printf '%s\n' "$ENTRY" >> "$CHECKLIST"
echo "Appended journal entry to $CHECKLIST"
echo "Update the two [ ] items in § Сквозная приёмка and remediation-progress.md (10/10)."
