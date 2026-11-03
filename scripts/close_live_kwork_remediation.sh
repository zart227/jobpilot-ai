#!/usr/bin/env sh
# Evidence review and release gate only: never mutate checklist or generate owner approval.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY=python3; fi
"$PY" scripts/validate_live_signoff.py
echo "Evidence checked; C109/C110 still require owner review. No checklist changed."
REMEDIATION_MAX_OPEN=0 "$PY" scripts/remediation_checklist_status.py
