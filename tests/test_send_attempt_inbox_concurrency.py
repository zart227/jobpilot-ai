"""Inbox reply send idempotency under sequential double-send (integration)."""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


@pytest.mark.asyncio
async def test_inbox_reply_second_send_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_DRY_RUN", "true")
    from app.config import Settings, get_settings

    get_settings.cache_clear()

    from sqlalchemy import func, select

    from app.db.models import Job, Proposal, SendAttempt, TelegramInboxPending
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.kwork_inbox import send_kwork_inbox_reply

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    interaction_id = uuid.uuid4()
    external_id = f"kwork-conc-{job_id.hex[:12]}"

    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="Concurrent",
                description="d",
                status="sent",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Hi",
                status="sent",
                version=1,
            )
        )
        session.add(
            TelegramInboxPending(
                id=pending_id,
                job_id=job_id,
                proposal_id=proposal_id,
                kwork_user_id=7,
                kwork_username="buyer",
                client_message="?",
                draft_reply="Answer",
                chat_id=1,
                status="approved",
            )
        )
        await session.commit()

    from conftest import seed_test_inbox_approval

    async with AsyncSessionLocal() as session:
        await seed_test_inbox_approval(session, pending_id)

    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=True)
    ok1, _ = await send_kwork_inbox_reply(
        user_id=7,
        username="buyer",
        reply_text="Answer",
        pending_id=pending_id,
        interaction_id=interaction_id,
        job_id=job_id,
        proposal_id=proposal_id,
        settings=settings,
    )
    ok2, _ = await send_kwork_inbox_reply(
        user_id=7,
        username="buyer",
        reply_text="Answer",
        pending_id=pending_id,
        interaction_id=interaction_id,
        job_id=job_id,
        proposal_id=proposal_id,
        settings=settings,
    )
    assert ok1 and ok2

    async with AsyncSessionLocal() as session:
        count = await session.execute(
            select(func.count())
            .select_from(SendAttempt)
            .where(
                SendAttempt.kind == "inbox_reply",
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
                SendAttempt.job_id == job_id,
            )
        )
        assert int(count.scalar() or 0) == 1

    get_settings.cache_clear()
