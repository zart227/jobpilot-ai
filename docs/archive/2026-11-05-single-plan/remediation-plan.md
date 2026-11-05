# Актуальный план JobPilot AI — 14 октября 2026

Основание: [повторный аудит](audit-2026-10-14.md), [evidence](audit-2026-10-14-evidence.json), [чеклисты](remediation-checklists.md). Предыдущий план сохранён в [архиве](archive/2026-10-14-before-reaudit/remediation-plan.md).

**Статус 03.11 (later): актуальный счёт критериев — [remediation-checklists.md](remediation-checklists.md) (113/115 подтверждено, 2 открыто: C109/C110).** Разделы ниже — история и backlog; при противоречии с чеклистом побеждает чеклист + [журнал](remediation-progress.md).

## Правила исполнения

- Сначала воспроизводить R-дефект, затем исправлять его и проверять связанные C-ID. Не повторять выполненные части ради галочек.
- Сохранить текущие данные и незнакомые изменения; не делать reset. Имеющийся untracked session backup содержит потенциально чувствительную сессию: не добавлять в Git и не выводить содержимое.
- Runtime config, production migrations/restarts/restore выполнять в пределах действующих разрешений пользователя и только после подготовки backup, проверки и rollback. Для адресной реальной отправки использовать разрешение на конкретное содержимое и получателя. Подготовка исправлений и isolated tests выполняются автономно.
- В тестах отдельная *_test БД, отдельные Redis>=2, отключённые внешние credentials, fake LLM/Telegram/Kwork. PLATFORM_DRY_RUN сам по себе не изолирует БД и не отключает LLM/Telegram notifications.
- Все state changes проверяются по business object, claim token и version. UNIQUE строки — не замена атомарному claim.
- Не держать DB TX во время сети; unknown_result не повторять вслепую. Не обещать exactly-once без idempotency площадки.
- Полная ручная пауза Kwork, review-first и human approval сохраняются. Любое изменение approved text/price/duration требует новой версии и одобрения.
- [x] только после полного доказательства. Release gate с открытыми blockers обязан падать; report gate не требует завершения проекта.

## Отбор коротких задач Kwork — 30.10

- [x] Изолировать извлечение количества проектов и процента найма в блоке покупателя; отклонять неоднозначную разметку и старые неподтверждённые показатели. Проверены 12 публичных DOM-карточек и offline browser tests; сквозная проверка остаётся отдельным пунктом.
- [x] Для новых обработок требовать минимум 5 проектов, найм от 50%, простой понятный объём и оценку до 3 рабочих часов; недостающие данные отклонять.
- [x] Получать минимальную разумную цену от ИИ; показывать её и фиксировать цену и срок один день в approval snapshot.
- [x] Требовать отдельную оценку календарного срока до одного дня; не оценивать только усечённые 2000 символов описания.
- [x] Отклонять явно обрезанное описание, если раскрытие не подтверждено и полная версия недоступна.
- [ ] Проверить полноту описания, парсинг реальной разметки и качество оценок на примерах с ручной проверкой.
- [x] Блокировать повторный review, подтверждение и отправку старых Kwork-карточек без действующей оценки; сверять данные заказа и заказчика с оценкой.
- [ ] Реализовать операторский процесс безопасной переоценки старых карточек; проверить полный поток с отдельным тестовым заказом без автоматической отправки. Добавлены [read-only отчёт и ручная переоценка одного свежеполученного заказа](operator-kwork-assessment-inventory.md); новая оценка требует отдельного /rereview. Сквозная проверка с актуальной карточкой и оператором остаётся открытой.

## Следующие шаги после 02.11

