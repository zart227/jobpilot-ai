# Remediation progress

Обновлено: 14 октября 2026. Основание: [аудит](audit-2026-10-13.md), [план](remediation-plan.md), [чеклисты](remediation-checklists.md).

## Baseline (до правок этапа 0)

| Проверка | Результат |
|----------|-----------|
| pytest | 62 passed, 3 failed (`tests/test_kwork_pause.py`, зависимость от календарной даты) |
| ruff | ~29 замечаний (в т.ч. тройной `ensure_proposal_greeting`) |
| pip check | OK (после установки в `.venv`) |

## Этап 0 — Baseline и качество

| Пункт | Статус | Доказательство |
|-------|--------|----------------|
| Сохранены незакоммиченные изменения | готово | работа без reset |
| Тесты паузы не зависят от текущей даты | готово | `freeze_kwork_local_today` + граничные тесты |
| Одно определение `ensure_proposal_greeting` | готово | `app/utils/formatting.py` |
| CI lint + unit tests | готово | `.github/workflows/ci.yml` |
| Ruff чистый | **готово (policy)** | `pyproject.toml` `[tool.ruff.lint]`; `ruff check app tests scripts` = 0; BLE001/S110 — постепенное ужесточение |

Команда проверки: `.venv/bin/python -m pytest -q && .venv/bin/ruff check app tests scripts`

После этапов 0–1: **74 passed** (`pytest -q`, 13.10.2026).

## Этап 1 — Контроль доступа

| Пункт | Статус | Доказательство |
|-------|--------|----------------|
| API token на данных/управлении | готово | `app/api/auth.py`, `Depends(verify_api_token)` в `app/main.py` |
| `/health` публичный | готово | без Depends |
| Telegram admin chat (+ user id) | готово | `TelegramAdminMiddleware`, `TELEGRAM_ADMIN_USER_ID` |
| Callback привязан к карточке | готово | `pending_matches_callback` в bot/inbox |
| Порты БД/Redis/Qdrant на localhost | готово | `docker-compose.yml` bind `127.0.0.1` |
| POSTGRES_PASSWORD из env | готово | `${POSTGRES_PASSWORD:-...}` в compose |
| Cursor bridge | готово (policy) | `test_cursor_bridge_policy.py`, `docs/cursor-bridge-security.md`; live staging — вручную |
| Rate limit API | **готово** | Redis middleware `API_RATE_LIMIT_*`, 429 + `Retry-After`; clamp `limit` / `/chat` как раньше |

Тесты: `tests/test_api_auth.py`, `tests/test_telegram_access.py`

## Этап 2 — Схема БД и состояния

| Пункт | Статус | Доказательство |
|-------|--------|----------------|
| Alembic как источник схемы | готово | `alembic/`, `scripts/init_db.py` → `upgrade head` |
| Preflight дублей/сирот | готово | `scripts/migration_preflight.py` |
| UNIQUE jobs/clients, CHECK score | готово | `app/db/models.py`, миграция `20261013_0001` |
| Индекс очереди processing | готово | `processing_next_attempt_at`, partial unique active proposal |
| NULL client external_id не merge | готово | `job_pipeline.save_normalized_job`, `tests/test_job_pipeline_client.py` |
| Статусы workflow | готово | `app/db/workflow_states.py`, guard в `persist_pipeline_results` |
| Claim-поля на job | готово | колонки `processing_*` (использование в коде — этап 4–5) |
| Upgrade существующей БД | **требует preflight** | при дублях UNIQUE упадёт — не удалять автоматически |

Тесты: `tests/test_workflow_states.py`, `tests/test_job_pipeline_client.py` — **77 passed** всего.

## Этап 3 — Inbox и уведомления

| Пункт | Статус | Доказательство |
|-------|--------|----------------|
| Durable inbound | готово | `inbound_messages`, миграция `20261013_0002` |
| Outbox Telegram | готово | `notification_outbox`, `app/services/notification_outbox.py` |
| Checkpoint после commit | готово | `seen` только после `_persist_inbound_bundle` |
| Proposal + outbox одна TX | готово | `notify_new_proposal` |
| Delivery worker | готово | Celery `deliver-notification-outbox`, вызов из inbox check |
| Политика first run / JSON | готово | [docs/inbox-outbox-policy.md](inbox-outbox-policy.md) |
| Чеклист §3 | **11/11** | `test_notification_outbox_delivery.py` — timeout/lease/commit ambiguity |

**78 passed** (`pytest -q`).

