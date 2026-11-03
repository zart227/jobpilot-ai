"""Unavailable vector memory must not block proposal generation or DB edits."""
import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings


def unavailable():
    raise ConnectionError('Qdrant offline')


@pytest.fixture(autouse=True)
async def clear_memory_index_queue_for_integration():
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        yield
        return
    from sqlalchemy import delete

    from app.db.models import MemoryIndexPending
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(delete(MemoryIndexPending))
        await session.commit()
    yield


async def test_proposal_generation_without_qdrant(monkeypatch):
    from app.agents import proposal_agent

    llm = SimpleNamespace(complete=AsyncMock(return_value='PROPOSAL:\nI can fix the issue.'))
    monkeypatch.setattr(proposal_agent, 'get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr(proposal_agent, 'get_llm_provider', lambda: llm)
    monkeypatch.setattr(proposal_agent, 'get_memory_store', unavailable)
    agent = proposal_agent.ProposalAgent()
    result = await agent.run({'job_data': {'platform': 'fake', 'title': 'Fix', 'description': 'Fix one bug'}, 'score': 80})
    assert result['proposal_content'] and not result.get('pipeline_processing_error')
    llm.complete.assert_awaited_once()


async def test_edit_generation_without_qdrant(monkeypatch):
    from app.agents import edit_proposal_agent

    llm = SimpleNamespace(complete=AsyncMock(return_value='Revised reply.'))
    monkeypatch.setattr(edit_proposal_agent, 'get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr(edit_proposal_agent, 'get_llm_provider', lambda: llm)
    monkeypatch.setattr(edit_proposal_agent, 'get_memory_store', unavailable)
    result = await edit_proposal_agent.EditProposalAgent().run(
        current_text='Original', instruction='Shorten', job={'platform': 'fake'})
    assert result == 'Revised reply.'


async def test_learning_result_survives_unavailable_qdrant(monkeypatch):
    from app.agents import learning_agent

    response = json.dumps({'weights': {'skill_match': .3, 'budget': .2, 'complexity': .15,
                                       'client_quality': .2, 'competition': .15},
                           'insights': [], 'top_platforms': []})
    llm = SimpleNamespace(complete=AsyncMock(return_value=response))
    monkeypatch.setattr(learning_agent, 'get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr(learning_agent, 'get_simple_llm_provider', lambda: llm)
    monkeypatch.setattr(learning_agent, 'get_memory_store', unavailable)
    agent = learning_agent.LearningAgent()
    agent._gather_stats = AsyncMock(return_value={})
    agent._persist_weights = AsyncMock()
    result = await agent.run({'outcome_status': 'replied', 'proposal_content': 'Proposal text with enough length.',
                              'proposal_id': str(uuid.uuid4()), 'job_data': {'platform': 'fake'}})
    assert not result.get('error')
    agent._persist_weights.assert_awaited_once()


@pytest.mark.skipif(os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required')
async def test_learning_queues_patterns_when_qdrant_init_fails(monkeypatch):
    from sqlalchemy import func, select

    from app.agents import learning_agent
    from app.db.models import MemoryIndexPending
    from app.db.session import AsyncSessionLocal

    response = json.dumps({'weights': {'skill_match': .3, 'budget': .2, 'complexity': .15,
                                       'client_quality': .2, 'competition': .15},
                           'insights': [], 'top_platforms': []})
    llm = SimpleNamespace(complete=AsyncMock(return_value=response))
    monkeypatch.setattr(learning_agent, 'get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr(learning_agent, 'get_simple_llm_provider', lambda: llm)
    monkeypatch.setattr(learning_agent, 'get_memory_store', unavailable)
    agent = learning_agent.LearningAgent()
    agent._gather_stats = AsyncMock(return_value={})
    agent._persist_weights = AsyncMock()
    proposal_id = str(uuid.uuid4())
    await agent.run({'outcome_status': 'replied',
                     'proposal_content': 'Long enough proposal sentence for phrase extraction.',
                     'proposal_id': proposal_id, 'job_data': {'platform': 'fake'}})
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(MemoryIndexPending.id)))
        assert count >= 1


async def test_edit_record_survives_unavailable_qdrant(monkeypatch):
    from sqlalchemy import func, select

    from app.db.models import Job, MemoryIndexPending, Proposal, ProposalEdit
    from app.db.session import AsyncSessionLocal
    from app.services import edit_preference_service

    job_id, proposal_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform='fake', external_id=str(job_id), title='t', description='d'))
        await session.flush()
        session.add(Proposal(id=proposal_id, job_id=job_id, content='Original', version=1))
        await session.commit()
    monkeypatch.setattr(edit_preference_service, 'get_memory_store', unavailable)
    service = edit_preference_service.EditPreferenceService()
    await service.record_edit_steps(proposal_id=proposal_id, job_id=job_id, platform='fake',
        job_context='context', edit_steps=[{'instruction': 'shorten', 'before': 'Original', 'after': 'Short'}])
    assert await service.get_style_hints('context') == []
    async with AsyncSessionLocal() as session:
        saved = await session.scalar(select(ProposalEdit).where(ProposalEdit.proposal_id == proposal_id))
        assert saved and saved.instruction == 'shorten'
        pending = await session.scalar(select(func.count(MemoryIndexPending.id)))
        assert pending == 1


async def test_proposal_llm_failure_is_retryable_processing_error(monkeypatch):
    from app.agents import proposal_agent
    from app.llm.errors import LLMErrorKind, LLMServiceError

    llm = SimpleNamespace(complete=AsyncMock(side_effect=LLMServiceError(LLMErrorKind.TIMEOUT, 'timeout', True)))
    monkeypatch.setattr(proposal_agent, 'get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr(proposal_agent, 'get_llm_provider', lambda: llm)
    monkeypatch.setattr(proposal_agent, 'get_memory_store', unavailable)
    result = await proposal_agent.ProposalAgent().run({'job_data': {'platform': 'fake', 'title': 't', 'description': 'd'}})
    assert result['pipeline_processing_error'] is True
    assert result['processing_retryable'] is True and result['processing_stage'] == 'proposal'


@pytest.mark.skipif(os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required')
async def test_proposal_processing_error_sets_backoff_without_creating_draft():
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService

    job_id, token = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform='fake', external_id=str(job_id), title='t', description='d',
                        status='new', processing_claim_token=token, processing_claim_owner='test',
                        processing_claimed_at=datetime.now(UTC), processing_attempt_count=1))
        await session.commit()
    result = await JobPipelineService().persist_pipeline_results(job_id, {
        'pipeline_processing_error': True, 'processing_retryable': True,
        'processing_stage': 'proposal', 'error': 'timeout',
    }, claim_token=token)
    assert result is None
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.status == 'retryable_error' and job.processing_next_attempt_at > datetime.now(UTC)
        assert job.processing_stage == 'proposal' and job.processing_claim_token is None
        assert await session.scalar(select(Proposal.id).where(Proposal.job_id == job_id)) is None
