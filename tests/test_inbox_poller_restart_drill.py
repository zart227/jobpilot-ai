"""C027 poller restart / retry drills for durable inbound capture."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import InboundMessage, Job, NotificationOutbox
from app.db.session import AsyncSessionLocal
from app.services import kwork_inbox
from app.services.notification_outbox import deliver_notification_outbox

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def _seed_job_for_message():
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"kwork-{job_id.int}",
                title="Test job",
                description="test",
                status="sent",
            )
        )
        await session.commit()
    return job_id


async def test_retry_received_runs_twice_after_simulated_poller_restart(monkeypatch):
    """Durable `received` rows are retried on each tick without Kwork re-fetch."""
    job_id = await _seed_job_for_message()
    message_id = uuid.uuid4().hex
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    msg = kwork_inbox.ClientInboxMessage(
        user_id=user_id,
        username="buyer",
        message_id=message_id,
        text="question",
        timestamp=1,
        dialog={"want_id": str(job_id.int)},
        raw={"MID": message_id, "message": "question"},
    )
    await kwork_inbox._capture_inbound(msg)
    message_key = kwork_inbox._message_key(user_id, msg.raw)
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(
            select(InboundMessage).where(
                InboundMessage.platform == "kwork", InboundMessage.message_key == message_key
            )
        )
        inbound.raw_metadata = {
            **(inbound.raw_metadata or {}),
            "generation_retry_after": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        }
        await session.commit()

    settings = Settings(_env_file=None, telegram_admin_chat_id="7")
    persist_calls = 0

    async def noop_persist(settings, msg, **kwargs):
        nonlocal persist_calls
        if kwork_inbox._message_key(msg.user_id, msg.raw) != message_key:
            return
        persist_calls += 1
        return

    monkeypatch.setattr(kwork_inbox, "_persist_inbound_bundle", noop_persist)
    monkeypatch.setattr(kwork_inbox, "deliver_notification_outbox", AsyncMock(return_value={"delivered": 0}))

    await kwork_inbox._retry_received_inbound(settings)
    await kwork_inbox._retry_received_inbound(settings)
    assert persist_calls == 2

    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(
            select(InboundMessage).where(InboundMessage.message_key == message_key)
        )
        assert inbound.status == "received"


async def test_check_kwork_inbox_retries_received_before_collect(monkeypatch, tmp_path):
    order: list[str] = []

    class FakeApi:
        async def get_actor(self):
            return {"username": "seller", "id": 1}

        async def get_dialogs(self):
            order.append("get_dialogs")
            return []

        async def close(self):
            return None

    state_file = tmp_path / "inbox-state.json"
    state_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(kwork_inbox, "create_kwork_api", AsyncMock(return_value=FakeApi()))

    async def track_retry(*args, **kwargs):
        order.append("retry_received")
        return 0

    async def track_collect(*args, **kwargs):
        order.append("collect_new")
        return []

    monkeypatch.setattr(kwork_inbox, "_retry_received_inbound", track_retry)
    monkeypatch.setattr(kwork_inbox, "_collect_new_messages", track_collect)
    monkeypatch.setattr(kwork_inbox, "deliver_notification_outbox", AsyncMock(return_value={"delivered": 0}))

    settings = Settings(
        _env_file=None,
        kwork_inbox_check_enabled=True,
        kwork_email="u@example.com",
        kwork_password="secret",
        telegram_bot_token="token",
        telegram_admin_chat_id="7",
        kwork_inbox_state_file=str(state_file),
    )
    await kwork_inbox.check_kwork_inbox(settings, bot=object())
    assert order.index("retry_received") < order.index("collect_new")


async def test_exhausted_generation_ops_alert_delivered_via_outbox(monkeypatch):
    job_id = await _seed_job_for_message()
    message_id = uuid.uuid4().hex
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    msg = kwork_inbox.ClientInboxMessage(
        user_id=user_id,
        username="buyer",
        message_id=message_id,
        text="question",
        timestamp=1,
        dialog={"want_id": str(job_id.int)},
        raw={"MID": message_id, "message": "question"},
    )
    await kwork_inbox._capture_inbound(msg)
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(
            select(InboundMessage).where(
                InboundMessage.message_key == kwork_inbox._message_key(user_id, msg.raw)
            )
        )
        inbound.raw_metadata = {**(inbound.raw_metadata or {}), "generation_attempts": 4}
        await session.commit()
        inbound_id = inbound.id

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", AsyncMock(side_effect=RuntimeError("offline")))
    settings = Settings(_env_file=None, telegram_admin_chat_id="7", NOTIFICATION_OUTBOX_MAX_ATTEMPTS=3)
    assert await kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7) is None

    async with AsyncSessionLocal() as session:
        outbox = await session.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.idempotency_key == f"inbound_generation_exhausted:{inbound_id}"
            )
        )
        assert outbox is not None and outbox.status == "pending"
        outbox_id = outbox.id

    from app.services import notification_outbox as outbox_mod

    async def claim_only_this_row(_settings, *, limit):
        claim_token = uuid.uuid4()
        lease_until = datetime.now(UTC) + timedelta(minutes=5)
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationOutbox, outbox_id, with_for_update=True)
            if not row or row.status != "pending":
                return []
            row.status = "sending"
            row.claim_token = claim_token
            row.claim_expires_at = lease_until
            await session.commit()
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationOutbox, outbox_id)
            return [row] if row else []

    monkeypatch.setattr(outbox_mod, "_claim_batch", claim_only_this_row)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=4242))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))
    result = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert result["delivered"] >= 1
    send_mock.assert_awaited_once()

    async with AsyncSessionLocal() as session:
        outbox = await session.get(NotificationOutbox, outbox_id)
        assert outbox.status == "sent" and outbox.telegram_message_id == 4242
