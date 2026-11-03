#!/usr/bin/env python3
"""Operator approve + live Kwork proposal send (same pipeline as Telegram APPROVE)."""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.approval_snapshot import proposal_card_is_current, record_proposal_approval
from app.services.job_pipeline import JobPipelineService
from app.services.kwork_pause import blocks_kwork_operator_send
from app.services.proposal_sender import ProposalSender


async def approve_and_send(pending_id: uuid.UUID) -> dict[str, Any]:
    settings = get_settings()
    chat_raw = settings.telegram_admin_chat_id.strip()
    if not chat_raw:
        raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID required")
    chat_id = int(chat_raw)
    user_raw = settings.telegram_admin_user_id.strip()
    actor_user_id = int(user_raw) if user_raw else chat_id

    if settings.platform_dry_run:
        raise RuntimeError("PLATFORM_DRY_RUN must be false for live send")
    if blocks_kwork_operator_send(settings):
        raise RuntimeError("Kwork pause still blocks live operator send")

    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, pending_id)
        if not pending or pending.status != "pending":
            raise RuntimeError(f"pending not actionable: {pending.status if pending else 'missing'}")
        if pending.chat_id != chat_id:
            raise RuntimeError("pending chat_id mismatch")

        job = await session.scalar(
            select(Job).where(Job.id == pending.job_id).with_for_update()
        )
        proposal = await session.scalar(
            select(Proposal).where(Proposal.id == pending.proposal_id).with_for_update()
        )
        await session.refresh(pending, with_for_update=True)
        if pending.status != "pending":
            raise RuntimeError("pending changed under lock")
        if not await proposal_card_is_current(session, pending.id, job, proposal):
            raise RuntimeError("missing or stale approval snapshot — reissue review first")

        if await JobPipelineService().is_job_sent(pending.job_id):
            raise RuntimeError("job already has sent proposal")

        message_id = pending.message_id or 0
        await record_proposal_approval(
            session,
            pending.id,
            job,
            proposal,
            actor_user_id=actor_user_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        pending.status = "approved"
        if proposal:
            proposal.status = "approved"
        job_id = str(pending.job_id)
        proposal_id = str(pending.proposal_id)
        await session.commit()

    pipeline = JobPipelineService()
    base_state = await pipeline.job_to_state(uuid.UUID(job_id))
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, uuid.UUID(proposal_id))
        content = proposal.content if proposal else ""

    state = {
        **base_state,
        "proposal_id": proposal_id,
        "proposal_content": content,
        "approval_status": "approved",
        "edited_proposal": "",
    }

    from app.agents.graph import send_node

    sender = ProposalSender()
    if await sender.is_already_sent(job_id, proposal_id):
        return {"ok": True, "skipped": "already_sent", "job_id": job_id}

    send_result = await send_node(state)
    state.update(send_result)
    send_success = send_result.get("outcome_status") == "sent"
    send_error = send_result.get("send_error") or None
    send_debug = send_result.get("send_debug") or None

    from app.services.outcome_service import record_outcome_and_learn

    outcome_status = state.get("outcome_status", "sent" if send_success else "send_failed")
    async with AsyncSessionLocal() as session:
        await record_outcome_and_learn(
            session,
            job_id=uuid.UUID(job_id),
            proposal_id=uuid.UUID(proposal_id),
            status=outcome_status,
            allow_internal=outcome_status == "send_failed",
        )

    return {
        "ok": send_success,
        "job_id": job_id,
        "proposal_id": proposal_id,
        "pending_id": str(pending_id),
        "send_error": send_error,
        "send_debug": send_debug,
        "outcome_status": outcome_status,
    }


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: ops_live_proposal_send.py <telegram_pending_uuid>", file=sys.stderr)
        return 2
    pending_id = uuid.UUID(sys.argv[1])
    get_settings.cache_clear()
    try:
        result = await approve_and_send(pending_id)
    except Exception as exc:
        print({"ok": False, "error": str(exc)})
        return 1
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