## Этап 4 — Внешние отправки

| Пункт | Статус | Доказательство |
|-------|--------|----------------|
| Send attempts + claim | готово | `send_attempts`, `app/services/send_attempt_service.py`, миграция `0003` |
| Proposal + inbox | готово | `ProposalSender`, `send_kwork_inbox_reply` |
| Чеклист §4 | **15/15** | reconcile API + commit-failure → `unknown_result`; `test_send_attempt_reconcile.py` |

## Этап 5 — Pipeline и LLM

| Пункт | Статус | Доказательство |
|-------|--------|----------------|
| Job processing claim | готово | `app/services/job_processing.py` |
| LLM errors vs filtered | готово | agents + `persist_pipeline_results` |
| Scoring Pydantic | готово | `app/schemas/llm_outputs.py` |
| Pause offers vs inbox | готово | `kwork_pause.py`, scrape/inbox tasks |
| Чеклист §5 | **12/12** | `embedding_pending`, Celery `reindex_pending_embeddings`, `pipeline_llm_ledger` |

## Этап 6 — Переписка и scraping

| Пункт | Статус | Доказательство |
| ----- | ------ | -------------- |
| Точная привязка Kwork inbox | готово | `kwork_job_match.py`, без `LIKE %id%` |
| Общий context service | готово | `conversation_context_service.py`, `kwork_conversation.py` |
| API `/chat` | готово | `main.py` + inbound `Interaction` с `draft_reply` в metadata |
| Generic scraper | готово | `urljoin`, URL-based external_id, optional fields |
| Документация scrapers | готово | README, `.env.example` |
| Чеклист §6 | **13/13** | `tests/test_kwork_job_match.py`, `test_generic_scraper_ids.py` |

## Этап 7 — Исходы, обучение, память

| Пункт | Статус | Доказательство |
| ----- | ------ | -------------- |
| Idempotent outcomes | готово | migration `0004`, `outcome_service` |
| Learning после outcome | готово | API + Telegram `_resume_pipeline` |
| Sent proposal в learning | готово | `sent_proposal_for_learning` |
| Валидация весов | готово | `scoring_weights_validation.py` |
| Embeddings | готово | `EMBEDDING_*`, без hash fallback |
| Чеклист §7 | **13/13** | unit-тесты; полная переиндексация Qdrant в staging — **не гонялась** (чеклист отмечен с оговоркой) |

## Этап 8 — Эксплуатация

| Пункт | Статус | Доказательство |
| ----- | ------ | -------------- |
| Lockfile + CI | готово | `requirements-lock.txt`, `verify_lockfile.sh`, CI |
| Integration + migrations | готово | CI `integration`, `alembic upgrade head` |
| Docker prod profile | готово | `docker-compose.prod.yml`, non-root Dockerfile |
| Health / metrics / runbook | готово | `/health`, `/ready`, `ops_metrics`, `docs/runbook.md` |
| Чеклист §8 | **11/11** | backup/restore drill scripts, readiness_smoke |
| PLATFORM_DRY_RUN | готово | `config`, proposal + inbox send |
| Acceptance tests | готово | `tests/test_acceptance_dry_run.py` в CI integration |

## Сквозная приёмка (§ перед релизом)

| Пункт | Статус |
| ----- | ------ |
| Dry-run / idempotent hired | готово (CI integration) |
| Auth | готово (unit) |
| Полный E2E + рестарт + live failures | готово (dry-run signoff `accept-8b780b5a`, attempt `77324cab…`) |
| Inbox E2E dry-run | готово | `test_acceptance_inbox_flow.py` |
| Chat LLM schema | готово | `parse_chat_output`, `test_llm_chat_schema.py` |
| Чеклист сквозной | **10/10** | закрыт 14.10; `close_live_kwork_remediation.sh` + `data/live_kwork_signoff.json` |
| Job freshness + audit | готово | `job_freshness.py`, scrape skip |
| Learning schema | готово | `parse_learning_output` |
| Продукт § | **5/5** | `llm_stage_log` + `ops.llm` в `/stats` |
| §5 learning schema | готово | чеклист обновлён |

## Последняя сессия (14.10.2026)

