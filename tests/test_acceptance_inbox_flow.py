"""Inbox persist → edit → dry-run reply; restart-safe inbound dedup."""

import os
import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


@pytest.fixture
def platform_dry_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_DRY_RUN", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_inbound_dedup_edit_and_dry_run_reply(platform_dry_run) -> None:
    from sqlalchemy import func, select

    from app.config import Settings
    from app.db.models import (
        InboundMessage,
        Interaction,
        Job,
        Proposal,
        SendAttempt,
        TelegramInboxPending,
    )
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.kwork_inbox import (
        ClientInboxMessage,
        _persist_inbound_bundle,
        send_kwork_inbox_reply,
    )

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    external_id = f"kwork-inbox-{job_id.hex[:12]}"
    project_num = str(int(uuid.uuid4().int % 900_000) + 100_000)
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="Inbox flow job",
                description="Desc",
                url=f"https://kwork.ru/projects/{project_num}/view",
                status="sent",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Наш отклик",
                status="sent",
                version=1,
            )
        )
        await session.commit()

    inbox_user_id = int(uuid.uuid4().int % 900_000_000) + 100
    message_id = uuid.uuid4().hex[:12]
    msg = ClientInboxMessage(
        user_id=inbox_user_id,
        username="buyer",
        message_id=message_id,
        text="Какой срок?",
        timestamp=1,
        dialog={"want_id": project_num},
        raw={"MID": message_id, "message": "Какой срок?", "from_username": "buyer"},
    )
    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=True)

    with patch(
        "app.services.kwork_inbox._generate_inbox_reply",
        return_value=("question", "Срок 5 дней."),
    ):
        pending_id = await _persist_inbound_bundle(
            settings,
            msg,
            target_chat=999001,
            my_username="seller",
            my_user_id=1,
        )
        duplicate = await _persist_inbound_bundle(
            settings,
            msg,
            target_chat=999001,
            my_username="seller",
            my_user_id=1,
        )

    assert pending_id is not None
    assert duplicate is None

    message_key = f"{inbox_user_id}:{message_id}"
    async with AsyncSessionLocal() as session:
        inbound_count = await session.execute(
            select(func.count())
            .select_from(InboundMessage)
            .where(InboundMessage.message_key == message_key)
        )
        assert int(inbound_count.scalar() or 0) == 1
        pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
        assert pending is not None
        assert pending.draft_reply == "Срок 5 дней."
        pending.draft_reply = "Срок 5 дней, можем начать завтра."
        pending.status = "edited"
        await session.commit()
        pid = pending.id
        interaction_id = pending.interaction_id
        user_id = pending.kwork_user_id
        username = pending.kwork_username

    ok, err = await send_kwork_inbox_reply(
        user_id=user_id,
        username=username,
        reply_text="Срок 5 дней, можем начать завтра.",
        pending_id=pid,
        interaction_id=interaction_id,
        job_id=job_id,
        proposal_id=proposal_id,
        settings=settings,
    )
    assert ok is True
    assert err is None

    ok2, _ = await send_kwork_inbox_reply(
        user_id=user_id,
        username=username,
        reply_text="Срок 5 дней, можем начать завтра.",
        pending_id=pid,
        interaction_id=interaction_id,
        job_id=job_id,
        proposal_id=proposal_id,
        settings=settings,
    )
    assert ok2 is True

    async with AsyncSessionLocal() as session:
        outbound = await session.execute(
            select(func.count())
            .select_from(Interaction)
            .where(
                Interaction.direction == "outbound",
                Interaction.channel == "kwork",
                Interaction.job_id == job_id,
            )
        )
        assert int(outbound.scalar() or 0) == 1
        attempts = await session.execute(
            select(func.count())
            .select_from(SendAttempt)
            .where(
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
                SendAttempt.job_id == job_id,
            )
        )
        assert int(attempts.scalar() or 0) == 1
