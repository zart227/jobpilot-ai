"""Reset falsely sent proposals and re-send Telegram approval alerts."""

import asyncio
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, Proposal
from app.db.session import AsyncSessionLocal
from app.telegram.bot import notify_new_proposal
from app.utils.proxy import create_telegram_bot


async def reset_false_sent() -> int:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Proposal, Job)
            .join(Job, Job.id == Proposal.job_id)
            .where(Proposal.status == "sent")
        )
        count = 0
        for proposal, job in rows.all():
            proposal.status = "pending_approval"
            proposal.sent_at = None
            job.status = "pending_approval"
            count += 1
        if count:
            await session.commit()
        return count


async def resend_pending_alerts() -> int:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID are required")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Proposal)
            .where(Proposal.status == "pending_approval")
            .order_by(Proposal.created_at.desc())
        )
        proposals = result.scalars().all()

    seen_jobs: set[uuid.UUID] = set()
    bot = create_telegram_bot(settings.telegram_bot_token, settings)
    sent = 0
    try:
        for proposal in proposals:
            if proposal.job_id in seen_jobs:
                continue
            seen_jobs.add(proposal.job_id)
            message_id = await notify_new_proposal(bot, proposal.job_id, proposal.id)
            if message_id:
                sent += 1
                await asyncio.sleep(0.4)
    finally:
        await bot.session.close()
    return sent


async def main() -> None:
    reset_count = await reset_false_sent()
    sent_count = await resend_pending_alerts()
    print(f"Reset {reset_count} falsely sent proposals")
    print(f"Sent {sent_count} Telegram alerts")


if __name__ == "__main__":
    asyncio.run(main())
