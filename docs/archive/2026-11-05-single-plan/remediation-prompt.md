> **АКТУАЛЬНАЯ ЦЕЛЬ 05.11.2026:** механический счёт **98 закрыто / 17 открыто** (`scripts/remediation_checklist_status.py`). Шапка чеклиста «103/12» и все «113/115, открыты только C109/C110» устарели. Релиз не принят. Рабочий промпт — блок ниже. Абзацы над ним и под ним — история, не текущий статус.

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
Продолжи /home/arthur/Projects/personal/jobpilot-ai до закрытия открытых критериев. Этот промпт запускается после остановки: заново прочитай галочки и код, не опирайся на память прошлого запуска и не на шапки документов.

Источник правды — строки `- [ ]` / `- [x]` в docs/remediation-checklists.md и вывод `python scripts/remediation_checklist_status.py`. На 05.11.2026 скрипт дал 98 закрыто и 17 открыто. Игнорируй «113/115», «103/12» и «открыты только C109/C110», пока сам не перепишешь их по свежему выводу скрипта. docs/audit-2026-11-04.md не существует; не ищи его и не восстанавливай. Аудиты 14.10, 01.11 и 03.11 — история. План A→H заново не проходи.

Сначала, одним проходом, выровняй статусы с фактическими галочками: шапка чеклиста, хвост чеклиста (там C110 ошибочно назван закрытым), README, remediation-progress, remediation-plan, remediation-closeout, remediation-closeout-plan-2026-11-03, owner-live-checklist-one-pager, live-kwork-prep, kwork-live-acceptance, acceptance-dry-run, autonomous-remediation-run, audit-remediation-matrix, remediation-prompt. Число бери только из скрипта. Это правка противоречий, не закрытие критериев. C109 оставь [x], если validate_live_signoff по-прежнему проходит; не переоткрывай его ради старых шапок. C110 не закрывай signoff-файлом.

Дальше закрывай критерии очередями. В очереди бери 1–3 ID, доведи каждый до [x] или до одного названного внешнего блокера, обнови чеклист, журнал и счётчики, затем следующая очередь. Не останавливайся, чтобы предложить план. Не пиши новые инвентаризации вместо исправления. Не ослабляй текст критерия. Не ставь [x] из-за соседнего закрытого ID. Не держи [ ] только потому, что в заметку вставлен провал соседа.

Очередь A — сломанный код, закрыть в этой сессии:
- C050 и C051 вместе. Сейчас ProposalSender.is_already_sent возвращает true, если на job есть любой proposal в sent/simulated_sent, а send() превращает job_already_sent и new_offer_after_sent в success/unchanged. Нужно: повтор той же уже отправленной версии и того же snapshot — идемпотентный unchanged; другой proposal того же job — по-прежнему отказ, без отправки. Негативные тесты обязательны. Прямой prepare_proposal_send не должен начать пропускать запрещённую новую отправку.
- C060. recover_stale_memory_index_claims выбирает строки без блокировки и обнуляет claim_token. Сделай compare-and-clear или FOR UPDATE так, чтобы новый claim между чтением и очисткой не стирался. Тест из двух сессий: stale recovery против свежего claim.
- В том же проходе по отправке: в _send_kwork_offer (app/services/kwork_browser.py около 1930 и 2011) mark_proxy_failed вызывается без импорта в этой функции — NameError на прокси. Импортируй его здесь. Кандидаты прокси для Kwork не должны запрашиваться как сервис openai. Закрой Ruff F821 на этих строках.

Очередь B — галочки сняты чужим текстом. Перечитай исходную фразу критерия и закрой, если она уже выполнена; если нет — допиши недостающий сценарий именно этого ID:
- C018. Свой тест — downgrade до 0004, seed, upgrade head, строки и FK. Полный production dump относится к C098. Если тест покрывает фразу C018, закрой C018 и убери из него абзац про dropdb.
- C031, C045, C069, C094. У них в причине вставлен провал C104. Закрой каждый по своей фразе, если свои тесты её доказывают. Оставь открытым только с одним недостающим сценарием этого ID, и сразу его реализуй.
- C083 отдельно от C089. Нет второго reward после сбоя Qdrant и повторной индексации — достаточно для C083. Полнота working corpus и switch — только C089.

