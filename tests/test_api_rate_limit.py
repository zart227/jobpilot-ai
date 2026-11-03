from unittest.mock import AsyncMock, patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.api import rate_limit as rate_limit_mod
from app.api.rate_limit import ApiRateLimitMiddleware, _check_fixed_window
from app.config import Settings


async def _ok(_request):
    return PlainTextResponse("ok")


def _mini_app() -> Starlette:
    app = Starlette(routes=[Route("/api/ping", _ok)])
    app.add_middleware(ApiRateLimitMiddleware)
    return app


@pytest.mark.asyncio
async def test_check_fixed_window_allows_then_blocks() -> None:
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=[1, 2, 3])
    allowed1, _ = await _check_fixed_window(mock_redis, "b", limit=2, window_seconds=60)
    allowed2, _ = await _check_fixed_window(mock_redis, "b", limit=2, window_seconds=60)
    allowed3, retry = await _check_fixed_window(mock_redis, "b", limit=2, window_seconds=60)
    assert allowed1 and allowed2 and not allowed3
    assert retry >= 1


def test_rate_limit_returns_429_when_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_REQUESTS=2,
        API_RATE_LIMIT_WINDOW_SECONDS=60,
        API_RATE_LIMIT_FAIL_OPEN=False,
    )
    monkeypatch.setattr(rate_limit_mod, "get_settings", lambda: settings)

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=[1, 2, 3])

    with patch("app.api.rate_limit.redis.from_url", return_value=mock_redis):
        client = TestClient(_mini_app())
        assert client.get("/api/ping").status_code == 200
        assert client.get("/api/ping").status_code == 200
        blocked = client.get("/api/ping")
        assert blocked.status_code == 429
        assert blocked.headers.get("retry-after")


def test_rate_limit_disabled_skips_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, API_RATE_LIMIT_ENABLED=False)
    monkeypatch.setattr(rate_limit_mod, "get_settings", lambda: settings)

    with patch("app.api.rate_limit.redis.from_url") as from_url:
        client = TestClient(_mini_app())
        for _ in range(5):
            assert client.get("/api/ping").status_code == 200
        from_url.assert_not_called()


def test_redis_failure_is_closed_by_default(monkeypatch):
    import redis

    settings = Settings(_env_file=None, API_RATE_LIMIT_ENABLED=True)
    monkeypatch.setattr(rate_limit_mod, "get_settings", lambda: settings)
    mock = AsyncMock()
    mock.eval.side_effect = redis.ConnectionError("test backend unavailable")
    with patch("app.api.rate_limit.redis.from_url", return_value=mock):
        assert TestClient(_mini_app()).get("/api/ping").status_code == 503


@pytest.mark.asyncio
async def test_atomic_redis_quota_concurrent_requests(monkeypatch):
    import asyncio
    import os
    import uuid

    import redis.asyncio as redis

    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        pytest.skip("requires isolated integration Redis")
    bucket = f"block-a-test:{uuid.uuid4()}"
    client = redis.from_url(os.environ["REDIS_URL"])
    monkeypatch.setattr(rate_limit_mod.time, "time", lambda: 1800000000)
    try:
        results = await asyncio.gather(
            *[_check_fixed_window(client, bucket, limit=3, window_seconds=60) for _ in range(20)]
        )
        assert sum(allowed for allowed, _ in results) == 3
        key = f"api_rl:{bucket}:{1800000000 // 60}"
        assert await client.ttl(key) > 0
        await client.delete(key)
    finally:
        await client.aclose()