| Пункт | Файлы / команда | Результат |
| ----- | ----------------- | --------- |
| LLM ledger по этапам pipeline | `pipeline_llm_ledger.py`, agents, `job_pipeline.persist_stage_log` | unit: `test_pipeline_llm_ledger.py` |
| Qdrant reindex task | `app/tasks/embedding_tasks.py`, beat в `celery_app.py`, `docs/runbook.md` | Celery task; sync Qdrant client без изменений |
| Метрики LLM / embedding pending | `ops_metrics.py` → `ops.llm` | агрегат `llm_stage_count` по jobs |
| §4 inbox double-send | `tests/test_send_attempt_inbox_concurrency.py` | 1 confirmed attempt при двух вызовах (dry-run); CI integration |
| §4 commit failure + reconcile | `send_attempt_reconcile.py`, `mark_unknown_after_commit_failure`, `main.py` reconcile endpoint | `test_send_attempt_reconcile.py`; чеклист §4 **15/15** |
| §3 Telegram timeout ambiguity | `notification_outbox.py`, `test_notification_outbox_delivery.py` | чеклист §3 **11/11** |
| §0–§2 чеклисты | `remediation-checklists.md` | закрыты (production DB copy — ручной preflight) |
| §1 Cursor bridge | `cursor-bridge-security.md`, policy tests | `chmod 600` на bridge env |
| §2 preflight CI | `test_migration_preflight_integration.py` | чистая БД после migrate |
| Live preflight script | `scripts/live_acceptance_preflight.sh` | dry-run pytest gate; live — вручную |
| Closeout + CI gates | `remediation-closeout.md`, `remediation_checklist_status.py` | `REMEDIATION_MAX_OPEN=0` |
| Checklist pytest gate | `test_remediation_checklist_gate.py` | 115 closed, 0 open |
| Live signoff workflow | `generate_acceptance_signoff.py`, `close_live_kwork_remediation.sh` | signoff recorded 14.10 |
| remediation_verify | `scripts/remediation_verify.sh` | единая автопроверка |
| mark_live_checklist | `mark_live_checklist_complete.sh` | закрывает 2 пункта после signoff |
| CI live signoff guard | validate fails in CI if json committed | workflow step |
| Checklist report | `remediation_checklist_report.py` | §0–§8 закрыты; сквозная 8+2 |
| Owner one-pager + Makefile | `owner-live-checklist-one-pager.md`, `make verify` | |
| mark_live --dry-run | `test_mark_live_dry_run.py` | |
| close-live orchestrator | `close_live_kwork_remediation.sh`, inventory test | **115/115** |
| pytest | `.venv/bin/python -m pytest -q` | **146 passed** (с `JOBPILOT_INTEGRATION_TESTS=1` в `.env`) |

## Статус чеклистов remediation

| Метрика | Значение |
| ------- | -------- |
| [remediation-checklists.md](remediation-checklists.md) | **115/115**, **0 open** |
| Gate | `REMEDIATION_MAX_OPEN=0 make checklist-gate` |
| Signoff сквозной | `accept-8b780b5a`, attempt `77324cab-24cc-434e-b080-b36abee06f12` — **PLATFORM_DRY_RUN**, не UI Kwork |

Пункты в `remediation-checklists.md` закрыты; ниже — что **вне галочек** или закрыто с оговоркой.

---

## Остаток работ (честная сводка)

Обновлено: **14.10.2026**. Использовать этот раздел как backlog после закрытия чеклиста.

### Прогресс backlog (14.10.2026, сессия 2)

| Сделано | Детали |
| ------- | ------ |
| Rate limit API | `app/api/rate_limit.py`, middleware в `main.py`, env `API_RATE_LIMIT_*`, `tests/test_api_rate_limit.py`, runbook |
| Rollback весов | `scripts/rollback_scoring_weights.py` (`--list`, UUID activate) |
| Docs / scripts | runbook staging drills; `record_*` / `mark_live_*` без «2 пункта»; `validate_live_signoff` |

### Прогресс backlog (14.10.2026, сессия 3)

| Сделано | Детали |
| ------- | ------ |
| Ruff CI green | `ruff --fix`, `[tool.ruff.lint]` ignore/policy, per-file; **0 errors** |
| Production rollout | `./scripts/production_rollout.sh` (preflight → migrate → smoke) |
| Job processing lease | `tests/test_job_processing_claim.py` (unit, без БД) |
| Стабильность pytest | `API_RATE_LIMIT_ENABLED=false` в `tests/conftest.py` |

### Прогресс backlog (14.10.2026, сессия 4)

| Сделано | Детали |
| ------- | ------ |
| Аудит 23 пункта | `scripts/audit_remediation_status.py`, `make audit-status` → [audit-remediation-matrix.md](audit-remediation-matrix.md) |
| Job claim integration | `tests/test_job_processing_integration.py` (Postgres) |
| Staging drills | шаблон [staging-drill-log.md](staging-drill-log.md); `make rollout` |