- [x] Сохранять полученную пачку входящих сообщений до генерации; при ошибке или пустом ответе сохранять received и повторять с backoff после авторизации Kwork.
- [ ] Устранить окно между сетевым чтением текущего диалога и фиксацией; eager capture с platform ID сразу после `get_messages`, per-message commit в цикле. Проверить отказ и рестарт на разных стадиях.
- [ ] Проверить heartbeat 15-минутного claim в restart/outage drills и подготовить совместимый rollout; добавить автоматическое operator notification и журнал. Heartbeat, пять попыток и [явный requeue](operator-inbox-generation-recovery.md) реализованы и проверены изолированно.
- [ ] Восстановить исторические pending без слепой повторной отправки и реализовать разрешение неопределённого результата. Read-only `ops_pending_cards.py` показывает delivery evidence и Kwork gates; на working (03.11) **10 pending, 0** с `review_prerequisites_present` (5× msg 999/NA; 3× `assessment_stale` с snapshot на 3271087/3270996/3270919; остальные `buyer_not_eligible`).
- [ ] Завершить durable retry всех типов памяти; rollout **0008** на `jobpilot_ai` выполнен. Переиндексация: [operator-qdrant-reindex.md](operator-qdrant-reindex.md) — инвентаризация сделана; mass `--apply` и удаление legacy Qdrant — нет.
- [ ] Проверить [старые счётчики job attempts](operator-job-retry-recovery.md) до rollout; cap, heartbeat, журнал attempts, isolated и **process-level E2E** restart drill готовы; stale recovery снимает enqueue dedup. Смешанный rollout и полная классификация ошибок на всех этапах остаются.
- [ ] Сверить исторические confirmed sends без явного режима; воронка уже исключает dry-run, simulation и inbox, но может недосчитывать старые записи. Дополнить operational metrics и учёт стоимости LLM.
- [ ] Реализовать и проверить настоящую browser auth, актуальный form contract, Qdrant recovery и изолированные restart/restore drills.

## Приоритет после сверки 01.11

**Выполнено:** fail-closed synthetic form, точный review/snapshot/sender, минимум цены ИИ, preflight, обработка текста, scraper recovery, атомарный proposal/outbox и деградация при недоступном Qdrant. Изолированно 707 тестов прошли, Ruff чистый. **Остаётся:** настоящая browser auth в preflight (сейчас намеренно блокируется), актуальная форма и receipt, восстановление старых карточек, durable inbox capture и retry памяти. Стек ранее обнаружен работающим в dry-run; процессы не перезапускались и могут использовать разные версии кода.

1. Проверить полный поток извлечения и оценки реальных карточек без отправки; реализовать безопасную переоценку устаревших записей. При повторном scrape сохраняются актуальные поля и корректная привязка buyer; оценки теперь привязаны к buyer ID, предыдущие оценки требуют пересмотра. Read-only inventory и ручная переоценка подготовлены; реальный сквозной поток ещё не подтверждён.
2. OpenRouter TTL выбранной модели и fallback при временном сбое каталога исправлены; auto fallback принудительно использует Ollama auto с проверкой бесплатной доступности и сохраняет тип задачи. Нужна дальнейшая сквозная проверка. Ollama auto проверяет /api/show; облачный выбор использует free usage со страницы аккаунта, квоту, баланс, auto-reload, retirement и совпадение аккаунта с API-ключом. Обновлённая сессия позволила проверить реальный cloud auto и короткие JSON-запросы beam/gpt-oss:120b; качество и runtime deployment не подтверждены. Отсутствие списаний при одновременных изменениях аккаунта сервером не гарантировано. Скриншот пользователя показывает account-specific список моделей, доступных в рамках free usage credits, и сроки отключения: после авторизованного доступа проверить реальный DOM и подготовленный synthetic parser, извлекать список и retirement, сопоставлять с /api/tags и /api/show, использовать ограниченный TTL и fail-closed при ошибках/исчерпании квоты. Не считать бесплатную квоту бессрочным нулевым тарифом.
3. Локально Ruff и полный isolated suite проходят; remote CI подтвердить отдельно.
4. Проверить безопасную ручную переоценку и /rereview на отдельном тестовом заказе без автоматической отправки; код подготовлен и проверен с fake LLM.
5. Проверить и стабилизировать dry-run: сейчас `.env` и worker содержат `PLATFORM_DRY_RUN=true`, основные Compose сервисы запущены; исторические утверждения о runtime, backup, паузе и схеме не являются текущим подтверждением. Рабочие изменения и live-действия выполнять только по отдельному разрешению. [Доказательства](audit-2026-11-01.md).

## A. Восстановить достоверную приёмку и закрыть runtime-доступ

**Приоритет:** P1. **Зависимости:** нет. **R01, R02, R15. C006–C016, C100, C106, C109–C110.**

**Уже сделано:** middleware/dependency, настройки токена, local bind инфраструктуры; в этом аудите исправлены статусы документов и отчётность. Код guards исправлен15.10: runtime API без токена503. Операционные token/deployment и version-card проверки остаются.

