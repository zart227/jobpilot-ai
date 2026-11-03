# Закрытие remediation (остаток)

Автоматизируемая часть remediation **закрыта**. [Чеклисты](remediation-checklists.md): **115/115** (14.10.2026, signoff dry-run acceptance + chat authorization).

## Сводка секций

Актуальная таблица (генерируется из чеклиста):

```bash
make checklist-report
# или: python scripts/remediation_checklist_report.py
```

На **14.10.2026**: **115 закрыто, 0 open**. Сквозная приёмка закрыта signoff: order `accept-8b780b5a`, attempt `77324cab-24cc-434e-b080-b36abee06f12` (см. журнал в чеклистах). Это **dry-run acceptance**, не проверка UI Kwork.

**Что осталось после закрытия чеклиста** (не реализовано / частично / исправить / backlog): [remediation-progress.md § «Остаток работ»](remediation-progress.md#остаток-работ-честная-сводка).

Краткий путь владельца: [live-kwork-prep.md](live-kwork-prep.md) · [owner-live-checklist-one-pager.md](owner-live-checklist-one-pager.md).

Проверка: `make verify` · gate: `make checklist-gate` (в CI).  
**Полный автономный прогон:** `make autonomous` — см. [autonomous-remediation-run.md](autonomous-remediation-run.md).

## Перед live Kwork

```bash
./scripts/remediation_verify.sh
# при реальной проверке:
REQUIRE_LIVE_KWORK=1 PLATFORM_DRY_RUN=false ./scripts/live_acceptance_preflight.sh
python scripts/validate_live_signoff.py   # после заполнения data/live_kwork_signoff.json
```

Чеклист сценария: [kwork-live-acceptance.md](kwork-live-acceptance.md).

## После успешной live-проверки

1. `./scripts/record_live_kwork_acceptance.sh <order_id> <send_attempt_uuid> "итог"` — журнал.
2. `./scripts/close_live_kwork_remediation.sh` (или по шагам: `record_*` + `mark_live_checklist_complete.sh`).
3. `make checklist-report` — ожидается **0 open**.
