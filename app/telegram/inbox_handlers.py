from __future__ import annotations

import html
import uuid

import structlog
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.agents.edit_reply_agent import EditReplyAgent
from app.db.models import Job, TelegramInboxPending
from app.db.session import AsyncSessionLocal
from app.llm.errors import LLMServiceError, format_llm_error_message
from app.services.kwork_inbox import send_kwork_inbox_reply
from app.telegram.keyboards import inbox_edit_preview_keyboard
from app.utils.formatting import extract_site_order_id, sanitize_proposal_text

logger = structlog.get_logger(__name__)


class EditInboxReplyState(StatesGroup):
    waiting_for_instruction = State()
    reviewing = State()


def format_inbox_edit_preview(
    reply: str,
    instruction: str,
    *,
    username: str,
    order_id: str | None = None,
) -> str:
    preview = sanitize_proposal_text(reply)
    if len(preview) > 3500:
        preview = preview[:3497] + "..."
    order_line = ""
    if order_id:
        order_line = f"<b>№ заказа:</b> {html.escape(order_id)}\n"
    return (
        "<b>Отредактированный ответ клиенту</b>\n\n"
        f"<b>Клиент:</b> {html.escape(username)}\n"
        f"{order_line}"
        f"<b>Ваша правка:</b> {html.escape(instruction)}\n\n"
        f"{html.escape(preview)}"
    )


async def _safe_clear_reply_markup(message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def register_inbox_handlers(router: Router) -> None:
    @router.callback_query(F.data.startswith("inbox_approve:"))
    async def handle_inbox_approve(callback: CallbackQuery) -> None:
        pending_id = callback.data.split(":", 1)[1]
        await _send_inbox_reply(callback, pending_id, edited=False)

    @router.callback_query(F.data.startswith("inbox_skip:"))
    async def handle_inbox_skip(callback: CallbackQuery) -> None:
        pending_id = callback.data.split(":", 1)[1]
        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
            if not pending or pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            username = pending.kwork_username
            pending.status = "skipped"
            await session.commit()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("⏭ Пропущено")
        await callback.message.answer(f"Ответ клиенту {username} пропущен.")

    @router.callback_query(F.data.startswith("inbox_edit:"))
    async def handle_inbox_edit(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
            if not pending:
                await callback.answer("Request expired", show_alert=True)
                return
            if pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            job = await session.get(Job, pending.job_id) if pending.job_id else None
            order_id = ""
            if job:
                order_id = extract_site_order_id(
                    external_id=job.external_id,
                    url=job.url,
                    platform=job.platform,
                ) or ""

        await state.set_state(EditInboxReplyState.waiting_for_instruction)
        await state.update_data(
            pending_id=pending_id,
            draft_reply=pending.draft_reply,
            client_message=pending.client_message,
            username=pending.kwork_username,
            job_title=job.title if job else "",
            order_id=order_id,
            edit_steps=[],
        )
        await callback.message.answer(
            "✏️ <b>Редактирование ответа клиенту</b>\n\n"
            f"<b>Клиент:</b> {html.escape(pending.kwork_username)}\n"
            "Напишите, что изменить в ответе.\n"
            "Пример: <i>добавь ссылку на GitHub, сделай короче</i>",
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()

    @router.message(EditInboxReplyState.waiting_for_instruction)
    async def receive_inbox_edit_instruction(message: Message, state: FSMContext) -> None:
        instruction = (message.text or "").strip()
        if not instruction:
            await message.answer("Напишите инструкцию для правки ответа.")
            return

        data = await state.get_data()
        pending_id = data["pending_id"]
        before = data.get("draft_reply") or ""
        client_message = data.get("client_message") or ""
        username = data.get("username") or ""
        job_title = data.get("job_title") or ""
        order_id = data.get("order_id") or None

        await message.answer("⏳ Применяю правки через LLM...")
        try:
            editor = EditReplyAgent()
            after = await editor.run(
                current_reply=before,
                instruction=instruction,
                client_message=client_message,
                job_title=job_title,
            )
        except LLMServiceError as exc:
            await message.answer(format_llm_error_message(exc, order_id), parse_mode=ParseMode.HTML)
            return
        except Exception as exc:
            logger.error("Inbox reply edit failed", error=str(exc))
            await message.answer(format_llm_error_message(exc, order_id), parse_mode=ParseMode.HTML)
            return

        edit_steps = list(data.get("edit_steps") or [])
        edit_steps.append({"instruction": instruction, "before": before, "after": after})
        await state.update_data(draft_reply=after, edit_steps=edit_steps)
        await state.set_state(EditInboxReplyState.reviewing)

        await message.answer(
            format_inbox_edit_preview(
                after,
                instruction,
                username=username,
                order_id=order_id,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=inbox_edit_preview_keyboard(pending_id),
        )

    @router.callback_query(F.data.startswith("inbox_edit_more:"))
    async def handle_inbox_edit_more(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if data.get("pending_id") != pending_id:
            await callback.answer("Сессия устарела", show_alert=True)
            return
        await state.set_state(EditInboxReplyState.waiting_for_instruction)
        await callback.message.answer("✏️ Напишите следующую правку для ответа клиенту.")
        await callback.answer()

    @router.callback_query(F.data.startswith("inbox_edit_cancel:"))
    async def handle_inbox_edit_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if data.get("pending_id") != pending_id:
            await callback.answer("Сессия устарела", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Редактирование ответа отменено.")
        await callback.answer()

    @router.callback_query(F.data.startswith("inbox_edit_send:"))
    async def handle_inbox_edit_send(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if data.get("pending_id") != pending_id:
            await callback.answer("Сессия устарела", show_alert=True)
            return
        new_reply = (data.get("draft_reply") or "").strip()
        if not new_reply:
            await callback.answer("Пустой ответ", show_alert=True)
            return

        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
            if not pending or pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            pending.draft_reply = new_reply
            pending.status = "edited"
            await session.commit()

        await state.clear()
        await _send_inbox_reply(callback, pending_id, edited=True, reply_text=new_reply)


async def _send_inbox_reply(
    callback: CallbackQuery,
    pending_id: str,
    *,
    edited: bool,
    reply_text: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
        if not pending:
            await callback.answer("Request expired", show_alert=True)
            return
        if pending.status not in ("pending", "edited"):
            await callback.answer("Already processed", show_alert=True)
            return

        text = (reply_text or pending.draft_reply or "").strip()
        if not text:
            await callback.answer("Нет текста ответа — нажмите EDIT", show_alert=True)
            return

        username = pending.kwork_username
        user_id = pending.kwork_user_id
        interaction_id = pending.interaction_id
        job_id = pending.job_id
        proposal_id = pending.proposal_id
        pending_uuid = pending.id

    try:
        await callback.answer("⏳ Отправляю на Kwork...")
    except Exception:
        pass

    success, error = await send_kwork_inbox_reply(
        user_id=user_id,
        username=username,
        reply_text=text,
        pending_id=pending_uuid,
        interaction_id=interaction_id,
        job_id=job_id,
        proposal_id=proposal_id,
    )

    await _safe_clear_reply_markup(callback.message)
    if success:
        label = "отредактированный ответ" if edited else "ответ"
        await callback.message.answer(
            f"✅ JobPilot AI: {label} отправлен клиенту {username} на Kwork."
        )
    else:
        await callback.message.answer(
            f"❌ Не удалось отправить ответ клиенту {username}.\n{error or 'Unknown error'}",
            parse_mode=ParseMode.HTML,
        )
