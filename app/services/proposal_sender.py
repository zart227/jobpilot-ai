import uuid
from datetime import datetime, timezone
from typing import Any

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

    async def send(
        self,
        job_id: str,
        proposal_id: str,
        content: str,
        *,
        allow_resubmit: bool = False,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        async with AsyncSessionLocal() as session:
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))
            if proposal and proposal.status == "sent" and not allow_resubmit:
                if proposal.content.strip() == content.strip():
                    logger.info(
                        "JobPilot AI skip duplicate send",
                        job_id=job_id,
                        proposal_id=proposal_id,
                    )
                    return True, None, {"offer_action": "unchanged"}

        if not allow_resubmit and await self.is_already_sent(job_id, proposal_id):
            logger.info(
                "JobPilot AI skip duplicate send",
                job_id=job_id,
                proposal_id=proposal_id,
            )
            return True, None, {"offer_action": "unchanged"}

        async with AsyncSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))

            if not job or not proposal:
                logger.error("JobPilot AI send failed: job or proposal not found")
                return False, "Заказ или отклик не найден в базе", None

            if proposal.status == "sent" and not allow_resubmit:
                if proposal.content.strip() == content.strip():
                    logger.info(
                        "JobPilot AI skip duplicate send",
                        job_id=job_id,
                        proposal_id=proposal_id,
                    )
                    return True, None, {"offer_action": "unchanged"}

            success, error, debug = await self._send_to_platform(job, proposal, content)

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
                    job_title=job.title,
                    error=error,
                    content_file=(debug or {}).get("content_file"),
                    content_length=(debug or {}).get("content_length"),
                )
            if debug is not None:
                debug["job_title"] = job.title
                debug["job_id"] = job_id
                debug["proposal_id"] = proposal_id
                debug["order_id"] = extract_site_order_id(
                    external_id=job.external_id,
                    url=job.url,
                    platform=job.platform,
                )
            return success, error, debug

    async def _send_to_platform(
        self,
        job: Job,
        proposal: Proposal,
        content: str,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        if job.platform == "kwork":
            content = compose_kwork_submission(content)

        logger.info(
            "JobPilot AI sending proposal",
            platform=job.platform,
            job_title=job.title,
            url=job.url,
            content_length=len(content),
        )

        if job.platform == "kwork" and job.url:
            success, error, debug = await send_kwork_offer(
                job.url,
                content,
                float(job.budget_min) if job.budget_min else None,
                float(job.budget_max) if job.budget_max else None,
            )
            return success, error, debug

        logger.warning(
            "JobPilot AI has no sender for platform, skipping real delivery",
            platform=job.platform,
        )
        return False, f"Отправка на платформу {job.platform} не настроена", None
