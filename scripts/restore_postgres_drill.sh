#!/usr/bin/env sh
# Restore drill into an ISOLATED database (default: jobpilot_ai_drill).
# Usage: JOBPILOT_DRILL_DB=jobpilot_ai_drill ./scripts/restore_postgres_drill.sh backups/foo.sql.gz
set -eu

DUMP="${1:?usage: restore_postgres_drill.sh <dump.sql.gz>}"
DRILL_DB="${JOBPILOT_DRILL_DB:-jobpilot_ai_drill}"

echo "Creating drill database $DRILL_DB (drop if exists)..."
docker compose exec -T postgres psql -U jobpilot -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DRILL_DB' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DRILL_DB;
CREATE DATABASE $DRILL_DB OWNER jobpilot;
SQL

echo "Restoring into $DRILL_DB..."
gunzip -c "$DUMP" | docker compose exec -T postgres psql -U jobpilot -d "$DRILL_DB" -v ON_ERROR_STOP=1

echo "Row counts:"
docker compose exec -T postgres psql -U jobpilot -d "$DRILL_DB" -c \
  "SELECT 'jobs' AS t, count(*) FROM jobs UNION ALL SELECT 'proposals', count(*) FROM proposals UNION ALL SELECT 'outcomes', count(*) FROM outcomes;"

echo "Drill complete. Drop with: docker compose exec -T postgres psql -U jobpilot -d postgres -c 'DROP DATABASE $DRILL_DB;'"
