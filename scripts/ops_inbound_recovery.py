#!/usr/bin/env python3
"""Inspect inbox generation failures or explicitly requeue one item.

Requires an explicit DATABASE_URL. The default mode is read-only; --apply changes one
specified record and never sends a message itself.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=uuid.UUID, help="ID of one inbound record")
    parser.add_argument("--apply", action="store_true", help="requeue the specified record")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        parser.error("explicit DATABASE_URL is required")
    if args.apply and not args.id:
        parser.error("--apply requires --id")
    return args


async def main(args):
    from sqlalchemy import select

    from app.db.models import InboundMessage
    from app.db.session import AsyncSessionLocal
    from app.services.kwork_inbox import requeue_inbound_for_review

    async with AsyncSessionLocal() as session:
        if args.apply:
            if not await requeue_inbound_for_review(session, args.id):
                print("Not requeued: record is absent, not eligible, or already has a review.")
                return 1
            await session.commit()
            print(f"Requeued inbound {args.id}; no message was sent by this command.")
            return 0
        query = select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.status == "needs_attention"
        ).order_by(InboundMessage.received_at).limit(100)
        if args.id:
            query = query.where(InboundMessage.id == args.id)
        for row in (await session.scalars(query)).all():
            print(f"id={row.id} received_at={row.received_at} "
                  f"attempts={(row.raw_metadata or {}).get('generation_attempts', 0)} "
                  f"has_review={bool(row.interaction_id or row.inbox_pending_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
