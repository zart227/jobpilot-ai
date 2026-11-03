"""Tests for OpenRouter/Ollama model catalog and auto selection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings
from app.llm.errors import LLMServiceError
from app.llm.model_catalog import (
    ModelTask,
    fetch_openrouter_models,
    invalidate_openrouter_catalog,
    openrouter_model_is_confirmed_free,
    resolve_ollama_model,
    resolve_openrouter_model,
    select_ollama_installed_model,
    select_openrouter_free_model,
)
from app.llm.provider import OpenRouterProvider, get_simple_llm_provider


def settings(**updates):
    return Settings(
        _env_file=None,
        LLM_PROVIDER="openrouter",
        LLM_SIMPLE_PROVIDER="openrouter",
        OPENROUTER_API_KEY="test-key",
        PROXY_ENABLED=False,
        **updates,
    )


@pytest.mark.asyncio
async def test_openrouter_auto_selection_rechecks_catalog_cache():
    provider = OpenRouterProvider(settings(), model="auto", task=ModelTask.SIMPLE)
    with patch('app.llm.provider.resolve_openrouter_model', new_callable=AsyncMock,
               side_effect=['first:free', 'second:free']) as resolve:
        assert await provider._resolve_model() == 'first:free'
        assert await provider._resolve_model() == 'second:free'
        assert resolve.await_count == 2


def test_openrouter_free_requires_prompt_and_completion_zero():
    assert openrouter_model_is_confirmed_free({"prompt": "0", "completion": "0"})
    assert openrouter_model_is_confirmed_free({"prompt": "0", "completion": "0", "request": "0"})
    assert not openrouter_model_is_confirmed_free({"prompt": "0", "completion": "0.00001"})
    assert not openrouter_model_is_confirmed_free({"prompt": "0"})
    assert not openrouter_model_is_confirmed_free(None)


def test_select_openrouter_free_stable_and_prefers_prefix():
    records = [
        {
            "id": "vendor/z-free",
            "context_length": 8192,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        },
        {
            "id": "vendor/a-free",
            "context_length": 8192,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        },
        {
            "id": "vendor/paid",
            "context_length": 8192,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0.00001", "completion": "0", "request": "0", "image": "0"},
        },
    ]
    assert select_openrouter_free_model(records, task=ModelTask.SIMPLE, prefer_csv="") == "vendor/a-free"
    assert (
        select_openrouter_free_model(records, task=ModelTask.SIMPLE, prefer_csv="vendor/z")
        == "vendor/z-free"
    )


def test_select_openrouter_free_empty_raises():
    with pytest.raises(LLMServiceError, match="confirmed zero pricing"):
        select_openrouter_free_model([], task=ModelTask.MAIN, prefer_csv="")


@pytest.mark.asyncio
async def test_resolve_openrouter_explicit_skips_catalog():
    cfg = settings(OPENROUTER_SIMPLE_MODEL="vendor/explicit:free")
    resolved = await resolve_openrouter_model(cfg, "vendor/explicit:free", task=ModelTask.SIMPLE)
    assert resolved == "vendor/explicit:free"


@pytest.mark.asyncio
async def test_fetch_openrouter_models_uses_cache():
    invalidate_openrouter_catalog()
    cfg = settings(OPENROUTER_MODELS_CACHE_SECONDS=600)
    payload = {"data": [{"id": "x/y", "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"}}]}

    mock_client = AsyncMock()
    request = httpx.Request("GET", "https://openrouter.ai/api/v1/models")
    mock_client.get = AsyncMock(
        return_value=httpx.Response(200, json=payload, request=request),
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.llm.model_catalog.httpx.AsyncClient", return_value=mock_client):
        first = await fetch_openrouter_models(cfg)
        second = await fetch_openrouter_models(cfg)
    assert first == second
    assert mock_client.get.await_count == 1


@pytest.mark.asyncio
async def test_openrouter_auto_selects_free_model_in_request():
    catalog = [
        {
            "id": "picked/free",
            "context_length": 8192,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        }
    ]
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content="ok")
    )])
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                             close=AsyncMock())

    with patch("app.llm.provider.get_proxy_candidates", return_value=[None]), \
         patch("app.llm.provider.build_httpx_async_client"), \
         patch("app.llm.provider.AsyncOpenAI", return_value=client), \
         patch(
             "app.llm.model_catalog.fetch_openrouter_models",
             AsyncMock(return_value=catalog),
         ):
        provider = get_simple_llm_provider(settings(OPENROUTER_SIMPLE_MODEL="auto"))
        result = await provider.complete("system", "user")
    assert result == "ok"
    assert create.await_args.kwargs["model"] == "picked/free"


@pytest.mark.asyncio
async def test_openrouter_auto_does_not_pick_paid_suffix_only():
    catalog = {
        "data": [
            {
                "id": "vendor/fake:free",
                "context_length": 8192,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "pricing": {"prompt": "0.00001", "completion": "0", "request": "0", "image": "0"},
            }
        ]
    }

    with patch("app.llm.provider.get_proxy_candidates", return_value=[None]), \
         patch("app.llm.model_catalog.fetch_openrouter_models", AsyncMock(return_value=catalog["data"])), \
         patch("app.llm.provider.AsyncOpenAI") as openai, \
         patch("app.llm.provider.OllamaProvider") as ollama:
        ollama.return_value.complete = AsyncMock(return_value="fallback")
        provider = OpenRouterProvider(settings(OPENROUTER_MODEL="auto"), task=ModelTask.MAIN)
        with pytest.raises(LLMServiceError, match="confirmed zero pricing"):
            await provider.complete("system", "user")
        ollama.assert_not_called()
        openai.assert_not_called()


def test_ollama_auto_prefers_scoring_hint():
    records = [{"name": "nemotron-3-nano:30b"}, {"name": "gpt-oss:120b"}]
    assert (
        select_ollama_installed_model(records, task=ModelTask.SCORING, prefer_csv="")
        == "gpt-oss:120b"
    )


@pytest.mark.asyncio
async def test_ollama_auto_selects_only_confirmed_local_chat_models():
    from app.llm.model_catalog import invalidate_ollama_catalog

    invalidate_ollama_catalog()
    cfg = settings(OLLAMA_BASE_URL='http://127.0.0.1:11434')
    records = [{'name': 'a-embedding'}, {'name': 'b-small-context'}, {'name': 'c-chat'}]

    def handler(request):
        import json

        name = json.loads(request.content)['model']
        caps = ['embedding'] if name == 'a-embedding' else ['completion']
        context = 512 if name == 'b-small-context' else 8192
        return httpx.Response(200, json={'capabilities': caps, 'model_info': {'test.context_length': context}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch('app.llm.model_catalog.fetch_ollama_models', AsyncMock(return_value=records)), \
         patch('app.llm.model_catalog.httpx.AsyncClient', return_value=client):
        assert await resolve_ollama_model(cfg, 'auto', task=ModelTask.SIMPLE) == 'c-chat'


@pytest.mark.asyncio
async def test_ollama_auto_rejects_cloud_without_account_session():
    cfg = settings(OLLAMA_BASE_URL='https://ollama.com', OLLAMA_SESSION_COOKIE='', OLLAMA_AID='')
    with pytest.raises(LLMServiceError, match='session cookies are required'):
        await resolve_ollama_model(cfg, 'auto', task=ModelTask.SIMPLE)


@pytest.mark.asyncio
async def test_ollama_cloud_auto_uses_current_entitlement_and_excludes_retired():
    from app.llm.model_catalog import invalidate_ollama_catalog

    invalidate_ollama_catalog()
    cfg = settings(OLLAMA_BASE_URL='https://ollama.com', OLLAMA_SESSION_COOKIE='test-cookie', OLLAMA_AID='test-aid')
    records = [{'name': 'a-paid'}, {'name': 'b-retiring'}, {'name': 'c-chat'}]
    html = '''<details id="settings-account-menu"><span>same@example.test</span></details>
    <span id="extra-usage-balance">$0</span><button id="extra-usage-reload-toggle" aria-checked="false"></button>
    <div id="free-plan-models"><p>Free usage credits can be used with the following cloud models:</p>
    <ul><li><a>b-retiring</a></li><li><a>c-chat</a></li></ul></div>
    <p>The following models will be retired soon</p><ul><li>b-retiring in 1 week</li></ul>
    <span>Free usage</span><div aria-label="Free usage: 0%"></div>'''
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/settings':
            return httpx.Response(200, text=html)
        if request.url.path == '/api/me':
            return httpx.Response(200, json={'Email': 'same@example.test'})
        return httpx.Response(200, json={'capabilities': ['completion'], 'model_info': {'x.context_length': 8192}})

    original = httpx.AsyncClient
    def client_factory(**kwargs):
        return original(transport=httpx.MockTransport(handler))

    with patch('app.llm.model_catalog.fetch_ollama_models', AsyncMock(return_value=records)), \
         patch('app.llm.model_catalog.httpx.AsyncClient', side_effect=client_factory):
        assert await resolve_ollama_model(cfg, 'auto', task=ModelTask.SIMPLE) == 'c-chat'
        assert await resolve_ollama_model(cfg, 'auto', task=ModelTask.SIMPLE) == 'c-chat'
    assert calls.count('/settings') == 1
    assert calls.count('/api/me') == 1
    assert calls.count('/api/show') == 1


@pytest.mark.asyncio
async def test_ollama_cloud_auto_does_not_follow_expired_session_redirect():
    from app.llm.model_catalog import invalidate_ollama_catalog

    invalidate_ollama_catalog()
    cfg = settings(OLLAMA_BASE_URL='https://ollama.com', OLLAMA_SESSION_COOKIE='expired', OLLAMA_AID='test')
    original = httpx.AsyncClient
    def client_factory(**kwargs):
        assert kwargs.get('follow_redirects') is False
        return original(transport=httpx.MockTransport(lambda request: httpx.Response(303, headers={'Location': 'https://example.invalid/'})))

    with patch('app.llm.model_catalog.httpx.AsyncClient', side_effect=client_factory):
        with pytest.raises(LLMServiceError, match='session expired'):
            await resolve_ollama_model(cfg, 'auto', task=ModelTask.SIMPLE)


@pytest.mark.asyncio
async def test_ollama_cloud_auto_rejects_different_account_key():
    from app.llm.model_catalog import invalidate_ollama_catalog

    invalidate_ollama_catalog()
    cfg = settings(OLLAMA_BASE_URL='https://ollama.com', OLLAMA_SESSION_COOKIE='cookie', OLLAMA_AID='aid')
    html = '''<details id="settings-account-menu"><span>one@example.test</span></details>
    <span id="extra-usage-balance">$0</span><button id="extra-usage-reload-toggle" aria-checked="false"></button>
    <div id="free-plan-models"><p>Free usage credits can be used with the following cloud models:</p>
    <ul><li><a>free-model</a></li></ul></div><div aria-label="Free usage: 0%"></div>'''
    original = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, text=html) if request.url.path == '/settings' else httpx.Response(200, json={'Email': 'two@example.test'})
    def client_factory(**kwargs):
        return original(transport=httpx.MockTransport(handler))

    with patch('app.llm.model_catalog.httpx.AsyncClient', side_effect=client_factory):
        with pytest.raises(LLMServiceError, match='entitlement is unavailable or unrecognized'):
            await resolve_ollama_model(cfg, 'auto', task=ModelTask.SIMPLE)


@pytest.mark.asyncio
async def test_ollama_auto_rechecks_selection_and_forbids_paid_fallback():
    from app.llm.provider import OllamaProvider, get_simple_llm_provider

    cfg = settings(OLLAMA_BASE_URL='http://localhost:11434', OLLAMA_AUTO_FALLBACK=True,
                   OPENAI_API_KEY='test-key')
    provider = OllamaProvider(cfg, model='auto')
    with patch('app.llm.provider.resolve_ollama_model', AsyncMock(side_effect=['first', 'second'])) as resolve:
        assert await provider._resolve_model() == 'first'
        assert await provider._resolve_model() == 'second'
        assert resolve.await_count == 2
    assert not provider._can_fallback_to_openai()
    cfg = cfg.model_copy(update={'llm_simple_provider': 'ollama', 'ollama_model': 'auto'})
    with patch('app.llm.ollama_usage.get_ollama_usage_sync') as usage:
        assert isinstance(get_simple_llm_provider(cfg), OllamaProvider)
        usage.assert_not_called()
