"""Durable Telegram notification delivery with claims and backoff."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.db.models import InboundMessage, NotificationOutbox, TelegramInboxPending, TelegramPending
from app.db.session import AsyncSessionLocal
from app.telegram.full_review import REVIEW_FORMAT, send_full_review, split_review_text
from app.telegram.keyboards import approval_keyboard, inbox_reply_keyboard
from app.utils.delivery_errors import is_ambiguous_send_error
from app.utils.proxy import create_telegram_bot

logger = structlog.get_logger(__name__)

OutboxKind = Literal["inbox_alert", "proposal_approval", "ops_alert"]
OutboxStatus = Literal["pending", "sending", "sent", "needs_attention", "cancelled"]

CLAIM_OWNER = "notification_outbox_worker"


class OutboxClaimLost(Exception):
    """The worker can no longer safely start another Telegram request."""


class PartialNotificationDelivery(Exception):
    """At least one part was delivered before a later request failed."""


def compute_backoff_seconds(attempt: int, base: int) -> int:
    return int(min(3600, base * (2 ** max(0, attempt - 1))))


async def enqueue_notification(
    *,
    kind: OutboxKind,
    idempotency_key: str,
    chat_id: int,
    payload: dict[str, Any],
    reference_type: str,
    reference_id: uuid.UUID,
    session,
) -> NotificationOutbox:
    existing = await session.execute(
        select(NotificationOutbox).where(
            NotificationOutbox.idempotency_key == idempotency_key,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        return row

    row = NotificationOutbox(
        kind=kind,
        idempotency_key=idempotency_key,
        chat_id=chat_id,
        payload=payload,
        reference_type=reference_type,
        reference_id=reference_id,
        status="pending",
    )
    session.add(row)
    await session.flush()
    return row


async def _claim_batch(
    settings: Settings,
    *,
    limit: int,
) -> list[NotificationOutbox]:
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=settings.notification_outbox_lease_seconds)
    claim_token = uuid.uuid4()
    claimed: list[NotificationOutbox] = []

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status == "pending",
                or_(
                    NotificationOutbox.next_attempt_at.is_(None),
                    NotificationOutbox.next_attempt_at <= now,
                ),
                or_(
                    NotificationOutbox.claim_expires_at.is_(None),
                    NotificationOutbox.claim_expires_at < now,
                ),
            )
            .order_by(NotificationOutbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = "sending"
            row.claim_owner = CLAIM_OWNER
            row.claim_token = claim_token
            row.claim_expires_at = lease_until
            claimed.append(row)
        if claimed:
            await session.commit()
        else:
            await session.rollback()
    return claimed


async def _mark_delivery_success(
    outbox_id: uuid.UUID, telegram_message_id: int, claim_token: uuid.UUID
) -> bool:
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id, with_for_update=True)
        if not row or row.status != "sending" or not claim_token or row.claim_token != claim_token:
            return False
        row.status = "sent"
        row.telegram_message_id = telegram_message_id
        row.sent_at = datetime.now(UTC)
        row.last_error = None
        row.claim_owner = None
        row.claim_token = None
        row.claim_expires_at = None

        if row.reference_type == "telegram_inbox_pending":
            pending = await session.get(TelegramInboxPending, row.reference_id)
            if pending:
                pending.message_id = telegram_message_id
            inbound = await session.execute(
                select(InboundMessage).where(
                    InboundMessage.inbox_pending_id == row.reference_id,
                )
            )
            inbound_row = inbound.scalar_one_or_none()
            if inbound_row:
                inbound_row.status = "notified"
                inbound_row.notified_at = datetime.now(UTC)
        elif row.reference_type == "telegram_pending":
            pending = await session.get(TelegramPending, row.reference_id)
            if pending and pending.message_id is None:
                pending.message_id = telegram_message_id

        await session.commit()
        return True


async def _mark_delivery_failure(
    outbox_id: uuid.UUID,
    error: str,
    settings: Settings,
    claim_token: uuid.UUID,
    *,
    partial_delivery: bool = False,
) -> bool:
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id, with_for_update=True)
        if not row or row.status != "sending" or not claim_token or row.claim_token != claim_token:
            return False
        row.attempt_count += 1
        row.last_error = (
            f"partial_telegram_delivery: {error}" if partial_delivery else error
        )[:2000]
        row.claim_owner = None
        row.claim_token = None
        row.claim_expires_at = None

        if (
            partial_delivery
            or is_ambiguous_send_error(error)
            or row.attempt_count >= settings.notification_outbox_max_attempts
        ):
            row.status = "needs_attention"
            row.next_attempt_at = None
        else:
            row.status = "pending"
            delay = compute_backoff_seconds(
                row.attempt_count,
                settings.notification_outbox_base_backoff_seconds,
            )
            row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)

        await session.commit()
        return True


async def _mark_delivery_success_commit_failure(
    outbox_id: uuid.UUID,
    telegram_message_id: int,
    error: str,
    claim_token: uuid.UUID,
) -> bool:
    """Telegram may have delivered the card while DB commit failed — no auto resend."""
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id, with_for_update=True)
        if not row or row.status != "sending" or not claim_token or row.claim_token != claim_token:
            return False
        row.status = "needs_attention"
        row.telegram_message_id = telegram_message_id
        row.last_error = f"commit_failed_after_telegram_send: {error}"[:2000]
        row.claim_owner = None
        row.claim_token = None
        row.claim_expires_at = None
        row.next_attempt_at = None
        if row.reference_type == "telegram_pending":
            pending = await session.get(TelegramPending, row.reference_id)
            if pending and pending.message_id is None:
                pending.message_id = telegram_message_id
        elif row.reference_type == "telegram_inbox_pending":
            pending = await session.get(TelegramInboxPending, row.reference_id)
            if pending and pending.message_id is None:
                pending.message_id = telegram_message_id
        await session.commit()
        return True


async def _claim_is_active(outbox_id: uuid.UUID, claim_token: uuid.UUID) -> bool:
    if not claim_token:
        return False
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        return bool(
            row and row.status == "sending" and row.claim_token == claim_token
            and row.claim_expires_at and row.claim_expires_at > datetime.now(UTC)
        )


async def _send_outbox_row(bot, row: NotificationOutbox) -> bool:
    payload = row.payload or {}
    text = str(payload.get("alert_text") or "")
    if not text:
        raise ValueError("outbox payload missing alert_text")

    if row.kind == "ops_alert":
        if not await _claim_is_active(row.id, row.claim_token):
            raise OutboxClaimLost("notification claim lost before Telegram request")
        message = await bot.send_message(
            row.chat_id, text, parse_mode="HTML", disable_web_page_preview=True
        )
        try:
            finalized = await _mark_delivery_success(row.id, message.message_id, row.claim_token)
            if not finalized:
                logger.warning(
                    "Ops alert sent after claim was lost; manual review required",
                    outbox_id=str(row.id),
                    telegram_message_id=message.message_id,
                )
            return finalized
        except SQLAlchemyError as exc:
            await _mark_delivery_success_commit_failure(
                row.id, message.message_id, str(exc), row.claim_token
            )
            raise

    pending_id = str(payload.get("pending_id") or row.reference_id)
    if row.kind == "inbox_alert":
        markup = inbox_reply_keyboard(pending_id)
    elif row.kind == "proposal_approval":
        markup = approval_keyboard(pending_id)
    else:
        raise ValueError(f"unknown outbox kind: {row.kind}")

    if payload.get("review_format") != REVIEW_FORMAT:
        raise ValueError("Legacy approval notification requires a fresh full-text review")

    sent_parts = 0

    async def send(text, **kwargs):
        nonlocal sent_parts
        if not await _claim_is_active(row.id, row.claim_token):
            raise OutboxClaimLost("notification claim lost before Telegram request")
        try:
            result = await bot.send_message(
                row.chat_id, text, disable_web_page_preview=True, **kwargs
            )
        except Exception as exc:
            if sent_parts:
                raise PartialNotificationDelivery(str(exc)) from exc
            raise
        sent_parts += 1
        return result

    content = payload.get("review_content")
    if not isinstance(content, str):
        raise TypeError("Missing exact review text")
    if not content.strip() and row.kind == "inbox_alert":
        rows = [
            [
                button
                for button in buttons
                if not (button.callback_data or "").startswith("inbox_approve:")
            ]
            for buttons in markup.inline_keyboard
        ]
        markup = markup.model_copy(
            update={"inline_keyboard": [buttons for buttons in rows if buttons]}
        )
        parts = split_review_text(
            str(payload.get("review_heading") or "Ответ клиенту")
            + "\n\nЧерновик отсутствует. Сначала нажмите EDIT."
        )
        for part in parts[:-1]:
            await send(part, parse_mode=None, reply_markup=None)
        message = await send(parts[-1], parse_mode=None, reply_markup=markup)
    else:
        message = await send_full_review(
            send,
            content=content,
            reference=pending_id,
            heading=str(payload.get("review_heading") or "Проверка исходящего сообщения"),
            markup=markup,
        )
    try:
        finalized = await _mark_delivery_success(row.id, message.message_id, row.claim_token)
        if not finalized:
            logger.warning(
                "Notification sent after claim was lost; manual review required",
                outbox_id=str(row.id), telegram_message_id=message.message_id,
            )
        return finalized
    except SQLAlchemyError as exc:
        await _mark_delivery_success_commit_failure(
            row.id, message.message_id, str(exc), row.claim_token
        )
        raise


async def deliver_notification_outbox(
    settings: Settings | None = None,
    *,
    bot=None,
    limit: int = 20,
) -> dict[str, Any]:
    settings = settings or get_settings()
    await recover_stale_outbox_claims(settings)
    owns_bot = bot is None
    if owns_bot:
        if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
            return {"delivered": 0, "skipped": True, "reason": "no_telegram"}
        bot = create_telegram_bot(settings.telegram_bot_token, settings)

    delivered = 0
    failed = 0
    try:
        rows = await _claim_batch(settings, limit=limit)
        for row in rows:
            try:
                if await _send_outbox_row(bot, row):
                    delivered += 1
                else:
                    failed += 1
            except SQLAlchemyError as exc:
                failed += 1
                logger.warning(
                    "Notification outbox database operation failed; delivery may be unknown",
                    outbox_id=str(row.id),
                    kind=row.kind,
                    error=str(exc),
                )
            except OutboxClaimLost as exc:
                failed += 1
                await recover_stale_outbox_claims(settings)
                logger.warning("Notification outbox claim lost", outbox_id=str(row.id), error=str(exc))
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Notification outbox delivery failed",
                    outbox_id=str(row.id),
                    kind=row.kind,
                    error=str(exc),
                )
                await _mark_delivery_failure(
                    row.id, str(exc), settings, row.claim_token,
                    partial_delivery=isinstance(exc, PartialNotificationDelivery),
                )
    finally:
        if owns_bot and bot is not None:
            await bot.session.close()

    return {"delivered": delivered, "failed": failed, "claimed": delivered + failed}


async def recover_stale_outbox_claims(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.status == "sending",
                NotificationOutbox.claim_expires_at.is_not(None),
                NotificationOutbox.claim_expires_at < now,
            )
            .values(
                status="needs_attention",
                last_error="lease_expired_possible_telegram_delivery",
                claim_owner=None,
                claim_token=None,
                claim_expires_at=None,
                next_attempt_at=None,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


OUTBOX_OPERATOR_REQUEUE_BLOCKED_PREFIXES = (
    "commit_failed_after_telegram_send:",
    "partial_telegram_delivery:",
)

OUTBOX_AMBIGUOUS_LEASE_ERROR = "lease_expired_possible_telegram_delivery"


async def requeue_outbox_for_delivery(
    session,
    outbox_id: uuid.UUID,
    *,
    actor: str = "operator",
    acknowledge_ambiguous_lease: bool = False,
) -> bool:
    """Explicitly return one safe needs_attention row to pending for the delivery worker."""
    row = await session.get(NotificationOutbox, outbox_id, with_for_update=True)
    if not row or row.status != "needs_attention":
        return False
    if row.telegram_message_id is not None:
        return False
    last_error = row.last_error or ""
    if any(last_error.startswith(prefix) for prefix in OUTBOX_OPERATOR_REQUEUE_BLOCKED_PREFIXES):
        return False
    if last_error == OUTBOX_AMBIGUOUS_LEASE_ERROR and not acknowledge_ambiguous_lease:
        return False
    if row.kind in ("proposal_approval", "inbox_alert"):
        payload = row.payload or {}
        if payload.get("review_format") != REVIEW_FORMAT:
            return False
    if row.reference_type == "telegram_pending":
        pending = await session.get(TelegramPending, row.reference_id)
        if pending and pending.message_id is not None:
            return False
    elif row.reference_type == "telegram_inbox_pending":
        pending = await session.get(TelegramInboxPending, row.reference_id)
        if pending and pending.message_id is not None:
            return False

    payload = dict(row.payload or {})
    payload["operator_outbox_requeue"] = {
        "at": datetime.now(UTC).isoformat(),
        "actor": actor,
        "previous_last_error": last_error[:500],
        "previous_attempt_count": row.attempt_count,
    }
    row.payload = payload
    row.status = "pending"
    row.last_error = None
    row.next_attempt_at = None
    row.claim_owner = None
    row.claim_token = None
    row.claim_expires_at = None
    row.attempt_count = 0
    await session.flush()
    return True
