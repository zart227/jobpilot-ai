import os
import uuid
from datetime import UTC, datetime

import pytest

from app.services.send_attempt_reconcile import outbound_reply_visible_in_thread

pytestmark_db = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


def test_outbound_reply_visible_matches_freelancer_message() -> None:
    messages = [
        {"from_id": 2, "message": "Question?"},
        {"from_id": 1, "message": "Our answer here", "id": 10, "time": 1001},
    ]
    assert outbound_reply_visible_in_thread(
        messages,
        expected_text="Our answer here",
        my_user_id=1,
        my_username="seller",
        sent_after=datetime.fromtimestamp(1000, UTC),
    )


def test_outbound_reply_ignores_client_copy() -> None:
    messages = [{"from_id": 2, "message": "Our answer here"}]
    assert not outbound_reply_visible_in_thread(
        messages,
        expected_text="Our answer here",
        my_user_id=1,
        my_username="seller",
        sent_after=datetime.fromtimestamp(1000, UTC),
    )


@pytest.mark.asyncio
@pytestmark_db
async def test_reconcile_dry_run_proposal_confirms_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_DRY_RUN", "true")
    from app.config import Settings, get_settings

    get_settings.cache_clear()

    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_KIND_PROPOSAL, ATTEMPT_UNKNOWN_RESULT
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_reconcile import reconcile_send_attempt

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    external_id = f"kwork-reconcile-{job_id.hex[:12]}"
    idempotency_key = f"test:reconcile:{attempt_id}"

    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="T",
                description="d",
                url="https://kwork.ru/projects/99/view",
                status="approved",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Offer",
                status="approved",
                version=1,
            )
        )
        session.add(
            SendAttempt(
                id=attempt_id,
                kind=ATTEMPT_KIND_PROPOSAL,
                idempotency_key=idempotency_key,
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content="Offer",
                snapshot_version=1,
                status=ATTEMPT_UNKNOWN_RESULT,
                platform_response={"dry_run": True},
            )
        )
        await session.commit()

    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=True)
    outcome = await reconcile_send_attempt(attempt_id, settings=settings)
    assert outcome == "confirmed"

    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, attempt_id)
        proposal = await session.get(Proposal, proposal_id)
        assert attempt is not None and attempt.status == "confirmed_sent"
        assert proposal is not None and proposal.status == "simulated_sent"

    get_settings.cache_clear()


@pytest.mark.asyncio
@pytestmark_db
async def test_live_kwork_proposal_reconcile_stays_inconclusive_without_payload_receipt() -> None:
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_KIND_PROPOSAL, ATTEMPT_UNKNOWN_RESULT
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_reconcile import reconcile_send_attempt

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"kwork-live-{job_id.hex[:8]}",
                title="T",
                description="d",
                url="https://kwork.ru/projects/1/view",
                status="approved",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Offer text long enough",
                status="approved",
                version=1,
            )
        )
        session.add(
            SendAttempt(
                id=attempt_id,
                kind=ATTEMPT_KIND_PROPOSAL,
                idempotency_key=f"live:{attempt_id}",
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content="Offer text long enough",
                status=ATTEMPT_UNKNOWN_RESULT,
                platform_response={"dry_run": False},
            )
        )
        await session.commit()

    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=False)
    outcome = await reconcile_send_attempt(attempt_id, settings=settings)
    assert outcome == "inconclusive"

    get_settings.cache_clear()


@pytest.mark.asyncio
@pytestmark_db
async def test_commit_failure_marks_unknown_not_blind_retry() -> None:
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_KIND_PROPOSAL, ATTEMPT_SENDING
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import (
        claim_send_attempt,
        mark_unknown_after_commit_failure,
        proposal_idempotency_key,
    )

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    token = uuid.uuid4()
    external_id = f"kwork-unknown-{job_id.hex[:12]}"

    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="T",
                description="d",
                status="approved",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Hi",
                status="approved",
                version=1,
            )
        )
        session.add(
            SendAttempt(
                kind=ATTEMPT_KIND_PROPOSAL,
                idempotency_key=proposal_idempotency_key(proposal_id, 1, "Hi"),
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content="Hi",
                snapshot_version=1,
                status=ATTEMPT_SENDING,
                claim_token=token,
                claim_owner="test",
            )
        )
        await session.commit()
        from sqlalchemy import select

        attempt = (
            await session.execute(select(SendAttempt).where(SendAttempt.proposal_id == proposal_id))
        ).scalar_one()

    await mark_unknown_after_commit_failure(
        attempt.id,
        token,
        error="commit_failed_after_platform_success: simulated",
        platform_response={"simulated": True},
    )

    claim = await claim_send_attempt(
        kind=ATTEMPT_KIND_PROPOSAL,
        idempotency_key=proposal_idempotency_key(proposal_id, 1, "Hi"),
        snapshot_content="Hi",
        snapshot_version=1,
        snapshot_metadata={},
        job_id=job_id,
        proposal_id=proposal_id,
    )
    assert claim.action == "blocked"
    assert claim.reason == "unknown_result"


@pytest.mark.parametrize(
    "problem", ["prefix", "old", "unknown_sender", "missing_id", "missing_boundary", "nan_time"]
)
def test_reply_reconcile_rejects_ambiguous_evidence(problem):
    text = "a" * 40 + " exact complete reply"
    message = {"from_id": 1, "message": text, "MID": "external-42", "time": 1001}
    boundary = datetime.fromtimestamp(1000, UTC)
    if problem == "prefix":
        message["message"] = "a" * 40 + " other reply"
    elif problem == "old":
        message["time"] = 999
    elif problem == "unknown_sender":
        message.pop("from_id")
    elif problem == "missing_id":
        message.pop("MID")
    elif problem == "missing_boundary":
        boundary = None
    else:
        message["time"] = float("nan")
    assert not outbound_reply_visible_in_thread(
        [message], expected_text=text, my_user_id=1, my_username="seller", sent_after=boundary
    )
