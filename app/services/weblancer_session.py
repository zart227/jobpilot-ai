"""Sanitize Weblancer Playwright storage state (drop unrelated site cookies)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.utils.storage_state import write_json_replacing

_WEBLANCER_HOSTS = ("weblancer.net",)
_CF_HOSTS = ("challenges.cloudflare.com", "cloudflare.com")
_KEEP_COOKIE_NAMES_ON_ANY_HOST = frozenset({"cf_clearance", "cf_chl_rc_ni"})


def _keep_cookie(cookie: dict[str, Any]) -> bool:
    domain = str(cookie.get("domain", "")).lower()
    name = str(cookie.get("name", ""))
    if any(h in domain for h in _WEBLANCER_HOSTS):
        return True
    # Keep challenge cookies only when domain is weblancer (never `.cloudflare.com` — FlareSolverr 500).
    if name in _KEEP_COOKIE_NAMES_ON_ANY_HOST and any(h in domain for h in _WEBLANCER_HOSTS):
        return True
    return False


def _keep_origin(origin: dict[str, Any]) -> bool:
    origin_url = str(origin.get("origin", "")).lower()
    return any(h in origin_url for h in (*_WEBLANCER_HOSTS, *_CF_HOSTS))


def sanitize_storage_state(payload: dict[str, Any]) -> dict[str, Any]:
    cookies = payload.get("cookies")
    if not isinstance(cookies, list):
        cookies = []
    cleaned_cookies = [c for c in cookies if isinstance(c, dict) and _keep_cookie(c)]

    origins = payload.get("origins")
    if not isinstance(origins, list):
        origins = []
    cleaned_origins = [o for o in origins if isinstance(o, dict) and _keep_origin(o)]

    return {"cookies": cleaned_cookies, "origins": cleaned_origins}


def load_storage_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return sanitize_storage_state(data)


def write_sanitized_storage(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = sanitize_storage_state(payload)
    write_json_replacing(path, cleaned)
    return cleaned
