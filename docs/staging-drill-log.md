# Staging drill log (шаблон)

Заполнять после прогона сценариев из [runbook.md](runbook.md) § Staging drills. Не коммитить секреты.

| Drill | Дата | Окружение | Исполнитель | Результат |
| ----- | ---- | --------- | ----------- | --------- |
| Qdrant full reindex | | staging | | |
| Dependency failure (postgres/redis) | | staging | | |
| Cursor bridge curl | | staging | | |
| Scoring weights rollback | | staging | | |
| `production_rollout.sh` на копии БД | 2026-10-14 | local 5434 | agent | preflight OK; `alembic upgrade` **blocked** `uq_proposals_one_active_per_job` — см. [upgrade-existing-db.md](upgrade-existing-db.md) |
| Разрешение active proposals + migrate | 2026-10-14 | local 5434 | agent | `resolve_active_proposal_conflicts.py --apply --confirm` (4 rows); `alembic upgrade head` → **0004** OK |
| `make dependency-drill` (postgres stop/start) | 2026-10-14 | local API :8000 | agent | `/ready` 503 while postgres down, 200 after recovery |
| `make redis-drill` | 2026-10-14 | local API :8000 | agent | `/ready` 503 while redis down, 200 after recovery |
| `make cursor-bridge-drill` | 2026-10-14 | local | agent | policy 5 passed; live probe skipped (bridge not running) |
| `make qdrant-drill` (dry-run) | 2026-10-14 | local | agent | Qdrant OK, 4 collections; `embedding_pending`=0 |
| `make cursor-bridge-container` | 2026-10-14 | telegram-bot | agent | curl host:9247 → HTTP 404 (proxy up, no auth) |
| `make scoring-drill` | 2026-10-14 | local 5434 | agent | `rollback_scoring_weights.py --list` OK |
| `make autonomous` | 2026-10-14 | local | agent | ruff OK · gate 115/115 · **176 pytest** · drills · signoff validate · rollout smoke |

## Qdrant full reindex

```text
# Команды:
# celery -A app.celery_app call app.tasks.embedding_tasks.reindex_pending_embeddings
# или beat task по расписанию

Лог / метрики:
```

## Dependency failure

```text
# Остановка: docker compose stop postgres (или redis)
# GET /ready → ожидание 503
# Восстановление: docker compose start postgres
# GET /ready → 200/degraded

Лог:
```

## Cursor bridge

```text
# См. docs/cursor-bridge-security.md

curl -v ...
```

## Scoring rollback

```text
python scripts/rollback_scoring_weights.py --list
python scripts/rollback_scoring_weights.py <uuid>
```

## Production rollout (копия БД)

```text
./scripts/backup_postgres.sh ./backups
./scripts/production_rollout.sh
```
