> **АКТУАЛЬНАЯ ЦЕЛЬ 03.11:** **74/115, 41 открыто.** **C015/C097** закрыты. Дальше operator outbox NA, **C100** docs, **C018/C023**. [progress](remediation-progress.md).

> Позже 02.11: **782 isolated tests passed**. Post-checkpoint verify 50/50; pause pilot; verify summary field.

> Позже 02.11: **779 isolated tests passed**. Snapshot pending_outbox; pilot +50 (635 signed, ~51.6% checkpoint).

> Позже 02.11: **777 isolated tests passed**. C030 pending/outbox crosscheck; backup pre535; pilot +50 (585 signed, ~47.6%).

> Позже 02.11: **774 isolated tests passed** (re-run). signed_percent in embedding inventory; make ops-snapshot; pilot +50 (535 signed, ~43.5%).

> Позже 02.11: **774 isolated tests passed**. ops_remediation_snapshot; backup pre435; pilot +50 (485 signed).

> Позже 02.11: **772 isolated tests passed**. C028 markdown triage checklist; ops_pilot_status embedding/outbox counts.

> Позже 02.11: **770 isolated tests passed**. C028 outbox triage groups; backup pre385; pilot +50 (435 signed).

> Позже 02.11: **769 isolated tests passed**. C027 inbound generation inventory; backup pre335; pilot +50 (385 signed).

> Позже 02.11: **762 isolated tests passed**. C052 restart drill runbook + preflight; pilot +50 (335 signed).

> Позже 02.11: **760 isolated tests passed**. C028 full outbox error buckets; pilot +50 (285 signed); verify min-text filter.

> Позже 02.11: **752 isolated tests passed**. Pilot +50 (235 signed); ops_embedding_progress; C054 summary error_kind.

> Позже 02.11: **751 isolated tests passed**. reprocess_pending+recover_stale; Qdrant 196/1155 points; outbox error buckets.

> Позже 02.11: **750 isolated tests passed**. C028 claim-batch guard; pilot +50 (185 signed); backup pre185.

> Позже 02.11: **749 isolated tests passed**. Outbox triage doc; C052 enqueue release test; working inventory 32 needs_attention outbox.

> Позже 02.11: **748 isolated tests passed**. Outbox inventory script; C052 reprocess drill; pilot +50 (135 signed).

> Позже 02.11: **746 isolated tests passed**. C027 poller restart drill; ops_alert E2E isolated; compose restart.

> Позже 02.11: **743 isolated tests passed**. C027 ops_alert on generation exhausted; pilot +50 (85 signed total).

> Позже 02.11: **742 isolated tests passed**. Pilot +25 unsigned; C052 beat re-queue drill; pilot script skips signed jobs by default.

> Позже 02.11: **740 isolated tests passed**. Pilot reindex 10→canonical; C027 KworkApiError drill; inbound needs_attention в ops metrics.

> Позже 02.11: **739 isolated tests passed**. verify_pilot_embedding_search 3/3; inbox C054 defer test.

> Позже 02.11: **738 isolated tests passed** (42 browser skipped). Pilot reindex 3→canonical Qdrant; PG+Qdrant backups; learning в pipeline_error helper.

> Позже 02.11: **735 isolated tests passed** (42 browser skipped). Rollout **0008** на jobpilot_ai (backup+drill+restart); embedding index signature + mark_embedding_reindex script.

> Позже 02.11: **733 isolated tests passed** (42 browser skipped). Process-level `_process_job` restart drill; Qdrant collection names include embedding dimension. C053/C089/C060 улучшены, не закрыты.

> Позже 02.11: **730 isolated tests passed** (43 skipped). Migration 0008 inventory doc/script, eager inbox capture, worker outage dedup drill, live Qdrant memory drill opt-in. C027/C052/C053/C060 улучшены, не закрыты; рабочая БД не мигрировалась.

> Позже 02.11: **727 isolated tests passed** (42 browser skipped). Per-message Kwork inbox capture, learning→memory_index_pending при недоступном Qdrant, ops metrics очереди, isolated memory recovery drill. C027/C060 улучшены, не закрыты; миграция 0008 не на рабочей БД.

> Позже 02.11: **724 isolated tests passed** (42 browser skipped). Processing attempt journal, stale recovery clears enqueue dedup, restart drill tests. C053/C058 улучшены, не закрыты.

> Позже 02.11: **721 isolated tests passed** (42 browser skipped). SQL `memory_index_pending` + beat retry для proposal/phrase/edit vector writes; миграция 0008 не на рабочей БД. C060 улучшен, не закрыт.

