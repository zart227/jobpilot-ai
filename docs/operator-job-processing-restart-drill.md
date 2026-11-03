# Controlled restart drill — обработка заказов (C052/C053)

Цель: подтвердить, что после **жёсткой остановки** `celery-worker` во время активного claim заказ снова попадает в очередь **один раз** после `recover_stale_job_claims` и снятия Redis dedup. Это **не** live Kwork/Telegram drill.

## 0. Preflight (read-only)

```bash
export JOBPILOT_WORKING_DATABASE_URL='postgresql+asyncpg://…/jobpilot_ai'
PYTHONPATH=. .venv/bin/python scripts/ops_job_processing_drill_preflight.py
PYTHONPATH=. .venv/bin/python scripts/ops_job_retry_recovery.py
```

Запишите: `active_processing_claims`, `stale_claims_pending_recovery`, `redis_enqueue_dedup.sample_count`.

Для фиксации evidence до/после drill (read-only JSON):

```bash
PYTHONPATH=. .venv/bin/python scripts/ops_job_processing_drill_record.py --phase before --job-id <UUID>
# … compose worker stop/start …
PYTHONPATH=. .venv/bin/python scripts/ops_job_processing_drill_record.py --phase after --job-id <UUID>
```

## 1. Выбор тестового заказа

- Предпочтительно **fake** / изолированный job на **staging** или явно согласованный UUID на working.
- Не используйте заказ с pending Telegram approval или активной отправкой Kwork.
- Убедитесь, что `PLATFORM_DRY_RUN=true` для рабочего пилота, если drill на production compose.

## 2. Порядок drill (compose)

**Automated (PLATFORM_DRY_RUN=true, fake job only):**

```bash
export JOBPILOT_WORKING_DATABASE_URL='postgresql+asyncpg://…/jobpilot_ai'
PYTHONPATH=. .venv/bin/python scripts/ops_job_processing_c052_compose_drill.py --confirm c052-compose-drill
```

Uses `docker-compose.c052-drill.yml` (lease **60s**, hold **120s** on fake platform). Restores default compose env after success.

**Manual** (staging / custom job):

1. Запустите обработку одного job (`process_single_job` через scrape или API), дождитесь `processing_claim_token` в БД (preflight покажет `active_claim_sample`).
2. **Без** graceful shutdown: `docker compose stop celery-worker` (или `kill -9` внутри контейнера worker — только на staging).
3. Подождите **> `JOB_PROCESSING_LEASE_SECONDS`** (по умолчанию 900 с; для ускорения drill временно уменьшите lease на staging и перезапустите worker **до** теста).
4. Запустите worker: `docker compose start celery-worker`.
5. Дождитесь beat/scrape цикла или вручную вызовите recover path (как в коде: `_reprocess_pending` → `recover_stale_job_claims` → `_queue_unprocessed_jobs`).

## 3. Доказательства (acceptance)

| Проверка | Ожидание |
|----------|----------|
| `processing_attempt_journal` | событие `stale_claim_recovered`, новый `claimed` |
| Redis dedup key для job | снят после stale recovery / failed task finally |
| Повторный enqueue | **один** новый task id, не шторм дубликатов |
| Terminal `filtered` / send paths | job **не** переоткрывается (см. isolated tests) |

Isolated доказательства уже в `tests/test_job_processing_restart_drill.py`, `tests/test_scrape_beat_restart_drill.py`, `tests/test_c052_enqueue_release_on_failure.py`. Compose drill закрывает C052 только после записи evidence в [staging-drill-log.md](staging-drill-log.md).

## 4. Запрещено

- Массовый restart worker во время live send или inbox generation без паузы.
- Смешивание старого и нового кода worker/beat.
- Считать drill успешным без проверки journal и dedup на **конкретном** job id.

## Связанные документы

- [operator-job-retry-recovery.md](operator-job-retry-recovery.md) — exhausted retries
- [remediation-progress.md](remediation-progress.md) — статус C052/C053
