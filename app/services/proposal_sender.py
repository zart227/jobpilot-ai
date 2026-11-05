import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.db.workflow_states import proposal_is_submitted
from app.services.kwork_browser import (
    is_kwork_connects_limit_error,
    is_kwork_order_closed_error,
    send_kwork_offer,
)
from app.services.kwork_pause import (
    get_kwork_pause_reason,
    is_kwork_paused,
    load_pause_state,
    record_connects_limit_detected,
)
from app.services.send_attempt_service import finalize_send_attempt, prepare_proposal_send
from app.utils.formatting import extract_site_order_id

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
                return bool(proposal and proposal_is_submitted(proposal.status))

            result = await session.execute(
                select(Proposal.id)
                .where(
                    Proposal.job_id == job.id,
                    Proposal.status.in_(("sent", "simulated_sent")),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def _proposal_already_submitted(self, job_id: str, proposal_id: str) -> bool:
        return await self.is_already_sent(job_id, proposal_id)

    async def send(
        self,
        job_id: str,
        proposal_id: str,
        content: str,
        *,
        allow_resubmit: bool | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        claim, job, proposal = await prepare_proposal_send(
            job_id,
            proposal_id,
            content,
        )
        if claim.action == "unchanged":
            return True, None, {"offer_action": "unchanged"}
        if claim.action == "already_sent":
            return True, None, {"offer_action": "unchanged"}
        if claim.action == "blocked":
            reason = claim.reason or "send_blocked"
            if reason == "new_offer_after_sent" and await self._proposal_already_submitted(
                job_id, proposal_id
            ):
                return True, None, {"offer_action": "unchanged"}
            return False, reason, {"error_label": reason}

        if not job or not proposal or not claim.attempt_id or not claim.claim_token:
            return False, "Не удалось захватить отправку", None

        success, error, debug = await self._send_to_platform(
            job, proposal, content, approved_payload=claim.approved_payload
        )

        finalized = await finalize_send_attempt(
            claim.attempt_id,
            claim.claim_token,
            success=success,
            error=error,
            platform_response=debug,
            ambiguous=bool(debug and debug.get("ambiguous")),
        )
        if not finalized:
            return False, "Устаревший claim отправки", {"error_label": "stale_claim"}

        if success:
            logger.info(
                "JobPilot AI proposal delivered",
                platform=job.platform,
                job_id=job_id,
            )
        elif not (debug and debug.get("ambiguous")):
            await self._handle_send_failure(job_id, proposal_id, error, debug)
        else:
            logger.warning("Proposal delivery outcome requires reconciliation", job_id=job_id)

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
        *,
        approved_payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        if not approved_payload or approved_payload.get("content") != content:
            return False, "approved_payload_mismatch", {"error_label": "approved_payload_mismatch"}

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
            debug: dict[str, Any] = {"dry_run": True, "platform": job.platform}
            if job.platform == "kwork":
                state = load_pause_state()
                if state and state.connects_remaining is not None:
                    debug["connects_remaining"] = state.connects_remaining
                if state and state.connects_total is not None:
                    debug["connects_total"] = state.connects_total
            return True, None, debug

        if job.platform == "kwork" and is_kwork_paused():
            reason = get_kwork_pause_reason() or "Kwork приостановлен"
            return False, reason, {"error_label": "kwork_paused"}

        if job.platform == "kwork" and job.url:
            success, error, debug = await send_kwork_offer(
                job.url,
                content,
                float(job.budget_min) if job.budget_min else None,
                float(job.budget_max) if job.budget_max else None,
                offer_price=approved_payload.get("offer_price"),
                duration_days=approved_payload.get("duration_days"),
                submission_action=approved_payload.get("submission_action"),
                offer_title=approved_payload.get("offer_title"),
                payment_type_label=approved_payload.get("payment_type_label"),
            )
            debug = dict(debug or {})
            debug["dry_run"] = False
            if success:
                # Current browser signals do not bind an external receipt to this payload.
                debug["ambiguous"] = True
                debug["error_label"] = "payload_receipt_unverified"
                return False, "Отправка не подтверждена по точному содержимому", debug
            return success, error, debug

        logger.warning(
            "JobPilot AI has no sender for platform, skipping real delivery",
            platform=job.platform,
        )
        return False, f"Отправка на платформу {job.platform} не настроена", None
