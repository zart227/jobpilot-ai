"""Check Kwork inbox for new client messages and notify via Telegram."""

from __future__ import annotations

import html
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from aiogram.enums import ParseMode
from sqlalchemy import or_, select

from app.agents.chat_agent import ChatAgent
from app.config import Settings, get_settings
from app.db.models import Client, Interaction, Job, Proposal, TelegramInboxPending
from app.db.session import AsyncSessionLocal
from app.services.kwork_api import (
    KworkApiError,
    KworkAuthError,
    clean_kwork_message_text,
    create_kwork_api,
)
from app.telegram.keyboards import inbox_reply_keyboard
from app.utils.formatting import extract_site_order_id, sanitize_proposal_text
from app.utils.proxy import create_telegram_bot

logger = structlog.get_logger(__name__)

SYSTEM_MESSAGE_TYPES = frozenset(
    {
        "offer_kwork_new",
        "offer_kwork_done",
        "order_created",
        "order_completed",
        "order_cancelled",
        "custom_request",
    }
)
MAX_SEEN_KEYS = 1000


@dataclass
class InboxState:
    seen_keys: list[str]
    my_username: str = ""
    last_check_at: float = 0.0

    @classmethod
    def load(cls, path: Path) -> InboxState:
        if not path.is_file():
            return cls(seen_keys=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            keys = data.get("seen_keys", [])
            return cls(
                seen_keys=[str(k) for k in keys][-MAX_SEEN_KEYS:],
                my_username=str(data.get("my_username", "")),
                last_check_at=float(data.get("last_check_at", 0)),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls(seen_keys=[])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "seen_keys": self.seen_keys[-MAX_SEEN_KEYS:],
                    "my_username": self.my_username,
                    "last_check_at": self.last_check_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def mark_seen(self, key: str) -> None:
        if key in self.seen_keys:
            return
        self.seen_keys.append(key)
        if len(self.seen_keys) > MAX_SEEN_KEYS:
            self.seen_keys = self.seen_keys[-MAX_SEEN_KEYS:]

    def is_seen(self, key: str) -> bool:
        return key in self.seen_keys


@dataclass
class ClientInboxMessage:
    user_id: int
    username: str
    message_id: str
    text: str
    timestamp: int
    dialog: dict[str, Any]
    raw: dict[str, Any]


def _message_key(user_id: int | str, message: dict[str, Any]) -> str:
    msg_id = message.get("MID") or message.get("id") or message.get("message_id")
    if msg_id:
        return f"{user_id}:{msg_id}"
    ts = message.get("time") or message.get("sent_timestamp") or 0
    text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))[:80]
    return f"{user_id}:{ts}:{hash(text)}"


def _is_from_client(message: dict[str, Any], my_username: str, my_user_id: int | None) -> bool:
    msg_type = str(message.get("type", "text"))
    if msg_type in SYSTEM_MESSAGE_TYPES:
        return False

    sender_id = message.get("from_id") or message.get("user_from_id")
    if my_user_id and sender_id and int(sender_id) == int(my_user_id):
        return False

    sender = str(message.get("from_username", message.get("sender", ""))).lower()
    if my_username and sender == my_username.lower():
        return False

    text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))
    return bool(text.strip())


def _extract_project_id(message: dict[str, Any], dialog: dict[str, Any]) -> str | None:
    for source in (message, dialog):
        for key in ("want_id", "project_id", "wantId", "projectId"):
            value = source.get(key)
            if value:
                return str(value)
    return None


