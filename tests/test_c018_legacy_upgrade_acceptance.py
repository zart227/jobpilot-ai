"""C018: upgrading from mid-chain revision preserves core relational rows."""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated *_test database required",
)


def _run_alembic(*args: str) -> None:
    root = os.path.dirname(os.path.dirname(__file__))
    proc = subprocess.run([os.path.join(root, ".venv/bin/python"), "-m", "alembic", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\n{proc.stderr}\n{proc.stdout}"
        )


@pytest.fixture
def ensure_at_head():
    _run_alembic("upgrade", "head")
    yield
    _run_alembic("upgrade", "head")


@pytest.mark.asyncio
async def test_upgrade_from_0004_preserves_job_proposal_links(ensure_at_head) -> None:
    from app.db.models import Client, Job, Proposal
    from app.db.session import AsyncSessionLocal

    legacy_rev = "20261013_0004"
    _run_alembic("downgrade", legacy_rev)

    job_id = uuid.uuid4()
    client_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:10]

    async with AsyncSessionLocal() as session:
        session.add(
            Client(
                id=client_id,
                platform="kwork",
                external_id=f"c018-client-{suffix}",
                name="C018 Client",
            )
        )
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c018-job-{suffix}",
                title="C018 legacy upgrade job",
                description="C018 description",
                status="pending",
                client_id=client_id,
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                status="draft",
                content="C018 proposal body",
            )
        )
        await session.commit()

    _run_alembic("upgrade", "head")

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        proposal = await session.get(Proposal, proposal_id)
        assert job is not None
        assert proposal is not None
        assert proposal.job_id == job_id

        version = await session.execute(text("SELECT version_num FROM alembic_version"))
        head = version.scalar_one()
        assert head == "20261103_0011"

        count = await session.execute(select(Job).where(Job.id == job_id))
        assert count.scalar_one_or_none() is not None
