from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.dev_agent_audit import log_dev_agent_event
from app.services.kwork_pause import format_kwork_pause_reason, get_kwork_pause_reason, is_kwork_paused
from app.services.reward_system import RewardSystem

if TYPE_CHECKING:
    from cursor_sdk import AsyncClient

logger = structlog.get_logger(__name__)

AGENT_ID_KEY = "jobpilot:dev_agent:agent_id:{chat_id}"
BRIDGE_ENV_FILE = Path("data/cursor_bridge.env")

DEV_AGENT_SYSTEM_PREFIX = """You are a development assistant for the JobPilot AI project on the user's local machine.
The user communicates via Telegram remotely. You can read and modify the codebase, run shell commands, check logs, and fix issues.

Project stack: FastAPI, Celery, LangGraph, PostgreSQL, Redis, Qdrant, Playwright, aiogram, Cursor SDK.
Main areas: Kwork scraper, proposal pipeline, Telegram approval bot, LLM providers (OpenAI/Ollama/Cursor).

Be concise in replies. When you change code, briefly explain what and why.
If you run commands, mention the key output.

User request:
"""


class DevAgentConfigurationError(RuntimeError):
    pass


@dataclass
class DevAgentResult:
    text: str
    status: str
    run_id: str
    agent_id: str
    error: str | None = None


@dataclass
class BridgeCredentials:
    base_url: str
    auth_token: str


