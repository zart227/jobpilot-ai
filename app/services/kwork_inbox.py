"""Check Kwork inbox for new client messages and notify via Telegram."""

from __future__ import annotations

import html
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from aiogram.enums import ParseMode
from sqlalchemy import select

from app.agents.chat_agent import ChatAgent
from app.config import Settings, get_settings
from app.db.models import (
    InboundMessage,
    Interaction,
    Job,
    Proposal,
    TelegramInboxPending,
)
from app.db.session import AsyncSessionLocal
from app.services.conversation_context_service import build_chat_agent_state
from app.services.kwork_api import (
    KworkApiError,
    KworkAuthError,
    clean_kwork_message_text,
    create_kwork_api,
)
from app.services.kwork_conversation import (
    is_from_client as _is_from_client,
)
from app.services.kwork_job_match import find_kwork_job_for_message
from app.services.notification_outbox import deliver_notification_outbox, enqueue_notification
from app.services.send_attempt_service import finalize_send_attempt, prepare_inbox_reply_send
from app.telegram.keyboards import inbox_reply_keyboard
from app.utils.formatting import extract_site_order_id, sanitize_proposal_text
from app.utils.proxy import create_telegram_bot

logger = structlog.get_logger(__name__)

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
    thread: list[dict[str, Any]] | None = None


def _message_key(user_id: int | str, message: dict[str, Any]) -> str:
    msg_id = message.get("MID") or message.get("id") or message.get("message_id")
    if msg_id:
        return f"{user_id}:{msg_id}"
    ts = message.get("time") or message.get("sent_timestamp") or 0
    text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))[:80]
    return f"{user_id}:{ts}:{hash(text)}"


async def _fetch_kwork_thread(username: str, settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    api = None
    try:
        api = await create_kwork_api(settings)
        actor = await api.get_actor()
        _ = actor
        return await api.get_messages(username)
    except (KworkAuthError, KworkApiError) as exc:
        logger.warning("Kwork thread fetch failed", username=username, error=str(exc))
        return []
    finally:
        if api is not None:
            await api.close()


def _extract_project_id(message: dict[str, Any], dialog: dict[str, Any]) -> str | None:
    for source in (message, dialog):
        for key in ("want_id", "project_id", "wantId", "projectId"):
            value = source.get(key)
            if value:
                return str(value)

    raw_text = str(message.get("message", message.get("text", "")))
    match = re.search(r"/projects/(\d+)", raw_text)
    if match:
        return match.group(1)

    cleaned = clean_kwork_message_text(raw_text)
    match = re.search(r"/projects/(\d+)", cleaned)
    if match:
        return match.group(1)

    return None


async def _generate_inbox_reply(
    job: Job,
    proposal: Proposal | None,
    client_message: str,
    *,
    username: str = "",
    thread_messages: list[dict[str, Any]] | None = None,
    my_username: str = "",
    my_user_id: int | None = None,
) -> tuple[str, str]:
    async with AsyncSessionLocal() as session:
        thread = thread_messages
        if not thread and username:
            thread = await _fetch_kwork_thread(username)
        agent_state = await build_chat_agent_state(
            session,
            job.id,
            client_message=client_message,
            channel="kwork",
            thread_messages=thread,
            my_username=my_username,
            my_user_id=my_user_id,
            proposal=proposal,
        )

    chat_agent = ChatAgent()
    result = await chat_agent.run(agent_state)
    return str(result.get("chat_intent", "")), str(result.get("chat_reply", ""))


def format_client_message_alert(
    msg: ClientInboxMessage,
    *,
    job: Job | None = None,
    chat_intent: str = "",
    chat_reply: str = "",
    needs_context: bool = False,
    candidate_job_count: int = 0,
) -> str:
    username = html.escape(msg.username)
    text = html.escape(sanitize_proposal_text(msg.text))
    if len(text) > 1500:
        text = text[:1497] + "..."

    lines = [
        "💬 <b>Kwork: сообщение от клиента</b>",
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

    if needs_context:
        count = candidate_job_count or 2
        lines.append(
            f"\n⚠️ <b>Нужен выбор заказа</b>: найдено {count} подходящих заказов. "
            "Черновик не привязан к конкретному заказу — уточните проект вручную."
        )

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
    claim = await prepare_inbox_reply_send(
        pending_id=pending_id,
        reply_text=reply_text,
        expected_user_id=user_id,
    )
    if claim.action == "already_sent":
        return True, None
    if claim.action == "unchanged":
        return True, None
    if claim.action != "proceed" or not claim.attempt_id or not claim.claim_token:
        return False, claim.reason or "send_blocked"

    api = None
    error: str | None = None
    success = False
    if settings.platform_dry_run:
        logger.info(
            "Platform dry-run: skipping Kwork inbox reply API",
            username=username,
            user_id=user_id,
        )
        success = True
    try:
        if not success:
            api = await create_kwork_api(settings)
            await api.send_message(user_id, reply_text)
            try:
                await api.mark_read(username)
            except KworkApiError as exc:
                logger.warning("Kwork mark_read failed", username=username, error=str(exc))
            success = True
    except KworkApiError as exc:
        error = str(exc)
        logger.error("Kwork inbox reply failed", username=username, error=error)
    except Exception as exc:
        error = str(exc)
        logger.error("Kwork inbox reply unexpected error", username=username, error=error)
    finally:
        if api is not None:
            await api.close()

    finalized = await finalize_send_attempt(
        claim.attempt_id,
        claim.claim_token,
        success=success,
        error=error,
        platform_response={"username": username, "user_id": user_id},
    )
    if not finalized:
        return False, "Устаревший claim отправки"

    if success:
        async with AsyncSessionLocal() as session:
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
                        "send_attempt_id": str(claim.attempt_id),
                    },
                )
            )
            await session.commit()
        logger.info("Kwork inbox reply sent", username=username, user_id=user_id)
        return True, None
    return False, error


