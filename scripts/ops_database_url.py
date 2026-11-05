"""Resolve which PostgreSQL URL host-side operator CLIs should use."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def pick_ops_database_url() -> str | None:
    """Prefer reachable host URL when WORKING targets in-compose hostname."""
    working = (os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or "").strip()
    database = (os.environ.get("DATABASE_URL") or "").strip()
    if working and database and working != database:
        wh, dh = _host(working), _host(database)
        if wh in {"postgres", "db", "postgresql"} and dh in {"127.0.0.1", "localhost", "::1"}:
            return database
    return working or database or None


def main() -> None:
    url = pick_ops_database_url()
    if not url:
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)
    print(url)


if __name__ == "__main__":
    main()