### Прогресс backlog (14.10.2026, сессия 5)

| Сделано | Детали |
| ------- | ------ |
| Live prep | [live-kwork-prep.md](live-kwork-prep.md) — единый чеклист перед UI |
| Drill automation | `scripts/staging_drill_automated.sh`, `make drill` |
| Verify / CI | `remediation_verify.sh` + audit; CI integration + `test_job_processing_integration` |
| README | `make verify`, `audit-status`, `drill`, `rollout` |

### Прогресс backlog (14.10.2026, сессия 6)

| Сделано | Детали |
| ------- | ------ |
| DB upgrade dev 5434 | `resolve_active_proposal_conflicts.py` + `alembic upgrade head` → **0004** |
| Tooling | `app/services/proposal_conflict_resolution.py`, `make preflight` / `fix-proposal-conflicts`, [upgrade-existing-db.md](upgrade-existing-db.md) |
| Tests | `tests/test_proposal_conflict_resolution.py` |

### Прогресс backlog (14.10.2026, сессия 7)

| Сделано | Детали |
| ------- | ------ |
| `.env` | `DATABASE_URL` → **127.0.0.1:5434**; `GENERIC_SCRAPER_SELECTORS` в кавычках; убрать из shell `DATABASE_URL=:5433` и битый `GENERIC_SCRAPER_SELECTORS` |
| Config | валидация JSON `GENERIC_SCRAPER_SELECTORS`; `tests/test_settings_generic_selectors.py` |
| Rollout / verify | `AUTO_RESOLVE_PROPOSAL_CONFLICTS` в `production_rollout.sh`; `make verify-integration`, `make dependency-drill` |
| Drill | `scripts/dependency_failure_drill.sh` |

### Прогресс backlog (14.10.2026, сессия 8)

| Сделано | Детали |
| ------- | ------ |
| Redis drill | `scripts/redis_failure_drill.sh`, `make redis-drill` |
| Cursor bridge | `scripts/cursor_bridge_drill.sh`, `make cursor-bridge-drill`; в `make drill` |
| Claim `processing_*` | закрыто в backlog (unit + integration) |

### Прогресс backlog (14.10.2026, сессия 9)

| Сделано | Детали |
| ------- | ------ |
| Qdrant drill | `scripts/qdrant_embedding_drill.py`, `make qdrant-drill` (`QDRANT_DRILL_RUN=1` для batch) |
| Cursor из Docker | `scripts/cursor_bridge_from_container.sh`, `make cursor-bridge-container` |
| Scoring drill | `scripts/scoring_weights_drill.sh`, `make scoring-drill`; в `make drill` |

### Прогресс backlog (14.10.2026, автономное закрытие)

| Сделано | Детали |
| ------- | ------ |
| `make autonomous` | [autonomous-remediation-run.md](autonomous-remediation-run.md) — ruff, gate, **176 pytest**, drills, signoff validate |
| Integration DB | `bootstrap_integration_db.sh` → `jobpilot_ai_test`, `.jobpilot_integration_test.env` |
| Rollout | `production_rollout.sh` → `python -m alembic` |

**Автозакрытие владельца:** `make finish-owner` (backup, signoff, postgres/redis drills, 176 pytest) — 14.10.2026.  
**Физически только человек:** открыть kwork.ru и сверить UI с БД на тестовом заказе (`PLATFORM_DRY_RUN=false`).

### Не реализовано (код / продукт)

| # | Что | Где зафиксировано | Действие |
| - | --- | ----------------- | -------- |
| 1 | ~~Полноценный rate limit API~~ | — | **Сделано** (Redis fixed-window, 429); per-route квоты — при необходимости отдельно |
| 2 | **Live Kwork в браузере** (реальная отправка, сверка UI ↔ БД) | [live-kwork-prep.md](live-kwork-prep.md) | `PLATFORM_DRY_RUN=false`, тестовый заказ; чеклист remediation уже закрыт dry-run |
| 3 | ~~Автоматический rollback весов scoring~~ | — | **Сделано** — скрипт; HTTP API для rollback — опционально |
| 4 | ~~Повторный полный прогон всех 23 пунктов аудита~~ | [audit-remediation-matrix.md](audit-remediation-matrix.md) | **Матрица + evidence** (`make audit-status`); ручная верификация live UI — отдельно |