async def _inbound_already_recorded(session, platform: str, message_key: str) -> bool:
    result = await session.execute(
        select(InboundMessage.id).where(
            InboundMessage.platform == platform,
            InboundMessage.message_key == message_key,
        )
    )
    return result.scalar_one_or_none() is not None


async def _seed_inbound_message(msg: ClientInboxMessage) -> None:
    message_key = _message_key(msg.user_id, msg.raw)
    async with AsyncSessionLocal() as session:
        if await _inbound_already_recorded(session, "kwork", message_key):
            return
        inbound = InboundMessage(
            platform="kwork",
            message_key=message_key,
            thread_key=str(msg.user_id),
            kwork_user_id=msg.user_id,
            kwork_username=msg.username,
            body=msg.text,
            raw_metadata={"seeded": True, "timestamp": msg.timestamp},
            status="seeded",
        )
        session.add(inbound)
        await session.commit()


async def _persist_inbound_bundle(
    settings: Settings,
    msg: ClientInboxMessage,
    *,
    target_chat: int,
    my_username: str = "",
    my_user_id: int | None = None,
) -> str | None:
    """Persist inbound + draft + outbox in one transaction. Returns pending_id or None if duplicate."""
    message_key = _message_key(msg.user_id, msg.raw)

    async with AsyncSessionLocal() as session:
        if await _inbound_already_recorded(session, "kwork", message_key):
            return None

        job: Job | None = None
        proposal: Proposal | None = None
        chat_intent = ""
        chat_reply = ""

        project_id = _extract_project_id(msg.raw, msg.dialog)
        match = await find_kwork_job_for_message(
            session,
            username=msg.username,
            project_id=project_id,
        )
        needs_context = match.needs_context
        job = match.job if not needs_context else None
        proposal = match.proposal if not needs_context else None

        if job:
            try:
                chat_intent, chat_reply = await _generate_inbox_reply(
                    job,
                    proposal,
                    msg.text,
                    username=msg.username,
                    thread_messages=msg.thread,
                    my_username=my_username,
                    my_user_id=my_user_id,
                )
            except Exception as exc:
                logger.warning("ChatAgent failed for inbox message", error=str(exc))

        inbound = InboundMessage(
            platform="kwork",
            message_key=message_key,
            thread_key=str(msg.user_id),
            kwork_user_id=msg.user_id,
            kwork_username=msg.username,
            body=msg.text,
            raw_metadata={
                "username": msg.username,
                "user_id": msg.user_id,
                "timestamp": msg.timestamp,
            },
            status="received",
        )
        session.add(inbound)
        await session.flush()

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
                "message_key": message_key,
                "timestamp": msg.timestamp,
                "inbound_id": str(inbound.id),
                "needs_context": needs_context,
                "candidate_job_ids": [str(j.id) for j in match.candidate_jobs],
            },
        )
        session.add(interaction)
        await session.flush()

        pending = TelegramInboxPending(
            interaction_id=interaction.id,
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

        inbound.interaction_id = interaction.id
        inbound.inbox_pending_id = pending.id
        inbound.status = "processed"
        inbound.processed_at = datetime.now(UTC)

        alert_text = format_client_message_alert(
            msg,
            job=job,
            chat_intent=chat_intent,
            chat_reply=chat_reply,
            needs_context=needs_context,
            candidate_job_count=len(match.candidate_jobs),
        )
        pending_id = str(pending.id)
        await enqueue_notification(
            kind="inbox_alert",
            idempotency_key=f"inbox_alert:{pending_id}",
            chat_id=target_chat,
            payload={"pending_id": pending_id, "alert_text": alert_text},
            reference_type="telegram_inbox_pending",
            reference_id=pending.id,
            session=session,
        )
        await session.commit()
        return pending_id


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
    async with AsyncSessionLocal() as session:
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
                if await _inbound_already_recorded(session, "kwork", key):
                    state.mark_seen(key)
                    continue

                text = clean_kwork_message_text(
                    str(message.get("message", message.get("text", "")))
                )
                collected.append(
                    ClientInboxMessage(
                        user_id=int(user_id),
                        username=username,
                        message_id=key.split(":", 1)[-1],
                        text=text,
                        timestamp=int(message.get("time") or message.get("sent_timestamp") or 0),
                        dialog=dialog,
                        raw=message,
                        thread=list(messages or []),
                    )
                )

    collected.sort(key=lambda item: item.timestamp)
    return collected


async def _process_and_notify(
    settings: Settings,
    messages: list[ClientInboxMessage],
    *,
    bot=None,
    my_username: str = "",
    my_user_id: int | None = None,
    state: InboxState | None = None,
) -> int:
    if not messages:
        return 0
    if not settings.telegram_admin_chat_id:
        return 0

    target_chat = int(settings.telegram_admin_chat_id)
    persisted = 0
    for msg in messages:
        pending_id = await _persist_inbound_bundle(
            settings,
            msg,
            target_chat=target_chat,
            my_username=my_username,
            my_user_id=my_user_id,
        )
        if not pending_id:
            continue
        persisted += 1
        if state is not None:
            state.mark_seen(_message_key(msg.user_id, msg.raw))
        logger.info(
            "Kwork inbound persisted",
            username=msg.username,
            pending_id=pending_id,
        )

    if persisted == 0:
        return 0

    delivery = await deliver_notification_outbox(settings, bot=bot)
    return int(delivery.get("delivered", 0))


async def _find_last_client_message(
    api,
    *,
    my_username: str,
    my_user_id: int | None,
    username: str | None = None,
    user_id: int | None = None,
) -> tuple[ClientInboxMessage | None, dict[str, Any] | None]:
    dialogs = await api.get_dialogs()
    if not dialogs:
        return None, None

    if username:
        dialog = next(
            (d for d in dialogs if str(d.get("username", "")).lower() == username.lower()),
            None,
        )
    elif user_id:
        dialog = next((d for d in dialogs if int(d.get("user_id") or 0) == int(user_id)), None)
    else:
        dialog = max(
            dialogs,
            key=lambda d: int(d.get("last_message_time") or d.get("time") or 0),
        )

    if not dialog:
        return None, None

    dialog_username = str(dialog.get("username") or "")
    dialog_user_id = dialog.get("user_id")
    if not dialog_username or not dialog_user_id:
        return None, dialog

    messages = await api.get_messages(dialog_username)
    client_messages: list[ClientInboxMessage] = []
    for message in messages or []:
        if not _is_from_client(message, my_username, my_user_id):
            continue
        text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))
        client_messages.append(
            ClientInboxMessage(
                user_id=int(dialog_user_id),
                username=dialog_username,
                message_id=str(message.get("MID") or message.get("id") or message.get("message_id") or ""),
                text=text,
                timestamp=int(message.get("time") or message.get("sent_timestamp") or 0),
                dialog=dialog,
                raw=message,
                thread=list(messages or []),
            )
        )

    if not client_messages:
        return None, dialog

    client_messages.sort(key=lambda item: item.timestamp)
    return client_messages[-1], dialog