async def _find_job_for_message(
    session,
    *,
    username: str,
    project_id: str | None,
) -> tuple[Job | None, Proposal | None]:
    if project_id:
        pattern = f"%{project_id}%"
        result = await session.execute(
            select(Job)
            .where(
                Job.platform == "kwork",
                or_(Job.external_id.like(pattern), Job.url.like(pattern)),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job:
            proposal = await _latest_sent_proposal(session, job.id)
            return job, proposal

    if username:
        pattern = f"%{username}%"
        result = await session.execute(
            select(Job)
            .join(Client, Job.client_id == Client.id, isouter=True)
            .where(
                Job.platform == "kwork",
                Job.status == "sent",
                or_(
                    Client.name.ilike(pattern),
                    Client.external_id.ilike(pattern),
                ),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job:
            proposal = await _latest_sent_proposal(session, job.id)
            return job, proposal

    return None, None


async def _latest_sent_proposal(session, job_id: uuid.UUID) -> Proposal | None:
    result = await session.execute(
        select(Proposal)
        .where(Proposal.job_id == job_id)
        .order_by(Proposal.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def format_client_message_alert(
    msg: ClientInboxMessage,
    *,
    job: Job | None = None,
    chat_intent: str = "",
    chat_reply: str = "",
) -> str:
    username = html.escape(msg.username)
    text = html.escape(sanitize_proposal_text(msg.text))
    if len(text) > 1500:
        text = text[:1497] + "..."

    lines = [
        f"💬 <b>Kwork: сообщение от клиента</b>",
        f"<b>От:</b> {username}",
    ]

    order_id = None
    if job:
        order_id = extract_site_order_id(
            external_id=job.external_id,
            url=job.url,
            platform=job.platform,
        )
        title = html.escape(job.title[:120])
        if order_id:
            lines.append(f"<b>Заказ:</b> {title} (№{html.escape(order_id)})")
        else:
            lines.append(f"<b>Заказ:</b> {title}")
        if job.url:
            lines.append(f'<a href="{job.url}">Открыть проект</a>')

    lines.append("")
    lines.append(f"<b>Сообщение:</b>\n{text}")

    if chat_intent and chat_intent not in ("none", "error"):
        lines.append(f"\n<b>Интент:</b> {html.escape(chat_intent)}")

    if chat_reply.strip():
        reply = html.escape(sanitize_proposal_text(chat_reply))
        if len(reply) > 1200:
            reply = reply[:1197] + "..."
        lines.append(f"\n<b>Черновик ответа:</b>\n{reply}")
    elif not chat_reply.strip():
        lines.append("\n<i>Черновик не сгенерирован — нажмите EDIT и напишите ответ или инструкцию.</i>")

    body = "\n".join(lines)
    return body[:4096]


async def send_kwork_inbox_reply(
    *,
    user_id: int,
    username: str,
    reply_text: str,
    pending_id: uuid.UUID,
    interaction_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> tuple[bool, str | None]:
    settings = settings or get_settings()
    api = None
    try:
        api = await create_kwork_api(settings)
        await api.send_message(user_id, reply_text)
        try:
            await api.mark_read(username)
        except KworkApiError as exc:
            logger.warning("Kwork mark_read failed", username=username, error=str(exc))

        async with AsyncSessionLocal() as session:
            pending = await session.get(TelegramInboxPending, pending_id)
            if pending:
                pending.status = "sent"
                pending.draft_reply = reply_text

            session.add(
                Interaction(
                    job_id=job_id,
                    proposal_id=proposal_id,
                    direction="outbound",
                    channel="kwork",
                    message=reply_text,
                    intent="reply_sent",
                    metadata_={
                        "username": username,
                        "user_id": user_id,
                        "pending_id": str(pending_id),
                        "interaction_id": str(interaction_id) if interaction_id else None,
                    },
                )
            )
            await session.commit()

        logger.info("Kwork inbox reply sent", username=username, user_id=user_id)
        return True, None
    except KworkApiError as exc:
        logger.error("Kwork inbox reply failed", username=username, error=str(exc))
        return False, str(exc)
    except Exception as exc:
        logger.error("Kwork inbox reply unexpected error", username=username, error=str(exc))
        return False, str(exc)
    finally:
        if api is not None:
            await api.close()


async def _collect_new_messages(
    api,
    state: InboxState,
    *,
    my_username: str,
    my_user_id: int | None,
) -> list[ClientInboxMessage]:
    dialogs = await api.get_dialogs()
    unread = [
        d
        for d in dialogs
        if int(d.get("unread_count") or 0) > 0 or d.get("unread")
    ]
    if not unread:
        return []

    collected: list[ClientInboxMessage] = []
    for dialog in unread:
        username = str(dialog.get("username") or "")
        user_id = dialog.get("user_id")
        if not username or not user_id:
            continue

        try:
            messages = await api.get_messages(username)
        except KworkApiError as exc:
            logger.warning("Kwork inbox messages failed", username=username, error=str(exc))
            continue

        for message in messages or []:
            if not _is_from_client(message, my_username, my_user_id):
                continue
            key = _message_key(user_id, message)
            if state.is_seen(key):
                continue

            text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))
            collected.append(
                ClientInboxMessage(
                    user_id=int(user_id),
                    username=username,
                    message_id=key.split(":", 1)[-1],
                    text=text,
                    timestamp=int(message.get("time") or message.get("sent_timestamp") or 0),
                    dialog=dialog,
                    raw=message,
                )
            )
            state.mark_seen(key)

    collected.sort(key=lambda item: item.timestamp)
    return collected


async def _process_and_notify(
    settings: Settings,
    messages: list[ClientInboxMessage],
    *,
    bot=None,
) -> int:
    if not messages:
        return 0

    owns_bot = bot is None
    if owns_bot:
        if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
            return 0
        bot = create_telegram_bot(settings.telegram_bot_token, settings)

    chat_agent = ChatAgent()
    notified = 0
    target_chat = int(settings.telegram_admin_chat_id)

    try:
        for msg in messages:
            job: Job | None = None
            proposal: Proposal | None = None
            chat_intent = ""
            chat_reply = ""

            interaction_id: uuid.UUID | None = None
            async with AsyncSessionLocal() as session:
                project_id = _extract_project_id(msg.raw, msg.dialog)
                job, proposal = await _find_job_for_message(
                    session,
                    username=msg.username,
                    project_id=project_id,
                )

                if job and proposal:
                    from app.services.job_pipeline import JobPipelineService

                    pipeline = JobPipelineService()
                    agent_state = await pipeline.job_to_state(job.id)
                    agent_state["client_message"] = msg.text
                    agent_state["proposal_content"] = proposal.content
                    try:
                        result = await chat_agent.run(agent_state)
                        chat_intent = str(result.get("chat_intent", ""))
                        chat_reply = str(result.get("chat_reply", ""))
                    except Exception as exc:
                        logger.warning("ChatAgent failed for inbox message", error=str(exc))

                interaction = Interaction(
                    job_id=job.id if job else None,
                    proposal_id=proposal.id if proposal else None,
                    direction="inbound",
                    channel="kwork",
                    message=msg.text,
                    intent=chat_intent or None,
                    metadata_={
                        "username": msg.username,
                        "user_id": msg.user_id,
                        "message_key": _message_key(msg.user_id, msg.raw),
                        "timestamp": msg.timestamp,
                    },
                )
                session.add(interaction)
                await session.flush()
                interaction_id = interaction.id

                pending = TelegramInboxPending(
                    interaction_id=interaction_id,
                    job_id=job.id if job else None,
                    proposal_id=proposal.id if proposal else None,
                    kwork_user_id=msg.user_id,
                    kwork_username=msg.username,
                    client_message=msg.text,
                    draft_reply=chat_reply,
                    chat_intent=chat_intent or None,
                    chat_id=target_chat,
                    status="pending",
                )
                session.add(pending)
                await session.flush()
                pending_id = str(pending.id)
                await session.commit()

            text = format_client_message_alert(
                msg,
                job=job,
                chat_intent=chat_intent,
                chat_reply=chat_reply,
            )
            message = await bot.send_message(
                target_chat,
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
                reply_markup=inbox_reply_keyboard(pending_id),
            )
            async with AsyncSessionLocal() as session:
                pending_row = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
                if pending_row:
                    pending_row.message_id = message.message_id
                    await session.commit()
            notified += 1
            logger.info(
                "Kwork client message notified",
                username=msg.username,
                job_id=str(job.id) if job else None,
                intent=chat_intent,
            )
    finally:
        if owns_bot and bot is not None:
            await bot.session.close()

    return notified


async def check_kwork_inbox(
    settings: Settings | None = None,
    *,
    bot=None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.kwork_inbox_check_enabled:
        return {"skipped": True, "reason": "disabled"}
    if not settings.kwork_email or not settings.kwork_password:
        return {"skipped": True, "reason": "no_credentials"}
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        return {"skipped": True, "reason": "no_telegram"}

    state_path = Path(settings.kwork_inbox_state_file)
    state = InboxState.load(state_path)
    first_run = not state_path.is_file()
    api = None

    try:
        api = await create_kwork_api(settings)
        actor = await api.get_actor()
        my_username = str(actor.get("username") or state.my_username or "")
        my_user_id = actor.get("id")
        state.my_username = my_username

        new_messages = await _collect_new_messages(
            api,
            state,
            my_username=my_username,
            my_user_id=int(my_user_id) if my_user_id else None,
        )
        state.last_check_at = datetime.now(UTC).timestamp()
        state.save(state_path)

        if first_run and new_messages:
            # First run: seed state without flooding Telegram with old unread messages.
            logger.info(
                "Kwork inbox initialized",
                seeded=len(new_messages),
            )
            return {
                "checked": True,
                "initialized": True,
                "seeded": len(new_messages),
                "notified": 0,
            }

        notified = await _process_and_notify(settings, new_messages, bot=bot)
        return {
            "checked": True,
            "unread_dialogs": len(new_messages),
            "notified": notified,
        }
    except KworkAuthError as exc:
        logger.warning("Kwork inbox check auth failed", error=str(exc))
        return {"skipped": True, "error": str(exc)}
    except Exception as exc:
        logger.error("Kwork inbox check failed", error=str(exc))
        return {"skipped": True, "error": str(exc)}
    finally:
        if api is not None:
            await api.close()