**Изменения15.10:** fail-closed guard, verified quota+Lua, group identity, simulation/live evidence v2 и запрет auto-close реализованы.212 isolated tests passed. Детали: [блок A](block-a-access-and-evidence.md). Открытые подпункты ниже не закрывать до runtime/deployment и versioned approval review.

**Работы:**

1. Fail-closed API auth в сетевом режиме; отсутствие токена при обязательной защите блокирует запуск/доступ. Отдельно описать opt-in localhost development mode.
2. Обязательные Telegram user/chat identity для групп и /dev; version/message checks для всех preview callbacks. Согласовать и применить runtime config только после готового reviewable diff.
3. Rate limit key строить по проверенной identity; не позволять secondary header менять bucket. INCR/EXPIRE атомарны; отдельная анонимная/IP квота и workload лимиты.
4. Разделить simulation/live signoff. Удалить auto ui_matches_db и auto approved_by; запретить live closure по dry-run. Validator проверяет mode, attempt evidence, источник UI подтверждения и отдельное human authorization.
5. Заменить closure scripts, которые автоматически переписывают чеклист, на запись evidence для ручного review. Статическая матрица не подтверждает поведение кодом наличия файла.

**Приёмка:** существующие auth unit tests плюс runtime GET без token=401/503 после согласованного применения; valid Bearer+любой другой header даёт одну квоту; dry-run signoff отклоняется live validator; CI допускает честный backlog как отчёт, но запрещает release.

## B. Атомарная отправка и approval snapshots

**30.10, B3 частично:** браузер не повторяет submit через другой proxy после исключения в submit; ошибки после клика и косвенный success без точной payload receipt приводят к unknown_result. Inbox send ошибки после начала сетевого вызова тоже считаются неизвестным результатом. В текущей реализации успешный live Kwork proposal без точной receipt не станет confirmed автоматически и потребует будущего operator reconciliation. C045/C047 остаются открытыми до полной stage/crash приёмки и реализации ручного разрешения.

**30.10, B2.11:** автоматические выбор оплаты и подстановка названия удалены; при обнаружении известных полей оплата/название форма блокируется и при заполнении, и непосредственно перед submit. Это временный fail-closed guard: полноценные snapshot/review/readback параметров и проверка реальной разметки ещё необходимы; C041 остаётся открытым. Из-за ограничения реальные формы с этими полями не отправляются. Далее — безопасная модель approval для всех параметров и C032 fencing outbox, затем остальные задачи блока C.

**16.10, B2.10:** /rereview reply/UUID и transactional reissue реализованы: одна source→одна новая full-text карточка, no automatic approval/send, strict actor/chat/delivery guards; race с sender и rollback проверены.376 full +10 targeted passed. Schema head0007 без изменений, deployment не выполнялся. C111/C112 partial — полная queue/unknown resolution/immutable override journal ещё впереди. [Operator flow](operator-review-reissue.md). Следом — remaining form params и all callback/FSM acceptance.

**16.10, B2.9:** browser-contract реализован и прогнан настоящим Chromium на synthetic offline HTML.6 исходных DOM регрессий исправлены;30 focused passed, затем333 tests passed в едином isolated+browser прогоне (оба opt-in flags=1,без skips). Readback проверяет editor+textarea, unique/same-form controls и selected duration, hidden textarea заполняется native setter/events. CI browser job настроен, remote run отсутствует. C041 не закрыт: current DOM/staging, title/payment/прочие params и внешняя receipt ещё впереди. [Повторный запуск и ограничения](block-b-browser-contract.md).

**16.10, B2.8:** full-text review вместо truncated/sanitized карточки: plain text части без нормализации, UTF-16 split, кнопки только после всех частей, финальный SHA-256. Ordinary/edit proposal+inbox используют общий путь; review_format в immutable payload инвалидирует старые truncated presentations. Skip inbox теперь locked и требует current presentation. C012 остаётся открытым до all-callback/FSM приёмки; далее HTML/staging формы, thread selector и operator recovery. Новая миграция не нужна сверх0007, но rollout всё ещё не выполнен.

**15.10, B2.7:** immutable inbox presentation/human event + exact sender enforcement реализованы. ID события — revision inbox payload. Ordinary/edit callbacks проверены на DB;308 full +11 targeted passed. C042 закрыт для кода, C012/C040/C041 остаются открытыми. Перед deployment требуется0007; рабочая БД не мигрирована. Следом — полнотекстовый UI review, HTML/staging формы, thread selector и explicit edit-existing/resubmit.