> Позже 02.11: **717 isolated tests passed** (42 browser skipped). Redis dedup для process_single_job enqueue, unified pipeline_error_from_exception для filter/scoring/proposal/chat. C052/C054 улучшены, не закрыты; restart/outage drills и полный stage audit остаются.

> Позже 02.11: **754 isolated tests passed**. Sync Qdrant init/upsert/search вынесены из event loop, C060 остаётся открытым до durable retry всех типов памяти и recovery drills.

> Позже 02.11: 751 full isolated tests passed, после уточнения выбора очереди 46 targeted passed. Активные job claims исключаются из enqueue, просроченные возвращаются; дубли незахваченных задач в брокере остаются возможны.

> Позже 02.11: **751 isolated tests passed**. Исправлены due retries/terminal queue и ограничены попытки jobs; graph lease продлевается и результат просроченного токена блокируется. До rollout проверить [старые счётчики](operator-job-retry-recovery.md): новый cap использует историю. C052/C053/C054/C058 остаются открыты.

> Позже 02.11: 734 full isolated tests passed, затем 48 targeted после финальных уточнений. Перед LLM повторно проверяется claim; read-only ops_pending_cards требует явный адрес БД и классифицирует outbox/delivery evidence. C030 остаётся открытым, не делайте слепой resend.

> Позже 02.11: 722 full isolated tests passed. Добавлены heartbeat генерации, отмена при потере claim и вычисление срока после row lock. C027/C032 открыты: restart/outage drills, совместимый rollout, внешние ограничения отмены и гонка Telegram.

> Позже 02.11: 718 full isolated tests passed. Воронка теперь считает confirmed proposal с явно dry_run=false и исключает simulation/inbox; старые записи без режима требуют сверки. Метрика outbox учитывает pending/sending. C096/C114/C115 остаются открытыми.

> 02.11 (позже): generation lease/token fencing, предел 5 попыток, сохранение по диалогам и ручной requeue реализованы; 716 full tests и 29 targeted после фильтра lease. C027/C032 открыты: нет heartbeat, полной проверки restart/Kwork outage, уведомления оператора и атомарности сетевого fetch. Смешанный rollout старого и нового worker запрещён; см. [операторский порядок](operator-inbox-generation-recovery.md).

> 02.11: 710 full isolated tests passed, 53/115 закрыто. Входящие фиксируются перед LLM, повтор после ошибок с backoff; C027/C032 открыты из-за окна сетевое чтение→фиксация, отсутствия claim генерации, предела попыток и operator recovery. Следующие задачи — закрыть эти риски, затем legacy pending и unknown-result recovery. Реальных отправок не было.

> Актуализация 01.11: **53/115 подтверждено, 62 открыто; 707 isolated tests passed**, Ruff и diff check прошли. После аудита исправлены price/snapshot/form/preflight/scraper, атомарность нового proposal/outbox и деградация Qdrant. Следом: durable inbox capture до LLM, восстановление исторических pending, operator recovery, retry Qdrant, реальная browser auth и актуальная форма. Preflight намеренно не пропускает pilot без реальной auth-проверки. Runtime может использовать разные версии кода; не перезапускать и не отправлять без отдельного согласования. Старые записи ниже исторические.

# Промпт продолжения после повторного аудита

