"""Send a one-line operational ping to TELEGRAM_ADMIN_CHAT_ID (dry-run stack check)."""

from __future__ import annotations

import asyncio
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import get_settings


async def main() -> int:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    chat = settings.telegram_admin_chat_id.strip()
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID required", file=sys.stderr)
        return 1
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        msg = await asyncio.wait_for(
            bot.send_message(
                int(chat),
                "JobPilot AI: operational ping (dry-run stack check).",
            ),
            timeout=60.0,
        )
        print(f"sent message_id={msg.message_id} chat_id={msg.chat.id}")
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