**15.10, B2.6:** sender/browser передают exact approved text/price/duration без compose/padding/recompute/fallback. Перед submit — readback textarea/price/duration с отказом при несовпадении. Existing offer больше не auto-edit по new_offer approval. C041/C051 остаются открытыми до HTML/staging приёмки и отдельного edit-existing approval flow. Схема по-прежнему требует0006 перед deployment.

**15.10, B2.5:** durable proposal approval event и sender enforcement реализованы для ordinary card/edited preview. Sender требует совпадающий immutable event text/version/params и связывает attempt с event ID. Отсутствующий/старый event блокирует отправку. Требуется migration0006 после0005 до deployment. Остаток — actual form payload/price/duration, inbox approvals и resubmit policy; полные C-ID пока открыты.

**15.10, B2.4 частично:** введена proposal_approval_snapshots и forward migration0005. Новая proposal-карточка сохраняет immutable payload в одной TX с pending/outbox; ordinary Approve/Edit отклоняет legacy/stale snapshot. Это presented snapshot, не завершённое human approval. Следом — durable actor/approval event и sender enforcement actual payload/price/duration, включая edit/inbox/resubmit. Применение0005 на рабочей БД не выполнялось и требуется перед deployment этого кода.

**15.10, B2.3:** inbox maintenance regeneration/resend заменяет pending отдельной карточкой+outbox, старую помечает superseded. Генерация отделена от TX сохранения; stale result и защищённая доставка блокируют замену. pending_only=False больше не переоткрывает processed rows. Durable approval snapshot остаётся следующим открытым шагом.

**15.10, B2.2:** regenerate проверяет отправку до LLM и под business lock перед сохранением; устаревший результат генерации отбрасывается при смене версии/текста/статуса. Telegram edit не меняет draft при sending/unknown_result/needs_attention. Проверка остальных write paths и durable approval остаются открытыми. Детали и проверки — в журнале блока B.

**15.10, B2.1 выполнена частично:** preview callbacks привязаны к message/chat/hash; inbox edited-send исправлен; proposal edit проверяет исходную версию под lock.239 isolated tests passed. Следующий шаг — долговечный approval snapshot text/version/price/duration и binding обычных approve-карточек. Критерии пока не закрыты.

**15.10, B1 выполнена:** business claims/fencing/строгая conservative reconciliation и4 критерия закрыты.232 tests passed. [Реализация и остаток B2](block-b-send-claims.md).

**Приоритет:** P1. **Зависимость:** A. **R03–R05. C037–C051.**

**Уже сделано:** send_attempts, snapshot fields, обычный token check finalize, known_failed/backoff и unknown states. Не переделывать всю интеграцию — исправить границы атомарности.

1. Lock/CAS по proposal/job или inbox pending; bounded retries и ON CONFLICT на создание. Один business object не может одновременно отправляться под разными content keys.
2. Approved snapshot/version/hash/price/duration фиксируется отдельной записью. Sender требует её и сравнивает actual submission после нормализации с approved payload.
3. Regenerate/edit во время sending/unknown блокируются или создают отдельную версию. Старая approval card не одобряет новое содержимое.
4. Reconcile exact content+sender+message/time boundary; proposal reconcile проверяет payload/версию, а не наличие любого offer. Prefix-only запрещён.
5. Finalize/mark_unknown/reconcile CAS по допустимому state и token; истечение lease не даёт новому worker права повторить неизвестную отправку. Network reconciliation выполняется без TX.
6. Не записывать simulated sent в production delivery scope. Dry-run result имеет simulation marker в попытке и бизнес-статусе либо выполняется исключительно в тестовой среде.

**Приёмка:** initial/retry/edit/resubmit из двух DB sessions и двух процессов; zero send для draft/другого payload; stale token и late commit failure не меняют confirmed/new attempt. Старый offer/похожий reply не подтверждает новый payload. Timeout-after-success требует сверки, а не новой отправки.

## C. Устранить окна потери inbox/outbox и истории

