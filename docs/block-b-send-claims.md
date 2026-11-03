# Блок B: журнал реализации15–16 октября 2026

Основание: R03–R05 [повторного аудита](audit-2026-10-14.md) и [план B](remediation-plan.md). Блок B целиком ещё не завершён. Актуально16.10: immutable approval и full-text review реализованы; synthetic DOM/Chromium проверка пройдена. Остаток — реальные form params/staging, полная operator queue/unknown resolution и edit-existing flow. Ниже сохранён последовательный журнал B1–B2.9; ранние ограничения относятся к дате соответствующего этапа.

## Изменения

- Все claims сериализуются блокировкой бизнес-объекта: Job→Proposal для отклика, TelegramInboxPending для ответа. Проверка и создание attempt выполняются в той же транзакции. Восемь параллельных initial/retry запросов получают одного победителя; другие возвращают blocked без IntegrityError.
- Активный sending/unknown_result/needs_attention блокирует остальные content keys того же заказа/pending. Смена текста больше не обходит незавершённую отправку.
- Prepare отклоняет draft proposal, неподтверждённый inbox pending, другой текст и другого получателя. Обычный inbox APPROVE фиксирует статус approved перед отправкой. Проверка означает согласие с текущим сохранённым содержимым; полноценная неизменяемая запись approval версии/цены/сроков пока отсутствует.
- Claim не вызывает сеть. Истёкший lease сохраняется как unknown_result и блокирует повтор; сверка вызывается отдельно после закрытия read-транзакции.
- Finalize, commit-failure и reconciliation используют единый порядок блокировок: бизнес-объект→attempt, затем проверяют статус/token. Поздний commit failure не переводит confirmed в unknown и не меняет новый retry claim.
- Finalize старой версии не перезаписывает более новый draft. Snapshot попытки остаётся записью подтверждённой отправки старой версии.
- Reconcile inbox требует полный текст, положительно определённого автора, внешний ID сообщения и timestamp не раньше начала попытки. Prefix-only, старое сообщение, неизвестный sender и нечисловой/NaN timestamp не подтверждают отправку. При секундной точности времени легитимная отправка в той же секунде может остаться неопределённой — это безопасный отказ от угадывания.
- Live proposal reconcile не использует «на странице есть offer»: без receipt конкретного payload возвращается inconclusive. Unknown остаётся заблокированным; оператору нужна сверка. Dry-run подтверждение применяется только к attempt с явным dry_run marker в simulated режиме.

## Доказательства

До исправлений7 новых integration tests падали: initial UNIQUE race, несколько retry winners, обход другим content key, перезапись confirmed поздней ошибкой, отправка другого текста и сеть внутри claim.

После исправлений проверены:

- 8 параллельных initial claims и8 retries на PostgreSQL;
- оба вида claims из двух независимых Python-процессов, синхронизированных перед началом вызова;
- 8 конкурентных ProposalSender и8 inbox sends с fake platform: по одному реальному вызову mock;
- отклонение draft/другого текста/получателя;
- неизменность нового token при позднем finalize/commit-failure;
- сохранение нового draft при finalize старой версии;
- строгие положительные и отрицательные примеры reply reconciliation.

Тесты: `tests/test_send_attempt_atomicity.py`, `tests/test_send_attempt_reconcile.py`. Все внешние платформы заменены fake; тесты используют отдельную *_test PostgreSQL и Redis14/15. Полный прогон:232 passed за24.66s; Ruff и diff whitespace checks проходят.

## Закрытые критерии

C037/C038: параллельная доставка через общий sender/claims, включая отдельные процессы. Это не доказательство version binding Telegram preview; оно относится к C012/C040/C042.

C043: claim и sender/reconciliation не делают внешние вызовы под бизнес-транзакцией.

C049: token/state fencing отправки и сохранение нового draft. Fencing outbox и pipeline — отдельные открытые C032/C053.

## Следующий шаг: B2

1. Неизменяемая запись одобрения конкретных text/version/price/duration и получателя.
2. Snapshot в момент показа карточки/preview; устаревшая карточка не одобряет новый draft. Отдельные правила new offer и edit/resubmit.
3. Удалить изменение фактического payload после approval, зафиксировать цену и длительность, запретить произвольный fallback формы.
4. Безопасный запрет regenerate/edit в sending/unknown и проверка всех скриптов отправки.
5. Payload-specific подтверждение live proposal либо управляемый manual resolution без автоматической повторной отправки.
6. Отделить simulation от рабочего sent/funnel scope.

