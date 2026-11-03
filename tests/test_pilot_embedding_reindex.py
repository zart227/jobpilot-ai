"""Pilot embedding script selects unsigned legacy jobs before already-signed ones."""

import os
import uuid

import pytest

from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.embedding_index_signature import SIGNATURE_KEY, embedding_index_signature
from scripts import pilot_embedding_reindex as pilot_mod

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def test_pilot_selects_unsigned_jobs_not_already_signed():
    sig = embedding_index_signature()
    signed_id = uuid.uuid4()
    unsigned_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=signed_id,
                platform="kwork",
                external_id=f"kwork-{signed_id.int}",
                title="signed",
                description="body",
                status="new",
                raw_data={SIGNATURE_KEY: sig},
            )
        )
        session.add(
            Job(
                id=unsigned_id,
                platform="kwork",
                external_id=f"kwork-{unsigned_id.int}",
                title="unsigned",
                description="body",
                status="new",
                raw_data={},
            )
        )
        await session.commit()

    jobs = await pilot_mod._select_jobs(5, None, include_legacy_unsigned=True)
    selected_ids = {job.id for job in jobs}
    assert unsigned_id in selected_ids
    assert signed_id not in selected_ids

    dry = await pilot_mod.run_pilot(
        limit=3,
        job_ids=None,
        include_legacy_unsigned=True,
        apply=False,
        confirm=None,
        execute=False,
    )
    assert all(item.get("stored_signature") != sig for item in dry["selected"])