async def resend_last_inbox_message(
    settings: Settings | None = None,
    *,
    username: str | None = None,
    user_id: int | None = None,
    bot=None,
) -> dict[str, Any]:
    """Re-notify Telegram for the latest client inbox message (with EDIT/SEND buttons)."""
    settings = settings or get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        return {"skipped": True, "reason": "no_telegram"}

    api = None
    try:
        api = await create_kwork_api(settings)
        actor = await api.get_actor()
        my_username = str(actor.get("username") or "")
        my_user_id = int(actor["id"]) if actor.get("id") else None

        msg, dialog = await _find_last_client_message(
            api,
            my_username=my_username,
            my_user_id=my_user_id,
            username=username,
            user_id=user_id,
        )
        if msg is None:
            return {"skipped": True, "reason": "no_client_message", "dialog": dialog}

        notified = await _process_and_notify(
            settings,
            [msg],
            bot=bot,
            my_username=my_username,
            my_user_id=my_user_id,
        )
        return {
            "resent": notified > 0,
            "notified": notified,
            "username": msg.username,
            "user_id": msg.user_id,
            "message_preview": msg.text[:120],
        }
    except KworkAuthError as exc:
        logger.warning("Kwork inbox resend auth failed", error=str(exc))
        return {"skipped": True, "error": str(exc)}
    except Exception as exc:
        logger.error("Kwork inbox resend failed", error=str(exc))
        return {"skipped": True, "error": str(exc)}
    finally:
        if api is not None:
            await api.close()


