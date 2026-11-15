"""Fetch Weblancer HTML via HTTP cookies, Chrome-channel Playwright, or FlareSolverr."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from app.config import get_settings
from app.services.weblancer_listing_parse import is_cloudflare_challenge_html
from app.services.weblancer_session import load_storage_state

logger = structlog.get_logger(__name__)

Transport = Literal["http", "playwright", "chrome", "flaresolverr"]

_CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def resolve_transport_chain(mode: str) -> list[Transport]:
    normalized = (mode or "auto").strip().lower()
    if normalized == "auto":
        chain: list[Transport] = ["http", "chrome", "playwright", "flaresolverr"]
    elif normalized in {"http", "playwright", "chrome", "flaresolverr"}:
        chain = [normalized]  # type: ignore[list-item]
    else:
        chain = ["http", "chrome", "playwright", "flaresolverr"]
    if os.environ.get("JOBPILOT_IN_DOCKER") == "1":
        chain = [t for t in chain if t != "chrome"]
    return chain


def storage_state_path() -> Path | None:
    settings = get_settings()
    candidates: list[Path] = []
    raw = settings.weblancer_storage_state.strip()
    if raw:
        candidates.append(Path(raw))
    candidates.extend(
        (
            Path("data/weblancer_session.json"),
            Path("/app/data/weblancer_session.json"),
        )
    )
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def cookie_header_from_state(state: dict[str, Any]) -> str:
    pairs: list[str] = []
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        domain = str(cookie.get("domain", "")).lower()
        if not name or value is None:
            continue
        if "weblancer.net" not in domain and cookie.get("name") != "cf_clearance":
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


async def fetch_html_http(url: str, *, timeout_s: float = 45.0) -> str | None:
    path = storage_state_path()
    if not path:
        logger.warning("Weblancer HTTP: no storage state file")
        return None
    state = load_storage_state(path)
    if not state:
        logger.warning("Weblancer HTTP: invalid storage state", path=str(path))
        return None

    settings = get_settings()
    cookie_header = cookie_header_from_state(state)
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    timeout = httpx.Timeout(timeout_s)
    client_kwargs: dict[str, Any] = {"timeout": timeout, "headers": headers, "follow_redirects": True}
    if settings.proxy_enabled and settings.proxy:
        client_kwargs["proxy"] = settings.proxy

    async with httpx.AsyncClient(**client_kwargs) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("Weblancer HTTP fetch failed", url=url, error=str(exc))
            return None

    if response.status_code != 200:
        logger.warning("Weblancer HTTP status", url=url, status=response.status_code)
        return None
    html = response.text
    if is_cloudflare_challenge_html(html):
        logger.warning("Weblancer HTTP: Cloudflare challenge", url=url)
        return None
    return html


def flaresolverr_base_url() -> str:
    settings = get_settings()
    configured = settings.weblancer_flaresolverr_url.strip().rstrip("/")
    if configured:
        return configured
    if settings.jobpilot_in_docker:
        return "http://flaresolverr:8191"
    return ""


def session_cookies_for_flaresolverr() -> list[dict[str, str]]:
    path = storage_state_path()
    if not path:
        return []
    state = load_storage_state(path)
    if not state:
        return []
    out: list[dict[str, str]] = []
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain", "")).lower()
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        if "weblancer" not in domain:
            continue
        if name == "cf_clearance" and "weblancer" not in domain:
            continue
        out.append(
            {
                "name": str(name),
                "value": str(value),
                "domain": str(cookie.get("domain", ".weblancer.net")),
            }
        )
    return out


async def fetch_flaresolverr_solution(url: str, *, timeout_s: float = 90.0) -> dict[str, Any] | None:
    base = flaresolverr_base_url()
    if not base:
        return None

    payload: dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60_000,
        "cookies": session_cookies_for_flaresolverr(),
    }
    timeout = httpx.Timeout(timeout_s)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base}/v1", json=payload)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Weblancer FlareSolverr failed", url=url, error=str(exc))
        return None

    if body.get("status") != "ok":
        logger.warning("Weblancer FlareSolverr bad status", url=url, detail=body.get("message"))
        return None
    solution = body.get("solution") or {}
    html = solution.get("response") or ""
    if html and is_cloudflare_challenge_html(html):
        logger.warning("Weblancer FlareSolverr: still challenge", url=url)
        return None
    return solution


async def fetch_html_flaresolverr(url: str) -> str | None:
    solution = await fetch_flaresolverr_solution(url)
    if not solution:
        return None
    html = solution.get("response") or ""
    if not html:
        return None
    return html


async def fetch_listing_html(url: str, transport: Transport) -> str | None:
    if transport == "http":
        return await fetch_html_http(url)
    if transport == "flaresolverr":
        return await fetch_html_flaresolverr(url)
    if transport in {"playwright", "chrome"}:
        from app.services.weblancer_browser import fetch_listing_html_playwright

        return await fetch_listing_html_playwright(url, channel="chrome" if transport == "chrome" else None)
    return None
