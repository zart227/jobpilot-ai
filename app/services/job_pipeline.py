import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db.models import Client, Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.db.workflow_states import (
    JOB_STATUS_FILTERED,
    JOB_STATUS_SCORED,
    PROPOSAL_SUBMITTED_STATUSES,
    job_allows_pipeline_persist,
    job_is_submitted,
    proposal_is_submitted,
)
from app.memory.qdrant_store import get_memory_store
from app.schemas.job import NormalizedJob
from app.services.send_attempt_service import delivery_blocks_mutation

logger = structlog.get_logger(__name__)


def _transition_job_status(job: Job, new_status: str) -> None:
    from app.db.status_transitions import allows_job_processing_transition

    if not allows_job_processing_transition(job.status, new_status):
        logger.warning(
            "Rejected job processing status transition",
            job_id=str(job.id),
            previous=job.status,
            new_status=new_status,
        )
        return
    job.status = new_status


async def _upsert_kwork_client(session, info):
    """Keep a durable buyer evidence revision, changing it only when facts change."""
    incoming = {key: value for key, value in info.metadata.items() if key != "_evidence_revision"}
    stmt = insert(Client).values(
        platform="kwork", external_id=info.external_id, name=info.name,
        rating=info.rating, reviews_count=info.reviews_count, metadata_={},
    ).on_conflict_do_nothing(constraint="uq_clients_platform_external_id")
    await session.execute(stmt)
    buyer = await session.scalar(select(Client).where(
        Client.platform == "kwork", Client.external_id == info.external_id,
    ).with_for_update())
    previous = dict(buyer.metadata_ or {})
    old_facts = {key: value for key, value in previous.items() if key != "_evidence_revision"}
    revision = previous.get("_evidence_revision")
    if not revision or old_facts != incoming:
        revision = str(uuid.uuid4())
    buyer.name, buyer.rating, buyer.reviews_count = info.name, info.rating, info.reviews_count
    buyer.metadata_ = {**incoming, "_evidence_revision": revision}
    return buyer


