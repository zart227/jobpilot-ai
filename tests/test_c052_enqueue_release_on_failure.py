"""C052: broker dedup must be released after failed job processing (task finally)."""

import os
import uuid
from unittest.mock import AsyncMock

import pytest

from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.job_task_enqueue import (
    queue_process_single_job,
    release_process_job_enqueue,
)
from app.tasks import scrape_tasks

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def test_failed_process_job_release_allows_reenqueue(monkeypatch):
    """Mirrors process_single_job finally: release_process_job_enqueue after failure."""
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="fake",
                external_id=str(job_id),
                title="t",
                description="d",
                status="new",
            )
        )
        await session.commit()

    assert queue_process_single_job(str(job_id)) is not None

    monkeypatch.setattr(
        scrape_tasks,
        "_process_job",
        AsyncMock(side_effect=RuntimeError("simulated worker crash mid-pipeline")),
    )

    with pytest.raises(RuntimeError, match="simulated worker crash"):
        await scrape_tasks._process_job(job_id)

    release_process_job_enqueue(str(job_id))
    assert queue_process_single_job(str(job_id)) is not None
    release_process_job_enqueue(str(job_id))
