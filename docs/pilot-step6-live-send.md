# Шаг 6 — первый live send (пилот)

> **01.11:** stack up, `PLATFORM_DRY_RUN=true`. **Блокер:** Kwork отвечает «Доступ заблокирован» с этого IP (listing и project URL) — см. `scripts/ops_kwork_access_probe.py`. Без восстановления доступа live send и scrape не выполняются. Прогресс — [remediation-progress.md](remediation-progress.md).

Предусловия (уже выполнены в пилотной подготовке):

- Backup: `data/backups/jobpilot_ai_20261030_pre_pilot.sql.gz` (+ SHA в `data/backups/README.md`)
- Schema **0007**, `production_rollout.sh` + smoke OK
- `API_AUTH_TOKEN` в `.env`, stack compose up
- **`PLATFORM_DRY_RUN=true`** по умолчанию до явного live (01.11 восстановлено после `false`)

Сводка pending: `DATABASE_URL=.../jobpilot_ai .venv/bin/python scripts/ops_pending_cards.py`

## A. Закрыть шаг 5 (вы в Telegram) — **выполнено 30.10**

1. Карточка **«Повторный review»** (message **584**) — **APPROVE** нажат.
2. Проверено: `send_attempt` **`confirmed_sent`**, `platform_response.dry_run=true`; live offer на Kwork **не** создавался.

Проверка:

```bash
DATABASE_URL='postgresql+asyncpg://jobpilot:jobpilot_secret@127.0.0.1:5434/jobpilot_ai' \
  .venv/bin/python scripts/ops_verify_dry_run_approve.py 60e079ce-bf4e-4af1-8215-1a8d88cc2b3e
```

```bash
DATABASE_URL='postgresql+asyncpg://jobpilot:jobpilot_secret@127.0.0.1:5434/jobpilot_ai' \
  .venv/bin/python scripts/ops_pilot_status.py
```

## B. Перед live (шаг 6 — human gate C109/C110)

Автоматическая сводка блокеров (ничего не меняет в `.env`):

```bash
DATABASE_URL='postgresql+asyncpg://jobpilot:jobpilot_secret@127.0.0.1:5434/jobpilot_ai' \
  .venv/bin/python scripts/ops_step6_preflight.py
```

Проверка доступа Kwork (Playwright, без изменений env):

```bash
.venv/bin/python scripts/ops_kwork_access_probe.py
```

Ожидаемые blockers до вашего go: `live_signoff_not_valid`, при IP-блоке — probe exit **1**.

1. Выберите **один** тестовый заказ Kwork (низкий риск).
2. Проверить фактическое состояние паузы и коннектов; не снимать и не обходить паузу без отдельного разрешения.
3. На **staging или осознанно на prod** один раз:
   - `PLATFORM_DRY_RUN=false` только на время одной отправки;
   - пройти UI Kwork глазами;
   - заполнить `data/live_kwork_signoff.json` по `data/live_kwork_signoff.json.example`;
   - `python scripts/validate_live_signoff.py` — должен пройти только с **реальными** attestation полями;
   - `./scripts/record_live_kwork_acceptance.sh ...` по [owner-live-checklist-one-pager.md](owner-live-checklist-one-pager.md).

4. Мониторинг: `unknown_result`, `needs_attention` в Telegram; при сомнении — снова **`PLATFORM_DRY_RUN=true`**, не жать Approve повторно вслепую.

## C. Stop / rollback (шаг 7)

- Вернуть `PLATFORM_DRY_RUN=true`, остановить workers при необходимости.
- Восстановление БД **только** из backup на **изолированном** сервере; не `DROP` рабочей БД без плана.
- Открытые критерии (**64/115**) остаются — live send не означает «релиз принят».

См. также [kwork-live-acceptance.md](kwork-live-acceptance.md), [runbook.md](runbook.md).
