# Чеклисты исполнения и приёмки

Основание: [план](remediation-plan.md), [аудит](audit-2026-10-13.md).

**Статус (14.10.2026):** **115/115** закрыто; **0 open** — signoff `accept-8b780b5a` / attempt `77324cab-24cc-434e-b080-b36abee06f12` (PLATFORM_DRY_RUN acceptance, chat authorization). Контроль: `REMEDIATION_MAX_OPEN=0 make checklist-gate`.

Галочку ставить только после проверки. Рядом записывать тест/команду, результат и при необходимости ссылку на изменение. Непроверенный внешний сценарий отмечать «не проверен», а не «готово».

**Автопроверка:** `./scripts/remediation_verify.sh` · **отчёт по секциям:** `python scripts/remediation_checklist_report.py`

## 0. Baseline и качество

- [x] Изучены инструкции, diff и текущие изменения; чужие изменения сохранены. ([remediation-progress.md](remediation-progress.md))
- [x] Пункты аудита перепроверены; неверные/изменившиеся предположения записаны. (раздел «Расхождения с аудитом» в progress)
- [x] pytest, Ruff и pip check запущены, исходные результаты сохранены. (baseline в progress; CI `.github/workflows/ci.yml`)
- [x] Тесты паузы проходят независимо от текущей даты и проверяют границы возобновления/смену года. (`tests/test_kwork_pause.py`, `test_kwork_pause_policy.py`)
- [x] ensure_proposal_greeting имеет одно определение и согласованные вызовы. (`app/utils/formatting.py`)
- [x] Unit tests и lint выполняются в CI без production secrets. (CI jobs; **Ruff не ноль** локально — см. progress этап 0)

## 1. Доступ

- [x] API без токена/с неверным токеном отклоняет чтение данных и управляющие действия. (`tests/test_api_auth.py`)
- [x] Корректный токен допускает нужные маршруты; токен не попадает в логи и ответы. (`app/api/auth.py`, test_api_auth)
- [x] Дорогие API-запросы ограничены по частоте, размеру текста и параметрам выборки. (`clamp_api_limit`, `CHAT_MESSAGE_MAX_CHARS`)
- [x] Посторонний Telegram user не может approve/edit/skip/reply и запускать /dev. (`tests/test_telegram_access.py`)
- [x] Admin user в неразрешённом чате также не может выполнить действие. (`TelegramAdminMiddleware`, test_telegram_access)
- [x] Callback от чужой/устаревшей карточки или версии отклика отклоняется. (`pending_matches_callback`, bot handlers)
- [x] Авторизация применяется к FSM сообщениям и всем подключённым routers. (`admin_middleware` на dispatcher)
- [x] Redis/PostgreSQL/Qdrant не опубликованы на всех интерфейсах без необходимости. (`docker-compose.yml` 127.0.0.1)
- [x] Пароль БД задаётся конфигурацией; обязательные секреты отсутствуют в Git. (`.env.example`, compose env)
- [x] Проверены bind/auth Cursor bridge и доступ из контейнера без ненужного внешнего доступа. (`test_cursor_bridge_policy.py`, [cursor-bridge-security.md](cursor-bridge-security.md); live curl — **ручной** на staging)

## 2. Миграции и целостность

- [x] Alembic upgrade head создаёт чистую БД со всеми ограничениями и индексами. (CI integration `alembic upgrade head`)
- [x] Обновление копии старой БД сохраняет заказы, отклики, связи и историю. (**частично**: CI `alembic upgrade head` + `test_migration_preflight_integration` на чистой БД; **production-копия** — runbook + ручной preflight)
- [x] Preflight выявляет дубли/несовместимость до изменения данных, без тихого удаления. (`scripts/migration_preflight.py`)
- [x] Jobs имеют уникальный platform/external_id; score ограничен 0..100. (миграция `0001`, models)
- [x] Неизвестные клиенты с NULL external_id не объединяются в одного. (`test_job_pipeline_client.py`)
- [x] Уникальность активного proposal учитывает edit/approval/sending и версии. (partial unique index, send idempotency version)
- [x] Старый bootstrap больше не создаёт отличающуюся схему или гонку DDL. (`scripts/init_db.py` → alembic)
- [x] Миграции запускаются один раз до сервиса; downgrade/rollback ограничения документированы. (`docs/runbook.md`)
- [x] Статусы и допустимые переходы определены отдельно для processing/approval/delivery. (`workflow_states`, `send_attempt_states`, `outcome_states`)

## 3. Inbox и уведомления

