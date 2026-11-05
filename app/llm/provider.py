import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, PermissionDeniedError

from app.config import Settings, get_settings
from app.llm.call_usage import LLMCallUsage, usage_from_openai_compat_usage
from app.llm.errors import LLMErrorKind, LLMServiceError, classify_llm_error
from app.llm.model_catalog import (
    ModelTask,
    invalidate_ollama_catalog,
    invalidate_openrouter_catalog,
    is_auto_model_name,
    resolve_ollama_model,
    resolve_openrouter_model,
)
from app.llm.ollama_usage import get_ollama_usage
from app.utils.proxy import (
    build_httpx_async_client,
    get_proxy_candidates,
    mark_proxy_failed,
    mask_proxy,
)

if TYPE_CHECKING:
    from ollama import AsyncClient

logger = structlog.get_logger(__name__)


class LLMProvider(ABC):
    def __init__(self) -> None:
        self._last_call_usage: LLMCallUsage | None = None

    def consume_last_call_usage(self) -> LLMCallUsage | None:
        usage = self._last_call_usage
        self._last_call_usage = None
        return usage

    def _set_last_call_usage(self, usage: LLMCallUsage | None) -> None:
        self._last_call_usage = usage

    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        pass

    @abstractmethod
    def get_chat_model(self) -> BaseChatModel:
        pass


