#!/usr/bin/env sh
# Production/staging rollout helper (run on host with DB access). Does not send Kwork messages.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== JobPilot production rollout preflight =="
echo "1/4 Backup (run if not done): ./scripts/backup_postgres.sh ./backups"
echo "2/4 Migration preflight (read-only checks on DATABASE_URL)"
if ! "$PY" scripts/migration_preflight.py; then
  if [ "${AUTO_RESOLVE_PROPOSAL_CONFLICTS:-}" = "1" ]; then
    echo "preflight failed; AUTO_RESOLVE_PROPOSAL_CONFLICTS=1 — superseding extra active proposals"
    "$PY" scripts/resolve_active_proposal_conflicts.py --apply --confirm
    "$PY" scripts/migration_preflight.py
  else
    echo "Preflight failed. If multiple active proposals per job:" >&2
    echo "  $PY scripts/resolve_active_proposal_conflicts.py" >&2
    echo "  $PY scripts/resolve_active_proposal_conflicts.py --apply --confirm  # after backup" >&2
    echo "Or: AUTO_RESOLVE_PROPOSAL_CONFLICTS=1 $0" >&2
    exit 1
  fi
fi

echo "3/4 Alembic upgrade head"
"$PY" -m alembic upgrade head

echo "4/4 Readiness smoke (API must be up; set JOBPILOT_API_URL if needed)"
if [ -x ./scripts/readiness_smoke.sh ]; then
  ./scripts/readiness_smoke.sh
else
  echo "skip: readiness_smoke.sh missing"
fi

echo ""
echo "OK: schema upgraded. Start/restart workers: docker compose up -d celery-worker celery-beat"
echo "Post-deploy: make verify · REMEDIATION_MAX_OPEN=0 make checklist-gate"
