from __future__ import annotations

import html
from typing import TYPE_CHECKING

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from cursor_sdk import CursorAgentError

from app.config import get_settings
from app.services.dev_agent_audit import read_recent_dev_agent_logs
from app.services.dev_agent_service import DevAgentConfigurationError, docker_bridge_hint
from app.llm.errors import format_llm_error_message

if TYPE_CHECKING:
    from app.services.dev_agent_service import DevAgentService

logger = structlog.get_logger(__name__)

TELEGRAM_MAX_MESSAGE = 4096


class DevAgentState(StatesGroup):
    active = State()


def split_telegram_messages(text: str, limit: int = TELEGRAM_MAX_MESSAGE) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    offset = 0
    while offset < len(text):
        chunks.append(text[offset : offset + limit])
        offset += limit
    return chunks


def is_admin_chat(message: Message) -> bool:
    settings = get_settings()
    if not settings.telegram_admin_chat_id:
        return False
    try:
        return message.chat.id == int(settings.telegram_admin_chat_id)
    except ValueError:
        return False


def register_dev_agent_handlers(router: Router, dev_agent: DevAgentService | None) -> None:
    @router.message(Command("dev"))
    async def cmd_dev(message: Message, state: FSMContext) -> None:
        if not is_admin_chat(message):
            await message.answer("Доступ только для администратора.")
            return
        if dev_agent is None or not dev_agent.enabled:
            await message.answer(
                "Dev-агент недоступен.\n\n"
                "Нужны <code>CURSOR_API_KEY</code> и <code>TELEGRAM_DEV_AGENT_ENABLED=true</code>.\n\n"
                f"{html.escape(docker_bridge_hint())}",
                parse_mode="HTML",
            )
            return

        await state.set_state(DevAgentState.active)
        await message.answer(
            "🤖 <b>Dev-агент активен</b>\n\n"
            "Пишите задачи — агент работает с кодом проекта на этом компьютере:\n"
            "• исправить ошибку\n"
            "• проверить логи или статус\n"
            "• доработать функционал\n\n"
            "<b>Команды:</b>\n"
            "/dev_status — статус проекта\n"
            "/dev_log — последние логи агента\n"
            "/dev_reset — новая сессия агента\n"
            "/dev_stop — выйти из режима агента",
            parse_mode="HTML",
        )

    @router.message(Command("dev_stop"))
    async def cmd_dev_stop(message: Message, state: FSMContext) -> None:
        if not is_admin_chat(message):
            return
        await state.clear()
        await message.answer("Dev-агент отключён. Команда /dev — снова включить.")

    @router.message(Command("dev_reset"))
    async def cmd_dev_reset(message: Message, state: FSMContext) -> None:
        if not is_admin_chat(message):
            return
        if dev_agent is None:
            await message.answer("Dev-агент недоступен.")
            return
        await dev_agent.reset_session(message.chat.id)
        await state.set_state(DevAgentState.active)
        await message.answer("🔄 Сессия агента сброшена. Контекст начнётся заново.")

    @router.message(Command("dev_log"))
    async def cmd_dev_log(message: Message) -> None:
        if not is_admin_chat(message):
            return
        records = read_recent_dev_agent_logs(limit=8)
        if not records:
            await message.answer("Логов dev-агента пока нет.\nФайлы: <code>data/dev_agent_logs/</code>", parse_mode="HTML")
            return
        lines = ["<b>Последние логи dev-агента</b>\n"]
        for record in records:
            ts = html.escape(str(record.get("ts", ""))[:19])
            event = html.escape(str(record.get("event", "")))
            req = html.escape(str(record.get("request", ""))[:120])
            duration = record.get("duration_sec")
            status = record.get("status") or record.get("error") or ""
            line = f"• <code>{ts}</code> <b>{event}</b>"
            if req:
                line += f"\n  запрос: {req}"
            if duration is not None:
                line += f"\n  {duration}s"
            if status:
                line += f" — {html.escape(str(status)[:80])}"
            lines.append(line)
        text = "\n\n".join(lines)
        if len(text) > 4000:
            text = text[:3997] + "..."
        await message.answer(text, parse_mode="HTML")

    @router.message(Command("dev_status"))
    async def cmd_dev_status(message: Message) -> None:
        if not is_admin_chat(message):
            return
        if dev_agent is None:
            await message.answer("Dev-агент недоступен.")
            return
        status = await dev_agent.collect_project_status()
        await message.answer(status, parse_mode="HTML")

    @router.message(DevAgentState.active, F.text)
    async def dev_agent_message(message: Message, state: FSMContext) -> None:
        if not is_admin_chat(message):
            return
        if dev_agent is None or not dev_agent.enabled:
            await message.answer("Dev-агент недоступен.")
            await state.clear()
            return

        text = (message.text or "").strip()
        if not text:
            return
        if text.startswith("/"):
            return

        progress = await message.answer("⏳ Агент работает…")
        try:
            result = await dev_agent.send(message.chat.id, text)
        except DevAgentConfigurationError as exc:
            await progress.edit_text(f"❌ {html.escape(str(exc))}", parse_mode="HTML")
            return
        except TimeoutError as exc:
            logger.warning("Dev agent timeout", error=str(exc))
            await progress.edit_text(f"⏱ {html.escape(str(exc))}", parse_mode="HTML")
            return
        except CursorAgentError as exc:
            logger.warning("Dev agent Cursor error", error=exc.message, retryable=exc.is_retryable)
            await progress.edit_text(
                f"❌ Cursor SDK: {html.escape(exc.message)}",
                parse_mode="HTML",
            )
            return
        except Exception as exc:
            logger.error("Dev agent unexpected error", error=str(exc))
            await progress.edit_text(format_llm_error_message(exc), parse_mode="HTML")
            return

        header = "✅ Готово"
        if result.status == "error":
            header = "⚠️ Агент завершил с ошибкой"

        body = result.text.strip() or "(пустой ответ)"
        footer = f"\n\n<i>run: {html.escape(result.run_id[:12])}…</i>"
        reply = f"<b>{header}</b>\n\n{html.escape(body)}{footer}"

        parts = split_telegram_messages(reply)
        await progress.edit_text(parts[0], parse_mode="HTML")
        for part in parts[1:]:
            await message.answer(part, parse_mode="HTML")
