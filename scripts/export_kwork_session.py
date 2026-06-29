"""Export Kwork browser session for headless Docker automation.

Usage:
    python scripts/export_kwork_session.py [output_path]

Default output: data/kwork_session.json

A visible Chromium window opens. Log in to Kwork manually, then press Enter
in the terminal to save cookies/localStorage to a Playwright storage state file.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_OUTPUT = Path("data/kwork_session.json")
LOGIN_URL = "https://kwork.ru/login"
SELLER_URL = "https://kwork.ru/seller"


async def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)

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
        print("1. В открывшемся окне войдите на Kwork (email + пароль).")
        print("2. Пройдите капчу, если появится.")
        print(f"3. Откройте {SELLER_URL} и убедитесь, что вы авторизованы.")
        print("4. Вернитесь сюда и нажмите Enter для сохранения сессии.")
        print("=" * 60)
        input()

        await page.goto(SELLER_URL, wait_until="domcontentloaded", timeout=60000)
        if "/login" in page.url:
            print("Ошибка: вы всё ещё на странице входа. Сессия не сохранена.")
            await browser.close()
            sys.exit(1)

        await context.storage_state(path=str(output))
        await browser.close()

    print(f"Сессия сохранена: {output.resolve()}")
    print()
    print("Добавьте в .env:")
    print(f"KWORK_STORAGE_STATE=/app/data/kwork_session.json")
    print()
    print("Перезапустите бота:")
    print("docker compose up -d --force-recreate telegram-bot")


if __name__ == "__main__":
    asyncio.run(main())
