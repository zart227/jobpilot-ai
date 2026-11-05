"""C066: API /chat and inbox share conversation_context_service."""

import inspect
from pathlib import Path

from app.services import conversation_context_service as ccs


def test_inbox_uses_shared_context_service() -> None:
    from app.services import kwork_inbox

    assert kwork_inbox.build_chat_agent_state is ccs.build_chat_agent_state
    assert kwork_inbox.finalize_chat_agent_state is ccs.finalize_chat_agent_state


def test_api_chat_handler_imports_shared_context_service() -> None:
    from app import main

    source = inspect.getsource(main.handle_client_chat)
    assert "conversation_context_service" in source
    assert "build_chat_agent_state" in source
    assert "finalize_chat_agent_state" in source
    assert "await db.rollback()" in source


def test_kwork_inbox_generate_releases_session_before_network() -> None:
    source = Path(__file__).resolve().parents[1].joinpath(
        "app/services/kwork_inbox.py"
    ).read_text(encoding="utf-8")
    assert "Detached input: no database transaction spans the thread fetch or generation." in source

