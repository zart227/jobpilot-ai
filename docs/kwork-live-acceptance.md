# Разрешённая проверка на Kwork (чеклист)

> Перепроверка14.10.2026: **44/115 подтверждено,71 открыт; приёмка не завершена.** Актуальные [аудит](audit-2026-10-14.md) и [план](remediation-plan.md) имеют приоритет. Dry-run не закрывает live. Auto-close/restore/rollout helpers требуют исправлений R15/R16 до использования на действующей системе.

Использовать **только** после закрытия dry-run в CI и явного решения владельца проекта.

[remediation-checklists.md](remediation-checklists.md) **44/115 подтверждено; 71 открыт** (dry-run signoff). Пункты `- [ ]` ниже — **живая** проверка в UI; что ещё не сделано в коде/опсе: [remediation-progress.md § Остаток работ](remediation-progress.md).

## Перед стартом

Автоматические ворота (без реального Kwork): `./scripts/remediation_verify.sh` или `./scripts/live_acceptance_preflight.sh`

Файл подписи (после live): скопировать `data/live_kwork_signoff.json.example` → `data/live_kwork_signoff.json`, затем `python scripts/validate_live_signoff.py`

- [ ] `PLATFORM_DRY_RUN=false` только на staging/тестовом окружении
- [ ] Выбран **тестовый заказ** и **тестовый диалог** (не прод-клиент без согласия)
- [ ] Лимиты коннектов и LLM проверены (`/dev_status`, usage alerts)
- [ ] Beat/worker можно остановить за 1 команду (см. runbook)

## Сценарий

1. Scrape или ручной job → filter → score → proposal → Telegram approve
2. Реальная отправка → сверка на Kwork UI
3. Inbound сообщение → context → draft → edit → reply
4. Outcome `hired` / `replied` → один reward → memory (если embeddings включены)

## После проверки

```bash
./scripts/record_live_kwork_acceptance.sh <order_id> <send_attempt_uuid> "краткий итог"
./scripts/mark_live_checklist_complete.sh   # после validate_live_signoff.py OK
```

- [ ] `send_attempts.status = confirmed_sent` совпадает с UI Kwork
- [ ] `snapshot_content` совпадает с отправленным текстом
- [ ] Повтор approve/callback **не** создаёт вторую отправку
- [ ] Запись в [remediation-checklists.md](remediation-checklists.md) § сквозная приёмка (дата, заказ, результат)

## Откат

Отключить beat, `PLATFORM_DRY_RUN=true`, зафиксировать unknown_result по runbook.
