"""The operator inventory must only read and must not imply send readiness."""
import importlib.util
import os
import uuid
from pathlib import Path

import pytest

from app.db.models import Job
from app.db.session import AsyncSessionLocal

spec = importlib.util.spec_from_file_location(
    'inspect_kwork_assessments', Path(__file__).resolve().parents[1] / 'scripts/inspect_kwork_assessments.py'
)
inspect_kwork_assessments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspect_kwork_assessments)


def test_inventory_requires_explicit_database(monkeypatch, capsys):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert inspect_kwork_assessments.main([]) == 2
    assert 'explicitly' in capsys.readouterr().err


@pytest.mark.parametrize('limit', ['0', '1001', '-1'])
def test_inventory_rejects_unbounded_limit(limit):
    with pytest.raises(SystemExit):
        inspect_kwork_assessments.parse_args(['--limit', limit])


@pytest.mark.skipif(os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required')
async def test_inventory_reports_legacy_block_without_changing_job():
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='private',
                        description='private description', raw_data={}, status='approved'))
        await session.commit()
    records = await inspect_kwork_assessments.inspect(1000)
    record = next(item for item in records if item['job_id'] == str(job_id))
    assert record == {'job_id': str(job_id), 'status': 'approved', 'assessment_gate': 'buyer_not_eligible'}
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.status == 'approved' and job.raw_data == {}
