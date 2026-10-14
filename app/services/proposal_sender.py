import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.kwork_browser import (
    is_kwork_connects_limit_error,
    is_kwork_order_closed_error,
    send_kwork_offer,
)
from app.services.kwork_pause import (
    get_kwork_pause_reason,
    is_kwork_paused,
    record_connects_limit_detected,
)
from app.services.send_attempt_service import finalize_send_attempt, prepare_proposal_send
from app.utils.formatting import compose_kwork_submission, extract_site_order_id

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
        claim, job, proposal = await prepare_proposal_send(
            job_id,
            proposal_id,
            content,
            allow_resubmit=allow_resubmit,
        )
        if claim.action == "unchanged":
            return True, None, {"offer_action": "unchanged"}
        if claim.action == "already_sent":
            return True, None, {"offer_action": "unchanged"}
        if claim.action == "blocked":
            reason = claim.reason or "send_blocked"
            return False, reason, {"error_label": reason}

        if not job or not proposal or not claim.attempt_id or not claim.claim_token:
            return False, "Не удалось захватить отправку", None

        success, error, debug = await self._send_to_platform(job, proposal, content)

        finalized = await finalize_send_attempt(
            claim.attempt_id,
            claim.claim_token,
            success=success,
            error=error,
            platform_response=debug,
        )
        if not finalized:
            return False, "Устаревший claim отправки", {"error_label": "stale_claim"}

        if success:
            logger.info(
                "JobPilot AI proposal delivered",
                platform=job.platform,
                job_id=job_id,
            )
        else:
            await self._handle_send_failure(job_id, proposal_id, error, debug)

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

    async def _handle_send_failure(
        self,
        job_id: str,
        proposal_id: str,
        error: str | None,
        debug: dict[str, Any] | None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            proposal = await session.get(Proposal, uuid.UUID(proposal_id))
            if not job or not proposal:
                return

            if is_kwork_order_closed_error(error):
                proposal.status = "skipped"
                job.status = "skipped"
                pending_rows = await session.execute(
                    select(TelegramPending).where(
                        TelegramPending.job_id == job.id,
                        TelegramPending.proposal_id == proposal.id,
                        TelegramPending.status.in_(("approved", "edited")),
                    )
                )
                for pending in pending_rows.scalars():
                    pending.status = "skipped"
                if debug is not None:
                    debug["error_label"] = "order_closed"
            else:
                proposal.status = "approved"
                job.status = "approved"
                if debug is not None and is_kwork_connects_limit_error(error):
                    debug["error_label"] = "connects_limit"
                    record_connects_limit_detected(error or "")
                logger.error(
                    "JobPilot AI proposal delivery failed",
                    platform=job.platform,
                    job_id=job_id,
                    job_title=job.title,
                    error=error,
                )
            await session.commit()

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

        if get_settings().platform_dry_run:
            logger.info(
                "Platform dry-run: skipping external delivery",
                platform=job.platform,
                job_id=str(job.id),
            )
            return True, None, {"dry_run": True, "platform": job.platform}

        if job.platform == "kwork" and is_kwork_paused():
            reason = get_kwork_pause_reason() or "Kwork приостановлен"
            return False, reason, {"error_label": "kwork_paused"}

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
        return False, f"Отправка на платформу {job.platform} не настроена", None
