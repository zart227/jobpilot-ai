import uuid
from datetime import datetime

import structlog
from sqlalchemy import select

from app.db.models import Client, Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.memory.qdrant_store import get_memory_store
from app.schemas.job import NormalizedJob

logger = structlog.get_logger(__name__)


class JobPipelineService:
    """Orchestrates job persistence and LangGraph pipeline execution."""

    async def save_normalized_job(self, normalized: NormalizedJob) -> uuid.UUID:
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(Job).where(
                    Job.platform == normalized.platform,
                    Job.external_id == normalized.external_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                updated = False
                if normalized.description and len(normalized.description) > len(row.description or ""):
                    row.description = normalized.description
                    updated = True
                if normalized.budget_max and (
                    not row.budget_max
                    or (row.budget_max < 50 and normalized.budget_max >= 50)
                ):
                    row.budget_min = normalized.budget_min
                    row.budget_max = normalized.budget_max
                    row.budget_currency = normalized.budget_currency
                    updated = True
                if updated:
                    await session.commit()
                    logger.info("JobPilot AI job updated", external_id=normalized.external_id)
                else:
                    logger.info("JobPilot AI job already exists", external_id=normalized.external_id)
                return row.id

            client_id = None
            if normalized.client:
                client_result = await session.execute(
                    select(Client).where(
                        Client.platform == normalized.platform,
                        Client.external_id == normalized.client.external_id,
                    )
                )
                client = client_result.scalar_one_or_none()
                if not client:
                    client = Client(
                        platform=normalized.platform,
                        external_id=normalized.client.external_id,
                        name=normalized.client.name,
                        rating=normalized.client.rating,
                        reviews_count=normalized.client.reviews_count,
                        metadata_=normalized.client.metadata,
                    )
                    session.add(client)
                    await session.flush()
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
                raw_data=normalized.raw_data,
                status="new",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            memory = get_memory_store()
            job_text = f"{job.title} {job.description}"
            await memory.store_job_embedding(
                str(job.id),
                job_text,
                {"platform": job.platform, "title": job.title},
            )

            return job.id

    async def is_job_sent(self, job_id: uuid.UUID) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Proposal.id).where(
                    Proposal.job_id == job_id,
                    Proposal.status == "sent",
                ).limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def persist_pipeline_results(
        self,
        job_id: uuid.UUID,
        state: dict,
    ) -> uuid.UUID | None:
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job:
                return None

            if job.status == "sent" or await self.is_job_sent(job_id):
                logger.info("JobPilot AI skip pipeline: job already sent", job_id=str(job_id))
                return None

            job.is_relevant = state.get("is_relevant")
            job.relevance_reason = state.get("relevance_reason")
            job.score = state.get("score")
            job.score_breakdown = state.get("score_breakdown", {})
            job.status = "scored" if state.get("score") else "filtered"

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
                job.status = "pending_approval"

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
            job = await session.get(Job, job_id)
            if not job:
                return None

            proposal_result = await session.execute(
                select(Proposal)
                .where(Proposal.job_id == job_id)
                .order_by(Proposal.created_at.desc())
                .limit(1)
            )
            proposal = proposal_result.scalar_one_or_none()
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

            job.status = "pending_approval"
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

    async def job_to_state(self, job_id: uuid.UUID) -> dict:
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job:
                return {}

            client_name = None
            client_rating = None
            client_reviews = 0
            if job.client_id:
                client = await session.get(Client, job.client_id)
                if client:
                    client_name = client.name
                    client_rating = float(client.rating) if client.rating else None
                    client_reviews = client.reviews_count

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
                    "client_rating": client_rating,
                    "client_reviews": client_reviews,
                },
            }
