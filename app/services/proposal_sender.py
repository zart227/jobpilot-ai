import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.db.models import Job, Proposal
from app.db.session import AsyncSessionLocal
from app.services.kwork_browser import send_kwork_offer
from app.utils.formatting import compose_kwork_submission

logger = structlog.get_logger(__name__)


class ProposalSender:
    """Sends approved proposals to freelance platforms."""

    last_error: str | None = None

    async def is_already_sent(self, job_id: str, proposal_id: str | None = None) -> bool:
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            if not job:
                return False

            if proposal_id:
                proposal = await session.get(Proposal, uuid.UUID(proposal_id))
                if proposal and proposal.status == "sent":
                    return True

            result = await session.execute(
                select(Proposal.id).where(
                    Proposal.job_id == job.id,
                    Proposal.status == "sent",
                ).limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def send(self, job_id: str, proposal_id: str, content: str) -> tuple[bool, str | None]:
        if await self.is_already_sent(job_id, proposal_id):
            logger.info(
                "JobPilot AI skip duplicate send",
                job_id=job_id,
                proposal_id=proposal_id,
            )
            return True, None

        async with AsyncSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))

            if not job or not proposal:
                logger.error("JobPilot AI send failed: job or proposal not found")
                return False, "Заказ или отклик не найден в базе"

            if proposal.status == "sent":
                logger.info(
                    "JobPilot AI skip duplicate send",
                    job_id=job_id,
                    proposal_id=proposal_id,
                )
                return True, None

            success, error = await self._send_to_platform(job, proposal, content)

            if success:
                proposal.status = "sent"
                proposal.sent_at = datetime.now(timezone.utc)
                job.status = "sent"
                await session.commit()
                logger.info(
                    "JobPilot AI proposal delivered",
                    platform=job.platform,
                    job_id=job_id,
                )
            else:
                proposal.status = "approved"
                job.status = "approved"
                await session.commit()
                logger.error(
                    "JobPilot AI proposal delivery failed",
                    platform=job.platform,
                    job_id=job_id,
                    error=error,
                )
            return success, error

    async def _send_to_platform(
        self,
        job: Job,
        proposal: Proposal,
        content: str,
    ) -> tuple[bool, str | None]:
        if job.platform == "kwork":
            content = compose_kwork_submission(
                content,
                proposal.execution_plan,
                proposal.timeline,
            )

        logger.info(
            "JobPilot AI sending proposal",
            platform=job.platform,
            url=job.url,
            content_preview=content[:200],
        )

        if job.platform == "kwork" and job.url:
            return await send_kwork_offer(
                job.url,
                content,
                float(job.budget_min) if job.budget_min else None,
                float(job.budget_max) if job.budget_max else None,
            )

        logger.warning(
            "JobPilot AI has no sender for platform, skipping real delivery",
            platform=job.platform,
        )
        return False, f"Отправка на платформу {job.platform} не настроена"
