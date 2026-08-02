from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)


def _log_dir(settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    return Path(cfg.dev_agent_log_dir).expanduser()


def _log_file(settings: Settings | None = None) -> Path:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return _log_dir(settings) / f"{today}.jsonl"


def log_dev_agent_event(
    event: str,
    *,
    chat_id: int | None = None,
    settings: Settings | None = None,
    **fields: Any,
) -> None:
    cfg = settings or get_settings()
    if not cfg.dev_agent_log_enabled:
        return

    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    if chat_id is not None:
        record["chat_id"] = chat_id

    path = _log_file(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Failed to write dev agent audit log", path=str(path), error=str(exc))

    logger.info("Dev agent audit", audit_event=event, chat_id=chat_id, **fields)


def read_recent_dev_agent_logs(
    limit: int = 10,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    cfg = settings or get_settings()
    path = _log_file(cfg)
    if not path.is_file():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
