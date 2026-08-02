#!/usr/bin/env python3
"""Re-send Telegram notification for the latest Kwork inbox client message."""

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
from app.services.kwork_inbox import resend_last_inbox_message


async def main() -> None:
    parser = argparse.ArgumentParser(description="Resend last Kwork inbox message to Telegram")
    parser.add_argument("--username", help="Kwork dialog username (optional)")
    parser.add_argument("--user-id", type=int, help="Kwork user id (optional)")
    args = parser.parse_args()

    result = await resend_last_inbox_message(
        get_settings(),
        username=args.username,
        user_id=args.user_id,
    )
    print(result)
    if result.get("notified"):
        raise SystemExit(0)
    raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
