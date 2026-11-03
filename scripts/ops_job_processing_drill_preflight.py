"""Read-only preflight before a controlled celery-worker restart drill (C052/C053)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.job_processing import has_active_processing_claim

logger = structlog.get_logger(__name__)

ENQUEUE_KEY_PREFIX = "jobpilot:enqueue:process_job:"


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


def _broker_host(settings) -> str:
    url = settings.celery_broker_url or ""
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname
    return "unknown"


def _scan_enqueue_dedup_keys(settings, *, sample_limit: int) -> dict:
    import redis

    client = redis.from_url(settings.celery_broker_url, decode_responses=True)
    try:
        keys: list[str] = []
        cursor = 0
        while len(keys) < sample_limit:
            cursor, batch = client.scan(cursor, match=f"{ENQUEUE_KEY_PREFIX}*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        keys = keys[:sample_limit]
        return {"sample_count": len(keys), "sample_job_ids": [k.removeprefix(ENQUEUE_KEY_PREFIX) for k in keys]}
    except Exception as exc:
        logger.warning("Redis enqueue dedup scan failed", error=str(exc))
        return {"error": str(exc), "sample_count": 0, "sample_job_ids": []}
    finally:
        client.close()


async def run_preflight(*, sample_limit: int) -> dict:
    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    settings = get_settings()
    url = (
        os.environ.get("JOBPILOT_WORKING_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    )
    db_name = urlparse(url).path.lstrip("/") or "unknown"
    now = datetime.now(UTC)
    lease_seconds = settings.job_processing_lease_seconds

    active_claims: list[dict] = []
    stale_claims: list[dict] = []
    queue_candidates = 0

    async with AsyncSessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(Job)
                    .where(Job.processing_claim_token.is_not(None))
                    .order_by(Job.processing_claimed_at.asc().nullslast())
                    .limit(200)
                )
            ).all()
        )
        queue_candidates = int(
            await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.status.in_(("new", "retryable_error", "scored", "processing")))
            )
            or 0
        )

    for job in rows:
        entry = {
            "job_id": str(job.id),
            "platform": job.platform,
            "status": job.status,
            "stage": job.processing_stage,
            "attempts": job.processing_attempt_count,
            "owner": job.processing_claim_owner,
            "claimed_at": job.processing_claimed_at.isoformat() if job.processing_claimed_at else None,
        }
        if has_active_processing_claim(job, settings):
            active_claims.append(entry)
        else:
            stale_claims.append(entry)

    redis_sample = _scan_enqueue_dedup_keys(settings, sample_limit=sample_limit)

    return {
        "database_name": db_name,
        "checked_at": now.isoformat(),
        "job_processing_lease_seconds": lease_seconds,
        "broker_host": _broker_host(settings),
        "jobs_with_claim_token": len(rows),
        "active_processing_claims": len(active_claims),
        "stale_claims_pending_recovery": len(stale_claims),
        "active_claim_sample": active_claims[:sample_limit],
        "stale_claim_sample": stale_claims[:sample_limit],
        "queue_candidate_jobs": queue_candidates,
        "redis_enqueue_dedup": redis_sample,
        "drill_notes": [
            "Stop only celery-worker during an active claim to simulate crash; beat recover_stale should clear claim+dedup.",
            "Do not run mixed old/new worker versions during drill.",
            "Capture processing_attempt_journal on the test job before/after.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    _require_database_url()
    try:
        report = asyncio.run(run_preflight(sample_limit=max(1, args.sample_limit)))
    except Exception as exc:
        logger.error("job processing drill preflight failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
