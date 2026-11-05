"""Operator override append-only journal."""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated database required",
)


@pytest.mark.asyncio
async def test_record_and_list_operator_override() -> None:
    from app.db.session import AsyncSessionLocal
    from app.services.operator_override_journal import (
        list_operator_overrides,
        record_operator_override,
    )

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        row = await record_operator_override(
            session,
            action="bind_inbound_job",
            actor_source="api_token",
            reference_type="inbound_message",
            reference_id=job_id,
            detail={"job_id": str(uuid.uuid4())},
        )
        await session.commit()
        override_id = row.id

    async with AsyncSessionLocal() as session:
        rows = await list_operator_overrides(
            session, reference_type="inbound_message", reference_id=job_id
        )
        assert len(rows) == 1
        assert rows[0].id == override_id
        assert rows[0].action == "bind_inbound_job"
