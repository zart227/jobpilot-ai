"""Concurrency and stale-worker checks using an isolated PostgreSQL test database."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.db.models import NotificationOutbox
from app.db.session import AsyncSessionLocal
from app.services.notification_outbox import (
    OutboxClaimLost,
    _claim_batch,
    _mark_delivery_failure,
    _mark_delivery_success,
    _mark_delivery_success_commit_failure,
    _send_outbox_row,
    deliver_notification_outbox,
    recover_stale_outbox_claims,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated database required"
)


@pytest.fixture
async def outbox_row():
    row_id = uuid.uuid4()
    token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(NotificationOutbox(
            id=row_id, kind="proposal_approval", idempotency_key=f"fencing:{row_id}",
            chat_id=1, payload={"alert_text": "synthetic"}, reference_type="telegram_pending",
            reference_id=uuid.uuid4(), status="sending", claim_token=token,
            claim_owner="worker", claim_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ))
        await session.commit()
    yield row_id, token
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        if row:
            await session.delete(row)
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["success", "failure", "commit_failure"])
async def test_stale_token_never_finalizes_another_claim(outbox_row, finish):
    row_id, current_token = outbox_row
    stale_token = uuid.uuid4()
    settings = Settings(_env_file=None)
    if finish == "success":
        await _mark_delivery_success(row_id, 777, stale_token)
    elif finish == "failure":
        await _mark_delivery_failure(row_id, "network timeout", settings, stale_token)
    else:
        await _mark_delivery_success_commit_failure(row_id, 777, "db error", stale_token)
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        assert row.status == "sending"
        assert row.claim_token == current_token
        assert row.telegram_message_id is None
        assert row.attempt_count == 0


@pytest.mark.asyncio
async def test_expired_sending_claim_cannot_be_reclaimed(outbox_row):
    row_id, token = outbox_row
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        row.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    settings = Settings(_env_file=None)
    claimed = await _claim_batch(settings, limit=100)
    assert row_id not in {row.id for row in claimed}
    await recover_stale_outbox_claims(settings)
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        assert row.status == "needs_attention"
        assert row.claim_token is None
    await _mark_delivery_success(row_id, 777, token)
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        assert row.status == "needs_attention"
        assert row.telegram_message_id is None


@pytest.mark.asyncio
async def test_concurrent_claims_only_one_worker_gets_pending_row(outbox_row):
    row_id, _ = outbox_row
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        row.status = "pending"
        row.claim_token = None
        row.claim_expires_at = None
        await session.commit()
    settings = Settings(_env_file=None)
    batches = await asyncio.gather(*(_claim_batch(settings, limit=100) for _ in range(8)))
    matching = [row for batch in batches for row in batch if row.id == row_id]
    assert len(matching) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["success", "failure", "commit_failure"])
async def test_current_token_can_finalize(outbox_row, finish):
    row_id, token = outbox_row
    settings = Settings(_env_file=None)
    if finish == "success":
        assert await _mark_delivery_success(row_id, 777, token)
        expected = "sent"
    elif finish == "failure":
        assert await _mark_delivery_failure(row_id, "network timeout", settings, token)
        expected = "needs_attention"
    else:
        assert await _mark_delivery_success_commit_failure(row_id, 777, "db error", token)
        expected = "needs_attention"
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        assert row.status == expected
        assert row.claim_token is None
        assert row.telegram_message_id == (None if finish == "failure" else 777)


@pytest.mark.asyncio
async def test_finalizer_and_expiry_recovery_cannot_overwrite_each_other(outbox_row):
    row_id, token = outbox_row
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        row.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    finalized, _ = await asyncio.gather(
        _mark_delivery_success(row_id, 777, token), recover_stale_outbox_claims()
    )
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        if finalized:
            assert row.status == "sent" and row.telegram_message_id == 777
        else:
            assert row.status == "needs_attention" and row.telegram_message_id is None
        assert row.claim_token is None


@pytest.mark.asyncio
async def test_late_success_is_not_counted_or_saved_after_recovery(outbox_row):
    row_id, _ = outbox_row
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        row.payload = {
            "alert_text": "synthetic", "review_format": "full_text_v1",
            "review_content": "synthetic content", "review_heading": "review",
        }
        await session.commit()

    calls = 0

    async def send(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            async with AsyncSessionLocal() as session:
                row = await session.get(NotificationOutbox, row_id)
                row.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
            await recover_stale_outbox_claims()
        return SimpleNamespace(message_id=777)

    async with AsyncSessionLocal() as session:
        detached_row = await session.get(NotificationOutbox, row_id)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=send))
    assert not await _send_outbox_row(bot, detached_row)
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        assert row.status == "needs_attention"
        assert row.telegram_message_id is None


@pytest.mark.asyncio
async def test_lost_claim_stops_sending_remaining_parts(outbox_row):
    row_id, _ = outbox_row
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        row.payload = {"alert_text": "card", "review_format": "full_text_v1",
                       "review_content": "content", "review_heading": "heading"}
        await session.commit()

    async def send(*args, **kwargs):
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationOutbox, row_id)
            row.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        await recover_stale_outbox_claims()
        return SimpleNamespace(message_id=777)

    async with AsyncSessionLocal() as session:
        detached_row = await session.get(NotificationOutbox, row_id)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=send))
    with pytest.raises(OutboxClaimLost):
        await _send_outbox_row(bot, detached_row)
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_partial_known_failure_needs_attention_without_retry(outbox_row, monkeypatch):
    from app.services import notification_outbox

    row_id, _ = outbox_row
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, row_id)
        row.payload = {"alert_text": "card", "review_format": "full_text_v1",
                       "review_content": "content", "review_heading": "heading"}
        await session.commit()
    monkeypatch.setattr(notification_outbox, "_claim_batch", AsyncMock(return_value=[row]))
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[
        SimpleNamespace(message_id=1), ValueError("synthetic definite failure")
    ]))
    result = await deliver_notification_outbox(Settings(_env_file=None), bot=bot)
    assert result["failed"] == 1 and result["delivered"] == 0
    assert bot.send_message.await_count == 2
    async with AsyncSessionLocal() as session:
        current = await session.get(NotificationOutbox, row_id)
        assert current.status == "needs_attention"
        assert current.next_attempt_at is None
        assert current.last_error.startswith("partial_telegram_delivery:")
