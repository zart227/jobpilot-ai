import asyncio
from abc import ABC, abstractmethod
from typing import Any

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import Settings, get_settings

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
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

        model = kwargs.get("model", self._settings.cursor_model)
        message = f"{system_prompt.strip()}\n\n---\n\n{user_prompt.strip()}"

        def _run_prompt() -> str:
            try:
                result = Agent.prompt(
                    message,
                    AgentOptions(
                        api_key=self._settings.cursor_api_key,
                        model=model,
                        local=LocalAgentOptions(cwd=self._settings.cursor_workspace),
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
        self._model = ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=0.7,
        )

    async def complete(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        temperature = kwargs.get("temperature", 0.7)
        model = ChatOpenAI(
            api_key=self._settings.openai_api_key,
            model=self._settings.openai_model,
            temperature=temperature,
        )
        response = await model.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        content = response.content
        return content if isinstance(content, str) else str(content)

    def get_chat_model(self) -> BaseChatModel:
        return self._model


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


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    cfg = settings or get_settings()
    if cfg.llm_provider == "cursor":
        logger.info("JobPilot AI using Cursor SDK", model=cfg.cursor_model)
        return CursorProvider(cfg)
    if cfg.llm_provider == "anthropic":
        logger.info("JobPilot AI using Anthropic LLM", model=cfg.anthropic_model)
        return AnthropicProvider(cfg)
    logger.info("JobPilot AI using OpenAI LLM", model=cfg.openai_model)
    return OpenAIProvider(cfg)
