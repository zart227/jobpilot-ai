"""Register the bot's supported commands in Telegram's menu."""

import asyncio

import structlog
from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
)

logger = structlog.get_logger(__name__)


async def _register_command_menu(bot: Bot, admin_chat_id: int) -> None:
    commands = [
        BotCommand(command="start", description="О боте и работе с откликами"),
        BotCommand(command="status", description="Статус откликов и результатов"),
        BotCommand(command="rereview", description="Повторно показать карточку для одобрения"),
        BotCommand(command="dev", description="Включить режим агента разработки"),
        BotCommand(command="dev_status", description="Проверить состояние проекта"),
        BotCommand(command="dev_log", description="Последние логи агента"),
        BotCommand(command="dev_reset", description="Начать новую сессию агента"),
        BotCommand(command="dev_stop", description="Выйти из режима агента"),
    ]
    scopes = [BotCommandScopeDefault()]
    if admin_chat_id:
        scopes.append(BotCommandScopeChat(chat_id=admin_chat_id))
    for scope in scopes:
        await bot.set_my_commands(commands, scope=scope)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def register_command_menu(bot: Bot, admin_chat_id: int) -> None:
    try:
        await asyncio.wait_for(_register_command_menu(bot, admin_chat_id), timeout=45.0)
    except TimeoutError:
        logger.warning(
            "Telegram command menu registration timed out; continuing to polling",
            admin_chat_id=admin_chat_id,
        )
    except Exception as exc:
        logger.warning(
            "Telegram command menu registration failed; continuing to polling",
            error=str(exc),
            admin_chat_id=admin_chat_id,
        )