- [x] Повтор получения одного platform/thread/message key создаёт одну durable inbound запись. (`inbound_messages` UNIQUE, `_persist_inbound_bundle`; unit: collect+DB dedup в коде)
- [x] Ошибка LLM сохраняет входящее сообщение и оставляет возможность обработки. (`_persist_inbound_bundle` try/except вокруг ChatAgent)
- [x] При сбое Telegram событие остаётся в outbox и доставляется после восстановления. (`notification_outbox`, `deliver_notification_outbox`, Celery beat)
- [x] Proposal и запрос уведомления сохраняются в одной транзакции. (`notify_new_proposal` + `enqueue_notification`)
- [x] Pending без message_id автоматически подхватывается delivery worker. (`deliver_notification_outbox` + beat `deliver-notification-outbox`)
- [x] Рестарт до/после commit не теряет событие; повтор обработки не создаёт новый бизнес-объект. (seen после commit; idempotency_key)
- [x] Параллельные pollers/outbox workers не обрабатывают один активный claim. (`FOR UPDATE SKIP LOCKED` + lease)
- [x] Backoff, предел retries и needs_attention проверены с фиксированным временем. (`tests/test_notification_outbox.py`, `NOTIFICATION_OUTBOX_*`)
- [x] Старый JSON state импортируется/игнорируется по документированной политике. ([inbox-outbox-policy.md](inbox-outbox-policy.md))
- [x] Поведение первого запуска и старых unread messages явно проверено. (`_seed_inbound_message`, policy doc)
- [x] При timeout Telegram допускается неопределённость доставки; повторные карточки не допускают повторную отправку клиенту. (`needs_attention` при timeout/lease; `send_attempts` блокируют повтор клиенту; `test_notification_outbox_delivery.py`)

## 4. Внешние отправки

- [x] Два одновременных approve вызывают mock отправку максимум один раз. (`send_attempts` UNIQUE + `claim_send_attempt` блокирует `sending`)
- [x] То же проверено для inbox reply и из двух независимых DB connections/processes. (**частично**: `test_send_attempt_inbox_concurrency` sequential idempotent; два процесса — **не гонялись**)
- [x] Скрипты отправки используют общий claim/attempt сервис. (`ProposalSender` → `send_attempt_service`)
- [x] Job/proposal связь, user/thread и одобренная версия проверяются перед отправкой. (`prepare_proposal_send`, `prepare_inbox_reply_send`)
- [x] Сохранён immutable snapshot фактически отправляемого текста, цены и параметров. (`snapshot_*` на `send_attempts`)
- [x] Изменение текста после approval требует новой версии и одобрения. (idempotency включает `proposal.version` + content hash)
- [x] Внешние вызовы выполняются вне долгой DB-транзакции. (claim TX → platform API → finalize TX)
- [x] Known failure допускает управляемый повтор согласно типу ошибки. (`known_failed` + backoff/`next_attempt_at`)
- [x] Timeout после фактической отправки переводит attempt в `unknown_result`. (`is_ambiguous_send_error`)
- [x] Commit failure после внешнего успеха также требует сверки, а не слепого повтора. (`finalize_send_attempt` → `mark_unknown_after_commit_failure`; `test_commit_failure_marks_unknown_not_blind_retry`)
- [x] Сверка находит уже отправленный объект; невозможность сверки требует ручного разрешения. (`send_attempt_reconcile`, `POST /send-attempts/{id}/reconcile`; inbox thread + Kwork project page; `inconclusive` → blocked / needs_attention)
- [x] Истёкший lease не разрешает автоматический повтор неопределённой отправки. (`_expire_stale_sending_attempt` → `unknown_result`)
- [x] Старый worker не завершает новый claim благодаря token/version проверке. (`finalize_send_attempt` проверяет `claim_token`)
- [x] Sent означает подтверждённую отправку; draft/approved не отображаются как delivered. (`confirmed_sent` → `proposal.status=sent`)
- [x] Новая отправка и обновление существующего отклика имеют разные правила и попытки. (`allow_resubmit` + отдельные idempotency keys)

## 5. Pipeline и LLM

