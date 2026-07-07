"""Regenerate Kwork proposals not yet sent and re-send Telegram alerts."""

import asyncio
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, Proposal
from app.db.session import AsyncSessionLocal
from app.services.job_pipeline import JobPipelineService
from app.telegram.bot import notify_new_proposal
from app.utils.proxy import create_telegram_bot


async def list_unsent_kwork_jobs() -> list[uuid.UUID]:
    async with AsyncSessionLocal() as session:
        sent_job_ids = select(Proposal.job_id).where(Proposal.status == "sent")
        result = await session.execute(
            select(Job.id)
            .where(
                Job.platform == "kwork",
                Job.is_relevant.is_(True),
                Job.id.not_in(sent_job_ids),
            )
            .order_by(Job.created_at.desc())
        )
        return [row[0] for row in result.all()]


async def regenerate_and_notify() -> dict:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID are required")

    pipeline = JobPipelineService()
    job_ids = await list_unsent_kwork_jobs()
    bot = create_telegram_bot(settings.telegram_bot_token, settings)

    regenerated = 0
    notified = 0
    superseded = 0
    failed = 0

    try:
        for job_id in job_ids:
            proposal_id = await pipeline.regenerate_proposal(job_id)
            if not proposal_id:
                failed += 1
                continue
            regenerated += 1
            superseded += await pipeline.supersede_telegram_pending(job_id)
            message_id = await notify_new_proposal(bot, job_id, proposal_id)
            if message_id:
                notified += 1
            await asyncio.sleep(0.5)
    finally:
        await bot.session.close()

    return {
        "jobs": len(job_ids),
        "regenerated": regenerated,
        "notified": notified,
        "superseded_pending": superseded,
        "failed": failed,
    }


async def main() -> None:
    stats = await regenerate_and_notify()
    print(
        "Regenerated {regenerated}/{jobs} proposals, "
        "sent {notified} Telegram alerts, "
        "superseded {superseded_pending} old pending, "
        "failed {failed}".format(**stats)
    )


if __name__ == "__main__":
    asyncio.run(main())
