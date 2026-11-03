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
from app.db.models import Job, Proposal, TelegramInboxPending
from app.db.session import AsyncSessionLocal
from app.llm.errors import LLMServiceError, format_llm_error_message
from app.services.conversation_context_service import (
    build_chat_agent_state,
    finalize_chat_agent_state,
)
from app.services.inbox_approval import inbox_presentation_is_current, record_inbox_approval
from app.services.kwork_inbox import (
    _fetch_kwork_thread,
    _get_kwork_actor,
    send_kwork_inbox_reply,
)
from app.services.send_attempt_service import delivery_blocks_mutation
from app.telegram.access import (
    edit_preview_binding,
    edit_preview_matches_callback,
    pending_matches_callback,
)
from app.telegram.full_review import send_full_review
from app.telegram.keyboards import inbox_edit_preview_keyboard
from app.utils.delivery_alerts import format_inbox_send_failure
from app.utils.formatting import extract_site_order_id

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
    preview = reply
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
            pending = await session.scalar(
                select(TelegramInboxPending)
                .where(TelegramInboxPending.id == uuid.UUID(pending_id))
                .with_for_update()
            )
            if not pending or pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            if not pending_matches_callback(pending, callback):
                await callback.answer("Карточка устарела", show_alert=True)
                return
            if not await inbox_presentation_is_current(session, pending):
                await callback.answer("Карточка устарела — запросите новую", show_alert=True)
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
            if not pending_matches_callback(pending, callback):
                await callback.answer("Карточка устарела", show_alert=True)
                return
            if not await inbox_presentation_is_current(session, pending):
                await callback.answer("Карточка устарела — запросите новую", show_alert=True)
                return
            job = await session.get(Job, pending.job_id) if pending.job_id else None
            proposal = (
                await session.get(Proposal, pending.proposal_id) if pending.proposal_id else None
            )
            order_id = ""
            conversation_context = ""
            if job:
                order_id = (
                    extract_site_order_id(
                        external_id=job.external_id,
                        url=job.url,
                        platform=job.platform,
                    )
                    or ""
                )
            pending_snapshot = (
                pending.job_id, pending.proposal_id, pending.kwork_username,
                pending.client_message, pending.draft_reply, pending.chat_id, pending.message_id,
            )
            job_snapshot = (job.title, job.description) if job else None
            proposal_snapshot = (proposal.content, proposal.version) if proposal else None

        if job:
            my_username, my_user_id = await _get_kwork_actor()
            thread = await _fetch_kwork_thread(pending.kwork_username)
            stale = False
            context_state = {}
            async with AsyncSessionLocal() as session:
                current = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
                current_job = await session.get(Job, pending_snapshot[0])
                current_proposal = (
                    await session.get(Proposal, pending_snapshot[1]) if pending_snapshot[1] else None
                )
                stale = (
                    not current or current.status != "pending"
                    or not pending_matches_callback(current, callback)
                    or (current.job_id, current.proposal_id, current.kwork_username,
                        current.client_message, current.draft_reply, current.chat_id,
                        current.message_id) != pending_snapshot
                    or not current_job or (current_job.title, current_job.description) != job_snapshot
                    or ((current_proposal.content, current_proposal.version) if current_proposal else None)
                    != proposal_snapshot
                    or not await inbox_presentation_is_current(session, current)
                )
                if not stale:
                    context_state = await build_chat_agent_state(
                        session, current_job.id, client_message=current.client_message,
                        thread_messages=thread or None, my_username=my_username,
                        my_user_id=my_user_id, proposal=current_proposal,
                    )
            if stale:
                await callback.answer("Карточка устарела — запросите новую", show_alert=True)
                return
            context_state = await finalize_chat_agent_state(context_state)
            conversation_context = context_state.get("conversation_context", "")

        await state.set_state(EditInboxReplyState.waiting_for_instruction)
        await state.update_data(
            pending_id=pending_id,
            draft_reply=pending.draft_reply,
            original_reply=pending.draft_reply,
            original_chat_id=pending.chat_id,
            original_message_id=pending.message_id,
            client_message=pending.client_message,
            username=pending.kwork_username,
            job_title=job.title if job else "",
            job_description=job.description if job else "",
            proposal_content=proposal.content if proposal else "",
            order_id=order_id,
            conversation_context=conversation_context,
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
        job_description = data.get("job_description") or ""
        proposal_content = data.get("proposal_content") or ""
        order_id = data.get("order_id") or None
        conversation_context = data.get("conversation_context") or ""

        await message.answer("⏳ Применяю правки через LLM...")
        try:
            editor = EditReplyAgent()
            after = await editor.run(
                current_reply=before,
                instruction=instruction,
                client_message=client_message,
                job_title=job_title,
                job_description=job_description,
                proposal_content=proposal_content,
                conversation_context=conversation_context,
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
        await state.update_data(draft_reply=after, edit_steps=edit_steps, preview_message_id=None)
        await state.set_state(EditInboxReplyState.reviewing)

        preview_message = await send_full_review(
            message.answer,
            content=after,
            reference=pending_id,
            heading=f"Отредактированный ответ • клиент {username[:150]} • заказ {order_id or 'не указан'}.\n"
            f"Правка: {instruction[:1000]}\nВходящее сообщение:\n{client_message}",
            markup=inbox_edit_preview_keyboard(pending_id),
        )
        await state.update_data(**edit_preview_binding(preview_message, after))

    @router.callback_query(F.data.startswith("inbox_edit_more:"))
    async def handle_inbox_edit_more(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if not edit_preview_matches_callback(
            data, callback, pending_id, data.get("draft_reply") or ""
        ):
            await callback.answer("Сессия устарела", show_alert=True)
            return
        await state.update_data(preview_message_id=None)
        await state.set_state(EditInboxReplyState.waiting_for_instruction)
        await callback.message.answer("✏️ Напишите следующую правку для ответа клиенту.")
        await callback.answer()

    @router.callback_query(F.data.startswith("inbox_edit_cancel:"))
    async def handle_inbox_edit_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if not edit_preview_matches_callback(
            data, callback, pending_id, data.get("draft_reply") or ""
        ):
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
        if not edit_preview_matches_callback(
            data, callback, pending_id, data.get("draft_reply") or ""
        ):
            await callback.answer("Сессия устарела", show_alert=True)
            return
        new_reply = data.get("draft_reply") or ""
        if not new_reply.strip():
            await callback.answer("Пустой ответ", show_alert=True)
            return

        async with AsyncSessionLocal() as session:
            pending = await session.scalar(
                select(TelegramInboxPending)
                .where(TelegramInboxPending.id == uuid.UUID(pending_id))
                .with_for_update()
            )
            if not pending or pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            if (
                pending.chat_id != data.get("original_chat_id")
                or pending.message_id != data.get("original_message_id")
                or pending.draft_reply != data.get("original_reply")
            ):
                await callback.answer("Карточка устарела", show_alert=True)
                return
            if await delivery_blocks_mutation(session, inbox_pending_id=pending.id):
                await callback.answer("Отправка выполняется или требует сверки", show_alert=True)
                return
            if not await inbox_presentation_is_current(session, pending):
                await callback.answer("Карточка устарела — запросите новую", show_alert=True)
                return
            pending.chat_id = callback.message.chat.id
            pending.message_id = callback.message.message_id
            pending.draft_reply = new_reply
            pending.status = "edited"
            await record_inbox_approval(
                session,
                pending,
                actor_user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                edited=True,
            )
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
        pending = await session.scalar(
            select(TelegramInboxPending)
            .where(TelegramInboxPending.id == uuid.UUID(pending_id))
            .with_for_update()
        )
        if not pending:
            await callback.answer("Request expired", show_alert=True)
            return
        if not pending_matches_callback(pending, callback):
            await callback.answer("Карточка устарела", show_alert=True)
            return
        if pending.status not in ("pending", "approved", "edited"):
            await callback.answer("Already processed", show_alert=True)
            return

        text = reply_text if reply_text is not None else (pending.draft_reply or "")
        if not text.strip():
            await callback.answer("Нет текста ответа — нажмите EDIT", show_alert=True)
            return

        username = pending.kwork_username
        user_id = pending.kwork_user_id
        interaction_id = pending.interaction_id
        job_id = pending.job_id
        proposal_id = pending.proposal_id
        pending_uuid = pending.id
        if pending.status == "pending":
            if not await inbox_presentation_is_current(session, pending):
                await callback.answer("Карточка устарела — запросите новую", show_alert=True)
                return
            await record_inbox_approval(
                session,
                pending,
                actor_user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
            )
            pending.status = "approved"
            await session.commit()

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
            format_inbox_send_failure(username, error),
            parse_mode=ParseMode.HTML,
        )
