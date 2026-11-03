# Audit → remediation matrix — historical findings as of 2026-10-30

Reviewed source: [audit-2026-10-14.md](audit-2026-10-14.md); historical baseline: [audit-2026-10-13.md](audit-2026-10-13.md).

Historical finding statuses below are dated and may be stale; see [current audit](audit-2026-11-03-completion.md) and the checklist for current work. Rendering does not rerun tests or approve release.

| # | P | Статус | Находка | Остаток / evidence |
| - | - | ------ | ------- | ----------------- |
| 1 | P1 | partial | Без защиты API и открытые порты | 15.10: fail-closed API guard исправлен; runtime GET без токена503. Verified identity rate quota/Lua исправлены. Runtime token configuration, bridge и полная deployment-проверка остаются; см. блок A. |
| 2 | P1 | partial | Telegram approval и inbox без проверки администратора | 15.10: groups требуют user+chat; private chat проверяет owner ID, invalid identity отклоняется. Middleware regression test проходит. Запущенный bot не перезапускался; version approval — блок B. |
| 3 | P1 | partial | Новые входящие могут потеряться при сбое | 30.10: ON CONFLICT предотвращает падение конкурентных inserts с одинаковым message ID; fallback без ID нестабилен между процессами. LLM вызывается до durable insert, retry не гарантирован. R06, R08. |
| 4 | P1 | partial | Повторная отправка ответа клиенту | 30.10: business claims/fencing, immutable approvals и full-text review; inbox send exceptions после начала запроса считаются ambiguous. Остаток: callback/FSM, outbox/history, operator unknown resolution. R03–R05. |
| 5 | P1 | partial | Гонки при отправке откликов | 30.10: no proxy retry после submit exception; browser success без точной receipt и post-click failure → unknown_result. Live form params/receipt, operator resolution и resubmit остаются открытыми. R03–R05. |
| 6 | P1 | partial | Свежая БД не получает UNIQUE и CHECK из schema.sql | Fresh upgrade и текущая БД на 0004 с UNIQUE/CHECK подтверждены. Legacy migration/immutable revisions и параллельный DDL не закрыты. R12. |
| 7 | P1 | rework | Одновременные pipeline создают дубли и конфликтующие статусы | Persist/recovery fencing добавлен, 30.10 отдельные ошибки scrape/jobs изолированы и C061 закрыт. Queue/backoff, heartbeat и concurrent upsert незавершены. R07. |
| 8 | P1 | partial | Ошибка LLM превращается в обычную фильтрацию | Filter/scoring ошибки отделены от filtered, но next_attempt не записывается и queue не гарантирует retry; proposal errors не унифицированы. R07. |
| 9 | P1 | partial | Чужой заказ может попасть в контекст ответа | 30.10: несовпавший явный project ID и unbound refresh больше не переходят на username fallback. Selector, thread isolation и проверка старых связей остаются. R09. |
| 10 | P1 | rework | Уведомления об откликах не восстанавливаются автоматически | 30.10: outbox fencing и partial-delivery guard реализованы; proposal/outbox ещё не в одной TX, recovery отсутствующих событий и poller conflicts не закрыты. R06. |
| 11 | P2 | done | Пауза Kwork останавливает все площадки и переписку | Введены is_kwork_offers_paused/is_kwork_inbox_paused и per-platform обработка. Проверены unit policy; реальная политика коннектов не проверялась. |
| 12 | P2 | rework | Ошибка Qdrant прерывает бизнес-процесс | QdrantClient sync и eager constructor остались; constructor ProposalAgent/LearningAgent может оборвать workflow до try. R08. |
| 13 | P2 | partial | Hash-векторы не обеспечивают семантическую память | Hash fallback удалён; provider выделен. Dimension/provider/version не включены в collection identity, silent skip теряет индексирование. R08. |
| 14 | P2 | partial | API /chat не использует сохранённую переписку | 30.10: общий service использует confirmed snapshot и исключает явно помеченный dry-run. Thread isolation и достоверность исторических confirmed статусов остаются открыты. R09. |
| 15 | P2 | partial | Успешные отклики не попадают в обучение через outcomes API | Learning получает proposal context после outcome; нет durable learning event/retry, fallback допускает draft. R10. |
| 16 | P2 | rework | Исходы и rewards можно записать многократно | Одинаковый event key идемпотентен; разные keys начисляют hired повторно. Transition helper не вызывается. R10. |
| 17 | P2 | partial | Learning меняет веса без валидированной статистики | Валидация и min count/rollback есть; качество не оценивается, одна активная версия не защищена от гонки. R10. |
| 18 | P2 | partial | Некорректный scoring JSON получает 50 баллов | 30.10: scoring/filter/chat/learning валидируются строго, лишние поля и невалидные веса отклоняются; C056/C057 закрыты. Retry classification остаётся R11. |
| 19 | P2 | done | Связь с клиентом без external_id может объединить разных людей | NULL external_id больше не используется для поиска существующего клиента. Unit tests проходят. Конкурентный insert с известным ID — отдельный остаток R07. |
| 20 | P2 | done | Generic scraper теряет относительные ссылки | urljoin, URL-based identity и optional count исправлены; tests проходят, stubs документированы. URL stability зависит от площадки. |
| 21 | P2 | partial | Проверки не покрывают критические сценарии | 176 существующих тестов проходят на отдельной БД, но воспроизводимые критические дефекты не покрыты; многие тесты проверяют helper/галочки. R15. |
| 22 | P3 | rework | Долговечность эксплуатации и воспроизводимость сборки | 30.10 smoke отклоняет 503, restore create-only, rollout проверяет backup digest/формат. Изолированный restore и provenance не подтверждены; Docker не использует lock, mounts сливаются. R13–R16. |
| 23 | P3 | done | Дубли функций и замечания линтера | Повтор greeting убран; Ruff чистый. Текущая конфигурация в основном E/F: отсутствие более широких правил не доказывает их соблюдение. |

## Summary

- Findings: **23**
- By status: {'partial': 14, 'rework': 5, 'done': 4}
- Historical checklist snapshot: 51/115 confirmed, 64 open.
- Current checklist markers (not independent evidence): 72/115 closed, 43 open.
- Release not accepted. Live/staging criteria cannot be replaced by simulated evidence.
- Review evidence is dated; update it when code or accepted behavior changes.