## Применение

Миграции схемы не нужны для этой части; working DB не изменялась. Рабочие Telegram/Celery процессы не перезапускались: для применения нового кода им потребуется согласованный deployment. Реальных сообщений, Kwork API/browser вызовов и paid LLM в проверках не было. Общий release gate остаётся blocked.

После проверки отдельная audit test БД удалена. Итог чеклистов50/115,65 открыто; release gate ожидаемо возвращает1.

## 15.10 — B2.1: привязка редактирования к показанному превью

Реализовано: send/more/cancel проверяют pending ID, chat ID, message ID и SHA-256 текста превью в FSM. После «ещё правка» старая кнопка сразу недействительна. Отсутствие binding после старой сессии приводит к отказу. Inbox edit/skip проверяют исходную карточку; edited send под row lock сверяет исходный текст/карточку и переносит message binding на актуальное превью перед отправкой. Это исправляет отказ легитимной edited-send кнопки без обхода проверки карточки. Proposal edit сохраняется под блокировками Job→Proposal→Pending с проверкой исходной версии, текста и связи объектов.

Проверки: полный isolated PostgreSQL/Redis suite **239 passed,31.88s**; целевые проверки Telegram **28 passed**; Ruff и diff check. Семь новых регрессий в `tests/test_edit_preview_binding.py`: чужой chat, старое сообщение, изменённый текст, отсутствующая binding, устаревшая send-кнопка, перенос inbox-карточки, немедленная инвалидизация после More. Внешние отправки/LLM отключены. Рабочая БД и конфигурация не менялись, процессы не перезапускались.

**Не завершено:** binding FSM не является долговечным approval snapshot; обычная approve-карточка ещё не фиксирует версию; price/duration и фактический payload после нормализации требуют отдельного исправления; общий запрет редактирования/regenerate при sending/unknown ещё не реализован. C012/C040/C041/C042 остаются открытыми. Итого **50/115,65 открыто**.

## 15.10 — B2.2: запрет изменения draft при незавершённой доставке

Telegram proposal/inbox edit перед сохранением под тем же business lock, что sender, проверяет sending/unknown_result/needs_attention. Истёкший lease не даёт права менять неопределённый payload. Regenerate проверяет защищённые attempts до LLM и повторно под Job lock перед commit; confirmed_sent также запрещает регенерацию. Снимок исходных proposal ID/version/content/status сравнивается после LLM: результат не перезаписывает более свежую правку. Known_failed допускает новую генерацию с новой версией и pending_approval. Не добавлены транзакции вокруг LLM.

Проверки: **249 passed,30.75s**, isolated PostgreSQL `jobpilot_mutation_20261015_test`, Redis14/15, fake генерация/доставка. Девять новых DB регрессий в `tests/test_draft_mutation_guards.py`, дополнительный негативный inbox handler test: отказ не меняет draft/card, не делает commit/send и не очищает FSM. Ruff/pip check/diff check проходят. Первая попытка тестирования:4 failures из-за реального Qdrant constructor, несмотря на fake run; fixture исправлена на отключение конструктора. Первые запросы к локальному Qdrant получили500; дефект eager memory init относится к открытому R08, этим изменением не исправлен. PostgreSQL рабочей БД, runtime config и процессы не менялись; реальных отправок и paid LLM не было.

Остаток: durable approval text/version/price/duration, ordinary approve version binding, проверка остальных write paths (pipeline persist/scripts) и точного actual submission. Старое утверждение B2.1 об отсутствии guard теперь относится к остальным путям, а не исправленным Telegram edit/regenerate. **C012/C040/C041/C042/C053 остаются открытыми.50/115 подтверждено,65 открыто.**

## 15.10 — D1: сохранение pipeline только с актуальным claim

При проверке write paths блока B исправлено окно R07: persist_pipeline_results требует актуальный claim_token (отсутствующий отклоняется), блокирует Job и проверяет token до любых изменений. Защищённые delivery attempts и sent proposals проверяются в той же транзакции; второй persist/replay не создаёт повторный proposal. Все четыре production call sites в scrape_tasks передают token. Release/error блокируют ту же строку перед проверкой token; error не перезаписывает approved/sent/pending_approval. Recovery использует FOR UPDATE SKIP LOCKED и не очищает claim, пока строка заблокирована другим worker. LLM/сеть не добавлены внутрь этих транзакций. Isolated reaudit probe обновлён под явный claim.

