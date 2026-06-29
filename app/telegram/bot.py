import html
import asyncio
import uuid

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

from app.agents.graph import compile_jobpilot_graph
from app.agents.learning_agent import LearningAgent
from app.config import get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.job_pipeline import JobPipelineService
from app.services.proposal_sender import ProposalSender
from app.services.reward_system import RewardSystem
from app.telegram.keyboards import approval_keyboard
from app.utils.formatting import format_budget, sanitize_proposal_text

logger = structlog.get_logger(__name__)


class EditProposalState(StatesGroup):
    waiting_for_text = State()


def format_job_description_messages(job: Job) -> list[str]:
    header_lines = [f"<b>Задание:</b> {html.escape(job.title)}"]
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
    budget = format_budget(
        float(job.budget_min) if job.budget_min else None,
        float(job.budget_max) if job.budget_max else None,
        job.budget_currency or "RUB",
        job.platform,
    )

    preview = sanitize_proposal_text(proposal.content[:800])
    if len(proposal.content) > 800:
        preview += "..."

    return (
        f"<b>JobPilot AI Alert</b>\n\n"
        f"<b>New job found:</b>\n\n"
        f"<b>Title:</b> {html.escape(job.title)}\n"
        f"<b>Platform:</b> {html.escape(job.platform)}\n"
        f"<b>Budget:</b> {html.escape(budget)}\n"
        f"<b>Score:</b> {score or 0}/100\n\n"
        f"<b>Proposal preview:</b>\n\n"
        f"{html.escape(preview)}"
    )


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
    bot = Bot(token=settings.telegram_bot_token)

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "👋 <b>JobPilot AI</b> is running.\n\n"
            "I will send you freelance job alerts with proposals.\n"
            "Use buttons: APPROVE | EDIT | SKIP | Задание",
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
            job_id = str(pending.job_id)
            proposal_id = str(pending.proposal_id)
        await state.set_state(EditProposalState.waiting_for_text)
        await state.update_data(job_id=job_id, proposal_id=proposal_id, pending_id=pending_id)
        await callback.message.answer(
            "✏️ Send the edited proposal text for JobPilot AI:"
        )
        await callback.answer()

    @dp.message(EditProposalState.waiting_for_text)
    async def receive_edited_proposal(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        job_id = data["job_id"]
        proposal_id = data["proposal_id"]
        new_content = message.text or ""

        async with AsyncSessionLocal() as session:
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))
            if proposal:
                proposal.content = new_content
                proposal.version += 1
                proposal.status = "edited"
                await session.commit()

        await state.clear()

        if await JobPipelineService().is_job_sent(uuid.UUID(job_id)):
            await message.answer(
                "⚠️ JobPilot AI: по этому заказу отклик уже был отправлен ранее."
            )
            return

        success = await _resume_pipeline(job_id, proposal_id, "edited", new_content)
        if success:
            await message.answer("✅ JobPilot AI: отредактированный отклик отправлен на Kwork.")
        else:
            await message.answer(
                "❌ JobPilot AI: не удалось отправить отклик на Kwork. "
                "Проверьте учётные данные Kwork в .env."
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
                await callback.message.answer(
                    "⚠️ JobPilot AI: по этому заказу отклик уже был отправлен ранее."
                )
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

        success, send_error = await _resume_pipeline(job_id, proposal_id, action)
        if success:
            await callback.message.answer(
                "✅ JobPilot AI: отклик отправлен на Kwork."
            )
        else:
            reason = f"\n\n<b>Причина:</b> {html.escape(send_error)}" if send_error else ""
            await callback.message.answer(
                "❌ JobPilot AI: не удалось отправить отклик на Kwork."
                f"{reason}\n\n"
                "Частые причины:\n"
                "1. Нет кворка с портфолио в нужной рубрике (SaaS/сайты)\n"
                "2. Не пройден урок по работе на Бирже\n"
                "3. Профиль продавца не подтверждён — kwork.ru/seller\n"
                "4. Сессия устарела — пересохраните data/kwork_session.json\n"
                "5. Заказ закрыт или форма отклика недоступна\n\n"
                "Логи: docker compose logs telegram-bot --tail=50\n"
                "Скриншоты: data/kwork_debug/ (в контейнере /app/data/kwork_debug/)",
                parse_mode="HTML",
            )

    async def _resume_pipeline(
        job_id: str,
        proposal_id: str,
        approval_status: str,
        edited_content: str | None = None,
    ) -> tuple[bool, str | None]:
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
        if approval_status in ("approved", "edited"):
            sender = ProposalSender()
            if await sender.is_already_sent(job_id, proposal_id):
                return True, None

            from app.agents.graph import send_node

            send_result = await send_node(state)
            state.update(send_result)
            send_success = send_result.get("outcome_status") == "sent"
            send_error = send_result.get("send_error") or None
            learning = LearningAgent()
            learn_result = await learning.run(state)
            state.update(learn_result)
        else:
            result = await graph.ainvoke(state)
            state.update(result)
            send_success = state.get("outcome_status") == "sent"
            send_error = state.get("send_error") or None

        reward_system = RewardSystem()
        await reward_system.record_outcome(
            uuid.UUID(job_id),
            uuid.UUID(proposal_id),
            state.get("outcome_status", "sent" if send_success else "draft"),
        )
        return send_success, send_error

    dp["bot_instance"] = bot
    return dp


async def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for JobPilot AI Telegram bot")

    bot = Bot(token=settings.telegram_bot_token)
    dp = create_dispatcher()

    logger.info("JobPilot AI Telegram bot starting")
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