def is_running_in_docker(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    if cfg.jobpilot_in_docker:
        return True
    return Path("/.dockerenv").exists()


def load_bridge_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or BRIDGE_ENV_FILE
    if not env_path.is_file():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_bridge_credentials(settings: Settings | None = None) -> BridgeCredentials | None:
    cfg = settings or get_settings()
    base_url = cfg.cursor_bridge_base_url.strip()
    auth_token = cfg.cursor_bridge_auth_token.strip()

    if not base_url or not auth_token:
        file_values = load_bridge_env_file()
        base_url = base_url or file_values.get("CURSOR_BRIDGE_BASE_URL", "").strip()
        auth_token = auth_token or file_values.get("CURSOR_BRIDGE_AUTH_TOKEN", "").strip()

    if base_url and auth_token:
        return BridgeCredentials(base_url=base_url, auth_token=auth_token)
    return None


def resolve_agent_workspace(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    host_workspace = cfg.cursor_workspace_host.strip()
    if host_workspace:
        return str(Path(host_workspace).expanduser().resolve())
    return str(Path(cfg.cursor_workspace).expanduser().resolve())


def resolve_workspace(settings: Settings | None = None) -> str:
    return resolve_agent_workspace(settings)


def docker_bridge_hint() -> str:
    return (
        "Запустите bridge на хосте:\n"
        "  ./scripts/docker-up.sh\n"
        "или:\n"
        "  python scripts/run_cursor_bridge.py"
    )


async def create_cursor_client(settings: Settings) -> AsyncClient | None:
    if not settings.cursor_api_key:
        return None

    from cursor_sdk import AsyncClient, LocalAgentOptions

    bridge = resolve_bridge_credentials(settings)
    in_docker = is_running_in_docker(settings)

    if in_docker:
        if bridge is None:
            raise DevAgentConfigurationError(
                "Dev-агент в Docker требует Cursor bridge на хосте.\n" + docker_bridge_hint()
            )
        logger.info("Connecting to host Cursor bridge from Docker", base_url=bridge.base_url)
        return AsyncClient.connect(
            bridge.base_url,
            bridge.auth_token,
            stream_timeout=settings.cursor_agent_timeout_seconds,
        )

    if bridge is not None:
        logger.info("Connecting to external Cursor bridge", base_url=bridge.base_url)
        return AsyncClient.connect(
            bridge.base_url,
            bridge.auth_token,
            stream_timeout=settings.cursor_agent_timeout_seconds,
        )

    workspace = resolve_agent_workspace(settings)
    logger.info("Launching local Cursor bridge", workspace=workspace)
    return await AsyncClient.launch_bridge(
        workspace=workspace,
        local=LocalAgentOptions(cwd=workspace),
        client_timeout=settings.cursor_agent_timeout_seconds,
    )


class DevAgentService:
    def __init__(
        self,
        client: AsyncClient,
        redis: Redis,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._redis = redis
        self._settings = settings or get_settings()
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._settings.cursor_api_key and self._settings.telegram_dev_agent_enabled)

    def _agent_key(self, chat_id: int) -> str:
        return AGENT_ID_KEY.format(chat_id=chat_id)

    def _build_agent_options(self) -> Any:
        from cursor_sdk import AgentOptions, LocalAgentOptions

        workspace = resolve_agent_workspace(self._settings)
        return AgentOptions(
            api_key=self._settings.cursor_api_key,
            model=self._settings.cursor_model,
            local=LocalAgentOptions(cwd=workspace),
        )

    def _build_send_options(self) -> Any:
        from cursor_sdk import SendOptions

        return SendOptions(model=self._settings.cursor_model)

    async def reset_session(self, chat_id: int) -> None:
        agent_id = await self._redis.get(self._agent_key(chat_id))
        if agent_id:
            raw_id = agent_id.decode() if isinstance(agent_id, bytes) else str(agent_id)
            try:
                from cursor_sdk import AgentOptions, AsyncAgent

                agent = await AsyncAgent.resume(
                    raw_id,
                    self._build_agent_options(),
                    client=self._client,
                )
                await agent.close()
            except Exception as exc:
                logger.warning("Failed to close dev agent session", agent_id=raw_id, error=str(exc))
        await self._redis.delete(self._agent_key(chat_id))

    async def _get_or_create_agent(self, chat_id: int) -> Any:
        from cursor_sdk import AsyncAgent

        options = self._build_agent_options()
        stored = await self._redis.get(self._agent_key(chat_id))
        if stored:
            agent_id = stored.decode() if isinstance(stored, bytes) else str(stored)
            try:
                return await AsyncAgent.resume(
                    agent_id,
                    options,
                    client=self._client,
                )
            except Exception as exc:
                logger.warning("Dev agent resume failed, creating new", error=str(exc))
                await self._redis.delete(self._agent_key(chat_id))

        agent = await AsyncAgent.create(
            options,
            client=self._client,
            model=self._settings.cursor_model,
            api_key=self._settings.cursor_api_key,
            local=options.local,
        )
        await self._redis.set(self._agent_key(chat_id), agent.agent_id)
        return agent

    async def send(self, chat_id: int, message: str) -> DevAgentResult:
        from cursor_sdk import CursorAgentError

        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        prompt = f"{DEV_AGENT_SYSTEM_PREFIX}{message.strip()}"
        started = time.monotonic()

        log_dev_agent_event(
            "request_started",
            chat_id=chat_id,
            settings=self._settings,
            request=message.strip(),
            model=self._settings.cursor_model,
        )

        async with lock:
            agent = await self._get_or_create_agent(chat_id)
            try:
                run = await agent.send(prompt, self._build_send_options())
                result = await asyncio.wait_for(
                    run.wait(),
                    timeout=self._settings.cursor_agent_timeout_seconds,
                )
                text = await run.text()
                duration = round(time.monotonic() - started, 1)
                if result.status == "error":
                    log_dev_agent_event(
                        "request_failed",
                        chat_id=chat_id,
                        settings=self._settings,
                        request=message.strip(),
                        run_id=run.id,
                        agent_id=agent.agent_id,
                        status=result.status,
                        duration_sec=duration,
                        response_preview=(text or "")[:500],
                    )
                    return DevAgentResult(
                        text=text or "Агент завершил работу с ошибкой.",
                        status="error",
                        run_id=run.id,
                        agent_id=agent.agent_id,
                        error=f"Run {run.id} failed",
                    )
                log_dev_agent_event(
                    "request_completed",
                    chat_id=chat_id,
                    settings=self._settings,
                    request=message.strip(),
                    run_id=run.id,
                    agent_id=agent.agent_id,
                    status=result.status,
                    duration_sec=duration,
                    response_preview=(text or "")[:500],
                )
                return DevAgentResult(
                    text=text or "(пустой ответ)",
                    status=result.status,
                    run_id=run.id,
                    agent_id=agent.agent_id,
                )
            except TimeoutError as exc:
                duration = round(time.monotonic() - started, 1)
                log_dev_agent_event(
                    "request_timeout",
                    chat_id=chat_id,
                    settings=self._settings,
                    request=message.strip(),
                    agent_id=agent.agent_id,
                    duration_sec=duration,
                    timeout_sec=self._settings.cursor_agent_timeout_seconds,
                )
                raise TimeoutError(
                    f"Агент не ответил за {int(self._settings.cursor_agent_timeout_seconds)} с. "
                    "Проверьте логи: data/dev_agent_logs/"
                ) from exc
            except CursorAgentError as exc:
                log_dev_agent_event(
                    "request_failed",
                    chat_id=chat_id,
                    settings=self._settings,
                    request=message.strip(),
                    error=exc.message,
                    retryable=exc.is_retryable,
                    duration_sec=round(time.monotonic() - started, 1),
                )
                logger.error("Dev agent startup failed", error=exc.message, retryable=exc.is_retryable)
                raise
            except Exception as exc:
                log_dev_agent_event(
                    "request_failed",
                    chat_id=chat_id,
                    settings=self._settings,
                    request=message.strip(),
                    error=str(exc),
                    duration_sec=round(time.monotonic() - started, 1),
                )
                logger.error("Dev agent run failed", error=str(exc))
                raise

    async def collect_project_status(self) -> str:
        lines = ["<b>JobPilot AI — статус проекта</b>\n"]

        async with AsyncSessionLocal() as session:
            pending = await session.scalar(
                select(func.count())
                .select_from(TelegramPending)
                .where(TelegramPending.status == "pending")
            )
            jobs_total = await session.scalar(select(func.count()).select_from(Job))
            proposals_sent = await session.scalar(
                select(func.count()).select_from(Proposal).where(Proposal.status == "sent")
            )

        rewards = RewardSystem()
        total_rewards = await rewards.get_total_rewards()

        lines.append(f"Ожидают одобрения: <b>{pending or 0}</b>")
        lines.append(f"Заказов в БД: <b>{jobs_total or 0}</b>")
        lines.append(f"Отправлено откликов: <b>{proposals_sent or 0}</b>")
        lines.append(f"Награды (rewards): <b>{total_rewards}</b>")

        if is_kwork_paused():
            lines.append("\n⏸ <b>Kwork на паузе</b>")
            reason = get_kwork_pause_reason() or format_kwork_pause_reason()
            if reason:
                lines.append(reason)
        else:
            lines.append("\n✅ Kwork активен")

        api_status = await self._check_api_health()
        lines.append(f"\nAPI: {api_status}")

        bridge = resolve_bridge_credentials(self._settings)
        if bridge:
            bridge_status = await self._check_bridge_health(bridge)
            lines.append(f"Cursor bridge: {bridge_status}")
        elif is_running_in_docker(self._settings):
            lines.append("Cursor bridge: ❌ не настроен")

        ollama_status = await self._check_ollama_usage()
        if ollama_status:
            lines.append(f"Ollama: {ollama_status}")

        openai_status = await self._check_openai_usage()
        if openai_status:
            lines.append(f"OpenAI: {openai_status}")

        workspace = resolve_agent_workspace(self._settings)
        lines.append(f"\nWorkspace: <code>{workspace}</code>")
        lines.append(f"Модель агента: <code>{self._settings.cursor_model}</code>")
        if is_running_in_docker(self._settings):
            lines.append("Режим: Docker → host bridge")

        return "\n".join(lines)

    async def _check_api_health(self) -> str:
        host = self._settings.api_host
        if host in {"0.0.0.0", ""}:
            host = "127.0.0.1"
        base = f"http://{host}:{self._settings.api_port}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{base}/health")
                if response.status_code == 200:
                    return "✅ ok"
                return f"⚠️ HTTP {response.status_code}"
        except Exception as exc:
            return f"❌ недоступен ({exc})"

    async def _check_bridge_health(self, bridge: BridgeCredentials) -> str:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.get(bridge.base_url.rstrip("/"))
            return "✅ ok"
        except httpx.HTTPStatusError:
            return "✅ ok"
        except Exception:
            return "❌ недоступен (запустите ./scripts/docker-up.sh)"

    async def _check_ollama_usage(self) -> str | None:
        if self._settings.llm_simple_provider != "ollama" and not self._settings.ollama_api_key:
            return None
        try:
            from app.llm.ollama_usage import get_ollama_usage

            snapshot = await get_ollama_usage(self._settings)
            if not snapshot.has_usage():
                return "лимиты не настроены"
            parts: list[str] = []
            if snapshot.session:
                parts.append(f"сессия {snapshot.session.percent:.0f}%")
            if snapshot.weekly:
                parts.append(f"неделя {snapshot.weekly.percent:.0f}%")
            return ", ".join(parts) if parts else "ok"
        except Exception as exc:
            return f"ошибка проверки ({exc})"

    async def _check_openai_usage(self) -> str | None:
        if not (
            self._settings.openai_session_token
            or self._settings.openai_admin_api_key
            or self._settings.openai_budget_usd > 0
        ):
            return None
        try:
            from app.llm.openai_usage import get_openai_usage

            snapshot = await get_openai_usage(self._settings)
            if not snapshot.has_data():
                return "баланс не настроен"
            remaining = snapshot.remaining_usd()
            if remaining is not None:
                parts = [f"остаток ${remaining:.2f}"]
            else:
                parts = []
            if snapshot.month_spend_usd is not None:
                parts.append(f"месяц ${snapshot.month_spend_usd:.2f}")
            return ", ".join(parts) if parts else "ok"
        except Exception as exc:
            return f"ошибка проверки ({exc})"


def write_bridge_env_file(
    base_url: str,
    auth_token: str,
    workspace: str,
    path: Path | None = None,
) -> Path:
    env_path = path or BRIDGE_ENV_FILE
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "\n".join(
            [
                "# Auto-generated by scripts/run_cursor_bridge.py",
                f"CURSOR_BRIDGE_BASE_URL={base_url}",
                f"CURSOR_BRIDGE_AUTH_TOKEN={auth_token}",
                f"CURSOR_WORKSPACE_HOST={workspace}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return env_path
