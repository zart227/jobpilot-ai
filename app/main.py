import uuid

import structlog
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __product_name__, __version__
from app.agents.chat_agent import ChatAgent
from app.agents.learning_agent import LearningAgent
from app.celery_app import celery_app
from app.config import get_settings
from app.db.models import Job, Outcome, Proposal
from app.db.session import get_db
from app.schemas.job import ChatRequest, ChatResponse, JobResponse, OutcomeRequest
from app.services.reward_system import RewardSystem
from app.tasks.scrape_tasks import process_single_job, reprocess_pending_jobs, run_all_scrapers

logger = structlog.get_logger(__name__)
settings = get_settings()

app = FastAPI(
    title=__product_name__,
    description="AI-powered freelance automation agent",
    version=__version__,
)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "product": __product_name__,
        "version": __version__,
    }


@app.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[Job]:
    query = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status:
        query = query.where(Job.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Job:
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/jobs/{job_id}/process")
async def trigger_job_processing(job_id: uuid.UUID) -> dict:
    task = process_single_job.delay(str(job_id))
    return {"task_id": task.id, "job_id": str(job_id)}


@app.post("/scrape/trigger")
async def trigger_scrape() -> dict:
    task = run_all_scrapers.delay()
    return {"task_id": task.id, "message": "JobPilot AI scrape triggered"}


@app.post("/jobs/reprocess-pending")
async def trigger_reprocess_pending() -> dict:
    task = reprocess_pending_jobs.delay()
    return {"task_id": task.id, "message": "Reprocessing pending jobs"}


@app.post("/chat", response_model=ChatResponse)
async def handle_client_chat(request: ChatRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    job = await db.get(Job, request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.services.job_pipeline import JobPipelineService

    pipeline = JobPipelineService()
    state = await pipeline.job_to_state(request.job_id)
    state["client_message"] = request.message

    agent = ChatAgent()
    result = await agent.run(state)

    learning = LearningAgent()
    state.update(result)
    if result.get("chat_intent") == "acceptance":
        state["outcome_status"] = "replied"
    await learning.run(state)

    return ChatResponse(intent=result.get("chat_intent", "other"), reply=result.get("chat_reply", ""))


@app.post("/outcomes/{job_id}")
async def record_outcome(
    job_id: uuid.UUID,
    body: OutcomeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    proposal_result = await db.execute(
        select(Proposal).where(Proposal.job_id == job_id).order_by(Proposal.created_at.desc()).limit(1)
    )
    proposal = proposal_result.scalar_one_or_none()

    reward_system = RewardSystem()
    reward = await reward_system.record_outcome(
        job_id,
        proposal.id if proposal else None,
        body.status,
        body.notes,
    )

    learning = LearningAgent()
    from app.services.job_pipeline import JobPipelineService

    pipeline = JobPipelineService()
    state = await pipeline.job_to_state(job_id)
    state["outcome_status"] = body.status
    await learning.run(state)

    return {"job_id": str(job_id), "status": body.status, "reward": reward}


@app.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)) -> dict:
    jobs_count = await db.execute(select(Job))
    outcomes = await db.execute(select(Outcome))
    reward_system = RewardSystem()

    return {
        "product": __product_name__,
        "total_jobs": len(jobs_count.scalars().all()),
        "total_outcomes": len(outcomes.scalars().all()),
        "total_rewards": await reward_system.get_total_rewards(),
    }
