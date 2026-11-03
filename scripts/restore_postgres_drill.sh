#!/usr/bin/env sh
# Restore into a NEW drill database (default: jobpilot_ai_drill).
# This uses the configured Compose PostgreSQL server, not an isolated server.
# Only restore trusted dumps, preferably on a separate staging instance.
# Usage: JOBPILOT_DRILL_DB=jobpilot_ai_drill ./scripts/restore_postgres_drill.sh backups/foo.sql.gz
set -eu

DUMP="${1:?usage: restore_postgres_drill.sh <dump.sql.gz>}"
DRILL_DB="${JOBPILOT_DRILL_DB:-jobpilot_ai_drill}"

case "$DRILL_DB" in
  jobpilot_*_drill) ;;
  *) echo "Drill database must be named jobpilot_<name>_drill" >&2; exit 1 ;;
esac
case "$DRILL_DB" in
  *[!a-z0-9_]*|jobpilot__drill) echo "Unsafe drill database name" >&2; exit 1 ;;
esac
if [ "${#DRILL_DB}" -gt 63 ]; then
  echo "Drill database name exceeds PostgreSQL's 63-byte limit" >&2
  exit 1
fi
if [ ! -f "$DUMP" ] || ! gzip -t -- "$DUMP"; then
  echo "Dump is missing or is not a valid gzip file" >&2
  exit 1
fi

echo "Creating new drill database $DRILL_DB (existing databases are never dropped)..."
docker compose exec -T postgres createdb -U jobpilot -O jobpilot -- "$DRILL_DB"

echo "Restoring into $DRILL_DB..."
gunzip -c "$DUMP" | docker compose exec -T postgres psql -U jobpilot -d "$DRILL_DB" -v ON_ERROR_STOP=1

echo "Row counts:"
docker compose exec -T postgres psql -U jobpilot -d "$DRILL_DB" -c \
  "SELECT 'jobs' AS t, count(*) FROM jobs UNION ALL SELECT 'proposals', count(*) FROM proposals UNION ALL SELECT 'outcomes', count(*) FROM outcomes;"

echo "Drill complete. Database retained for inspection; remove it manually after verification."
