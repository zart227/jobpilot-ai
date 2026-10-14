#!/usr/bin/env sh
# Subset of staging drills that can run without stopping production deps (safe on dev).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== staging_drill_automated ($(date -Iseconds)) =="

echo "--- audit matrix ---"
"$PY" scripts/audit_remediation_status.py --write docs/audit-remediation-matrix.md

echo "--- migration preflight ---"
if [ -n "${DATABASE_URL:-}" ]; then
  if ! "$PY" scripts/migration_preflight.py; then
    echo "preflight: failed (wrong DATABASE_URL or DB down; compose uses host port 5434)"
  fi
else
  echo "skip: DATABASE_URL unset"
fi

echo "--- API readiness ---"
if [ -x ./scripts/readiness_smoke.sh ]; then
  ./scripts/readiness_smoke.sh || echo "readiness_smoke: non-fatal (API may be down)"
fi

echo "--- scoring weights inventory ---"
if [ -n "${DATABASE_URL:-}" ]; then
  "$PY" scripts/rollback_scoring_weights.py --list 2>/dev/null || echo "rollback list: skipped"
else
  echo "skip: DATABASE_URL unset"
fi

echo ""
echo "--- optional local dependency drills (disruptive) ---"
if [ "${RUN_DEPENDENCY_DRILLS:-}" = "1" ]; then
  ./scripts/dependency_failure_drill.sh || echo "postgres drill: failed"
  ./scripts/redis_failure_drill.sh || echo "redis drill: failed"
else
  echo "skip: set RUN_DEPENDENCY_DRILLS=1 to run make dependency-drill / redis-drill"
fi

echo "--- cursor bridge policy ---"
./scripts/cursor_bridge_drill.sh || echo "cursor_bridge_drill: failed"

echo "--- qdrant embedding (dry-run) ---"
./scripts/qdrant_reindex_drill.sh || echo "qdrant drill: failed"

echo "--- scoring weights inventory ---"
./scripts/scoring_weights_drill.sh 2>/dev/null || echo "scoring drill: skipped"

echo "--- cursor bridge from container (optional) ---"
./scripts/cursor_bridge_from_container.sh || echo "cursor from container: skipped"

echo "Manual drills (see docs/staging-drill-log.md): QDRANT_DRILL_RUN=1, live Kwork on staging."
echo "Copy this output into staging-drill-log.md when run on staging."
