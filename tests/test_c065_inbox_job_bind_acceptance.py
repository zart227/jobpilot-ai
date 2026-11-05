"""C065: operator bind + reprocess for ambiguous inbox job context."""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated database required",
)


@pytest.mark.asyncio
async def test_bound_job_id_resolves_single_job_without_username_ambiguity() -> None:
    from app.db.models import Client, Job
    from app.db.session import AsyncSessionLocal
    from app.services.kwork_job_match import find_kwork_job_for_message

    client_id = uuid.uuid4()
    job_a = uuid.uuid4()
    job_b = uuid.uuid4()
    buyer_suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        session.add(
            Client(
                id=client_id,
                platform="kwork",
                external_id=f"/user/buyer-{buyer_suffix}",
                name=f"buyer-{buyer_suffix}",
            )
        )
        session.add(
            Job(
                id=job_a,
                platform="kwork",
                external_id=f"kwork-a-{job_a.hex[:8]}",
                status="sent",
                title="One",
                description="d",
                client_id=client_id,
            )
        )
        session.add(
            Job(
                id=job_b,
                platform="kwork",
                external_id=f"kwork-b-{job_b.hex[:8]}",
                status="sent",
                title="Two",
                description="d",
                client_id=client_id,
            )
        )
        await session.commit()
        ambiguous = await find_kwork_job_for_message(
            session, username=f"buyer-{buyer_suffix}", project_id=None
        )
        assert ambiguous.needs_context
        bound = await find_kwork_job_for_message(
            session,
            username=f"buyer-{buyer_suffix}",
            project_id=None,
            bound_job_id=job_a,
        )
        assert bound.job is not None and bound.job.id == job_a
        assert not bound.needs_context


@pytest.mark.asyncio
async def test_bind_and_reprocess_enables_generation_with_selected_job(monkeypatch) -> None:
    from app.config import Settings
    from app.db.models import Client, InboundMessage, Job, TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services import kwork_inbox

    client_id = uuid.uuid4()
    job_a = uuid.uuid4()
    job_b = uuid.uuid4()
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    message_id = uuid.uuid4().hex
    buyer_suffix = uuid.uuid4().hex[:8]
    username = f"buyer-{buyer_suffix}"
    async with AsyncSessionLocal() as session:
        session.add(
            Client(
                id=client_id,
                platform="kwork",
                external_id=f"/user/{username}",
                name=username,
            )
        )
        session.add(
            Job(
                id=job_a,
                platform="kwork",
                external_id=f"kwork-a-{job_a.hex[:8]}",
                status="sent",
                title="One",
                description="d",
                client_id=client_id,
            )
        )
        session.add(
            Job(
                id=job_b,
                platform="kwork",
                external_id=f"kwork-b-{job_b.hex[:8]}",
                status="sent",
                title="Two",
                description="d",
                client_id=client_id,
            )
        )
        await session.commit()

    msg = kwork_inbox.ClientInboxMessage(
        user_id=user_id,
        username=username,
        message_id=message_id,
        text="Какой срок?",
        timestamp=1,
        dialog={},
        raw={"MID": message_id, "message": "Какой срок?"},
    )
    await kwork_inbox._capture_inbound(msg)
    settings = Settings(_env_file=None, telegram_admin_chat_id="7")

    with patch(
        "app.services.kwork_inbox._generate_inbox_reply",
        return_value=("question", ""),
    ):
        pending_id = await kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7)
    assert pending_id

    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(
            select(InboundMessage).where(
                InboundMessage.platform == "kwork",
                InboundMessage.message_key == kwork_inbox._message_key(msg.user_id, msg.raw),
            )
        )
        assert inbound is not None
        inbound_id = inbound.id
        assert await kwork_inbox.bind_inbound_to_job(session, inbound_id, job_a)
        assert await kwork_inbox.operator_reprocess_inbound_after_bind(session, inbound_id)
        await session.commit()

    with patch(
        "app.services.kwork_inbox._generate_inbox_reply",
        return_value=("question", "Срок 5 дней."),
    ):
        new_pending = await kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7)

    assert new_pending
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, uuid.UUID(new_pending))
        assert pending is not None
        assert pending.job_id == job_a
        assert pending.draft_reply == "Срок 5 дней."
