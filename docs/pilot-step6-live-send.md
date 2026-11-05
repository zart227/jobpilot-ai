# Шаг 6 — первый live send (пилот)

> **03.11:** stack up, **`PLATFORM_DRY_RUN=true`** в runtime (compose). Перед live: **`ops_step6_preflight.py`** — blocker **`live_signoff_not_valid`** до human signoff; listing/session — **`ops_kwork_access_probe.py`**. Старый IP-block на listing мог быть временным — перепроверять probe. Прогресс — [remediation-progress.md](remediation-progress.md).

Предусловия (уже выполнены в пилотной подготовке):

- Backup: `data/backups/jobpilot_ai_20261030_pre_pilot.sql.gz` (+ SHA в `data/backups/README.md`)
- Alembic head **`20261103_0011`** on working DB (`alembic current` / `ops_rollout_readiness`); historical note: backup at schema **0007**, `production_rollout.sh` + smoke OK
- `API_AUTH_TOKEN` в `.env`, stack compose up
- **`PLATFORM_DRY_RUN=true`** по умолчанию до явного live (01.11 восстановлено после `false`)

Сводка pending: `DATABASE_URL=.../jobpilot_ai .venv/bin/python scripts/ops_pending_cards.py` (или `ops_pilot_status.py` — строки **review-ready** / **assessment_stale**).

**03.11 (sync):** working — **10** pending, **0** review-ready, **3** с `assessment_stale` (Kwork **3271087**, **3270996**, **3270919** — snapshot есть, нужны fresh scrape → reassess → `/rereview` перед live APPROVE). **`ops_step6_preflight`:** только **`live_signoff_not_valid`**; probe **`browser_auth_verified=true`**; **`unknown_result=0`**; **`confirmed_sent` dry_run=false = 0**.

**03.11 (later) — C109 prep на working:** добавлены `ops_refresh_kwork_job_listing.py`, `ops_probe_kwork_projects.py`; refresh listing + reassess для **3271087** → gate **pass**; reissue → pending **`3f654c1b…`**, Telegram **msg 794**, **`review_prerequisites_present=true`**. **3270996** после reassess → **rejected** (старая карточка **4c70…** не review-ready). **3271087:** probe ранее **propose_button_count=0** — перед live сверить UI Kwork (возможен уже существующий отклик). **3270996/3270766:** probe **new_offer_likely**; для **3270766** нет свежей Telegram-карточки.

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

Перед live: **`docker compose exec celery-worker python scripts/ops_kwork_access_probe.py`** → **`ok` + `browser_auth_verified=true`** (probe пробует direct, как send path).

Ожидаемые blockers до вашего go: `live_signoff_not_valid`; при IP-блоке listing — probe exit **1**; при отсутствии сессии — `browser_auth_verified=false` в JSON probe.

1. Выберите **один** тестовый заказ Kwork (низкий риск).
2. Проверить фактическое состояние паузы и коннектов; не снимать и не обходить паузу без отдельного разрешения.
3. На **staging или осознанно на prod** один раз:
   - `PLATFORM_DRY_RUN=false` только на время одной отправки;
   - пройти UI Kwork глазами;
   - заполнить `data/live_kwork_signoff.json` по `data/live_kwork_signoff.json.example`;
   - `python scripts/validate_live_signoff.py` — должен пройти только с **реальными** attestation полями;
   - `./scripts/record_live_kwork_acceptance.sh` — только validate (R15); затем ручной [x] в чеклисте по [owner-live-checklist-one-pager.md](owner-live-checklist-one-pager.md).

4. Мониторинг: `unknown_result`, `needs_attention` в Telegram; при сомнении — снова **`PLATFORM_DRY_RUN=true`**, не жать Approve повторно вслепую.

## C. Stop / rollback (шаг 7)

- Вернуть `PLATFORM_DRY_RUN=true`, остановить workers при необходимости.
- Восстановление БД **только** из backup на **изолированном** сервере; не `DROP` рабочей БД без плана.
- Открытые критерии (**2/115 — C109/C110**) остаются — live send не означает «релиз принят» без signoff и ручного [x].

См. также [kwork-live-acceptance.md](kwork-live-acceptance.md), [runbook.md](runbook.md).
