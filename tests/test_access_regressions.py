"""Block A: regressions found during the 14 October audit."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.auth import verify_api_token
from app.api.rate_limit import _client_rate_key
from app.config import Settings
from app.telegram.access import is_authorized_telegram


def test_network_api_cannot_disable_auth_with_legacy_flag():
    cfg = Settings(_env_file=None, API_AUTH_REQUIRED=False, API_AUTH_TOKEN="", API_HOST="0.0.0.0")
    with pytest.raises(HTTPException) as err:
        verify_api_token(authorization=None, x_api_key=None, settings=cfg)
    assert err.value.status_code == 503


def test_group_without_admin_user_id_is_denied():
    cfg = Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID="-10042", TELEGRAM_ADMIN_USER_ID="")
    assert not is_authorized_telegram(-10042, 7, cfg)


def test_rate_bucket_uses_verified_bearer_not_secondary_header():
    cfg = Settings(_env_file=None, API_AUTH_TOKEN="test-secret")
    buckets = []
    for secondary in ("a", "b"):
        request = Request(
            {
                "type": "http",
                "client": ("127.0.0.1", 1),
                "headers": [
                    (b"authorization", b"Bearer test-secret"),
                    (b"x-api-key", secondary.encode()),
                ],
            }
        )
        buckets.append(_client_rate_key(request, cfg))
    assert buckets[0] == buckets[1]


@pytest.mark.parametrize(
    "host,env,peer",
    [
        ("0.0.0.0", "development", "127.0.0.1"),
        ("127.0.0.1", "production", "127.0.0.1"),
        ("127.0.0.1", "development", "203.0.113.7"),
    ],
)
def test_local_bypass_rejects_network_bind_production_or_remote_peer(host, env, peer):
    cfg = Settings(
        _env_file=None,
        APP_ENV=env,
        API_HOST=host,
        API_AUTH_REQUIRED=False,
        API_AUTH_TOKEN="",
        API_ALLOW_UNAUTHENTICATED_LOCAL=True,
    )
    request = Request({"type": "http", "client": (peer, 42), "headers": []})
    with pytest.raises(HTTPException):
        verify_api_token(authorization=None, x_api_key=None, settings=cfg, request=request)


def test_untrusted_credentials_cannot_rotate_anonymous_quota():
    cfg = Settings(_env_file=None, API_AUTH_TOKEN="valid")
    keys = []
    for token in (b"bad1", b"bad2"):
        request = Request(
            {"type": "http", "client": ("203.0.113.7", 42), "headers": [(b"x-api-key", token)]}
        )
        keys.append(_client_rate_key(request, cfg))
    assert keys[0] == keys[1]


def test_invalid_admin_user_does_not_fall_back_to_private_chat():
    cfg = Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID="100", TELEGRAM_ADMIN_USER_ID="bad")
    assert not is_authorized_telegram(100, 100, cfg)


@pytest.mark.asyncio
async def test_group_denial_stops_handler_before_fsm_or_dev_agent(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from aiogram.types import Chat, Message, Update, User

    from app.telegram import admin_middleware

    cfg = Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID="-10042", TELEGRAM_ADMIN_USER_ID="")
    monkeypatch.setattr(
        admin_middleware,
        "is_authorized_telegram",
        lambda chat, user: is_authorized_telegram(chat, user, cfg),
    )
    monkeypatch.setattr(Message, "answer", AsyncMock())
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=-10042, type="supergroup"),
            from_user=User(id=7, is_bot=False, first_name="Other"),
            text="/dev",
        ),
    )
    handler = AsyncMock()
    await admin_middleware.TelegramAdminMiddleware()(handler, update, {})
    handler.assert_not_called()


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/jobs", None),
        ("get", "/jobs/00000000-0000-0000-0000-000000000001", None),
        ("post", "/jobs/00000000-0000-0000-0000-000000000001/process", None),
        ("post", "/scrape/trigger", None),
        ("post", "/jobs/reprocess-pending", None),
        ("post", "/chat", {"job_id": "00000000-0000-0000-0000-000000000001", "message": "test"}),
        ("post", "/outcomes/00000000-0000-0000-0000-000000000001", {"status": "hired"}),
        ("post", "/send-attempts/00000000-0000-0000-0000-000000000001/reconcile", None),
        ("get", "/stats", None),
    ],
)
def test_data_and_control_routes_fail_closed_without_token(method, path, body):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.db.session import get_db
    from app.main import app

    cfg = Settings(_env_file=None, API_AUTH_REQUIRED=False, API_AUTH_TOKEN="", API_HOST="0.0.0.0")

    async def database():
        yield None  # An auth failure must never reach a handler or its SQL.

    app.dependency_overrides[get_settings] = lambda: cfg
    app.dependency_overrides[get_db] = database
    try:
        response = TestClient(app).request(method, path, json=body)
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_db, None)
