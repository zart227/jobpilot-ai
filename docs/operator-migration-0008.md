# Инвентаризация и rollout миграции 0008 (`memory_index_pending`)

Миграция `20261102_0008_memory_index_queue.py` добавляет таблицу `memory_index_pending`. **Актуальный runtime** (не полагаться только на этот файл — перепроверять):

```bash
docker compose exec -T postgres psql -U jobpilot -d jobpilot_ai -tAc "SELECT version_num FROM alembic_version"
docker compose exec -T postgres psql -U jobpilot -d jobpilot_ai -tAc \
  "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='memory_index_pending')"
```

По состоянию после rollout **02.11.2026**: рабочая БД `jobpilot_ai` на **20261102_0008**, сервисы compose указывают на `jobpilot_ai` (не drill/test). Drill `jobpilot_0008_drill` — только для проверки миграции; к worker не подключена.

**03.11.2026:** применена additive миграция **`20261103_0009`** (claim columns для `memory_index_pending`); актуальный head — **`20261103_0009`**. Перепроверять: `SELECT version_num FROM alembic_version` и `migration_schema_inventory.py`.

Подробнее по фазам Qdrant/embedding: [operator-qdrant-reindex.md](operator-qdrant-reindex.md).

## Read-only инвентаризация

Требуется явный URL (не подставлять из `.env` без намерения). Для копии/staging:

```bash
export JOBPILOT_WORKING_DATABASE_URL='postgresql+asyncpg://USER:PASS@HOST:PORT/DBNAME'
PYTHONPATH=. .venv/bin/python scripts/migration_schema_inventory.py
```

Скрипт **отказывается** от БД с именем `*_test`, кроме случая `JOBPILOT_ALLOW_TEST_URL=1` (только для локальной проверки).

Вывод: текущий `alembic_version`, наличие `memory_index_pending`, счётчики backlog `memory_index_pending` и `jobs.embedding_pending`.

## Preflight перед upgrade

На той же БД (после backup):

```bash
export DATABASE_URL="$JOBPILOT_WORKING_DATABASE_URL"
PYTHONPATH=. .venv/bin/python scripts/migration_preflight.py
```

При блокирующих дубликатах/конфликтах — устранить вручную; см. `scripts/resolve_active_proposal_conflicts.py`.

## Проверка на копии (рекомендуется)

1. Создать gzip backup (`pg_dump`) и зафиксировать SHA-256.
2. Восстановить в **новую** БД с суффиксом `_drill` или `_staging` (`scripts/restore_postgres_drill.sh` — только drill-имена).
3. `migration_schema_inventory.py` → `needs_upgrade_to_0008: true`.
4. `alembic upgrade head` на копии.
5. Повторить inventory → `ready_after_upgrade: true`.
6. Прогнать isolated tests против копии **не требуется** для приёмки схемы; smoke: `scripts/qdrant_embedding_drill.py` (dry-run) с `DATABASE_URL` копии.

## Production/staging apply

После backup и успешной проверки на drill-копии (02.11.2026 выполнено):

1. Backup: `data/backups/jobpilot_ai_pre0008_20261102.sql.gz` (+ `.sha256` рядом), drill `jobpilot_0008_drill` → upgrade **0008** OK.
2. `migration_preflight.py` на `jobpilot_ai` — OK.
3. `alembic upgrade head` → **20261102_0008**, `memory_index_pending` создана (0 строк).
4. Перезапуск `api`, `celery-worker`, `celery-beat`, `telegram-bot`; `/ready` → `ready:true`.
5. `qdrant_embedding_drill.py` dry-run — Qdrant OK, `jobs_embedding_pending`: 4.

Не применять повторно без новой миграции. Откат: восстановить backup в **новую** БД и переключить `DATABASE_URL` (не drop production без отдельного подтверждения).
