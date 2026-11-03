"""Mark jobs for embedding reindex after embedding provider/model/dimension change."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.embedding_index_signature import (
    embedding_index_signature,
    job_has_legacy_missing_signature,
    job_needs_embedding_reindex,
)

logger = structlog.get_logger(__name__)


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def run_inventory(*, apply: bool, confirm: str | None, include_legacy_unsigned: bool) -> dict:
    settings = get_settings()
    current = embedding_index_signature(settings)
    stale_ids: list[str] = []
    pending_count = legacy_unsigned = signature_mismatch = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Job.id, Job.raw_data))
        rows = result.all()

    for job_id, raw_data in rows:
        raw = raw_data or {}
        if raw.get("embedding_pending") in (True, "true"):
            pending_count += 1
        if job_has_legacy_missing_signature(raw):
            legacy_unsigned += 1
        stored = raw.get("embedding_index_signature")
        if stored and stored != current:
            signature_mismatch += 1
        if job_needs_embedding_reindex(
            raw, settings, include_legacy_unsigned=include_legacy_unsigned
        ):
            stale_ids.append(str(job_id))

    report = {
        "current_signature": current,
        "jobs_already_pending": pending_count,
        "legacy_indexed_without_signature": legacy_unsigned,
        "signature_mismatch": signature_mismatch,
        "include_legacy_unsigned": include_legacy_unsigned,
        "jobs_needing_reindex": len(stale_ids),
        "sample_job_ids": stale_ids[:20],
    }

    if not apply:
        return report

    if confirm != current:
        print("Refusing --apply without --confirm matching current_signature", file=sys.stderr)
        raise SystemExit(1)

    updated = 0
    async with AsyncSessionLocal() as session:
        for job_id_str in stale_ids:
            job = await session.get(Job, uuid.UUID(job_id_str))
            if not job:
                continue
            raw = dict(job.raw_data or {})
            raw["embedding_pending"] = True
            job.raw_data = raw
            updated += 1
        await session.commit()
    report["marked_pending"] = updated
    report.pop("sample_job_ids", None)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Set embedding_pending on selected jobs")
    parser.add_argument("--confirm", help="Must equal current_signature from dry-run")
    parser.add_argument(
        "--include-legacy-unsigned",
        action="store_true",
        help="Also mark jobs indexed before embedding_index_signature existed (large blast radius)",
    )
    args = parser.parse_args()
    _require_database_url()
    if args.apply and not args.include_legacy_unsigned and args.confirm:
        print(
            "Warning: --apply without --include-legacy-unsigned skips legacy jobs; "
            "see embedding_queue_inventory.py legacy_indexed_without_signature",
            file=sys.stderr,
        )
    try:
        report = asyncio.run(
            run_inventory(
                apply=args.apply,
                confirm=args.confirm,
                include_legacy_unsigned=args.include_legacy_unsigned,
            )
        )
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("embedding reindex inventory failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.apply:
        print("Marked jobs embedding_pending; run beat reindex or qdrant_embedding_drill --run")
    else:
        print(
            "Dry-run only. Use embedding_queue_inventory.py and qdrant_collection_inventory.py first.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