**30.10, C3 частично:** confirmed inbox attempt, pending и outbound interaction сохраняются одной TX; reconciliation создаёт ту же точную историю и не дублирует legacy запись с тем же attempt ID. Ошибка записи приводит к rollback и unknown_result. Исторические уже-confirmed записи без interaction не backfilled; crash/restart E2E остаётся открытым. C043 остаётся открытым до аудита всех сетевых путей; inbound fetch/generation и summary LLM вынесены из DB-транзакции, кэш summary сохраняется отдельной guarded TX.

**30.10, C4 частично:** inbox message fetch и генерация вынесены из caller DB-сессии; перед сохранением match и основные поля job/proposal перепроверяются под row locks, устаревший результат отбрасывается без отметки seen. Сохранение inbound до LLM, дедупликация параллельной генерации и полная проверка зависимых DB/сеть путей остаются открытыми; C027/C032/C043 не закрыты.

**30.10, C2 частично:** конкурирующие poller для одинакового platform message ID используют ON CONFLICT и сохраняют один bundle без ошибки; то же относится к первичному seed. Это не предотвращает параллельный LLM до insert и не делает inbound durable до него. C026 остаётся частичным: fallback без message ID теперь стабилен, точные legacy DB записи распознаются, неоднозначные JSON/сообщения блокируются и возвращаются в diagnostic. Отдельный operator recovery, идентичные сообщения с одним временем и rollout legacy-копии остаются открытыми; смешанные версии poller запрещены. C032 остаётся открытым.

**30.10, C1 частично:** fencing outbox success/failure/commit-failure по state/token и блокировка повторного claim просроченного sending; перед каждой частью проверяется актуальный claim, частично доставленные сообщения при ошибке переходят в needs_attention. C032 остаётся открытым: concurrent inbound pollers и неатомарная граница DB/Telegram требуют отдельной приёмки. Durable фиксация ID позднего ответа и operator reconciliation ещё не реализованы. C028 переоткрыт: needs_attention не означает автоматическую доставку после восстановления Telegram.

**Приоритет:** P1. **Зависимость:** B. **R06, R09. C026–C036, C069.**

1. Capture inbound commit до LLM; отдельные processing state/retry для blank/error drafts. First-run seed и JSON policy должны сохранять фактическую идентичность сообщения.
2. Proposal+approval pending+outbox в одной TX со стабильным key proposal/version. Повтор notify не создаёт второй business request.
3. Recovery ищет approvals без events, не только уже существующие outbox rows.
4. Outbox claim/finalize/recovery используют token и CAS. Poller concurrent insert штатно обрабатывает UNIQUE conflict.
5. Confirmed inbox send и outbound interaction фиксируются атомарно; reconcile также восстанавливает историю без дубликатов.
6. Сетевой timeout Telegram остаётся needs_attention; оператор может восстановить карточку, сохранив одно approval/send business action.

**Приёмка:** рестарт между сохранением proposal и notification не теряет карточку; сообщения переживают LLM failure; token mismatch не закрывает outbox; одинаковые inbound events из двух pollers дают одну обработку.

## D. Pipeline fencing, очередь retries и точный контекст

**30.10, D6 частично:** общий контекст API/inbox берёт точный snapshot последней confirmed proposal attempt, исключая явно помеченный dry-run. При отсутствии подтверждения либо чужом proposal текст не подставляется. Старые confirmed записи могут быть недостоверны, thread isolation и payload-specific receipt ещё не доказаны; C067 остаётся открытым.

**30.10, D5 частично:** явный несопоставленный Kwork project ID теперь требует контекста вместо fallback по username; unbound pending не регенерируется по username. Исходный ID записывается в metadata, сообщение без совпадений не заявляет выдуманные два заказа. Ручной selector и thread isolation отсутствуют, C065/C066 остаются открытыми; регенерация unbound pending ограничена до реализации выбора.

**30.10, D4:** ошибки отдельного scraper и ранних стадий заказа больше не прерывают цикл; pipeline/release errors также локализованы, результат содержит failed_sources/failed_jobs. C061 закрыт. Исправлен циклический импорт Celery task modules; оба порядка импорта и регистрация задач проверены в отдельных процессах.

**15.10, D1 частично:** token-fenced persist и production call sites, row locks release/error, skip-locked recovery реализованы;256 full tests и10 targeted после финальной правки прошли. C053 остаётся открытым до lease/terminal/freshness приёмки. Остальные пункты D не завершены.

