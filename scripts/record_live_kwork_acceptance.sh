#!/usr/bin/env sh
# Validate and review evidence; never rewrite acceptance criteria or append an unverified live journal.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY=python3; fi
"$PY" scripts/validate_live_signoff.py
echo "Live evidence reviewed. Record owner review in docs/remediation-progress.md; checklist unchanged."
