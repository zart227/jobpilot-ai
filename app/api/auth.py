from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings

API_LIMIT_MAX = 200
CHAT_MESSAGE_MAX_CHARS = 8000


def api_auth_required(settings: Settings) -> bool:
    if settings.api_auth_required:
        return True
    return bool(settings.api_auth_token.strip())


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def verify_api_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    if not api_auth_required(settings):
        return

    expected = settings.api_auth_token.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API auth is required but API_AUTH_TOKEN is not configured",
        )

    provided = _extract_bearer_token(authorization) or (x_api_key.strip() if x_api_key else None)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def clamp_api_limit(limit: int, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    ceiling = min(API_LIMIT_MAX, max(1, settings.api_list_limit_max))
    return max(1, min(limit, ceiling))
