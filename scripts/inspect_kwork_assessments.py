#!/usr/bin/env python3
"""Read-only inventory of Kwork assessment gates; requires explicit DATABASE_URL."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100, help='Maximum jobs to inspect (1–1000)')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000')
    return args


async def inspect(limit: int) -> list[dict[str, str]]:
    from sqlalchemy import select, text

    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.opportunity_policy import kwork_job_assessment_rejection

    async with AsyncSessionLocal() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        jobs = (await session.scalars(
            select(Job).where(Job.platform == 'kwork').order_by(Job.created_at.desc(), Job.id).limit(limit)
        )).all()
        results = []
        for job in jobs:
            reason = await kwork_job_assessment_rejection(session, job)
            results.append({'job_id': str(job.id), 'status': job.status, 'assessment_gate': reason or 'pass'})
        return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not os.environ.get('DATABASE_URL', '').strip():
        print('Set DATABASE_URL explicitly; this command never chooses a working database.', file=sys.stderr)
        return 2
    try:
        results = asyncio.run(inspect(args.limit))
    except Exception:
        print('Could not inspect the database; check connectivity and access permissions.', file=sys.stderr)
        return 1
    print(json.dumps({'scanned': len(results), 'limit': args.limit, 'jobs': results}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