**30.10, D2 частично:** scoring criteria валидируются до преобразования типов; C056 закрыт. Filter/chat/learning отклоняют невалидные типы и лишние поля, learning weights проверяются до coercion и имеют точный набор ключей; C057 закрыт. Retry policy остаётся открытой.

**Приоритет:** P1/P2. **Зависимость:** B–C. **R07, R09, R11. C052–C061, C065–C071.**

1. Persist принимает claim token; recovery/release — atomic CAS. Ввести heartbeat или достаточный lease с контролем версии; stale worker не очищает новый claim.
2. Queue выбирает due retryable_error с next_attempt/max attempts, исключает terminal/protected/active. Вызвать единый processing error recorder и нормализовать ошибки ProposalAgent.
3. Save job/client выполняется через idempotent upsert или безопасный conflict retry, внутри per-job exception boundary.
4. Strict Filter/Scoring/Learning types/exact keys: bool и numeric strings не выдаются за числа, invalid JSON не получает проходной score. Политика transient invalid output — явная ограниченная retry, затем needs_attention/permanent classification по причине.
5. При конфликте явного project ID запретить username fallback. Хранить thread/project связь; needs_context получает реальный selector и reprocess действие.
6. История по thread и cutoff message ID/time; sent snapshot из attempt. Summary cache учитывает thread/content/version. API /chat не запускает learning без зафиксированного корректного outcome.

**Приёмка:** stale persist/recovery не меняет новый claim; повтор beat не множит активные дорогие работы; due retry с backoff восстанавливается; ambiguous thread требует выбора; старое inbound не получает будущие сообщения в prompt.

## E. Async memory, durable indexing, outcomes и learning

**Приоритет:** P2. **Зависимость:** C–D. **R08, R10. C079–C089.**

1. Async/lazy Qdrant, startup/retrieval failure допускает генерацию без памяти. Обязательный async client не создаёт collections в constructor агента.
2. Indexed/deferred result вместо silent None; durable queue jobs/successful proposals/edit preferences. SQL selection по pending, без окна «последние500»; ошибки не закрывают event.
3. Collection signature provider/model/dimension/version, валидация existing dims, staged reindex и switch/rollback без смешения пространства.
4. Reward уникален по job/milestone независимо от custom event key. Проверить event payload collision, transitions/corrections и journal отрицательных компенсаций по выбранной модели.
5. Outcome+reward+learning event одной TX; learning worker retries после commit/crash и не требует нового outcome. Только confirmed sent version является successful pattern.
6. Один active scoring weight под DB guard, качество перед activation и rollback. Min count — предварительный фильтр, не оценка качества; review-first остаётся фиксированным.

**30.10, E1 частично:** новая job сохраняется с embedding_pending до сетевого вызова, вызов выполняется после закрытия DB-сессии; недоступный embedding не снимает pending. Повтор выбирает pending через SQL от старых к новым и проверяет актуальность текста под row lock. Sync Qdrant, индексация изменений существующих jobs и durable retry для других типов памяти остаются открытыми; C060 не закрыт.

**Приёмка:** повтор hired с разными keys не даёт100; crash после commit восстанавливает learning один раз; отключённые embeddings остаются pending; смена dims/provider не читает старую коллекцию; Qdrant outage не блокирует предложение.

## F. Миграции, production сборка и безопасная эксплуатация

**30.10, F1 частично:** restore больше не завершает соединения и не удаляет существующую БД; принимает только ограниченное имя новой drill-БД и валидный gzip. Он использует настроенный Compose PostgreSQL server: настоящий isolated restore должен выполняться на отдельном сервере с доверенным dump. Readiness smoke требует успешные liveness и ready=true/HTTP 200; rollout требует имеющийся smoke и архив PostgreSQL dump с совпадающей SHA-256 и валидным gzip до миграций. Это не доказывает происхождение backup или его восстановимость. C094/C098 и R16 остаются открытыми до соответствующих реальных isolated drills.

**Приоритет:** P1 для destructive safeguards; P2 для остального. **Зависимость:** safeguards можно делать параллельно A; upgrade после B–E. **R12, R13, R16. C018, C023–C025, C090–C100.**

