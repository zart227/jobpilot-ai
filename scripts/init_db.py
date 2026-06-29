"""Initialize JobPilot AI database schema."""

import asyncio
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.models import Base

logger = structlog.get_logger(__name__)


async def init_database() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)

    schema_path = Path(__file__).resolve().parent.parent / "app" / "db" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in schema_sql.split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    await conn.execute(text(stmt))
                except Exception as exc:
                    if "already exists" not in str(exc).lower():
                        logger.debug("Schema statement skipped", error=str(exc))

    await engine.dispose()
    logger.info("JobPilot AI database initialized")


def main() -> None:
    asyncio.run(init_database())
    print("JobPilot AI database ready.")


if __name__ == "__main__":
    main()