```text
Работай в /home/arthur/Projects/personal/jobpilot-ai. Реализуй исправления по актуальным документам:
- docs/audit-2026-10-14.md
- docs/audit-2026-10-14-evidence.json
- docs/remediation-plan.md
- docs/remediation-checklists.md
- docs/remediation-progress.md

Состояние 01.11: сначала прочитай docs/audit-2026-11-01-agent-review.md и docs/audit-2026-11-01.md: исправить fail-open browser form, несоответствие показанного и одобренного текста, цену ниже минимума ИИ и preflight, затем проверить сквозной поток и подготовить безопасную переоценку старых карточек. Извлечение buyer stats из отдельного блока, перепривязка buyer при rescrape, обновление полей и fingerprint buyer ID, OpenRouter auto TTL/fallback и ранее локальный Ruff проходил; read-only inventory и ручная fenced переоценка свежеполученного заказа с новым /rereview подготовлены; реальный сквозной поток ещё не подтверждён; legacy статистика без подтверждённого источника блокируется. `.env` и worker содержат PLATFORM_DRY_RUN=true, основные Compose сервисы запущены; не перезапускай их без отдельного разрешения. 50/115 критериев подтверждено, 65 открыто; текущие isolated tests 661 passed/17 failed, Ruff 4 ошибки. C043 остаётся открытым для аудита прочих сетевых путей: summary LLM и inbox edit fetch вынесены из caller DB-транзакции, отдельный summary cache сохраняется с проверкой конкурентных изменений; job embedding вынесен из сессии и сохраняет pending до успешной актуальной индексации. Outbound interaction атомарен с подтверждением новых inbox attempts и reconciliation; исторические пропуски ещё не backfilled. Chat context использует snapshot confirmed attempt и исключает явно отмеченный dry-run; legacy confirmed статусы требуют отдельной проверки. C061 закрыт после изоляции ошибок scrape; restore/rollout safeguards улучшены, но isolated restore/restart drills ещё не выполнены. C026 частично исправлен: fallback ключ устойчив, legacy DB запись сопоставляется по пользователю/времени/тексту, неоднозначный JSON блокируется. Ещё нужны durable operator recovery, проверка legacy копии и разбор сообщений, неразличимых без ID; смешанный rollout старых и новых pollers запрещён. Конкурирующие inserts одинакового ID теперь используют ON CONFLICT, но LLM до durable capture и полная C032 ещё открыты. Outbox fencing улучшен, C028 ожидает operator recovery. Известные неподтверждённые поля title/payment блокируют Kwork submit; явный approval и реальный DOM остаются открыты. Сначала перепроверь текущие код/diff и применимые AGENTS.md; сохраняй чужие изменения и данные.

30.10 B3: исключение в browser submit больше не повторяется через proxy; post-click failure и косвенный success без payload receipt → unknown_result, inbox send exceptions после начала запроса тоже ambiguous. Автоматическое подтверждение реального Kwork proposal сейчас ограничено до реализации точной receipt; ручное разрешение ещё не реализовано.

30.10 D5: несовпавший явный project ID больше не заменяется заказом по username; unbound pending не регенерируется без явной привязки. Реализовать selector и thread isolation, после чего повторно проверить C065/C066.

Выполни план A→B→C→D→E→F→G→H с его зависимостями; destructive safeguards F можно подготовить раньше. Сначала воспроизведи R-дефект, потом внеси минимальное достаточное исправление, проведи meaningful checks и обнови связанные C-ID/evidence. Сохрани подтверждённые fixed части, human approval, review-first policy и намеренную ручную паузу. Не делай новую архитектуру/новые площадки вместо устранения блокеров.

Главные задачи: fail-closed runtime auth; rate keys по verified identity; atomic claims по business object; exact approved version/content/price/duration; fenced persist/finalize/recovery; exact reconciliation с temporal boundary; proposal+pending+outbox одной TX; inbound commit до LLM; durable draft/learning/embedding retries; reward uniqueness по milestone, применяемые transitions; async lazy Qdrant и provider/model/dim/version collections; thread selector и snapshot history; frozen migrations/single runner; Docker lock/mounts/healthchecks; safe restore target и rollout readiness; достоверные metrics/costs.

Конкурентные проверки должны запускаться одновременно, из двух DB connections/процессов. Последовательный second call недостаточен. Unknown send result не повторяй вслепую. Lease expiry не доказывает, что сообщения не было. Не держи TX во время внешнего network call. Dry-run не должен загрязнять рабочий sent/funnel scope.

Все проверки: отдельная *_test PostgreSQL, Redis>=2, fake Telegram/Kwork/LLM, credentials внешних сервисов отключены. PLATFORM_DRY_RUN не заменяет изоляцию и не отключает платные LLM/Telegram notifications. scripts/reaudit_probes.py воспроизводит часть текущих ошибок; прочитай его safeguards и запускай только в отдельной test environment.

Не меняй production config/DB, не делай rollout/restart/restore действующих сервисов, не отправляй реальные сообщения и не расходуй коннекты/платные LLM без конкретного разрешения. Подготовь готовый reviewable результат до такого шага. Не запускай auto-close/finish-owner для накрутки галочек. Не выводи .env/секреты/session backups. Не создавай commits/PR/deploy без указания пользователя.

[x] ставь только после полного критерия, с командой, результатом, датой, ревизией, средой и артефактом. Partial/unverified остаются открытыми. Исправляй report tests для достоверности, а не под требование0 open. Release gate должен оставаться красным до реального закрытия blockers. Live signoff требует отдельного human authorization и внешнего UI evidence, не generator ui_matches_db=True.

Веди progress и evidence; после каждой группы run pytest/Ruff/нужные integration tests, миграции/config/build по необходимости. Продолжай независимые разрешённые задачи при недоступной внешней проверке. Не выдавай частично выполненный этап за готовый.

В финале сообщи что исправлено, какие R/C закрыты, фактические результаты tests/gates и оставшиеся блокеры; приложи ссылки на документы.
```