1. Restore guard: явное тестовое имя, отказ working DB/host, безопасный SQL identifier; запрет interpolation из произвольного env. До guard не запускать restore/drills на действующей инфраструктуре.
2. Readiness smoke требует200+ready=true для rollout. В failure drill503 — отдельное ожидаемое условие, не успешный deploy.
3. Rollout требует backup evidence и preservation check; print напоминания недостаточно.
4. Заморозить migrations: не импортировать живой Base в исторической ревизии; single migration runner. Для уже применённых revisions выбрать forward repair, не переписывать историю вслепую.
5. Проверить upgrade копии legacy schema с данными, fresh/legacy schema equivalence и восстановление backup в изолированной среде.
6. Docker frozen install из lock; consistency pyproject/lock. Production rendered config без source mounts, со healthchecks/restart, конфигурируемыми путями и проверенными правами volumes.
7. Secrets/debug content/retention: проверить реальные логи, session backups и permissions, без публикации содержимого.

**Приёмка:** unsafe restore target отвергается до SQL; 503 rollout возвращает nonzero; fresh/legacy одинаковы; app Docker использует lock, merged production mounts соответствуют документации; backup restore сверяет counts/relations.

## G. Достоверные метрики и продуктовые действия

**Приоритет:** P2. **Зависимость:** B–F. **R14, R09. C096, C111–C115.**

1. Метрики из реальных claim/state: outbox sending вместо claimed, active processing token вместо несуществующего status; last_success, retry/dead-letter age.
2. Funnel proposal sends отдельно от replies/simulation; релевантные, approved, sent, replied, hired — уникальные jobs по бизнес-событиям.
3. Per-call usage/tokens/USD и connects attribution; versioned ledger хранит каждую фактическую попытку, включая failures. Не называть stage_count расходами.
4. Operator queue unknown_result/errors/needs_context с конкретными безопасными действиями и manual override journal.
5. Dry-run следы действующей БД не удалять автоматически; подготовить read-only inventory и согласованный способ исключить simulation из метрик.

**Приёмка:** fixture sending виден в metrics; simulation/inbox не увеличивают external proposal funnel; повтор job processing сохраняет все попытки billing, без двойной агрегации.

## H. Полная приёмка и выпуск

**Зависимость:** все предыдущие блокеры закрыты. **C101–C110.**

1. Один fake full-story тест через graph+Telegram Update/FSM+sender+inbox+learning с настоящей тестовой БД. Existing helper tests сохранить, но не подменять ими сценарий.
2. Отдельные процессы: crash/restart каждого persistence/send boundary; fault matrix PostgreSQL/Redis/Qdrant/LLM/Telegram. Проверить неопределённый успех и recovery.
3. CI unit/integration/build и source/lock/config consistency; remote run evidence отдельно от наличия YAML.
4. Staging migration/backup/failure/reindex drills с датой, средой и артефактами.
5. Затем отдельное разрешение на конкретный live Kwork тест и UI↔snapshot сверка. Simulation signoff его не заменяет.
6. Release допускается после доказанной приёмки; ещё открытые критерии показываются владельцу, а не переносятся в optional ради зелёной цифры.

## Что не нужно переделывать без причины

Дата-независимые pause tests, greeting cleanup, NULL-client guard, urljoin/optional scraper extraction и fixed review-first policy подтверждены. Auth/outbox/attempts/Alembic — рабочая база, которой нужна доводка, а не новая архитектура с нуля. Поддержка новых площадок и отдельный frontend не входят в устранение блокеров.

## Минимальный запуск в работу (30.10.2026)

### Какой режим возможен сейчас

| Режим | Доступность | Смысл |
| ----- | ----------- | ----- |
| **Локальный dry-run / dev** | **После настройки и отдельной проверки**: сейчас dry-run выключен, сервисы остановлены; использовать изолированную тестовую БД и mock внешних вызовов | Scraping, pipeline, Telegram-карточки, симуляция отправки; без доказательства live Kwork receipt |
| **Ограниченный пилот** (один admin, real Kwork/Telegram, минимальный объём) | **Нет без обязательных шагов ниже** | Требуются миграции до **0007**, runtime `API_AUTH_TOKEN`, `TELEGRAM_ADMIN_USER_ID`, backup, smoke `ready=true`, human signoff C109–C110 |
| **Полноценная эксплуатация** | **Нет** | **2** открытых C-ID (C109/C110) + backlog в этом документе; release gate см. чеклист |

### Блокеры по приоритету (ограниченный пилот)

