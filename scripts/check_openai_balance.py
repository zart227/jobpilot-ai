"""Check OpenAI credit balance and spend."""

import argparse
import asyncio
import sys

from app.config import get_settings
from app.llm.openai_usage import format_usage_summary, get_openai_usage


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check OpenAI balance and spend")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh data",
    )
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()

    if not (
        settings.openai_session_token
        or settings.openai_admin_api_key
        or settings.openai_budget_usd > 0
    ):
        print(
            "Set OPENAI_SESSION_TOKEN and/or OPENAI_ADMIN_API_KEY "
            "(optional OPENAI_BUDGET_USD) in .env",
            file=sys.stderr,
        )
        return 1

    snapshot = await get_openai_usage(settings, force_refresh=args.refresh)
    print(format_usage_summary(snapshot))
    print()

    if not snapshot.has_data():
        if snapshot.error:
            print(f"Status: UNAVAILABLE ({snapshot.error})", file=sys.stderr)
            return 1
        print("Status: no data", file=sys.stderr)
        return 1

    warn = settings.openai_balance_warn_usd
    critical = settings.openai_balance_critical_usd
    if snapshot.is_critical(critical):
        print(f"Status: CRITICAL (<= ${critical:.2f})")
        return 2
    if snapshot.is_warning(warn, critical):
        print(f"Status: WARNING (<= ${warn:.2f})")
        return 0
    print("Status: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
