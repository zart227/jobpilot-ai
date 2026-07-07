from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass
from enum import Enum
from typing import Any


class LLMErrorKind(str, Enum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    AUTH = "auth"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    UNKNOWN = "unknown"


@dataclass
class LLMServiceError(Exception):
    kind: LLMErrorKind
    message: str
    retryable: bool = False
    details: str | None = None

    def __str__(self) -> str:
        return self.message

    def user_message_ru(self) -> str:
        messages = {
            LLMErrorKind.TIMEOUT: (
                "⏱ <b>Таймаут Ollama Cloud</b>\n\n"
                "Модель не ответила вовремя. Попробуйте ещё раз или упростите инструкцию."
            ),
            LLMErrorKind.NETWORK: (
                "🌐 <b>Ошибка сети / прокси</b>\n\n"
                "Не удалось связаться с Ollama Cloud. Попробуйте через минуту."
            ),
            LLMErrorKind.AUTH: (
                "🔑 <b>Ошибка авторизации Ollama</b>\n\n"
                "Проверьте <code>OLLAMA_API_KEY</code> в .env "
                "(ключ: ollama.com/settings/keys)."
            ),
            LLMErrorKind.QUOTA: (
                "📉 <b>Лимит Ollama Cloud исчерпан</b>\n\n"
                "Закончилась квота или баланс API. Проверьте аккаунт на ollama.com."
            ),
            LLMErrorKind.RATE_LIMIT: (
                "🚦 <b>Слишком много запросов</b>\n\n"
                "Ollama Cloud временно ограничил частоту. Подождите 30–60 сек и повторите."
            ),
            LLMErrorKind.SERVER: (
                "⚠️ <b>Ollama Cloud временно недоступен</b>\n\n"
                "Сервер вернул ошибку. Попробуйте позже."
            ),
            LLMErrorKind.UNKNOWN: (
                "❌ <b>Не удалось отредактировать отклик</b>\n\n"
                "Попробуйте переформулировать инструкцию или повторите позже."
            ),
        }
        text = messages.get(self.kind, messages[LLMErrorKind.UNKNOWN])
        if self.details and self.kind == LLMErrorKind.UNKNOWN:
            text += f"\n\n<i>{self.details[:200]}</i>"
        return text


def _error_text(exc: Exception) -> str:
    parts: list[str] = []
    for attr in ("error", "message", "body"):
        value = getattr(exc, attr, None)
        if value:
            parts.append(str(value))
    parts.append(str(exc))
    return " ".join(parts).lower()


def _kind_from_status(status_code: int, text: str) -> tuple[LLMErrorKind, bool]:
    if status_code == 429 or "rate limit" in text or "too many requests" in text:
        return LLMErrorKind.RATE_LIMIT, True
    if status_code in {401, 403} and any(
        token in text for token in ("api key", "unauthorized", "forbidden", "invalid key", "authentication")
    ):
        return LLMErrorKind.AUTH, False
    if status_code == 402 or any(
        token in text
        for token in ("quota", "insufficient", "billing", "credit", "balance", "payment", "quota exceeded")
    ):
        return LLMErrorKind.QUOTA, False
    if status_code in {500, 502, 503, 504}:
        return LLMErrorKind.SERVER, True
    if status_code in {407, 408}:
        return LLMErrorKind.NETWORK, True
    return LLMErrorKind.UNKNOWN, False


def classify_llm_error(exc: Exception) -> LLMServiceError:
    if isinstance(exc, LLMServiceError):
        return exc

    if isinstance(exc, asyncio.TimeoutError):
        return LLMServiceError(
            kind=LLMErrorKind.TIMEOUT,
            message="Ollama request timed out",
            retryable=True,
        )

    exc_name = type(exc).__name__.lower()
    text = _error_text(exc)

    if "timeout" in exc_name or "timeout" in text or "timed out" in text:
        return LLMServiceError(
            kind=LLMErrorKind.TIMEOUT,
            message="Ollama request timed out",
            retryable=True,
            details=str(exc),
        )

    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        kind, retryable = _kind_from_status(int(status_code), text)
        return LLMServiceError(
            kind=kind,
            message=text or f"HTTP {status_code}",
            retryable=retryable,
            details=str(exc),
        )

    if isinstance(exc, ConnectionError) or exc_name in {
        "connecterror",
        "connecttimeout",
        "readtimeout",
        "networkerror",
        "proxyerror",
    }:
        return LLMServiceError(
            kind=LLMErrorKind.NETWORK,
            message="Connection failed",
            retryable=True,
            details=str(exc),
        )

    if any(token in text for token in ("connection error", "failed to connect", "connection refused")):
        return LLMServiceError(
            kind=LLMErrorKind.NETWORK,
            message="Connection failed",
            retryable=True,
            details=str(exc),
        )

    if any(token in text for token in ("quota", "insufficient", "billing", "credit", "balance exceeded")):
        return LLMServiceError(
            kind=LLMErrorKind.QUOTA,
            message="Quota exceeded",
            retryable=False,
            details=str(exc),
        )

    if "rate limit" in text or "too many requests" in text:
        return LLMServiceError(
            kind=LLMErrorKind.RATE_LIMIT,
            message="Rate limited",
            retryable=True,
            details=str(exc),
        )

    return LLMServiceError(
        kind=LLMErrorKind.UNKNOWN,
        message=str(exc),
        retryable=False,
        details=str(exc),
    )


def format_llm_error_message(exc: Exception, order_id: str | None = None) -> str:
    text = classify_llm_error(exc).user_message_ru()
    if order_id:
        text += f"\n\n<b>№ заказа:</b> {html.escape(order_id)}"
    return text
