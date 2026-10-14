import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.job import ClientInfo, NormalizedJob
from app.services.job_pipeline import JobPipelineService


@pytest.mark.asyncio
async def test_save_job_does_not_merge_clients_with_null_external_id() -> None:
    service = JobPipelineService()
    normalized = NormalizedJob(
        platform="kwork",
        external_id="job-1",
        title="Test",
        description="Desc",
        client=ClientInfo(
            platform="kwork",
            external_id=None,
            name="Unknown",
            rating=None,
            reviews_count=0,
            metadata={},
        ),
    )

    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    async def _refresh(job):
        job.id = uuid.uuid4()

    session.refresh = AsyncMock(side_effect=_refresh)

    added_clients: list = []

    def capture_add(obj):
        if obj.__class__.__name__ == "Client":
            added_clients.append(obj)

    session.add.side_effect = capture_add

    with (
        patch("app.services.job_pipeline.AsyncSessionLocal") as session_factory,
        patch("app.services.job_pipeline.get_memory_store") as memory_factory,
    ):
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
        memory = MagicMock()
        memory.store_job_embedding = AsyncMock()
        memory_factory.return_value = memory

        job_id = await service.save_normalized_job(normalized)

    assert isinstance(job_id, uuid.UUID)
    assert len(added_clients) == 1
    assert added_clients[0].external_id is None
    session.execute.assert_awaited_once()
