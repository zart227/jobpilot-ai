# Outbox `needs_attention` — операторская сводка

Read-only инвентаризация **не отправляет** Telegram и **не меняет** статусы.

## Когда использовать

- После timeout/lease/partial delivery outbox остаётся в `needs_attention` **без автоматического повтора** (см. [inbox-outbox-policy.md](inbox-outbox-policy.md)).
- Отдельно от inbound generation exhausted: для последнего случая используется kind **`ops_alert`** и [operator-inbox-generation-recovery.md](operator-inbox-generation-recovery.md).

## Команда

```bash
export DATABASE_URL='postgresql+asyncpg://…/jobpilot_ai'   # из .env (127.0.0.1:5434); при `JOBPILOT_WORKING_DATABASE_URL=@postgres:` host-side `make` выберет localhost через `scripts/ops_database_url.py`
PYTHONPATH=. .venv/bin/python scripts/ops_working_db_preflight.py
make -s ops-outbox-triage    # JSON rollup: P1/P2/inbox counts
make -s ops-outbox-triage-md > /tmp/outbox-triage-bundle.md   # full markdown bundle
PYTHONPATH=. .venv/bin/python scripts/ops_notification_outbox_inventory.py --pending-limit 50
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_inbox_na_checklist.py
```

Markdown-чеклист (read-only): `ops_outbox_triage_checklist.py` — те же группы, что `needs_attention_triage_groups`, в виде `- [ ]` для оператора.

Приоритетная очередь (P1 inactive orphan NA → P2 active pending NA → P3 timeout groups):

```bash
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_triage_priority.py
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_p1_timeout_checklist.py   # default --error-bucket timeout
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_p1_timeout_checklist.py --error-bucket other
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_p2_active_pending_checklist.py
```

Явный requeue одной **безопасной** строки (не вызывает Telegram сам; доставку выполняет beat/worker):

```bash
export DATABASE_URL="$JOBPILOT_WORKING_DATABASE_URL"
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_recovery.py
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_recovery.py --id '<uuid>' --apply
PYTHONPATH=. .venv/bin/python scripts/ops_outbox_recovery.py --id '<uuid>' --apply --ack-ambiguous-lease
```

Поля отчёта:

- `by_kind_status` — агрегаты по kind/status
- `needs_attention_totals_by_kind` — полные счётчики `needs_attention` (не только sample)
- `needs_attention_totals_by_error_bucket` — полная разбивка по bucket на всех `needs_attention`
- `needs_attention_by_reference_type` — полные счётчики по `reference_type` (working **03.11**: `telegram_pending` **20**, `telegram_inbox_pending` **13**; всего **33**)
- `needs_attention_sample_by_error_bucket` — buckets только в sample (до `--pending-limit`)
- `needs_attention_total` — число всех строк `needs_attention`
- `needs_attention_triage_groups` — группы `{kind, error_bucket}` с id/reference для чеклиста (без payload)
- `pending_ops_alert` — ожидающие operator alerts (generation exhausted) в `ops_notification_outbox_inventory.py`
- `outbox_needs_attention_sample` — последние `needs_attention` (id, kind, preview `last_error`, `error_bucket`)

Crosscheck (`ops_pending_outbox_crosscheck.py`): `inactive_needs_attention_by_error_bucket` (working **02.11**: timeout **10**, other **5**), `inactive_needs_attention_by_kind`.

## Безопасные действия

1. Сверить `telegram_message_id` и текст ошибки в Telegram UI (карточка могла быть доставлена частично).
2. Для proposal/inbox approval — [operator-review-reissue.md](operator-review-reissue.md) / ручной reissue, **не** повторная отправка клиенту Kwork без reconcile.
3. Связка pending ↔ outbox: `scripts/ops_pending_outbox_crosscheck.py` (read-only).
4. После сверки UI — `ops_outbox_recovery.py --apply` для eligible `ops_alert` / full-text review rows; legacy cards и partial delivery → [operator-review-reissue.md](operator-review-reissue.md), не blind resend.
5. Не массово сбрасывать `needs_attention` в `pending` без понимания риска duplicate Telegram.

## Связанные метрики

`GET /stats` → `notification_outbox.needs_attention`, `inbound_messages.needs_attention`.
