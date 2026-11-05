"""Append-only audit trail for operator manual overrides (C112)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.db.models import OperatorOverrideEvent


async def record_operator_override(
    session,
    *,
    action: str,
    actor_source: str,
    reference_type: str,
    reference_id: uuid.UUID | None = None,
    actor_user_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> OperatorOverrideEvent:
    row = OperatorOverrideEvent(
        action=action[:64],
        actor_source=actor_source[:32],
        actor_user_id=actor_user_id,
        reference_type=reference_type[:64],
        reference_id=reference_id,
        detail=dict(detail or {}),
    )
    session.add(row)
    await session.flush()
    return row


async def list_operator_overrides(
    session,
    *,
    reference_type: str,
    reference_id: uuid.UUID,
    limit: int = 10,
) -> list[OperatorOverrideEvent]:
    result = await session.scalars(
        select(OperatorOverrideEvent)
        .where(
            OperatorOverrideEvent.reference_type == reference_type,
            OperatorOverrideEvent.reference_id == reference_id,
        )
        .order_by(OperatorOverrideEvent.created_at.desc())
        .limit(limit)
    )
    return list(result)