Проверки: полный isolated suite **256 passed,42.54s**; после финального изменения protected-error guard целевой suite **10 passed,7.23s**. tests/test_pipeline_claim_fencing.py проверяет stale/missing token для success/error, параллельный persist, stale release/error, locked recovery и protected statuses. Ruff/pip check/diff check проходят. Тестовая БД jobpilot_fencing_20261015_test удалена после проверки; рабочая PostgreSQL/config не менялись, процессы не перезапускались, внешних отправок/LLM не было.

**C053 частично исправлен, остаётся открытым:** нужны heartbeat/достаточная lease policy, terminal filtered/permanent handling и приёмка остальных error/freshness paths. C052/C054 (due retry/queue/единая ошибка ProposalAgent) не завершены. Блок B также остаётся незавершённым: durable approval snapshot, обычные versioned approve-карточки, frozen price/duration/actual payload и оставшиеся inbox maintenance writes. **50/115 подтверждено,65 открыто.**

## 15.10 — B2.3: безопасная maintenance-регенерация inbox

Два пути regenerate_pending_inbox_drafts/resend_inbox_pending_notifications объединены. Обрабатываются только pending; pending_only=False сохранён для совместимости аргумента, но не переоткрывает approved/edited/sent. Перед генерацией фиксируется revision полей pending; после генерации строка блокируется и revision сверяется заново. Sending/unknown_result/needs_attention/confirmed attempts запрещают замену. Перезапись draft_reply старой карточки устранена: старая строка становится superseded, новая pending с новым UUID и notification outbox создаётся одной транзакцией. Ошибка enqueue откатывает обе записи. Старые кнопки упираются в superseded, новые до доставки не имеют message_id и не проходят callback guard.

Fetch/generation maintenance выполняются после закрытия исходной DB-сессии; внутри _generate_inbox_reply thread fetch перенесён перед его context session. Остальные inbound/edit paths с внешней caller-транзакцией этим не закрыты. Telegram-доставка идёт через существующий outbox; ошибки остаются в очереди. updated/queued сообщают постановку, sent считает только sent outbox новых карточек этого вызова, а не старые события глобального batch. Новые items возвращают pending_id/superseded_pending_id; синхронный message_id больше не обещается. При Telegram сбое старая карточка недействительна, новая восстанавливается outbox worker. Outbox fencing/unknown timeout остаются отдельной незавершённой работой C032.

Проверки: **268 passed,33.63s** full isolated PostgreSQL/Redis; после финальной правки sent scope и дополнительных тестов — **11 passed,8.19s** targeted. tests/test_inbox_refresh_safety.py: неизменность старого текста, atomic new pending/outbox, шесть approval/edit/send races, parallel refresh one winner, enqueue rollback, sent scope и запрет переоткрытия approved. В первом targeted прогоне один тест ошибочно захватил pending из других fixtures; исправлена изоляция проверки selection, финальные11 проходят. Внешние LLM/Kwork/Telegram заменены fake; fake token не даёт создать live Telegram bot. Ruff/pip/diff checks проходят. Тестовая БД jobpilot_refresh_20261015_test удалена; рабочая PostgreSQL/config не менялись, процессы не перезапускались.

**Остаток B2:** durable immutable approval text/version/price/duration/recipient; ordinary approve version binding, exact normalized outbound payload и правила resubmit. Исторические заметки B2.2/D1 об оставшихся inbox maintenance writes теперь относятся к состоянию до этого исправления. **C012/C040/C041/C042 остаются открытыми;50/115 подтверждено,65 открыто.**

## 15.10 — B2.4: долговечный снимок показанной proposal-карточки

Добавлены ProposalApprovalSnapshot и forward migration20261015_0005. В отдельной таблице pending UUID связывается с presented payload: полные text/version, job/proposal ID, platform/URL, budget/currency, расчётная offer_price, duration7 и new_offer. UPDATE запрещён DB trigger; ORM helper не изменяет существующий снимок. FK cascade намеренно не добавлен: удаление pending не должно автоматически удалять исторический payload. Отдельные DELETE/retention правила и complete approval journal ещё требуют решения.

