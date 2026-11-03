"""Pilot job embedding reindex: mark a small explicit batch, then optional --run."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select

from app.config import get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.embedding_index_signature import (
    SIGNATURE_KEY,
    embedding_index_signature,
    job_needs_embedding_reindex,
)

logger = structlog.get_logger(__name__)

PILOT_KEY = "embedding_pilot_batch"


def _require_working_url() -> None:
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)
    db = url.rsplit("/", 1)[-1]
    if db.endswith("_test") and os.environ.get("JOBPILOT_ALLOW_TEST_URL") != "1":
        print("Refusing *_test database for pilot; set JOBPILOT_WORKING_DATABASE_URL to jobpilot_ai", file=sys.stderr)
        raise SystemExit(2)


async def _select_jobs(
    limit: int,
    job_ids: list[str] | None,
    *,
    include_legacy_unsigned: bool,
) -> list[Job]:
    settings = get_settings()
    current = embedding_index_signature(settings)
    async with AsyncSessionLocal() as session:
        if job_ids:
            ids = [uuid.UUID(j) for j in job_ids]
            rows = list((await session.scalars(select(Job).where(Job.id.in_(ids)))).all())
            return rows[:limit]
        base_filters = [Job.description.is_not(None), Job.title.is_not(None)]
        if include_legacy_unsigned:
            need_filter = or_(
                Job.raw_data["embedding_pending"].as_boolean().is_(True),
                Job.raw_data[SIGNATURE_KEY].astext.is_(None),
                Job.raw_data[SIGNATURE_KEY].astext != current,
            )
        else:
            need_filter = or_(
                Job.raw_data["embedding_pending"].as_boolean().is_(True),
                Job.raw_data[SIGNATURE_KEY].astext != current,
            )
        rows = list(
            (
                await session.scalars(
                    select(Job)
                    .where(*base_filters, need_filter)
                    .order_by(Job.updated_at.desc().nullslast(), Job.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )
        if include_legacy_unsigned:
            return rows
        return [job for job in rows if job_needs_embedding_reindex(job.raw_data, settings, include_legacy_unsigned=False)][:limit]


async def run_pilot(
    *,
    limit: int,
    job_ids: list[str] | None,
    include_legacy_unsigned: bool,
    apply: bool,
    confirm: str | None,
    execute: bool,
) -> dict:
    settings = get_settings()
    signature = embedding_index_signature(settings)
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    jobs = await _select_jobs(limit, job_ids, include_legacy_unsigned=include_legacy_unsigned)
    report: dict = {
        "current_signature": signature,
        "include_legacy_unsigned": include_legacy_unsigned,
        "pilot_batch_id": batch_id,
        "selected": [
            {
                "id": str(j.id),
                "platform": j.platform,
                "title": (j.title or "")[:80],
                "stored_signature": (j.raw_data or {}).get(SIGNATURE_KEY),
            }
            for j in jobs
        ],
    }
    if not apply:
        report["dry_run"] = True
        return report

    if confirm != signature:
        print("Refusing --apply without --confirm matching current_signature", file=sys.stderr)
        raise SystemExit(1)

    marked = 0
    async with AsyncSessionLocal() as session:
        for job in jobs:
            row = await session.get(Job, job.id)
            if not row:
                continue
            raw = dict(row.raw_data or {})
            raw["embedding_pending"] = True
            raw[PILOT_KEY] = batch_id
            row.raw_data = raw
            marked += 1
        await session.commit()
    report["marked_pending"] = marked

    if execute and marked:
        from app.tasks.embedding_tasks import _reindex_job_embeddings

        report["reindex_result"] = await _reindex_job_embeddings(limit=marked)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=3, help="Max jobs in pilot batch")
    parser.add_argument("--job-id", action="append", dest="job_ids", help="Explicit job UUID (repeatable)")
    parser.add_argument(
        "--include-legacy-unsigned",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Select jobs without embedding_index_signature (default: true; avoids re-piloting signed jobs)",
    )
    parser.add_argument("--apply", action="store_true", help="Mark selected jobs embedding_pending")
    parser.add_argument("--confirm", help="Must match current_signature")
    parser.add_argument(
        "--run",
        action="store_true",
        help="After mark, call embedding reindex (uses configured embedding API)",
    )
    args = parser.parse_args()
    _require_working_url()
    try:
        report = asyncio.run(
            run_pilot(
                limit=max(1, args.limit),
                job_ids=args.job_ids,
                include_legacy_unsigned=args.include_legacy_unsigned,
                apply=args.apply,
                confirm=args.confirm,
                execute=args.run,
            )
        )
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("pilot embedding reindex failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        print("Dry-run. --apply --confirm <signature> [--run]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
