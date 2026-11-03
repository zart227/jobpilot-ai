# Поэтапная переиндексация Qdrant и embedding backlog

Цель: понять, какие данные нужны в **канонических** коллекциях, восстановить их из PostgreSQL (не копированием точек между коллекциями), проверить поиск, сохранить откат. **Не удалять** legacy-коллекции Qdrant и drill-БД до отдельного решения.

## 0. Read-only runtime (перед каждым этапом)

```bash
docker compose ps
docker compose exec -T postgres psql -U jobpilot -d jobpilot_ai -tAc "SELECT version_num FROM alembic_version"
PYTHONPATH=. .venv/bin/python scripts/migration_schema_inventory.py  # требует JOBPILOT_WORKING_DATABASE_URL
```

Compose `DATABASE_URL` → `jobpilot_ai` (см. `docker-compose.yml`). Drill: `jobpilot_0008_drill` — не используется сервисами.

## 1. Backup

```bash
docker compose exec -T postgres pg_dump -U jobpilot jobpilot_ai | gzip > data/backups/jobpilot_ai_<stamp>.sql.gz
sha256sum data/backups/jobpilot_ai_<stamp>.sql.gz > data/backups/jobpilot_ai_<stamp>.sql.gz.sha256
PYTHONPATH=. .venv/bin/python scripts/backup_qdrant_snapshot.py
```

PostgreSQL: см. `data/backups/` (не коммитить). Пример перед крупным pilot batch: `jobpilot_ai_20261102_pre335.sql.gz` (+ `.sha256`). Qdrant: metadata JSON от `backup_qdrant_snapshot.py`; файлы snapshot в volume `qdrant_data`.

## 2.5 Pilot batch (перед mass reindex)

```bash
export JOBPILOT_WORKING_DATABASE_URL='postgresql+asyncpg://…/jobpilot_ai'
PYTHONPATH=. .venv/bin/python scripts/pilot_embedding_reindex.py --limit 3
PYTHONPATH=. .venv/bin/python scripts/pilot_embedding_reindex.py --limit 25 --apply --confirm '<signature>' --run
```

По умолчанию `--include-legacy-unsigned` (не переиндексирует jobs с актуальной signature). Явный `--no-include-legacy-unsigned` — только mismatch/`embedding_pending`.

После **`signed_percent_of_total` ≥ ~50%** (см. `embedding_queue_inventory` / `make ops-snapshot`): рекомендуется пауза pilot batch для расширенного `ops_post_checkpoint_verify.py` (например `--limit 100`), outbox triage и решения по legacy Qdrant — не обязательный auto-stop в коде.

## 2. Инвентаризация

```bash
PYTHONPATH=. .venv/bin/python scripts/qdrant_collection_inventory.py
PYTHONPATH=. .venv/bin/python scripts/embedding_queue_inventory.py
PYTHONPATH=. .venv/bin/python scripts/ops_embedding_progress.py   # PG + Qdrant rollup
PYTHONPATH=. .venv/bin/python scripts/ops_remediation_snapshot.py # embedding + outbox + inbound + job claims
PYTHONPATH=. .venv/bin/python scripts/mark_embedding_reindex.py   # dry-run
```

Смотреть:

| Поле | Смысл |
|------|--------|
| `expected_collections` | Канонические имена (prefix + base + **provider** + model + `_d{dimension}`) |
| `legacy_count` / `legacy_points_total` | Старые коллекции без полной сигнатуры в имени |
| `embedding_pending_sql` | Очередь job embeddings в PostgreSQL |
| `jobs_with_current_signature` / `signed_percent_of_total` | Прогресс pilot reindex |
| `legacy_indexed_without_signature` | Jobs без signature — `--apply` только с `--include-legacy-unsigned` |

Provider учитывается в **signature** (`provider:model:dim`) и в **каноническом имени коллекции**; смена provider требует переиндексации так же, как смена model/dim.

## 3. Ограниченная переиндексация (после разрешения embedding API)

```bash
PYTHONPATH=. .venv/bin/python scripts/qdrant_embedding_drill.py          # dry-run
PYTHONPATH=. .venv/bin/python scripts/qdrant_embedding_drill.py --run --limit 10
PYTHONPATH=. .venv/bin/python scripts/embedding_queue_inventory.py     # сверка очереди
PYTHONPATH=. .venv/bin/python scripts/qdrant_collection_inventory.py   # points в canonical job_embeddings
```

Не отправляет Kwork/Telegram. Проверить ошибки провайдера в логах worker.

## 4. Массовая пометка jobs (только после backup и решения)

```bash
# Только signature mismatch + уже pending (по умолчанию):
PYTHONPATH=. .venv/bin/python scripts/mark_embedding_reindex.py --apply --confirm '<signature>'

# Включая все legacy jobs без signature (большой объём):
PYTHONPATH=. .venv/bin/python scripts/mark_embedding_reindex.py --apply --confirm '<signature>' --include-legacy-unsigned
```

Затем batch `--run` / beat `reindex_pending_embeddings`.

## 5. Проверка поиска

```bash
export JOBPILOT_WORKING_DATABASE_URL='postgresql+asyncpg://…/jobpilot_ai'
PYTHONPATH=. .venv/bin/python scripts/verify_pilot_embedding_search.py --limit 5
PYTHONPATH=. .venv/bin/python scripts/ops_post_checkpoint_verify.py --limit 50
```

Ожидание после pilot: `self_in_top10: true` для jobs с signature в canonical `job_embeddings`. По умолчанию проверяются недавно обновлённые jobs с title+description ≥20 символов (короткие тестовые строки на working БД пропускаются). Legacy коллекции не используются для routing.

## 6. Legacy Qdrant и drill DB

- Legacy-коллекции: удалять только после п.5 и явного решения.
- `jobpilot_0008_drill`: после сохранения результатов проверки миграции — `DROP DATABASE jobpilot_0008_drill` (отдельное подтверждение, не в автоматическом remediation).