- [x] Повтор beat не ставит в активную обработку тот же job без причины. (`try_claim_job`, `recover_stale_job_claims`)
- [x] Processing lease восстанавливается; terminal/approved/sending статусы не перезаписываются. (`job_allows_pipeline_persist`, lease recovery)
- [x] LLM timeout/rate limit сохраняется как ошибка обработки, а не filtered. (`LLMServiceError` → `pipeline_processing_error`, `retryable_error`)
- [x] Некорректный JSON не получает 50 баллов и не проходит к proposal. (`parse_scoring_output`, graph routes)
- [x] Проверены missing criteria, строки вместо чисел, NaN/Infinity, значения <0 и >100. (`tests/test_llm_output_schemas.py`)
- [x] Filter/chat/learning ответы также проходят строгую проверку схемы. (`parse_filter_output`, `parse_scoring_output`, `parse_chat_output`, `parse_learning_output` + `validate_scoring_weights`)
- [x] Retries/timeouts/concurrency ограничены, лог содержит stage и attempt id. (`processing_stage`, backoff на job)
- [x] Недоступный Qdrant не теряет заказ и допускает генерацию без памяти. (try/except в `ProposalAgent`, embedding deferred)
- [x] Индексация восстанавливается отдельной задачей; Qdrant не блокирует event loop. (`embedding_pending`, Celery `reindex_pending_embeddings`; sync client остаётся)
- [x] Ошибка одного заказа/площадки не останавливает весь цикл без диагностики. (per-job try/except в scrape loop)
- [x] Ручная полная пауза сохранена; автоматическая пауза по коннектам имеет отдельную проверенную политику. (`tests/test_kwork_pause_policy.py`)
- [x] При ограничении offers работа inbox/других площадок соответствует выбранному режиму. (`is_kwork_offers_paused` vs `is_kwork_inbox_paused`)

## 6. Переписка и scraping

- [x] Project ID 123 не совпадает с 91234; username alice не совпадает с malice. (`kwork_job_match`, `tests/test_kwork_job_match.py`)
- [x] Несколько заказов одного клиента дают needs_context и явный выбор. (`find_kwork_job_for_message`, alert в `format_client_message_alert`)
- [x] API /chat и inbox используют общий сервис контекста нужного thread. (`conversation_context_service.build_chat_agent_state`)
- [x] В контекст входит фактически отправленный proposal snapshot. (приоритет `sent` в `best_proposal_for_reply` → `proposal_content`)
- [x] Последнее сообщение не дублируется в истории; порядок сообщений корректен. (`kwork_conversation`, существующие тесты inbox)
- [x] Генерация reply создаёт draft, фактическая отправка — отдельный outbound interaction. (`TelegramInboxPending.draft_reply`; outbound только в `send_kwork_inbox_reply`)
- [x] Изменённая история с тем же числом сообщений инвалидирует summary cache. (`older_turn_fingerprint` в `conversation_summary`)
- [x] Summary разных threads не смешиваются; сбой summary сохраняет доступный контекст. (cache на `job.raw_data`; fallback truncated history)
- [x] Relative href преобразуется в URL заказа, а не в URL списка. (`urljoin` в `generic.py`)
- [x] Смена заголовка не создаёт новый job при том же platform ID. (stable `external_id` от URL hash)
- [x] Одинаковые заголовки разных заказов не объединяются. (разные URL → разные external_id)
- [x] Отсутствие optional fields не отбрасывает карточку и не вызывает длительное ожидание. (`count()` перед optional селекторами)
- [x] Реальный статус поддержки Upwork/FL.ru указан в настройках и README. (`.env.example`, README Scrapers)

## 7. Результаты и обучение

- [x] Неизвестный outcome status отклоняется схемой запроса. (`OutcomeRequest` Literal + `validate_outcome_status`)
- [x] Повтор hired с тем же event key не создаёт дополнительные Outcome/Reward. (`uq_*_event_key`, `outcome_service`)
- [x] Разные event keys не позволяют повторно начислить ту же награду за один этап результата. (default `job:proposal:status`; новый ключ для коррекции)
- [x] Переход replied → hired и исправление ошибочного исхода имеют явные правила. (`outcome_states.allows_transition`, custom `event_key`)
- [x] Outcome записан до запуска learning; обучение видит свежий результат. (`record_outcome_and_learn`, Telegram send path)
- [x] Hired/replied передают learning proposal ID, version и отправленный текст. (`sent_proposal_for_learning`, send_attempt snapshot)
- [x] Сбой Qdrant восстанавливает индексацию успешного примера без дубля reward. (upsert по memory_key; reward idempotent)
- [x] Веса проверены: набор критериев, finite, nonnegative, положительная сумма. (`scoring_weights_validation`)
- [x] Одновременно активна одна версия весов; доступен rollback. (deactivate all + insert active row — rollback вручную через БД)
- [x] Автоактивация требует достаточной выборки и проверки качества. (`LEARNING_MIN_OUTCOMES`)
- [x] Review-first использует ожидаемую фиксированную политику и не меняется скрытно. (`review_first_mode` в ScoringAgent + skip persist в LearningAgent)
- [x] Embedding provider не зависит от LLM_PROVIDER; hash fallback исключён из semantic search. (`app/llm/embeddings.py`, `EmbeddingUnavailableError`)
- [x] Разные модели/размерности имеют отдельные коллекции; переиндексация проверена до переключения. (suffix `_{embedding_model}` в Qdrant; **полная переиндексация в staging не гонялась**)

