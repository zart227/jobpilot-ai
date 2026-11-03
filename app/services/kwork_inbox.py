"""Check Kwork inbox for new client messages and notify via Telegram."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.agents.chat_agent import ChatAgent
from app.config import Settings, get_settings
from app.db.models import (
    InboundMessage,
    Interaction,
    Job,
    NotificationOutbox,
    Proposal,
    TelegramInboxPending,
)
from app.db.session import AsyncSessionLocal
from app.services.conversation_context_service import (
    build_chat_agent_state,
    finalize_chat_agent_state,
)
from app.services.inbox_approval import capture_inbox_presentation
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
from app.services.send_attempt_service import (
    delivery_blocks_mutation,
    finalize_send_attempt,
    prepare_inbox_reply_send,
)
from app.utils.formatting import extract_site_order_id, sanitize_proposal_text

logger = structlog.get_logger(__name__)

MAX_SEEN_KEYS = 1000


@dataclass
class InboxState:
    seen_keys: list[str]
    my_username: str = ""
    last_check_at: float = 0.0
    identity_ambiguities: list[dict[str, Any]] = field(default_factory=list)

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
    try:
        ts = int(message.get("time") or message.get("sent_timestamp") or 0)
    except (TypeError, ValueError, OverflowError):
        ts = 0
    text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))
    if ts <= 0 or not text.strip():
        raise ValueError("message without platform ID requires timestamp and text")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{user_id}:v2:{ts}:{digest}"


def _legacy_fallback_keys(state: InboxState, user_id: int | str, timestamp: int) -> list[str]:
    pattern = re.compile(rf"^{re.escape(str(user_id))}:{timestamp}:-?\d+$")
    return [key for key in state.seen_keys if pattern.fullmatch(key)]


async def _legacy_inbound_exists(session, user_id: int, timestamp: int, body: str) -> bool:
    rows = await session.scalars(select(InboundMessage.message_key).where(
        InboundMessage.platform == "kwork", InboundMessage.kwork_user_id == user_id,
        InboundMessage.message_key.like(f"{user_id}:{timestamp}:%"),
        InboundMessage.body == body,
    ))
    pattern = re.compile(rf"^{user_id}:{timestamp}:-?\d+$")
    return any(pattern.fullmatch(key) for key in rows)


async def _fetch_kwork_thread(
    username: str, settings: Settings | None = None
) -> list[dict[str, Any]]:
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
    thread = thread_messages
    if not thread and username:
        thread = await _fetch_kwork_thread(username)
    async with AsyncSessionLocal() as session:
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

    agent_state = await finalize_chat_agent_state(agent_state)
    chat_agent = ChatAgent()
    result = await chat_agent.run(agent_state)
    if result.get("pipeline_processing_error") or not str(result.get("chat_reply", "")).strip():
        kind = result.get("processing_error_kind", "unknown")
        detail = result.get("error") or "empty draft"
        raise RuntimeError(f"inbox reply generation failed ({kind}): {detail}")
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
        reason = (
            f"найдено {candidate_job_count} подходящих заказов. "
            if candidate_job_count > 1
            else "связанный заказ не определён. "
        )
        lines.append(
            f"\n⚠️ <b>Нужен выбор заказа</b>: {reason}"
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
        lines.append(
            "\n<i>Черновик не сгенерирован — нажмите EDIT и напишите ответ или инструкцию.</i>"
        )

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
        expected_username=username,
    )
    if claim.action == "already_sent":
        return True, None
    if claim.action == "unchanged":
        return True, None
    if claim.action != "proceed" or not claim.attempt_id or not claim.claim_token:
        return False, claim.reason or "send_blocked"

    approved = claim.approved_payload
    if not approved:
        return False, "durable_approval_required"
    user_id, username, reply_text = approved["user_id"], approved["username"], approved["content"]
    api = None
    error: str | None = None
    success = False
    send_started = False
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
            send_started = True
            await api.send_message(user_id, reply_text)
            success = True
            try:
                await api.mark_read(username)
            except Exception as exc:
                logger.warning("Kwork mark_read failed", username=username, error=str(exc))
    except KworkApiError as exc:
        error = str(exc)
        logger.error("Kwork inbox reply failed", username=username, error=error)
    except Exception as exc:
        error = str(exc)
        logger.error("Kwork inbox reply unexpected error", username=username, error=error)
    finally:
        if api is not None:
            try:
                await api.close()
            except Exception as exc:
                logger.warning("Kwork inbox API close failed", error=str(exc))

    finalized = await finalize_send_attempt(
        claim.attempt_id,
        claim.claim_token,
        success=success,
        error=error,
        platform_response={"username": username, "user_id": user_id, "dry_run": settings.platform_dry_run},
        ambiguous=send_started and not success,
    )
    if not finalized:
        return False, "Устаревший claim отправки"

    if success:
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


async def _inbound_or_legacy_recorded(session, msg: ClientInboxMessage, key: str) -> bool:
    if await _inbound_already_recorded(session, "kwork", key):
        return True
    if not (msg.raw.get("MID") or msg.raw.get("id") or msg.raw.get("message_id")):
        return await _legacy_inbound_exists(session, msg.user_id, msg.timestamp, msg.text)
    return False


async def _seed_inbound_message(msg: ClientInboxMessage) -> None:
    message_key = _message_key(msg.user_id, msg.raw)
    async with AsyncSessionLocal() as session:
        if await _inbound_or_legacy_recorded(session, msg, message_key):
            return
        statement = insert(InboundMessage).values(
            platform="kwork",
            message_key=message_key,
            thread_key=str(msg.user_id),
            kwork_user_id=msg.user_id,
            kwork_username=msg.username,
            body=msg.text,
            raw_metadata={"seeded": True, "timestamp": msg.timestamp},
            status="seeded",
        )
        await session.execute(statement.on_conflict_do_nothing(
            constraint="uq_inbound_platform_message_key"
        ))
        await session.commit()


def _inbox_match_revision(match) -> tuple:
    job = match.job if not match.needs_context else None
    proposal = match.proposal if not match.needs_context else None
    return (
        match.needs_context,
        tuple(sorted(str(candidate.id) for candidate in match.candidate_jobs)),
        (job.id, job.platform, job.external_id, job.title, job.description, job.status, job.client_id,
         job.url, job.deadline, job.budget_min, job.budget_max, job.budget_currency,
         tuple(job.skills or ()))
        if job else None,
        (proposal.id, proposal.updated_at, proposal.version, proposal.content, proposal.status)
        if proposal else None,
    )


async def _eager_capture_platform_messages(
    dialog: dict[str, Any],
    client_messages: list[dict[str, Any]],
    *,
    user_id: int,
    username: str,
    thread: list[dict[str, Any]],
) -> None:
    """Commit rows with platform message IDs immediately after get_messages (C027)."""
    for message in client_messages:
        if not (message.get("MID") or message.get("id") or message.get("message_id")):
            continue
        try:
            _message_key(user_id, message)
        except ValueError:
            continue
        text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))
        if not text.strip():
            continue
        platform_id = message.get("MID") or message.get("id") or message.get("message_id")
        await _capture_inbound(
            ClientInboxMessage(
                user_id=int(user_id),
                username=username,
                message_id=str(platform_id),
                text=text,
                timestamp=int(message.get("time") or message.get("sent_timestamp") or 0),
                dialog=dialog,
                raw=message,
                thread=list(thread),
            )
        )


async def _capture_inbound(msg: ClientInboxMessage) -> None:
    """Commit the incoming message before any generation or external work."""
    message_key = _message_key(msg.user_id, msg.raw)
    project_id = _extract_project_id(msg.raw, msg.dialog)
    platform_id = msg.raw.get("MID") or msg.raw.get("id") or msg.raw.get("message_id")
    async with AsyncSessionLocal() as session:
        if (not platform_id
                and await _legacy_inbound_exists(session, msg.user_id, msg.timestamp, msg.text)):
            return
        statement = insert(InboundMessage).values(
            platform="kwork", message_key=message_key, thread_key=str(msg.user_id),
            kwork_user_id=msg.user_id, kwork_username=msg.username, body=msg.text,
            raw_metadata={"timestamp": msg.timestamp, "project_id": project_id,
                          "platform_message_id": str(platform_id) if platform_id else None},
            status="received",
        ).on_conflict_do_nothing(constraint="uq_inbound_platform_message_key")
        await session.execute(statement)
        await session.commit()


INBOUND_GENERATION_LEASE_SECONDS = 900
INBOUND_GENERATION_MAX_ATTEMPTS = 5


async def _enqueue_generation_exhausted_operator_alert(
    session,
    inbound: InboundMessage,
    *,
    target_chat: int,
) -> None:
    metadata = dict(inbound.raw_metadata or {})
    if metadata.get("generation_exhausted_notified_at"):
        return
    attempts = int(metadata.get("generation_attempts") or INBOUND_GENERATION_MAX_ATTEMPTS)
    user_line = html.escape(inbound.kwork_username or "")
    body_preview = html.escape((inbound.body or "")[:200])
    alert_text = (
        "⚠️ Kwork inbound: исчерпаны попытки генерации ответа.\n"
        f"Пользователь: {inbound.kwork_user_id} ({user_line})\n"
        f"message_key: {html.escape(inbound.message_key)}\n"
        f"inbound_id: {inbound.id}\n"
        f"попыток: {attempts}\n"
        f"Текст (начало): {body_preview}\n"
        f"Requeue: ops_inbound_recovery.py --id {inbound.id} --apply"
    )
    await enqueue_notification(
        kind="ops_alert",
        idempotency_key=f"inbound_generation_exhausted:{inbound.id}",
        chat_id=target_chat,
        payload={"alert_text": alert_text},
        reference_type="inbound_message",
        reference_id=inbound.id,
        session=session,
    )
    metadata["generation_exhausted_notified_at"] = datetime.now(UTC).isoformat()
    inbound.raw_metadata = metadata


async def _maybe_notify_generation_exhausted(
    session,
    inbound: InboundMessage,
    *,
    target_chat: int | None = None,
) -> None:
    if inbound.status != "needs_attention" or inbound.inbox_pending_id is not None:
        return
    chat = target_chat
    if chat is None:
        admin = get_settings().telegram_admin_chat_id
        if not admin:
            return
        chat = int(admin)
    await _enqueue_generation_exhausted_operator_alert(session, inbound, target_chat=chat)


async def _claim_inbound_generation(msg: ClientInboxMessage, project_id: str | None) -> str | None:
    """Claim a received message without holding a transaction during LLM work."""
    key = _message_key(msg.user_id, msg.raw)
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == key,
        ).with_for_update())
        if (inbound is None or inbound.status != "received"
                or inbound.kwork_user_id != msg.user_id or inbound.body != msg.text
                or inbound.kwork_username != msg.username
                or (inbound.raw_metadata or {}).get("project_id") != project_id):
            return None
        now = datetime.now(UTC)
        metadata = dict(inbound.raw_metadata or {})
        if metadata.get("generation_retry_after", "") > now.isoformat():
            return None
        if metadata.get("generation_claim_until", "") > now.isoformat():
            return None
        attempts = int(metadata.get("generation_attempts") or 0)
        if attempts >= INBOUND_GENERATION_MAX_ATTEMPTS:
            inbound.status = "needs_attention"
            metadata.pop("generation_claim_token", None)
            metadata.pop("generation_claim_until", None)
            inbound.raw_metadata = metadata
            await _maybe_notify_generation_exhausted(session, inbound)
            await session.commit()
            return None
        token = str(uuid.uuid4())
        metadata["generation_attempts"] = attempts + 1
        metadata["generation_claim_token"] = token
        metadata["generation_claim_until"] = (
            now + timedelta(seconds=INBOUND_GENERATION_LEASE_SECONDS)
        ).isoformat()
        metadata.pop("generation_retry_after", None)
        inbound.raw_metadata = metadata
        await session.commit()
        return token


def _claim_is_current(inbound: InboundMessage, token: str) -> bool:
    metadata = inbound.raw_metadata or {}
    return (inbound.status == "received" and metadata.get("generation_claim_token") == token
            and metadata.get("generation_claim_until", "") > datetime.now(UTC).isoformat())


async def _renew_inbound_claim(message_key: str, token: str) -> bool:
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == message_key,
        ).with_for_update())
        if inbound is None or not _claim_is_current(inbound, token):
            return False
        metadata = dict(inbound.raw_metadata or {})
        metadata["generation_claim_until"] = (
            datetime.now(UTC) + timedelta(seconds=INBOUND_GENERATION_LEASE_SECONDS)
        ).isoformat()
        inbound.raw_metadata = metadata
        await session.commit()
        return True


async def _inbound_claim_heartbeat(message_key: str, token: str) -> None:
    while True:
        await asyncio.sleep(max(0.05, INBOUND_GENERATION_LEASE_SECONDS / 3))
        if not await _renew_inbound_claim(message_key, token):
            raise RuntimeError("inbound generation claim lost")


async def _generate_with_inbound_claim(message_key: str, token: str, **kwargs) -> tuple[str, str]:
    generation = asyncio.create_task(_generate_inbox_reply(**kwargs))
    heartbeat = asyncio.create_task(_inbound_claim_heartbeat(message_key, token))
    try:
        done, _ = await asyncio.wait((generation, heartbeat), return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            # A failed renewal may mean another worker owns the message; discard this result.
            await heartbeat
            raise RuntimeError("inbound generation heartbeat stopped")
        return await generation
    finally:
        for task in (generation, heartbeat):
            if not task.done():
                task.cancel()
        await asyncio.gather(generation, heartbeat, return_exceptions=True)


def _release_inbound_claim(inbound: InboundMessage, *, failed: bool) -> None:
    metadata = dict(inbound.raw_metadata or {})
    metadata.pop("generation_claim_token", None)
    metadata.pop("generation_claim_until", None)
    attempts = int(metadata.get("generation_attempts") or 0)
    if attempts >= INBOUND_GENERATION_MAX_ATTEMPTS:
        inbound.status = "needs_attention"
    elif failed:
        delay = min(3600, 60 * (2 ** min(max(0, attempts - 1), 6)))
        metadata["generation_retry_after"] = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
    inbound.raw_metadata = metadata


async def _defer_inbound_retry(message_key: str, token: str, *, target_chat: int | None = None) -> None:
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == message_key,
        ).with_for_update())
        if inbound is None or not _claim_is_current(inbound, token):
            return
        _release_inbound_claim(inbound, failed=True)
        await _maybe_notify_generation_exhausted(session, inbound, target_chat=target_chat)
        await session.commit()


async def requeue_inbound_for_review(session, inbound_id: uuid.UUID) -> bool:
    """Requeue a failed inbound explicitly; never alter already-created reviews."""
    inbound = await session.get(InboundMessage, inbound_id, with_for_update=True)
    if (inbound is None or inbound.platform != "kwork" or inbound.status != "needs_attention"
            or inbound.interaction_id is not None or inbound.inbox_pending_id is not None):
        return False
    metadata = dict(inbound.raw_metadata or {})
    for key in ("generation_attempts", "generation_claim_token", "generation_claim_until",
                "generation_retry_after"):
        metadata.pop(key, None)
    metadata["operator_requeued_at"] = datetime.now(UTC).isoformat()
    inbound.raw_metadata = metadata
    inbound.status = "received"
    await session.flush()
    return True


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

    project_id = _extract_project_id(msg.raw, msg.dialog)
    await _capture_inbound(msg)

    claim_token = await _claim_inbound_generation(msg, project_id)
    if claim_token is None:
        return None
    async with AsyncSessionLocal() as session:
        match = await find_kwork_job_for_message(
            session,
            username=msg.username,
            project_id=project_id,
        )
        needs_context = match.needs_context
        job = match.job if not needs_context else None
        proposal = match.proposal if not needs_context else None
        revision = _inbox_match_revision(match)

    chat_intent = ""
    chat_reply = ""
    if job:
        try:
            if not await _renew_inbound_claim(message_key, claim_token):
                logger.warning("Inbox claim expired before generation", user_id=msg.user_id)
                return None
            chat_intent, chat_reply = await _generate_with_inbound_claim(
                message_key, claim_token,
                job=job,
                proposal=proposal,
                client_message=msg.text,
                username=msg.username,
                thread_messages=msg.thread,
                my_username=my_username,
                my_user_id=my_user_id,
            )
        except Exception as exc:
            logger.warning("ChatAgent failed for inbox message", error=str(exc))
            await _defer_inbound_retry(message_key, claim_token, target_chat=target_chat)
            return None

    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == message_key,
        ).with_for_update())
        if inbound is None or not _claim_is_current(inbound, claim_token):
            return None
        match = await find_kwork_job_for_message(
            session, username=msg.username, project_id=project_id,
        )
        needs_context = match.needs_context
        job = match.job if not needs_context else None
        proposal = match.proposal if not needs_context else None
        if job:
            await session.refresh(job, with_for_update=True)
        if proposal:
            await session.refresh(proposal, with_for_update=True)
        if _inbox_match_revision(match) != revision:
            logger.warning("Inbox context changed during generation", user_id=msg.user_id)
            _release_inbound_claim(inbound, failed=True)
            await session.commit()
            return None
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
                "project_id": project_id,
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

        await capture_inbox_presentation(session, pending)
        inbound.interaction_id = interaction.id
        inbound.inbox_pending_id = pending.id
        _release_inbound_claim(inbound, failed=False)
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
            payload={
                "pending_id": pending_id,
                "alert_text": alert_text,
                "review_format": "full_text_v1",
                "review_content": pending.draft_reply,
                "review_heading": f"Ответ клиенту {pending.kwork_username[:150]} (user {pending.kwork_user_id}).\nЗаказ: {pending.job_id or 'не определён'}\n{job.title[:300] if job else ''}\nВходящее сообщение:\n{pending.client_message}",
            },
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
    capture_mode: str | None = None,
) -> list[ClientInboxMessage]:
    if capture_mode not in (None, "received", "seeded"):
        raise ValueError("invalid inbox capture mode")
    dialogs = await api.get_dialogs()
    unread = [d for d in dialogs if int(d.get("unread_count") or 0) > 0 or d.get("unread")]
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


        client_messages = [m for m in messages or [] if _is_from_client(m, my_username, my_user_id)]
        if capture_mode == "received":
            await _eager_capture_platform_messages(
                dialog,
                client_messages,
                user_id=int(user_id),
                username=username,
                thread=list(messages or []),
            )
        fallback_keys = []
        for message in client_messages:
            if not (message.get("MID") or message.get("id") or message.get("message_id")):
                try:
                    fallback_keys.append(_message_key(user_id, message))
                except ValueError:
                    pass
        duplicate_fallbacks = {key for key, count in Counter(fallback_keys).items() if count > 1}
        async with AsyncSessionLocal() as session:
            for message in client_messages:
                try:
                    key = _message_key(user_id, message)
                except ValueError:
                    state.identity_ambiguities.append({"user_id": str(user_id), "reason": "missing_timestamp_or_text"})
                    continue
                if key in duplicate_fallbacks:
                    timestamp = int(message.get("time") or message.get("sent_timestamp"))
                    state.identity_ambiguities.append({"user_id": str(user_id), "timestamp": timestamp, "reason": "identical_messages_without_id"})
                    continue
                if state.is_seen(key):
                    continue
                if await _inbound_already_recorded(session, "kwork", key):
                    state.mark_seen(key)
                    continue

                text = clean_kwork_message_text(
                    str(message.get("message", message.get("text", "")))
                )
                no_id = not (message.get("MID") or message.get("id") or message.get("message_id"))
                if no_id:
                    timestamp = int(message.get("time") or message.get("sent_timestamp"))
                    if await _legacy_inbound_exists(session, int(user_id), timestamp, text):
                        state.mark_seen(key)
                        continue
                    if _legacy_fallback_keys(state, user_id, timestamp):
                        state.identity_ambiguities.append({"user_id": str(user_id), "timestamp": timestamp, "reason": "legacy_state_unmatched"})
                        continue
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
                if capture_mode == "received":
                    await _capture_inbound(collected[-1])
                elif capture_mode == "seeded":
                    await _seed_inbound_message(collected[-1])

        # Per-message capture above shrinks the window after get_messages; the next
        # dialog fetch cannot discard messages already parsed from this thread.

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
        await _capture_inbound(msg)
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


async def _retry_received_inbound(
    settings: Settings, *, bot=None, my_username: str = "", my_user_id: int | None = None,
) -> int:
    """Retry captured messages after errors or process restarts, even if no longer unread."""
    if not settings.telegram_admin_chat_id:
        return 0
    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.status == "received",
            or_(InboundMessage.raw_metadata["generation_retry_after"].astext.is_(None),
                InboundMessage.raw_metadata["generation_retry_after"].astext <= datetime.now(UTC).isoformat()),
            or_(InboundMessage.raw_metadata["generation_claim_until"].astext.is_(None),
                InboundMessage.raw_metadata["generation_claim_until"].astext <= datetime.now(UTC).isoformat()),
        ).order_by(InboundMessage.received_at).limit(50))).all()
        messages = []
        for row in rows:
            metadata = row.raw_metadata or {}
            timestamp = int(metadata.get("timestamp") or 0)
            suffix = row.message_key.split(":", 1)[-1]
            raw = {"time": timestamp, "message": row.body}
            platform_id = metadata.get("platform_message_id")
            if platform_id is not None:
                raw["MID"] = platform_id
            elif not suffix.startswith("v2:"):
                raw["MID"] = suffix
            messages.append(ClientInboxMessage(
                user_id=row.kwork_user_id, username=row.kwork_username or "",
                message_id=suffix, text=row.body, timestamp=timestamp,
                dialog={"project_id": metadata.get("project_id")}, raw=raw,
            ))
    return await _process_and_notify(
        settings, messages, bot=bot, my_username=my_username, my_user_id=my_user_id,
    )


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
                message_id=str(
                    message.get("MID") or message.get("id") or message.get("message_id") or ""
                ),
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


def _inbox_draft_revision(row: TelegramInboxPending) -> tuple:
    return (
        row.status,
        row.draft_reply,
        row.chat_intent,
        row.chat_id,
        row.message_id,
        row.job_id,
        row.proposal_id,
        row.kwork_user_id,
        row.kwork_username,
        row.client_message,
        row.interaction_id,
    )


async def _refresh_pending_inbox_draft(
    pending_id: uuid.UUID,
    settings: Settings,
    *,
    my_username: str,
    my_user_id: int | None,
) -> dict[str, Any] | None:
    """Generate outside the TX, then supersede the old approval atomically."""
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pending_id)
        if not row or row.status != "pending":
            return None
        if await delivery_blocks_mutation(session, inbox_pending_id=row.id, include_confirmed=True):
            return None
        revision = _inbox_draft_revision(row)
        job = await session.get(Job, row.job_id) if row.job_id else None
        proposal = await session.get(Proposal, row.proposal_id) if row.proposal_id else None
        if not job:
            # A username alone cannot reconstruct or override the original thread context.
            return None

    # Detached input: no database transaction spans the thread fetch or generation.
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
        logger.warning("Inbox draft refresh failed", pending_id=str(pending_id), error=str(exc))
        return None
    if not chat_reply.strip():
        return None

    async with AsyncSessionLocal() as session:
        current = await session.scalar(
            select(TelegramInboxPending)
            .where(TelegramInboxPending.id == pending_id)
            .with_for_update()
        )
        if not current or _inbox_draft_revision(current) != revision:
            return None
        if await delivery_blocks_mutation(
            session, inbox_pending_id=pending_id, include_confirmed=True
        ):
            return None
        replacement = TelegramInboxPending(
            interaction_id=current.interaction_id,
            job_id=job.id,
            proposal_id=proposal.id if proposal else None,
            kwork_user_id=current.kwork_user_id,
            kwork_username=current.kwork_username,
            client_message=current.client_message,
            draft_reply=chat_reply,
            chat_intent=chat_intent or None,
            chat_id=int(settings.telegram_admin_chat_id),
            status="pending",
        )
        current.status = "superseded"
        session.add(replacement)
        await session.flush()
        await capture_inbox_presentation(session, replacement)
        msg = ClientInboxMessage(
            user_id=current.kwork_user_id,
            username=current.kwork_username,
            message_id="",
            text=current.client_message,
            timestamp=0,
            dialog={},
            raw={},
        )
        alert = format_client_message_alert(
            msg, job=job, chat_intent=chat_intent, chat_reply=chat_reply
        )
        await enqueue_notification(
            kind="inbox_alert",
            idempotency_key=f"inbox_alert:{replacement.id}",
            chat_id=replacement.chat_id,
            payload={
                "pending_id": str(replacement.id),
                "alert_text": alert,
                "review_format": "full_text_v1",
                "review_content": replacement.draft_reply,
                "review_heading": f"Ответ клиенту {replacement.kwork_username[:150]} (user {replacement.kwork_user_id}).\nЗаказ: {replacement.job_id}\n{job.title[:300]}\nВходящее сообщение:\n{replacement.client_message}",
            },
            reference_type="telegram_inbox_pending",
            reference_id=replacement.id,
            session=session,
        )
        await session.commit()
        return {
            "pending_id": str(replacement.id),
            "superseded_pending_id": str(pending_id),
            "username": replacement.kwork_username,
            "draft_preview": chat_reply[:120],
        }


async def _refresh_inbox_notifications(settings, *, bot=None, limit=0, usernames=None):
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        return {"skipped": True, "reason": "no_telegram"}
    my_username, my_user_id = await _get_kwork_actor(settings)
    async with AsyncSessionLocal() as session:
        query = (
            select(TelegramInboxPending.id)
            .where(TelegramInboxPending.status == "pending")
            .order_by(TelegramInboxPending.created_at.desc())
        )
        if usernames:
            query = query.where(TelegramInboxPending.kwork_username.in_(usernames))
        if limit > 0:
            query = query.limit(limit)
        ids = list((await session.scalars(query)).all())
    items = []
    for pending_id in ids:
        item = await _refresh_pending_inbox_draft(
            pending_id, settings, my_username=my_username, my_user_id=my_user_id
        )
        if item:
            items.append(item)
    delivery = (
        await deliver_notification_outbox(settings, bot=bot, limit=max(len(items), 1))
        if items
        else {"delivered": 0}
    )
    # The global outbox batch can deliver older events; report only our new cards.
    sent_count = 0
    if items:
        async with AsyncSessionLocal() as session:
            delivered_ids = set(
                (
                    await session.scalars(
                        select(NotificationOutbox.reference_id).where(
                            NotificationOutbox.reference_type == "telegram_inbox_pending",
                            NotificationOutbox.reference_id.in_(
                                [uuid.UUID(item["pending_id"]) for item in items]
                            ),
                            NotificationOutbox.status == "sent",
                        )
                    )
                ).all()
            )
            sent_count = len(delivered_ids)
    return {
        "updated": len(items),
        "queued": len(items),
        "sent": sent_count,
        "items": items,
        "delivery": delivery,
    }


async def regenerate_pending_inbox_drafts(
    settings: Settings | None = None,
    *,
    bot=None,
    pending_only: bool = True,
) -> dict[str, Any]:
    """Replace only pending drafts; pending_only=False never reopens processed rows."""
    return await _refresh_inbox_notifications(settings or get_settings(), bot=bot)


async def resend_inbox_pending_notifications(
    settings: Settings | None = None,
    *,
    limit: int = 2,
    usernames: list[str] | None = None,
    bot=None,
) -> dict[str, Any]:
    """Queue fresh approval cards without changing text behind old buttons."""
    return await _refresh_inbox_notifications(
        settings or get_settings(), bot=bot, limit=limit, usernames=usernames
    )


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
        await _retry_received_inbound(
            settings, bot=bot, my_username=my_username,
            my_user_id=int(my_user_id) if my_user_id else None,
        )

        new_messages = await _collect_new_messages(
            api,
            state,
            my_username=my_username,
            my_user_id=int(my_user_id) if my_user_id else None,
            capture_mode="seeded" if first_run else "received",
        )
        if state.identity_ambiguities:
            logger.warning("Kwork inbox identity requires manual review", count=len(state.identity_ambiguities))

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
                "identity_ambiguities": state.identity_ambiguities,
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
            "identity_ambiguities": state.identity_ambiguities,
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