class CursorProvider(LLMProvider):
    """LLM provider backed by Cursor SDK (Agent.prompt one-shot)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
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
    def __init__(self, settings: Settings, *, model: str | None = None) -> None:
        super().__init__()
        self._settings = settings
        self._model = model or settings.openai_model

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
                    model=self._model,
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
                usage = usage_from_openai_compat_usage(
                    self._model, "openai", getattr(response, "usage", None)
                )
                self._set_last_call_usage(usage)
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


class OpenRouterProvider(LLMProvider):
    """OpenAI-compatible chat API with an optional, non-paid Ollama fallback."""

    def __init__(
        self,
        settings: Settings,
        *,
        model: str | None = None,
        task: ModelTask = ModelTask.MAIN,
    ) -> None:
        if not settings.openrouter_api_key.strip():
            raise ValueError("OPENROUTER_API_KEY is required when using OpenRouter")
        super().__init__()
        self._settings = settings
        self._model = model or settings.openrouter_model
        self._task = task
        self._resolved_model: str | None = None

    async def _resolve_model(self, *, refresh: bool = False) -> str:
        if refresh:
            invalidate_openrouter_catalog()
            self._resolved_model = None
        if self._resolved_model and not is_auto_model_name(self._model):
            return self._resolved_model
        self._resolved_model = await resolve_openrouter_model(
            self._settings,
            self._model,
            task=self._task,
        )
        return self._resolved_model

    async def _fallback(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        updates: dict[str, Any] = {"ollama_auto_fallback": False}
        if is_auto_model_name(self._model):
            updates["ollama_model"] = "auto"
        fallback_settings = self._settings.model_copy(update=updates)
        ollama = OllamaProvider(fallback_settings, task=self._task)
        text = await ollama.complete(system_prompt, user_prompt, **kwargs)
        self._set_last_call_usage(ollama.consume_last_call_usage())
        return text

    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        # Free-model limits are shared across proxies; do not rotate on 429.
        proxy = get_proxy_candidates(self._settings, "openrouter")[0]
        last_model_error: APIStatusError | None = None
        for attempt in range(2):
            try:
                model = await self._resolve_model(refresh=attempt > 0)
            except LLMServiceError as exc:
                if not exc.retryable or not self._settings.openrouter_fallback_to_ollama:
                    raise
                logger.warning("OpenRouter catalog unavailable, falling back to Ollama")
                return await self._fallback(system_prompt, user_prompt, **kwargs)
            client = AsyncOpenAI(
                api_key=self._settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=self._settings.openrouter_timeout_seconds,
                max_retries=0,
                http_client=build_httpx_async_client(
                    proxy=proxy, timeout_seconds=self._settings.openrouter_timeout_seconds,
                ),
            )
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=kwargs.get("temperature", 0.7),
                )
                if not response.choices:
                    raise LLMServiceError(LLMErrorKind.SERVER, "OpenRouter returned no choices", True)
                choice = response.choices[0]
                content = choice.message.content
                if choice.finish_reason == "length" or not content or not content.strip():
                    raise LLMServiceError(LLMErrorKind.SERVER, "OpenRouter returned incomplete text", True)
                self._set_last_call_usage(
                    usage_from_openai_compat_usage(model, "openrouter", response.usage)
                )
                return content
            except APIStatusError as exc:
                if exc.status_code == 404 and attempt == 0 and is_auto_model_name(self._model):
                    last_model_error = exc
                    logger.warning(
                        "OpenRouter auto-selected model unavailable, refreshing catalog",
                        model=model,
                    )
                    continue
                recoverable = exc.status_code in {402, 408, 429, 500, 502, 503, 504}
                if recoverable and self._settings.openrouter_fallback_to_ollama:
                    logger.warning(
                        "OpenRouter unavailable, falling back to Ollama",
                        configured_model=self._model,
                        status_code=exc.status_code,
                    )
                    return await self._fallback(system_prompt, user_prompt, **kwargs)
                raise
            except (APIConnectionError, LLMServiceError) as exc:
                recoverable = (
                    isinstance(exc, APIConnectionError)
                    or isinstance(exc, LLMServiceError) and exc.retryable
                )
                if not recoverable or not self._settings.openrouter_fallback_to_ollama:
                    raise
                logger.warning(
                    "OpenRouter unavailable, falling back to Ollama",
                    configured_model=self._model,
                    resolved_model=model,
                )
                return await self._fallback(system_prompt, user_prompt, **kwargs)
            finally:
                await client.close()
        if last_model_error:
            raise last_model_error
        raise LLMServiceError(
            LLMErrorKind.SERVER,
            "OpenRouter completion failed after model catalog refresh",
            retryable=False,
        )

    def get_chat_model(self) -> BaseChatModel:
        raise NotImplementedError("OpenRouter provider does not expose a LangChain chat model")


class OllamaProvider(LLMProvider):
    """Ollama chat via official ollama-python SDK (local or https://ollama.com)."""

    def __init__(
        self,
        settings: Settings,
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        task: ModelTask = ModelTask.SIMPLE,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._model = model or settings.ollama_model
        self._fallback_model = fallback_model or settings.openai_simple_model
        self._task = task
        self._resolved_model: str | None = None

    async def _resolve_model(self, *, refresh: bool = False) -> str:
        if refresh:
            invalidate_ollama_catalog()
            self._resolved_model = None
        if self._resolved_model and not is_auto_model_name(self._model):
            return self._resolved_model
        self._resolved_model = await resolve_ollama_model(
            self._settings,
            self._model,
            task=self._task,
        )
        return self._resolved_model

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
        *,
        model: str,
    ) -> str:
        from ollama import ResponseError

        client = self._client(proxy)
        try:
            response = await asyncio.wait_for(
                client.chat(
                    model=model,
                    messages=messages,
                    options={"temperature": temperature},
                ),
                timeout=self._settings.ollama_timeout_seconds,
            )
        except TimeoutError as exc:
            raise classify_llm_error(exc) from exc
        except ResponseError as exc:
            logger.error(
                "Ollama request failed",
                model=model,
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
        if not isinstance(content, str) or not content.strip() or getattr(response, 'done_reason', None) == 'length':
            raise LLMServiceError(LLMErrorKind.SERVER, 'Ollama returned empty or truncated text', retryable=True)
        logger.info(
            "Ollama response",
            model=model,
            proxy=mask_proxy(proxy) if proxy else None,
        )
        prompt = getattr(response, "prompt_eval_count", None)
        completion = getattr(response, "eval_count", None)
        total = None
        if prompt is not None and completion is not None:
            total = int(prompt) + int(completion)
        self._set_last_call_usage(
            LLMCallUsage(
                resolved_model=model,
                provider_kind="ollama",
                prompt_tokens=int(prompt) if prompt is not None else None,
                completion_tokens=int(completion) if completion is not None else None,
                total_tokens=total,
                cost_usd=0.0,
            )
        )
        return content

    def _can_fallback_to_openai(self) -> bool:
        return bool(not is_auto_model_name(self._model) and self._settings.ollama_auto_fallback and self._settings.openai_api_key)

    async def _fallback_to_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        reason: str,
        **kwargs: Any,
    ) -> str:
        model = self._fallback_model
        logger.warning(
            "Ollama limits exceeded, falling back to OpenAI",
            model=model,
            reason=reason,
        )
        openai = OpenAIProvider(self._settings, model=model)
        text = await openai.complete(system_prompt, user_prompt, **kwargs)
        self._set_last_call_usage(openai.consume_last_call_usage())
        return text

    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        snapshot = await get_ollama_usage(self._settings)
        critical = self._settings.ollama_usage_critical_percent
        if snapshot.has_usage() and snapshot.is_critical(critical):
            if self._can_fallback_to_openai():
                period = "session" if snapshot.session_critical(critical) else "weekly"
                return await self._fallback_to_openai(
                    system_prompt,
                    user_prompt,
                    reason=f"proactive check ({period} >= {critical}%)",
                    **kwargs,
                )
            if snapshot.session_critical(critical):
                period: Literal["session", "weekly"] = "session"
            else:
                period = "weekly"
            raise LLMServiceError(
                kind=LLMErrorKind.QUOTA,
                message=f"Ollama {period} usage limit reached (proactive check)",
                retryable=False,
                quota_period=period,
            )

        temperature = kwargs.get("temperature", 0.3)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        proxies = get_proxy_candidates(self._settings, "ollama")
        last_error: LLMServiceError | None = None

        for attempt in range(2):
            model = await self._resolve_model(refresh=attempt > 0)
            for proxy in proxies:
                if proxy:
                    logger.info("Ollama request via proxy", proxy=mask_proxy(proxy))
                try:
                    return await self._chat_once(proxy, messages, temperature, model=model)
                except LLMServiceError as exc:
                    last_error = exc
                    if (
                        attempt == 0
                        and is_auto_model_name(self._model)
                        and exc.kind in {LLMErrorKind.SERVER, LLMErrorKind.NETWORK}
                    ):
                        logger.warning(
                            "Ollama auto-selected model failed, refreshing catalog",
                            model=model,
                        )
                        break
                    if exc.kind == LLMErrorKind.QUOTA and self._can_fallback_to_openai():
                        return await self._fallback_to_openai(
                            system_prompt,
                            user_prompt,
                            reason=exc.message,
                            **kwargs,
                        )
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
            else:
                continue
            continue

        if last_error:
            if last_error.kind == LLMErrorKind.QUOTA and self._can_fallback_to_openai():
                return await self._fallback_to_openai(
                    system_prompt,
                    user_prompt,
                    reason=last_error.message,
                    **kwargs,
                )
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
        super().__init__()
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
    provider: Literal["openai", "anthropic", "cursor", "ollama", "openrouter"],
) -> LLMProvider:
    if provider == "openrouter":
        return OpenRouterProvider(cfg, task=ModelTask.MAIN)
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
    """Filtering, learning and conversation summaries — routine tasks."""
    cfg = settings or get_settings()
    simple = cfg.llm_simple_provider
    if simple == "same":
        return get_llm_provider(cfg)
    if simple == "ollama" and not is_auto_model_name(cfg.ollama_model) and cfg.ollama_auto_fallback and cfg.openai_api_key:
        from app.llm.ollama_usage import get_ollama_usage_sync

        snapshot = get_ollama_usage_sync(cfg)
        if snapshot.has_usage() and snapshot.is_critical(cfg.ollama_usage_critical_percent):
            logger.warning(
                "Ollama limits critical, falling back to OpenAI",
                model=cfg.openai_simple_model,
                session=snapshot.session.percent if snapshot.session else None,
                weekly=snapshot.weekly.percent if snapshot.weekly else None,
            )
            return OpenAIProvider(cfg, model=cfg.openai_simple_model)
    if simple == "openrouter":
        return OpenRouterProvider(cfg, model=cfg.openrouter_simple_model, task=ModelTask.SIMPLE)
    if simple == "openai":
        return OpenAIProvider(cfg, model=cfg.openai_simple_model)
    return _build_provider(cfg, simple)


def get_scoring_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Use stronger reasoning for scoring, with the main provider as fallback."""
    cfg = settings or get_settings()
    if cfg.llm_simple_provider == "ollama":
        return OllamaProvider(
            cfg, model=cfg.ollama_scoring_model, fallback_model=cfg.openai_model, task=ModelTask.SCORING
        )
    return get_llm_provider(cfg)
