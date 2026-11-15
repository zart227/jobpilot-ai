"""Export Weblancer session: обычный Chrome (без Playwright-флагов), Enter — cookies в файл.

Cloudflare ломается, если Chrome запущен через launch_persistent_context
(жёлтая плашка про --disable-blink-features=AutomationControlled). Поэтому
мы стартуем **системный Chrome** и только подключаемся по CDP, чтобы сохранить cookies.

Usage:
    bash scripts/setup_weblancer_session.sh
    python scripts/export_weblancer_session.py [output_path]
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

from app.services.weblancer_browser import (
    HOME_URL,
    VERIFY_URL,
    is_weblancer_challenge_title,
    page_has_listing,
)
from app.services.weblancer_session import write_sanitized_storage
from app.utils.storage_state import save_browser_storage_state

DEFAULT_OUTPUT = Path("data/weblancer_session.json")
PROFILE_DIR = Path("data/weblancer_chrome_profile")
DOMAIN_HINT = "weblancer.net"
DEFAULT_CDP_PORT = 9333


def _find_chrome() -> Path | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        found = shutil.which(name)
        if found:
            return Path(found)
    fallback = Path("/usr/bin/google-chrome")
    return fallback if fallback.is_file() else None


def _cdp_port() -> int:
    raw = os.environ.get("WEBLANCER_CDP_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_CDP_PORT


def _count_weblancer_cookies(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        return 0
    return sum(
        1
        for c in cookies
        if isinstance(c, dict) and DOMAIN_HINT in str(c.get("domain", ""))
    )


def _launch_chrome_user_mode(port: int) -> subprocess.Popen[bytes]:
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("Не найден google-chrome (apt install google-chrome-stable)")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(chrome),
        f"--user-data-dir={PROFILE_DIR.resolve()}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        HOME_URL,
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def _connect_cdp(playwright, port: int, *, timeout_s: float = 45):
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await playwright.chromium.connect_over_cdp(url)
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(0.5)
    raise RuntimeError(f"Не удалось подключиться к Chrome CDP ({url}): {last_exc}") from last_exc


def _default_context(browser):
    if browser.contexts:
        return browser.contexts[0]
    raise RuntimeError("В Chrome нет контекста — откройте вкладку weblancer.net")


async def _pick_weblancer_page(context):
    for page in context.pages:
        try:
            if DOMAIN_HINT in page.url and not is_weblancer_challenge_title(await page.title()):
                return page
        except Exception:
            continue
    return context.pages[0] if context.pages else None


async def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(output.parent, os.W_OK | os.X_OK):
        print(f"Ошибка: нет прав на запись в {output.parent.resolve()}")
        sys.exit(1)

    cdp_url = os.environ.get("WEBLANCER_CDP_URL", "").strip()
    port = _cdp_port()
    chrome_proc: subprocess.Popen[bytes] | None = None

    print()
    print("=" * 60)
    if cdp_url:
        print(f"Подключение к уже запущенному Chrome: {cdp_url}")
    else:
        print("Откроется обычный Chrome (без флагов автоматизации Playwright).")
        print("Пройдите Cloudflare, войдите на Weblancer.")
        print("Затем Enter в терминале — cookies сохранятся в проект.")
    print("=" * 60)
    print()

    async with async_playwright() as playwright:
        if cdp_url:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
        else:
            try:
                chrome_proc = _launch_chrome_user_mode(port)
            except RuntimeError as exc:
                print(exc)
                sys.exit(1)
            try:
                browser = await _connect_cdp(playwright, port)
            except RuntimeError as exc:
                if chrome_proc and chrome_proc.poll() is None:
                    chrome_proc.terminate()
                print(exc)
                print()
                print("Если Chrome уже открыт с этим профилем — закройте его и повторите.")
                print("Или: bash scripts/setup_weblancer_session.sh --clipboard")
                sys.exit(1)

        context = _default_context(browser)

        input("Enter, когда на Weblancer видна нормальная страница (не «Проверяем, человек ли вы»)…")

        active = await _pick_weblancer_page(context)
        if active:
            try:
                await active.goto(VERIFY_URL, wait_until="domcontentloaded", timeout=90_000)
                await active.wait_for_timeout(2000)
            except Exception as exc:
                print(f"(Лента перед сохранением: {exc})")

            try:
                title = await active.title()
                if is_weblancer_challenge_title(title):
                    print("⚠️  Всё ещё экран Cloudflare — сохраним cookies, но в Docker может не хватить.")
                elif await page_has_listing(active):
                    print("Лента заказов видна — ок.")
            except Exception:
                pass

        tmp = output.with_suffix(".raw.json")
        await save_browser_storage_state(context, tmp)
        raw = json.loads(tmp.read_text(encoding="utf-8"))
        tmp.unlink(missing_ok=True)
        cleaned = write_sanitized_storage(output, raw)
        print(f"Оставлено cookies: {len(cleaned.get('cookies', []))} (только Weblancer/CF)")
        await browser.close()
        if chrome_proc and chrome_proc.poll() is None:
            print("(Окно Chrome можно закрыть вручную.)")

    n = _count_weblancer_cookies(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    names = {
        c.get("name")
        for c in payload.get("cookies", [])
        if DOMAIN_HINT in str(c.get("domain", ""))
    }
    if n == 0:
        print("Ошибка: нет cookies weblancer.net (CF не пройден?).")
        print("Запасной путь: обычный Chrome с Cookie-Editor → bash scripts/setup_weblancer_session.sh --clipboard")
        sys.exit(1)

    print(f"Сессия сохранена: {output.resolve()} ({n} cookies)")
    if "cf_clearance" not in names:
        print("⚠️  Нет cf_clearance — headless может снова упереться в Cloudflare.")

    print()
    print("WEBLANCER_STORAGE_STATE=/app/data/weblancer_session.json")
    print("docker compose restart celery-worker")


if __name__ == "__main__":
    asyncio.run(main())