notify_new_proposal держит Job→Proposal locks и сохраняет snapshot/pending/outbox одной TX; цена/срок указаны в карточке. Ordinary Approve под Job→Proposal→Pending lock сверяет snapshot с текущими полями и повторно проверяет callback message binding после lock. Edit entry также отклоняет stale/legacy карту. Отсутствие snapshot не считается одобрением; старые карточки требуют нового показа. Body карточки ограничен3800 до footer, чтобы параметры не вышли за лимит Telegram. Полный текст хранится в snapshot, существующее сокращённое отображение текста не заменяет full-payload review (остаётся открытым).

Проверки: fresh migration→0005, затем isolated downgrade0005→0004 и forward upgrade0004→0005; **6 snapshot tests passed,0.70s**; полный isolated suite **276 passed,34.20s**. Проверены text/version/budget/URL/currency stale binding, legacy отсутствие снимка и запрет SQL UPDATE. Первый full run выявил test settings cache (скидка25 вместо ожидаемой15); fixture исправлена на явный env и cache clear. Ruff/pip/diff checks проходят.

**Граница реализации:** presented snapshot НЕ является человеческим approval event. Пока нет immutable actor/approved_at/message event, sender enforcement и frozen actual price/duration/text после нормализации; edit_send/inbox/resubmit ещё должны получить собственную durable approval binding. Сохранённые proposal.status approved не превращаются автоматически в новые события. C012/C040/C041/C042 остаются открытыми;50/115 подтверждено,65 открыто.

**Применение:** production revision не менялась. Перед запуском нового notify/approval кода требуется отдельно применить проверенную0005 к рабочей БД; без новой таблицы путь не заработает. Telegram/Celery не перезапускались, working config не менялся, реальных отправок/paid LLM не было. Existing dev reload может импортировать код; это не подтверждает deployment готовность. Тестовая БД jobpilot_snapshot_20261015_test после проверок удалена. Baseline0001 всё ещё импортирует live metadata (R12):0005 учитывает table-already-exists, но общий дефект исторических миграций не объявлен исправленным.

## 15.10 — B2.5: человеческое одобрение proposal и обязательная проверка sender

Добавлены ProposalApprovalEvent и migration20261015_0006. Событие сохраняет exact payload, pending/proposal ID, actor_user_id, chat_id, message_id, kind(card/edited_preview) и server timestamp. DB trigger запрещает UPDATE. Запись без presented snapshot, без identity или с изменившимися параметрами отклоняется. Edited preview допускает только content и version+1 относительно исходной карточки; цена/URL/получатель/прочие параметры должны совпадать. Callback до записи проверяет FSM preview chat/message/hash и original proposal revision; событие/новая версия/status фиксируются одной TX. Ordinary approval также записывается вместе со status под бизнес-блокировками.

prepare_proposal_send требует event с совпадающим полным payload; approved/edited status сам по себе больше не разрешает отправку. Exact content сравнивается без strip, изменение пробелов также требует нового одобрения. SendAttempt.snapshot_metadata сохраняет approval_event_id и approved_payload. Business claim lock исключает параллельный send другой версии. Legacy approved rows без event не grandfathered: нужно показать новую карточку и получить подтверждение. Scripts send_approved_proposals проходят через тот же sender и также блокируются без события.

Simulation generator обновлён: explicit *_test/integration guard и forced dry-run сохранены; внутри этой среды создаётся synthetic presentation/approval fixture с actor1/chat1/message1. Это НЕ human signoff; live approved_by по-прежнему не генерируется. Production helper не создаёт events из legacy status автоматически. Test fixtures создают synthetic approval явно, проверки отказа без event оставлены отдельными. Политика переноса simulation данных из testDB в working DB этим не разрешается.

Проверки: **284 passed,32.14s** full isolated suite. Fresh upgrade→0006, затем isolated downgrade0006→0005 и forward upgrade0005→0006; **10 targeted tests passed,8.84s** (approval enforcement + simulation safety). Тесты подтверждают zero external call/zero attempt без event, stale text/version/budget, exact whitespace, linkage attempt→event, immutable SQL UPDATE, edited preview и отказ без presented snapshot. Первый full run выявил старый simulation fixture без approval event (279 pass/1 fail); fixture исправлена под изолированным guard, итоговый full зелёный. Ruff/pip/compile/diff checks проходят.

