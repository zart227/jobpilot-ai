# Cursor bridge — политика доступа

Dev-агент в Docker **не** запускает bridge внутри контейнера: он подключается к bridge на хосте с токеном из `data/cursor_bridge.env`.

## Ожидаемая схема

| Компонент | Bind / доступ |
| --------- | ------------- |
| `run_cursor_bridge.py` | `127.0.0.1:19247` (по умолчанию) |
| `cursor-bridge-proxy` (socat) | `0.0.0.0:9247` → `127.0.0.1:19247` только на хосте |
| Telegram bot в Docker | `CURSOR_BRIDGE_BASE_URL` → `host.docker.internal:9247` + `CURSOR_BRIDGE_AUTH_TOKEN` |
| Секреты | `data/cursor_bridge.env` в `.gitignore`, `chmod 600` при записи |

## Проверка (без запуска bridge)

```bash
make cursor-bridge-drill
# или: pytest -q tests/test_cursor_bridge_policy.py
```

Скрипт `scripts/cursor_bridge_drill.sh`: policy-тесты + при запущенном bridge на `127.0.0.1:19247` — HTTP probe (без вывода токена).

## Ручная проверка на staging

1. `./scripts/docker-up.sh` или `python scripts/run_cursor_bridge.py`
2. Убедиться, что `ss -lntp | grep 19247` слушает **127.0.0.1**
3. С хоста без токена: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:19247/` — не должен отдавать агент без auth (ожидается отказ SDK)
4. Из контейнера `telegram-bot`: лог `Connecting to host Cursor bridge from Docker` при старте с валидным env  
5. Автоматический probe: `make cursor-bridge-container` (curl к `host.docker.internal:9247` из compose)

## Риски

- Публикация `19247` на `0.0.0.0` на хосте **запрещена** — используйте defaults скрипта.
- Не коммитить `CURSOR_BRIDGE_AUTH_TOKEN` в `.env` репозитория; только auto-generated env file.
