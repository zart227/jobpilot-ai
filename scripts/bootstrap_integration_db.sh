#!/usr/bin/env sh
# Create jobpilot_ai_test on local compose Postgres and migrate (safe for pytest integration).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE="${COMPOSE:-docker compose}"
PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

HOST_PORT="${JOBPILOT_PG_HOST_PORT:-5434}"
DB_USER="${POSTGRES_USER:-jobpilot}"
DB_PASS="${POSTGRES_PASSWORD:-jobpilot_secret}"
TEST_DB="${JOBPILOT_TEST_DATABASE:-jobpilot_ai_test}"

echo "== bootstrap_integration_db =="

if ! $COMPOSE exec -T postgres psql -U "$DB_USER" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='${TEST_DB}'" | grep -q 1; then
  $COMPOSE exec -T postgres psql -U "$DB_USER" -d postgres -c "CREATE DATABASE ${TEST_DB};"
fi

unset REDIS_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND
export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASS}@127.0.0.1:${HOST_PORT}/${TEST_DB}"
export REDIS_URL="redis://127.0.0.1:6379/2"
export CELERY_BROKER_URL="redis://127.0.0.1:6379/2"
export CELERY_RESULT_BACKEND="redis://127.0.0.1:6379/3"
export QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
export DEBUG="${DEBUG:-false}"

echo "DATABASE_URL -> ${TEST_DB} on 127.0.0.1:${HOST_PORT}"
"$PY" -m alembic upgrade head
"$PY" scripts/migration_preflight.py

ENV_FILE="${ROOT}/.jobpilot_integration_test.env"
cat > "$ENV_FILE" <<EOF
DATABASE_URL=${DATABASE_URL}
REDIS_URL=${REDIS_URL}
CELERY_BROKER_URL=${CELERY_BROKER_URL}
CELERY_RESULT_BACKEND=${CELERY_RESULT_BACKEND}
QDRANT_URL=${QDRANT_URL}
DEBUG=false
EOF
chmod 600 "$ENV_FILE"
echo "Wrote ${ENV_FILE} (gitignored via .jobpilot_*)"