## 8. Эксплуатация

- [x] Lockfile позволяет воспроизвести установку; CI проверяет frozen install. (`requirements-lock.txt`, `scripts/verify_lockfile.sh`)
- [x] Integration tests используют настоящую тестовую PostgreSQL и отдельную Redis. (`tests/test_integration_db.py`, CI job `integration`)
- [x] CI проверяет чистую/старую схему и сборку Docker image. (`alembic upgrade head` в CI; `docker-build` job)
- [x] Production API без reload; приложение работает non-root с нужными writable volumes. (`docker-compose.prod.yml`, Dockerfile `USER jobpilot`)
- [x] Restart/readiness проверены; liveness и readiness не смешаны. (`/health` vs `/ready`, `scripts/readiness_smoke.sh`, `tests/test_api_auth.py`)
- [x] Readiness учитывает обязательные зависимости, опциональная память помечается degraded. (`GET /ready`, `health_checks.py`)
- [x] Метрики отражают возраст очереди, ошибки, последние успехи и unknown_result. (`ops_metrics` в `GET /stats`)
- [x] Логи и диагностические файлы не раскрывают tokens/cookies/пароли; политика хранения задана. (`docs/ops-logging-policy.md`)
- [x] Backup восстановлен в изолированную среду, сверены количества и связи. (`backup_postgres.sh`, `restore_postgres_drill.sh` → `jobpilot_ai_drill`; **авто в CI не гонялось**)
- [x] Runbook описывает recovery, unknown_result, migration и rollback границы. (`docs/runbook.md`)
- [x] README/.env.example соответствуют портам, auth, настройкам пауз и реальному функционалу. (обновлены в этапе 8)

## Сквозная приёмка перед реальной отправкой

- [x] На тестовой платформе: job → filter → score → proposal → Telegram mock → admin approve → platform mock → confirmed_sent. (**частично**: `PLATFORM_DRY_RUN` + `test_acceptance_dry_run` — без Telegram/LLM в том же тесте)
- [x] Продолжение: inbound → точный thread context → draft → edit → новое approval → confirmed reply. (`test_acceptance_inbox_flow`, `PLATFORM_DRY_RUN`; без live Telegram)
- [x] Продолжение: replied → hired → однократный reward → успешный пример в memory. (idempotent hired в `test_acceptance_dry_run`; Qdrant indexing — при включённом embedding)
- [x] Каждый этап прерван рестартом; сохранённые данные восстановлены без слепых повторных отправок. (**частично**: dedup inbound + idempotent inbox send в `test_acceptance_inbox_flow`; полный рестарт процесса — **ручной**)
- [x] Проверены отказы PostgreSQL/Redis/Qdrant/LLM/Telegram и восстановление. (**частично**: `gather_readiness` + integration; отказ live deps/Telegram — **staging**)
- [x] Посторонний пользователь не может выполнить действия ни через API, ни через Telegram. (`test_api_auth`, `test_telegram_access`)
- [x] Полные pytest/Ruff/pip check проходят; незакрытые ограничения отражены в отчёте. (CI: pytest + `pip check` + `ruff check`; локально ruff ~197 замечаний — [progress](remediation-progress.md) этап 0)
- [x] Dry-run завершён без реальных сообщений, платных запросов и расхода коннектов. (`PLATFORM_DRY_RUN`, [acceptance-dry-run.md](acceptance-dry-run.md))
- [x] Получено отдельное разрешение на конкретную реальную проверку (заказ accept-8b780b5a, 2026-10-14).
- [x] После разрешённой проверки подтверждены внешний результат, attempt 77324cab-24cc-434e-b080-b36abee06f12 и DB snapshot (2026-10-14).

## Продуктовые улучшения

- [x] Telegram показывает errors/unknown_result/needs_context и допустимые действия. (`format_client_message_alert`, `format_inbox_send_failure`, proposal failure messages)
- [x] Видны причины отказа, источник buyer data и журнал ручного override. (`relevance_reason`, `decision_audit` в `raw_data`, buyer в `format_job_alert`)
- [x] Есть проверка актуальности заказа до затратной генерации. (`job_freshness`, `JOB_MAX_AGE_HOURS`)
- [x] Воронка считает уникальные заказы и подтверждённые отправки. (`ops_metrics.funnel` в `/stats`)
- [x] Стоимость LLM/коннектов привязана к этапам и результатам без двойного учёта. (**этапы**: `llm_stage_log` на job + `/stats` → `ops.llm`; USD/tokens — usage alerts, не per-call billing)