async def _get_kwork_actor(settings: Settings | None = None) -> tuple[str, int | None]:
    settings = settings or get_settings()
    api = None
    try:
        api = await create_kwork_api(settings)
        actor = await api.get_actor()
        my_user_id = int(actor["id"]) if actor.get("id") else None
        return str(actor.get("username") or ""), my_user_id
    except (KworkAuthError, KworkApiError) as exc:
        logger.warning("Kwork actor fetch failed", error=str(exc))
        return "", None
    finally:
        if api is not None:
            await api.close()


async def regenerate_pending_inbox_drafts(
    settings: Settings | None = None,
    *,
    bot=None,
    pending_only: bool = True,
) -> dict[str, Any]:
    """Regenerate draft replies for inbox pending rows and refresh Telegram messages."""
    settings = settings or get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        return {"skipped": True, "reason": "no_telegram"}

    owns_bot = bot is None
    if owns_bot:
        bot = create_telegram_bot(settings.telegram_bot_token, settings)

    updated = 0
    my_username, my_user_id = await _get_kwork_actor(settings)
    try:
        async with AsyncSessionLocal() as session:
            query = select(TelegramInboxPending).order_by(TelegramInboxPending.created_at.desc())
            if pending_only:
                query = query.where(TelegramInboxPending.status == "pending")
            rows = list((await session.execute(query)).scalars().all())

        for pending in rows:
            async with AsyncSessionLocal() as session:
                row = await session.get(TelegramInboxPending, pending.id)
                if not row:
                    continue
                job = await session.get(Job, row.job_id) if row.job_id else None
                proposal = (
                    await session.get(Proposal, row.proposal_id) if row.proposal_id else None
                )
                if not job:
                    match = await find_kwork_job_for_message(
                        session,
                        username=row.kwork_username,
                        project_id=None,
                    )
                    if match.needs_context:
                        continue
                    job = match.job
                    proposal = match.proposal
                    if job:
                        row.job_id = job.id
                        row.proposal_id = proposal.id if proposal else None

                if not job:
                    continue

                try:
                    thread = await _fetch_kwork_thread(row.kwork_username, settings)
                    chat_intent, chat_reply = await _generate_inbox_reply(
                        job,
                        proposal,
                        row.client_message,
                        username=row.kwork_username,
                        thread_messages=thread or None,
                        my_username=my_username,
                        my_user_id=my_user_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Inbox draft regeneration failed",
                        pending_id=str(row.id),
                        error=str(exc),
                    )
                    continue

                row.draft_reply = chat_reply
                row.chat_intent = chat_intent or None
                if proposal and not row.proposal_id:
                    row.proposal_id = proposal.id
                await session.commit()

            if row.message_id and row.chat_id:
                msg = ClientInboxMessage(
                    user_id=row.kwork_user_id,
                    username=row.kwork_username,
                    message_id="",
                    text=row.client_message,
                    timestamp=0,
                    dialog={},
                    raw={},
                )
                text = format_client_message_alert(
                    msg,
                    job=job,
                    chat_intent=chat_intent,
                    chat_reply=chat_reply,
                )
                try:
                    from aiogram.enums import ParseMode

                    from app.telegram.keyboards import inbox_reply_keyboard

                    await bot.edit_message_text(
                        text,
                        chat_id=row.chat_id,
                        message_id=row.message_id,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                        reply_markup=inbox_reply_keyboard(str(row.id)),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to refresh inbox Telegram message",
                        pending_id=str(row.id),
                        error=str(exc),
                    )
            updated += 1

        return {"updated": updated}
    finally:
        if owns_bot and bot is not None:
            await bot.session.close()


