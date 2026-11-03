"""Qdrant / embedding_pending drill: probe Qdrant, count backlog, optional reindex batch."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx
import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.tasks.embedding_tasks import _reindex_pending_embeddings

logger = structlog.get_logger(__name__)


async def _count_embedding_pending() -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.raw_data["embedding_pending"].as_boolean().is_(True))
        )
        return int(result.scalar() or 0)


async def _count_memory_index_pending() -> int:
    from app.db.models import MemoryIndexPending

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(select(func.count()).select_from(MemoryIndexPending))
        except Exception:
            return -1
        return int(result.scalar() or 0)


async def _probe_qdrant() -> dict:
    settings = get_settings()
    url = settings.qdrant_url.rstrip("/")
    prefix = settings.qdrant_collection_prefix
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/collections")
            response.raise_for_status()
            payload = response.json()
            names = [item.get("name") for item in payload.get("result", {}).get("collections", [])]
            job_collections = [name for name in names if name and name.startswith(prefix)]
            return {"ok": True, "collections_matching_prefix": job_collections}
    except Exception as exc:
        logger.warning("Qdrant probe failed", error=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def run_drill(run_reindex: bool, limit: int, *, run_memory_queue: bool) -> dict:
    qdrant = await _probe_qdrant()
    pending = await _count_embedding_pending()
    memory_pending = await _count_memory_index_pending()
    report: dict = {
        "qdrant": qdrant,
        "jobs_embedding_pending": pending,
        "memory_index_pending": memory_pending,
        "reindex_limit": limit,
    }
    if run_reindex:
        report["reindex_result"] = await _reindex_pending_embeddings(limit=limit)
        report["jobs_embedding_pending_after"] = await _count_embedding_pending()
        report["memory_index_pending_after"] = await _count_memory_index_pending()
    elif run_memory_queue and memory_pending != -1:
        from app.services.memory_index_queue import process_memory_index_queue

        report["memory_queue_result"] = await process_memory_index_queue(limit=limit)
        report["memory_index_pending_after"] = await _count_memory_index_pending()
    return report


def main() -> int:
    import logging

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Process up to --limit pending jobs (calls OpenAI/embeddings API if configured)",
    )
    parser.add_argument(
        "--run-memory-queue",
        action="store_true",
        help="Process up to --limit memory_index_pending rows (requires migration 0008)",
    )
    parser.add_argument("--limit", type=int, default=25, help="Batch size for --run")
    args = parser.parse_args()

    try:
        report = asyncio.run(
            run_drill(
                run_reindex=args.run,
                limit=args.limit,
                run_memory_queue=args.run_memory_queue,
            )
        )
    except Exception as exc:
        logger.error("qdrant_embedding_drill failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("qdrant", {}).get("ok"):
        print("Qdrant unreachable — fix QDRANT_URL or start compose qdrant", file=sys.stderr)
        return 1
    if args.run:
        print("Reindex batch finished (see reindex_result).")
    else:
        print("Dry-run OK. Re-run with --run to process embedding_pending jobs.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
