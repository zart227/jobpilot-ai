"""Record outcomes/rewards idempotently and run learning with sent proposal context."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Job, Outcome, Proposal, Reward, SendAttempt
from app.db.outcome_states import (
    ALL_OUTCOME_STATUSES,
    default_event_key,
    validate_outcome_status,
)
from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
from app.services.conversation_context_service import (
    build_chat_agent_state,
    finalize_chat_agent_state,
)
from app.services.reward_constants import REWARD_MAP

logger = structlog.get_logger(__name__)


@dataclass
class OutcomeRecordResult:
    created: bool
    duplicate: bool
    reward: int
    outcome_id: uuid.UUID | None
    event_key: str
    status: str


async def latest_outcome_status(session, job_id: uuid.UUID) -> str | None:
    result = await session.execute(
        select(Outcome.status)
        .where(Outcome.job_id == job_id)
        .order_by(Outcome.recorded_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return str(row) if row else None


async def sent_proposal_for_learning(
    session,
    proposal_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, str, int | None]:
    if not proposal_id:
        return None, "", None
    proposal = await session.get(Proposal, proposal_id)
    if not proposal:
        return None, "", None

    attempt_result = await session.execute(
        select(SendAttempt)
        .where(
            SendAttempt.proposal_id == proposal_id,
            SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
        )
        .order_by(SendAttempt.finished_at.desc().nulls_last(), SendAttempt.created_at.desc())
        .limit(1)
    )
    attempt = attempt_result.scalar_one_or_none()
    if attempt:
        return proposal_id, attempt.snapshot_content, attempt.snapshot_version
    return proposal_id, proposal.content or "", proposal.version


async def build_learning_state(
    session,
    job_id: uuid.UUID,
    *,
    status: str,
    proposal_id: uuid.UUID | None,
) -> dict:
    state = await build_chat_agent_state(session, job_id, channel="api")
    if not state:
        job = await session.get(Job, job_id)
        if not job:
            return {}
        state = {"job_id": str(job_id), "job_data": {"platform": job.platform}}

    pid, content, version = await sent_proposal_for_learning(session, proposal_id)
    if pid:
        state["proposal_id"] = str(pid)
    if content:
        state["proposal_content"] = content
    if version is not None:
        state["proposal_version"] = version
    state["outcome_status"] = status
    return state


async def record_outcome(
    session,
    *,
    job_id: uuid.UUID,
    proposal_id: uuid.UUID | None,
    status: str,
    notes: str | None = None,
    event_key: str | None = None,
    allow_internal: bool = False,
) -> OutcomeRecordResult:
    normalized = validate_outcome_status(status, allow_internal=allow_internal)
    if normalized not in ALL_OUTCOME_STATUSES:
        raise ValueError(f"unsupported outcome status: {status}")

    key = (event_key or default_event_key(str(job_id), str(proposal_id) if proposal_id else None, normalized)).strip()
    if not key:
        raise ValueError("event_key is required")

    existing = await session.execute(select(Outcome).where(Outcome.event_key == key))
    prior = existing.scalar_one_or_none()
    if prior:
        return OutcomeRecordResult(
            created=False,
            duplicate=True,
            reward=int(prior.reward or 0),
            outcome_id=prior.id,
            event_key=key,
            status=prior.status,
        )

    reward_value = REWARD_MAP.get(normalized, 0)
    outcome = Outcome(
        job_id=job_id,
        proposal_id=proposal_id,
        status=normalized,
        reward=reward_value,
        notes=notes,
        event_key=key,
    )
    reward_row = Reward(
        job_id=job_id,
        proposal_id=proposal_id,
        event_type=normalized,
        reward_value=reward_value,
        context={"notes": notes, "event_key": key},
        event_key=key,
    )
    session.add(outcome)
    session.add(reward_row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        retry = await session.execute(select(Outcome).where(Outcome.event_key == key))
        row = retry.scalar_one_or_none()
        return OutcomeRecordResult(
            created=False,
            duplicate=True,
            reward=int(row.reward or 0) if row else 0,
            outcome_id=row.id if row else None,
            event_key=key,
            status=row.status if row else normalized,
        )

    logger.info(
        "Outcome recorded",
        job_id=str(job_id),
        status=normalized,
        reward=reward_value,
        event_key=key,
    )
    return OutcomeRecordResult(
        created=True,
        duplicate=False,
        reward=reward_value,
        outcome_id=outcome.id,
        event_key=key,
        status=normalized,
    )


async def record_outcome_and_learn(
    session,
    *,
    job_id: uuid.UUID,
    proposal_id: uuid.UUID | None,
    status: str,
    notes: str | None = None,
    event_key: str | None = None,
    allow_internal: bool = False,
    run_learning: bool = True,
) -> OutcomeRecordResult:
    from app.agents.learning_agent import LearningAgent

    result = await record_outcome(
        session,
        job_id=job_id,
        proposal_id=proposal_id,
        status=status,
        notes=notes,
        event_key=event_key,
        allow_internal=allow_internal,
    )
    await session.commit()

    if run_learning and result.created:
        state = await build_learning_state(
            session,
            job_id,
            status=result.status,
            proposal_id=proposal_id,
        )
        if state:
            await session.rollback()
            state = await finalize_chat_agent_state(state)
            agent = LearningAgent()
            await agent.run(state)
    return result
