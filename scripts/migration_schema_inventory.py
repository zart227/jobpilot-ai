"""Read-only Alembic head and memory-queue inventory before applying migration 0008+."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = structlog.get_logger(__name__)

EXPECTED_HEAD = "20261103_0011"


def _resolve_database_url() -> str:
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print(
            "Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL (read-only inventory).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if url.rstrip("/").endswith("_test") and os.environ.get("JOBPILOT_ALLOW_TEST_URL") != "1":
        print(
            "Refusing _test database unless JOBPILOT_ALLOW_TEST_URL=1 (use a copy/staging URL).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return url


async def run_inventory(database_url: str) -> dict:
    engine = create_async_engine(database_url)
    report: dict = {"expected_alembic_head": EXPECTED_HEAD}

    async with engine.connect() as conn:
        version = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        row = version.first()
        report["alembic_version"] = row[0] if row else None

        table = await conn.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = 'memory_index_pending'
                )
                """
            )
        )
        has_memory_queue = bool(table.scalar())
        report["memory_index_pending_table"] = has_memory_queue

        if has_memory_queue:
            pending = await conn.execute(text("SELECT COUNT(*) FROM memory_index_pending"))
            report["memory_index_pending_rows"] = int(pending.scalar() or 0)
            oldest = await conn.execute(
                text("SELECT MIN(created_at) FROM memory_index_pending")
            )
            oldest_row = oldest.first()
            report["memory_index_pending_oldest_at"] = (
                oldest_row[0].isoformat() if oldest_row and oldest_row[0] else None
            )
        else:
            report["memory_index_pending_rows"] = None
            report["memory_index_pending_oldest_at"] = None

        embedding = await conn.execute(
            text(
                """
                SELECT COUNT(*) FROM jobs
                WHERE (raw_data->>'embedding_pending')::boolean IS TRUE
                """
            )
        )
        report["jobs_embedding_pending"] = int(embedding.scalar() or 0)

    await engine.dispose()
    report["needs_upgrade_to_0008"] = report["alembic_version"] != EXPECTED_HEAD
    report["ready_after_upgrade"] = (
        report["alembic_version"] == EXPECTED_HEAD and report["memory_index_pending_table"]
    )
    return report


def main() -> int:
    try:
        url = _resolve_database_url()
        report = asyncio.run(run_inventory(url))
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("Schema inventory failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if report["needs_upgrade_to_0008"]:
        print(
            f"\nAction: after backup, run `alembic upgrade head` on this database "
            f"(target revision {EXPECTED_HEAD}). Do not apply on production without explicit approval.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
