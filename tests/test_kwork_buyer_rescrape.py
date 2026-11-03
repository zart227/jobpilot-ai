"""A refreshed Kwork listing must not assign one buyer's stats to another."""
import os
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Client, Job
from app.db.session import AsyncSessionLocal
from app.schemas.job import ClientInfo, NormalizedJob
from app.services.job_pipeline import JobPipelineService

pytestmark = pytest.mark.skipif(
    os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required'
)


def listing(external_id, buyer, projects=5, hire=50):
    return NormalizedJob(
        platform='kwork', external_id=external_id, title='Small fix', description='Fix one bug',
        client=ClientInfo(name=buyer, external_id=f'/user/{buyer}', metadata={
            'projects_posted': projects, 'hire_percent': hire, 'stats_source': 'kwork_buyer_block_v1',
        }) if buyer else None,
    )


async def test_changed_buyer_is_relinked_without_modifying_original_client():
    from app.services.opportunity_policy import (
        kwork_job_assessment_rejection,
        kwork_opportunity_fingerprint,
    )

    job_id, old_id = uuid.uuid4(), uuid.uuid4()
    original_stats = {'projects_posted': 5, 'hire_percent': 50, 'stats_source': 'kwork_buyer_block_v1'}
    async with AsyncSessionLocal() as session:
        session.add(Client(id=old_id, platform='kwork', external_id=f'/user/old-{job_id}',
                           metadata_=original_stats))
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='Small fix',
                        description='Fix one bug', client_id=old_id))
        await session.commit()
    facts = (await JobPipelineService().job_to_state(job_id))['job_data']
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.raw_data = {'opportunity_assessment': {
            'relevant': True, 'assessment_id': 'e48e15f8-6d76-4076-9631-78bf927c5719', 'source_fingerprint': kwork_opportunity_fingerprint(facts),
            'estimated_hours': 2, 'estimated_calendar_days': 1, 'scope_clear': True,
            'complexity': 'simple', 'minimum_price_rub': 700,
        }}
        await session.commit()
    await JobPipelineService().save_normalized_job(listing(str(job_id), f'new-{job_id}'))
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        old = await session.get(Client, old_id)
        buyer = await session.get(Client, job.client_id)
        assert old.metadata_ == original_stats
        assert buyer.id != old_id and buyer.external_id == f'/user/new-{job_id}'
        assert buyer.metadata_['hire_percent'] == 50
        assert await kwork_job_assessment_rejection(session, job) == 'assessment_stale'
        assert (await JobPipelineService().job_to_state(job_id))['job_data']['client_id'] == str(buyer.id)


async def test_unidentified_buyer_drops_old_evidence_without_affecting_shared_client():
    job_id, client_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Client(id=client_id, platform='kwork', external_id=f'/user/test-{job_id}',
                           metadata_={'projects_posted': 10, 'hire_percent': 90, 'stats_source': 'kwork_buyer_block_v1'}))
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='Small fix',
                        description='Fix one bug', client_id=client_id))
        await session.commit()
    await JobPipelineService().save_normalized_job(listing(str(job_id), None))
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).client_id is None
        assert (await session.get(Client, client_id)).metadata_['hire_percent'] == 90


async def test_same_buyer_is_updated_without_duplicate():
    job_id, client_id = uuid.uuid4(), uuid.uuid4()
    name = f'buyer-{job_id}'
    async with AsyncSessionLocal() as session:
        session.add(Client(id=client_id, platform='kwork', external_id=f'/user/{name}', metadata_={}))
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='Small fix',
                        description='Fix one bug', client_id=client_id))
        await session.commit()
    await JobPipelineService().save_normalized_job(listing(str(job_id), name, 7, 60))
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).client_id == client_id
        assert (await session.get(Client, client_id)).metadata_['projects_posted'] == 7
        buyers = (await session.scalars(select(Client).where(Client.external_id == f'/user/{name}'))).all()
        assert len(buyers) == 1


