"""C101: mock platform E2E — job graph → Telegram review → admin approve → confirmed dry-run send."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, User
from sqlalchemy import select

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


@pytest.fixture
def review_telegram(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "7000000000:AAFakeTokenForTests")
    from aiogram.fsm.storage.memory import MemoryStorage

    monkeypatch.setattr("app.telegram.bot.RedisStorage", lambda **_kw: MemoryStorage())
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_kwork_job_with_claim():
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_processing import try_claim_job

    from conftest import seed_test_kwork_eligibility

    job_id = uuid.uuid4()
    external_id = f"kwork-c101-{job_id.hex[:12]}"
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="C101 integration job",
                description="Full mock E2E description for filter and proposal.",
                url="https://kwork.ru/projects/424242/view",
                status="new",
                budget_min=1500,
                budget_max=3000,
            )
        )
        await session.commit()
    claim = await try_claim_job(job_id, owner="c101-mock-e2e")
    assert claim is not None
    async with AsyncSessionLocal() as session:
        await seed_test_kwork_eligibility(session, job_id)
        await session.commit()
    return job_id, claim


@pytest.mark.asyncio
async def test_c101_job_filter_score_proposal_telegram_approve_confirmed_sent(
    platform_dry_run,
    review_telegram,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.config import Settings, get_settings
    from app.db.models import Job, NotificationOutbox, Proposal, SendAttempt, TelegramPending
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService
    from app.services.job_processing import run_job_graph_with_claim
    from app.services.notification_outbox import deliver_notification_outbox
    from app.telegram.bot import create_dispatcher

    job_id, claim = await _seed_kwork_job_with_claim()
    pipeline = JobPipelineService()
    state = await pipeline.job_to_state(job_id)
    proposal_text = "C101 mock E2E proposal awaiting Telegram approval."

    async def mock_graph_ainvoke(graph_state):
        return {
            **graph_state,
            "is_relevant": True,
            "relevance_reason": "mock_filter_pass",
            "filter_source": "mock_filter_agent",
            "score": 92,
            "score_breakdown": {"mock": 92},
            "proposal_content": proposal_text,
        }

    graph = SimpleNamespace(ainvoke=mock_graph_ainvoke)
    graph_result = await run_job_graph_with_claim(graph, state, claim)
    assert graph_result.get("is_relevant") is True
    assert graph_result.get("score") == 92
    assert graph_result.get("proposal_content") == proposal_text

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        from app.services.opportunity_policy import kwork_job_facts, kwork_opportunity_fingerprint

        facts = await kwork_job_facts(session, job)
        assessment = dict((job.raw_data or {}).get("opportunity_assessment") or {})
        assessment["source_fingerprint"] = kwork_opportunity_fingerprint(facts)
        assessment["relevant"] = True
        graph_result["opportunity_assessment"] = assessment

    proposal_id = await pipeline.persist_pipeline_results(
        job_id, graph_result, claim_token=claim.claim_token
    )
    assert proposal_id is not None

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.is_relevant is True
        assert job.score == 92
        assert job.status == "pending_approval"
        pending = await session.scalar(
            select(TelegramPending).where(TelegramPending.proposal_id == proposal_id)
        )
        assert pending is not None and pending.status == "pending"
        outbox = await session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.reference_id == pending.id)
        )
        assert outbox is not None
        pending_id = pending.id

    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=True)
    telegram_message_id = 9001
    deliver_bot = SimpleNamespace(
        send_message=AsyncMock(
            return_value=SimpleNamespace(message_id=telegram_message_id)
        )
    )
    delivery = await deliver_notification_outbox(settings, bot=deliver_bot, limit=5)
    assert delivery.get("delivered", 0) >= 1

    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, pending_id)
        assert pending is not None
        assert pending.message_id == telegram_message_id

    get_settings.cache_clear()
    dp = create_dispatcher()
    approve_handler = next(
        h.callback
        for h in dp.callback_query.handlers
        if h.callback.__name__ == "handle_approve"
    )

    operator = User(id=100, is_bot=False, first_name="Operator")
    chat = Chat(id=100, type="private")
    review_message = SimpleNamespace(
        chat=chat,
        message_id=telegram_message_id,
        answer=AsyncMock(),
        edit_reply_markup=AsyncMock(return_value=True),
    )
    callback = SimpleNamespace(
        id="c101-approve",
        from_user=operator,
        data=f"approve:{pending_id}",
        message=review_message,
        answer=AsyncMock(),
    )

    await approve_handler(callback)

    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, proposal_id)
        assert proposal is not None
        assert proposal.status == "sent"
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == "sent"
        attempt = await session.scalar(
            select(SendAttempt).where(
                SendAttempt.proposal_id == proposal_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
        )
        assert attempt is not None
        assert (attempt.platform_response or {}).get("dry_run") is True
