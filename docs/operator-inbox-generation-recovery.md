# Восстановление генерации ответа на входящее сообщение Kwork

Сначала проверьте состояние рабочих сервисов и согласуйте операционное изменение. Команда предназначена для записей `inbound_messages` со статусом `needs_attention` после исчерпания пяти попыток генерации; она не восстанавливает неизвестный результат отправки Kwork или Telegram.

Укажите `DATABASE_URL` в окружении безопасным способом, не передавая пароль в истории команд и не копируя его в отчёт. Из корня проекта список выводится без текста сообщений:

```bash
PYTHONPATH=. .venv/bin/python scripts/ops_inbound_recovery.py
```

Для повторной постановки ровно одной проверенной записи:

```bash
PYTHONPATH=. .venv/bin/python scripts/ops_inbound_recovery.py --id UUID --apply
```

Команда проверяет статус и отсутствие созданного interaction/pending, сбрасывает счётчик попыток и записывает время операции. При исчерпании пяти попыток генерации worker ставит в outbox **`ops_alert`** (`inbound_generation_exhausted:{inbound_id}`); доставка через обычный `deliver_notification_outbox`. Read-only сводка outbox: `scripts/ops_notification_outbox_inventory.py`. Read-only сводка inbound/generation: `scripts/ops_inbound_generation_inventory.py` — поля `ops_alert_totals_by_status`, `ops_alert_pending_or_sending` (working **02.11**: inbound `needs_attention` **0**, ops_alert backlog **0**).

Захват генерации рассчитан на 15 минут и периодически продлевается во время вызова ИИ. При ошибке или потере продления вызов отменяется, а результат устаревшего токена блокируется. Длительная блокировка event loop или недоступность базы может привести к истечению захвата и пересечению внешних запросов; отмена локального запроса не гарантирует прекращение обработки провайдером. Не запускайте старую и новую версии inbox worker одновременно: старый код не учитывает этот захват.