**Остаток:** sender пока не передаёт frozen price/duration до формы; compose/browser всё ещё могут менять фактический текст после claim. Нет полного durable inbox approval event, явной resubmit/edit-existing политики и полной UI callback Update/FSM приёмки. Поэтому **C012/C040/C041/C042/C051 остаются открытыми;50/115 подтверждено,65 открыто**. Прежняя заметка B2.4 об отсутствии event/sender enforcement относится к состоянию до B2.5; полная immutable actual submission ещё не завершена.

**Применение:** перед deployment нужен upgrade рабочей БД до0006 (включая0005). В рабочую БД миграции НЕ применялись; Telegram/Celery не перезапускались, .env не менялся, реальных отправок/paid LLM не было. Без новых таблиц notify/approval/sender пути не готовы к запуску. Тестовая БД jobpilot_approval_20261015_test удалена после проверок. DELETE/retention policy и R12 historical baseline остаются открытыми.

## 15.10 — B2.6: frozen submission до формы и readback перед submit

ClaimResult передаёт одобренный payload из immutable event в ProposalSender. Sender требует exact content match, больше не вызывает compose_kwork_submission и передаёт explicit approved offer_price/duration_days в send_kwork_offer→locked browser→submit_offer→fill form. Browser не пересчитывает цену по текущим settings/budget, не подставляет5000, не дописывает текст. До запуска browser и заполнения проверяются150–2000 символов, integer price>=500 и positive integer duration (bool запрещён). Невалидный payload не запускает browser; исправление требует новой карточки/одобрения.

Duration selector выбирает только exact day label, не принимает17 за7 и не выбирает первую опцию. Отсутствие поля/варианта — отказ. Непосредственно перед submit textarea.description, price и selected duration читаются из DOM и сравниваются с approved payload. Mismatch возвращает approved_form_mismatch без submit. Для price readback поддерживаются только известные named inputs; другая разметка блокируется и потребует расширения после fixture review. Это сознательно консервативно; наличие fallback selector при fill больше не даёт права считать поле подтверждённым. Другие поля (title/payment choice) ещё не включены в полный approved payload.

Наличие старого offer теперь возвращает existing_offer_requires_approval вместо автоматического редактирования по new_offer approval; retained legacy edit helper также fail-closed. Весь approve flow сейчас имеет submission_action=new_offer; edit-existing/resubmit требует отдельного будущего UI/event/action. Это ограничение функциональности, не завершённая поддержка редактирования отправленного offer.

Проверки: **295 full isolated tests passed,39.65s**; затем **13 targeted passed,4.84s**, включая два дополнительных негативных теста. tests/test_frozen_submission.py проверяет invalid payload, no-browser refusal, точные frozen args/text без padding, exact/mismatched readback и duration17≠7, запрет legacy edit. Existing send atomicity fake адаптирован к explicit approved_payload. Ruff/pip/diff checks проходят. Ни live HTML, ни Telegram/Kwork/paid LLM не использовались; test PostgreSQL jobpilot_payload_20261015_test удалена после проверки.

**C041 остаётся открытым:** fake readback проверяет логику, а не совместимость с реальной формой; нужна сохранённая HTML fixture/staging проверка, остальные form fields и actual receipt. C051 остаётся открытым до отдельного edit-existing approval flow. Durable inbox approvals, полнотекстовый UI review и simulation scope тоже не завершены. **50/115 подтверждено,65 открыто**. Прежние B2.5 заметки об оставшихся compose/price/duration заменены этим результатом; общий блок B не объявлен завершённым.

Схема не менялась в B2.6, но весь новый approval путь требует миграций0005/0006 перед deployment. Рабочую PostgreSQL/config не меняли, процессы не перезапускали, реальных отправок не было.

## 15.10 — B2.7: durable inbox authorization и закрытие C042

Добавлены InboxApprovalRecord и forward migration20261015_0007. Отдельные immutable rows фиксируют presented и human card/edited_preview approval: exact text, pending/interaction/job/proposal ID, recipient user/username, исходное сообщение, actor/chat/message/server timestamp. UPDATE запрещён DB trigger. Presented создаётся вместе с новой pending+outbox в обоих production paths (initial inbound и maintenance replacement). Без presentation нельзя одобрить legacy row. Event UUID служит immutable revision inbox payload; отдельного числового version в mutable pending не добавлено.

Ordinary approve под pending lock проверяет card chat/message и presented payload, пишет human event и status одной TX. Edited send дополнительно проверяет FSM preview hash/message, исходную card/text, delivery state и current presentation; разрешён только новый content, recipient/context должны совпадать. Новая binding preview и event/status сохраняются одной TX. Strip после preview убран, sender также требует exact whitespace. Already approved retries не создают новое одобрение автоматически; matching event должен существовать.

