"""Register the bot's supported commands in Telegram's menu."""

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
)


async def register_command_menu(bot: Bot, admin_chat_id: int) -> None:
    commands = [
        BotCommand(command="start", description="О боте и работе с откликами"),
        BotCommand(command="status", description="Статус откликов и результатов"),
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
        for language in ("", "ru", "en"):
            await bot.set_my_commands(commands, scope=scope, language_code=language)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
