# Проверка browser-контракта — 16 октября 2026

Фикстура — синтетическая локальная HTML-модель, не копия текущей Kwork. Она проверяет исполнение Playwright/JavaScript адаптера в настоящем Chromium. Не подтверждает совместимость live DOM или внешний результат отправки.

## Повторный запуск

Из корня проекта, с установленным соответствующим Playwright Chromium:

```sh
JOBPILOT_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests/test_kwork_form_dom.py tests/test_frozen_submission.py
```

Default unit runs пропускают opt-in browser tests. При JOBPILOT_BROWSER_TESTS=1 недоступность Chromium — ошибка, не автоматический skip. В CI отдельный browser-form-contract job устанавливает Chromium и запускает эту команду; remote CI evidence ещё отсутствует.

Если локальный Playwright Chromium отсутствует, для тестов можно явно указать совместимый установленный браузер: `JOBPILOT_TEST_CHROMIUM=/usr/bin/google-chrome`. Без этого параметра сохраняется обычный Playwright Chromium CI.

Все внешние запросы context блокируются. В тестах submit_offer только fixture.invalid перехватывается и отдаётся из локального HTML; form submit фиксирует counter/payload локально. Не передавать storage_state, .env credentials, Telegram/Kwork клиентов или рабочую БД. Браузерный тест не требует PostgreSQL, Redis, API key или аккаунта Kwork.

## Подтверждено

- Исходный browser run:6 failed/6 passed; отдельный итог:17 DOM +13 unit checks passed,19.85s. Финальный единый regression+browser+DB run:333 passed,42.89s,без skips.
- Hidden textarea и contenteditable получают один exact текст, включая пробелы, HTML-like литералы, Unicode и переносы; input/change опубликованы.
- Проверяются оба текста, unique controls, общий form owner, named price aliases и selected duration.
- Search query7 при selected17, duplicate/missing/hidden/cross-form controls и price clamp отклоняются.
- Production submit_offer допускает локальный click при exact payload и запрещает его при price mismatch; local form не является внешней receipt, результат не объявляется live success.

## Не подтверждено

30.10: автоматическое изменение title и выбор первого payment item удалены. Распознанные поля title/payment вызывают отказ как до заполнения, так и перед submit; в том числе если появились после заполнения. Это намеренно блокирует формы с такими полями до реализации их явного approval. Проверка ограничена известными селекторами и не доказывает отсутствие других неподтверждённых полей.

Current Kwork DOM, реальные Vue/Trumbowyg handlers/CSS, staging/live delivery, title/payment/все прочие поля и payload-specific внешняя receipt. Неизвестные поля не гарантированно распознаются. C041 и выпуск остаются открытыми;51/115 критериев подтверждено,64 открыто. Для deployment нужен существующий upgrade0007, рабочая БД не мигрирована.
