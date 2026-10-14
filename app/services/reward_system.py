import uuid

import structlog
from sqlalchemy import select

from app.db.models import Reward
from app.db.session import AsyncSessionLocal
from app.services.outcome_service import record_outcome as record_outcome_idempotent

logger = structlog.get_logger(__name__)


class RewardSystem:
    async def record_outcome(
        self,
        job_id: uuid.UUID,
        proposal_id: uuid.UUID | None,
        status: str,
        notes: str | None = None,
        *,
        event_key: str | None = None,
        allow_internal: bool = False,
    ) -> int:
        async with AsyncSessionLocal() as session:
            result = await record_outcome_idempotent(
                session,
                job_id=job_id,
                proposal_id=proposal_id,
                status=status,
                notes=notes,
                event_key=event_key,
                allow_internal=allow_internal,
            )
            await session.commit()

        logger.info(
            "JobPilot AI reward recorded",
            status=result.status,
            reward=result.reward,
            job_id=str(job_id),
            duplicate=result.duplicate,
        )
        return result.reward

    async def get_total_rewards(self) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Reward.reward_value).where(Reward.reward_value > 0)
            )
            return sum(row[0] for row in result.all())
