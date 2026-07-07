import html
import asyncio
import uuid
from typing import Any

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import CallbackQuery, Message
from redis.asyncio import Redis
from sqlalchemy import select

from app.agents.edit_proposal_agent import EditProposalAgent
from app.agents.graph import compile_jobpilot_graph
from app.agents.learning_agent import LearningAgent
from app.config import get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.llm.errors import LLMServiceError, format_llm_error_message
from app.services.edit_preference_service import EditPreferenceService
from app.services.job_pipeline import JobPipelineService
from app.services.proposal_sender import ProposalSender
from app.services.reward_system import RewardSystem
from app.telegram.keyboards import approval_keyboard, edit_preview_keyboard
from app.utils.formatting import (
    KWORK_MAX_OFFER_CHARS,
    extract_site_order_id,
    format_budget,
    format_offer_price,
    sanitize_proposal_text,
)
from app.utils.proxy import create_telegram_bot

logger = structlog.get_logger(__name__)


class EditProposalState(StatesGroup):
    waiting_for_instruction = State()
    reviewing = State()


def _resolve_order_id(
    job: Job | None = None,
    send_debug: dict[str, Any] | None = None,
) -> str | None:
    if job:
        return extract_site_order_id(
            external_id=job.external_id,
            url=job.url,
            platform=job.platform,
        )
    debug = send_debug or {}
    return extract_site_order_id(
        url=debug.get("job_url"),
        platform="kwork" if debug.get("job_url") else None,
    )


def _order_id_html_line(job: Job | None) -> str:
    order_id = _resolve_order_id(job=job)
    if not order_id:
        return ""
    return f"<b>№ заказа:</b> {html.escape(order_id)}\n"


def format_kwork_success_message(
    send_debug: dict[str, Any] | None,
    *,
    edited: bool = False,
    job: Job | None = None,
) -> str:
    action = (send_debug or {}).get("offer_action")
    if action == "updated" or edited:
        base = "✅ JobPilot AI: отредактированный отклик обновлён на Kwork."
    else:
        base = "✅ JobPilot AI: отклик отправлен на Kwork."

    order_id = _resolve_order_id(job=job, send_debug=send_debug)
    if order_id:
        return f"{base}\n№ заказа: {order_id}"
    return base


def _order_id_plain_suffix(order_id: str | None) -> str:
    if not order_id:
        return ""
    return f"\n№ заказа: {order_id}"


def format_edit_preview(content: str, instruction: str, order_id: str | None = None) -> str:
    preview = sanitize_proposal_text(content)
    if len(preview) > 3500:
        preview = preview[:3497] + "..."
    order_line = ""
    if order_id:
        order_line = f"<b>№ заказа:</b> {html.escape(order_id)}\n"
    return (
        "<b>Отредактированный отклик</b>\n\n"
        f"{order_line}"
        f"<b>Ваша правка:</b> {html.escape(instruction)}\n\n"
        f"{html.escape(preview)}"
    )


def format_job_description_messages(job: Job) -> list[str]:
    header_lines = [f"<b>Задание:</b> {html.escape(job.title)}"]
    order_id_line = _order_id_html_line(job).rstrip("\n")
    if order_id_line:
        header_lines.append(order_id_line)
    if job.url:
        header_lines.append(
            f'<a href="{job.url}">Открыть на {html.escape(job.platform)}</a>'
        )
    header = "\n".join(header_lines) + "\n\n<b>Описание:</b>\n\n"

    desc = html.escape(sanitize_proposal_text(job.description))
    skills = ""
    if job.skills:
        skills = "\n\n<b>Навыки:</b> " + ", ".join(
            html.escape(skill) for skill in job.skills[:15]
        )

    full = header + desc + skills
    if len(full) <= 4096:
        return [full]

    messages: list[str] = []
    first_limit = 4096 - len(header)
    messages.append(header + desc[:first_limit])
    offset = first_limit
    while offset < len(desc):
        messages.append(desc[offset : offset + 4096])
        offset += 4096

    if skills:
        last = messages[-1]
        if len(last) + len(skills) <= 4096:
            messages[-1] = last + skills
        else:
            messages.append(skills.lstrip("\n"))
    return messages


