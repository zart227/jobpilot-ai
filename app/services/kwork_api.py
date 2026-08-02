"""Async HTTP client for api.kwork.ru (inbox, dialogs, actor)."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from app.config import Settings, get_settings
from app.utils.proxy import build_httpx_async_client, get_proxy_candidates

logger = structlog.get_logger(__name__)

API_BASE = "https://api.kwork.ru"
BASIC_AUTH = ("mobile_api", "qFvfRl7w")


class KworkError(Exception):
    pass


class KworkAuthError(KworkError):
    pass


class KworkApiError(KworkError):
    pass


def _token_file(settings: Settings) -> Path:
    state_file = Path(settings.kwork_inbox_state_file)
    return state_file.parent / "kwork_api_token.json"


class KworkApi:
    """Async HTTP client for api.kwork.ru with token auto-refresh."""

    def __init__(self, settings: Settings | None = None, *, proxy: str | None = None) -> None:
        self._settings = settings or get_settings()
        self._proxy = proxy
        self._client: httpx.AsyncClient | None = None
        self._token = ""
        self._token_expires = 0.0
        self._lock = asyncio.Lock()
        self._user_id: int | None = None
        self._username: str | None = None

    async def connect(self) -> None:
        self._client = build_httpx_async_client(
            proxy=self._proxy,
            timeout_seconds=30.0,
        )
        token_path = _token_file(self._settings)
        if token_path.is_file():
            try:
                data = json.loads(token_path.read_text(encoding="utf-8"))
                if data.get("expires", 0) > time.time() + 3600:
                    self._token = str(data["token"])
                    self._token_expires = float(data["expires"])
                    logger.debug("Kwork API loaded cached token")
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _sign_in(self) -> str:
        login = self._settings.kwork_email.strip()
        password = self._settings.kwork_password
        if not login or not password:
            raise KworkAuthError("KWORK_EMAIL/KWORK_PASSWORD not configured")

        assert self._client is not None
        resp = await self._client.post(
            f"{API_BASE}/signIn",
            auth=BASIC_AUTH,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"login": login, "password": password},
        )
        body = resp.json()
        if not body.get("success"):
            raise KworkAuthError(f"Sign in failed: {body.get('error', body)}")

        token_data = body["response"]
        self._token = str(token_data["token"])
        self._token_expires = time.time() + float(token_data.get("expired", 2592000))

        token_path = _token_file(self._settings)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(
            json.dumps({"token": self._token, "expires": self._token_expires}),
            encoding="utf-8",
        )
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
        logger.info("Kwork API token refreshed")
        return self._token

    async def _ensure_token(self) -> str:
        if self._token and self._token_expires > time.time() + 86400:
            return self._token
        async with self._lock:
            if self._token and self._token_expires > time.time() + 86400:
                return self._token
            return await self._sign_in()

    async def _post(
        self,
        endpoint: str,
        data: dict[str, str] | None = None,
        *,
        retry: bool = True,
    ) -> Any:
        assert self._client is not None
        token = await self._ensure_token()
        url = f"{API_BASE}/{endpoint}?token={token}"
        resp = await self._client.post(
            url,
            auth=BASIC_AUTH,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data or {},
        )
        body = resp.json()

        if not body.get("success") and retry:
            err = str(body.get("error", ""))
            code = body.get("error_code", 0)
            if code in (401, 403) or "авторизац" in err.lower() or "token" in err.lower():
                self._token = ""
                return await self._post(endpoint, data, retry=False)

        if not body.get("success") and "error" in body:
            raise KworkApiError(f"/{endpoint}: {body.get('error', 'Unknown error')}")

        return body.get("response", body)

    async def get_actor(self) -> dict[str, Any]:
        actor = await self._post("actor")
        if isinstance(actor, dict):
            self._user_id = actor.get("id")
            self._username = actor.get("username")
        return actor

    async def get_dialogs(self) -> list[dict[str, Any]]:
        result = await self._post("dialogs")
        return result if isinstance(result, list) else []

    async def get_messages(self, username: str) -> list[dict[str, Any]]:
        result = await self._post("inboxes", {"username": username})
        if isinstance(result, list) and result:
            return result
        fallback = await self._post("getInboxTracks", {"username": username})
        return fallback if isinstance(fallback, list) else []

    async def mark_read(self, username: str) -> dict[str, Any]:
        result = await self._post("inboxRead", {"username": username})
        return result if isinstance(result, dict) else {}

    async def send_message(self, user_id: int, text: str) -> dict[str, Any]:
        result = await self._post("inboxCreate", {"user_id": str(user_id), "text": text})
        return result if isinstance(result, dict) else {}


async def create_kwork_api(settings: Settings | None = None) -> KworkApi:
    settings = settings or get_settings()
    last_error: str | None = None
    for proxy in get_proxy_candidates(settings, "openai"):
        api = KworkApi(settings, proxy=proxy)
        try:
            await api.connect()
            await api.get_actor()
            return api
        except KworkError as exc:
            last_error = str(exc)
            await api.close()
        except Exception as exc:
            last_error = str(exc)
            await api.close()
    raise KworkAuthError(last_error or "Kwork API connection failed")


def clean_kwork_message_text(text: str) -> str:
    if not text:
        return ""
    import html

    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()
