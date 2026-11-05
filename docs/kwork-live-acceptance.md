# Разрешённая проверка на Kwork (чеклист)

> **05.11.2026:** это не отдельный план. Счёт и критерии — [remediation-plan.md](remediation-plan.md) (**98/115**, **17** открыто). Dry-run не закрывает live.

Использовать **только** после закрытия dry-run в CI и явного решения владельца проекта.

Пункты ниже — живая проверка UI для **C110** в [remediation-plan.md](remediation-plan.md). Они не добавляют критерии и не заменяют план.

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
./scripts/record_live_kwork_acceptance.sh        # validate only (R15)
./scripts/mark_live_checklist_complete.sh        # после validate_live_signoff.py OK; не ставит [x]
```

- [ ] `send_attempts.status = confirmed_sent` совпадает с UI Kwork
- [ ] `snapshot_content` совпадает с отправленным текстом
- [ ] Повтор approve/callback **не** создаёт вторую отправку
- [ ] **Вручную** `[x]` только **C110** в [remediation-plan.md](remediation-plan.md) после совпадения UI с snapshot (дата, заказ, результат)

## Откат

Отключить beat, `PLATFORM_DRY_RUN=true`, зафиксировать unknown_result по runbook.
