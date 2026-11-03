"""Fixed-window HTTP API rate limiting (Redis)."""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

import redis.asyncio as redis
from fastapi import Request
from starlette.responses import JSONResponse

from app.api.auth import validated_api_token
from app.config import Settings, get_settings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

_PUBLIC_PATHS = frozenset({"/health", "/ready", "/openapi.json", "/docs", "/redoc"})


def _client_rate_key(request: Request, settings: Settings) -> str:
    token = validated_api_token(
        request.headers.get("authorization"),
        request.headers.get("x-api-key"),
        settings,
    )
    if token is not None:
        # Authenticated identity is independent of IP and unused request headers.
        return "identity:" + hashlib.sha256(token.encode()).hexdigest()[:32]
    host = request.client.host if request.client else "unknown"
    return "anonymous:" + hashlib.sha256(host.encode()).hexdigest()[:32]


_WINDOW_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 or redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


async def _check_fixed_window(
    client: redis.Redis,
    bucket: str,
    *,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds)."""
    window_start = int(time.time()) // window_seconds
    key = f"api_rl:{bucket}:{window_start}"
    count = int(await client.eval(_WINDOW_SCRIPT, 1, key, window_seconds + 1))
    if count <= limit:
        return True, 0
    now = int(time.time())
    retry_after = window_seconds - (now % window_seconds)
    return False, max(1, retry_after)


class ApiRateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._redis: redis.Redis | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/docs"):
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        if not settings.api_rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        limit = settings.api_rate_limit_requests
        window = settings.api_rate_limit_window_seconds
        bucket = _client_rate_key(request, settings)

        try:
            if self._redis is None:
                self._redis = redis.from_url(
                    settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
            allowed, retry_after = await _check_fixed_window(
                self._redis,
                bucket,
                limit=limit,
                window_seconds=window,
            )
        except (redis.RedisError, OSError):
            if settings.api_rate_limit_fail_open:
                await self.app(scope, receive, send)
                return
            response = JSONResponse(
                status_code=503,
                content={"detail": "Rate limit backend unavailable"},
            )
            await response(scope, receive, send)
            return

        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
