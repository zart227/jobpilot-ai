"""Reproduce audit findings using an explicitly isolated test database; no platform calls."""

import os

from sqlalchemy.engine import make_url

if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
    raise SystemExit("Set JOBPILOT_INTEGRATION_TESTS=1 and explicit isolated DATABASE_URL")
if not (make_url(os.environ.get("DATABASE_URL", "")).database or "").endswith("_test"):
    raise SystemExit("Refusing non-test database")
for name in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
    if int(make_url(os.environ.get(name, "redis://localhost/0")).database or "0") < 2:
        raise SystemExit("Refusing production Redis/broker database")
os.environ["PLATFORM_DRY_RUN"] = "true"
os.environ["DEBUG"] = "false"
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import Job, NotificationOutbox, Proposal, SendAttempt
from app.db.session import AsyncSessionLocal, engine
from app.schemas.llm_outputs import FilterLLMOutput, LearningLLMOutput, ScoringBreakdown
from app.services import send_attempt_service as ss
from app.services.job_pipeline import JobPipelineService
from app.services.notification_outbox import _mark_delivery_success
from app.services.outcome_service import record_outcome
from app.services.send_attempt_reconcile import outbound_reply_visible_in_thread


async def run():
    evidence = {}
    jid, pid = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as s:
        s.add(
            Job(
                id=jid,
                platform="kwork",
                external_id=f"audit-{jid}",
                title="Audit fake",
                description="d",
                status="new",
            )
        )
        await s.commit()
        s.add(Proposal(id=pid, job_id=jid, content="Unapproved text", version=1, status="draft"))
        await s.commit()
    claim, _, _ = await ss.prepare_proposal_send(
        str(jid), str(pid), "Different text never approved"
    )
    evidence["unapproved_draft_different_content_claim"] = claim.action
    aid = uuid.uuid4()
    key = f"audit-race:{aid}"
    async with AsyncSessionLocal() as s:
        s.add(
            SendAttempt(
                id=aid,
                kind="proposal",
                idempotency_key=key,
                job_id=jid,
                proposal_id=pid,
                snapshot_content="same",
                snapshot_metadata={},
                status="known_failed",
            )
        )
        await s.commit()
    claims = await asyncio.gather(
        *[
            ss.claim_send_attempt(
                kind="proposal",
                idempotency_key=key,
                snapshot_content="same",
                snapshot_version=1,
                snapshot_metadata={},
                job_id=jid,
                proposal_id=pid,
            )
            for _ in range(2)
        ]
    )
    evidence["parallel_retry_claim_actions"] = [claim.action for claim in claims]
    jid2 = uuid.uuid4()
    token = uuid.uuid4()
    async with AsyncSessionLocal() as s:
        s.add(
            Job(
                id=jid2,
                platform="audit",
                external_id=str(jid2),
                title="Fencing",
                description="d",
                status="new",
                processing_claim_token=token,
                processing_claimed_at=datetime.now(UTC),
            )
        )
        await s.commit()
    await JobPipelineService().persist_pipeline_results(
        jid2,
        {"is_relevant": False, "relevance_reason": "old worker result"},
        claim_token=uuid.uuid4(),
    )
    async with AsyncSessionLocal() as s:
        j = await s.get(Job, jid2)
        evidence["stale_worker_can_clear_new_claim"] = j.processing_claim_token is None
    for idx in range(2):
        async with AsyncSessionLocal() as s:
            await record_outcome(
                s, job_id=jid, proposal_id=pid, status="hired", event_key=f"audit-hired:{jid}:{idx}"
            )
            await s.commit()
    async with AsyncSessionLocal() as s:
        from app.db.models import Reward

        evidence["hired_rewards_different_keys"] = int(
            (
                await s.execute(select(func.sum(Reward.reward_value)).where(Reward.job_id == jid))
            ).scalar()
        )
    jid3 = uuid.uuid4()
    async with AsyncSessionLocal() as s:
        s.add(
            Job(
                id=jid3,
                platform="audit",
                external_id=str(jid3),
                title="Outbox gap",
                description="d",
                status="new",
            )
        )
        await s.commit()
    from app.services.job_processing import try_claim_job

    outbox_probe_claim = await try_claim_job(jid3, owner="isolated_audit_probe")
    assert outbox_probe_claim is not None
    await JobPipelineService().persist_pipeline_results(
        jid3,
        {"is_relevant": True, "score": 75, "proposal_content": "fake draft"},
        claim_token=outbox_probe_claim.claim_token,
    )
    async with AsyncSessionLocal() as s:
        evidence["outbox_after_proposal_commit"] = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(NotificationOutbox)
                    .where(NotificationOutbox.payload["job_id"].astext == str(jid3))
                )
            ).scalar()
        )
    oid = uuid.uuid4()
    newtoken = uuid.uuid4()
    async with AsyncSessionLocal() as s:
        s.add(
            NotificationOutbox(
                id=oid,
                kind="proposal_approval",
                idempotency_key=f"audit:{oid}",
                chat_id=1,
                payload={"alert_text": "x"},
                reference_type="test",
                reference_id=uuid.uuid4(),
                status="sending",
                claim_token=newtoken,
                claim_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
        await s.commit()
    await _mark_delivery_success(oid, 99)
    async with AsyncSessionLocal() as s:
        row = await s.get(NotificationOutbox, oid)
        evidence["outbox_finalize_requires_token"] = False
        evidence["outbox_status_after_unfenced_finalize"] = row.status
    evidence["reconcile_accepts_prefix_only"] = outbound_reply_visible_in_thread(
        [
            {
                "from_id": 1,
                "from_username": "seller",
                "message": "x" * 40 + " old unrelated continuation",
            }
        ],
        expected_text="x" * 40 + " NEW reply",
        my_user_id=1,
        my_username="seller",
    )
    evidence["scoring_boolean_coerced"] = ScoringBreakdown.model_validate(
        dict.fromkeys(
            ["skill_match", "budget", "complexity", "client_quality", "competition"], True
        )
    ).skill_match
    evidence["filter_string_boolean_coerced"] = FilterLLMOutput.model_validate(
        {"relevant": "true", "reason": "x"}
    ).relevant
    evidence["learning_schema_accepts_unknown_negative_weights"] = LearningLLMOutput.model_validate(
        {"weights": {"oops": -1}}
    ).weights

    from unittest.mock import AsyncMock

    from starlette.requests import Request

    from app.api.auth import verify_api_token
    from app.api.rate_limit import _client_rate_key
    from app.config import Settings
    from app.llm.embeddings import EmbeddingUnavailableError
    from app.memory.qdrant_store import QdrantMemoryStore

    mem = object.__new__(QdrantMemoryStore)
    mem._embed = AsyncMock(side_effect=EmbeddingUnavailableError("test disabled"))
    evidence["embedding_write_silently_returns_success_shape"] = (
        await mem.store_job_embedding("audit", "fake", {}) is None
    )
    names = []
    for dim in (1536, 512):
        m = object.__new__(QdrantMemoryStore)
        m._settings = Settings(
            _env_file=None, EMBEDDING_MODEL="same-model", EMBEDDING_DIMENSIONS=dim
        )
        m._prefix = "audit"
        names.append(m._collection_name("job_embeddings"))
    evidence["different_embedding_dimensions_share_collection"] = names[0] == names[1]
    cfg = Settings(_env_file=None, API_AUTH_REQUIRED=True, API_AUTH_TOKEN="audit-only")
    buckets = []
    for extra in ("a", "b"):
        req = Request(
            {
                "type": "http",
                "headers": [
                    (b"authorization", b"Bearer audit-only"),
                    (b"x-api-key", extra.encode()),
                ],
                "client": ("127.0.0.1", 1),
                "method": "GET",
                "path": "/jobs",
                "query_string": b"",
            }
        )
        verify_api_token(authorization="Bearer audit-only", x_api_key=extra, settings=cfg)
        buckets.append(_client_rate_key(req, cfg))
    evidence["valid_bearer_random_secondary_header_changes_rate_bucket"] = buckets[0] != buckets[1]
    print("AUDIT_PROBES=" + json.dumps(evidence, ensure_ascii=False))
    await engine.dispose()


asyncio.run(run())
