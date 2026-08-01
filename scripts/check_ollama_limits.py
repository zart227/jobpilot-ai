"""Check Ollama Cloud plan and usage limits."""

import argparse
import asyncio
import sys

from app.config import get_settings
from app.llm.ollama_usage import format_usage_summary, get_ollama_usage


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check Ollama Cloud usage limits")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh data",
    )
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()

    if not settings.ollama_api_key and not settings.ollama_session_cookie:
        print(
            "Set OLLAMA_API_KEY and/or OLLAMA_SESSION_COOKIE + OLLAMA_AID in .env",
            file=sys.stderr,
        )
        return 1

    snapshot = await get_ollama_usage(settings, force_refresh=args.refresh)
    print(format_usage_summary(snapshot))
    print()

    if snapshot.has_usage():
        warn = settings.ollama_usage_warn_percent
        critical = settings.ollama_usage_critical_percent
        if snapshot.is_critical(critical):
            print(f"Status: CRITICAL (>= {critical:.0f}%)")
            return 2
        if snapshot.is_warning(warn, critical):
            print(f"Status: WARNING (>= {warn:.0f}%)")
            return 0
        print("Status: OK")
        return 0

    if snapshot.error:
        print(f"Status: UNAVAILABLE ({snapshot.error})", file=sys.stderr)
        return 1

    print("Status: plan only (usage cookies not configured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
