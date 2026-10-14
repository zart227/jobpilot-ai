from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.llm.provider import (
    OllamaProvider,
    OpenAIProvider,
    get_llm_provider,
    get_scoring_llm_provider,
    get_simple_llm_provider,
)


def settings():
    return Settings(
        _env_file=None, LLM_PROVIDER='openai', LLM_SIMPLE_PROVIDER='ollama',
        OPENAI_API_KEY='test', OPENAI_MODEL='main-gpt', OPENAI_SIMPLE_MODEL='small-gpt',
        OLLAMA_MODEL='nemotron-3-nano:30b', OLLAMA_SCORING_MODEL='gpt-oss:120b',
        OLLAMA_AUTO_FALLBACK=False,
    )


def test_routing():
    cfg = settings()
    routine = get_simple_llm_provider(cfg)
    scoring = get_scoring_llm_provider(cfg)
    main = get_llm_provider(cfg)
    assert isinstance(routine, OllamaProvider)
    assert routine._model == 'nemotron-3-nano:30b'
    assert isinstance(scoring, OllamaProvider)
    assert scoring._model == 'gpt-oss:120b'
    assert scoring._fallback_model == 'main-gpt'
    assert isinstance(main, OpenAIProvider)
    assert main._model == 'main-gpt'


@pytest.mark.asyncio
async def test_selected_model_sent_to_ollama():
    provider = get_scoring_llm_provider(settings())
    client = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(message=SimpleNamespace(content='ok'))),
        close=AsyncMock(),
    )
    with patch.object(provider, '_client', return_value=client):
        assert await provider._chat_once(None, [], 0.2) == 'ok'
    assert client.chat.call_args.kwargs['model'] == 'gpt-oss:120b'
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_scoring_fallback_uses_main_openai():
    provider = get_scoring_llm_provider(settings())
    with patch('app.llm.provider.OpenAIProvider') as constructor:
        constructor.return_value.complete = AsyncMock(return_value='fallback')
        assert await provider._fallback_to_openai('system', 'user', reason='quota') == 'fallback'
    assert constructor.call_args.kwargs['model'] == 'main-gpt'
