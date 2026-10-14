"""Liveness/readiness dependency probes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)


async def check_postgres() -> dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            value = await session.execute(text("SELECT 1"))
            if value.scalar() != 1:
                return {"ok": False, "error": "unexpected response"}
        return {"ok": True}
    except Exception as exc:
        logger.warning("Postgres readiness failed", error=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def check_redis(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        import redis.asyncio as redis

        client = redis.from_url(settings.redis_url)
        try:
            pong = await client.ping()
            if not pong:
                return {"ok": False, "error": "no pong"}
            return {"ok": True}
        finally:
            await client.aclose()
    except Exception as exc:
        logger.warning("Redis readiness failed", error=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def check_qdrant(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    url = settings.qdrant_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/collections")
            if response.status_code >= 500:
                return {"ok": False, "error": f"http_{response.status_code}"}
            return {"ok": True, "degraded_ok": True}
    except Exception as exc:
        logger.warning("Qdrant readiness degraded", error=str(exc))
        return {"ok": False, "error": type(exc).__name__, "degraded_ok": True}


async def gather_readiness() -> dict[str, Any]:
    settings = get_settings()
    postgres, redis_status, qdrant = await asyncio.gather(
        check_postgres(),
        check_redis(settings),
        check_qdrant(settings),
    )
    required_ok = postgres.get("ok") and redis_status.get("ok")
    qdrant_ok = qdrant.get("ok")
    status = "ready" if required_ok else "not_ready"
    if required_ok and not qdrant_ok:
        status = "degraded"
    return {
        "status": status,
        "ready": required_ok,
        "checked_at": datetime.now(UTC).isoformat(),
        "dependencies": {
            "postgres": postgres,
            "redis": redis_status,
            "qdrant": {**qdrant, "optional": True},
        },
    }
