# Политика логирования и диагностики

## Не писать в логи

- `API_AUTH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `CURSOR_API_KEY`
- Пароли Kwork, cookies, полные storage state JSON
- Полный текст переписки и откликов (допустимы усечённые preview ≤120 символов, как в Celery/inbox логах)

## Допустимо

- `job_id`, `proposal_id`, `pending_id`, `send_attempt_id`, idempotency keys
- Статусы, коды ошибок, типы исключений без stack secrets
- Длины сообщений, intent, platform

## Файлы на диске

| Путь | Содержимое | Рекомендация |
| ---- | ---------- | ------------ |
| `data/kwork_inbox_state.json` | seen keys | не коммитить |
| `data/kwork_connects_pause.json` | pause state | не коммитить |
| `data/cursor_bridge.env` | bridge secrets | `chmod 600`, не коммитить |

Ротация: удалять старые debug-дампы вручную; в Docker ограничить размер логов драйвера (`max-size` в logging driver при необходимости).

## Инциденты

При утечке токена в лог — ротация секрета в `.env`, перезапуск api/workers/bot.
