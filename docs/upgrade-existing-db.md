# Upgrade существующей PostgreSQL

Код и Alembic рассчитаны на схему **`0004`** (`alembic upgrade head`). На старых БД миграция может упасть на ограничениях.

## Порядок

1. **Backup:** `./scripts/backup_postgres.sh ./backups`
2. **Preflight (read-only):** `python scripts/migration_preflight.py`  
   - `duplicate_jobs`, `duplicate_clients_with_external_id`, `multiple_active_proposals` — исправить вручную до UNIQUE.
3. **Migrate:** `alembic upgrade head` или `./scripts/production_rollout.sh`  
   При только конфликтах proposals: `AUTO_RESOLVE_PROPOSAL_CONFLICTS=1 ./scripts/production_rollout.sh` (после backup).
4. **Smoke:** `JOBPILOT_API_URL=http://127.0.0.1:8000 ./scripts/readiness_smoke.sh`

## Типичные ошибки

| Ошибка | Причина | Действие |
| ------ | ------- | -------- |
| `uq_proposals_one_active_per_job` | Несколько active proposals на один job | Отчёт: `python scripts/resolve_active_proposal_conflicts.py`; после backup: `--apply --confirm` |
| `uq_jobs_platform_external_id` | Дубли platform+external_id | Слить или удалить дубли по политике владельца |
| `column processing_claim_owner does not exist` | Миграции не применены | После preflight — `alembic upgrade head` |

## Docker dev

- Host port **5434** → `DATABASE_URL=...@127.0.0.1:5434/jobpilot_ai` (см. `.env.example`).
- Чистая БД: новый volume или контейнер только для тестов.

См. [remediation-progress.md](remediation-progress.md) § «Upgrade существующей БД».
