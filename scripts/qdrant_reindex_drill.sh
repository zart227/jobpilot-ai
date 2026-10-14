#!/usr/bin/env sh
# Safe by default: probe Qdrant + count embedding_pending. Set QDRANT_DRILL_RUN=1 to call reindex batch.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== qdrant_reindex_drill ($(date -Iseconds)) =="

export DEBUG="${DEBUG:-false}"

LIMIT="${QDRANT_DRILL_LIMIT:-25}"
if [ "${QDRANT_DRILL_RUN:-}" = "1" ]; then
  echo "mode: run reindex batch (limit=$LIMIT)"
  "$PY" scripts/qdrant_embedding_drill.py --run --limit "$LIMIT"
else
  echo "mode: dry-run (set QDRANT_DRILL_RUN=1 to index pending jobs)"
  "$PY" scripts/qdrant_embedding_drill.py --limit "$LIMIT"
fi