prepare_inbox_reply_send требует immutable event с exact payload и текущей chat/message binding; status approved/edited без event блокируется durable_approval_required. SendAttempt хранит approval_event_id/approved_payload; API получает user/text/username и history IDs из этого payload, не из caller history arguments. Так manual test mutation текста/получателя/question/card не переносит старое разрешение на новый payload. Outbound history остаётся отдельным commit после finalize — C036 этим не исправлен. Simulation sent scope/маркер также не исправлялся (C050).

Проверки: **308 passed,33.13s** full isolated PostgreSQL/Redis; fresh migration→0007 и isolated downgrade0007→0006 / upgrade0006→0007; **11 targeted passed,10.07s**. Новый test_inbox_approval_enforcement: zero API без event, stale content/user/username/question/card, whitespace, immutable SQL UPDATE/linkage, edited text vs altered recipient. Ordinary и edit callbacks вызываются с real isolated DB + fake Telegram/FSM/send и подтверждают запись actor/message/exact content до dispatch. Existing concurrency/acceptance fixtures теперь явно создают synthetic approval вместо статуса.Процессные/конкурентные guards из прежних блоков сохранены в полном наборе. Ruff/pip/diff checks проходят.

**C042 закрыт для реализованного кода:** после изменения текста нужен новый proposal version+event либо inbox event revision, старый event не авторизует иной payload. Это не runtime/live signoff. **51/115 подтверждено,64 открыто.** C012 остаётся открытым до полнотекстового UI review/all callback приёмки; C040 — до полного thread/context selector; C041 — до HTML/staging и остальных form fields. C051 explicit edit-existing/resubmit также открыт.

**Применение:** нужен upgrade рабочей БД до0007 (включая0005/0006) перед запуском новых inbox/approval paths. Рабочая PostgreSQL/config не менялись, процессы не перезапускались, live Telegram/Kwork/paid LLM не вызывались. Legacy approved без event не grandfathered; отдельный безопасный operator re-review flow ещё нужен, автоматически переоткрывать эти строки запрещено. DELETE/retention policy, eager historical baseline и full restart/failure acceptance остаются в плане. Тестовая БД jobpilot_inbox_approval_20261015_test удалена после проверок.

## 16.10 — B2.8: полнотекстовый Telegram review

Production ordinary proposal/inbox outbox и оба edit-превью используют send_full_review: заголовок/параметры, все части exact исходящего content и только затем финальное подтверждение с кнопками. Content не проходит sanitize/compose/HTML escaping/обрезку; parse_mode=None явно отключает форматирование, поэтому **markdown**, <tags>, &, тире, пробелы и переносы показаны как в payload. Разбиение не меняет текст и считает UTF-16 units (3000 на часть), не разрывает emoji. Длинные заголовки, включая полный исходный вопрос клиента, тоже разбиваются. Финальная карточка содержит reference UUID, число символов/частей и полный SHA-256. Pending.message_id и FSM binding относятся к этому последнему сообщению. Edit FSM invalidates прежний preview_message_id до начала публикации; частичный сбой не публикует controls и не сохраняет новую binding.

Proposal review показывает version/score/target URL и frozen price/currency/duration; edited preview повторяет параметры исходного snapshot, сохранённые при входе в FSM. Старые FSM без review_parameters отказываются продолжать. Inbox ordinary/edit review показывает user/заказ и полное исходное сообщение. Старый HTML alert_text сохранён в outbox payload для контекста/совместимости данных, но transport approval использует новый plain heading + exact review_content. Это замена прежней краткой approval-сводки; view_job остаётся для полного описания заказа.

В immutable proposal/inbox payload добавлен review_format=full_text_v1. Старые presented snapshots/events без marker не совпадают с текущим payload, поэтому сокращённая старая карточка не превращается в разрешение нового review автоматически. Старые outbox без review_format transport отказывает отправлять. Legacy re-review/reissue остаётся отдельной operator задачей; существующие approved rows автоматически не переоткрываются. Marker — контракт production пути показа, не доказательство фактического прочтения человеком. Simulation fixtures остаются synthetic и не являются live signoff.

