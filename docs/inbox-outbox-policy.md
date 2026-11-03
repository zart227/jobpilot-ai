# Inbox и outbox уведомлений

## Источник истины

- Входящие Kwork: таблица `inbound_messages` (`platform` + `message_key` уникальны).
- Файл `data/kwork_inbox_state.json` — кэш `seen_keys` для совместимости; при расхождении приоритет у БД.
- Доставка в Telegram: `notification_outbox` с `idempotency_key` (одна карточка на pending).

## Порядок обработки

1. Сбор сообщений **без** `mark_seen` до успешного commit в БД. Fallback key сообщения без platform ID пока нестабилен между процессами; безопасная миграция старых ключей не реализована.
2. Контекст и генерация выполняются до durable insert, после закрытия caller DB-сессии; перед сохранением job/proposal повторно проверяются. Сбой процесса до insert может потерять входящее событие, а параллельные poller могут запустить LLM дважды. Отдельный LLM summary внутри построения контекста всё ещё может удерживать собственную DB-сессию.
3. Транзакция: `inbound_messages` → `interactions` → `telegram_inbox_pending` → outbox; конфликт одинакового platform message ID не создаёт второй bundle. Перехваченная ошибка LLM может оставить пустой черновик без автоматического retry.
4. `deliver_notification_outbox` отправляет Telegram и проставляет `message_id`. При timeout/неоднозначной ошибке или истёкшем lease статус `needs_attention` (без слепого resend). Commit failure после успешного `send_message` также → `needs_attention` с сохранённым `telegram_message_id`.
5. После commit — `mark_seen` в JSON state. Подтверждённый inbox send и outbound interaction фиксируются одной транзакцией; то же относится к последующей сверке unknown_result. Исторические пробелы ещё не восстановлены.

## Первый запуск

Если файла state ещё нет, непрочитанные диалоги **только** записываются в `inbound_messages` со статусом `seeded`, без Telegram.

## Сбой Telegram

Известная ошибка до доставки может оставить outbox `pending` с backoff; Celery beat `deliver-notification-outbox` (каждые 2 мин) повторяет её. Timeout, потеря lease, частичная доставка или исчерпание попыток переводят запись в `needs_attention` без автоматического повтора. Явный requeue одной безопасной строки: `scripts/ops_outbox_recovery.py` (см. [operator-outbox-needs-attention.md](operator-outbox-needs-attention.md)).

## Proposal approval

`notify_new_proposal` создаёт `telegram_pending` и outbox `proposal_approval` в одной транзакции, затем вызывает delivery worker.
