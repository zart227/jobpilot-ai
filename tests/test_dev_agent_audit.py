from __future__ import annotations

import json
from pathlib import Path

from app.services.dev_agent_audit import log_dev_agent_event, read_recent_dev_agent_logs


def test_log_and_read_dev_agent_audit(tmp_path, monkeypatch):
    from app.config import Settings

    settings = Settings(DEV_AGENT_LOG_DIR=str(tmp_path / "logs"), DEV_AGENT_LOG_ENABLED=True)
    monkeypatch.setattr("app.services.dev_agent_audit.get_settings", lambda: settings)

    log_dev_agent_event(
        "request_started",
        chat_id=123,
        settings=settings,
        request="fix bug",
    )
    log_dev_agent_event(
        "request_completed",
        chat_id=123,
        settings=settings,
        request="fix bug",
        status="finished",
        duration_sec=12.5,
        response_preview="done",
    )

    records = read_recent_dev_agent_logs(limit=5, settings=settings)
    assert len(records) == 2
    assert records[0]["event"] == "request_started"
    assert records[1]["event"] == "request_completed"
    assert records[1]["duration_sec"] == 12.5

    log_file = tmp_path / "logs" / f"{records[0]['ts'][:10]}.jsonl"
    assert log_file.is_file()
    parsed = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert parsed["request"] == "fix bug"
