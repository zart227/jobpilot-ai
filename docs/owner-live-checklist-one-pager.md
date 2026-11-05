# Live Kwork — один лист для владельца

> **05.11.2026:** это не отдельный план. Счёт и критерии — [remediation-plan.md](remediation-plan.md) (**98/115**, **17** открыто). C110 закрывается только совпадением UI с snapshot после отдельного разрешения. Dry-run не закрывает live.

Ниже — проверка в UI Kwork, когда до неё дойдёт очередь плана. Preflight: **`make ops-rollout-readiness`** + [kwork-live-acceptance.md](kwork-live-acceptance.md).

## До отправки

```bash
make verify                    # или ./scripts/remediation_verify.sh
make -s ops-rollout-readiness  # working jobpilot_ai, /ready, alembic head, stats NA
DATABASE_URL=…/jobpilot_ai .venv/bin/python scripts/ops_step6_preflight.py   # ожидаемо: live_signoff_not_valid
docker compose exec celery-worker python scripts/ops_kwork_access_probe.py     # ok + browser_auth_verified
# в .env: PLATFORM_DRY_RUN=false только на staging и после явного разрешения владельца
```

На working (03.11): **10** pending Telegram cards — выберите **один** тестовый Kwork заказ **без** вашего отклика; **`ops_pilot_status.py`** для сводки.

Чеклист сценария: [kwork-live-acceptance.md](kwork-live-acceptance.md).

## После проверки на Kwork

```bash
cp data/live_kwork_signoff.json.example data/live_kwork_signoff.json
# заполнить human attestation + артефакты (см. block-a-access-and-evidence.md)
python scripts/validate_live_signoff.py          # exit 0 только при реальном evidence
./scripts/record_live_kwork_acceptance.sh        # повторная validate; чеклист не меняет
./scripts/mark_live_checklist_complete.sh        # validate; чеклист не меняет (R15)
./scripts/close_live_kwork_remediation.sh        # validate + REMEDIATION_MAX_OPEN=0 gate
# вручную: [x] только C110 в docs/remediation-plan.md после совпадения UI и snapshot + запись в remediation-progress.md
make checklist-report          # после ручного [x] ожидается 115/0; до этого **2 open**
```

## Что проверить глазами

- [ ] UI Kwork = текст в `send_attempts.snapshot_content`
- [ ] `status = confirmed_sent` один раз; повтор approve не шлёт снова
- [ ] Inbox reply (если гоняли) — тот же attempt/snapshot

Подробности live-шагов не заменяют критерии: [remediation-plan.md](remediation-plan.md).
