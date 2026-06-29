#!/bin/sh
set -e

echo "JobPilot AI: waiting for PostgreSQL..."
until python -c "
import asyncio, os, sys
import asyncpg
async def check():
    try:
        conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg', ''))
        await conn.close()
        return True
    except Exception:
        return False
sys.exit(0 if asyncio.run(check()) else 1)
" 2>/dev/null; do
  sleep 2
done

echo "JobPilot AI: initializing database..."
python scripts/init_db.py

exec "$@"
