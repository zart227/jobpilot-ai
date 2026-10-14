# Inbox и outbox уведомлений

## Источник истины

- Входящие Kwork: таблица `inbound_messages` (`platform` + `message_key` уникальны).
- Файл `data/kwork_inbox_state.json` — кэш `seen_keys` для совместимости; при расхождении приоритет у БД.
- Доставка в Telegram: `notification_outbox` с `idempotency_key` (одна карточка на pending).

## Порядок обработки

1. Сбор сообщений **без** `mark_seen` до успешного commit в БД.
2. Транзакция: `inbound_messages` → `interactions` → `telegram_inbox_pending` → outbox.
3. Ошибка LLM не отменяет сохранение входящего; черновик может быть пустым.
4. `deliver_notification_outbox` отправляет Telegram и проставляет `message_id`. При timeout/неоднозначной ошибке или истёкшем lease статус `needs_attention` (без слепого resend). Commit failure после успешного `send_message` также → `needs_attention` с сохранённым `telegram_message_id`.
5. После commit — `mark_seen` в JSON state.

## Первый запуск

Если файла state ещё нет, непрочитанные диалоги **только** записываются в `inbound_messages` со статусом `seeded`, без Telegram.

## Сбой Telegram

Outbox остаётся `pending` / `sending` с backoff; Celery beat `deliver-notification-outbox` (каждые 2 мин) повторяет доставку.
После исчерпания попыток — `needs_attention` (ручное разрешение).

## Proposal approval

`notify_new_proposal` создаёт `telegram_pending` и outbox `proposal_approval` в одной транзакции, затем вызывает delivery worker.
