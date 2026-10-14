"""Read-only checks before applying DB migrations (duplicates, orphans, invalid scores)."""

from __future__ import annotations

import asyncio
import json
import sys

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

logger = structlog.get_logger(__name__)

QUERIES: dict[str, str] = {
    "duplicate_jobs": """
        SELECT platform, external_id, COUNT(*) AS cnt
        FROM jobs
        GROUP BY platform, external_id
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
        LIMIT 50
    """,
    "duplicate_clients_with_external_id": """
        SELECT platform, external_id, COUNT(*) AS cnt
        FROM clients
        WHERE external_id IS NOT NULL
        GROUP BY platform, external_id
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
        LIMIT 50
    """,
    "multiple_active_proposals_per_job": """
        SELECT job_id, COUNT(*) AS cnt
        FROM proposals
        WHERE status IN ('draft', 'pending_approval', 'approved', 'edited', 'sending')
        GROUP BY job_id
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
        LIMIT 50
    """,
    "invalid_job_scores": """
        SELECT id, platform, external_id, score
        FROM jobs
        WHERE score IS NOT NULL AND (score < 0 OR score > 100)
        LIMIT 50
    """,
    "orphan_proposals": """
        SELECT p.id, p.job_id
        FROM proposals p
        LEFT JOIN jobs j ON j.id = p.job_id
        WHERE j.id IS NULL
        LIMIT 50
    """,
}


async def run_preflight() -> dict[str, list[dict]]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    report: dict[str, list[dict]] = {}

    async with engine.connect() as conn:
        for name, sql in QUERIES.items():
            result = await conn.execute(text(sql))
            rows = [dict(row._mapping) for row in result]
            report[name] = rows

    await engine.dispose()
    return report


def main() -> int:
    try:
        report = asyncio.run(run_preflight())
    except Exception as exc:
        logger.error("Preflight failed to connect or query", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    blocking = any(report.values())
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if blocking:
        print(
            "\nPreflight found issues. Resolve duplicates/conflicts manually before adding UNIQUE constraints.",
            file=sys.stderr,
        )
        return 1
    print("Preflight OK: no blocking issues detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
