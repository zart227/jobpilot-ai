"""Durable presentation binding. A presented snapshot is not human authorization."""

from app.config import get_settings
from app.db.models import Job, Proposal, ProposalApprovalSnapshot
from app.utils.formatting import kwork_text_price_matches, resolve_kwork_offer_price


def proposal_card_payload(job: Job, proposal: Proposal) -> dict:
    minimum = float(job.budget_min) if job.budget_min is not None else None
    maximum = float(job.budget_max) if job.budget_max is not None else None
    assessment = (job.raw_data or {}).get("opportunity_assessment") or {}
    settings = get_settings()
    offer = resolve_kwork_offer_price(
        minimum,
        maximum,
        assessment,
        settings.kwork_offer_discount_percent,
    )
    payload = {
        "schema_version": 1,
        "review_format": "full_text_v1",
        "job_id": str(job.id),
        "proposal_id": str(proposal.id),
        "proposal_version": proposal.version,
        "content": proposal.content,
        "platform": job.platform,
        "url": job.url,
        "budget_min": minimum,
        "budget_max": maximum,
        "currency": job.budget_currency,
        "offer_price": offer if job.platform == "kwork" else None,
        "duration_days": (1 if assessment.get("minimum_price_rub") is not None else 7)
        if job.platform == "kwork"
        else None,
        "submission_action": "new_offer",
    }
    if job.platform == "kwork":
        payload["assessment_id"] = assessment.get("assessment_id")
    return payload


async def capture_proposal_card(session, pending_id, job, proposal):
    if proposal.job_id != job.id:
        raise ValueError("Proposal/job mismatch")
    payload = proposal_card_payload(job, proposal)
    if job.platform == "kwork" and not kwork_text_price_matches(proposal.content, payload["offer_price"]):
        raise ValueError("Proposal price does not match reviewed offer")
    snapshot = ProposalApprovalSnapshot(
        pending_id=pending_id, payload=payload
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def proposal_card_is_current(session, pending_id, job, proposal) -> bool:
    if not job or not proposal or proposal.job_id != job.id:
        return False
    from app.services.opportunity_policy import kwork_job_assessment_rejection

    if await kwork_job_assessment_rejection(session, job):
        return False
    if job.platform == "kwork" and not kwork_text_price_matches(
        proposal.content, proposal_card_payload(job, proposal)["offer_price"]
    ):
        return False
    snapshot = await session.get(ProposalApprovalSnapshot, pending_id)
    return snapshot is not None and snapshot.payload == proposal_card_payload(job, proposal)


async def record_proposal_approval(
    session,
    pending_id,
    job,
    proposal,
    *,
    actor_user_id: int,
    chat_id: int,
    message_id: int,
    approval_kind: str = "card",
):
    """Called inside the locked callback transaction after validating its presentation."""
    from app.db.models import ProposalApprovalEvent

    if actor_user_id <= 0 or message_id <= 0 or not chat_id:
        raise ValueError("Missing human approval identity")
    if proposal.job_id != job.id or approval_kind not in ("card", "edited_preview"):
        raise ValueError("Invalid approval target/kind")
    presented = await session.get(ProposalApprovalSnapshot, pending_id)
    payload = proposal_card_payload(job, proposal)
    if job.platform == "kwork" and not kwork_text_price_matches(proposal.content, payload["offer_price"]):
        raise ValueError("Proposal price does not match reviewed offer")
    if presented is None:
        raise ValueError("Missing presented approval snapshot")
    expected = dict(presented.payload)
    if approval_kind == "edited_preview":
        if payload["proposal_version"] != expected["proposal_version"] + 1:
            raise ValueError("Edited approval must advance exactly one version")
        expected.update(content=payload["content"], proposal_version=payload["proposal_version"])
    if expected != payload:
        raise ValueError("Presented approval snapshot is stale")
    event = ProposalApprovalEvent(
        pending_id=pending_id,
        proposal_id=proposal.id,
        payload=payload,
        actor_user_id=actor_user_id,
        chat_id=chat_id,
        message_id=message_id,
        approval_kind=approval_kind,
    )
    session.add(event)
    await session.flush()
    return event


async def matching_proposal_approval(session, job, proposal):
    from sqlalchemy import select

    from app.db.models import ProposalApprovalEvent

    payload = proposal_card_payload(job, proposal)
    if job.platform == "kwork" and not kwork_text_price_matches(proposal.content, payload["offer_price"]):
        return None
    return await session.scalar(
        select(ProposalApprovalEvent)
        .where(
            ProposalApprovalEvent.proposal_id == proposal.id,
            ProposalApprovalEvent.payload == payload,
        )
        .order_by(ProposalApprovalEvent.created_at.desc())
        .limit(1)
    )
