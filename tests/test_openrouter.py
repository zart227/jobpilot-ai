import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APIStatusError

from app.config import Settings
from app.llm.errors import LLMErrorKind, LLMServiceError
from app.llm.model_catalog import ModelTask
from app.llm.provider import OpenRouterProvider, get_llm_provider, get_simple_llm_provider
from app.services.pipeline_llm_ledger import stage_entry
from app.utils.proxy import get_service_proxy_list


def config(**updates):
    return Settings(_env_file=None, **{
        'LLM_PROVIDER': 'openrouter', 'LLM_SIMPLE_PROVIDER': 'openrouter',
        'OPENROUTER_API_KEY': 'test-key', 'PROXY_ENABLED': False,
        'OPENROUTER_MODEL': 'main:free', 'OPENROUTER_SIMPLE_MODEL': 'small:free',
        **updates,
    })


def test_routing_and_labels():
    cfg = config()
    assert get_llm_provider(cfg)._model == 'main:free'
    assert get_simple_llm_provider(cfg)._model == 'small:free'
    assert stage_entry('filter', 'simple', cfg)['provider'] == 'openrouter:small:free'
    assert stage_entry('proposal', 'main', cfg)['provider'] == 'openrouter:main:free'
    assert stage_entry('score', 'scoring', cfg)['provider'] == 'openrouter:main:free'
    same = config(LLM_SIMPLE_PROVIDER='same')
    assert stage_entry('filter', 'simple', same)['provider'] == 'openrouter:main:free'
    assert get_service_proxy_list(cfg, 'openrouter') is None


def test_missing_key():
    with pytest.raises(ValueError, match='OPENROUTER_API_KEY'):
        OpenRouterProvider(config(OPENROUTER_API_KEY=''))


@pytest.mark.asyncio
@pytest.mark.parametrize(('retryable', 'enabled'), [(True, True), (False, True), (True, False)])
async def test_catalog_failure_fallback_is_free_and_task_aware(retryable, enabled):
    cfg = config(OPENROUTER_MODEL='auto', OLLAMA_MODEL='potentially-paid',
                 OPENROUTER_FALLBACK_TO_OLLAMA=enabled)
    provider = OpenRouterProvider(cfg, task=ModelTask.SCORING)
    error = LLMServiceError(LLMErrorKind.NETWORK, 'catalog unavailable', retryable)
    with patch.object(provider, '_resolve_model', new_callable=AsyncMock, side_effect=error), \
         patch('app.llm.provider.OllamaProvider') as fallback, \
         patch('app.llm.provider.AsyncOpenAI') as client:
        fallback.return_value.complete = AsyncMock(return_value='result')
        if retryable and enabled:
            assert await provider.complete('system', 'user') == 'result'
            assert fallback.call_args.args[0].ollama_model == 'auto'
            assert fallback.call_args.args[0].ollama_auto_fallback is False
            assert fallback.call_args.kwargs['task'] == ModelTask.SCORING
        else:
            with pytest.raises(LLMServiceError):
                await provider.complete('system', 'user')
            fallback.assert_not_called()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,content,finish', [
    (200, '{"relevant": true}', 'stop'),
    (429, None, None), (402, None, None), (503, None, None),
    (401, None, None), (200, '', 'stop'), (200, 'partial', 'length'),
])
async def test_api_and_fallback(status, content, finish):
    seen = []

    def handler(request):
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json={'error': {'message': 'unavailable'}})
        return httpx.Response(200, json={
            'id': 'generation-1', 'object': 'chat.completion', 'created': 1,
            'model': 'small:free', 'choices': [{
                'index': 0, 'finish_reason': finish,
                'message': {'role': 'assistant', 'content': content},
            }],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fallback = AsyncMock(return_value='ollama-result')
    with patch('app.llm.provider.build_httpx_async_client', return_value=client), \
         patch('app.llm.provider.OllamaProvider') as ollama:
        ollama.return_value.complete = fallback
        provider = get_simple_llm_provider(config())
        if status == 401:
            with pytest.raises(APIStatusError):
                await provider.complete('system', 'user')
            fallback.assert_not_awaited()
        else:
            result = await provider.complete('system', 'user', temperature=0.2)
            if status == 200 and content and finish == 'stop':
                assert result == content
                fallback.assert_not_awaited()
            else:
                assert result == 'ollama-result'
                assert ollama.call_args.args[0].ollama_auto_fallback is False
                fallback.assert_awaited_once_with('system', 'user', temperature=0.2)
    assert client.is_closed
    assert len(seen) == 1
    payload = json.loads(seen[0].content)
    assert str(seen[0].url) == 'https://openrouter.ai/api/v1/chat/completions'
    assert payload['model'] == 'small:free'
    assert payload['messages'] == [
        {'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'user'},
    ]


@pytest.mark.asyncio
async def test_disabled_fallback_propagates_rate_limit():
    client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(429, json={'error': {'message': 'rate limited'}})
        ))
    with patch('app.llm.provider.build_httpx_async_client', return_value=client), \
         patch('app.llm.provider.OllamaProvider') as fallback:
        provider = OpenRouterProvider(config(OPENROUTER_FALLBACK_TO_OLLAMA=False))
        with pytest.raises(APIStatusError):
            await provider.complete('system', 'user')
        fallback.assert_not_called()
    assert client.is_closed
