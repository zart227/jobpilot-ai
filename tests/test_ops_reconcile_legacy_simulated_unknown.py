"""Integration tests for legacy simulated unknown_result operator reconcile."""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated DB required",
)


@pytest.mark.asyncio
async def test_legacy_simulated_unknown_reconciles_to_confirmed_dry_run(monkeypatch):
    from app.config import get_settings
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_KIND_PROPOSAL, ATTEMPT_UNKNOWN_RESULT
    from app.db.session import AsyncSessionLocal
    from scripts import ops_reconcile_legacy_simulated_unknown as mod

    monkeypatch.setenv("PLATFORM_DRY_RUN", "true")
    get_settings.cache_clear()

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"legacy-{job_id.hex[:8]}",
                title="Legacy",
                description="d",
                url="https://kwork.ru/projects/1/view",
                status="approved",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Legacy offer text long enough",
                status="approved",
                version=1,
            )
        )
        session.add(
            SendAttempt(
                id=attempt_id,
                kind=ATTEMPT_KIND_PROPOSAL,
                idempotency_key=f"legacy:{attempt_id}",
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content="Legacy offer text long enough",
                snapshot_version=1,
                status=ATTEMPT_UNKNOWN_RESULT,
                last_error=f"{mod.LEGACY_ERROR}",
                platform_response={},
            )
        )
        await session.commit()

    monkeypatch.setenv("JOBPILOT_OPERATOR_LEGACY_SIMULATED_UNKNOWN", "1")
    outcome = await mod._apply_one(attempt_id)
    assert outcome == "confirmed"

    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, attempt_id)
        proposal = await session.get(Proposal, proposal_id)
        assert attempt.status == "confirmed_sent"
        assert (attempt.platform_response or {}).get("dry_run") is True
        assert proposal.status == "simulated_sent"
