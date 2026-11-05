"""C085: single active scoring weight row and rollback activation."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)

WEIGHTS_A = {
    "skill_match": 0.25,
    "budget": 0.05,
    "complexity": 0.35,
    "client_quality": 0.25,
    "competition": 0.10,
}
WEIGHTS_B = {
    "skill_match": 0.20,
    "budget": 0.10,
    "complexity": 0.30,
    "client_quality": 0.25,
    "competition": 0.15,
}


@pytest.mark.asyncio
async def test_only_one_active_scoring_weight_after_replace_and_rollback():
    from app.db.models import ScoringWeight
    from app.db.session import AsyncSessionLocal
    from app.services.scoring_weights_service import (
        activate_scoring_weight_row,
        count_active_scoring_weights,
        replace_active_scoring_weights,
    )

    first_id: uuid.UUID
    second_id: uuid.UUID
    async with AsyncSessionLocal() as session:
        first = await replace_active_scoring_weights(session, WEIGHTS_A)
        await session.commit()
        first_id = first.id

    async with AsyncSessionLocal() as session:
        second = await replace_active_scoring_weights(session, WEIGHTS_B)
        await session.commit()
        second_id = second.id
        assert await count_active_scoring_weights(session) == 1
        active = (
            await session.execute(select(ScoringWeight).where(ScoringWeight.is_active.is_(True)))
        ).scalar_one()
        assert active.id == second_id

    async with AsyncSessionLocal() as session:
        await activate_scoring_weight_row(session, first_id)
        await session.commit()
        assert await count_active_scoring_weights(session) == 1
        active = (
            await session.execute(select(ScoringWeight).where(ScoringWeight.is_active.is_(True)))
        ).scalar_one()
        assert active.id == first_id


@pytest.mark.asyncio
async def test_database_rejects_two_simultaneous_active_rows():
    from app.db.models import ScoringWeight
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        session.add(ScoringWeight(weights=WEIGHTS_A, source="test-a", is_active=True))
        session.add(ScoringWeight(weights=WEIGHTS_B, source="test-b", is_active=True))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
