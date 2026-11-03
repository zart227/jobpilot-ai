"""A missing exact external receipt must not authorize a repeat or confirmed send."""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_submit_exception_never_tries_another_proxy(monkeypatch):
    from app.config import Settings
    from app.services import kwork_browser
    from app.utils import proxy

    settings = Settings(_env_file=None, KWORK_STORAGE_STATE="")
    monkeypatch.setattr(kwork_browser, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy, "get_proxy_candidates", lambda *args: ["http://proxy.invalid:1"])
    monkeypatch.setattr(proxy, "mark_proxy_failed", lambda *args: None)
    browser = AsyncMock()
    monkeypatch.setattr(kwork_browser, "launch_browser", AsyncMock(return_value=(object(), browser, object())))
    monkeypatch.setattr(kwork_browser, "login", AsyncMock(return_value=True))
    submit = AsyncMock(side_effect=RuntimeError("browser crashed after request"))
    monkeypatch.setattr(kwork_browser, "submit_offer", submit)
    monkeypatch.setattr(kwork_browser, "close_browser", AsyncMock())
    monkeypatch.setattr(kwork_browser, "_save_submission_content", lambda *args: None)
    monkeypatch.setattr(kwork_browser, "_save_debug_context", AsyncMock(return_value=None))
    ok, _, debug = await kwork_browser._send_kwork_offer_locked(
        "https://example.invalid/projects/1", "x" * 150, offer_price=500, duration_days=7
    )
    assert not ok and debug["ambiguous"] is True
    assert submit.await_count == 1


@pytest.mark.asyncio
async def test_post_click_rejection_without_exact_receipt_is_ambiguous(monkeypatch):
    from app.config import Settings
    from app.services import kwork_browser
    from app.utils import proxy

    monkeypatch.setattr(kwork_browser, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(proxy, "get_proxy_candidates", lambda *args: [])
    monkeypatch.setattr(kwork_browser, "launch_browser", AsyncMock(return_value=(object(), object(), object())))
    monkeypatch.setattr(kwork_browser, "login", AsyncMock(return_value=True))
    monkeypatch.setattr(kwork_browser, "submit_offer", AsyncMock(return_value=(
        False, "still on form", {"steps": ["нажатие «Предложить»"], "error_label": "stayed_on_form"}
    )))
    monkeypatch.setattr(kwork_browser, "close_browser", AsyncMock())
    ok, _, debug = await kwork_browser._send_kwork_offer_locked(
        "https://example.invalid/projects/1", "x" * 150, offer_price=500, duration_days=7
    )
    assert not ok and debug["ambiguous"] is True


@pytest.mark.asyncio
async def test_generic_kwork_success_is_not_payload_specific_confirmation(monkeypatch):
    from app.config import Settings
    from app.services import proposal_sender

    monkeypatch.setattr(proposal_sender, "get_settings", lambda: Settings(_env_file=None, PLATFORM_DRY_RUN=False))
    monkeypatch.setattr(proposal_sender, "is_kwork_paused", lambda: False)
    monkeypatch.setattr(proposal_sender, "send_kwork_offer", AsyncMock(return_value=(True, None, {"offer_action": "created"})))
    job = SimpleNamespace(platform="kwork", title="Fake", url="https://example.invalid", budget_min=1000, budget_max=2000)
    content = "x" * 150
    success, _, debug = await proposal_sender.ProposalSender()._send_to_platform(
        job, None, content, approved_payload={"content": content, "offer_price": 500, "duration_days": 7}
    )
    assert not success and debug["ambiguous"] is True
    assert debug["dry_run"] is False


@pytest.mark.asyncio
async def test_proposal_propagates_ambiguous_result_to_finalization(monkeypatch):
    from app.services import proposal_sender

    attempt_id, token = uuid.uuid4(), uuid.uuid4()
    claim = SimpleNamespace(action="proceed", attempt_id=attempt_id, claim_token=token,
                            approved_payload={"content": "text"})
    job = SimpleNamespace(platform="kwork", title="Fake", external_id="1", url="https://example.invalid")
    monkeypatch.setattr(proposal_sender, "prepare_proposal_send", AsyncMock(return_value=(claim, job, object())))
    finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(proposal_sender, "finalize_send_attempt", finalize)
    sender = proposal_sender.ProposalSender()
    monkeypatch.setattr(sender, "_send_to_platform", AsyncMock(return_value=(False, "unverified", {"ambiguous": True})))
    monkeypatch.setattr(sender, "_handle_send_failure", AsyncMock())
    ok, _, _ = await sender.send(str(uuid.uuid4()), str(uuid.uuid4()), "text")
    assert not ok
    assert finalize.await_args.kwargs["ambiguous"] is True
    sender._handle_send_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbox_arbitrary_send_exception_is_ambiguous(monkeypatch):
    from app.config import Settings
    from app.services import kwork_inbox

    pending_id = uuid.uuid4()
    claim = SimpleNamespace(
        action="proceed", attempt_id=uuid.uuid4(), claim_token=uuid.uuid4(),
        approved_payload={"user_id": 5, "username": "client", "content": "reply",
                          "job_id": None, "proposal_id": None, "interaction_id": None},
    )
    monkeypatch.setattr(kwork_inbox, "prepare_inbox_reply_send", AsyncMock(return_value=claim))
    api = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("connection dropped")),
                          close=AsyncMock(), mark_read=AsyncMock())
    monkeypatch.setattr(kwork_inbox, "create_kwork_api", AsyncMock(return_value=api))
    finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(kwork_inbox, "finalize_send_attempt", finalize)
    ok, _ = await kwork_inbox.send_kwork_inbox_reply(
        user_id=5, username="client", reply_text="reply", pending_id=pending_id,
        settings=Settings(_env_file=None, PLATFORM_DRY_RUN=False),
    )
    assert not ok
    assert finalize.await_args.kwargs["ambiguous"] is True
    api.mark_read.assert_not_awaited()


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
@pytest.mark.asyncio
async def test_ambiguous_proposal_stays_unknown_and_blocks_retry(monkeypatch):
    from conftest import seed_test_proposal_approval
    from sqlalchemy import select

    from app.db.models import Job, Proposal, SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.proposal_sender import ProposalSender

    jid, pid = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=jid, platform="fake", external_id=str(jid), title="Test",
                        description="Test", status="approved"))
        await session.flush()
        session.add(Proposal(id=pid, job_id=jid, content="Approved", version=1, status="approved"))
        await session.commit()
        await seed_test_proposal_approval(session, jid, pid)
    platform = AsyncMock(return_value=(False, "uncertain response", {"ambiguous": True}))
    monkeypatch.setattr(ProposalSender, "_send_to_platform", platform)
    sender = ProposalSender()
    first, _, _ = await sender.send(str(jid), str(pid), "Approved")
    second, _, _ = await sender.send(str(jid), str(pid), "Approved")
    assert not first and not second
    assert platform.await_count == 1
    async with AsyncSessionLocal() as session:
        attempts = (await session.scalars(select(SendAttempt).where(SendAttempt.job_id == jid))).all()
        assert len(attempts) == 1 and attempts[0].status == "unknown_result"
        assert (await session.get(Proposal, pid)).status == "approved"
