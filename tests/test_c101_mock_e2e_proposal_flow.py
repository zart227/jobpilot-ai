"""C101: mock platform E2E — filter → score → proposal → Telegram → approve → confirmed dry-run send."""

from __future__ import annotations

import json
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

FILTER_JSON = json.dumps(
    {
        "relevant": True,
        "reason": "C101 mock filter pass",
        "estimated_hours": 2,
        "estimated_calendar_days": 1,
        "scope_clear": True,
        "complexity": "simple",
        "minimum_price_rub": 1500,
    }
)
SCORING_JSON = json.dumps(
    {
        "score": 92,
        "breakdown": {
            "skill_match": 90,
            "budget": 85,
            "complexity": 88,
            "client_quality": 90,
            "competition": 80,
        },
        "summary": "C101 mock scoring",
    }
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


@pytest.fixture
def mock_pipeline_llm(monkeypatch: pytest.MonkeyPatch):
    from app.agents import filter_agent, proposal_agent, scoring_agent
    from app.config import Settings
    from app.llm.call_usage import LLMCallUsage
    from app.memory.qdrant_store import get_memory_store

    proposal_text_holder: dict[str, str] = {}
    mock_usage = LLMCallUsage(resolved_model="mock-c101", provider_kind="mock")

    async def complete(system: str, user: str, **kwargs):
        if "Determine if" in system:
            return FILTER_JSON
        if "Score the job" in system:
            return SCORING_JSON
        return (
            "PROPOSAL:\n"
            f"{proposal_text_holder.get('text', 'C101 mock E2E proposal awaiting Telegram approval.')}\n\n"
            "EXECUTION_PLAN:\n1. Analyze\n2. Implement\n\n"
            "TIMELINE:\n1 day"
        )

    llm = SimpleNamespace(complete=complete, consume_last_call_usage=lambda: mock_usage)
    monkeypatch.setattr(filter_agent, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(scoring_agent, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(proposal_agent, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(filter_agent, "get_simple_llm_provider", lambda: llm)
    monkeypatch.setattr(scoring_agent, "get_scoring_llm_provider", lambda: llm)
    monkeypatch.setattr(proposal_agent, "get_llm_provider", lambda: llm)
    memory = SimpleNamespace(
        search_similar_proposals=AsyncMock(return_value=[]),
        store_successful_proposal=AsyncMock(return_value=False),
    )

    async def fake_to_thread(func, *args, **kwargs):
        if func is get_memory_store:
            return memory
        return func(*args, **kwargs)

    monkeypatch.setattr(proposal_agent.asyncio, "to_thread", fake_to_thread)
    return proposal_text_holder


async def _seed_kwork_job_with_claim():
    from conftest import seed_test_kwork_eligibility

    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_processing import try_claim_job

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
    mock_pipeline_llm,
):
    from app.agents.graph import filter_node, proposal_node, scoring_node
    from app.config import Settings, get_settings
    from app.db.models import Job, NotificationOutbox, Proposal, SendAttempt, TelegramPending
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService
    from app.services.notification_outbox import deliver_notification_outbox
    from app.telegram.bot import create_dispatcher

    job_id, claim = await _seed_kwork_job_with_claim()
    pipeline = JobPipelineService()
    state = await pipeline.job_to_state(job_id)
    proposal_text = "C101 mock E2E proposal awaiting Telegram approval."
    mock_pipeline_llm["text"] = proposal_text

    filter_out = await filter_node(state)
    assert filter_out.get("is_relevant") is True
    state = {**state, **filter_out}

    score_out = await scoring_node(state)
    assert score_out.get("score") is not None and score_out["score"] >= 50
    state = {**state, **score_out}

    propose_out = await proposal_node(state)
    assert propose_out.get("proposal_content")
    graph_result = {**state, **propose_out}

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
        assert job.score is not None and job.score >= 50
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
        outbox_id = outbox.id

    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=True)
    message_ids = iter(range(9000, 9500))

    async def _fake_send(*_args, **_kwargs):
        return SimpleNamespace(message_id=next(message_ids))

    deliver_bot = SimpleNamespace(send_message=AsyncMock(side_effect=_fake_send))
    telegram_message_id: int | None = None
    for _ in range(40):
        async with AsyncSessionLocal() as session:
            outbox_row = await session.get(NotificationOutbox, outbox_id)
            assert outbox_row is not None
            if outbox_row.status == "sent" and outbox_row.telegram_message_id is not None:
                telegram_message_id = outbox_row.telegram_message_id
                break
        await deliver_notification_outbox(settings, bot=deliver_bot, limit=10)
    assert telegram_message_id is not None

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
        assert proposal.status == "simulated_sent"
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == "simulated_sent"
        attempt = await session.scalar(
            select(SendAttempt).where(
                SendAttempt.proposal_id == proposal_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
        )
        assert attempt is not None
        assert (attempt.platform_response or {}).get("dry_run") is True
