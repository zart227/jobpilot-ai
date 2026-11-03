"""Summaries must not perform network calls or commits inside caller transactions."""
import os
import uuid
from unittest.mock import AsyncMock

import pytest

from app.db.models import Interaction, Job
from app.db.session import AsyncSessionLocal
from app.services import conversation_context_service, conversation_summary

pytestmark = pytest.mark.skipif(
    os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required'
)


async def seed():
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id),
                        title='Original', description='Description', raw_data={'other': 1}))
        await session.flush()
        for i in range(4):
            session.add(Interaction(job_id=job_id, direction='inbound', channel='kwork',
                                    message=f'message {i}'))
        await session.commit()
    return job_id


async def test_context_does_not_commit_caller_changes_or_call_llm(monkeypatch):
    job_id = await seed()
    summary = AsyncMock(return_value='summary')
    monkeypatch.setattr(conversation_summary, 'summarize_turns', summary)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.title = 'Uncommitted change'
        state = await conversation_context_service.build_chat_agent_state(session, job_id)
        summary.assert_not_awaited()
        async with AsyncSessionLocal() as observer:
            assert (await observer.get(Job, job_id)).title == 'Original'
        await session.rollback()
    await conversation_context_service.finalize_chat_agent_state(state)
    summary.assert_awaited_once()
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.title == 'Original'
        assert job.raw_data['other'] == 1
        assert job.raw_data['inbox_conversation_summary']['text'] == 'summary'


async def test_concurrent_cache_and_other_changes_are_not_overwritten(monkeypatch):
    job_id = await seed()
    async with AsyncSessionLocal() as session:
        state = await conversation_context_service.build_chat_agent_state(session, job_id)
    async def summarize(_turns):
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            job.raw_data = {**job.raw_data, 'other': 2, 'inbox_conversation_summary': {'text': 'newer'}}
            await session.commit()
        return 'older result'
    monkeypatch.setattr(conversation_summary, 'summarize_turns', summarize)
    result = await conversation_context_service.finalize_chat_agent_state(state)
    assert 'older result' in result['conversation_context']
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.raw_data['other'] == 2
        assert job.raw_data['inbox_conversation_summary'] == {'text': 'newer'}


async def test_api_chat_releases_transaction_before_summary_and_reply(monkeypatch):
    from types import SimpleNamespace

    from app import main
    from app.schemas.job import ChatRequest

    job_id = await seed()
    async with AsyncSessionLocal() as session:
        for i in range(4):
            session.add(Interaction(job_id=job_id, direction='inbound', channel='api', message=f'api {i}'))
        await session.commit()
    async with AsyncSessionLocal() as session:
        async def summarize(_turns):
            assert not session.in_transaction()
            return 'Summary of API conversation'
        async def reply(state):
            assert not session.in_transaction()
            assert 'Summary of API conversation' in state['conversation_context']
            return {'chat_intent': 'question', 'chat_reply': 'Test reply'}
        monkeypatch.setattr(conversation_summary, 'summarize_turns', summarize)
        monkeypatch.setattr(main, 'ChatAgent', lambda: SimpleNamespace(run=reply))
        monkeypatch.setattr(main, 'LearningAgent', lambda: SimpleNamespace(run=AsyncMock()))
        result = await main.handle_client_chat(ChatRequest(job_id=job_id, message='Question'), db=session, _=None)
        assert result.reply == 'Test reply'
