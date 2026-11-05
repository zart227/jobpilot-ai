# Staging drill log (шаблон)

> Журнал drill, не текущий счёт. Критерии: [remediation-plan.md](remediation-plan.md). Dry-run не закрывает live.

Заполнять после прогона сценариев из [runbook.md](runbook.md) § Staging drills. Не коммитить секреты.

| Drill | Дата | Окружение | Исполнитель | Результат |
| ----- | ---- | --------- | ----------- | --------- |
| Qdrant full reindex | | staging | | |
| Dependency failure (postgres/redis) | | staging | | |
| Cursor bridge curl | | staging | | |
| Job processing worker crash | 2026-11-02 | local compose `jobpilot_ai` | agent | **compose drill OK**: `ops_job_processing_c052_compose_drill.py --confirm c052-compose-drill` → `stale_claim_recovered` + `claim_seen_before_stop` (fake job, dry-run); overlay [docker-compose.c052-drill.yml](../docker-compose.c052-drill.yml). Baseline read-only capture ранее same date. |
| Outbox NA triage (read-only) | 2026-11-03 | working `jobpilot_ai` | agent | `make ops-outbox-triage`: NA **32** (`telegram_pending` **20**, `telegram_inbox_pending` **12**). Crosscheck: inactive orphan NA **15** (timeout **10**, other **5**); active pending+NA **5**. Без `--apply`, Telegram не отправлялся. |
| Embedding/Qdrant inventory (read-only) | 2026-11-03 | working `jobpilot_ai` + compose Qdrant | agent | PG: **~51.2%** jobs with current signature, **605** legacy unsigned, `embedding_pending` **0**; canonical collection **`…_d1536`** has points (**668** in 4 canonical cols). Legacy Qdrant **12** cols / **1155** pts. `mark_embedding_reindex` dry-run **0** to mark (default scope). No API batch, no collection delete. |
| Scoring weights rollback | | staging | | |
| `production_rollout.sh` на копии БД | 2026-10-14 | local 5434 | agent | preflight OK; `alembic upgrade` **blocked** `uq_proposals_one_active_per_job` — см. [upgrade-existing-db.md](upgrade-existing-db.md) |
| Разрешение active proposals + migrate | 2026-10-14 | local 5434 | agent | `resolve_active_proposal_conflicts.py --apply --confirm` (4 rows); `alembic upgrade head` → **0004** OK |
| `make dependency-drill` (postgres stop/start) | 2026-11-03 | local API :8000 | agent | `/ready` **503** while postgres down, **200** after recovery (repeat drill OK) |
| `make redis-drill` | 2026-11-03 | local API :8000 | agent | `/ready` **503** while redis down, **200** after recovery (repeat drill OK) |
| `make dependency-drill` (postgres stop/start) | 2026-11-05 | local API :8000 | agent | `/ready` **503** while postgres down, **200** after recovery (`dependency_failure_drill.sh` OK) |
| `make redis-drill` | 2026-11-05 | local API :8000 | agent | `/ready` **503** while redis down, **200** after recovery (`redis_failure_drill.sh` OK) |
| `make cursor-bridge-drill` | 2026-11-03 | local | agent | policy **5 passed**; unauth **404** on **19247** (not **200**) |
| `make cursor-bridge-container` | 2026-11-03 | telegram-bot Up | agent | curl **9247** без auth → **HTTP 404** |
| `c106_runtime_api_probe.sh` | 2026-11-03 | local API :8000 | agent | `/health` **200**, `/jobs` без token **401** |
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
