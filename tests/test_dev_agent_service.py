from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.services.dev_agent_service import (
    DevAgentService,
    load_bridge_env_file,
    resolve_agent_workspace,
    resolve_bridge_credentials,
    write_bridge_env_file,
)
from app.telegram.dev_agent_handlers import split_telegram_messages


def test_resolve_agent_workspace_prefers_host_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        CURSOR_WORKSPACE="/app",
        CURSOR_WORKSPACE_HOST=str(tmp_path),
    )
    assert resolve_agent_workspace(settings) == str(tmp_path.resolve())


def test_load_and_resolve_bridge_credentials(tmp_path):
    env_file = tmp_path / "cursor_bridge.env"
    env_file.write_text(
        "CURSOR_BRIDGE_BASE_URL=http://host.docker.internal:9247\n"
        "CURSOR_BRIDGE_AUTH_TOKEN=secret-token\n",
        encoding="utf-8",
    )
    loaded = load_bridge_env_file(env_file)
    assert loaded["CURSOR_BRIDGE_BASE_URL"] == "http://host.docker.internal:9247"

    settings = Settings()
    with patch("app.services.dev_agent_service.BRIDGE_ENV_FILE", env_file):
        creds = resolve_bridge_credentials(settings)
    assert creds is not None
    assert creds.auth_token == "secret-token"


def test_write_bridge_env_file(tmp_path):
    path = write_bridge_env_file(
        "http://host.docker.internal:9247",
        "token",
        str(tmp_path),
        path=tmp_path / "bridge.env",
    )
    content = path.read_text(encoding="utf-8")
    assert "CURSOR_BRIDGE_BASE_URL=http://host.docker.internal:9247" in content
    assert f"CURSOR_WORKSPACE_HOST={tmp_path}" in content


def test_split_telegram_messages():
    short = "hello"
    assert split_telegram_messages(short) == ["hello"]
    long_text = "x" * 5000
    parts = split_telegram_messages(long_text, limit=4096)
    assert len(parts) == 2
    assert sum(len(part) for part in parts) == 5000


@pytest.mark.asyncio
async def test_collect_project_status_includes_counts():
    redis = AsyncMock()
    client = MagicMock()
    service = DevAgentService(client, redis)

    with patch("app.services.dev_agent_service.AsyncSessionLocal") as mock_session:
        session = AsyncMock()
        mock_session.return_value.__aenter__.return_value = session
        session.scalar = AsyncMock(side_effect=[3, 10, 5])

        with patch("app.services.dev_agent_service.RewardSystem") as mock_rewards:
            mock_rewards.return_value.get_total_rewards = AsyncMock(return_value=42)
            with patch("app.services.dev_agent_service.is_kwork_paused", return_value=False):
                with patch("app.services.dev_agent_service.httpx.AsyncClient") as mock_http:
                    mock_http.return_value.__aenter__.return_value.get = AsyncMock(
                        return_value=MagicMock(status_code=200)
                    )
                    status = await service.collect_project_status()

    assert "Ожидают одобрения" in status
    assert "<b>3</b>" in status
    assert "Kwork активен" in status


@pytest.mark.asyncio
async def test_reset_session_deletes_redis_key():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"agent-123")
    client = MagicMock()
    service = DevAgentService(client, redis)

    with patch("cursor_sdk.AsyncAgent") as mock_agent_cls:
        agent = AsyncMock()
        mock_agent_cls.resume = AsyncMock(return_value=agent)
        await service.reset_session(12345)

    agent.close.assert_awaited_once()
    redis.delete.assert_awaited_once()
