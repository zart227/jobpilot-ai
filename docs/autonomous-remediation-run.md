# Автономный прогон remediation (без участия владельца)

Одна команда для всего, что можно проверить локально **без** реального Kwork UI:

```bash
make finish-owner   # backup + signoff + drills + autonomous (максимум без браузера Kwork)
make autonomous
# или: ./scripts/autonomous_remediation_run.sh
```

Если в терминале Cursor висят старые `export` (5433, битый JSON):  
`source ~/.config/jobpilot/clear-stale-exports.sh` перед прогоном.

## Что выполняется

| Шаг | Содержание |
| --- | ---------- |
| Ruff | `ruff check app tests scripts` |
| Checklist gate | `REMEDIATION_MAX_OPEN=0`, 115/115 |
| Unit verify | `remediation_verify.sh` (без integration в shell) |
| Integration DB | `bootstrap_integration_db.sh` → `jobpilot_ai_test` на compose **5434**, Redis DB **2–3** |
| Full pytest | `176` тестов с `JOBPILOT_INTEGRATION_TESTS=1` |
| Audit matrix | `docs/audit-remediation-matrix.md` |
| Staging drills | audit, preflight, qdrant dry-run, cursor policy, container probe |
| Signoff | `validate_live_signoff.py` если есть `data/live_kwork_signoff.json` |
| Rollout | preflight + `alembic upgrade head` + readiness smoke |

Файл окружения для integration (не коммитить): `.jobpilot_integration_test.env`

## Что **не** автоматизируется

- Отправка в **live Kwork** (`PLATFORM_DRY_RUN=false`) и сверка UI
- Повтор drills на отдельном **staging** хосте
- `git commit` / push (по политике репозитория — только по запросу владельца)
- Удаление битых `export` из `~/.zshrc` (скрипты сами сбрасывают их на время прогона)

## После прогона (14.10.2026)

Лог: `make autonomous` exit 0 · pytest **176 passed** · checklist **0 open** · signoff OK (dry-run).
