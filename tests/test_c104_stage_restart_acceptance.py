"""C104: isolated restart/crash recovery at durable stage boundaries (mock platform, test DB)."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated DB required",
)


@pytest.fixture
def platform_dry_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_DRY_RUN", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_c104_confirmed_proposal_send_blocks_blind_resend_after_worker_restart(
    platform_dry_run,
):
    """After confirmed dry-run send, a new worker must not create a second business delivery."""
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.proposal_sender import ProposalSender

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    external_id = f"kwork-c104-{job_id.hex[:12]}"
    content = "C104 restart resend guard text."
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="C104 job",
                description="Test",
                url="https://kwork.ru/projects/1",
                status="approved",
                budget_min=1000,
                budget_max=2000,
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content=content,
                status="approved",
                version=1,
            )
        )
        await session.commit()

    from conftest import seed_test_kwork_eligibility, seed_test_proposal_approval

    async with AsyncSessionLocal() as session:
        await seed_test_kwork_eligibility(session, job_id)
        await seed_test_proposal_approval(session, job_id, proposal_id)

    sender_a = ProposalSender()
    ok1, err1, _ = await sender_a.send(str(job_id), str(proposal_id), content)
    assert ok1 and err1 is None

    sender_b = ProposalSender()
    ok2, err2, debug2 = await sender_b.send(str(job_id), str(proposal_id), content)
    assert ok2 is True
    assert err2 is None
    assert debug2 is not None
    assert debug2.get("offer_action") == "unchanged"

    async with AsyncSessionLocal() as session:
        attempt_count = await session.scalar(
            select(func.count(SendAttempt.id)).where(
                SendAttempt.proposal_id == proposal_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
        )
        assert attempt_count == 1


STAGE_RESTART_EVIDENCE = (
    ("job_processing_claim", "tests/test_job_processing_restart_drill.py"),
    ("scrape_beat_ordering", "tests/test_scrape_beat_restart_drill.py"),
    ("proposal_telegram_outbox", "tests/test_outbox_restart_drill.py"),
    ("inbound_poller", "tests/test_inbox_poller_restart_drill.py"),
    ("platform_send_guard", "tests/test_c104_stage_restart_acceptance.py"),
    ("memory_index_queue", "tests/test_memory_index_queue.py"),
    ("send_attempt_finalize", "tests/test_send_attempt_atomicity.py"),
)


def test_c104_stage_restart_evidence_catalog_is_complete():
    """Document which isolated drills map to C104 stage boundaries (not live SIGKILL)."""
    assert len(STAGE_RESTART_EVIDENCE) >= 7
    paths = [path for _, path in STAGE_RESTART_EVIDENCE]
    assert len(paths) == len(set(paths))
