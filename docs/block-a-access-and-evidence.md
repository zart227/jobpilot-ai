# Блок A: доступ и достоверная приёмка — 15 октября 2026

Реализованы изменения R01/R02/R15 из [плана](remediation-plan.md). Это первый набор исправлений, не завершение всего плана.

## API

Сетевой API требует токен даже при старом `API_AUTH_REQUIRED=false`. Пока `API_AUTH_TOKEN` пуст, data/control routes возвращают503; с неверным токеном —401. `/health`, `/ready` и документация остаются публичными. Проверено на всех9 data/control маршрутах через ASGI tests; работающий GET `/jobs` без токена теперь503.

Локальный режим без токена включается только при одновременном выполнении:

- `APP_ENV=development`;
- `API_AUTH_REQUIRED=false`;
- `API_ALLOW_UNAUTHENTICATED_LOCAL=true`;
- `API_HOST=127.0.0.1` или `::1`;
- фактический peer запроса — literal loopback;
- токен не настроен.

За reverse proxy локальный peer не доказывает локальность конечного пользователя: этот режим предназначен только для отдельного локального процесса без проксирования. Для Docker и любого proxy-развёртывания нужен токен. Поле API_HOST не заменяет фактический bind uvicorn; запуск обязан соответствовать конфигурации.

Токен задаёт владелец в `.env`/secret store; текущие секреты в рамках исправления не менялись. Не передавать токен в URL и не вставлять его в документацию/отчёты.

## Квоты

Для авторизованных запросов bucket вычисляется из проверенного токена, независимо от IP, способа передачи и неиспользуемого заголовка. Неправильные credentials не создают отдельную identity: анонимная квота привязана к peer IP. Это отдельные buckets для authenticated/anonymous. Один администратор использует одну квоту; API пока не multi-user.

Redis Lua атомарно выполняет INCR+TTL/EXPIRE. По умолчанию ошибка Redis блокирует запрос503 (`API_RATE_LIMIT_FAIL_OPEN=false`). Явно заданное старое true продолжит включать fail-open — проверить при следующем deployment. Квота HTTP не является лимитом очереди LLM/стоимости: это остаётся в блоке D/G.

## Telegram

В группе обязательны разрешённый chat ID и положительный admin user ID. При пустом/некорректном user ID доступ закрыт. В личном чате при пустом user ID допускается только владелец (`from_user.id == chat.id`). Middleware применяется до маршрутизации Update; отдельно проверен отказ до запуска /dev/FSM handler.

Callback без записанного message ID отклоняется. Version/hash binding старой карточки всё ещё требует блока B, C012 остаётся открытым. Запущенный Telegram process автоматически не перезагружается: новый guard нужно применить согласованным restart/release; в этой сессии restart не выполнялся.

## Simulation и live evidence

`scripts/generate_acceptance_signoff.py` теперь:

- требует явно изолированную *_test БД и `JOBPILOT_INTEGRATION_TESTS=1` до подключения;
- принудительно включает dry-run, даже если shell передал false;
- пишет только `data/simulation_signoff.json`, с `mode=simulation`, `ui_matches_db=false`;
- не назначает approved_by и не заявляет разрешение человека.

Live signoff использует schema_version2 из `data/live_kwork_signoff.json.example`. Его заполняет владелец после отдельно разрешённой реальной проверки. Нужны ссылки на human authorization и UI review, timezone timestamps, реальный numeric order ID, UUID attempt и SHA256 snapshot. UI evidence и JSON attempt evidence — файлы внутри каталога signoff с проверяемыми SHA256; traversal/подмена файлов запрещены.

Минимальный attempt artifact содержит send_attempt_id, kwork_order_id, snapshot_sha256, mode=live, status=confirmed_sent, dry_run=false. JSON artifact — сохранённый результат read-only сверки, а не возможность объявить симуляцию live.

Команды после разрешённой live-проверки:

```bash
# Только валидация artifacts: не проверяет БД, не даёт live acceptance.
.venv/bin/python scripts/validate_live_signoff.py --artifacts-only
# Дополнительно read-only проверяет существующий attempt/job/snapshot в настроенной БД.
.venv/bin/python scripts/validate_live_signoff.py
```

Полная проверка сверяет proposal attempt с confirmed_sent, отсутствие dry_run marker, площадку Kwork, точный order ID и snapshot hash. Evidence остаётся human attestation: валидатор не может доказать подлинность скриншота или полномочия автора текстовой ссылки. Поэтому scripts никогда автоматически не закрывают C109/C110 и не выдают release approval.

`mark_live_checklist_complete.sh`, `record_live_kwork_acceptance.sh`, `close_live_kwork_remediation.sh` больше не переписывают галочки. `finish_owner_checklist_automated.sh` останавливается до side effects: автоматически завершить owner/live-приёмку нельзя.

## Остаток

Runtime auth token и согласованный deployment Telegram, bridge/network review, versioned approval, workload limits, весь блок B–H и настоящая live-приёмка остаются открытыми. Исторический аудит14.10 сохранён; текущие изменения фиксируются в progress/evidence, а не задним числом как будто были сделаны при аудите.
