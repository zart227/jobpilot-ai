from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
import structlog
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

ServiceName = Literal["openai", "telegram"]
_pool_cache: dict[str, ProxyRotator] = {}


class ProxyRotator:
    def __init__(self, list_path: Path, state_path: Path, mode: str = "sequential") -> None:
        self.list_path = list_path
        self.state_path = state_path
        self.mode = mode
        self._proxies = load_proxy_list(list_path)
        if not self._proxies:
            raise ValueError(f"Proxy list is empty: {list_path}")
        self._index = 0
        self._failed: set[str] = set()
        self._load_state()
        logger.info(
            "Proxy rotator ready",
            list_path=str(list_path),
            total=len(self._proxies),
            mode=mode,
            failed=len(self._failed),
        )

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._index = int(data.get("index", 0)) % len(self._proxies)
            self._failed = set(data.get("failed", []))
        except Exception as exc:
            logger.warning("Could not load proxy rotation state", error=str(exc))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"index": self._index, "failed": sorted(self._failed)}
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def mark_failed(self, proxy: str) -> None:
        self._failed.add(proxy.strip())
        self._save_state()

    @property
    def total(self) -> int:
        return len(self._proxies)

    def next_proxy(self) -> str | None:
        available = [proxy for proxy in self._proxies if proxy not in self._failed]
        if not available:
            logger.error("No proxies left in rotation", list_path=str(self.list_path))
            return None

        if self.mode == "random":
            proxy = random.choice(available)
        else:
            proxy = None
            for _ in range(len(self._proxies)):
                candidate = self._proxies[self._index]
                self._index = (self._index + 1) % len(self._proxies)
                self._save_state()
                if candidate not in self._failed:
                    proxy = candidate
                    break
            if proxy is None:
                return None

        logger.info(
            "Selected proxy",
            proxy=mask_proxy(proxy),
            available=len(available),
            total=len(self._proxies),
        )
        return proxy


def load_proxy_list(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Proxy list not found: {path}")
    proxies: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            line = f"http://{line}"
        proxies.append(line)
    return proxies


def mask_proxy(proxy: str) -> str:
    if "@" not in proxy:
        return proxy
    parsed = urlparse(proxy)
    scheme = parsed.scheme or "http"
    port = parsed.port or (1080 if scheme.startswith("socks") else 8080)
    return f"{scheme}://***:***@{parsed.hostname}:{port}"


def resolve_proxy_list_path(path_value: str) -> Path | None:
    raw = path_value.strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def get_service_proxy_list(settings: Settings, service: ServiceName) -> Path | None:
    if settings.proxy:
        return None
    list_value = (
        settings.openai_proxy_list if service == "openai" else settings.telegram_proxy_list
    )
    return resolve_proxy_list_path(list_value)


def get_proxy_rotator(settings: Settings, service: ServiceName) -> ProxyRotator | None:
    if settings.proxy or not settings.proxy_rotate:
        return None

    list_path = get_service_proxy_list(settings, service)
    if not list_path:
        return None

    cache_key = f"{service}:{list_path}"
    if cache_key not in _pool_cache:
        state_path = resolve_proxy_list_path(settings.proxy_state_dir) / f"{service}_proxy_rotation.json"
        _pool_cache[cache_key] = ProxyRotator(
            list_path=list_path,
            state_path=state_path,
            mode=settings.proxy_rotate_mode,
        )
    return _pool_cache[cache_key]


def get_proxy_candidates(settings: Settings, service: ServiceName) -> list[str | None]:
    if settings.proxy:
        return [settings.proxy]

    rotator = get_proxy_rotator(settings, service)
    if rotator:
        candidates: list[str | None] = []
        attempts = min(settings.proxy_max_attempts, rotator.total)
        for _ in range(attempts):
            proxy = rotator.next_proxy()
            if proxy and proxy not in candidates:
                candidates.append(proxy)
        return candidates or [None]

    list_path = get_service_proxy_list(settings, service)
    if list_path and list_path.is_file():
        proxies = load_proxy_list(list_path)
        if proxies:
            return [proxies[0]]

    return [None]


def build_httpx_async_client(
    *,
    proxy: str | None,
    timeout_seconds: float,
) -> httpx.AsyncClient:
    kwargs: dict = {"timeout": timeout_seconds}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


def create_telegram_bot(token: str, settings: Settings | None = None) -> Bot:
    cfg = settings or get_settings()
    proxy = get_proxy_candidates(cfg, "telegram")[0]
    if proxy:
        logger.info("Telegram bot using proxy", proxy=mask_proxy(proxy))
        session = AiohttpSession(proxy=proxy)
        return Bot(token=token, session=session)
    return Bot(token=token)


def mark_proxy_failed(settings: Settings, service: ServiceName, proxy: str | None) -> None:
    if not proxy:
        return
    rotator = get_proxy_rotator(settings, service)
    if rotator:
        rotator.mark_failed(proxy)
