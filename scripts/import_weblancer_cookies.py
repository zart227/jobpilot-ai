"""Build Playwright storage state from browser-exported cookies (Cookie-Editor JSON).

Предпочтительно — браузер (скрипт сам сохранит cookies):

    bash scripts/setup_weblancer_session.sh

Запасной вариант — Cookie-Editor в буфер:

    bash scripts/setup_weblancer_session.sh --clipboard

Или вручную:

    python scripts/import_weblancer_cookies.py --auto
    python scripts/import_weblancer_cookies.py --clipboard
    python scripts/import_weblancer_cookies.py data/browser-imports/weblancer-cookies.json

Accepts:
  - list of cookie objects (Cookie-Editor / EditThisCookie)
  - or {"cookies": [...]} Playwright storage fragment
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.services.weblancer_session import write_sanitized_storage
from app.utils.storage_state import write_json_replacing

DEFAULT_OUTPUT = Path("data/weblancer_session.json")
IMPORT_DIR = Path("data/browser-imports")
ARCHIVE_NAME = "weblancer-cookies.json"
DOMAIN_HINTS = ("weblancer.net",)


def _normalize_cookie(raw: dict) -> dict | None:
    name = raw.get("name")
    if not name:
        return None
    domain = str(raw.get("domain") or ".weblancer.net")
    if not any(h in domain for h in DOMAIN_HINTS):
        return None
    out: dict = {
        "name": name,
        "value": str(raw.get("value", "")),
        "domain": domain,
        "path": raw.get("path") or "/",
    }
    if raw.get("expires") is not None:
        out["expires"] = raw["expires"]
    if raw.get("expirationDate") is not None:
        out["expires"] = raw["expirationDate"]
    for key in ("httpOnly", "secure", "sameSite"):
        if key in raw:
            out[key] = raw[key]
    return out


def _downloads_dir() -> Path | None:
    try:
        out = subprocess.check_output(["xdg-user-dir", "DOWNLOAD"], text=True).strip()
        if out:
            return Path(out)
    except (OSError, subprocess.CalledProcessError):
        pass
    for candidate in (
        Path.home() / "Загрузки",
        Path.home() / "Downloads",
    ):
        if candidate.is_dir():
            return candidate
    return None


def _raw_cookie_list(payload: object) -> list | None:
    if isinstance(payload, dict) and "cookies" in payload:
        raw = payload["cookies"]
        return raw if isinstance(raw, list) else None
    if isinstance(payload, list):
        return payload
    return None


def _weblancer_cookie_count(payload: object) -> int:
    raw_list = _raw_cookie_list(payload)
    if not raw_list:
        return 0
    return sum(1 for item in raw_list if isinstance(item, dict) and _normalize_cookie(item))


def _read_clipboard_text() -> str | None:
    commands = (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    )
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if proc.returncode != 0 or not proc.stdout:
            continue
        text = proc.stdout.decode("utf-8", errors="replace").strip()
        if text.startswith(("[", "{")):
            return text
    return None


def _scan_dirs() -> list[Path]:
    dirs: list[Path] = []
    if IMPORT_DIR.is_dir():
        dirs.append(IMPORT_DIR)
    dl = _downloads_dir()
    if dl and dl.is_dir():
        dirs.append(dl)
    return dirs


def _discover_export_files() -> list[tuple[float, int, Path]]:
    found: list[tuple[float, int, Path]] = []
    for base in _scan_dirs():
        for path in base.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            count = _weblancer_cookie_count(payload)
            if count == 0:
                continue
            found.append((path.stat().st_mtime, count, path))
    found.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return found


def _archive_source(path: Path) -> Path:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    dest = IMPORT_DIR / ARCHIVE_NAME
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)
    return dest


def _resolve_src(path_str: str) -> Path:
    expanded = Path(os.path.expanduser(path_str))
    if expanded.is_file():
        return expanded
    if IMPORT_DIR.is_dir():
        candidate = IMPORT_DIR / path_str
        if candidate.is_file():
            return candidate
        for name in (path_str, ARCHIVE_NAME, "cookies.json", "cookie.json"):
            candidate = IMPORT_DIR / name
            if candidate.is_file():
                return candidate
    if expanded.parent == Path(".") or not expanded.parent.exists():
        dl = _downloads_dir()
        if dl:
            candidate = dl / path_str
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(expanded)


def _import_payload(payload: object, out: Path, *, label: str) -> None:
    raw_list = _raw_cookie_list(payload)
    if raw_list is None:
        print("Неизвестный формат: нужен JSON-массив или объект с ключом cookies")
        sys.exit(1)

    cookies = [c for item in raw_list if isinstance(item, dict) and (c := _normalize_cookie(item))]
    if not cookies:
        print(f"В {label} нет cookies для weblancer.net")
        sys.exit(1)

    names = {c["name"] for c in cookies}
    if "cf_clearance" not in names:
        print("Предупреждение: нет cf_clearance — Cloudflare может снова блокировать.")

    cleaned = write_sanitized_storage(out, {"cookies": cookies, "origins": []})
    print(f"Сохранено {len(cleaned.get('cookies', []))} cookies → {out.resolve()}")


def _auto_sources() -> tuple[object, str] | None:
    text = _read_clipboard_text()
    if text and text.strip():
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if payload is not None and _weblancer_cookie_count(payload) > 0:
            IMPORT_DIR.mkdir(parents=True, exist_ok=True)
            (IMPORT_DIR / ARCHIVE_NAME).write_text(text.strip() + "\n", encoding="utf-8")
            return payload, "буфер обмена"

    discovered = _discover_export_files()
    if discovered:
        _, count, path = discovered[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        archived = _archive_source(path)
        return payload, f"{archived} ({count} cookies)"

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт cookies Weblancer → Playwright storage state")
    parser.add_argument("path", nargs="?", help="JSON-файл (имя ищется в data/browser-imports/ и Загрузках)")
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Только из буфера (Cookie-Editor → Export → Copy)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Сначала буфер, иначе самый свежий JSON в browser-imports / Загрузках",
    )
    args = parser.parse_args()

    out = DEFAULT_OUTPUT
    use_auto = args.auto or (not args.path and not args.clipboard)

    if args.clipboard:
        text = _read_clipboard_text()
        if not text or not text.strip():
            print("Буфер пуст. Cookie-Editor: Export All → Copy to clipboard")
            sys.exit(1)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            print(f"В буфере не JSON: {exc}")
            sys.exit(1)
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        (IMPORT_DIR / ARCHIVE_NAME).write_text(text.strip() + "\n", encoding="utf-8")
        print("Импорт из буфера обмена")
        _import_payload(payload, out, label="буфере")
        return

    if use_auto and not args.path:
        found = _auto_sources()
        if not found:
            IMPORT_DIR.mkdir(parents=True, exist_ok=True)
            print("Не найден экспорт cookies.")
            print()
            print("В Chrome на weblancer.net:")
            print("  Cookie-Editor → Export All → Copy")
            print("  bash scripts/setup_weblancer_session.sh")
            print()
            print("или сохраните JSON в data/browser-imports/ и снова:")
            print("  python scripts/import_weblancer_cookies.py --auto")
            sys.exit(1)
        payload, label = found
        print(f"Импорт из {label}")
        _import_payload(payload, out, label=label)
        return

    if not args.path:
        parser.print_help()
        sys.exit(1)

    try:
        src = _resolve_src(args.path)
    except FileNotFoundError as exc:
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Файл не найден: {exc.args[0]}")
        discovered = _discover_export_files()
        if discovered:
            print("Похожие JSON (weblancer cookies):")
            for _, count, p in discovered[:6]:
                print(f"  - {p} ({count})")
        print()
        print("Или: Cookie-Editor → Copy → bash scripts/setup_weblancer_session.sh")
        sys.exit(1)

    payload = json.loads(src.read_text(encoding="utf-8"))
    archived = _archive_source(src)
    print(f"Импорт из {archived}")
    _import_payload(payload, out, label=str(archived))


if __name__ == "__main__":
    main()
