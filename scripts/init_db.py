"""Apply Alembic migrations (single schema source of truth)."""

import structlog
from alembic.config import Config

from alembic import command

logger = structlog.get_logger(__name__)


def main() -> None:
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    logger.info("JobPilot AI database migrated to head")
    print("JobPilot AI database ready.")


if __name__ == "__main__":
    main()
