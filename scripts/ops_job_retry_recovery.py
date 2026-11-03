#!/usr/bin/env python3
"""Inspect exhausted job retries or explicitly requeue one eligible job.

An explicit DATABASE_URL is required. Default mode is read-only; --apply changes
one specified job and may trigger processing by a running worker.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=uuid.UUID, help="ID of one job")
    parser.add_argument("--apply", action="store_true", help="requeue the specified exhausted job")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        parser.error("explicit DATABASE_URL is required")
    if args.apply and not args.id:
        parser.error("--apply requires --id")
    return args


async def main(args):
    from sqlalchemy import and_, or_, select

    from app.config import get_settings
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.job_processing import requeue_exhausted_job

    async with AsyncSessionLocal() as session:
        if args.apply:
            if not await requeue_exhausted_job(session, args.id):
                print("Not requeued: job is not eligible, is claimed, or has delivery artifacts.")
                return 1
            await session.commit()
            print(f"Requeued job {args.id}; a running worker may now process it.")
            return 0
        max_attempts = get_settings().job_processing_max_attempts
        query = select(Job).where(or_(
            and_(Job.status == "permanent_error", Job.processing_error_type == "retry_exhausted"),
            and_(Job.status.in_(("new", "retryable_error", "scored")),
                 Job.processing_attempt_count >= max_attempts),
        )).order_by(Job.created_at).limit(100)
        if args.id:
            query = query.where(Job.id == args.id)
        for job in (await session.scalars(query)).all():
            has_proposal = bool(await session.scalar(select(Proposal.id).where(Proposal.job_id == job.id).limit(1)))
            has_attempt = bool(await session.scalar(select(SendAttempt.id).where(SendAttempt.job_id == job.id).limit(1)))
            print(f"id={job.id} platform={job.platform} status={job.status} "
                  f"attempts={job.processing_attempt_count} stage={job.processing_stage} "
                  f"claimed={bool(job.processing_claim_token)} has_proposal={has_proposal} "
                  f"has_send_attempt={has_attempt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
