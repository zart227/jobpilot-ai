"""Verify pilot job vectors are searchable in canonical Qdrant collection."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.memory.qdrant_store import get_memory_store
from app.services.embedding_index_signature import (
    SIGNATURE_KEY,
    expected_job_embedding_collection,
)

logger = structlog.get_logger(__name__)


def _require_working_url() -> None:
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)
    db = url.rsplit("/", 1)[-1]
    if db.endswith("_test") and os.environ.get("JOBPILOT_ALLOW_TEST_URL") != "1":
        print("Refusing *_test database; point at jobpilot_ai", file=sys.stderr)
        raise SystemExit(2)


async def verify(job_ids: list[str] | None, limit: int) -> dict:
    settings = get_settings()
    collection = expected_job_embedding_collection(settings)
    memory = await asyncio.to_thread(get_memory_store, settings)

    async with AsyncSessionLocal() as session:
        if job_ids:
            ids = [uuid.UUID(j) for j in job_ids]
            jobs = list((await session.scalars(select(Job).where(Job.id.in_(ids)))).all())
        else:
            min_chars = 20
            jobs = list(
                (
                    await session.scalars(
                        select(Job)
                        .where(
                            Job.raw_data[SIGNATURE_KEY].astext.is_not(None),
                            func.length(
                                func.coalesce(Job.title, "") + func.coalesce(Job.description, "")
                            )
                            >= min_chars,
                        )
                        .order_by(Job.updated_at.desc().nullslast(), Job.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )

    checks = []
    for job in jobs:
        text = f"{job.title} {job.description}"
        hits = await memory.find_similar_jobs(text, platform=job.platform, limit=10)
        jid = str(job.id)
        self_rank = next(
            (index for index, hit in enumerate(hits) if str(hit.get("job_id")) == jid),
            None,
        )
        checks.append(
            {
                "job_id": jid,
                "platform": job.platform,
                "title": (job.title or "")[:80],
                "signature": (job.raw_data or {}).get(SIGNATURE_KEY),
                "hits": len(hits),
                "self_in_top10": self_rank is not None,
                "self_rank": self_rank,
                "top_hit_job_id": hits[0].get("job_id") if hits else None,
            }
        )

    ok = all(item["self_in_top10"] for item in checks) if checks else False
    passed = sum(1 for item in checks if item["self_in_top10"])
    failed = [item for item in checks if not item["self_in_top10"]]
    return {
        "ok": ok,
        "expected_collection": collection,
        "checked": len(checks),
        "summary": {
            "self_in_top10_ok": passed,
            "self_in_top10_failed": len(failed),
            "failed_sample": [
                {"job_id": item["job_id"], "title": item["title"], "hits": item["hits"]}
                for item in failed[:10]
            ],
        },
        "results": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", action="append", dest="job_ids")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    _require_working_url()
    try:
        report = asyncio.run(verify(args.job_ids, args.limit))
    except Exception as exc:
        logger.error("pilot search verify failed", error=str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
