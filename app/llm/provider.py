import asyncio
from abc import ABC, abstractmethod
from typing import Any, Literal

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, PermissionDeniedError

from app.config import Settings, get_settings
from app.llm.errors import LLMErrorKind, LLMServiceError, classify_llm_error
from app.utils.proxy import (
    build_httpx_async_client,
    get_proxy_candidates,
    mark_proxy_failed,
    mask_proxy,
)

logger = structlog.get_logger(__name__)


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        pass

    @abstractmethod
    def get_chat_model(self) -> BaseChatModel:
        pass


class CursorProvider(LLMProvider):
    """LLM provider backed by Cursor SDK (Agent.prompt one-shot)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError

        model = kwargs.get("model", self._settings.cursor_model)
        message = f"{system_prompt.strip()}\n\n---\n\n{user_prompt.strip()}"

        def _run_prompt() -> str:
            try:
                # No local workspace: JobPilot only needs text completion, not codebase context.
                result = Agent.prompt(
                    message,
                    AgentOptions(
                        api_key=self._settings.cursor_api_key,
                        model=model,
                    ),
                )
            except CursorAgentError as exc:
                logger.error(
                    "Cursor SDK startup failed",
                    error=exc.message,
                    retryable=exc.is_retryable,
                )
                raise RuntimeError(f"Cursor SDK error: {exc.message}") from exc

            if result.status == "error":
                raise RuntimeError(f"Cursor agent run failed: {result.id}")

            text = result.result
            if text is None:
                return ""
            return text if isinstance(text, str) else str(text)

        logger.info("JobPilot AI Cursor SDK prompt", model=model)
        return await asyncio.to_thread(_run_prompt)

    def get_chat_model(self) -> BaseChatModel:
        raise NotImplementedError("Cursor SDK provider does not expose a LangChain chat model")


class OpenAIProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _build_client(self, proxy: str | None) -> AsyncOpenAI:
        http_client = build_httpx_async_client(
            proxy=proxy,
            timeout_seconds=self._settings.openai_timeout_seconds,
        )
        return AsyncOpenAI(
            api_key=self._settings.openai_api_key,
            timeout=self._settings.openai_timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        temperature = kwargs.get("temperature", 0.7)
        proxies = get_proxy_candidates(self._settings, "openai")
        last_error: Exception | None = None

        for proxy in proxies:
            if proxy:
                logger.info("OpenAI request via proxy", proxy=mask_proxy(proxy))
            client = self._build_client(proxy)
            try:
                response = await client.responses.create(
                    model=self._settings.openai_model,
                    instructions=system_prompt,
                    input=[
                        {
                            "role": "user",
                            "content": user_prompt,
                        }
                    ],
                    temperature=temperature,
                )
                text = getattr(response, "output_text", None)
                if text is None:
                    return ""
                return text if isinstance(text, str) else str(text)
            except (PermissionDeniedError, APIConnectionError, APIStatusError) as exc:
                last_error = exc
                if proxy and self._should_rotate_proxy(exc):
                    logger.warning(
                        "OpenAI proxy failed, rotating",
                        proxy=mask_proxy(proxy),
                        error=str(exc),
                    )
                    mark_proxy_failed(self._settings, "openai", proxy)
                    continue
                raise
            finally:
                await client.close()

        if last_error:
            raise last_error
        return ""

    @staticmethod
    def _should_rotate_proxy(exc: Exception) -> bool:
        if isinstance(exc, PermissionDeniedError):
            return True
        if isinstance(exc, APIConnectionError):
            return True
        if isinstance(exc, APIStatusError) and exc.status_code in {407, 408, 429, 500, 502, 503, 504}:
            return True
        message = str(exc).lower()
        return "unsupported_country" in message or "unsupported_country_region_territory" in message

    def get_chat_model(self) -> BaseChatModel:
        raise NotImplementedError("OpenAI SDK provider does not expose a LangChain chat model")


class OllamaProvider(LLMProvider):
    """Ollama chat via official ollama-python SDK (local or https://ollama.com)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self, proxy: str | None) -> "AsyncClient":
        from ollama import AsyncClient

        headers: dict[str, str] = {}
        if self._settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {self._settings.ollama_api_key}"

        client_kwargs: dict[str, Any] = {
            "timeout": self._settings.ollama_timeout_seconds,
        }
        if proxy:
            client_kwargs["proxy"] = proxy

        return AsyncClient(
            host=self._settings.ollama_base_url.rstrip("/"),
            headers=headers,
            **client_kwargs,
        )

    async def _chat_once(
        self,
        proxy: str | None,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        from ollama import ResponseError

        client = self._client(proxy)
        try:
            response = await asyncio.wait_for(
                client.chat(
                    model=self._settings.ollama_model,
                    messages=messages,
                    options={"temperature": temperature},
                ),
                timeout=self._settings.ollama_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise classify_llm_error(exc) from exc
        except ResponseError as exc:
            logger.error(
                "Ollama request failed",
                model=self._settings.ollama_model,
                status_code=exc.status_code,
                error=exc.error,
                proxy=mask_proxy(proxy) if proxy else None,
            )
            raise classify_llm_error(exc) from exc
        except Exception as exc:
            raise classify_llm_error(exc) from exc
        finally:
            await client.close()

        content = response.message.content
        logger.info(
            "Ollama response",
            model=self._settings.ollama_model,
            proxy=mask_proxy(proxy) if proxy else None,
        )
        return content if isinstance(content, str) else str(content)

    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        temperature = kwargs.get("temperature", 0.3)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        proxies = get_proxy_candidates(self._settings, "ollama")
        last_error: LLMServiceError | None = None

        for proxy in proxies:
            if proxy:
                logger.info("Ollama request via proxy", proxy=mask_proxy(proxy))
            try:
                return await self._chat_once(proxy, messages, temperature)
            except LLMServiceError as exc:
                last_error = exc
                if proxy and exc.retryable and exc.kind in {
                    LLMErrorKind.NETWORK,
                    LLMErrorKind.SERVER,
                    LLMErrorKind.RATE_LIMIT,
                }:
                    logger.warning(
                        "Ollama proxy failed, rotating",
                        proxy=mask_proxy(proxy),
                        kind=exc.kind.value,
                        error=exc.message,
                    )
                    mark_proxy_failed(self._settings, "ollama", proxy)
                    continue
                raise

        if last_error:
            raise last_error
        raise LLMServiceError(
            kind=LLMErrorKind.NETWORK,
            message="No working proxy available for Ollama",
            retryable=False,
        )

    def get_chat_model(self) -> BaseChatModel:
        raise NotImplementedError("Ollama provider does not expose a LangChain chat model")


class AnthropicProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = ChatAnthropic(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            temperature=0.7,
        )

    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        temperature = kwargs.get("temperature", 0.7)
        model = ChatAnthropic(
            api_key=self._settings.anthropic_api_key,
            model=self._settings.anthropic_model,
            temperature=temperature,
        )
        response = await model.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        content = response.content
        return content if isinstance(content, str) else str(content)

    def get_chat_model(self) -> BaseChatModel:
        return self._model


def _build_provider(
    cfg: Settings,
    provider: Literal["openai", "anthropic", "cursor", "ollama"],
) -> LLMProvider:
    if provider == "cursor":
        logger.info("JobPilot AI using Cursor SDK", model=cfg.cursor_model)
        return CursorProvider(cfg)
    if provider == "anthropic":
        logger.info("JobPilot AI using Anthropic LLM", model=cfg.anthropic_model)
        return AnthropicProvider(cfg)
    if provider == "ollama":
        logger.info("JobPilot AI using Ollama", model=cfg.ollama_model, base_url=cfg.ollama_base_url)
        return OllamaProvider(cfg)
    logger.info("JobPilot AI using OpenAI LLM", model=cfg.openai_model)
    return OpenAIProvider(cfg)


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    cfg = settings or get_settings()
    return _build_provider(cfg, cfg.llm_provider)


def get_simple_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Filter, scoring, chat, learning — cheaper/faster models."""
    cfg = settings or get_settings()
    simple = cfg.llm_simple_provider
    if simple == "same":
        return get_llm_provider(cfg)
    return _build_provider(cfg, simple)
