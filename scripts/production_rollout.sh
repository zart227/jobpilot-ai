#!/usr/bin/env sh
# Production/staging rollout helper (run on host with DB access). Does not send Kwork messages.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

BACKUP="${JOBPILOT_ROLLOUT_BACKUP:-}"
BACKUP_SHA256="${JOBPILOT_ROLLOUT_BACKUP_SHA256:-}"
if [ ! -x ./scripts/readiness_smoke.sh ]; then
  echo "Readiness smoke is missing or not executable; rollout stopped." >&2
  exit 1
fi
if [ -z "$BACKUP" ] || [ -z "$BACKUP_SHA256" ]; then
  echo "Set JOBPILOT_ROLLOUT_BACKUP and JOBPILOT_ROLLOUT_BACKUP_SHA256 before rollout." >&2
  exit 1
fi
"$PY" - "$BACKUP" "$BACKUP_SHA256" <<'PY'
import gzip
import hashlib
import re
import sys

path, expected = sys.argv[1:]
if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
    sys.exit("Invalid backup SHA-256")
try:
    digest = hashlib.sha256()
    with open(path, "rb") as backup:
        for block in iter(lambda: backup.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected.lower():
        sys.exit("Backup SHA-256 mismatch")
    with gzip.open(path, "rb") as backup:
        header = backup.read(4096)
        if b"PostgreSQL database dump" not in header:
            sys.exit("Backup does not look like a PostgreSQL SQL dump")
        for _ in iter(lambda: backup.read(1024 * 1024), b""):
            pass  # Verify the entire gzip stream, not only the header.
except (OSError, EOFError, ValueError) as exc:
    sys.exit(f"Cannot validate backup: {exc}")
PY

echo "== JobPilot production rollout preflight =="
echo "1/5 Backup digest and gzip verified: $BACKUP"
if [ "${JOBPILOT_ROLLOUT_OPS_READINESS:-}" = "1" ] && [ -n "${JOBPILOT_WORKING_DATABASE_URL:-}" ]; then
  echo "2/5 Ops rollout readiness (JOBPILOT_ROLLOUT_OPS_READINESS=1)"
  DATABASE_URL="$JOBPILOT_WORKING_DATABASE_URL" PYTHONPATH=. "$PY" scripts/ops_rollout_readiness.py
elif [ "${JOBPILOT_ROLLOUT_OPS_READINESS:-}" = "1" ]; then
  echo "JOBPILOT_ROLLOUT_OPS_READINESS=1 requires JOBPILOT_WORKING_DATABASE_URL; rollout stopped." >&2
  exit 1
fi
echo "3/5 Migration preflight (read-only checks on DATABASE_URL)"
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

echo "4/5 Alembic upgrade head"
"$PY" -m alembic upgrade head

echo "5/5 Readiness smoke (API must be up; set JOBPILOT_API_URL if needed)"
./scripts/readiness_smoke.sh

echo ""
echo "OK: schema upgraded. Start/restart workers: docker compose up -d celery-worker celery-beat"
echo "Post-deploy: make verify · REMEDIATION_MAX_OPEN=0 make checklist-gate"
echo "Optional pre-migrate: JOBPILOT_ROLLOUT_OPS_READINESS=1 JOBPILOT_WORKING_DATABASE_URL=… $0"
