"""Align Telegram review card with confirmed proposal send (DB + optional notify)."""
from __future__ import annotations

import argparse
import asyncio
import uuid

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, Proposal, SendAttempt, TelegramPending
from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT, ATTEMPT_KIND_PROPOSAL
from app.db.session import AsyncSessionLocal
from app.telegram.bot import format_kwork_success_message


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending-id", type=uuid.UUID, required=True)
    parser.add_argument("--notify", action="store_true", help="Send success line in Telegram chat")
    args = parser.parse_args()

    settings = get_settings()
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, args.pending_id)
        if not pending:
            print("pending_not_found")
            return 1
        proposal = await session.get(Proposal, pending.proposal_id)
        job = await session.get(Job, pending.job_id)
        attempt = await session.scalar(
            select(SendAttempt)
            .where(
                SendAttempt.kind == ATTEMPT_KIND_PROPOSAL,
                SendAttempt.proposal_id == pending.proposal_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
            .order_by(SendAttempt.finished_at.desc())
            .limit(1)
        )
        if not proposal or proposal.status not in ("sent", "simulated_sent"):
            print("proposal_not_sent", proposal.status if proposal else None)
            return 2
        pending.status = "simulated_sent" if proposal.status == "simulated_sent" else "sent"
        chat_id = pending.chat_id
        message_id = pending.message_id
        pending_status = pending.status
        await session.commit()
        send_debug = dict((attempt.platform_response if attempt else None) or {})
        if job:
            send_debug.setdefault("job_url", job.url)

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
        if args.notify:
            text = format_kwork_success_message(send_debug, job=job)
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=message_id,
                parse_mode=ParseMode.HTML,
            )
    finally:
        await bot.session.close()

    print(
        "ok",
        f"pending={args.pending_id}",
        f"message_id={message_id}",
        f"status={pending_status}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