Blank inbox draft публикует только Edit/Skip, без Approve; большой heading в этом пути также дробится. Inbox Skip теперь блокирует pending row и требует current presentation, чтобы старая кнопка не скрыла изменённый draft. Edit entry и proposal Skip тоже отвергают stale presentations. Existing approval/send business locks и exact event guards сохранены. При сетевом сбое multipart delivery outbox сохраняет прежнюю conservative needs_attention/retry политику; частично доставленные текстовые части могут дублироваться при известной повторяемой ошибке. Outbox fencing/C032 этим не закрыты.

Проверки: **316 passed,51.17s** full isolated PostgreSQL/Redis. После финального исправления blank-header split — **11 targeted passed,7.69s** (6 full review +5 checklist/gate checks). Новые tests/test_full_text_review.py проверяют exact reconstruction, Unicode UTF-16 size, plain text, controls только на последнем сообщении, отказ при partial failure, legacy format refusal, blank draft без Approve и длинный heading. Дополнительно DB tests: old truncated proposal snapshot не проходит current guard; stale inbox Skip сохраняет pending. Outbox commit-failure test обновлён на multipart (3 send calls до финального message_id). Ruff/pip/diff checks проходят. Full-story Update/FSM и live Telegram не выдаются за выполненные.

**C012 остаётся открытым** до полной приёмки всех callback/FSM ветвей и operator recovery. C040/C041/C051 по-прежнему требуют thread selector, HTML/staging формы и отдельный edit-existing/resubmit flow. **51/115 подтверждено,64 открыто; новых полных критериев не закрыто.** Далее — synthetic browser HTML tests/readback и приёмка remaining callback guards, затем operator re-review.

Новая схема не нужна сверх0007, но ранее подготовленные0005–0007 на рабочую БД НЕ применены. Код без миграций не готов к запуску approval paths. Рабочие PostgreSQL/config и процессы не менялись; live Telegram/Kwork/paid LLM не вызывались. Изолированная БД jobpilot_review_20261016_test удалена после проверки. Исторические заметки15.10 о truncated UI относятся к состоянию до B2.8.

## 16.10 — B2.9: настоящий DOM формы и Chromium CI

Добавлены synthetic offline fixture `tests/fixtures/kwork/offer_form_synthetic.html` и opt-in `tests/test_kwork_form_dom.py`. Это авторская модель контракта Trumbowyg/Vue, НЕ сохранённая страница Kwork и НЕ staging/live evidence. Браузер использует свежий временный context без storage_state/cookies; service workers отключены, context route abort блокирует внешние запросы. Для production submit-path теста только fixture.invalid перехватывается page route и исполняется из локального HTML, без сети. HTML submit только записывает local counter/payload и не создаёт marketplace receipt.

Перед исправлением Chromium воспроизвёл **6 failed /6 passed,12.48s**: hidden textarea не получает exact content; поиск7 выдаётся за выбранные17; другой visible editor игнорируется; дубли description/price/duration принимаются. В ходе исправления дополнительно выявлена DOM-нормализация line breaks при contenteditable.fill. Теперь plain-text editor DOM устанавливается явно, backing textarea пишется native setter с input/change. Readback требует exact editor.innerText + textarea.value, уникальные controls одной формы и однозначный selected duration. Search query не считается выбранным сроком. Missing/duplicate/hidden/cross-form controls и JS price clamp блокируют submit.

Production form readiness больше не ждёт видимости hidden textarea: ждёт attached backend field и visible editor. Два production submit_offer теста проходят через реальные fill/readback/click: exact values дают1 локальный submit, price mutation перед проверкой даёт0 и approved_form_mismatch. Local receipt отсутствует, поэтому даже разрешённый локальный click НЕ возвращает подтверждённый live send.

Итог **30 targeted passed,19.85s**:17 real Chromium DOM tests +13 существующих focused unit tests. Полный isolated PostgreSQL/Redis suite **316 passed,15 skipped,27.80s** до добавления двух production-path browser cases; skipped — отдельно opt-in DOM tests, не подмена browser результата. Финальный browser run включает оба новых production cases. Ruff/pip/diff checks проходят; YAML browser CI job проверен parser-ом. Добавлен отдельный browser-form-contract CI job: locked dependencies, Chromium, explicit JOBPILOT_BROWSER_TESTS=1, отключённые external credentials. Remote CI не запускался, C006 не закрыт.

