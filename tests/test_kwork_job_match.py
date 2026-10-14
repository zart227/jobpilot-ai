import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import Client, Job
from app.services.kwork_job_match import (
    find_kwork_job_for_message,
    kwork_project_id_matches,
    kwork_username_matches_client,
)


def test_kwork_project_id_exact_not_substring() -> None:
    job_123 = Job(
        platform="kwork",
        external_id="kwork-123",
        url="https://kwork.ru/projects/123",
        title="A",
        description="d",
    )
    job_91234 = Job(
        platform="kwork",
        external_id="kwork-91234",
        url="https://kwork.ru/projects/91234",
        title="B",
        description="d",
    )
    assert kwork_project_id_matches(job_123, "123")
    assert not kwork_project_id_matches(job_91234, "123")
    assert kwork_project_id_matches(job_91234, "91234")


def test_kwork_username_exact_not_substring() -> None:
    alice = Client(name="alice", external_id="/user/alice")
    malice = Client(name="malice", external_id="/user/malice")
    assert kwork_username_matches_client(alice, "alice")
    assert not kwork_username_matches_client(malice, "alice")
    assert kwork_username_matches_client(malice, "malice")


@pytest.mark.asyncio
async def test_find_kwork_job_ambiguous_by_username() -> None:
    client = Client(id=uuid.uuid4(), name="buyer", external_id="/user/buyer")
    job_a = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="kwork-1",
        status="sent",
        title="One",
        description="d",
        client_id=client.id,
    )
    job_b = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="kwork-2",
        status="sent",
        title="Two",
        description="d",
        client_id=client.id,
    )

    session = AsyncMock()
    scalars = MagicMock()
    scalars.all.return_value = [job_a, job_b]
    result = MagicMock()
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=client)

    match = await find_kwork_job_for_message(session, username="buyer", project_id=None)
    assert match.needs_context
    assert len(match.candidate_jobs) == 2
    assert match.job is None
