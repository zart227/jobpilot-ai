"""Read-only inbound message generation and ops_alert inventory (C027)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlparse

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import InboundMessage, NotificationOutbox
from app.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

GENERATION_ATTEMPTS_KEY = "generation_attempts"
GENERATION_EXHAUSTED_NOTIFIED = "generation_exhausted_notified_at"


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


def _attempts(raw_metadata) -> int:
    raw = raw_metadata or {}
    try:
        return int(raw.get(GENERATION_ATTEMPTS_KEY) or 0)
    except (TypeError, ValueError):
        return 0


async def run_inventory(*, sample_limit: int) -> dict:
    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    settings = get_settings()
    url = (
        os.environ.get("JOBPILOT_WORKING_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    )
    db_name = urlparse(url).path.lstrip("/") or "unknown"
    max_attempts = 5

    async with AsyncSessionLocal() as session:
        status_rows = await session.execute(
            select(InboundMessage.status, func.count())
            .where(InboundMessage.platform == "kwork")
            .group_by(InboundMessage.status)
        )
        by_status = {status: int(count) for status, count in status_rows.all()}

        needs_attention = list(
            (
                await session.scalars(
                    select(InboundMessage)
                    .where(
                        InboundMessage.platform == "kwork",
                        InboundMessage.status == "needs_attention",
                    )
                    .order_by(InboundMessage.received_at.desc())
                    .limit(sample_limit)
                )
            ).all()
        )

        received_recent = list(
            (
                await session.scalars(
                    select(InboundMessage)
                    .where(
                        InboundMessage.platform == "kwork",
                        InboundMessage.status == "received",
                    )
                    .order_by(InboundMessage.received_at.desc())
                    .limit(sample_limit)
                )
            ).all()
        )

        ops_alert_rows = list(
            (
                await session.scalars(
                    select(NotificationOutbox)
                    .where(NotificationOutbox.kind == "ops_alert")
                    .order_by(NotificationOutbox.created_at.desc())
                    .limit(sample_limit)
                )
            ).all()
        )

        ops_status_rows = await session.execute(
            select(NotificationOutbox.status, func.count())
            .where(NotificationOutbox.kind == "ops_alert")
            .group_by(NotificationOutbox.status)
        )
        ops_alert_totals_by_status = {
            status: int(count) for status, count in ops_status_rows.all()
        }

    exhausted_notified = 0
    at_max_attempts = 0
    async with AsyncSessionLocal() as session:
        all_kwork = await session.execute(
            select(InboundMessage.raw_metadata).where(InboundMessage.platform == "kwork")
        )
        for (meta,) in all_kwork.all():
            if _attempts(meta) >= max_attempts:
                at_max_attempts += 1
            if (meta or {}).get(GENERATION_EXHAUSTED_NOTIFIED):
                exhausted_notified += 1

    def _inbound_sample(row: InboundMessage) -> dict:
        meta = row.raw_metadata or {}
        return {
            "id": str(row.id),
            "status": row.status,
            "received_at": row.received_at.isoformat() if row.received_at else None,
            "generation_attempts": _attempts(meta),
            "generation_exhausted_notified": bool(meta.get(GENERATION_EXHAUSTED_NOTIFIED)),
            "has_interaction": bool(row.interaction_id),
            "has_inbox_pending": bool(row.inbox_pending_id),
            "platform_message_id": meta.get("platform_message_id"),
        }

    ops_by_status: dict[str, int] = {}
    for row in ops_alert_rows:
        ops_by_status[row.status] = ops_by_status.get(row.status, 0) + 1

    return {
        "database_name": db_name,
        "kwork_inbound_by_status": by_status,
        "generation_max_attempts": max_attempts,
        "inbound_at_max_generation_attempts": at_max_attempts,
        "inbound_with_exhausted_ops_alert_enqueued": exhausted_notified,
        "needs_attention_inbound_sample": [_inbound_sample(r) for r in needs_attention],
        "received_inbound_sample": [_inbound_sample(r) for r in received_recent],
        "ops_alert_recent_sample": [
            {
                "id": str(row.id),
                "status": row.status,
                "idempotency_key": row.idempotency_key,
                "reference_id": str(row.reference_id) if row.reference_id else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in ops_alert_rows
        ],
        "ops_alert_sample_status_counts": ops_by_status,
        "ops_alert_totals_by_status": ops_alert_totals_by_status,
        "ops_alert_pending_or_sending": int(
            ops_alert_totals_by_status.get("pending", 0)
            + ops_alert_totals_by_status.get("sending", 0)
        ),
        "notes": [
            "ops_alert uses idempotency inbound_generation_exhausted:{inbound_id}.",
            "Requeue: scripts/ops_inbound_recovery.py --id UUID --apply (never auto on working without review).",
            "Live Telegram delivery of ops_alert requires worker + valid bot token; this script does not send.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    _require_database_url()
    try:
        report = asyncio.run(run_inventory(sample_limit=max(1, args.sample_limit)))
    except Exception as exc:
        logger.error("inbound generation inventory failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
