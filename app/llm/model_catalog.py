"""Discover chat models from OpenRouter and Ollama with conservative free-model checks."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import time
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from app.config import Settings
from app.llm.errors import LLMErrorKind, LLMServiceError

logger = structlog.get_logger(__name__)

# Values that trigger API-backed selection instead of passing the string to the provider.
AUTO_MODEL_SENTINELS = frozenset({"auto", "openrouter/auto", "openrouter/free"})

# Mandatory zero prices for text chat auto-selection on OpenRouter.
_OPENROUTER_REQUIRED_ZERO = ("prompt", "completion")

_cache_lock = asyncio.Lock()
_openrouter_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ollama_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ollama_details_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_ollama_entitlement_cache: dict[str, tuple[float, list[str]]] = {}


class ModelTask(str, Enum):
    """Which JobPilot workload is requesting a model."""

    SIMPLE = "simple"
    MAIN = "main"
    SCORING = "scoring"


def is_auto_model_name(name: str) -> bool:
    normalized = (name or "").strip().lower()
    return normalized in AUTO_MODEL_SENTINELS


def _prefer_rank(model_id: str, prefer_csv: str) -> tuple[int, str]:
    prefixes = [p.strip() for p in prefer_csv.split(",") if p.strip()]
    if not prefixes:
        return (0, model_id)
    for index, prefix in enumerate(prefixes):
        if model_id == prefix or model_id.startswith(prefix):
            return (index, model_id)
    return (len(prefixes) + 1, model_id)


def _parse_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def openrouter_model_is_confirmed_free(pricing: dict[str, Any] | None) -> bool:
    """True when chat input/output prices are present and zero; any other listed price must be zero."""
    if not pricing or not isinstance(pricing, dict):
        return False
    for field in _OPENROUTER_REQUIRED_ZERO:
        parsed = _parse_price(pricing.get(field))
        if parsed is None:
            return False
        if parsed != 0.0:
            return False
    for key, raw in pricing.items():
        if key in _OPENROUTER_REQUIRED_ZERO:
            continue
        parsed = _parse_price(raw)
        if parsed is None:
            return False
        if parsed != 0.0:
            return False
    return True


def _openrouter_supports_chat(record: dict[str, Any], *, min_context: int) -> bool:
    architecture = record.get("architecture") or {}
    outputs = architecture.get("output_modalities") or []
    inputs = architecture.get("input_modalities") or []
    if "text" not in outputs:
        return False
    if inputs and "text" not in inputs:
        return False
    context = record.get("context_length") or 0
    try:
        context_int = int(context)
    except (TypeError, ValueError):
        return False
    return context_int >= min_context


def _min_context_for_task(task: ModelTask) -> int:
    if task == ModelTask.SIMPLE:
        return 4096
    if task == ModelTask.SCORING:
        return 8192
    return 8192


async def fetch_openrouter_models(settings: Settings) -> list[dict[str, Any]]:
    cache_key = hashlib.sha256(settings.openrouter_api_key.strip().encode()).hexdigest()
    ttl = max(30, settings.openrouter_models_cache_seconds)
    now = time.monotonic()
    async with _cache_lock:
        cached = _openrouter_cache.get(cache_key)
        if cached and cached[0] > now:
            return list(cached[1])

    headers = {"Authorization": f"Bearer {settings.openrouter_api_key.strip()}"}
    url = "https://openrouter.ai/api/v1/models"
    timeout = httpx.Timeout(settings.openrouter_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                headers=headers,
                params={"limit": 500, "output_modalities": "text"},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise LLMServiceError(
            LLMErrorKind.NETWORK,
            f"OpenRouter model list unavailable: {exc}",
            retryable=True,
        ) from exc

    data = payload.get("data")
    if not isinstance(data, list):
        raise LLMServiceError(
            LLMErrorKind.SERVER,
            "OpenRouter model list returned unexpected payload",
            retryable=True,
        )

    async with _cache_lock:
        _openrouter_cache[cache_key] = (now + ttl, list(data))
    logger.info(
        "OpenRouter model catalog refreshed",
        count=len(data),
        cache_ttl_seconds=ttl,
    )
    return list(data)


def select_openrouter_free_model(
    records: list[dict[str, Any]],
    *,
    task: ModelTask,
    prefer_csv: str,
) -> str:
    min_context = _min_context_for_task(task)
    candidates: list[str] = []
    for record in records:
        model_id = str(record.get("id") or "").strip()
        if not model_id:
            continue
        if not _openrouter_supports_chat(record, min_context=min_context):
            continue
        if not openrouter_model_is_confirmed_free(record.get("pricing")):
            continue
        candidates.append(model_id)
    if not candidates:
        raise LLMServiceError(
            LLMErrorKind.SERVER,
            "No OpenRouter model with confirmed zero pricing for chat completion",
            retryable=False,
        )
    candidates.sort(key=lambda mid: _prefer_rank(mid, prefer_csv))
    chosen = candidates[0]
    logger.info(
        "OpenRouter auto-selected free model",
        task=task.value,
        model=chosen,
        candidate_count=len(candidates),
    )
    return chosen


async def resolve_openrouter_model(
    settings: Settings,
    configured: str,
    *,
    task: ModelTask,
) -> str:
    if not is_auto_model_name(configured):
        return configured.strip()
    prefer = settings.openrouter_auto_prefer
    if task == ModelTask.SIMPLE:
        prefer = settings.openrouter_simple_auto_prefer or prefer
    records = await fetch_openrouter_models(settings)
    return select_openrouter_free_model(records, task=task, prefer_csv=prefer)


async def fetch_ollama_models(settings: Settings) -> list[dict[str, Any]]:
    cache_key = settings.ollama_base_url.rstrip("/")
    ttl = max(30, settings.ollama_models_cache_seconds)
    now = time.monotonic()
    async with _cache_lock:
        cached = _ollama_cache.get(cache_key)
        if cached and cached[0] > now:
            return list(cached[1])

    headers: dict[str, str] = {}
    if settings.ollama_api_key.strip():
        headers["Authorization"] = f"Bearer {settings.ollama_api_key.strip()}"

    url = f"{cache_key}/api/tags"
    timeout = httpx.Timeout(settings.ollama_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise LLMServiceError(
            LLMErrorKind.NETWORK,
            f"Ollama model list unavailable: {exc}",
            retryable=True,
        ) from exc

    models = payload.get("models")
    if not isinstance(models, list):
        raise LLMServiceError(
            LLMErrorKind.SERVER,
            "Ollama /api/tags returned unexpected payload",
            retryable=True,
        )

    async with _cache_lock:
        _ollama_cache[cache_key] = (now + ttl, list(models))
    logger.info(
        "Ollama model catalog refreshed",
        count=len(models),
        base_url=cache_key,
        cache_ttl_seconds=ttl,
    )
    return list(models)


def _ollama_record_name(record: dict[str, Any]) -> str:
    return str(record.get("name") or record.get("model") or "").strip()


def select_ollama_installed_model(
    records: list[dict[str, Any]],
    *,
    task: ModelTask,
    prefer_csv: str,
) -> str:
    names = [_ollama_record_name(r) for r in records]
    names = [n for n in names if n]
    if not names:
        raise LLMServiceError(
            LLMErrorKind.SERVER,
            "No models installed on configured Ollama server",
            retryable=False,
        )

    if task == ModelTask.SCORING:
        prefer = prefer_csv
        # Prefer larger context / reasoning tags when no explicit prefer list.
        scoring_hints = ("120b", "70b", "gpt-oss", "reason", "scoring")
        if not prefer.strip():

            def score(name: str) -> tuple[int, int, str]:
                lower = name.lower()
                hint_rank = next(
                    (index for index, hint in enumerate(scoring_hints) if hint in lower),
                    len(scoring_hints),
                )
                return (hint_rank, -len(name), name)

            names.sort(key=score)
            chosen = names[0]
            logger.info(
                "Ollama auto-selected scoring model",
                model=chosen,
                installed_count=len(names),
            )
            return chosen

    names.sort(key=lambda mid: _prefer_rank(mid, prefer_csv))
    chosen = names[0]
    logger.info(
        "Ollama auto-selected model",
        task=task.value,
        model=chosen,
        installed_count=len(names),
    )
    return chosen


async def fetch_ollama_free_entitlement(settings: Settings) -> list[str]:
    """Read current account entitlement; never follow an authentication redirect."""
    from app.llm.ollama_free_usage import parse_free_usage_entitlement, parse_settings_account_email
    from app.llm.ollama_usage import _build_cookie_header, _cookies_configured

    if not _cookies_configured(settings):
        raise LLMServiceError(LLMErrorKind.AUTH, 'Ollama settings session cookies are required for free cloud auto', False)
    cookie = _build_cookie_header(settings)
    cache_key = hashlib.sha256((cookie + '\0' + settings.ollama_api_key).encode()).hexdigest()
    cached = _ollama_entitlement_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return list(cached[1])
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.ollama_timeout_seconds), follow_redirects=False) as client:
            response = await client.get('https://ollama.com/settings', headers={'Cookie': cookie, 'Accept': 'text/html'})
            if response.status_code in (301, 302, 303, 307, 308, 401, 403):
                raise LLMServiceError(LLMErrorKind.AUTH, 'Ollama settings session expired or requires verification', False)
            response.raise_for_status()
            models, quota = parse_free_usage_entitlement(response.text)
            session_email = parse_settings_account_email(response.text)
            actor = await client.post('https://ollama.com/api/me', headers={'Authorization': f'Bearer {settings.ollama_api_key}'}, json={})
            actor.raise_for_status()
            api_email = actor.json().get('Email')
            if not isinstance(api_email, str) or api_email.strip().lower() != session_email:
                raise ValueError('Ollama session and API key account identities do not match')
    except (httpx.HTTPError, ValueError) as exc:
        raise LLMServiceError(LLMErrorKind.SERVER, 'Ollama free usage entitlement is unavailable or unrecognized', False) from exc
    _ollama_entitlement_cache[cache_key] = (
        time.monotonic() + min(300, max(30, settings.ollama_models_cache_seconds)), models
    )
    logger.info('Ollama free usage entitlement refreshed', model_count=len(models), used_percent=quota)
    return list(models)


async def resolve_ollama_model(
    settings: Settings,
    configured: str,
    *,
    task: ModelTask,
) -> str:
    if not is_auto_model_name(configured):
        return configured.strip()
    host = urlsplit(settings.ollama_base_url).hostname or ""
    try:
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    entitlement = None
    if not loopback and settings.ollama_base_url.rstrip('/') == 'https://ollama.com':
        entitlement = await fetch_ollama_free_entitlement(settings)
    elif not loopback:
        raise LLMServiceError(
            LLMErrorKind.SERVER,
            "Ollama auto selection requires a local server: cloud model pricing cannot be verified via tags",
            retryable=False,
        )
    prefer = settings.ollama_auto_prefer
    if task == ModelTask.SCORING:
        prefer = settings.ollama_scoring_auto_prefer or prefer
    records = await fetch_ollama_models(settings)
    if entitlement is not None:
        allowed = set(entitlement)
        records = [record for record in records if isinstance(record, dict) and (
            _ollama_record_name(record) in allowed
            or _ollama_record_name(record).removesuffix(':latest') in allowed
        )]
    headers = {"Authorization": f"Bearer {settings.ollama_api_key.strip()}"} if settings.ollama_api_key.strip() else {}
    compatible = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.ollama_timeout_seconds)) as client:
        for record in records:
            if not isinstance(record, dict):
                continue
            name = _ollama_record_name(record)
            if not name:
                continue
            try:
                cache_key = (settings.ollama_base_url.rstrip('/'), name)
                cached = _ollama_details_cache.get(cache_key)
                if cached and cached[0] > time.monotonic():
                    details = cached[1]
                else:
                    response = await client.post(f"{cache_key[0]}/api/show", headers=headers, json={"model": name})
                    response.raise_for_status()
                    details = response.json()
                    _ollama_details_cache[cache_key] = (
                        time.monotonic() + max(30, settings.ollama_models_cache_seconds), details
                    )
                caps = details.get("capabilities") or []
                info = details.get("model_info") or {}
                contexts = [v for k, v in info.items() if k.endswith(".context_length") and type(v) is int]
                if "completion" in caps and contexts and max(contexts) >= _min_context_for_task(task):
                    compatible.append(record)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                logger.warning("Ollama model details unavailable", model=name)
    return select_ollama_installed_model(compatible, task=task, prefer_csv=prefer)


def invalidate_openrouter_catalog() -> None:
    _openrouter_cache.clear()


def invalidate_ollama_catalog() -> None:
    _ollama_cache.clear()
    _ollama_details_cache.clear()
    _ollama_entitlement_cache.clear()


def configured_model_label(settings: Settings, *, provider: str, bucket: str) -> str:
    """Human-readable configured or auto sentinel for stage logs."""
    if provider == "openrouter":
        if bucket == "simple":
            return settings.openrouter_simple_model
        return settings.openrouter_model
    if bucket == "scoring":
        return settings.ollama_scoring_model
    if provider == "ollama" or bucket == "simple":
        return settings.ollama_model
    if bucket == "main":
        return settings.openai_model
    return settings.openai_simple_model
