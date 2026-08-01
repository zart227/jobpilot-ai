"""Probe Kwork connects limit and update auto-pause state.

Usage:
    python scripts/check_kwork_connects_pause.py
    python scripts/check_kwork_connects_pause.py --force
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.services.kwork_browser import refresh_kwork_pause_from_kwork
from app.services.kwork_pause import get_kwork_pause_reason, is_kwork_paused, load_pause_state


async def main() -> None:
    parser = argparse.ArgumentParser(description="Check Kwork connects pause state")
    parser.add_argument("--force", action="store_true", help="Ignore check interval throttle")
    args = parser.parse_args()

    get_settings.cache_clear()
    result = await refresh_kwork_pause_from_kwork(force=args.force)
    state = load_pause_state()

    print("Probe result:", result)
    if state:
        print("State file:", state.to_dict())
    print("Paused now:", is_kwork_paused())
    reason = get_kwork_pause_reason()
    if reason:
        print(reason)


if __name__ == "__main__":
    asyncio.run(main())
