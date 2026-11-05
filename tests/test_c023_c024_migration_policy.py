"""C023/C024: Alembic-only bootstrap, single compose migrator, advisory lock."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "docker/entrypoint.sh").read_text(encoding="utf-8")
INIT_DB = (ROOT / "scripts/init_db.py").read_text(encoding="utf-8")
ENV_PY = (ROOT / "alembic/env.py").read_text(encoding="utf-8")


def test_init_db_only_runs_alembic_upgrade() -> None:
    assert "command.upgrade" in INIT_DB
    assert "create_all" not in INIT_DB


def test_entrypoint_honors_skip_migrate_flag() -> None:
    assert "JOBPILOT_SKIP_DB_MIGRATE" in ENTRYPOINT
    assert '="1"' in ENTRYPOINT or "=1" in ENTRYPOINT


def test_compose_runs_migrations_only_on_api() -> None:
    api_block = COMPOSE.split("    api:", 1)[1].split("\n    celery-worker:", 1)[0]
    worker_block = COMPOSE.split("    celery-worker:", 1)[1].split("\n    celery-beat:", 1)[0]
    beat_block = COMPOSE.split("    celery-beat:", 1)[1].split("\n    telegram-bot:", 1)[0]
    bot_block = COMPOSE.split("    telegram-bot:", 1)[1].split("\n    cursor-bridge-proxy:", 1)[0]

    assert 'JOBPILOT_SKIP_DB_MIGRATE: "0"' in api_block
    for block in (worker_block, beat_block, bot_block):
        assert 'JOBPILOT_SKIP_DB_MIGRATE: "1"' in block


def test_alembic_env_uses_advisory_lock() -> None:
    assert "pg_advisory_lock" in ENV_PY
    assert "MIGRATION_ADVISORY_LOCK_KEY" in ENV_PY
    assert "await connection.commit()" in ENV_PY


def test_production_rollout_runs_migrate_once_in_script() -> None:
    rollout = (ROOT / "scripts/production_rollout.sh").read_text(encoding="utf-8")
    assert "alembic upgrade head" in rollout
    assert rollout.count("alembic upgrade head") == 1


def test_runbook_documents_single_migrator_and_rollback() -> None:
    runbook = (ROOT / "docs/runbook.md").read_text(encoding="utf-8")
    assert "JOBPILOT_SKIP_DB_MIGRATE" in runbook or "single" in runbook.lower()
    assert "backup" in runbook.lower() or "restore" in runbook.lower()
