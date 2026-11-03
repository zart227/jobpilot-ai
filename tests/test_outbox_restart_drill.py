"""C031: committed outbox survives worker restart and delivers without duplicating business rows."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from test_pipeline_claim_fencing import seed

from app.config import get_settings
from app.db.models import NotificationOutbox, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.job_pipeline import JobPipelineService
from app.services.notification_outbox import deliver_notification_outbox

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated DB required",
)


@pytest.fixture
def review_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "100")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_committed_outbox_delivers_after_simulated_worker_restart(review_chat, monkeypatch):
    """Pipeline commit leaves durable outbox; a later delivery pass sends once without new proposal rows."""
    from app.services import notification_outbox as outbox_mod

    job_id, token = await seed()
    proposal_id = await JobPipelineService().persist_pipeline_results(
        job_id,
        {"score": 99, "is_relevant": True, "proposal_content": "Restart drill text"},
        claim_token=token,
    )
    async with AsyncSessionLocal() as session:
        pending = await session.scalar(
            select(TelegramPending).where(TelegramPending.proposal_id == proposal_id)
        )
        assert pending is not None
        outbox = await session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.reference_id == pending.id)
        )
        assert await session.scalar(
            select(func.count()).select_from(TelegramPending).where(
                TelegramPending.proposal_id == proposal_id
            )
        ) == 1
        assert outbox is not None and outbox.status == "pending"
        outbox_id = outbox.id

    get_settings.cache_clear()
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_ADMIN_CHAT_ID="100",
    )

    async def claim_only_this_row(_settings, *, limit):
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationOutbox, outbox_id, with_for_update=True)
            if not row or row.status != "pending":
                return []
            row.status = "sending"
            row.claim_token = uuid.uuid4()
            row.claim_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            await session.commit()
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationOutbox, outbox_id)
            return [row] if row else []

    monkeypatch.setattr(outbox_mod, "_claim_batch", claim_only_this_row)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=909))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))

    first = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert first["delivered"] >= 1
    calls_after_first = send_mock.await_count
    assert calls_after_first >= 1

    second = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert second["delivered"] == 0
    assert send_mock.await_count == calls_after_first

    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Proposal).where(Proposal.job_id == job_id)) == 1
        assert await session.scalar(select(func.count()).select_from(TelegramPending).where(
            TelegramPending.proposal_id == proposal_id
        )) == 1
        outbox = await session.get(NotificationOutbox, outbox_id)
        assert outbox is not None and outbox.status == "sent" and outbox.telegram_message_id == 909
        pending = await session.scalar(select(TelegramPending).where(TelegramPending.proposal_id == proposal_id))
        assert pending is not None and pending.message_id == 909

    get_settings.cache_clear()
