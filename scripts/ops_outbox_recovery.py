#!/usr/bin/env python3
"""Inspect outbox needs_attention rows or explicitly requeue one eligible row.

Requires an explicit DATABASE_URL. Default mode is read-only; --apply resets one
row to pending so the delivery worker may send Telegram. This command never calls
Telegram itself.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=uuid.UUID, help="ID of one notification_outbox row")
    parser.add_argument("--apply", action="store_true", help="requeue the specified outbox row")
    parser.add_argument(
        "--ack-ambiguous-lease",
        action="store_true",
        help="allow requeue after lease_expired_possible_telegram_delivery when Telegram UI was checked",
    )
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        parser.error("explicit DATABASE_URL is required")
    if args.apply and not args.id:
        parser.error("--apply requires --id")
    return args


async def main(args):
    from sqlalchemy import select

    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal
    from app.services.notification_outbox import requeue_outbox_for_delivery

    async with AsyncSessionLocal() as session:
        if args.apply:
            if not await requeue_outbox_for_delivery(
                session,
                args.id,
                acknowledge_ambiguous_lease=args.ack_ambiguous_lease,
            ):
                print(
                    "Not requeued: row is absent, not eligible, has delivery evidence, "
                    "or requires review-reissue instead."
                )
                return 1
            await session.commit()
            print(
                f"Requeued outbox {args.id}; deliver-notification-outbox may send Telegram."
            )
            return 0
        query = (
            select(NotificationOutbox)
            .where(NotificationOutbox.status == "needs_attention")
            .order_by(NotificationOutbox.created_at.desc())
            .limit(100)
        )
        if args.id:
            query = query.where(NotificationOutbox.id == args.id)
        for row in (await session.scalars(query)).all():
            preview = (row.last_error or "")[:120].replace("\n", " ")
            print(
                f"id={row.id} kind={row.kind} reference_type={row.reference_type} "
                f"telegram_message_id={row.telegram_message_id} "
                f"attempts={row.attempt_count} last_error={preview!r}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
