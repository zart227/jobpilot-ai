"""Activate scoring weight versions with a single active row enforced in DB."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScoringWeight
from app.services.scoring_weights_validation import validate_scoring_weights


async def deactivate_all_scoring_weights(session: AsyncSession) -> None:
    await session.execute(update(ScoringWeight).values(is_active=False))


async def replace_active_scoring_weights(
    session: AsyncSession,
    weights: dict[str, float],
    *,
    source: str = "learning_agent",
) -> ScoringWeight:
    normalized = validate_scoring_weights(weights)
    await deactivate_all_scoring_weights(session)
    row = ScoringWeight(weights=normalized, source=source, is_active=True)
    session.add(row)
    await session.flush()
    return row


async def activate_scoring_weight_row(session: AsyncSession, weight_id: uuid.UUID) -> ScoringWeight:
    target = await session.get(ScoringWeight, weight_id)
    if target is None:
        raise ValueError(f"scoring weight not found: {weight_id}")
    validate_scoring_weights(target.weights)
    await deactivate_all_scoring_weights(session)
    target.is_active = True
    await session.flush()
    return target


async def count_active_scoring_weights(session: AsyncSession) -> int:
    result = await session.execute(
        select(ScoringWeight.id).where(ScoringWeight.is_active.is_(True))
    )
    return len(result.all())
