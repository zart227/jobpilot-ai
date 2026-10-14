"""Static policy checks for Cursor bridge exposure (no live bridge required)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_bridge_host_script_defaults_to_localhost() -> None:
    script = (ROOT / "scripts" / "run_cursor_bridge.py").read_text(encoding="utf-8")
    assert 'default="127.0.0.1"' in script
    assert "127.0.0.1" in script


def test_cursor_bridge_secrets_gitignored() -> None:
    assert "data/cursor_bridge.env" in GITIGNORE
    assert "data/cursor_bridge.pid" in GITIGNORE


def test_data_services_bind_localhost_only() -> None:
    for name in ("postgres", "redis", "qdrant"):
        match = re.search(
            rf"^\s{{4}}{name}:\n(.*?)(?=^\s{{4}}\w|\Z)",
            COMPOSE,
            re.MULTILINE | re.DOTALL,
        )
        assert match, f"service block missing: {name}"
        assert "127.0.0.1:" in match.group(1), f"{name} should publish ports on localhost"


def test_cursor_proxy_forwards_to_host_loopback() -> None:
    assert "cursor-bridge-proxy" in COMPOSE
    assert "127.0.0.1:19247" in COMPOSE


@pytest.mark.asyncio
async def test_docker_dev_agent_requires_bridge_credentials() -> None:
    from unittest.mock import patch

    from app.config import Settings
    from app.services.dev_agent_service import DevAgentConfigurationError, create_cursor_client

    settings = Settings(
        _env_file=None,
        CURSOR_API_KEY="test-key",
        CURSOR_BRIDGE_BASE_URL="",
        CURSOR_BRIDGE_AUTH_TOKEN="",
        JOBPILOT_IN_DOCKER=True,
    )
    with patch("app.services.dev_agent_service.is_running_in_docker", return_value=True):
        with patch("app.services.dev_agent_service.resolve_bridge_credentials", return_value=None):
            with pytest.raises(DevAgentConfigurationError):
                await create_cursor_client(settings)
