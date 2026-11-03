"""Read-only PostgreSQL embedding backlog and signature inventory."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Job, MemoryIndexPending
from app.db.session import AsyncSessionLocal
from app.services.embedding_index_signature import (
    SIGNATURE_KEY,
    embedding_index_signature,
    expected_job_embedding_collection,
    job_has_legacy_missing_signature,
    job_needs_embedding_reindex,
)

logger = structlog.get_logger(__name__)


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def run_inventory(*, include_legacy_unsigned: bool) -> dict:
    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    settings = get_settings()
    current = embedding_index_signature(settings)
    pending = legacy_unsigned = signature_mismatch = signed_current = 0
    sample_legacy: list[str] = []

    from urllib.parse import urlparse

    url = (
        os.environ.get("JOBPILOT_WORKING_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    )
    db_name = urlparse(url).path.lstrip("/") or "unknown"
    if db_name.endswith("_test") and os.environ.get("JOBPILOT_ALLOW_TEST_URL") != "1":
        print(
            f"Warning: DATABASE_URL targets {db_name}; for working inventory set JOBPILOT_WORKING_DATABASE_URL to jobpilot_ai",
            file=sys.stderr,
        )

    async with AsyncSessionLocal() as session:
        alembic = await session.execute(select(func.count()).select_from(Job))
        total_jobs = int(alembic.scalar() or 0)
        pending_sql = await session.scalar(
            select(func.count()).select_from(Job).where(
                Job.raw_data["embedding_pending"].as_boolean().is_(True)
            )
        )
        memory_pending = 0
        try:
            memory_pending = int(
                await session.scalar(select(func.count()).select_from(MemoryIndexPending)) or 0
            )
        except Exception:
            memory_pending = -1

        result = await session.execute(select(Job.id, Job.raw_data))
        rows = result.all()

    for job_id, raw_data in rows:
        raw = raw_data or {}
        if raw.get("embedding_pending") in (True, "true"):
            pending += 1
        if job_has_legacy_missing_signature(raw):
            legacy_unsigned += 1
            if len(sample_legacy) < 10:
                sample_legacy.append(str(job_id))
        stored = raw.get(SIGNATURE_KEY)
        if stored == current:
            signed_current += 1
        if stored and stored != current:
            signature_mismatch += 1

    would_reindex = 0
    for _job_id, raw_data in rows:
        if job_needs_embedding_reindex(
            raw_data, settings, include_legacy_unsigned=include_legacy_unsigned
        ):
            would_reindex += 1

    return {
        "database_name": db_name,
        "database_jobs_total": total_jobs,
        "embedding_pending_sql": int(pending_sql or 0),
        "embedding_pending_scan": pending,
        "memory_index_pending": memory_pending,
        "current_signature": current,
        "expected_job_embedding_collection": expected_job_embedding_collection(settings),
        "legacy_indexed_without_signature": legacy_unsigned,
        "jobs_with_current_signature": signed_current,
        "signed_percent_of_total": round((signed_current / total_jobs) * 100, 1) if total_jobs else 0.0,
        "signature_mismatch": signature_mismatch,
        "would_mark_on_apply_default": would_reindex,
        "would_mark_if_include_legacy_unsigned": sum(
            1
            for _, raw in rows
            if job_needs_embedding_reindex(raw, settings, include_legacy_unsigned=True)
        ),
        "sample_legacy_unsigned_job_ids": sample_legacy,
        "notes": [
            "mark_embedding_reindex.py --apply does NOT include legacy unsigned unless --include-legacy-unsigned.",
            "Provider changes are tracked in signature and in canonical Qdrant collection names.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-legacy-unsigned",
        action="store_true",
        help="Also compute would_mark count including jobs without embedding_index_signature",
    )
    args = parser.parse_args()
    _require_database_url()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    try:
        report = asyncio.run(run_inventory(include_legacy_unsigned=args.include_legacy_unsigned))
    except Exception as exc:
        logger.error("embedding queue inventory failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
