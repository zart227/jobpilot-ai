"""Cross-check Telegram pending cards vs notification outbox (read-only, C030/C028)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlparse

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def run_crosscheck() -> dict:
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.models import Job, NotificationOutbox, ProposalApprovalSnapshot, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services.opportunity_policy import kwork_job_assessment_rejection
    from scripts.ops_notification_outbox_inventory import error_bucket as outbox_error_bucket
    from scripts.ops_pending_cards import delivery_state

    db_name = urlparse(url).path.lstrip("/") or "unknown"
    uncertain_states = {
        "delivery_unknown_no_outbox",
        "delivery_uncertain",
        "delivery_inconsistent",
    }

    pending_rows: list[dict] = []
    async with AsyncSessionLocal() as session:
        pendings = list(
            (
                await session.scalars(
                    select(TelegramPending)
                    .where(TelegramPending.status == "pending")
                    .order_by(TelegramPending.message_id.desc().nullslast())
                )
            ).all()
        )
        for tp in pendings:
            job = await session.get(Job, tp.job_id)
            gate = None
            if job and job.platform == "kwork":
                gate = await kwork_job_assessment_rejection(session, job) or "pass"
            snap = await session.get(ProposalApprovalSnapshot, tp.id) is not None
            outbox_rows = list(
                (
                    await session.scalars(
                        select(NotificationOutbox)
                        .where(
                            NotificationOutbox.reference_type == "telegram_pending",
                            NotificationOutbox.reference_id == tp.id,
                        )
                        .order_by(NotificationOutbox.created_at)
                    )
                ).all()
            )
            statuses = [row.status for row in outbox_rows]
            state = delivery_state(tp.message_id, statuses)
            pending_rows.append(
                {
                    "pending_id": str(tp.id),
                    "message_id": tp.message_id,
                    "delivery_state": state,
                    "outbox_statuses": statuses,
                    "outbox_needs_attention_ids": [
                        str(row.id) for row in outbox_rows if row.status == "needs_attention"
                    ],
                    "review_prerequisites_present": gate == "pass" and snap and tp.message_id is not None,
                }
            )

        orphan_na = list(
            (
                await session.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.status == "needs_attention",
                        NotificationOutbox.reference_type == "telegram_pending",
                    )
                )
            ).all()
        )
        orphan_details: list[dict] = []
        for row in orphan_na:
            ref_id = row.reference_id
            tp = await session.get(TelegramPending, ref_id) if ref_id else None
            active = tp is not None and tp.status == "pending"
            orphan_details.append(
                {
                    "outbox_id": str(row.id),
                    "reference_id": str(ref_id) if ref_id else None,
                    "kind": row.kind,
                    "telegram_message_id": row.telegram_message_id,
                    "pending_still_active": active,
                    "error_bucket": outbox_error_bucket(row.last_error),
                }
            )
        orphan_outbox_needs_attention_total = len(orphan_details)
        inactive_orphan_details = [
            item for item in orphan_details if not item["pending_still_active"]
        ]

    uncertain = [row for row in pending_rows if row["delivery_state"] in uncertain_states]
    inactive_na = inactive_orphan_details
    inactive_by_kind: dict[str, int] = {}
    inactive_by_error_bucket: dict[str, int] = {}
    for item in inactive_na:
        kind = str(item.get("kind") or "unknown")
        bucket = str(item.get("error_bucket") or "unknown")
        inactive_by_kind[kind] = inactive_by_kind.get(kind, 0) + 1
        inactive_by_error_bucket[bucket] = inactive_by_error_bucket.get(bucket, 0) + 1
    active_pending_na = [
        {
            "pending_id": row["pending_id"],
            "outbox_needs_attention_ids": row["outbox_needs_attention_ids"],
        }
        for row in pending_rows
        if row["outbox_needs_attention_ids"]
    ]
    return {
        "database_name": db_name,
        "pending_total": len(pending_rows),
        "pending_delivery_uncertain": len(uncertain),
        "pending_with_outbox_needs_attention": len(active_pending_na),
        "orphan_outbox_needs_attention_total": orphan_outbox_needs_attention_total,
        "inactive_needs_attention_orphan_count": len(inactive_na),
        "inactive_needs_attention_by_kind": inactive_by_kind,
        "inactive_needs_attention_by_error_bucket": inactive_by_error_bucket,
        "uncertain_pending_sample": uncertain[:20],
        "needs_attention_outbox_for_inactive_pending": inactive_na[:20],
        "active_pending_with_needs_attention": active_pending_na[:20],
        "notes": [
            "Does not change outbox status or resend Telegram.",
            "Pair with ops_outbox_triage_checklist.py for legacy proposal_approval rows.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    _require_database_url()
    try:
        report = asyncio.run(run_crosscheck())
    except Exception as exc:
        logger.error("pending/outbox crosscheck failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
