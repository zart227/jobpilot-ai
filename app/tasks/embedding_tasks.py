"""Retry deferred Qdrant job embeddings."""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import func, select

from app.celery_app import celery_app
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.memory.qdrant_store import get_memory_store
from app.tasks.async_runner import run_async as _run_async

logger = structlog.get_logger(__name__)


async def _reindex_job_embeddings(limit: int = 25) -> dict:
    memory = await asyncio.to_thread(get_memory_store)
    indexed = 0
    failed = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        pending_filter = Job.raw_data["embedding_pending"].astext == "true"
        pending_seen = await session.scalar(select(func.count(Job.id)).where(pending_filter)) or 0
        result = await session.execute(
            select(Job).where(pending_filter).order_by(Job.created_at.asc(), Job.id.asc()).limit(max(0, limit))
        )
        candidates = list(result.scalars().all())

    for job in candidates:
        try:
            job_text = f"{job.title} {job.description}"
            stored = await memory.store_job_embedding(
                str(job.id),
                job_text,
                {"platform": job.platform, "title": job.title},
            )
            if stored is not True:
                failed += 1
                logger.warning("Embedding reindex deferred", job_id=str(job.id))
                continue
            async with AsyncSessionLocal() as session:
                row = await session.scalar(select(Job).where(Job.id == job.id).with_for_update())
                if row:
                    from app.services.embedding_index_signature import (
                        SIGNATURE_KEY,
                        embedding_index_signature,
                    )

                    raw = dict(row.raw_data or {})
                    unchanged = f"{row.title} {row.description}" == job_text
                    raw["embedding_pending"] = not unchanged
                    if unchanged:
                        raw[SIGNATURE_KEY] = embedding_index_signature()
                    row.raw_data = raw
                    await session.commit()
                    if unchanged:
                        indexed += 1
                    else:
                        failed += 1
                else:
                    skipped += 1
        except Exception as exc:
            failed += 1
            logger.warning("Embedding reindex failed", job_id=str(job.id), error=str(exc))

    skipped += max(0, pending_seen - len(candidates))
    return {"indexed": indexed, "failed": failed, "skipped": skipped, "pending_seen": pending_seen}


async def _reindex_pending_embeddings(limit: int = 25) -> dict:
    from app.services.memory_index_queue import process_memory_index_queue

    job_stats = await _reindex_job_embeddings(limit=limit)
    queue_stats = await process_memory_index_queue(limit=limit)
    return {**job_stats, **queue_stats}


@celery_app.task(name="app.tasks.embedding_tasks.reindex_pending_embeddings")
def reindex_pending_embeddings(limit: int = 25) -> dict:
    return _run_async(_reindex_pending_embeddings(limit=limit))