async def test_rescrape_refreshes_even_shorter_or_missing_description_and_invalidates_assessment():
    from app.services.opportunity_policy import (
        kwork_job_assessment_rejection,
        kwork_opportunity_fingerprint,
    )

    job_id, client_id = uuid.uuid4(), uuid.uuid4()
    buyer = f'buyer-{job_id}'
    async with AsyncSessionLocal() as session:
        session.add(Client(id=client_id, platform='kwork', external_id=f'/user/{buyer}', metadata_={
            'projects_posted': 5, 'hire_percent': 50, 'stats_source': 'kwork_buyer_block_v1',
        }))
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='Old title',
                        description='An older long description', client_id=client_id,
                        budget_min=500, budget_max=5000, budget_currency='RUB', raw_data={}))
        await session.commit()
    service = JobPipelineService()
    facts = (await service.job_to_state(job_id))['job_data']
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.raw_data = {'opportunity_assessment': {
            'relevant': True, 'assessment_id': 'e48e15f8-6d76-4076-9631-78bf927c5719', 'source_fingerprint': kwork_opportunity_fingerprint(facts),
            'estimated_hours': 2, 'estimated_calendar_days': 1, 'scope_clear': True,
            'complexity': 'simple', 'minimum_price_rub': 700,
        }}
        await session.commit()
    incoming = listing(str(job_id), buyer)
    incoming.title = 'New title'
    incoming.description = ''  # scraper failed to obtain a complete description
    incoming.budget_min = 500
    incoming.budget_max = 1000
    incoming.budget_currency = 'RUB'
    await service.save_normalized_job(incoming)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert (job.title, job.description, int(job.budget_max)) == ('New title', '', 1000)
        assert job.raw_data['embedding_pending'] is True
        assert await kwork_job_assessment_rejection(session, job) == 'assessment_stale'


async def test_parallel_rescrapes_use_one_buyer_record():
    import asyncio

    job_ids = [uuid.uuid4(), uuid.uuid4()]
    buyer = f'parallel-{uuid.uuid4()}'
    async with AsyncSessionLocal() as session:
        for job_id in job_ids:
            session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='Small fix',
                            description='Fix one bug'))
        await session.commit()
    await asyncio.gather(*(JobPipelineService().save_normalized_job(listing(str(job_id), buyer))
                           for job_id in job_ids))
    async with AsyncSessionLocal() as session:
        jobs = [await session.get(Job, job_id) for job_id in job_ids]
        assert jobs[0].client_id == jobs[1].client_id
        buyers = (await session.scalars(select(Client).where(Client.external_id == f'/user/{buyer}'))).all()
        assert len(buyers) == 1


async def test_returning_job_text_to_old_value_never_reactivates_assessment():
    from app.services.opportunity_policy import (
        kwork_job_assessment_rejection,
        kwork_opportunity_fingerprint,
    )

    service = JobPipelineService()
    external_id = str(uuid.uuid4())
    buyer = f'revert-{external_id}'
    normal = listing(external_id, buyer)
    normal.raw_data = {'listing_observed_at': '2026-11-01T10:00:00+00:00'}
    # Avoid external embeddings on creation.
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.services import job_pipeline

    with patch.object(job_pipeline, 'get_memory_store', return_value=SimpleNamespace(store_job_embedding=AsyncMock(return_value=False))):
        job_id = await service.save_normalized_job(normal)
    facts = (await service.job_to_state(job_id))['job_data']
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.raw_data = {**job.raw_data, 'opportunity_assessment': {
            'relevant': True, 'assessment_id': str(uuid.uuid4()),
            'source_fingerprint': kwork_opportunity_fingerprint(facts),
            'estimated_hours': 1, 'estimated_calendar_days': 1, 'scope_clear': True,
            'complexity': 'simple', 'minimum_price_rub': 500,
        }}
        await session.commit()
    await service.save_normalized_job(normal)
    async with AsyncSessionLocal() as session:
        unchanged = await session.get(Job, job_id)
        assert await kwork_job_assessment_rejection(session, unchanged) is None
    changed = normal.model_copy(update={'description': 'Changed text'})
    await service.save_normalized_job(changed)
    await service.save_normalized_job(normal)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.description == normal.description
        assert await kwork_job_assessment_rejection(session, job) == 'assessment_stale'


async def test_buyer_stats_revision_cannot_return_to_previous_assessment():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.services import job_pipeline
    from app.services.opportunity_policy import (
        kwork_job_assessment_rejection,
        kwork_opportunity_fingerprint,
    )

    service = JobPipelineService()
    suffix = str(uuid.uuid4())
    first = listing(f'first-{suffix}', f'buyer-{suffix}')
    second = listing(f'second-{suffix}', f'buyer-{suffix}')
    with patch.object(job_pipeline, 'get_memory_store', return_value=SimpleNamespace(store_job_embedding=AsyncMock(return_value=False))):
        first_id = await service.save_normalized_job(first)
        await service.save_normalized_job(second)
    facts = (await service.job_to_state(first_id))['job_data']
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, first_id)
        job.raw_data = {**job.raw_data, 'opportunity_assessment': {
            'relevant': True, 'assessment_id': str(uuid.uuid4()),
            'source_fingerprint': kwork_opportunity_fingerprint(facts),
            'estimated_hours': 1, 'estimated_calendar_days': 1, 'scope_clear': True,
            'complexity': 'simple', 'minimum_price_rub': 500,
        }}
        await session.commit()
    await service.save_normalized_job(listing(second.external_id, f'buyer-{suffix}', 6, 60))
    await service.save_normalized_job(second)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, first_id)
        assert await kwork_job_assessment_rejection(session, job) == 'assessment_stale'