Sandbox initially блокировал Chromium и Docker socket; offline browser и тестовая БД запускались через одобренное расширение среды. Auto-review отказов не было. Рабочие PostgreSQL/config, session storage и процессы не менялись; Telegram/Kwork/paid LLM не вызывались. Test DB jobpilot_dom_20261016_test удалена после проверок. Схема без изменений сверх0007; на рабочую БД0005–0007 по-прежнему НЕ применялись.

**51/115 подтверждено,64 открыто. C041 частично улучшен, остаётся открытым:** ещё нужны проверка реальной разметки/staging, freeze/readback title/payment/других form params и payload-specific receipt. C012 all callback/FSM/operator recovery, C040 thread selector и C051 separate edit-existing/resubmit остаются открытыми. Следующая работа — остальные form params и безопасный operator re-review legacy карточек. [Контракт и повторный запуск](block-b-browser-contract.md).

Финальная проверка B2.9 после всех изменений: **333 passed,42.89s,без skips** в одном запуске с JOBPILOT_INTEGRATION_TESTS=1 и JOBPILOT_BROWSER_TESTS=1, включая17 DOM tests. Отдельный browser+unit run:30 passed,19.85s. Промежуточный316/15skip выше — история до финального combined run, не итог. Gate отчётности проверен5 tests passed.51/115 подтверждено,64 открыто.

## 16.10 — B2.10: безопасный operator re-review

Добавлены review_reissue service и /rereview handler/menu. Reply на исходную карточку определяет единственную связь chat/message→family/pending; explicit proposal/inbox UUID позволяет восстановить lost-message pending. Проверяются admin chat/user в handler и самом service, source chat/message identity и допустимые статусы. Новый review не выдаёт approval и не вызывает внешние сервисы.

Под общими business locks (Job→Proposal→Pending либо inbox pending) старая карточка superseded, новая pending+immutable presentation+outbox сохраняются одной TX. Proposal content/version не меняются, readiness отзывается в pending_approval, approved_at очищается. Inbox exact text/recipient/context копируются без генерации; inbound pointer переносится на новую карточку. Stable key review_reissue:family:sourceUUID даёт одну замену при8 concurrent requests; replay возвращает ту же запись. Payload хранит источник, инициатора и старый статус/message ID. Ошибка enqueue откатывает всё. Это metadata trail, не завершённый immutable override journal C112.

Новые review блокируются при sending/unknown/needs_attention/confirmed attempt и известной sent history; protected/terminal/cross-chat/ambiguous cases не переоткрываются. Reissue и sender проверены настоящей конкуренцией sessions: может победить только одно действие. Уже имеющийся reissue mapping возвращается без нового queue/send; unknown Telegram delivery не повторяется автоматически. Legacy source без immutable snapshot можно заново показать, но новый approval event появится только после явного подтверждения новой full-text карточки. Заменяется выбранная source-карточка, не произвольные sibling карточки; глобальная cleanup/recovery queue ещё не реализована.

Reply command зарегистрирована до edit handlers; успешный reissue очищает только FSM, связанную с заменённым source, чтобы текст не отправился в старую редакцию. Для уже существующего mapping ответ не обещает нового approval/delivery. После deployment команда появится в меню; живое Telegram menu этой работой не обновлялось.

Проверки: первый target33 passed,9.77s; после добавления race/ambiguous cases **376 full tests passed,61.48s** в unified isolated PostgreSQL/Redis+offline Chromium run,без skips. После финального изменения FSM/repeated response — **10 targeted command tests passed,4.51s**. Всего36 DB/service cases нового сервиса включены в full run. Покрыты8-way idempotency,2 реальных reissue/send races, rollback, exact source preservation/no approval,4 delivery states, legacy sent history, protected statuses, auth/chat/stale/ambiguous/lost-message и real DB reply handler. Ruff/pip/diff checks проходят.

Схема без изменений сверх0007, рабочие PostgreSQL/config и процессы не менялись, реальных Telegram/Kwork/paid LLM не было. Тестовая БД jobpilot_rereview_20261016_test удалена после проверок. **51/115 подтверждено,64 открыто; новых полных критериев не закрыто.** C111/C112 покрыты одной безопасной operator action, но полная очередь/manual unknown resolution/immutable override journal остаются. C012 all callback/FSM, C041 real form/title/payment/receipt и C051 explicit edit-existing также открыты. Далее — остальные form params и полная callback/operator приёмка. [Использование и ограничения](operator-review-reissue.md).
