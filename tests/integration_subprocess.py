"""Shared env for acceptance tests that spawn integration pytest subprocesses."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[1]


def load_integration_subprocess_env() -> dict[str, str] | None:
    """Return subprocess env when test DB URLs are configured; else None (CI unit job)."""
    env = os.environ.copy()
    env_path = ROOT / ".jobpilot_integration_test.env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key, value)
    env["JOBPILOT_INTEGRATION_TESTS"] = "1"
    database_url = env.get("DATABASE_URL", "")
    redis_url = env.get("REDIS_URL", "")
    try:
        database_name = make_url(database_url).database or ""
        redis_database = int(make_url(redis_url).database or "0")
    except (ValueError, ArgumentError):
        return None
    if not database_name.endswith("_test") or redis_database < 2:
        return None
    return env
