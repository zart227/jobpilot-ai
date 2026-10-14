from unittest.mock import AsyncMock, patch

import pytest

from app.services.health_checks import gather_readiness


@pytest.mark.asyncio
async def test_gather_readiness_not_ready_when_postgres_down() -> None:
    with (
        patch(
            "app.services.health_checks.check_postgres",
            new=AsyncMock(return_value={"ok": False, "error": "down"}),
        ),
        patch(
            "app.services.health_checks.check_redis",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.services.health_checks.check_qdrant",
            new=AsyncMock(return_value={"ok": True}),
        ),
    ):
        payload = await gather_readiness()
    assert payload["ready"] is False
    assert payload["status"] == "not_ready"


@pytest.mark.asyncio
async def test_gather_readiness_degraded_without_qdrant() -> None:
    with (
        patch(
            "app.services.health_checks.check_postgres",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.services.health_checks.check_redis",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.services.health_checks.check_qdrant",
            new=AsyncMock(return_value={"ok": False, "error": "timeout", "degraded_ok": True}),
        ),
    ):
        payload = await gather_readiness()
    assert payload["ready"] is True
    assert payload["status"] == "degraded"
