"""Retry deferred Qdrant job embeddings."""

from __future__ import annotations

import structlog
from sqlalchemy import select

from app.celery_app import celery_app
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.memory.qdrant_store import get_memory_store
from app.tasks.scrape_tasks import _run_async

logger = structlog.get_logger(__name__)


async def _reindex_pending_embeddings(limit: int = 25) -> dict:
    memory = get_memory_store()
    indexed = 0
    failed = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Job).order_by(Job.created_at.desc()).limit(500))
        candidates = [job for job in result.scalars().all() if (job.raw_data or {}).get("embedding_pending")]

    for job in candidates[:limit]:
        try:
            job_text = f"{job.title} {job.description}"
            await memory.store_job_embedding(
                str(job.id),
                job_text,
                {"platform": job.platform, "title": job.title},
            )
            async with AsyncSessionLocal() as session:
                row = await session.get(Job, job.id)
                if row:
                    raw = dict(row.raw_data or {})
                    raw["embedding_pending"] = False
                    row.raw_data = raw
                    await session.commit()
            indexed += 1
        except Exception as exc:
            failed += 1
            logger.warning("Embedding reindex failed", job_id=str(job.id), error=str(exc))

    skipped = max(0, len(candidates) - limit)
    return {"indexed": indexed, "failed": failed, "skipped": skipped, "pending_seen": len(candidates)}


@celery_app.task(name="app.tasks.embedding_tasks.reindex_pending_embeddings")
def reindex_pending_embeddings(limit: int = 25) -> dict:
    return _run_async(_reindex_pending_embeddings(limit=limit))