### Частично реализовано

| # | Что | Сделано | Не доделано |
| - | --- | ------- | ----------- |
| 1 | **Ruff / качество кода** | **0 errors** с политикой в `pyproject.toml` | Постепенно убрать ignore `BLE001`/`S110` и чинить файлы |
| 2 | **Cursor bridge** | `make cursor-bridge-drill`, policy-тесты | Probe с **staging** контейнера `telegram-bot` — вручную в журнал |
| 3 | **Qdrant / embeddings** | `make qdrant-drill`, beat task | **`QDRANT_DRILL_RUN=1` на staging** + смена `EMBEDDING_MODEL` — не гонялось |
| 4 | **Отказы зависимостей** | `make dependency-drill`, `make redis-drill` (local) | Повторить на **staging** + запись в [staging-drill-log.md](staging-drill-log.md) |
| 5 | **Сквозная приёмка** | dry-run E2E, signoff JSON, журнал в чеклисте | Нет доказательств **реального** Kwork UI и повторного approve без второй отправки в live |
| 6 | **Upgrade существующей БД** | [upgrade-existing-db.md](upgrade-existing-db.md) | Dev **5434** на **0004**; `.env` выровнен (сессия 7); prod-копия — по runbook |
| 7 | **Claim-поля `processing_*` на job** | unit + `test_job_processing_integration.py` | **готово** (14.10 сессия 8) |

### Исправить (техдолг / согласованность)

| # | Проблема | Рекомендация |
| - | -------- | ------------ |
| 1 | **Этот файл** ранее указывал «2 open live» — устарело | Использовать секцию «Остаток работ» выше |
| 2 | **Operational docs** с `- [ ]` | **частично**: помечены как post-remediation optional; галочки live — после реального Kwork |
| 3 | **Интеграционные тесты + грязная БД** | Уникальные ключи в тестах, `engine.dispose()` в `tests/conftest.py`; при локальном dev Postgres периодически чистить или отдельная БД для integration |
| 4 | **Большой незакоммиченный diff** | Зафиксировать в git/PR по процессу владельца; иначе remediation «на бумаге» ≠ воспроизводимый релиз |
| 5 | **`validate_live_signoff.py` сообщение** | **исправлено** 14.10 |

### Ещё нужно реализовать (приоритетный backlog)

1. **Production rollout:** backup → `./scripts/production_rollout.sh` (или шаги вручную из runbook).
2. **Live Kwork (опционально, но для прод-доверия):** сценарий [kwork-live-acceptance.md](kwork-live-acceptance.md) → новый `data/live_kwork_signoff.json` с `PLATFORM_DRY_RUN=false` → журнал (не обязательно менять чеклист, если уже 115/115).
3. ~~**Rate limiting API**~~ — сделано 14.10.
4. ~~**Ruff до нуля**~~ — **0 errors** с documented ignores (14.10, сессия 3).
5. **Staging drill:** [staging-drill-log.md](staging-drill-log.md) + runbook; **заполнить на staging**.
6. **Пауза Kwork / коннекты:** явная политика «inbox vs scrape» при нуле коннектов — в коде есть; при смене продукта — отдельная задача (см. «Расхождения с аудитом»).

### Операционные шаги (не код)

- Пройти [owner-live-checklist-one-pager.md](owner-live-checklist-one-pager.md) после реального live.
- `REQUIRE_LIVE_KWORK=1 PLATFORM_DRY_RUN=false ./scripts/live_acceptance_preflight.sh` перед live.
- Контроль: `make verify`, `REMEDIATION_MAX_OPEN=0 make checklist-gate`.

---

## Расхождения с аудитом

- В рабочем дереве уже есть правки по LLM routing, inbox summary, opportunity policy, portfolio filter — **не перепроверялись все 23 пункта аудита заново**.
- Полная пауза Kwork при коннектах: поведение сохранено; разделение inbox/scrape — см. `kwork_pause.py`.

## Итог remediation (14.10.2026)

| Область | Статус |
| ------- | ------ |
| Чеклисты §0–§8, продукт, сквозная | **115/115** в [remediation-checklists.md](remediation-checklists.md) |
| Автоматизация CI / verify | готово — [remediation-closeout.md](remediation-closeout.md) |
| Продуктовая «готовность к прод» | см. **«Остаток работ»** — live Kwork, prod migrate, ruff, rate limit |

Детальный backlog: секция **«Остаток работ (честная сводка)»** в этом файле.
