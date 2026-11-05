# Статус приёмки remediation — 14 октября 2026

**Не закрыта: 113/115 подтверждено, 2 открыто (C109/C110). Release signoff отсутствует.** (см. [чеклисты](remediation-checklists.md))

Прежнее115/115 отменено повторной проверкой: partial/unrun/live criteria были закрыты симуляцией и наличием artifacts. История сохранена [в архиве](archive/2026-10-14-before-reaudit/remediation-closeout.md).

Источники: [аудит](audit-2026-10-14.md), [план](remediation-plan.md), [чеклисты](remediation-checklists.md), [progress](remediation-progress.md).

`make checklist-report` — информационный подсчёт, не приёмка. `REMEDIATION_MAX_OPEN=0 make checklist-gate` сейчас обязан завершиться nonzero. Это ожидаемый запрет release при открытых критериях; tests инвентаря проверяют достоверность подсчёта и формат.

До live: исправить P1, пройти isolated full-story и staging проверки. Для live получить отдельное разрешение на конкретный заказ/действие; запись dry-run не доказывает такого разрешения и не подтверждает UI.

**Auto-close/finish-owner не дают подтверждения готовности.** После исправления15.10 mark/record/close проверяют evidence без изменения галочек; finish-owner отключён. Runtime auth guard подхвачен API dev reload, но конфигурация токена, Telegram deployment, отправки и migrations не менялись. Restore/rollout safeguards R16 ещё не исправлены.

15.10: auto-close механизмы исправлены и больше не переписывают критерии; validator требует v2 evidence+DB. Это исправление R15 не означает live acceptance. См. [блок A](block-a-access-and-evidence.md).

15.10 B2.1: preview message/chat/hash binding и inbox edited-send исправлены;239 isolated tests passed. Остаток B2 — durable approval snapshot и обычные versioned approve-карточки, frozen payload/price/duration и in-flight edit/regenerate guard.50/115 подтверждено,65 открыто; deployment не выполнен.

15.10 B2.2: Telegram edit/regenerate защищены от незавершённой доставки; stale regeneration не перезаписывает новую версию.249 isolated tests passed. Остаются durable approval snapshot/frozen payload и проверка остальных write paths.50/115 подтверждено,65 открыто. Deployment не выполнен.

15.10 D1: pipeline persist/release/error/recovery fencing исправлен в описанной области;256 full isolated tests +10 targeted после финальной правки. C053 частично, ещё открыта lease/terminal/freshness приёмка. Следом продолжить durable approval B и остальные write paths.50/115,65 открыто; deployment не выполнен.

15.10 B2.3: inbox maintenance больше не меняет draft под прежней карточкой: supersede+new pending+outbox атомарны, stale/approved/sending защищены.268 full +11 targeted tests прошли. Следующий шаг — durable approval snapshot/frozen payload.50/115,65 открыто; deployment не выполнен.

15.10 B2.4: durable presented proposal snapshot + migration0005 + stale ordinary Approve/Edit guard.276 full tests passed; isolated0004→0005 и DB immutable update проверены. Рабочая БД0005 НЕ применена; нужна до deployment notify/approval кода. Следующий шаг — human approval event и обязательный sender enforcement exact payload/price/duration, edit/inbox/resubmit.50/115,65 открыто.

15.10 B2.5: immutable proposal approval event (actor/chat/message/payload) и обязательный sender event match реализованы, ordinary+edited callbacks подключены.284 full +10 targeted tests passed; isolated0005→0006 проверена. До deployment нужен0006, рабочая БД не мигрирована. Далее — frozen actual form text/price/duration, inbox durable approval и resubmit policy.50/115,65 открыто.

15.10 B2.6: exact text/price/duration передаются до browser; padding/recompute/fallback и auto-edit existing offer удалены. Pre-submit DOM readback fail-closed.295 full +13 targeted passed. C041/C051 ещё открыты до HTML/staging/отдельного edit-existing approval; далее durable inbox approval и полная UI review.50/115,65 открыто; рабочая БД не мигрирована, для deployment нужен0006.

15.10 B2.7: durable inbox presentation/event и exact sender enforcement, DB callback tests,308 full +11 targeted passed. C042 закрыт для кода;51/115,64 открыто. Для deployment нужен0007; рабочая БД не мигрирована. Далее full-text UI review, thread selector, HTML/staging form и explicit edit-existing approval.

16.10 B2.8: full exact plain-text review proposal/inbox/edits, UTF-16 split, controls after all parts; old truncated formats invalidated, stale Skip guarded.316 full +11 targeted passed.51/115,64 открыто; C012 ещё partial. Далее browser HTML/readback и remaining callback/FSM/operator review. Схема без изменений сверх0007, рабочая БД не мигрирована, deployment не выполнен.

16.10 B2.9: synthetic offline DOM/Chromium контракт:6 исходных регрессий исправлены,30 targeted passed;316 full isolated tests passed, browser checks отдельно opt-in. CI browser job настроен, remote CI не подтверждён. C041 остаётся partial до настоящей HTML/staging, других form params и receipt; далее form params/operator re-review.51/115,64 открыто; рабочая БД не мигрирована, deployment не выполнен.

Финальная проверка B2.9 после всех изменений: **333 passed,42.89s,без skips** в одном запуске с JOBPILOT_INTEGRATION_TESTS=1 и JOBPILOT_BROWSER_TESTS=1, включая17 DOM tests. Отдельный browser+unit run:30 passed,19.85s. Промежуточный316/15skip выше — история до финального combined run, не итог. Gate отчётности проверен5 tests passed.51/115 подтверждено,64 открыто.

16.10 B2.10: /rereview reply/UUID и atomic/idempotent pending+presentation+outbox, strict delivery guards и FSM cleanup реализованы.376 unified full +10 final targeted passed.51/115,64 открыто; C111/C112 partial. Operator legacy re-review теперь есть, но all callback/FSM, полная queue/unknown resolution, form params/staging и edit-existing ещё открыты. До deployment нужен0007, рабочие процессы не перезапускались.
