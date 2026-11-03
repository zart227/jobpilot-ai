"""Telegram outbox: ambiguous delivery must not cause blind resend or duplicate client sends."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services.notification_outbox import (
    _mark_delivery_failure,
    _mark_delivery_success_commit_failure,
    deliver_notification_outbox,
    recover_stale_outbox_claims,
)
from app.utils.delivery_errors import is_ambiguous_send_error

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


def test_ambiguous_telegram_errors_match_send_policy() -> None:
    assert is_ambiguous_send_error("Telegram network timeout") is True
    assert is_ambiguous_send_error("invalid token") is False


@pytest.mark.asyncio
async def test_timeout_marks_needs_attention_not_pending_retry() -> None:
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal

    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        NOTIFICATION_OUTBOX_MAX_ATTEMPTS=5,
        NOTIFICATION_OUTBOX_BASE_BACKOFF_SECONDS=30,
    )
    outbox_id = uuid.uuid4()
    token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="proposal_approval",
                idempotency_key=f"test:timeout:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "hi", "pending_id": str(uuid.uuid4())},
                reference_type="telegram_pending",
                reference_id=uuid.uuid4(),
                status="sending",
                claim_token=token,
            )
        )
        await session.commit()

    await _mark_delivery_failure(outbox_id, "Connection timeout to api.telegram.org", settings, token)

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None
        assert row.status == "needs_attention"
        assert row.next_attempt_at is None

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stale_claim_recovery_does_not_auto_resend() -> None:
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal

    get_settings.cache_clear()
    settings = Settings(_env_file=None, NOTIFICATION_OUTBOX_LEASE_SECONDS=60)
    outbox_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="inbox_alert",
                idempotency_key=f"test:stale:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "alert"},
                reference_type="telegram_inbox_pending",
                reference_id=uuid.uuid4(),
                status="sending",
                claim_expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        await session.commit()

    count = await recover_stale_outbox_claims(settings)
    assert count >= 1

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None
        assert row.status == "needs_attention"
        assert "lease_expired" in (row.last_error or "")

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_claim_batch_never_selects_needs_attention_for_delivery() -> None:
    """C028: needs_attention outbox rows must not re-enter automatic delivery."""
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services.notification_outbox import _claim_batch

    get_settings.cache_clear()
    settings = Settings(_env_file=None, NOTIFICATION_OUTBOX_LEASE_SECONDS=60)
    blocked_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=blocked_id,
                kind="proposal_approval",
                idempotency_key=f"test:na:{blocked_id}",
                chat_id=1,
                payload={"alert_text": "blocked"},
                reference_type="telegram_pending",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                last_error="timeout",
            )
        )
        session.add(
            NotificationOutbox(
                id=pending_id,
                kind="ops_alert",
                idempotency_key=f"test:pending:{pending_id}",
                chat_id=1,
                payload={"alert_text": "ok"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="pending",
            )
        )
        await session.commit()

    claimed = await _claim_batch(settings, limit=20)
    claimed_ids = {row.id for row in claimed}
    assert blocked_id not in claimed_ids

    async with AsyncSessionLocal() as session:
        blocked_row = await session.get(NotificationOutbox, blocked_id)
        assert blocked_row.status == "needs_attention"
        pending_row = await session.get(NotificationOutbox, pending_id)
        assert pending_row.status in ("pending", "sending")

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_commit_failure_after_telegram_send_records_message_id() -> None:
    from app.db.models import NotificationOutbox, TelegramPending
    from app.db.session import AsyncSessionLocal

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    external_id = f"kwork-outbox-{job_id.hex[:12]}"
    token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        from app.db.models import Job, Proposal

        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="T",
                description="d",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="c",
                status="pending",
                version=1,
            )
        )
        session.add(
            TelegramPending(
                id=pending_id,
                job_id=job_id,
                proposal_id=proposal_id,
                chat_id=1,
                status="pending",
            )
        )
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="proposal_approval",
                idempotency_key=f"test:commit:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "x", "pending_id": str(pending_id)},
                reference_type="telegram_pending",
                reference_id=pending_id,
                status="sending",
                claim_token=token,
            )
        )
        await session.commit()

    await _mark_delivery_success_commit_failure(outbox_id, 999, "simulated db error", token)

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        pending = await session.get(TelegramPending, pending_id)
        assert row is not None and row.status == "needs_attention"
        assert row.telegram_message_id == 999
        assert pending is not None and pending.message_id == 999


@pytest.mark.asyncio
async def test_deliver_idempotent_outbox_does_not_double_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second delivery run must not call Telegram again once needs_attention from timeout."""
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services import notification_outbox as outbox_mod

    get_settings.cache_clear()
    monkeypatch.setattr(outbox_mod, "recover_stale_outbox_claims", AsyncMock(return_value=0))

    async def claim_nothing(_settings, limit: int = 20):
        return []

    monkeypatch.setattr(outbox_mod, "_claim_batch", claim_nothing)
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_ADMIN_CHAT_ID="1",
    )
    outbox_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="proposal_approval",
                idempotency_key=f"test:idem:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "card", "pending_id": str(uuid.uuid4())},
                reference_type="telegram_pending",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                last_error="Connection timeout",
            )
        )
        await session.commit()

    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=1))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))

    await deliver_notification_outbox(settings, bot=bot, limit=5)
    send_mock.assert_not_called()

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None and row.status == "needs_attention"

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_deliver_success_then_commit_failure_marks_needs_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services import notification_outbox as outbox_mod

    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_ADMIN_CHAT_ID="1",
    )
    outbox_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="proposal_approval",
                idempotency_key=f"test:send:{outbox_id}",
                chat_id=1,
                payload={
                    "alert_text": "card",
                    "pending_id": str(uuid.uuid4()),
                    "review_format": "full_text_v1",
                    "review_content": "Exact content",
                    "review_heading": "Review",
                },
                reference_type="telegram_pending",
                reference_id=uuid.uuid4(),
                status="pending",
            )
        )
        await session.commit()

    async def boom(*_a, **_k):
        raise SQLAlchemyError("simulated commit failure")

    monkeypatch.setattr(outbox_mod, "_mark_delivery_success", boom)

    async def claim_only_this_row(_settings, limit: int = 20):
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationOutbox, outbox_id)
            if row is None or row.status != "pending":
                return []
            row.status = "sending"
            row.claim_token = uuid.uuid4()
            row.claim_expires_at = datetime.now(UTC) + timedelta(minutes=1)
            await session.commit()
            return [row]

    monkeypatch.setattr(outbox_mod, "_claim_batch", claim_only_this_row)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=555))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))

    result = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert result["failed"] >= 1
    assert send_mock.await_count == 3

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None
        assert row.status == "needs_attention"
        assert row.telegram_message_id == 555

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ops_alert_delivers_without_review_format(monkeypatch) -> None:
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services import notification_outbox as outbox_mod

    get_settings.cache_clear()
    settings = Settings(_env_file=None, NOTIFICATION_OUTBOX_MAX_ATTEMPTS=3)
    outbox_id = uuid.uuid4()
    token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="ops_alert",
                idempotency_key=f"test:ops:{outbox_id}",
                chat_id=123,
                payload={"alert_text": "test <b>ops</b> alert"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="sending",
                claim_token=token,
                claim_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()

    async def claim_only_this_row(_settings, *, limit):
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationOutbox, outbox_id)
            return [row] if row else []

    monkeypatch.setattr(outbox_mod, "_claim_batch", claim_only_this_row)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=999))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))

    result = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert result["delivered"] >= 1
    send_mock.assert_awaited_once()
    assert send_mock.await_args.kwargs.get("parse_mode") == "HTML"

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None and row.status == "sent" and row.telegram_message_id == 999

    get_settings.cache_clear()
