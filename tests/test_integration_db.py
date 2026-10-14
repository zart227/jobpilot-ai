"""Integration tests (PostgreSQL + Redis). Enable with JOBPILOT_INTEGRATION_TESTS=1."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1 to run",
)


@pytest.mark.asyncio
async def test_postgres_select_one() -> None:
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_readiness_when_deps_up() -> None:
    from app.services.health_checks import gather_readiness

    payload = await gather_readiness()
    assert payload["ready"] is True
    assert payload["status"] in ("ready", "degraded")


@pytest.mark.asyncio
async def test_redis_ping() -> None:
    import redis.asyncio as redis

    from app.config import get_settings

    settings = get_settings()
    client = redis.from_url(settings.redis_url)
    try:
        assert await client.ping()
    finally:
        await client.aclose()
