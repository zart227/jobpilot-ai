# JobPilot AI — операционный runbook

> Перепроверка14.10.2026: актуальный счёт — [remediation-checklists.md](remediation-checklists.md) (**53/115 подтверждено, 62 открыто; релиз не принят**). Dry-run не закрывает live.

## Health endpoints

| Endpoint | Назначение |
| -------- | ---------- |
| `GET /health` | **Liveness** — процесс жив, без проверки зависимостей |
| `GET /ready` | **Readiness** — PostgreSQL и Redis обязательны; Qdrant опционален (`degraded`) |

Kubernetes: liveness → `/health`, readiness → `/ready`.

Перед rollout на working (read-only, без отправок):

```bash
export JOBPILOT_WORKING_DATABASE_URL='postgresql+asyncpg://…/jobpilot_ai'
make -s ops-rollout-readiness   # DB preflight, /ready, ops_metrics, beat schedule
make -s ops-remediation-preflight   # preflight + readiness + outbox triage JSON
make -s ops-outbox-triage       # NA rollup — ручной triage при needs_attention > 0
./scripts/readiness_smoke.sh
```

## Миграции

1. Остановить beat и workers (чтобы не было конкурирующих DDL): `docker compose stop celery-beat celery-worker`
2. `python scripts/migration_preflight.py` на **копии** production БД ([upgrade-existing-db.md](upgrade-existing-db.md); дубли jobs/clients/proposals — исправить вручную до UNIQUE)
3. `alembic upgrade head` (или `./scripts/production_rollout.sh` после backup — опционально `JOBPILOT_ROLLOUT_OPS_READINESS=1` + `JOBPILOT_WORKING_DATABASE_URL` перед migrate)
4. Повторить preflight — должен быть `Preflight OK` (CI: `tests/test_migration_preflight_integration.py`)
5. Запустить workers снова
6. Локальные drills отказа зависимостей (опционально): `make dependency-drill` (postgres), `make redis-drill`
7. Cursor bridge: `make cursor-bridge-drill` — см. [cursor-bridge-security.md](cursor-bridge-security.md)

**Rollback приложения** откатывает код, **не** данные и **не** внешние сообщения. Откат миграций Alembic только если revision обратим и нет несовместимых данных — иначе восстановление из backup.

## unknown_result (внешняя отправка)

1. Найти attempt: `GET /stats` (auth) → `send_attempts.unknown_result` или SQL по `send_attempts.status = 'unknown_result'`.
2. Автоматическая read-only сверка: `POST /send-attempts/{attempt_id}/reconcile` (auth). Inbox — поиск текста в thread Kwork; proposal — проверка страницы проекта (или `dry_run` metadata).
3. При `outcome=confirmed` attempt переходит в `confirmed_sent` без повторной отправки. При `inconclusive` — ручной разбор, не слепой retry.
4. Если `not_found` — новая версия/approval и новый idempotency key через обычный flow.
5. Commit failure после успеха на площадке оставляет `unknown_result` (`commit_failed_after_platform_success`); повтор approve не шлёт снова до сверки.

## Recovery notification outbox

- Pending с большим `oldest_pending_age_seconds` в `/stats`: проверить Telegram token, сеть, логи `deliver_notification_outbox`.
- `needs_attention` — ручной разбор `last_error`, затем сброс статуса или повтор по политике в `docs/inbox-outbox-policy.md`. Read-only: `make -s ops-outbox-triage` (JSON rollup), `ops_outbox_triage_markdown_bundle.py` → файл для оператора, `ops_working_db_preflight.py`, детали — `docs/operator-outbox-needs-attention.md`.

## Recovery processing jobs

- Зависшие `processing` — lease recovery в pipeline (`recover_stale_job_claims`).
- `retryable_error` — beat reprocess; при постоянных LLM ошибках проверить провайдер и лимиты.

## Backup (процедура)

Restore-скрипт использует PostgreSQL из текущего Compose-проекта, поэтому запускайте drill только на отдельном изолированном сервере и только с доверенным SQL dump. Скрипт создаёт новую БД с именем `jobpilot_<name>_drill`, отказывается от существующей БД и не удаляет её. После ошибки созданная БД остаётся для ручного разбора. Факт успешного запуска не заменяет сверку данных и связей.

```bash
./scripts/backup_postgres.sh ./backups
JOBPILOT_DRILL_DB=jobpilot_ai_drill ./scripts/restore_postgres_drill.sh ./backups/<file>.sql.gz
```

1. **PostgreSQL**: скрипт выше или `pg_dump` вручную.
2. **Qdrant**: snapshot volume `qdrant_data` или API snapshot.
3. **Файлы**: `data/` (inbox state, pause state, cursor bridge env — секреты отдельно).

