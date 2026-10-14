#!/usr/bin/env python3
"""Activate a previous scoring_weights row (rollback learning)."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select, update


async def _list_recent(limit: int) -> None:
    from app.db.models import ScoringWeight
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(ScoringWeight).order_by(ScoringWeight.created_at.desc()).limit(limit)
            )
        ).scalars().all()
    if not rows:
        print("No scoring_weights rows.")
        return
    for row in rows:
        active = "active" if row.is_active else "inactive"
        print(f"{row.id}  {active}  {row.source}  {row.created_at}  keys={len(row.weights or {})}")


async def _activate(weight_id: uuid.UUID) -> int:
    from app.db.models import ScoringWeight
    from app.db.session import AsyncSessionLocal
    from app.services.scoring_weights_validation import validate_scoring_weights

    async with AsyncSessionLocal() as session:
        target = await session.get(ScoringWeight, weight_id)
        if target is None:
            print(f"Not found: {weight_id}", file=sys.stderr)
            return 1
        validate_scoring_weights(target.weights)
        await session.execute(update(ScoringWeight).values(is_active=False))
        target.is_active = True
        await session.commit()
    print(f"Activated scoring_weights {weight_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="List or rollback scoring weight versions")
    parser.add_argument("--list", action="store_true", help="List recent rows")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("weight_id", nargs="?", help="UUID to activate (deactivates all others)")
    args = parser.parse_args()

    if args.list:
        asyncio.run(_list_recent(args.limit))
        return 0
    if not args.weight_id:
        parser.print_help()
        return 2
    return asyncio.run(_activate(uuid.UUID(args.weight_id)))


if __name__ == "__main__":
    raise SystemExit(main())
