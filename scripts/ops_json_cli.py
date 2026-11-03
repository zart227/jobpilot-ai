"""Shared helpers for operator CLI scripts that emit JSON on stdout."""

from __future__ import annotations

import json
import logging
import os


def quiet_sqlalchemy_logs() -> None:
    logging.getLogger("sqlalchemy.engine").setLevel(logging.CRITICAL)


def parse_json_stdout(text: str) -> dict:
    text = text.strip()
    decoder = json.JSONDecoder()
    idx = text.find("{")
    if idx < 0:
        raise ValueError("no JSON object in stdout")
    obj, _end = decoder.raw_decode(text, idx)
    return obj


async def rebind_async_engine() -> None:
    """Point SQLAlchemy at JOBPILOT_WORKING_DATABASE_URL / DATABASE_URL (ops CLIs)."""
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        return
    os.environ["DATABASE_URL"] = url
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.db.session as db_session
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if str(db_session.engine.url) == settings.database_url:
        return
    await db_session.engine.dispose()
    db_session.engine = create_async_engine(
        settings.database_url,
        echo=settings.sqlalchemy_echo,
        pool_pre_ping=True,
    )
    db_session.AsyncSessionLocal = async_sessionmaker(
        db_session.engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