## Remediation / live Kwork

Автоматические gates: `make verify` (см. актуальный счёт в [remediation-checklists.md](remediation-checklists.md)). Отдельная live-приёмка: [owner-live-checklist-one-pager.md](owner-live-checklist-one-pager.md). Backlog: [remediation-progress.md](remediation-progress.md).

## Staging drills (backlog)

Журнал прогонов: [staging-drill-log.md](staging-drill-log.md).

| Drill | Команда / действие |
| ----- | ------------------ |
| Qdrant full reindex | Celery `reindex_pending_embeddings` на staging после смены `EMBEDDING_MODEL`; сверить коллекцию `_{model}` |
| Dependency failure | Остановить postgres/redis по очереди → `GET /ready` = 503; поднять → восстановление; зафиксировать в журнале |
| Cursor bridge | [cursor-bridge-security.md](cursor-bridge-security.md) — curl из app-контейнера |
| Job processing worker crash | [operator-job-processing-restart-drill.md](operator-job-processing-restart-drill.md) — preflight `ops_job_processing_drill_preflight.py`, затем controlled `docker compose stop celery-worker` на staging |

## HTTP API rate limit

По умолчанию включён fixed-window лимит в Redis (`API_RATE_LIMIT_*`). Публичные пути: `/health`, `/ready`, OpenAPI. Ответ **429** + `Retry-After`. При недоступном Redis и `API_RATE_LIMIT_FAIL_OPEN=true` запросы проходят.

## Cursor bridge (dev agent)

См. [cursor-bridge-security.md](cursor-bridge-security.md). Быстрая проверка политики: `pytest -q tests/test_cursor_bridge_policy.py`.

## Embeddings (Qdrant)

При сбое индексации на scrape у job появляется `raw_data.embedding_pending=true`. Beat задача `reindex-pending-embeddings` (каждые 30 мин) или вручную:

```bash
make qdrant-drill              # probe + count embedding_pending (dry-run)
QDRANT_DRILL_RUN=1 make qdrant-drill   # batch reindex (uses embedding API)
celery -A app.celery_app call app.tasks.embedding_tasks.reindex_pending_embeddings
```

## Readiness smoke

```bash
./scripts/readiness_smoke.sh
# JOBPILOT_API_URL=http://api:8000 при проверке из другого контейнера
```

Smoke требует успешную liveness и `HTTP 200` с `ready: true` и status `ready` или `degraded`. HTTP 503 не считается готовностью. Перед rollout укажите `JOBPILOT_ROLLOUT_BACKUP` с путём к gzip SQL dump и `JOBPILOT_ROLLOUT_BACKUP_SHA256` с ранее зафиксированной SHA-256. Скрипт проверяет checksum, формат и целостность gzip перед миграциями; отдельно проверьте происхождение backup и возможность его восстановления.

## Production compose

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Без bind-mount `./app` — код только из образа. API без `--reload`.

## Секреты и логи

См. [ops-logging-policy.md](ops-logging-policy.md). Не логировать `API_AUTH_TOKEN`, cookies Kwork, содержимое `.env`.

## Исправления доступа и evidence — 15.10.2026

Сетевой API теперь fail-closed независимо от старого API_AUTH_REQUIRED=false. Без токена data/control endpoints503. Telegram groups требуют admin user ID. Lua rate-limit использует verified identity; default fail-open выключен. Live signoff v2 требует artifacts+DB сверку и ручной review; auto-close helpers не меняют галочки. Подробности, миграция настроек и ограничения: [блок A](block-a-access-and-evidence.md). Не запускать finish-owner для создания разрешения владельца.

## Offline browser form tests

[Контракт и ограничения проверки](block-b-browser-contract.md). Локальный запуск: `JOBPILOT_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests/test_kwork_form_dom.py tests/test_frozen_submission.py`. Нужен установленный Playwright Chromium; при opt-in отсутствие browser — ошибка. Фикстура синтетическая, все внешние запросы заблокированы; не передавать рабочую сессию или credentials. В CI для этих tests выделен browser-form-contract job. Это не staging/live signoff.

## Повторный показ старых approval-карточек

После согласованного deployment: ответьте `/rereview` на карточку с кнопками или используйте `/rereview proposal <pending UUID>` / `/rereview inbox <pending UUID>`. [Правила и ограничения](operator-review-reissue.md). Действие только ставит новый review в outbox, не одобряет и не отправляет клиенту. Sending/unknown/sent/protected состояния требуют отдельной сверки и не обходятся этим действием. Рабочая версия ещё не обновлялась.