class JobPipelineService:
    """Orchestrates job persistence and LangGraph pipeline execution."""

    async def save_normalized_job(self, normalized: NormalizedJob) -> uuid.UUID:
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(Job).where(
                    Job.platform == normalized.platform,
                    Job.external_id == normalized.external_id,
                ).with_for_update()
            )
            row = existing.scalar_one_or_none()
            if row:
                updated = False
                listing_changed = False
                if normalized.platform == "kwork":
                    row.raw_data = {
                        **(row.raw_data or {}),
                        "listing_observed_at": normalized.raw_data.get("listing_observed_at"),
                    }
                    updated = True
                    incoming = normalized.client
                    if not incoming or not incoming.external_id or incoming.metadata.get("stats_source") != "kwork_buyer_block_v1":
                        if row.client_id is not None:
                            row.client_id = None
                            updated = True
                            listing_changed = True
                    else:
                        buyer = await _upsert_kwork_client(session, incoming)
                        listing_changed = row.client_id != buyer.id
                        row.client_id = buyer.id
                        updated = True
                elif normalized.client and row.client_id:
                    existing_client = await session.get(Client, row.client_id)
                    if existing_client:
                        existing_client.rating = normalized.client.rating
                        existing_client.reviews_count = normalized.client.reviews_count
                        existing_client.metadata_ = normalized.client.metadata
                        updated = True
                if normalized.platform == "kwork":
                    text_changed = row.title != normalized.title or row.description != normalized.description
                    for field in (
                        "title", "description", "budget_min", "budget_max", "budget_currency",
                        "skills", "deadline", "url",
                    ):
                        value = getattr(normalized, field)
                        if getattr(row, field) != value:
                            setattr(row, field, value)
                            updated = True
                            listing_changed = True
                    if text_changed:
                        row.raw_data = {**(row.raw_data or {}), "embedding_pending": True}
                    if listing_changed or not (row.raw_data or {}).get("listing_revision"):
                        row.raw_data = {**(row.raw_data or {}), "listing_revision": str(uuid.uuid4())}
                elif normalized.description and len(normalized.description) > len(
                    row.description or ""
                ):
                    row.description = normalized.description
                    updated = True
                if normalized.platform != "kwork" and normalized.budget_max and (
                    not row.budget_max or (row.budget_max < 50 and normalized.budget_max >= 50)
                ):
                    row.budget_min = normalized.budget_min
                    row.budget_max = normalized.budget_max
                    row.budget_currency = normalized.budget_currency
                    updated = True
                if updated:
                    await session.commit()
                    logger.info("JobPilot AI job updated", external_id=normalized.external_id)
                else:
                    logger.info(
                        "JobPilot AI job already exists", external_id=normalized.external_id
                    )
                return row.id

            client_id = None
            if normalized.platform == "kwork" and normalized.client and normalized.client.external_id:
                client_id = (await _upsert_kwork_client(session, normalized.client)).id
            elif normalized.client:
                external_id = normalized.client.external_id
                client = None
                if external_id:
                    client_result = await session.execute(
                        select(Client).where(
                            Client.platform == normalized.platform,
                            Client.external_id == external_id,
                        )
                    )
                    client = client_result.scalar_one_or_none()
                if not client:
                    client = Client(
                        platform=normalized.platform,
                        external_id=external_id,
                        name=normalized.client.name,
                        rating=normalized.client.rating,
                        reviews_count=normalized.client.reviews_count,
                        metadata_=normalized.client.metadata,
                    )
                    session.add(client)
                    await session.flush()
                else:
                    client.rating = normalized.client.rating
                    client.reviews_count = normalized.client.reviews_count
                    client.metadata_ = normalized.client.metadata
                client_id = client.id

            job = Job(
                platform=normalized.platform,
                external_id=normalized.external_id,
                title=normalized.title,
                description=normalized.description,
                budget_min=normalized.budget_min,
                budget_max=normalized.budget_max,
                budget_currency=normalized.budget_currency,
                skills=normalized.skills,
                deadline=normalized.deadline,
                url=normalized.url,
                client_id=client_id,
                raw_data={**normalized.raw_data, "embedding_pending": True, **(
                    {"listing_revision": str(uuid.uuid4())} if normalized.platform == "kwork" else {}
                )},
                status="new",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id
            job_text = f"{job.title} {job.description}"
            metadata = {"platform": job.platform, "title": job.title}

        try:
            memory = await asyncio.to_thread(get_memory_store)
            stored = await memory.store_job_embedding(str(job_id), job_text, metadata)
        except Exception as exc:
            logger.warning("Job embedding deferred", job_id=str(job_id), error=str(exc))
            stored = False

        if stored is True:
            async with AsyncSessionLocal() as session:
                row = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if row:
                    from app.services.embedding_index_signature import (
                        SIGNATURE_KEY,
                        embedding_index_signature,
                    )

                    raw = dict(row.raw_data or {})
                    raw["embedding_pending"] = f"{row.title} {row.description}" != job_text
                    if not raw["embedding_pending"]:
                        raw[SIGNATURE_KEY] = embedding_index_signature()
                    row.raw_data = raw
                    await session.commit()
        else:
            logger.info("Job embedding remains pending", job_id=str(job_id))
        return job_id

    async def is_job_sent(self, job_id: uuid.UUID) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Proposal.id)
                .where(
                    Proposal.job_id == job_id,
                    Proposal.status.in_(PROPOSAL_SUBMITTED_STATUSES),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def persist_pipeline_results(
        self,
        job_id: uuid.UUID,
        state: dict,
        *,
        claim_token: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        async with AsyncSessionLocal() as session:
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job:
                return None

            from app.services.job_processing import has_active_processing_claim

            if (claim_token is None or job.processing_claim_token != claim_token
                    or not has_active_processing_claim(job)):
                logger.info("Discarding pipeline result without current claim", job_id=str(job_id))
                return None

            if await delivery_blocks_mutation(session, job_id=job_id, include_confirmed=True):
                return None

            sent_proposal = await session.scalar(
                select(Proposal.id)
                .where(Proposal.job_id == job_id, Proposal.status.in_(PROPOSAL_SUBMITTED_STATUSES))
                .limit(1)
            )
            if job_is_submitted(job.status) or sent_proposal is not None:
                logger.info("JobPilot AI skip pipeline: job already sent", job_id=str(job_id))
                return None

            if not job_allows_pipeline_persist(job.status):
                logger.info(
                    "JobPilot AI skip pipeline: protected job status",
                    job_id=str(job_id),
                    status=job.status,
                )
                return None

            if state.get("pipeline_processing_error"):
                retryable = bool(state.get("processing_retryable", True))
                from app.services.job_processing import apply_processing_failure

                apply_processing_failure(
                    job, stage=state.get("processing_stage") or "pipeline",
                    retryable=retryable, settings=get_settings(),
                )
                job.relevance_reason = (
                    state.get("relevance_reason") or state.get("error") or "processing error"
                )
                error_kind = state.get("processing_error_kind")
                if error_kind:
                    job.raw_data = {
                        **(job.raw_data or {}),
                        "last_processing_error_kind": str(error_kind),
                    }
                await session.commit()
                return None

            job.is_relevant = state.get("is_relevant")
            job.relevance_reason = state.get("relevance_reason")
            if isinstance(state.get("opportunity_assessment"), dict):
                job.raw_data = {**(job.raw_data or {}), "opportunity_assessment": state["opportunity_assessment"]}
            job.score = state.get("score")
            job.score_breakdown = state.get("score_breakdown", {})
            threshold = get_settings().min_score_threshold
            if state.get("is_relevant") is False:
                from app.services.job_freshness import append_decision_audit

                append_decision_audit(
                    job,
                    action="filtered",
                    reason=str(state.get("relevance_reason") or "not relevant"),
                    source=str(state.get("filter_source") or "pipeline"),
                )
                _transition_job_status(job, JOB_STATUS_FILTERED)
            elif state.get("score") is not None and state.get("score") >= threshold:
                _transition_job_status(job, JOB_STATUS_SCORED)
            elif state.get("score") is not None:
                _transition_job_status(job, JOB_STATUS_FILTERED)
            else:
                _transition_job_status(job, JOB_STATUS_FILTERED)

            job.processing_claim_owner = None
            job.processing_claim_token = None
            job.processing_claimed_at = None
            job.processing_stage = None
            job.processing_error_type = None
            job.processing_next_attempt_at = None

            from app.services.pipeline_llm_ledger import persist_stage_log

            persist_stage_log(job, state)

            proposal_id = None
            content = state.get("proposal_content")
            if content:
                existing = await session.execute(
                    select(Proposal).where(
                        Proposal.job_id == job_id,
                        Proposal.status.in_(["pending_approval", "draft"]),
                    )
                )
                if existing.scalar_one_or_none():
                    await session.commit()
                    return None

                proposal = Proposal(
                    job_id=job_id,
                    content=content,
                    execution_plan=state.get("execution_plan"),
                    timeline=state.get("timeline"),
                    status="pending_approval",
                )
                session.add(proposal)
                await session.flush()
                proposal_id = proposal.id
                _transition_job_status(job, "pending_approval")
                settings = get_settings()
                try:
                    target_chat = int(settings.telegram_admin_chat_id)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Telegram review destination is not configured") from exc
                if not target_chat:
                    raise RuntimeError("Telegram review destination is not configured")
                if job.platform == "kwork":
                    from app.services.kwork_pause import is_kwork_paused
                    from app.services.opportunity_policy import kwork_job_assessment_rejection

                    if is_kwork_paused():
                        raise RuntimeError("Kwork is paused before review creation")
                    if await kwork_job_assessment_rejection(session, job):
                        raise RuntimeError("Kwork assessment is missing, ineligible or stale")
                from app.telegram.bot import queue_proposal_review

                await queue_proposal_review(session, job, proposal, target_chat)

            await session.commit()
            return proposal_id

    async def regenerate_proposal(self, job_id: uuid.UUID) -> uuid.UUID | None:
        """Re-run ProposalAgent for a job that has not been sent yet."""
        if await self.is_job_sent(job_id):
            return None

        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job or not job.is_relevant:
                return None
            if job.platform == "kwork":
                from app.services.kwork_pause import is_kwork_paused

                if is_kwork_paused():
                    logger.info(
                        "JobPilot AI skip proposal regeneration while Kwork paused",
                        job_id=str(job_id),
                    )
                    return None

        async with AsyncSessionLocal() as session:
            if await delivery_blocks_mutation(session, job_id=job_id, include_confirmed=True):
                return None
            original = await session.scalar(
                select(Proposal)
                .where(Proposal.job_id == job_id)
                .order_by(Proposal.created_at.desc(), Proposal.id.desc())
                .limit(1)
            )
            original_revision = (
                (original.id, original.version, original.content, original.status)
                if original
                else None
            )

        state = await self.job_to_state(job_id)
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job:
                return None
            state["score"] = job.score or 0
            state["is_relevant"] = bool(job.is_relevant)

        from app.agents.proposal_agent import ProposalAgent

        result = await ProposalAgent().run(state)
        content = result.get("proposal_content")
        if not content:
            logger.warning("JobPilot AI proposal regeneration empty", job_id=str(job_id))
            return None

        async with AsyncSessionLocal() as session:
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job:
                return None

            if job_is_submitted(job.status) or await delivery_blocks_mutation(
                session, job_id=job_id, include_confirmed=True
            ):
                return None

            proposal_result = await session.execute(
                select(Proposal)
                .where(Proposal.job_id == job_id)
                .order_by(Proposal.created_at.desc(), Proposal.id.desc())
                .limit(1)
                .with_for_update()
            )
            proposal = proposal_result.scalar_one_or_none()
            current_revision = (
                (proposal.id, proposal.version, proposal.content, proposal.status)
                if proposal
                else None
            )
            if current_revision != original_revision or (
                proposal and proposal_is_submitted(proposal.status)
            ):
                logger.info("Discarding stale proposal regeneration", job_id=str(job_id))
                return None
            if proposal:
                proposal.content = content
                proposal.execution_plan = result.get("execution_plan")
                proposal.timeline = result.get("timeline")
                proposal.version += 1
                proposal.status = "pending_approval"
                proposal.sent_at = None
            else:
                proposal = Proposal(
                    job_id=job_id,
                    content=content,
                    execution_plan=result.get("execution_plan"),
                    timeline=result.get("timeline"),
                    status="pending_approval",
                )
                session.add(proposal)

            _transition_job_status(job, "pending_approval")
            await session.flush()
            proposal_id = proposal.id
            await session.commit()
            logger.info(
                "JobPilot AI proposal regenerated",
                job_id=str(job_id),
                proposal_id=str(proposal_id),
                version=proposal.version,
            )
            return proposal_id

    async def supersede_telegram_pending(self, job_id: uuid.UUID) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TelegramPending).where(
                    TelegramPending.job_id == job_id,
                    TelegramPending.status == "pending",
                )
            )
            rows = result.scalars().all()
            for row in rows:
                row.status = "superseded"
            if rows:
                await session.commit()
            return len(rows)

    async def create_telegram_pending(
        self,
        job_id: uuid.UUID,
        proposal_id: uuid.UUID,
        chat_id: int,
        message_id: int | None = None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            pending = TelegramPending(
                job_id=job_id,
                proposal_id=proposal_id,
                chat_id=chat_id,
                message_id=message_id,
                status="pending",
            )
            session.add(pending)
            await session.commit()

    async def job_to_chat_state(
        self, job_id: uuid.UUID, *, client_message: str = "", **kwargs
    ) -> dict:
        from app.services.conversation_context_service import (
            build_chat_agent_state,
            finalize_chat_agent_state,
        )

        async with AsyncSessionLocal() as session:
            state = await build_chat_agent_state(
                session,
                job_id,
                client_message=client_message,
                **kwargs,
            )
        return await finalize_chat_agent_state(state)

    async def job_to_state(self, job_id: uuid.UUID) -> dict:
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job:
                return {}

            client_name = None
            client_rating = None
            client_reviews = 0
            client_metadata = {}
            if job.client_id:
                client = await session.get(Client, job.client_id)
                if client:
                    client_name = client.name
                    client_rating = float(client.rating) if client.rating else None
                    client_reviews = client.reviews_count
                    client_metadata = client.metadata_ or {}

            return {
                "job_id": str(job.id),
                "job_data": {
                    "id": str(job.id),
                    "platform": job.platform,
                    "external_id": job.external_id,
                    "title": job.title,
                    "description": job.description,
                    "budget_min": float(job.budget_min) if job.budget_min else None,
                    "budget_max": float(job.budget_max) if job.budget_max else None,
                    "budget_currency": job.budget_currency,
                    "skills": job.skills or [],
                    "deadline": job.deadline.isoformat() if job.deadline else None,
                    "url": job.url,
                    "client_name": client_name,
                    "client_id": str(job.client_id) if job.client_id else None,
                    "client_rating": client_rating,
                    "client_reviews": client_reviews,
                    "client_completed_paid_orders": client_metadata.get("completed_paid_orders"),
                    "client_projects_posted": client_metadata.get("projects_posted"),
                    "client_hire_percent": client_metadata.get("hire_percent"),
                    "client_stats_source": client_metadata.get("stats_source"),
                    "client_evidence_revision": client_metadata.get("_evidence_revision"),
                    "listing_revision": (job.raw_data or {}).get("listing_revision"),
                    "opportunity_assessment": (job.raw_data or {}).get("opportunity_assessment"),
                },
            }
