#!/usr/bin/env sh
# Inventory scoring weight versions (read-only drill).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== scoring_weights_drill ($(date -Iseconds)) =="
"$PY" scripts/rollback_scoring_weights.py --list
