"""Parse DevTools request headers export and print OpenAI env hints."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def parse_header_export(text: str) -> dict[str, str]:
    fetch_match = re.search(
        r'"headers"\s*:\s*(\{.*?\})\s*,\s*"referrer"',
        text,
        flags=re.DOTALL,
    )
    if fetch_match:
        try:
            raw = json.loads(fetch_match.group(1))
            return {str(key).lower(): str(value) for key, value in raw.items()}
        except json.JSONDecodeError:
            pass

    lines = [line.strip() for line in text.splitlines()]
    headers: dict[str, str] = {}
    index = 0
    while index < len(lines):
        name = lines[index]
        if not name:
            index += 1
            continue
        if name.startswith(":"):
            index += 2
            continue
        if index + 1 >= len(lines):
            break
        value = lines[index + 1]
        headers[name.lower()] = value
        index += 2
    return headers


def extract_bearer(authorization: str) -> str | None:
    match = re.match(r"^Bearer\s+(.+)$", authorization.strip(), flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Import OpenAI headers from DevTools export")
    parser.add_argument(
        "path",
        nargs="?",
        default="temp/request_header.txt",
        help="Path to copied request headers (name/value lines)",
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    headers = parse_header_export(path.read_text(encoding="utf-8"))
    auth = headers.get("authorization", "")
    cookie = headers.get("cookie", "")
    bearer = extract_bearer(auth) if auth else None

    print(f"Parsed: {path}")
    if bearer:
        print("\nOPENAI_SESSION_TOKEN found:")
        print(bearer)
    else:
        print("\nOPENAI_SESSION_TOKEN: NOT FOUND")
        print(
            "Скопируйте заголовки API-запроса (Fetch/XHR) к api.openai.com, "
            "не главной страницы platform.openai.com."
        )

    if cookie:
        print("\nOPENAI_SESSION_COOKIE found (optional, use with token):")
        print(cookie[:120] + ("..." if len(cookie) > 120 else ""))
    else:
        print("\nOPENAI_SESSION_COOKIE: not found")

    return 0 if bearer else 2


if __name__ == "__main__":
    raise SystemExit(main())
