# Live Kwork — один лист для владельца

> Перепроверка14.10.2026: **44/115 подтверждено,71 открыт; приёмка не завершена.** Актуальные [аудит](audit-2026-10-14.md) и [план](remediation-plan.md) имеют приоритет. Dry-run не закрывает live. Auto-close/restore/rollout helpers требуют исправлений R15/R16 до использования на действующей системе.

[remediation-checklists.md](remediation-checklists.md): **44/115 подтверждено; 71 открыт** (dry-run signoff уже в журнале). Ниже — **отдельная** проверка в UI Kwork перед продом; backlog: [remediation-progress.md § Остаток работ](remediation-progress.md).

## До отправки

```bash
make verify                    # или ./scripts/remediation_verify.sh
# в .env: PLATFORM_DRY_RUN=false только на staging
```

Чеклист сценария: [kwork-live-acceptance.md](kwork-live-acceptance.md).

## После проверки на Kwork

```bash
cp data/live_kwork_signoff.json.example data/live_kwork_signoff.json
# отредактировать JSON (order_id, send_attempt_id, флаги true)
python scripts/validate_live_signoff.py
./scripts/record_live_kwork_acceptance.sh <order_id> <attempt_uuid> "краткий итог"
./scripts/mark_live_checklist_complete.sh --dry-run   # посмотреть diff
./scripts/close_live_kwork_remediation.sh           # journal + [x] + final gate
# или вручную: mark_live_checklist_complete.sh && REMEDIATION_MAX_OPEN=0 python scripts/remediation_checklist_status.py
make checklist-report          # ожидается 71 open; release gate nonzero
```

## Что проверить глазами

- [ ] UI Kwork = текст в `send_attempts.snapshot_content`
- [ ] `status = confirmed_sent` один раз; повтор approve не шлёт снова
- [ ] Inbox reply (если гоняли) — тот же attempt/snapshot

Подробности: [remediation-closeout.md](remediation-closeout.md).