1. **Runtime secrets и auth (A):** без `API_AUTH_TOKEN` API отдаёт 503; Telegram group требует `TELEGRAM_ADMIN_USER_ID`. *Последствие:* открытый API или неоднозначный admin в группе.
2. **Миграции 0005–0007 на копии/стaging (B):** журнал другого агента сообщает о 0007 на рабочей БД; фактическая текущая схема не проверена. *Последствие:* sender/enforcement рассинхрон с кодом.
3. **Backup + rollout smoke (F):** `production_rollout.sh` требует gzip dump и `ready=true`; restore drill на **отдельном** Postgres server. *Последствие:* необратимая потеря данных при ошибке.
4. **Live Kwork receipt (B3/C045–C047):** proposal без точной внешней receipt → `unknown_result`; operator reconciliation не завершён. *Последствие:* ложный confirmed или застрявший unknown.
5. **Inbox / C027–C032:** входящее сохраняется до LLM, per-message commit внутри диалога сужает fetch→commit; token lease с heartbeat; остаются окно до первого commit после `get_messages`, restart/Kwork outage drills и внешняя граница outbox/Telegram.
6. **C026 stable message identity:** fallback hash зависит от `PYTHONHASHSEED`. *Последствие:* дубликаты карточек после рестарта.
7. **Human authorization C109–C110:** simulation signoff не заменяет live. *Последствие:* формальное «принято» без UI↔DB сверки.

### Что можно отложить после пилота

- Полный semantic Qdrant reindex и async client (E) — pipeline работает с `embedding_pending`.
- Metrics/billing ledger C115 — не блокирует единичную отправку при ручном контроле.
- Remote CI proof C006 остаётся открытым; локальный Ruff проходит.
- Thread selector C065–C066 — при ручной привязке заказов.
- Docker lockfile parity (F) — при запуске из текущего compose dev.

### Последовательность подготовки пилота

| Шаг | Критерий готовности | Проверка |
| --- | ------------------- | -------- |
| 1. Конфигурация | `API_AUTH_TOKEN`, Telegram admin IDs, `PLATFORM_DRY_RUN=true` до signoff | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/jobs` → 401/503 без token; с Bearer → 200 |
| 2. Backup | Архив PostgreSQL + SHA-256 в rollout | `production_rollout.sh` dry-run на staging; отказ при битом gzip |
| 3. Миграции | `alembic upgrade head` → 0007 на **копии** | `migration_preflight.py`; counts proposals/approvals |
| 4. Сервисы | compose up; `/health` + `/ready` | `readiness_smoke.sh` → 200 и `ready=true` |
| 5. Smoke сценарий | Telegram APPROVE в dry-run (reissue/smoke card) | `ops_verify_dry_run_approve.py`; `platform_response.dry_run=true` |
| 6. Первый live send | отдельное разрешение; C109 evidence | `validate_live_signoff.py`; UI↔snapshot hash — см. [pilot-step6-live-send.md](pilot-step6-live-send.md) |
| 7. Stop / rollback | unknown_result, needs_attention, `PLATFORM_DRY_RUN=true` | runbook; не resend blind |

Условия остановки: `unknown_result` без operator plan; partial Telegram outbox; расхождение approved hash и формы; ошибка миграции — откат к backup, не «догонять» отправками.

### LLM: автовыбор моделей (30.10)

- `OPENROUTER_*=auto` или `openrouter/free`: каталог `/models`, prompt/completion обязательны и равны 0, другие присутствующие pricing-поля должны быть 0; отсутствующие поля не проверяются. Выбор модели в долгоживущем экземпляре не обновляется по TTL.
- `OLLAMA_*=auto`: список `/api/tags` и проверка `/api/show`; для облака также требуется подтверждение free usage аккаунта. Cloud auto и два коротких JSON-запроса проверены с действующей сессией; рабочая конфигурация переключена по запросу пользователя; сервисы остановлены, runtime не перезапускался.
- Явный id в `.env` отключает автопроверку цены (OpenRouter).
- **Ограничение:** OpenRouter не гарантирует актуальность pricing для редких полей; неизвестная цена → модель исключается из auto. **Ollama→OpenAI fallback** остаётся платным — отключить `OLLAMA_AUTO_FALLBACK` для строго free контура.
- Live OpenRouter/Ollama catalog в этой сессии **не вызывался**; покрытие — unit tests с mocks.
