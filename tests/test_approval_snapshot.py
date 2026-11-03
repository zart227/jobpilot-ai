import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


@pytest.fixture(autouse=True)
def fixed_discount(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("KWORK_OFFER_DISCOUNT_PERCENT", "15")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def seed():
    from conftest import seed_test_kwork_eligibility

    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import capture_proposal_card

    jid, pid, card = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        job = Job(
            id=jid,
            platform="kwork",
            external_id=str(jid),
            title="Fake",
            description="Fake",
            budget_min=10000,
            budget_max=15000,
            budget_currency="RUB",
        )
        session.add(job)
        await session.flush()
        await seed_test_kwork_eligibility(session, jid, price=8500)
        proposal = Proposal(
            id=pid, job_id=jid, content="Shown text", version=1, status="pending_approval"
        )
        session.add(proposal)
        await session.flush()
        snapshot = await capture_proposal_card(session, card, job, proposal)
        await session.commit()
        return jid, pid, card, snapshot.payload


@pytest.mark.parametrize("field,value", [("content", "new text"), ("version", 2)])
async def test_text_or_version_change_invalidates_card(field, value):
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import proposal_card_is_current

    jid, pid, card, payload = await seed()
    async with AsyncSessionLocal() as session:
        job, proposal = await session.get(Job, jid), await session.get(Proposal, pid)
        assert await proposal_card_is_current(session, card, job, proposal)
        setattr(proposal, field, value)
        assert not await proposal_card_is_current(session, card, job, proposal)
        assert payload["content"] == "Shown text"


@pytest.mark.parametrize(
    "field,value",
    [("budget_min", 20000), ("url", "https://example.test/changed"), ("budget_currency", "USD")],
)
async def test_price_url_and_legacy_cards_are_bound(field, value):
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import proposal_card_is_current

    jid, pid, card, payload = await seed()
    assert payload["duration_days"] == 1
    assert payload["offer_price"] == 8500
    async with AsyncSessionLocal() as session:
        job, proposal = await session.get(Job, jid), await session.get(Proposal, pid)
        assert not await proposal_card_is_current(session, uuid.uuid4(), job, proposal)
        setattr(job, field, value)
        assert not await proposal_card_is_current(session, card, job, proposal)


async def test_snapshot_update_is_rejected_by_database():
    from app.db.session import AsyncSessionLocal

    _, _, card, _ = await seed()
    async with AsyncSessionLocal() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text(
                    "UPDATE proposal_approval_snapshots SET payload = '{}'::jsonb WHERE pending_id = :id"
                ),
                {"id": card},
            )
        await session.rollback()


async def test_legacy_truncated_presentation_does_not_authorize_full_review():
    from app.db.models import Job, Proposal, ProposalApprovalSnapshot
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import proposal_card_is_current

    jid, pid, _, payload = await seed()
    legacy_card = uuid.uuid4()
    payload = dict(payload)
    payload.pop("review_format")
    async with AsyncSessionLocal() as session:
        session.add(ProposalApprovalSnapshot(pending_id=legacy_card, payload=payload))
        await session.commit()
        assert not await proposal_card_is_current(
            session, legacy_card, await session.get(Job, jid), await session.get(Proposal, pid)
        )