def format_job_alert(job: Job, proposal: Proposal, score: int | None) -> str:
    settings = get_settings()
    budget = format_budget(
        float(job.budget_min) if job.budget_min else None,
        float(job.budget_max) if job.budget_max else None,
        job.budget_currency or "RUB",
        job.platform,
    )

    offer_line = ""
    if job.platform == "kwork":
        offer = format_offer_price(
            float(job.budget_min) if job.budget_min else None,
            float(job.budget_max) if job.budget_max else None,
            job.budget_currency or "RUB",
            settings.kwork_offer_discount_percent,
        )
        if offer:
            offer_line = f"<b>Предлагаем:</b> {html.escape(offer)}\n"

    preview = sanitize_proposal_text(proposal.content[:800])
    if len(proposal.content) > 800:
        preview += "..."

    order_id_line = _order_id_html_line(job)

    return (
        f"<b>JobPilot AI Alert</b>\n\n"
        f"<b>New job found:</b>\n\n"
        f"<b>Title:</b> {html.escape(job.title)}\n"
        f"{order_id_line}"
        f"<b>Platform:</b> {html.escape(job.platform)}\n"
        f"<b>Budget:</b> {html.escape(budget)}\n"
        f"{offer_line}"
        f"<b>Score:</b> {score or 0}/100\n\n"
        f"<b>Proposal preview:</b>\n\n"
        f"{html.escape(preview)}"
    )


def format_kwork_failure_message(
    job: Job | None,
    send_error: str | None,
    debug: dict[str, Any] | None,
) -> str:
    parts = ["❌ JobPilot AI: не удалось отправить отклик на Kwork."]

    title = (job.title if job else None) or (debug or {}).get("job_title")
    if title:
        parts.append(f"\n\n<b>Задание:</b> {html.escape(title)}")

    order_id_line = _order_id_html_line(job).rstrip("\n")
    if not order_id_line and debug and debug.get("job_url"):
        order_id = extract_site_order_id(url=debug["job_url"], platform="kwork")
        if order_id:
            order_id_line = f"<b>№ заказа:</b> {html.escape(order_id)}"
    if order_id_line:
        parts.append(f"\n{order_id_line}")

    job_url = (job.url if job else None) or (debug or {}).get("job_url")
    if job_url:
        parts.append(f'\n<a href="{html.escape(job_url)}">Открыть на Kwork</a>')

    if send_error:
        parts.append(f"\n\n<b>Причина:</b> {html.escape(send_error)}")

    if debug:
        content_length = debug.get("content_length")
        if content_length is not None:
            parts.append(
                f"\n\n<b>Длина текста:</b> {content_length} симв. "
                f"(рекомендуется ≤1200, лимит Kwork: {KWORK_MAX_OFFER_CHARS})"
            )
        offer_price = debug.get("offer_price")
        if offer_price:
            parts.append(f"\n<b>Цена в отклике:</b> {offer_price} ₽")
        content_file = debug.get("content_file")
        if content_file:
            parts.append(
                f"\n\n<b>Текст отклика:</b> <code>{html.escape(str(content_file))}</code>"
            )
        steps = debug.get("steps")
        if steps:
            step_lines = "\n".join(
                f"{index}. {html.escape(str(step))}" for index, step in enumerate(steps, 1)
            )
            parts.append(f"\n\n<b>Шаги до ошибки:</b>\n{step_lines}")
        debug_file = debug.get("debug_file")
        screenshot = debug.get("screenshot")
        if debug_file or screenshot:
            parts.append("\n\n<b>Дебаг:</b>")
            if debug_file:
                parts.append(f"\nJSON: <code>{html.escape(str(debug_file))}</code>")
            if screenshot:
                parts.append(f"\nСкрин: <code>{html.escape(str(screenshot))}</code>")

    parts.append(
        "\n\nЧастые причины:\n"
        "1. Нет кворка с портфолио в нужной рубрике (SaaS/сайты)\n"
        "2. Не пройден урок по работе на Бирже\n"
        "3. Профиль продавца не подтверждён — kwork.ru/seller\n"
        "4. Сессия устарела — пересохраните data/kwork_session.json\n"
        "5. Заказ закрыт или форма отклика недоступна\n\n"
        "Логи: docker compose logs telegram-bot --tail=50"
    )
    text = "".join(parts)
    if len(text) > 4096:
        return text[:4093] + "..."
    return text


