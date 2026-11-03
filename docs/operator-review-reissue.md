# Повторный review карточки — реализация16.10.2026

Код реализован и проверен изолированно, но на рабочие процессы не установлен. До использования нужен согласованный upgrade до0007 и deployment общей версии API/worker/bot; Kwork pause/review-first сохраняются. Новые миграции этим изменением не добавлены.

## Действие оператора после deployment

Ответить `/rereview` на сообщение карточки с кнопками в настроенном admin-чате. Сервис найдёт единственный proposal/inbox pending по chat/message ID. Ответ на отдельную текстовую часть multipart review не считается ответом на карточку.

Если сообщение потеряно, использовать explicit pending ID:

```text
/rereview proposal <pending UUID>
/rereview inbox <pending UUID>
```

Проверяются настроенные admin chat/user и принадлежность исходной карточки этому чату. Неоднозначный reply не угадывается; неизвестный UUID, другой чат, закрытая/защищённая карточка дают отказ.

## Что меняется

Одна короткая TX создаёт новую pending-карточку, immutable presentation и outbox; исходная становится superseded. Текст и получатель inbox сохраняются, ссылка inbound на актуальный pending переносится; proposal content/version сохраняются, mutable proposal/job readiness переводится в pending_approval. Новая карточка требует отдельного человеческого одобрения. Сам rereview не создаёт approval event, SendAttempt и не вызывает LLM, Telegram или Kwork. Ответ команды в Telegram отправляется только после завершения DB TX.

На один source UUID создаётся одна замена: повторные/параллельные запросы возвращают тот же pending/outbox. Outbox сохраняет reissued_from/requested_by/source_status/source_message_id; это служебный audit trail запроса, не immutable human approval и не полный override journal. Если редактировалась именно исходная карточка, FSM очищается; другая активная редакция не затрагивается.

Queued означает постановку в outbox. Сообщение доставляет существующий worker; needs_attention/dead-letter не переотправляются этой командой автоматически. Для уже созданной карточки повтор старого запроса ничего не создаёт и не обещает нового одобрения/доставки.

## Отказы, которые нельзя обходить

Sending/unknown_result/needs_attention/confirmed attempt, известная sent history и sent/terminal/protected statuses блокируют новую замену. Неопределённая отправка сначала требует сверки; эта команда не является resolution/force-send. Отменённые/skipped, чужие и уже superseded без собственного reissue mapping строки автоматически не переоткрываются. Переезд карточек между admin-чатами не реализован.

## Проверки

36 DB/service cases включены в полный изолированный прогон376 passed,61.48s (включая Chromium). После финального FSM/repeated-response изменения10 command tests passed,4.51s. Покрыты8 параллельных запросов→одна замена, две реальные reissue/send races, rollback enqueue failure, legacy без snapshot, отсутствие нового approval, состояния delivery, sent history, terminal source/job, авторизация, чужой чат, stale reply ID, ambiguous reply, восстановление по UUID без message ID и reply handler с настоящей тестовой БД. Командные проверки используют fake Telegram/FSM; полный Telegram Update/FSM/network recovery ещё впереди.

Remote CI/deployment/live acceptance не выполнялись. Общий статус51/115,64 открыто. C111/C112 остаются partial: нет полной operator queue, unknown resolution и immutable override journal. C012 также не объявлен завершённым.
