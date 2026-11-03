from __future__ import annotations

import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings

API_LIMIT_MAX = 200
CHAT_MESSAGE_MAX_CHARS = 8000


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def api_auth_required(settings: Settings) -> bool:
    """The legacy false flag alone must never expose a network API."""
    local_mode = (
        settings.api_allow_unauthenticated_local
        and not settings.api_auth_required
        and settings.app_env == "development"
        and _is_loopback(settings.api_host)
        and not settings.api_auth_token.strip()
    )
    return not local_mode


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def validated_api_token(
    authorization: str | None,
    x_api_key: str | None,
    settings: Settings,
) -> str | None:
    """Use the same credential precedence for authentication and rate identity."""
    expected = settings.api_auth_token.strip()
    provided = _extract_bearer_token(authorization) or (x_api_key.strip() if x_api_key else None)
    if expected and provided and hmac.compare_digest(provided.encode(), expected.encode()):
        return expected
    return None


def verify_api_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Settings = Depends(get_settings),
    request: Request = None,
) -> None:
    if (
        not api_auth_required(settings)
        and request is not None
        and request.client is not None
        and _is_loopback(request.client.host)
    ):
        return

    if not settings.api_auth_token.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API auth is required but API_AUTH_TOKEN is not configured",
        )
    if validated_api_token(authorization, x_api_key, settings) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def clamp_api_limit(limit: int, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    ceiling = min(API_LIMIT_MAX, max(1, settings.api_list_limit_max))
    return max(1, min(limit, ceiling))
