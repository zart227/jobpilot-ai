#!/usr/bin/env python3
"""Close TelegramPending rows whose jobs left pending_approval (one-off or cron)."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, TelegramPending
from app.db.session import AsyncSessionLocal
from app.logging_setup import configure_application_logging
from app.services.telegram_review_status import close_telegram_pending_if_job_not_in_review

configure_application_logging()
get_settings.cache_clear()


async def _run() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        os.environ["DATABASE_URL"] = database_url

    closed_total = 0
    async with AsyncSessionLocal() as session:
        job_ids = (
            await session.scalars(
                select(TelegramPending.job_id).where(TelegramPending.status == "pending").distinct()
            )
        ).all()
        for job_id in job_ids:
            job = await session.get(Job, job_id)
            if not job:
                continue
            closed = await close_telegram_pending_if_job_not_in_review(session, job)
            if closed:
                closed_total += closed
        await session.commit()
    print(f"Closed {closed_total} stale telegram_pending row(s).")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
