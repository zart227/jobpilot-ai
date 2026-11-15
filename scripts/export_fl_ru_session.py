"""Export FL.ru browser session for headless automation.

Usage:
    python scripts/export_fl_ru_session.py [output_path]

Default output: data/fl_ru_session.json

A visible Chromium window opens. Log in to FL.ru manually, then press Enter
to save cookies/localStorage to a Playwright storage state file.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from app.services.fl_ru_browser import VERIFY_URL, is_fl_login_url, read_current_uid
from app.utils.storage_state import save_browser_storage_state

DEFAULT_OUTPUT = Path("data/fl_ru_session.json")
LOGIN_URL = "https://www.fl.ru/account/login/"


async def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(output.parent, os.W_OK | os.X_OK):
        print(f"Ошибка: нет прав на запись в {output.parent.resolve()}")
        sys.exit(1)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ru-RU",
        )
        page = await context.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print()
        print("=" * 60)
        print("1. В открывшемся окне войдите на FL.ru (логин или соцсеть).")
        print("2. Пройдите капчу, если появится.")
        print(f"3. Откройте {VERIFY_URL} и убедитесь, что вы авторизованы.")
        print("4. Вернитесь сюда и нажмите Enter для сохранения сессии.")
        print("=" * 60)
        input()

        await page.goto(VERIFY_URL, wait_until="domcontentloaded", timeout=60000)
        if is_fl_login_url(page.url) or await read_current_uid(page) <= 0:
            print("Ошибка: сессия не подтверждена (current-uid=0 или страница входа).")
            await browser.close()
            sys.exit(1)

        await save_browser_storage_state(context, output)
        await browser.close()

    print(f"Сессия сохранена: {output.resolve()}")
    print()
    print("Добавьте в .env:")
    docker_path = "/app/data/fl_ru_session.json"
    local_path = output.resolve()
    if output == DEFAULT_OUTPUT:
        print(f"FL_RU_STORAGE_STATE={docker_path}")
        print(f"# локально без Docker: FL_RU_STORAGE_STATE={local_path}")
    else:
        print(f"FL_RU_STORAGE_STATE={local_path}")
    print()
    print("Перезапустите сервисы, которые ходят на FL.ru (celery-worker / compose).")


if __name__ == "__main__":
    asyncio.run(main())