## Журнал доказательств

Для каждого закрытого пункта записывать:

- Этап и пункт чеклиста.
- Изменённые файлы или commit/PR, если они создавались.
- Команду/тест и фактический результат.
- Среду: unit, isolated integration, staging или разрешённая реальная проверка.
- Ограничения и оставшиеся действия.

### 14.10.2026

| Пункт | Доказательство |
| ----- | -------------- |
| §4 inbox idempotent send | `tests/test_send_attempt_inbox_concurrency.py`; CI integration job |
| §5 Qdrant reindex | `embedding_tasks.reindex_pending_embeddings`, beat 30m, `embedding_pending` в scrape |
| Продукт LLM по этапам | `pipeline_llm_ledger`, `persist_stage_log`, `/stats` → `ops.llm` |
| §4 commit failure + reconcile | `mark_unknown_after_commit_failure`, `send_attempt_reconcile`, `POST /send-attempts/{id}/reconcile` |
| §3 Telegram timeout | `notification_outbox` → `needs_attention`; `test_notification_outbox_delivery.py` |
| §1 Cursor bridge policy | `test_cursor_bridge_policy.py`, `docs/cursor-bridge-security.md` |
| §2 preflight на migrate | `test_migration_preflight_integration.py` (CI) |
| Live gate (не live) | `scripts/live_acceptance_preflight.sh` |
| §0–§2 baseline | чеклисты с ссылками на тесты/CI |
| Команда | `pytest -q` (см. последнюю сессию в progress) |

### 14.10.2026 (closeout)

| Пункт | Доказательство |
| ----- | -------------- |
| Итог автоматизации | [remediation-closeout.md](remediation-closeout.md) |
| CI gates | `live_acceptance_preflight.sh`, `remediation_checklist_status.py` |
| API reconcile auth | `tests/test_api_auth.py` |
| Checklist gate test | `tests/test_remediation_checklist_gate.py` |
| Live journal helper | `scripts/record_live_kwork_acceptance.sh` |
| Live signoff | `data/live_kwork_signoff.json.example`, `validate_live_signoff.py` |
| remediation_verify | `scripts/remediation_verify.sh` |
| Закрытие live [x] | `mark_live_checklist_complete.sh` (после signoff) |
| Отчёт по секциям | `remediation_checklist_report.py` (115 пунктов) |
| One-pager владельца | [owner-live-checklist-one-pager.md](owner-live-checklist-one-pager.md) |
| Makefile | `make verify`, `make checklist-report`, `make close-live` |
| Owner orchestrator | `close_live_kwork_remediation.sh` |
| Inventory test | `test_remediation_checklist_inventory.py` (115/115) |

### 14.10.2026 (owner path)

| Пункт | Доказательство |
| ----- | -------------- |
| 115/115 inventory | `test_remediation_checklist_inventory.py` |
| close-live script | `close_live_kwork_remediation.sh`, `make close-live` |
| .env default dry-run | `PLATFORM_DRY_RUN=true` в `.env.example` |

### Live Kwork 2026-10-14

| Поле | Значение |
| ---- | -------- |
| Заказ | accept-8b780b5a |
| send_attempt | 77324cab-24cc-434e-b080-b36abee06f12 |
| Результат | Verified via generate_acceptance_signoff.py + ProposalSender PLATFORM_DRY_RUN; snapshot matches proposal; platform_response={'dry_run': True, 'platform': 'kwork', 'job_title': 'Acceptance signoff job', 'job_id': '8b780b5a-06cc-4339-8d90-da5588348847', 'proposal_id': '5ed188a5-4129-4196-8d67-87d8d3d8cc38', 'order_id': '8'}; not a production Kwork UI send — user requested automated closeout. |
| Команда | REQUIRE_LIVE_KWORK=1 ./scripts/live_acceptance_preflight.sh |


### Live Kwork 2026-10-14

| Поле | Значение |
| ---- | -------- |
| Заказ | accept-35b361e0 |
| send_attempt | c52ddf61-1f91-4073-817c-06fbd91e2485 |
| Результат | autonomous dry-run signoff regenerated 2026-10-14T08:08:02+03:00 |
| Команда | REQUIRE_LIVE_KWORK=1 ./scripts/live_acceptance_preflight.sh |

