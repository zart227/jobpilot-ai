#!/usr/bin/env python3
"""Regenerate inbox draft replies with full job/proposal context."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import get_settings
from app.services.kwork_inbox import regenerate_pending_inbox_drafts


async def main() -> None:
    result = await regenerate_pending_inbox_drafts(get_settings())
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
