import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.auth import verify_api_token
from app.config import Settings, get_settings
from app.main import app


def test_health_is_public() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_is_public() -> None:
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "dependencies" in body
    assert "postgres" in body["dependencies"]


def test_verify_api_token_required() -> None:
    settings = Settings(
        _env_file=None,
        API_AUTH_TOKEN="secret-test-token",
        API_AUTH_REQUIRED=True,
    )
    with pytest.raises(HTTPException) as exc:
        verify_api_token(authorization=None, x_api_key=None, settings=settings)
    assert exc.value.status_code == 401

    verify_api_token(
        authorization="Bearer secret-test-token",
        x_api_key=None,
        settings=settings,
    )


def test_verify_api_token_skipped_only_for_explicit_loopback_dev() -> None:
    from starlette.requests import Request

    settings = Settings(
        _env_file=None,
        API_AUTH_TOKEN="",
        API_AUTH_REQUIRED=False,
        API_ALLOW_UNAUTHENTICATED_LOCAL=True,
        API_HOST="127.0.0.1",
    )
    request = Request({"type": "http", "client": ("127.0.0.1", 42), "headers": []})
    verify_api_token(authorization=None, x_api_key=None, settings=settings, request=request)


def test_verify_api_token_enforced_when_token_configured() -> None:
    settings = Settings(
        _env_file=None,
        API_AUTH_TOKEN="configured",
        API_AUTH_REQUIRED=False,
    )
    with pytest.raises(HTTPException):
        verify_api_token(authorization="Bearer wrong", x_api_key=None, settings=settings)
    verify_api_token(authorization=None, x_api_key="configured", settings=settings)


def test_reconcile_send_attempt_requires_auth() -> None:
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        API_AUTH_TOKEN="reconcile-secret",
        API_AUTH_REQUIRED=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        attempt_id = uuid.uuid4()
        assert client.post(f"/send-attempts/{attempt_id}/reconcile").status_code == 401
    finally:
        app.dependency_overrides.pop(get_settings, None)
        get_settings.cache_clear()


def test_reconcile_send_attempt_returns_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        API_AUTH_TOKEN="reconcile-secret",
        API_AUTH_REQUIRED=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings

    async def fake_reconcile(attempt_id: uuid.UUID, **_kwargs):
        assert isinstance(attempt_id, uuid.UUID)
        return "inconclusive"

    monkeypatch.setattr(
        "app.services.send_attempt_reconcile.reconcile_send_attempt",
        AsyncMock(side_effect=fake_reconcile),
    )

    try:
        client = TestClient(app)
        attempt_id = uuid.uuid4()
        response = client.post(
            f"/send-attempts/{attempt_id}/reconcile",
            headers={"Authorization": "Bearer reconcile-secret"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["attempt_id"] == str(attempt_id)
        assert body["outcome"] == "inconclusive"
    finally:
        app.dependency_overrides.pop(get_settings, None)
        get_settings.cache_clear()
