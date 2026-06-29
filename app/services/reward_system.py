import uuid

import structlog
from sqlalchemy import select

from app.db.models import Job, Outcome, Proposal, Reward
from app.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

REWARD_MAP: dict[str, int] = {
    "sent": 0,
    "ignored": 0,
    "replied": 5,
    "hired": 50,
}


class RewardSystem:
    async def record_outcome(
        self,
        job_id: uuid.UUID,
        proposal_id: uuid.UUID | None,
        status: str,
        notes: str | None = None,
    ) -> int:
        reward = REWARD_MAP.get(status, 0)

        async with AsyncSessionLocal() as session:
            outcome = Outcome(
                job_id=job_id,
                proposal_id=proposal_id,
                status=status,
                reward=reward,
                notes=notes,
            )
            session.add(outcome)

            reward_record = Reward(
                job_id=job_id,
                proposal_id=proposal_id,
                event_type=status,
                reward_value=reward,
                context={"notes": notes},
            )
            session.add(reward_record)

            job = await session.get(Job, job_id)
            if job:
                job.status = status

            if proposal_id:
                proposal = await session.get(Proposal, proposal_id)
                if proposal:
                    proposal.status = status

            await session.commit()

        logger.info("JobPilot AI reward recorded", status=status, reward=reward, job_id=str(job_id))
        return reward

    async def get_total_rewards(self) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Reward.reward_value).where(Reward.reward_value > 0)
            )
            return sum(row[0] for row in result.all())
