#!/usr/bin/env python3
"""List pending Telegram proposal cards and Kwork assessment gates (read-only)."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select


def delivery_state(message_id: int | None, statuses: list[str]) -> str:
    """Classify evidence without assuming an unrecorded delivery did not happen."""
    if any(status in {"sending", "needs_attention"} for status in statuses):
        return "delivery_uncertain"
    if statuses.count("sent") > 1 or statuses.count("pending") > 1:
        return "delivery_uncertain"
    if any(status not in {"pending", "sent", "cancelled"} for status in statuses):
        return "delivery_uncertain"
    if not statuses:
        return "legacy_card_known" if message_id else "delivery_unknown_no_outbox"
    if "sent" in statuses:
        if "pending" in statuses:
            return "delivery_uncertain"
        return "delivery_recorded" if message_id else "delivery_inconsistent"
    if "pending" in statuses:
        return "queued" if message_id is None else "delivery_uncertain"
    return "cancelled_only"


async def main() -> int:
    database_url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("An explicit DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL is required.")
        return 2
    os.environ["DATABASE_URL"] = database_url
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.models import Job, NotificationOutbox, ProposalApprovalSnapshot, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services.opportunity_policy import kwork_job_assessment_rejection

    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(TelegramPending)
                .where(TelegramPending.status == "pending")
                .order_by(TelegramPending.message_id.desc())
            )
        ).all()
        out = []
        for tp in rows:
            job = await session.get(Job, tp.job_id)
            gate = None
            if job and job.platform == "kwork":
                gate = await kwork_job_assessment_rejection(session, job) or "pass"
            snap = await session.get(ProposalApprovalSnapshot, tp.id) is not None
            statuses = list(await session.scalars(select(NotificationOutbox.status).where(
                NotificationOutbox.reference_type == "telegram_pending",
                NotificationOutbox.reference_id == tp.id,
            ).order_by(NotificationOutbox.created_at)))
            state = delivery_state(tp.message_id, statuses)
            out.append(
                {
                    "pending_id": str(tp.id),
                    "message_id": tp.message_id,
                    "job_url": job.url if job else None,
                    "job_status": job.status if job else None,
                    "assessment_gate": gate,
                    "approval_snapshot": snap,
                    "outbox_statuses": statuses,
                    "delivery_state": state,
                    "review_prerequisites_present": gate == "pass" and snap and tp.message_id is not None,
                }
            )
    for item in out:
        print(item)
    incomplete = sum(1 for i in out if not i["review_prerequisites_present"])
    uncertain = sum(1 for i in out if i["delivery_state"] in {
        "delivery_unknown_no_outbox", "delivery_uncertain", "delivery_inconsistent"
    })
    print(f"\nSummary: {len(out)} pending, {incomplete} missing review prerequisites, "
          f"{uncertain} need delivery investigation. Approval requires all runtime guards.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