Очередь C — доказательство, которое можно сделать локально:
- C107. Исправь все ошибки Ruff в app, tests, scripts (на 05.11 их 58: PLW1510, I001, F401, ASYNC221, F821 и остальные). Не отключай правила и не глуши noqa ради зелёного прогона. Затем ruff, pip check и pytest. В отчёте укажи passed/skipped/failed и среду. Browser suite — если Chromium есть; отсутствие браузера запиши, но не оставляй C107 открытым только из-за него, если pytest/ruff/pip прошли и ограничение записано.
- C006. Проверь workflow: unit и lint без production secrets. Локальный ruff не закрывает C006. Если этот diff не прогонялся в CI и push не запрошен, оставь C006 открытым с этой одной причиной и иди дальше. Почини workflow, если он тянет секреты или не запускает lint.
- C101. test_c101 не должен подменять весь graph.ainvoke готовым state. Прогони filter → score → proposal на mock-провайдерах и дойди до confirmed send. Dry-run платформы допустим; пропуск стадий — нет.
- C104. Матрица реальных сценариев: каждый этап, обрыв до commit и после commit, восстановление, без второй бизнес-отправки. Список файлов тестов критерий не закрывает. Допиши недостающие строки матрицы и тесты.
- C105. Отдельное восстановление для PostgreSQL, Redis, Qdrant, LLM и Telegram. Переход /ready 503→200 сам по себе не закрывает критерий.
- C098. Запрети dropdb, если имя не из allowlist drill и если цель совпадает с рабочей БД. Прогони restore и сверку counts и FK только на этой цели. Отдельный физический сервер: если его нет, оставь C098 открытым после безопасного guard и не трогай рабочую БД.

Очередь D — не делать без отдельного разрешения, подготовить пакет и идти дальше:
- C110. Не отправляй отклик и не пиши заказчику. Подготовь один кандидат: job id, proposal id, текст, цену, срок, получателя. Остановись на запросе разрешения только когда очереди A–C закончены.
- C089. Не запускай массовую переиндексацию и не удаляй legacy-коллекции. Не трать платные embeddings. Если локальная бесплатная проверка switch/rollback невозможна, оставь C089 открытым с этим блокером.
- Пункты плана с `- [ ]` (ручная разметка и качество оценок, сквозной /rereview старых карточек, окно get_messages→commit, heartbeat 15 минут в outage, исторические pending без слепого resend) закрой кодом и тестом, где это тот же дефект, что уже в очереди. Остальное перечисли одним следующим действием, не разворачивай новый трек. portfolio-plan.md в эту сессию не входит.

Не перепроверяй пакетом 41 закрытый критерий, у которых в тексте только «код и текущие unit/integration проверки». Если правка задевает такой ID и тест падает — чини. Не снимай с них галочки пачкой и не подтверждай пачкой.

Правила приёмки: [x] только когда все условия этого ID подтверждены командой, средой и результатом в журнале. Конкурентный случай — два соединения или два процесса одновременно. Unknown_result не переотправляй. Не держи DB-транзакцию на сетевом вызове. Dry-run не доказывает live и не изолирует БД. Интеграции — только база *_test и Redis DB ≥ 2, внешние credentials выключены. Не печатай .env, ключи, cookies, session files. Не удаляй рабочие данные. Не делай reset и не трогай чужой незакоммиченный diff. Коммиты, PR и deploy — только если пользователь прямо попросил. Rollout, backup, миграция рабочей БД и рестарт сервисов — после проверки на копии и записанного порядка восстановления. auto-close и finish-owner не ставят галочки.

Сохраняй отбор Kwork, оценку цены и срока, проверку заказчика, точный human approval и выбор доступных бесплатных моделей.

Перед остановкой: закрытые ID и чем подтверждены; что осталось одним действием; команды и результаты; почему проверка недоступна; риски; ревизия рабочей БД и процессы, если смотрел; свежий вывод checklist status; следующий ID. Релиз не объявляй, пока скрипт показывает открытые строки.
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
