"""Operator outbox requeue and delivery-after-recovery acceptance."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


def _patch_claim_only_outbox(monkeypatch: pytest.MonkeyPatch, outbox_id: uuid.UUID) -> None:
    """Claim a single row so delivery tests stay stable when the integration DB has other pending rows."""
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services import notification_outbox as outbox_mod

    async def claim_only(_settings, *, limit):
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

    monkeypatch.setattr(outbox_mod, "_claim_batch", claim_only)


@pytest.mark.asyncio
async def test_known_error_returns_to_pending_and_delivers_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C028: definite failures retry via pending/backoff once Telegram works again."""
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services.notification_outbox import _mark_delivery_failure, deliver_notification_outbox

    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_ADMIN_CHAT_ID="1",
        NOTIFICATION_OUTBOX_MAX_ATTEMPTS=5,
    )
    outbox_id = uuid.uuid4()
    token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="ops_alert",
                idempotency_key=f"test:retry:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "ops"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="sending",
                claim_token=token,
            )
        )
        await session.commit()

    await _mark_delivery_failure(outbox_id, "Bad Request: chat not found", settings, token)

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None and row.status == "pending"
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    _patch_claim_only_outbox(monkeypatch, outbox_id)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=42))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))
    result = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert result["delivered"] >= 1
    send_mock.assert_awaited_once()

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None and row.status == "sent" and row.telegram_message_id == 42

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pending_outbox_delivers_when_telegram_pending_has_no_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C030: delivery worker picks up pending outbox and sets pending.message_id."""
    from app.config import Settings, get_settings
    from app.db.models import Job, NotificationOutbox, Proposal, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services.notification_outbox import deliver_notification_outbox
    from app.telegram.full_review import REVIEW_FORMAT

    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_ADMIN_CHAT_ID="1",
    )
    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"ext-{job_id.hex[:8]}",
                title="T",
                description="d",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Exact",
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
                message_id=None,
            )
        )
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="proposal_approval",
                idempotency_key=f"test:c030:{outbox_id}",
                chat_id=1,
                payload={
                    "alert_text": "card",
                    "pending_id": str(pending_id),
                    "review_format": REVIEW_FORMAT,
                    "review_content": "Exact",
                    "review_heading": "Review",
                },
                reference_type="telegram_pending",
                reference_id=pending_id,
                status="pending",
            )
        )
        await session.commit()

    _patch_claim_only_outbox(monkeypatch, outbox_id)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=808))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))
    result = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert result["delivered"] >= 1
    assert send_mock.await_count >= 1

    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, pending_id)
        outbox = await session.get(NotificationOutbox, outbox_id)
        assert pending is not None and pending.message_id == 808
        assert outbox is not None and outbox.status == "sent"

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_operator_requeue_rejects_delivery_evidence_and_ambiguous_cases() -> None:
    from app.db.models import Job, NotificationOutbox, Proposal, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services.notification_outbox import requeue_outbox_for_delivery
    from app.telegram.full_review import REVIEW_FORMAT

    ids = [uuid.uuid4() for _ in range(5)]
    pending_id = uuid.uuid4()
    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"ext-{job_id.hex[:8]}",
                title="T",
                description="d",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Exact",
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
                message_id=111,
            )
        )
        session.add(
            NotificationOutbox(
                id=ids[0],
                kind="ops_alert",
                idempotency_key=f"test:rq0:{ids[0]}",
                chat_id=1,
                payload={"alert_text": "ok"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                last_error="network glitch",
            )
        )
        session.add(
            NotificationOutbox(
                id=ids[1],
                kind="ops_alert",
                idempotency_key=f"test:rq1:{ids[1]}",
                chat_id=1,
                payload={"alert_text": "x"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                telegram_message_id=999,
                last_error="commit_failed_after_telegram_send: boom",
            )
        )
        session.add(
            NotificationOutbox(
                id=ids[2],
                kind="proposal_approval",
                idempotency_key=f"test:rq2:{ids[2]}",
                chat_id=1,
                payload={"alert_text": "legacy"},
                reference_type="telegram_pending",
                reference_id=pending_id,
                status="needs_attention",
                last_error="timeout",
            )
        )
        session.add(
            NotificationOutbox(
                id=ids[3],
                kind="proposal_approval",
                idempotency_key=f"test:rq3:{ids[3]}",
                chat_id=1,
                payload={
                    "alert_text": "card",
                    "pending_id": str(pending_id),
                    "review_format": REVIEW_FORMAT,
                    "review_content": "Exact",
                },
                reference_type="telegram_pending",
                reference_id=pending_id,
                status="needs_attention",
                last_error="timeout",
            )
        )
        session.add(
            NotificationOutbox(
                id=ids[4],
                kind="ops_alert",
                idempotency_key=f"test:rq4:{ids[4]}",
                chat_id=1,
                payload={"alert_text": "lease"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                last_error="lease_expired_possible_telegram_delivery",
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        assert await requeue_outbox_for_delivery(session, ids[0])
        assert not await requeue_outbox_for_delivery(session, ids[1])
        assert not await requeue_outbox_for_delivery(session, ids[2])
        assert not await requeue_outbox_for_delivery(session, ids[3])
        assert not await requeue_outbox_for_delivery(session, ids[4])
        await session.commit()

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, ids[0])
        assert row is not None and row.status == "pending"
        assert row.payload["operator_outbox_requeue"]["previous_last_error"] == "network glitch"


@pytest.mark.asyncio
async def test_operator_requeue_then_worker_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services.notification_outbox import (
        deliver_notification_outbox,
        requeue_outbox_for_delivery,
    )

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
                kind="ops_alert",
                idempotency_key=f"test:deliver:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "after requeue"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                last_error="Bad Request: temporary",
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        assert await requeue_outbox_for_delivery(session, outbox_id)
        await session.commit()

    _patch_claim_only_outbox(monkeypatch, outbox_id)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=7))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))
    result = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert result["delivered"] >= 1

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None and row.status == "sent"

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ambiguous_lease_requeue_requires_ack_then_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    """C028: stale lease rows stay blocked until operator acknowledges ambiguity."""
    from app.config import Settings, get_settings
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services.notification_outbox import (
        OUTBOX_AMBIGUOUS_LEASE_ERROR,
        deliver_notification_outbox,
        requeue_outbox_for_delivery,
    )

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
                kind="ops_alert",
                idempotency_key=f"test:lease:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "lease ack drill"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                last_error=OUTBOX_AMBIGUOUS_LEASE_ERROR,
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        assert not await requeue_outbox_for_delivery(session, outbox_id)
        assert await requeue_outbox_for_delivery(
            session, outbox_id, acknowledge_ambiguous_lease=True
        )
        await session.commit()

    _patch_claim_only_outbox(monkeypatch, outbox_id)
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=55))
    bot = SimpleNamespace(send_message=send_mock, session=SimpleNamespace(close=AsyncMock()))
    result = await deliver_notification_outbox(settings, bot=bot, limit=5)
    assert result["delivered"] >= 1

    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationOutbox, outbox_id)
        assert row is not None and row.status == "sent"
        assert row.payload.get("operator_outbox_requeue", {}).get("actor") == "operator"

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ops_outbox_recovery_script_inventory(monkeypatch, capsys):
    import runpy
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal

    module = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "ops_outbox_recovery.py")
    )
    db_url = os.environ.get("DATABASE_URL")
    assert db_url
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["ops_outbox_recovery.py"])
    with pytest.raises(SystemExit):
        module["parse_args"]()
    monkeypatch.setenv("DATABASE_URL", db_url)
    outbox_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            NotificationOutbox(
                id=outbox_id,
                kind="ops_alert",
                idempotency_key=f"test:script:{outbox_id}",
                chat_id=1,
                payload={"alert_text": "listed"},
                reference_type="inbound_message",
                reference_id=uuid.uuid4(),
                status="needs_attention",
                last_error="listed",
            )
        )
        await session.commit()
    assert await module["main"](SimpleNamespace(id=outbox_id, apply=False, ack_ambiguous_lease=False)) == 0
    assert str(outbox_id) in capsys.readouterr().out