async def notify_new_proposal(
    bot: Bot,
    job_id: uuid.UUID,
    proposal_id: uuid.UUID,
    chat_id: int | None = None,
) -> int | None:
    settings = get_settings()
    target_chat = chat_id or int(settings.telegram_admin_chat_id)

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        proposal = await session.get(Proposal, proposal_id)
        if not job or not proposal:
            return None

        text = format_job_alert(job, proposal, job.score)
        if len(text) > 4000:
            text = text[:3997] + "..."

        pending = TelegramPending(
            job_id=job_id,
            proposal_id=proposal_id,
            chat_id=target_chat,
            status="pending",
        )
        session.add(pending)
        await session.flush()

        message = await bot.send_message(
            chat_id=target_chat,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=approval_keyboard(str(pending.id)),
        )

        pending.message_id = message.message_id
        await session.commit()
        return message.message_id


def create_dispatcher() -> Dispatcher:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    storage = RedisStorage(redis=redis)
    dp = Dispatcher(storage=storage)
    bot = create_telegram_bot(settings.telegram_bot_token, settings)

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "👋 <b>JobPilot AI</b> is running.\n\n"
            "I will send you freelance job alerts with proposals.\n"
            "Use buttons: APPROVE | EDIT | SKIP | Задание\n\n"
            "EDIT: напишите инструкцию для LLM (например: «сделай короче»).",
            parse_mode=ParseMode.HTML,
        )

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        async with AsyncSessionLocal() as session:
            pending_count = await session.execute(
                select(TelegramPending).where(TelegramPending.status == "pending")
            )
            count = len(pending_count.scalars().all())
        rewards = RewardSystem()
        total = await rewards.get_total_rewards()
        await message.answer(
            f"<b>JobPilot AI Status</b>\n\n"
            f"Pending approvals: {count}\n"
            f"Total rewards: {total}",
            parse_mode=ParseMode.HTML,
        )

    @dp.callback_query(F.data.startswith("approve:"))
    async def handle_approve(callback: CallbackQuery) -> None:
        pending_id = callback.data.split(":", 1)[1]
        await _process_approval(callback, pending_id, "approved")

    @dp.callback_query(F.data.startswith("skip:"))
    async def handle_skip(callback: CallbackQuery) -> None:
        pending_id = callback.data.split(":", 1)[1]
        await _process_approval(callback, pending_id, "skipped")

    @dp.callback_query(F.data.startswith("view_job:"))
    async def handle_view_job(callback: CallbackQuery) -> None:
        pending_id = callback.data.split(":", 1)[1]
        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramPending, uuid.UUID(pending_id))
            if not pending:
                await callback.answer("Request expired", show_alert=True)
                return
            job = await session.get(Job, pending.job_id)
            if not job:
                await callback.answer("Job not found", show_alert=True)
                return

        for part in format_job_description_messages(job):
            await callback.message.answer(part, parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.callback_query(F.data.startswith("edit:"))
    async def handle_edit(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramPending, uuid.UUID(pending_id))
            if not pending:
                await callback.answer("Request expired", show_alert=True)
                return
            if pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            job = await session.get(Job, pending.job_id)
            proposal = await session.get(Proposal, pending.proposal_id)
            if not proposal:
                await callback.answer("Proposal not found", show_alert=True)
                return
            job_id = str(pending.job_id)
            proposal_id = str(pending.proposal_id)
            original_content = proposal.content
            order_id = _resolve_order_id(job=job)

        await state.set_state(EditProposalState.waiting_for_instruction)
        await state.update_data(
            job_id=job_id,
            proposal_id=proposal_id,
            pending_id=pending_id,
            original_content=original_content,
            draft_content=original_content,
            edit_steps=[],
            order_id=order_id or "",
        )
        order_line = _order_id_html_line(job).rstrip("\n")
        if order_line:
            order_line = f"{order_line}\n"
        await callback.message.answer(
            "✏️ <b>Редактирование через LLM</b>\n\n"
            f"{order_line}"
            "Напишите, что изменить в отклике.\n"
            "Пример: <i>сделай короче, убери вопросы про сроки, добавь Next.js</i>",
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()

    @dp.message(EditProposalState.waiting_for_instruction)
    async def receive_edit_instruction(message: Message, state: FSMContext) -> None:
        instruction = (message.text or "").strip()
        if not instruction:
            await message.answer("Напишите инструкцию для правки отклика.")
            return

        data = await state.get_data()
        job_id = data["job_id"]
        proposal_id = data["proposal_id"]
        pending_id = data["pending_id"]
        before = data.get("draft_content") or data.get("original_content", "")
        order_id = data.get("order_id") or None

        pipeline = JobPipelineService()
        job_state = await pipeline.job_to_state(uuid.UUID(job_id))
        job = job_state.get("job_data", {})
        if not order_id:
            order_id = extract_site_order_id(
                external_id=job.get("external_id"),
                url=job.get("url"),
                platform=job.get("platform"),
            )

        await message.answer(
            f"⏳ Применяю правки через LLM...{_order_id_plain_suffix(order_id)}"
        )
        try:
            editor = EditProposalAgent()
            after = await editor.run(
                current_text=before,
                instruction=instruction,
                job=job,
            )
        except LLMServiceError as exc:
            logger.warning(
                "Edit proposal LLM failed",
                kind=exc.kind.value,
                error=exc.message,
                retryable=exc.retryable,
            )
            await message.answer(
                format_llm_error_message(exc, order_id),
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as exc:
            logger.error("Edit proposal unexpected error", error=str(exc))
            await message.answer(
                format_llm_error_message(exc, order_id),
                parse_mode=ParseMode.HTML,
            )
            return

        edit_steps = list(data.get("edit_steps") or [])
        edit_steps.append(
            {"instruction": instruction, "before": before, "after": after}
        )
        await state.update_data(draft_content=after, edit_steps=edit_steps, order_id=order_id or "")
        await state.set_state(EditProposalState.reviewing)

        await message.answer(
            format_edit_preview(after, instruction, order_id),
            parse_mode=ParseMode.HTML,
            reply_markup=edit_preview_keyboard(pending_id),
        )

    @dp.callback_query(F.data.startswith("edit_more:"))
    async def handle_edit_more(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if data.get("pending_id") != pending_id:
            await callback.answer("Сессия устарела", show_alert=True)
            return
        await state.set_state(EditProposalState.waiting_for_instruction)
        order_id = data.get("order_id") or None
        await callback.message.answer(
            f"✏️ Напишите следующую правку для отклика.{_order_id_plain_suffix(order_id)}"
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("edit_cancel:"))
    async def handle_edit_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if data.get("pending_id") != pending_id:
            await callback.answer("Сессия устарела", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_reply_markup(reply_markup=None)
        order_id = data.get("order_id") or None
        await callback.message.answer(
            f"Редактирование отменено.{_order_id_plain_suffix(order_id)}"
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("edit_send:"))
    async def handle_edit_send(callback: CallbackQuery, state: FSMContext) -> None:
        pending_id = callback.data.split(":", 1)[1]
        data = await state.get_data()
        if data.get("pending_id") != pending_id:
            await callback.answer("Сессия устарела", show_alert=True)
            return

        job_id = data["job_id"]
        proposal_id = data["proposal_id"]
        new_content = data.get("draft_content") or ""
        edit_steps = list(data.get("edit_steps") or [])

        if not new_content.strip():
            await callback.answer("Пустой отклик", show_alert=True)
            return

        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramPending, uuid.UUID(pending_id))
            if not pending or pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            job = await session.get(Job, pending.job_id)
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))
            if proposal:
                proposal.content = new_content
                proposal.version += 1
                proposal.status = "edited"
            pending.status = "edited"
            await session.commit()
            platform = job.platform if job else "unknown"
            job_context = f"{job.title} {job.description[:400]}" if job else ""

        await state.clear()
        await callback.message.edit_reply_markup(reply_markup=None)

        if await JobPipelineService().is_job_sent(uuid.UUID(job_id)):
            async with AsyncSessionLocal() as session:
                proposal = await session.get(Proposal, uuid.UUID(proposal_id))
                job_for_msg = await session.get(Job, uuid.UUID(job_id))
            if not proposal or proposal.status != "edited":
                duplicate_msg = "⚠️ JobPilot AI: по этому заказу отклик уже был отправлен ранее."
                order_id = _resolve_order_id(job=job_for_msg)
                if order_id:
                    duplicate_msg += f"\n№ заказа: {order_id}"
                await callback.message.answer(duplicate_msg)
                await callback.answer()
                return

        try:
            await callback.answer("⏳ Отправляю на Kwork...")
        except Exception:
            pass

        success, send_error, send_debug = await _resume_pipeline(
            job_id, proposal_id, "edited", new_content
        )
        if success:
            if edit_steps:
                await EditPreferenceService().record_edit_steps(
                    proposal_id=uuid.UUID(proposal_id),
                    job_id=uuid.UUID(job_id),
                    platform=platform,
                    job_context=job_context,
                    edit_steps=edit_steps,
                )
            await callback.message.answer(
                format_kwork_success_message(send_debug, edited=True, job=job)
            )
        else:
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, uuid.UUID(job_id))
            await callback.message.answer(
                format_kwork_failure_message(job, send_error, send_debug),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    async def _process_approval(
        callback: CallbackQuery,
        pending_id: str,
        action: str,
    ) -> None:
        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramPending, uuid.UUID(pending_id))
            if not pending or pending.status != "pending":
                await callback.answer("Already processed", show_alert=True)
                return
            job_id = str(pending.job_id)
            proposal_id = str(pending.proposal_id)
            job = await session.get(Job, pending.job_id)

            if action in ("approved", "edited") and await JobPipelineService().is_job_sent(
                pending.job_id
            ):
                pending.status = "duplicate"
                await session.commit()
                await callback.message.edit_reply_markup(reply_markup=None)
                try:
                    await callback.answer("Уже отправлено", show_alert=True)
                except Exception:
                    pass
                duplicate_msg = "⚠️ JobPilot AI: по этому заказу отклик уже был отправлен ранее."
                order_id = _resolve_order_id(job=job)
                if order_id:
                    duplicate_msg += f"\n№ заказа: {order_id}"
                await callback.message.answer(duplicate_msg)
                return

            pending.status = action
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))
            if proposal:
                proposal.status = action
            await session.commit()

        await callback.message.edit_reply_markup(reply_markup=None)

        if action == "skipped":
            try:
                await callback.answer("⏭ Skipped")
            except Exception:
                pass
            reward_system = RewardSystem()
            await reward_system.record_outcome(
                uuid.UUID(job_id), uuid.UUID(proposal_id), "ignored"
            )
            learning = LearningAgent()
            pipeline = JobPipelineService()
            state = await pipeline.job_to_state(uuid.UUID(job_id))
            state["outcome_status"] = "ignored"
            state["approval_status"] = "skipped"
            await learning.run(state)
            await callback.message.answer("JobPilot AI: job skipped.")
            return

        try:
            await callback.answer("⏳ Отправляю на Kwork...")
        except Exception:
            pass

        success, send_error, send_debug = await _resume_pipeline(job_id, proposal_id, action)
        if success:
            await callback.message.answer(
                format_kwork_success_message(send_debug, job=job),
            )
        else:
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, uuid.UUID(job_id))
            await callback.message.answer(
                format_kwork_failure_message(job, send_error, send_debug),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    async def _resume_pipeline(
        job_id: str,
        proposal_id: str,
        approval_status: str,
        edited_content: str | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        pipeline = JobPipelineService()
        base_state = await pipeline.job_to_state(uuid.UUID(job_id))

        async with AsyncSessionLocal() as session:
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))
            content = edited_content or (proposal.content if proposal else "")

        state = {
            **base_state,
            "proposal_id": proposal_id,
            "proposal_content": content,
            "approval_status": approval_status,
            "edited_proposal": edited_content or "",
        }

        graph = compile_jobpilot_graph()
        send_success = False
        send_error: str | None = None
        send_debug: dict[str, Any] | None = None
        if approval_status in ("approved", "edited"):
            sender = ProposalSender()
            if approval_status != "edited" and await sender.is_already_sent(job_id, proposal_id):
                return True, None, None

            from app.agents.graph import send_node

            send_result = await send_node(state)
            state.update(send_result)
            send_success = send_result.get("outcome_status") == "sent"
            send_error = send_result.get("send_error") or None
            send_debug = send_result.get("send_debug") or None
            learning = LearningAgent()
            learn_result = await learning.run(state)
            state.update(learn_result)
        else:
            result = await graph.ainvoke(state)
            state.update(result)
            send_success = state.get("outcome_status") == "sent"
            send_error = state.get("send_error") or None
            send_debug = state.get("send_debug") or None

        reward_system = RewardSystem()
        await reward_system.record_outcome(
            uuid.UUID(job_id),
            uuid.UUID(proposal_id),
            state.get("outcome_status", "sent" if send_success else "draft"),
        )
        return send_success, send_error, send_debug

    dp["bot_instance"] = bot
    return dp


async def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for JobPilot AI Telegram bot")

    bot = create_telegram_bot(settings.telegram_bot_token, settings)
    dp = create_dispatcher()

    logger.info("JobPilot AI Telegram bot starting")
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
