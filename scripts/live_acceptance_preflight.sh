#!/usr/bin/env sh
# Automated gates before a permitted live Kwork check (see docs/kwork-live-acceptance.md).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== JobPilot live acceptance preflight (automated only) =="

if [ "${REQUIRE_LIVE_KWORK:-0}" = "1" ]; then
  if [ "${PLATFORM_DRY_RUN:-true}" = "true" ]; then
    echo "REQUIRE_LIVE_KWORK=1 but PLATFORM_DRY_RUN is still true" >&2
    exit 1
  fi
  echo "PLATFORM_DRY_RUN=false (live mode env detected)"
else
  echo "Dry-run mode OK (set REQUIRE_LIVE_KWORK=1 to enforce live env)"
fi

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

"$PY" -m pytest -q \
  tests/test_acceptance_dry_run.py \
  tests/test_acceptance_inbox_flow.py \
  tests/test_cursor_bridge_policy.py

echo ""
echo "Manual steps still required (checklist):"
echo "  - docs/kwork-live-acceptance.md"
echo "  - docs/remediation-checklists.md (сквозная приёмка требует отдельного review критериев и live evidence)"
echo "  - Record date, test order id, and results in remediation-checklists journal"