async def resend_inbox_pending_notifications(
    settings: Settings | None = None,
    *,
    limit: int = 2,
    usernames: list[str] | None = None,
    bot=None,
) -> dict[str, Any]:
    """Regenerate drafts and send fresh Telegram notifications for pending inbox rows."""
    settings = settings or get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        return {"skipped": True, "reason": "no_telegram"}

    owns_bot = bot is None
    if owns_bot:
        bot = create_telegram_bot(settings.telegram_bot_token, settings)

    target_chat = int(settings.telegram_admin_chat_id)
    sent: list[dict[str, Any]] = []
    my_username, my_user_id = await _get_kwork_actor(settings)

    try:
        async with AsyncSessionLocal() as session:
            query = (
                select(TelegramInboxPending)
                .where(TelegramInboxPending.status == "pending")
                .order_by(TelegramInboxPending.created_at.desc())
            )
            if usernames:
                query = query.where(TelegramInboxPending.kwork_username.in_(usernames))
            if limit > 0:
                query = query.limit(limit)
            rows = list((await session.execute(query)).scalars().all())

        for pending in rows:
            async with AsyncSessionLocal() as session:
                row = await session.get(TelegramInboxPending, pending.id)
                if not row or row.status != "pending":
                    continue

                job = await session.get(Job, row.job_id) if row.job_id else None
                proposal = (
                    await session.get(Proposal, row.proposal_id) if row.proposal_id else None
                )
                if not job:
                    match = await find_kwork_job_for_message(
                        session,
                        username=row.kwork_username,
                        project_id=None,
                    )
                    if match.needs_context:
                        continue
                    job = match.job
                    proposal = match.proposal
                    if job:
                        row.job_id = job.id
                        row.proposal_id = proposal.id if proposal else None

                chat_intent = row.chat_intent or ""
                chat_reply = row.draft_reply or ""
                if job:
                    try:
                        thread = await _fetch_kwork_thread(row.kwork_username, settings)
                        chat_intent, chat_reply = await _generate_inbox_reply(
                            job,
                            proposal,
                            row.client_message,
                            username=row.kwork_username,
                            thread_messages=thread or None,
                            my_username=my_username,
                            my_user_id=my_user_id,
                        )
                        row.draft_reply = chat_reply
                        row.chat_intent = chat_intent or None
                        if proposal and not row.proposal_id:
                            row.proposal_id = proposal.id
                    except Exception as exc:
                        logger.warning(
                            "Inbox resend draft generation failed",
                            pending_id=str(row.id),
                            error=str(exc),
                        )

                row.chat_id = target_chat
                await session.commit()
                pending_id = str(row.id)
                client_message = row.client_message
                username = row.kwork_username
                user_id = row.kwork_user_id

            msg = ClientInboxMessage(
                user_id=user_id,
                username=username,
                message_id="",
                text=client_message,
                timestamp=0,
                dialog={},
                raw={},
            )
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
                row = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
                if row:
                    row.message_id = message.message_id
                    await session.commit()

            sent.append(
                {
                    "pending_id": pending_id,
                    "username": username,
                    "message_id": message.message_id,
                    "draft_preview": chat_reply[:120],
                }
            )
            logger.info(
                "Inbox pending notification resent",
                username=username,
                pending_id=pending_id,
                message_id=message.message_id,
            )

        return {"sent": len(sent), "items": sent}
    finally:
        if owns_bot and bot is not None:
            await bot.session.close()


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

        if first_run and new_messages:
            for msg in new_messages:
                await _seed_inbound_message(msg)
                state.mark_seen(_message_key(msg.user_id, msg.raw))
            state.last_check_at = datetime.now(UTC).timestamp()
            state.save(state_path)
            logger.info(
                "Kwork inbox initialized (seeded without Telegram)",
                seeded=len(new_messages),
            )
            return {
                "checked": True,
                "initialized": True,
                "seeded": len(new_messages),
                "notified": 0,
            }

        notified = await _process_and_notify(
            settings,
            new_messages,
            bot=bot,
            my_username=my_username,
            my_user_id=int(my_user_id) if my_user_id else None,
            state=state,
        )
        state.last_check_at = datetime.now(UTC).timestamp()
        state.save(state_path)
        return {
            "checked": True,
            "unread_dialogs": len(new_messages),
            "persisted": len(new_messages),
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
