#!/usr/bin/env sh
# Review evidence only. Closing criteria requires human review of the full requirement.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY=python3; fi
if [ "${1:-}" = "--dry-run" ]; then
  "$PY" scripts/validate_live_signoff.py --artifacts-only
  echo "DRY RUN — review only; no checklist files changed."
else
  "$PY" scripts/validate_live_signoff.py
  echo "Evidence checked. Review C109/C110 manually; no checklist files changed."
fi
