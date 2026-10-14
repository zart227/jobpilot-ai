# Сквозная приёмка (dry-run)

Цель: проверить цепочку **без** реальных сообщений Kwork, без расхода коннектов и без платных LLM-вызовов в сценарии отправки.

## Переключатель

```env
PLATFORM_DRY_RUN=true
```

- `ProposalSender` помечает отправку успешной и пишет `send_attempts` → `confirmed_sent`, proposal → `sent`.
- `send_kwork_inbox_reply` не вызывает Kwork API.
- Telegram/LLM в pipeline **не** отключаются — для полного E2E без LLM используйте моки в тестах или staging с отключённым beat.

## Автоматические проверки

При `JOBPILOT_INTEGRATION_TESTS=1` (CI job `integration`):

```bash
pytest -q tests/test_acceptance_dry_run.py
```

Покрывает:

- approve → dry-run send → `confirmed_sent`
- повторный `hired` outcome → один reward (idempotency)
- inbound dedup → draft/edit → dry-run inbox reply (`test_acceptance_inbox_flow.py`)

## Ручной smoke

```bash
./scripts/readiness_smoke.sh
./scripts/backup_postgres.sh ./backups
JOBPILOT_DRILL_DB=jobpilot_ai_drill ./scripts/restore_postgres_drill.sh backups/<file>.sql.gz
```

## Полный автоматический gate

```bash
./scripts/remediation_verify.sh
```

## Не входит в dry-run (требует staging / разрешения)

- Реальный Kwork approve → отклик на площадке ([closeout](remediation-closeout.md))
- Inbound с живого inbox + Telegram timeout
- Рестарт посередине полного цикла (см. runbook для ручной проверки)
