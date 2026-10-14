#!/usr/bin/env sh
# Dump JobPilot PostgreSQL (docker compose postgres service).
set -eu

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
FILE="$OUT_DIR/jobpilot_ai_${STAMP}.sql.gz"

echo "Writing $FILE"
docker compose exec -T postgres pg_dump -U jobpilot -d jobpilot_ai | gzip -c > "$FILE"
echo "Done. Restore drill: scripts/restore_postgres_drill.sh $FILE"
