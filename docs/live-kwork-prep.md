# Live Kwork — подготовка (единый чеклист)

> **05.11.2026:** счёт только в [remediation-plan.md](remediation-plan.md) (**98/115**, **17** открыто). Этот лист не закрывает критерии. Dry-run не закрывает live.

Remediation-чеклист: открыты **C109/C110**. Этот документ — **перед первой реальной отправкой** в UI Kwork.

## 1. Автоматические ворота

```bash
make verify
make -s ops-rollout-readiness
make audit-status
./scripts/staging_drill_automated.sh   # опционально, лог в staging-drill-log.md
```

## 2. Окружение

- [ ] Staging или тестовый заказ (не прод-клиент без согласия)
- [ ] `PLATFORM_DRY_RUN=false` только на этом окружении
- [ ] `API_AUTH_TOKEN` + `API_AUTH_REQUIRED=true` если API доступен из сети
- [ ] Workers/beat можно остановить: `docker compose stop celery-beat celery-worker` (см. runbook)

Подробно: [kwork-live-acceptance.md](kwork-live-acceptance.md).

## 3. Прогон

1. Отправка отклика на тестовый заказ (один раз).
2. Сверка UI Kwork ↔ `send_attempts.snapshot_content`, `status=confirmed_sent`.
3. Повтор approve/callback — **не** должна слать снова.
4. (Опционально) inbox reply — тот же attempt/snapshot.

Глазами: [owner-live-checklist-one-pager.md](owner-live-checklist-one-pager.md).

## 4. После успеха

```bash
cp data/live_kwork_signoff.json.example data/live_kwork_signoff.json
# заполнить attestation + artifacts; PLATFORM_DRY_RUN=false только на время одной отправки
python scripts/validate_live_signoff.py
./scripts/record_live_kwork_acceptance.sh
```

Журнал опционален (чеклист не закрыт); для аудита продакшена — рекомендуется.

## 5. Откат

- Вернуть `PLATFORM_DRY_RUN=true`
- Runbook: [runbook.md](runbook.md) § unknown_result, outbox

Backlog: [remediation-progress.md](remediation-progress.md).
