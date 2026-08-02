#!/usr/bin/env python3
"""Regenerate and send fresh Telegram notifications for pending inbox messages."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import get_settings
from app.services.kwork_inbox import resend_inbox_pending_notifications


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate drafts and resend pending inbox messages to Telegram"
    )
    parser.add_argument("--limit", type=int, default=2, help="How many latest pending rows")
    parser.add_argument("--username", action="append", help="Filter by Kwork username")
    args = parser.parse_args()

    result = await resend_inbox_pending_notifications(
        get_settings(),
        limit=args.limit,
        usernames=args.username,
    )
    print(result)
    if result.get("sent"):
        raise SystemExit(0)
    raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