Для ограниченного запуска добавь: «В этой сессии выполни только блокB и необходимые dependencies; критерииB должны быть доказаны, остальные галочки не меняй».

15.10 B2.1: preview message/chat/hash binding и inbox edited-send исправлены;239 isolated tests passed. Остаток B2 — durable approval snapshot и обычные versioned approve-карточки, frozen payload/price/duration и in-flight edit/regenerate guard.50/115 подтверждено,65 открыто; deployment не выполнен.

15.10 B2.2: Telegram edit/regenerate защищены от незавершённой доставки; stale regeneration не перезаписывает новую версию.249 isolated tests passed. Остаются durable approval snapshot/frozen payload и проверка остальных write paths.50/115 подтверждено,65 открыто. Deployment не выполнен.

15.10 D1: pipeline persist/release/error/recovery fencing исправлен в описанной области;256 full isolated tests +10 targeted после финальной правки. C053 частично, ещё открыта lease/terminal/freshness приёмка. Следом продолжить durable approval B и остальные write paths.50/115,65 открыто; deployment не выполнен.

15.10 B2.3: inbox maintenance больше не меняет draft под прежней карточкой: supersede+new pending+outbox атомарны, stale/approved/sending защищены.268 full +11 targeted tests прошли. Следующий шаг — durable approval snapshot/frozen payload.50/115,65 открыто; deployment не выполнен.

15.10 B2.4: durable presented proposal snapshot + migration0005 + stale ordinary Approve/Edit guard.276 full tests passed; isolated0004→0005 и DB immutable update проверены. Рабочая БД0005 НЕ применена; нужна до deployment notify/approval кода. Следующий шаг — human approval event и обязательный sender enforcement exact payload/price/duration, edit/inbox/resubmit.50/115,65 открыто.

15.10 B2.5: immutable proposal approval event (actor/chat/message/payload) и обязательный sender event match реализованы, ordinary+edited callbacks подключены.284 full +10 targeted tests passed; isolated0005→0006 проверена. До deployment нужен0006, рабочая БД не мигрирована. Далее — frozen actual form text/price/duration, inbox durable approval и resubmit policy.50/115,65 открыто.

15.10 B2.6: exact text/price/duration передаются до browser; padding/recompute/fallback и auto-edit existing offer удалены. Pre-submit DOM readback fail-closed.295 full +13 targeted passed. C041/C051 ещё открыты до HTML/staging/отдельного edit-existing approval; далее durable inbox approval и полная UI review.50/115,65 открыто; рабочая БД не мигрирована, для deployment нужен0006.

15.10 B2.7: durable inbox presentation/event и exact sender enforcement, DB callback tests,308 full +11 targeted passed. C042 закрыт для кода;51/115,64 открыто. Для deployment нужен0007; рабочая БД не мигрирована. Далее full-text UI review, thread selector, HTML/staging form и explicit edit-existing approval.

16.10 B2.8: full exact plain-text review proposal/inbox/edits, UTF-16 split, controls after all parts; old truncated formats invalidated, stale Skip guarded.316 full +11 targeted passed.51/115,64 открыто; C012 ещё partial. Далее browser HTML/readback и remaining callback/FSM/operator review. Схема без изменений сверх0007, рабочая БД не мигрирована, deployment не выполнен.

16.10 B2.9: synthetic offline DOM/Chromium контракт:6 исходных регрессий исправлены,30 targeted passed;316 full isolated tests passed, browser checks отдельно opt-in. CI browser job настроен, remote CI не подтверждён. C041 остаётся partial до настоящей HTML/staging, других form params и receipt; далее form params/operator re-review.51/115,64 открыто; рабочая БД не мигрирована, deployment не выполнен.

Финальная проверка B2.9 после всех изменений: **333 passed,42.89s,без skips** в одном запуске с JOBPILOT_INTEGRATION_TESTS=1 и JOBPILOT_BROWSER_TESTS=1, включая17 DOM tests. Отдельный browser+unit run:30 passed,19.85s. Промежуточный316/15skip выше — история до финального combined run, не итог. Gate отчётности проверен5 tests passed.51/115 подтверждено,64 открыто.

16.10 B2.10: /rereview reply/UUID и atomic/idempotent pending+presentation+outbox, strict delivery guards и FSM cleanup реализованы.376 unified full +10 final targeted passed.51/115,64 открыто; C111/C112 partial. Operator legacy re-review теперь есть, но all callback/FSM, полная queue/unknown resolution, form params/staging и edit-existing ещё открыты. До deployment нужен0007, рабочие процессы не перезапускались.
