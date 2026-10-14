import uuid

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __product_name__, __version__
from app.agents.chat_agent import ChatAgent
from app.agents.learning_agent import LearningAgent
from app.api.auth import CHAT_MESSAGE_MAX_CHARS, clamp_api_limit, verify_api_token
from app.api.rate_limit import ApiRateLimitMiddleware
from app.celery_app import celery_app  # noqa: F401 — init Celery before task imports
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
app.add_middleware(ApiRateLimitMiddleware)


@app.get("/health")
async def health() -> dict:
    """Liveness: process is up (no dependency checks)."""
    return {
        "status": "ok",
        "product": __product_name__,
        "version": __version__,
    }


@app.get("/ready")
async def ready(response: Response) -> dict:
    """Readiness: required deps up; Qdrant optional (degraded)."""
    from app.services.health_checks import gather_readiness

    payload = await gather_readiness()
    if not payload.get("ready"):
        response.status_code = 503
    return payload


@app.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    status: str | None = None,
    limit: int = Query(50, ge=1),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_token),
) -> list[Job]:
    limit = clamp_api_limit(limit)
    query = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status:
        query = query.where(Job.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_token),
) -> Job:
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/jobs/{job_id}/process")
async def trigger_job_processing(
    job_id: uuid.UUID,
    _: None = Depends(verify_api_token),
) -> dict:
    task = process_single_job.delay(str(job_id))
    return {"task_id": task.id, "job_id": str(job_id)}


@app.post("/scrape/trigger")
async def trigger_scrape(_: None = Depends(verify_api_token)) -> dict:
    task = run_all_scrapers.delay()
    return {"task_id": task.id, "message": "JobPilot AI scrape triggered"}


@app.post("/jobs/reprocess-pending")
async def trigger_reprocess_pending(_: None = Depends(verify_api_token)) -> dict:
    task = reprocess_pending_jobs.delay()
    return {"task_id": task.id, "message": "Reprocessing pending jobs"}


@app.post("/chat", response_model=ChatResponse)
async def handle_client_chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_token),
) -> ChatResponse:
    if len(request.message) > CHAT_MESSAGE_MAX_CHARS:
        raise HTTPException(status_code=400, detail="Message too long")
    job = await db.get(Job, request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.db.models import Interaction
    from app.services.conversation_context_service import build_chat_agent_state

    state = await build_chat_agent_state(
        db,
        request.job_id,
        client_message=request.message,
        channel="api",
    )
    if not state:
        raise HTTPException(status_code=404, detail="Job not found")

    agent = ChatAgent()
    result = await agent.run(state)

    proposal_id = state.get("proposal_id")
    db.add(
        Interaction(
            job_id=request.job_id,
            proposal_id=uuid.UUID(str(proposal_id)) if proposal_id else None,
            direction="inbound",
            channel="api",
            message=request.message,
            intent=str(result.get("chat_intent") or "") or None,
            metadata_={
                "draft_reply": result.get("chat_reply", ""),
                "source": "api_chat",
            },
        )
    )
    await db.commit()

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
    _: None = Depends(verify_api_token),
) -> dict:
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    proposal = None
    if body.proposal_id:
        proposal = await db.get(Proposal, body.proposal_id)
        if not proposal or proposal.job_id != job_id:
            raise HTTPException(status_code=400, detail="proposal_id does not belong to job")
    else:
        proposal_result = await db.execute(
            select(Proposal)
            .where(Proposal.job_id == job_id)
            .order_by(Proposal.created_at.desc())
            .limit(1)
        )
        proposal = proposal_result.scalar_one_or_none()

    from app.services.outcome_service import record_outcome_and_learn

    try:
        result = await record_outcome_and_learn(
            db,
            job_id=job_id,
            proposal_id=proposal.id if proposal else None,
            status=body.status,
            notes=body.notes,
            event_key=body.event_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "job_id": str(job_id),
        "status": result.status,
        "reward": result.reward,
        "duplicate": result.duplicate,
        "event_key": result.event_key,
    }


@app.post("/send-attempts/{attempt_id}/reconcile")
async def reconcile_send_attempt_endpoint(
    attempt_id: uuid.UUID,
    _: None = Depends(verify_api_token),
) -> dict:
    from app.services.send_attempt_reconcile import reconcile_send_attempt

    outcome = await reconcile_send_attempt(attempt_id)
    return {"attempt_id": str(attempt_id), "outcome": outcome}


@app.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_token),
) -> dict:
    from app.services.ops_metrics import collect_ops_metrics

    jobs_count = await db.execute(select(Job))
    outcomes = await db.execute(select(Outcome))
    reward_system = RewardSystem()
    ops = await collect_ops_metrics(db)

    return {
        "product": __product_name__,
        "total_jobs": len(jobs_count.scalars().all()),
        "total_outcomes": len(outcomes.scalars().all()),
        "total_rewards": await reward_system.get_total_rewards(),
        "ops": ops,
    }
